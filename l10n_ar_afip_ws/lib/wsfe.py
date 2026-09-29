# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Cliente del WSFEv1 — solicitud de CAE para comprobantes de mercado interno.

El WSFEv1 tiene un catálogo de métodos; acá envolvemos los que necesitamos
para MVP y dejamos los demás para fase posterior:

Implementados (Fase 1):
    - FEDummy(): ping sin autenticación para ver si el WS está vivo.
    - FECompUltimoAutorizado(Auth, PtoVta, CbteTipo): último comprobante
      autorizado por tipo y punto de venta. Necesario para numerar.
    - FECAESolicitar(Auth, FeCAEReq): solicita el CAE para uno o más
      comprobantes. Devuelve el CAE + fecha de vencimiento o errores.
    - FECompConsultar(Auth, FeCompConsReq): trae un comprobante ya autorizado.
    - FEParamGetPtosVenta(Auth): POS autorizados para el CUIT. Usado para
      diagnóstico y para validar un journal antes de pedir CAE.

Pendientes (fases siguientes): FEParamGetTiposCbte, FEParamGetTiposIva,
FEParamGetTiposDoc, FEParamGetCotizacion, FECAEARegInformativo, etc.

Por ahora vamos a wrappear solo lo indispensable. Los demás se pueden
agregar a medida que los tests los reclamen.

Toda la capa acá es *pura*: recibe datos como diccionarios, devuelve
datos como diccionarios. El mapeo account.move ↔ FECAERequest se hace
en el módulo `trx_l10n_ar_edi` (que sí importa Odoo).
"""
import logging

from zeep.exceptions import Fault, TransportError as ZeepTransportError

from . import errors, urls

_logger = logging.getLogger(__name__)


def _build_client(environment, transport):
    import zeep
    wsdl = urls.get_wsdl_url("wsfe", environment)
    try:
        return zeep.Client(wsdl=wsdl, transport=transport)
    except ZeepTransportError as e:
        raise errors.TransportError(
            message="No pude traer el WSDL de WSFEv1 (%s): %s" % (wsdl, e),
        )


def _auth(cuit, token, sign):
    """Devuelve el header `FEAuthRequest` que WSFE espera en casi todos los métodos."""
    return {"Token": token, "Sign": sign, "Cuit": int(cuit)}


def _check_errors(response, method):
    """Levanta WsfeError si la respuesta trae <Errors> o <Events> con errores reales.

    AFIP mete tanto errores como advertencias en el mismo sobre. Los
    `Events` suelen ser informativos ("vencimiento cercano del certificado")
    y NO hay que abortar por ellos; solo logueamos. Los `Errors` son los
    que hay que propagar.
    """
    # zeep devuelve objetos con atributos, no dicts; usamos getattr.
    errs = getattr(response, "Errors", None)
    if errs:
        items = getattr(errs, "Err", None) or []
        if items:
            first = items[0]
            code = getattr(first, "Code", None)
            msg = getattr(first, "Msg", "") or ""
            desc, hint = errors.get_wsfe_hint(code)
            full_msg = msg
            if len(items) > 1:
                extra = "; ".join("[%s] %s" % (
                    getattr(e, "Code", "?"), getattr(e, "Msg", "")
                ) for e in items[1:])
                full_msg = "%s (además: %s)" % (msg, extra)
            raise errors.WsfeError(code=code, message=full_msg, hint=hint)

    events = getattr(response, "Events", None)
    if events:
        for ev in getattr(events, "Evt", None) or []:
            _logger.info(
                "WSFE %s evento [%s]: %s",
                method, getattr(ev, "Code", "?"), getattr(ev, "Msg", "")
            )


def dummy(environment, transport):
    """FEDummy: ping sin autenticación. Devuelve dict con AppServer/DbServer/AuthServer."""
    client = _build_client(environment, transport)
    try:
        r = client.service.FEDummy()
    except (Fault, ZeepTransportError) as e:
        raise errors.TransportError(message="FEDummy: %s" % e)
    return {
        "app_server": getattr(r, "AppServer", None),
        "db_server": getattr(r, "DbServer", None),
        "auth_server": getattr(r, "AuthServer", None),
    }


def comp_ultimo_autorizado(auth, pto_vta, cbte_tipo, environment, transport):
    """FECompUltimoAutorizado: último comprobante autorizado.

    :param auth: dict {'token', 'sign', 'cuit'}.
    :return: dict {'pto_vta', 'cbte_tipo', 'cbte_nro'}.
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FECompUltimoAutorizado(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
            PtoVta=int(pto_vta),
            CbteTipo=int(cbte_tipo),
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_errors(r, "FECompUltimoAutorizado")
    return {
        "pto_vta": getattr(r, "PtoVta", None),
        "cbte_tipo": getattr(r, "CbteTipo", None),
        "cbte_nro": getattr(r, "CbteNro", None),
    }


def cae_solicitar(auth, fe_cae_req, environment, transport):
    """FECAESolicitar: solicita un CAE para uno o más comprobantes.

    :param auth: dict {'token', 'sign', 'cuit'}.
    :param fe_cae_req: dict con la estructura esperada por AFIP. Ver docstring
                       más abajo para el formato aceptado. Acá NO construimos
                       el payload — lo hace el caller en el módulo EDI, porque
                       depende de `account.move` y la responsabilidad fiscal.
    :param environment: 'testing' o 'production'.
    :param transport: CapturingTransport para auditar XML.
    :return: dict con:
        - cabecera: dict (CUIT, PtoVta, CbteTipo, FchProceso, CantReg, Resultado, Reproceso)
        - detalle: lista de dicts por cada comprobante con Concepto, DocTipo,
          DocNro, CbteDesde/Hasta, Observaciones, Resultado, CAE, CAEFchVto,
          etc. (las keys son las de AFIP, no las nuestras).
    :raises WsfeError: si AFIP devuelve <Errors>.

    Formato esperado de `fe_cae_req`:
        {
            "FeCabReq": {
                "CantReg": 1,
                "PtoVta": 1,
                "CbteTipo": 1,   # 1=FA, 6=FB, 11=FC, etc.
            },
            "FeDetReq": {
                "FECAEDetRequest": [
                    {
                        "Concepto": 1,          # 1=Productos, 2=Servicios, 3=ambos
                        "DocTipo": 80,          # CUIT=80, DNI=96, ...
                        "DocNro": 30111111111,
                        "CbteDesde": 123,
                        "CbteHasta": 123,
                        "CbteFch": "20260423",  # YYYYMMDD
                        "ImpTotal": 121.00,
                        "ImpTotConc": 0,
                        "ImpNeto": 100.00,
                        "ImpOpEx": 0,
                        "ImpIVA": 21.00,
                        "ImpTrib": 0,
                        "MonId": "PES",
                        "MonCotiz": 1,
                        "CanMisMonExt": "N",
                        # condiciones receptor (RG 5616):
                        "CondicionIVAReceptorId": 1,
                        "Iva": {
                            "AlicIva": [
                                {"Id": 5, "BaseImp": 100.00, "Importe": 21.00},
                            ],
                        },
                        # opcionales: Tributos, CbtesAsoc, Opcionales, Periodo,
                        # FchServDesde/Hasta (si Concepto=2/3), etc.
                    },
                ],
            },
        }
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FECAESolicitar(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
            FeCAEReq=fe_cae_req,
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_errors(r, "FECAESolicitar")

    cab = getattr(r, "FeCabResp", None)
    det_wrapper = getattr(r, "FeDetResp", None)
    det_items = getattr(det_wrapper, "FECAEDetResponse", None) if det_wrapper else None

    def _s(o):
        # serializa un objeto zeep a dict plano (solo atributos simples).
        if o is None:
            return None
        return {
            k: getattr(o, k, None)
            for k in dir(o)
            if not k.startswith("_") and not callable(getattr(o, k, None))
        }

    return {
        "cabecera": _s(cab),
        "detalle": [_s(d) for d in (det_items or [])],
    }


def param_get_ptos_venta(auth, environment, transport):
    """FEParamGetPtosVenta: puntos de venta dados de alta en AFIP para el CUIT.

    Fundamental antes de configurar un `account.journal` con `RLI_RLM`: si el
    POS del journal no aparece acá o viene con `Bloqueado='S'`, AFIP va a
    rechazar cualquier solicitud de CAE con error 1018 ("PuntoDeVenta no
    autorizado").

    :param auth: dict {'token', 'sign', 'cuit'}.
    :return: lista de dicts con keys:
        - nro (int): nro de punto de venta.
        - emision_tipo (str): 'CAE' (online WSFEv1) o 'CAEA' (anticipado).
        - bloqueado (str): 'N' habilitado, 'S' bloqueado.
        - fch_baja (str|None): fecha de baja si aplica, o None.
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FEParamGetPtosVenta(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_errors(r, "FEParamGetPtosVenta")
    result = getattr(r, "ResultGet", None)
    if result is None:
        return []
    items = getattr(result, "PtoVenta", None) or []
    return [
        {
            "nro": getattr(p, "Nro", None),
            "emision_tipo": getattr(p, "EmisionTipo", None),
            "bloqueado": getattr(p, "Bloqueado", None),
            "fch_baja": getattr(p, "FchBaja", None),
        }
        for p in items
    ]


def param_get_tipos_tributos(auth, environment, transport):
    """FEParamGetTiposTributos: catálogo de tributos válidos en AFIP.

    Devuelve la lista de Ids+descripción que AFIP acepta en el nodo
    `Tributos` del FECAESolicitar (para percepciones IIBB, impuestos
    municipales, internos, etc.). Útil para validar el mapeo entre el
    código de community `account.tax.group.l10n_ar_tribute_afip_code`
    y el `Id` que viaja al WS.

    :return: lista de dicts {id (int), desc (str), fch_desde, fch_hasta}.
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FEParamGetTiposTributos(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_errors(r, "FEParamGetTiposTributos")
    result = getattr(r, "ResultGet", None)
    if result is None:
        return []
    items = getattr(result, "TributoTipo", None) or []
    return [
        {
            "id": getattr(t, "Id", None),
            "desc": getattr(t, "Desc", None),
            "fch_desde": getattr(t, "FchDesde", None),
            "fch_hasta": getattr(t, "FchHasta", None),
        }
        for t in items
    ]


def param_get_cotizacion(auth, mon_id, environment, transport):
    """FEParamGetCotizacion: cotización oficial AFIP para una moneda.

    AFIP valida la `MonCotiz` que va en el FECAESolicitar contra esta
    referencia (o la del día hábil anterior). Si te desviás demasiado,
    rechaza con código 10024 ("Cotización no válida").

    :param mon_id: código AFIP de moneda — `'DOL'`, `'EUR'`, `'060'`.
    :return: dict {mon_id, cotiz, fch_cotiz} o None si no devuelve datos.
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.FEParamGetCotizacion(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
            MonId=mon_id,
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_errors(r, "FEParamGetCotizacion")
    result = getattr(r, "ResultGet", None)
    if result is None:
        return None
    return {
        "mon_id": getattr(result, "MonId", None),
        "cotiz": getattr(result, "MonCotiz", None),
        "fch_cotiz": getattr(result, "FchCotiz", None),
    }


def comp_consultar(auth, pto_vta, cbte_tipo, cbte_nro, environment, transport):
    """FECompConsultar: trae los datos de un comprobante ya autorizado."""
    client = _build_client(environment, transport)
    try:
        r = client.service.FECompConsultar(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
            FeCompConsReq={
                "PtoVta": int(pto_vta),
                "CbteTipo": int(cbte_tipo),
                "CbteNro": int(cbte_nro),
            },
        )
    except Fault as f:
        raise errors.WsfeError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_errors(r, "FECompConsultar")
    result = getattr(r, "ResultGet", None)
    if result is None:
        return None
    return {
        k: getattr(result, k, None)
        for k in dir(result)
        if not k.startswith("_") and not callable(getattr(result, k, None))
    }
