# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Cliente del WSFEXv1 — Factura Electrónica de Exportación (FA-E / NC-E / ND-E).

A diferencia del WSFEv1 (mercado interno), WSFEX:

* Usa otra URL (ver `urls.py` con key `'wsfex'`).
* Tiene un header `Auth={Token, Sign, Cuit}` igual.
* Maneja un **Id** propio del contribuyente (secuencia de transacciones)
  además del Cbte_nro estándar — para pedir CAE hay que mandar un Id
  monotónico creciente, que se obtiene via `FEXGetLast_ID + 1`.
* Tipos de comprobante: `'19'` FA-E, `'20'` ND-E, `'21'` NC-E.
* El payload incluye un array `Items` con detalle por línea (no totales
  como WSFEv1) — cada item con `Pro_codigo`, `Pro_ds`, `Pro_qty`,
  `Pro_umed`, `Pro_precio_uni`, `Pro_total_item`, `Pro_bonificacion`.
* Soporta `Permisos[]` (despachos de exportación) y `Cmps_asoc[]` (NC/ND
  refiriendo a la FA-E original).
* Catálogos extra: `FEXGetPARAM_DST_pais`, `FEXGetPARAM_MON`,
  `FEXGetPARAM_INCOTERMS`, `FEXGetPARAM_UMed`, `FEXGetPARAM_Cbte_Tipo`.

Spec oficial: https://www.afip.gob.ar/fe/documentos/manualdesarrollador-COMPG-v2.5.pdf

Lib pura — recibe dicts, devuelve dicts. El mapping `account.move ↔
FEXAuthorizeRequest` se hace en `trx_l10n_ar_edi/lib/payload_fex.py`.
"""
import logging

from zeep.exceptions import Fault, TransportError as ZeepTransportError

from . import errors, urls

_logger = logging.getLogger(__name__)


def _build_client(environment, transport):
    import zeep
    wsdl = urls.get_wsdl_url("wsfex", environment)
    try:
        return zeep.Client(wsdl=wsdl, transport=transport)
    except ZeepTransportError as e:
        raise errors.TransportError(
            message="No pude traer el WSDL de WSFEXv1 (%s): %s" % (wsdl, e),
        )


def _auth(cuit, token, sign):
    """Header `ClsFEXAuthRequest` que WSFEX espera."""
    return {"Token": token, "Sign": sign, "Cuit": int(cuit)}


def _check_response(response, method):
    """Levanta WsfeError si la respuesta trae FEXErr.ErrCode != 0.

    Adaptado del patrón de WSFEv1 pero acomodado a la estructura de
    WSFEX (que devuelve `FEXErr` y `FEXEvents` en el sobre raíz).
    """
    err = getattr(response, "FEXErr", None)
    if err:
        code = getattr(err, "ErrCode", None) or 0
        msg = getattr(err, "ErrMsg", "") or ""
        if int(code) != 0 or (msg and msg != "OK"):
            # Probar primero con catálogo WSFEX (códigos 1500-1700);
            # si no, caer al genérico WSFE.
            desc, hint = errors.get_wsfex_hint(code)
            if hint is None:
                desc, hint = errors.get_wsfe_hint(code)
            raise errors.WsfeError(
                code=code, message="WSFEX %s: %s" % (method, msg), hint=hint,
            )
    events = getattr(response, "FEXEvents", None)
    if events:
        ev_code = getattr(events, "EventCode", None) or 0
        ev_msg = getattr(events, "EventMsg", "") or ""
        if int(ev_code) != 0 and ev_msg and ev_msg != "Ok":
            _logger.info("WSFEX %s evento [%s]: %s", method, ev_code, ev_msg)


def _serialize(o):
    """zeep object → dict plano."""
    if o is None:
        return None
    return {
        k: getattr(o, k, None)
        for k in dir(o)
        if not k.startswith("_") and not callable(getattr(o, k, None))
    }


# ----------------------------------------------------------------------
# Métodos
# ----------------------------------------------------------------------
def dummy(environment, transport):
    """FEXDummy: ping sin autenticación."""
    client = _build_client(environment, transport)
    try:
        r = client.service.FEXDummy()
    except (Fault, ZeepTransportError) as e:
        raise errors.TransportError(message="FEXDummy: %s" % e)
    return {
        "app_server": getattr(r, "AppServer", None),
        "db_server": getattr(r, "DbServer", None),
        "auth_server": getattr(r, "AuthServer", None),
    }


def get_last_id(auth, environment, transport):
    """FEXGetLast_ID: último ``Id`` (secuencia interna) usado por el contribuyente.

    Para emitir hay que mandar `Id = last_id + 1`. Ese Id es global por
    CUIT (no por POS/tipo); sirve para que AFIP detecte reproceso.

    :return: int (o 0 si nunca se emitió).
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FEXGetLast_ID(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))
    _check_response(r, "FEXGetLast_ID")
    result = getattr(r, "FEXResultGet", None)
    if result is None:
        return 0
    return int(getattr(result, "Id", 0) or 0)


def get_last_cmp(auth, pto_vta, cbte_tipo, environment, transport):
    """FEXGetLast_CMP: último Cbte_nro autorizado por (POS, Cbte_Tipo).

    A diferencia de WSFEv1 (que separa Auth y los args en parámetros
    distintos), WSFEX define un único parámetro `Auth` de tipo
    `ClsFEX_LastCMP` que **envuelve adentro** Token+Sign+Cuit + los
    datos de la consulta (Pto_venta, Cbte_Tipo).

    :param pto_vta: int.
    :param cbte_tipo: int o str (19, 20, 21).
    :return: dict {pto_vta, cbte_tipo, cbte_nro, fecha_cbte}.
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FEXGetLast_CMP({
            "Token": auth["token"],
            "Sign": auth["sign"],
            "Cuit": int(auth["cuit"]),
            "Pto_venta": int(pto_vta),
            "Cbte_Tipo": int(cbte_tipo),
        })
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))
    _check_response(r, "FEXGetLast_CMP")
    result = getattr(r, "FEXResult_LastCMP", None)
    if result is None:
        return None
    return {
        "pto_vta": getattr(result, "Pto_venta", None),
        "cbte_tipo": getattr(result, "Cbte_Tipo", None),
        "cbte_nro": getattr(result, "Cbte_nro", None),
        "fecha_cbte": getattr(result, "Fecha_cbte", None),
    }


def authorize(auth, cmp_dict, environment, transport):
    """FEXAuthorize: solicita CAE para una FA-E / NC-E / ND-E.

    :param cmp_dict: dict listo según spec WSFEX. Las keys son las que
        AFIP define (Id, Fecha_cbte, Cbte_Tipo, Punto_vta, Cbte_nro,
        Tipo_expo, permisos, Dst_cmp, Cliente, Domicilio_cliente,
        Id_impositivo, Cuit_pais_cliente, Moneda_Id, Moneda_ctz,
        Imp_total, Idioma_cbte, Items, Cmps_asoc, ...).
        El builder recomendado: ``trx_l10n_ar_edi.lib.payload_fex.build_fex_request``.
    :param environment: 'testing' o 'production'.
    :param transport: instancia de ``CapturingTransport`` (para auditar XML).
    :return: dict con:
        - resultado: 'A' aprobado / 'R' rechazado
        - cae, cae_fecha_vto, cbte_nro, motivos_obs, reproceso, fecha_cbte
        - raw: dict completo de la respuesta (debug)
    :raises WsfeError: si AFIP devuelve FEXErr.ErrCode != 0.
    """
    client = _build_client(environment, transport)

    # WSFEX requiere los arrays como tipos zeep (no dicts python sueltos).
    # WSDL exige las keys con capitalización exacta: Items, Cmps_asoc,
    # Permisos (con P mayúscula — enterprise tiene typo histórico
    # `permisos` minúscula).
    cmp_dict = dict(cmp_dict)
    items_raw = cmp_dict.get("Items") or []
    cmps_asoc_raw = cmp_dict.get("Cmps_asoc") or None
    permisos_raw = cmp_dict.get("Permisos") or cmp_dict.pop("permisos", None) or None

    if items_raw and isinstance(items_raw, list):
        ArrayOfItem = client.get_type("ns0:ArrayOfItem")
        cmp_dict["Items"] = ArrayOfItem(items_raw)

    if cmps_asoc_raw and isinstance(cmps_asoc_raw, list):
        ArrayOfCmp_asoc = client.get_type("ns0:ArrayOfCmp_asoc")
        cmp_dict["Cmps_asoc"] = ArrayOfCmp_asoc(cmps_asoc_raw)

    if permisos_raw and isinstance(permisos_raw, list):
        ArrayOfPermiso = client.get_type("ns0:ArrayOfPermiso")
        cmp_dict["Permisos"] = ArrayOfPermiso(permisos_raw)

    try:
        r = client.service.FEXAuthorize(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
            Cmp=cmp_dict,
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_response(r, "FEXAuthorize")
    result = getattr(r, "FEXResultAuth", None)
    if result is None:
        raise errors.WsfeError(
            code="empty",
            message="WSFEX FEXAuthorize devolvió respuesta sin FEXResultAuth",
        )
    return {
        "resultado": getattr(result, "Resultado", None),
        "cae": getattr(result, "Cae", None),
        "cae_fecha_vto": getattr(result, "Fch_venc_Cae", None),
        "cbte_nro": getattr(result, "Cbte_nro", None),
        "motivos_obs": getattr(result, "Motivos_Obs", None),
        "reproceso": getattr(result, "Reproceso", None),
        "fecha_cbte": getattr(result, "Fecha_cbte", None),
        "raw": _serialize(result),
    }


def get_cmp(auth, cbte_tipo, pto_vta, cbte_nro, environment, transport):
    """FEXGetCMP: trae los datos de una FA-E ya autorizada.

    Mismo patrón que `FEXGetLast_CMP`: el parámetro `Auth` envuelve TODO.
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FEXGetCMP({
            "Token": auth["token"],
            "Sign": auth["sign"],
            "Cuit": int(auth["cuit"]),
            "Cbte_tipo": int(cbte_tipo),
            "Punto_vta": int(pto_vta),
            "Cbte_nro": int(cbte_nro),
        })
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))
    _check_response(r, "FEXGetCMP")
    return _serialize(getattr(r, "FEXResultGet", None))


# ----------------------------------------------------------------------
# Catálogos auxiliares (FEXGetPARAM_*)
# ----------------------------------------------------------------------
def param_get_dst_pais(auth, environment, transport):
    """Catálogo de países destino con su código AFIP."""
    return _param_get(auth, "FEXGetPARAM_DST_pais", "ClsFEXResponse_DST_pais", environment, transport)


def param_get_mon(auth, environment, transport):
    """Catálogo de monedas extranjeras (Mon_Id, Mon_Ds, Mon_vig_desde, Mon_vig_hasta)."""
    return _param_get(auth, "FEXGetPARAM_MON", "ClsFEXResponse_Mon", environment, transport)


def param_get_incoterms(auth, environment, transport):
    """Catálogo Incoterms (FOB/CIF/EXW/etc.)."""
    return _param_get(auth, "FEXGetPARAM_INCOTERMS", "ClsFEXResponse_Inc", environment, transport)


def param_get_umed(auth, environment, transport):
    """Catálogo unidades de medida AFIP (kg, unidad, m, etc.)."""
    return _param_get(auth, "FEXGetPARAM_UMed", "ClsFEXResponse_UMed", environment, transport)


def param_get_cbte_tipo(auth, environment, transport):
    """Catálogo tipos de comprobante WSFEX (19/20/21)."""
    return _param_get(auth, "FEXGetPARAM_Cbte_Tipo", "ClsFEXResponse_Cbte_Tipo", environment, transport)


def _param_get(auth, method_name, _array_type, environment, transport):
    """Generic helper para los FEXGetPARAM_*."""
    client = _build_client(environment, transport)
    method = getattr(client.service, method_name)
    try:
        r = method(Auth=_auth(auth["cuit"], auth["token"], auth["sign"]))
    except Fault as f:
        raise errors.WsfeError(code="fault", message="%s: %s" % (method_name, f))
    except ZeepTransportError as e:
        raise errors.TransportError(message="%s: %s" % (method_name, e))
    _check_response(r, method_name)
    result = getattr(r, "FEXResultGet", None)
    if result is None:
        return []
    # Cada catálogo trae un array con un tag interno distinto. Recogemos
    # todos los iterables que no sean privados.
    items = []
    for k in dir(result):
        if k.startswith("_") or callable(getattr(result, k, None)):
            continue
        v = getattr(result, k, None)
        if isinstance(v, list):
            items = v
            break
    return [_serialize(i) for i in items]
