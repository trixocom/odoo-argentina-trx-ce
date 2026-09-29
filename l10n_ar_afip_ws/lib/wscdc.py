# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Cliente del WSCDC — Constatación de Comprobantes recibidos.

WSCDC ("Web Service Constatación De Comprobantes") sirve para verificar que un
comprobante que recibimos de un proveedor fue REALMENTE autorizado por AFIP.
Lo usás del lado del comprador (cuentas a pagar), no del emisor.

Operación principal:

    ComprobanteConstatar(Auth, CmpReq) -> {Resultado: A|O|R, Observaciones, Errors}

Donde `CmpReq` lleva:

    {
        "CbteModo": "CAE" | "CAEA" | "CAI",
        "CuitEmisor": 30111111111,
        "PtoVta": 1,
        "CbteTipo": 1,                 # 1=FA, 6=FB, 11=FC, etc.
        "CbteNro": 123,
        "CbteFch": "20260423",         # YYYYMMDD
        "ImpTotal": 121.00,
        "CodAutorizacion": "...",      # CAE/CAEA/CAI
        "DocTipoReceptor": "80",       # 80=CUIT, 96=DNI, 99=Sin identificar
        "DocNroReceptor": "20111111111",
    }

Resultado:

    A = Aprobado (existe y matchea con AFIP)
    O = Observado (existe pero algún campo no matchea)
    R = Rechazado (no existe en AFIP, posible factura apócrifa)

Si AFIP devuelve <Errors> (errores estructurales del request, no resultado del
constatar), levantamos `WscdcError`. Si devuelve <Observaciones> (matching
parcial), van en el dict del response y el caller decide qué hacer.

Operaciones auxiliares (catálogos, todas requieren TA):

    Dummy()                       # ping sin auth
    ComprobantesTiposConsultar()
    DocumentosTiposConsultar()
    MonedasTiposConsultar()
    OpcionalesTiposConsultar()
    TributosTiposConsultar()

Toda la capa acá es *pura*: recibe datos como diccionarios, devuelve datos
como diccionarios. El mapeo account.move ↔ CmpReq se hace en el módulo
`trx_l10n_ar_edi` (que sí importa Odoo).
"""
import logging

from zeep.exceptions import Fault, TransportError as ZeepTransportError

from . import errors, urls

_logger = logging.getLogger(__name__)


def _build_client(environment, transport):
    import zeep
    wsdl = urls.get_wsdl_url("wscdc", environment)
    try:
        return zeep.Client(wsdl=wsdl, transport=transport)
    except ZeepTransportError as e:
        raise errors.TransportError(
            message="No pude traer el WSDL de WSCDC (%s): %s" % (wsdl, e),
        )


def _auth(cuit, token, sign):
    """Header de autenticación que WSCDC espera (mismo shape que WSFE)."""
    return {"Token": token, "Sign": sign, "Cuit": int(cuit)}


def _check_errors(response, method):
    """Levanta WscdcError si el sobre trae <Errors>.

    OJO: WSCDC distingue entre <Errors> (errores estructurales del request) y
    <Resultado> + <Observaciones> (resultado del constatar en sí). Solo los
    primeros son fatales — los segundos son la respuesta esperada y van al
    caller.
    """
    errs = getattr(response, "Errors", None)
    if errs:
        items = getattr(errs, "Err", None) or []
        if items:
            first = items[0]
            code = getattr(first, "Code", None)
            msg = getattr(first, "Msg", "") or ""
            full_msg = msg
            if len(items) > 1:
                extra = "; ".join("[%s] %s" % (
                    getattr(e, "Code", "?"), getattr(e, "Msg", "")
                ) for e in items[1:])
                full_msg = "%s (además: %s)" % (msg, extra)
            _, hint = errors.get_wscdc_hint(code)
            raise errors.WscdcError(code=code, message=full_msg, hint=hint)

    events = getattr(response, "Events", None)
    if events:
        for ev in getattr(events, "Evt", None) or []:
            _logger.info(
                "WSCDC %s evento [%s]: %s",
                method, getattr(ev, "Code", "?"), getattr(ev, "Msg", "")
            )


def dummy(environment, transport):
    """Dummy: ping sin autenticación. Devuelve dict con AppServer/DbServer/AuthServer."""
    client = _build_client(environment, transport)
    try:
        r = client.service.ComprobanteDummy()
    except (Fault, ZeepTransportError) as e:
        raise errors.TransportError(message="ComprobanteDummy: %s" % e)
    return {
        "app_server": getattr(r, "AppServer", None),
        "db_server": getattr(r, "DbServer", None),
        "auth_server": getattr(r, "AuthServer", None),
    }


def comprobante_constatar(auth, cmp_req, environment, transport):
    """ComprobanteConstatar: verifica que un comprobante recibido sea legítimo.

    :param auth: dict {'token', 'sign', 'cuit'}.
    :param cmp_req: dict con la estructura esperada por AFIP. Ver docstring del
                    módulo arriba para el shape.
    :param environment: 'testing' o 'production'.
    :param transport: CapturingTransport para auditar XML.
    :return: dict con:
        - resultado (str): 'A' aprobado, 'O' observado, 'R' rechazado, '' raro.
        - observaciones (list[dict] | None): si AFIP devolvió matching parcial.
        - cab_req (dict): copia del request para forensics.
        - fecha_proceso (str|None): timestamp AFIP del constatar.
    :raises WscdcError: si AFIP devuelve <Errors> estructurales.
    """
    client = _build_client(environment, transport)
    try:
        r = client.service.ComprobanteConstatar(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
            CmpReq=cmp_req,
        )
    except Fault as f:
        raise errors.WscdcError(code="fault", message=str(f))
    except ZeepTransportError as e:
        raise errors.TransportError(message=str(e))

    _check_errors(r, "ComprobanteConstatar")

    obs = getattr(r, "Observaciones", None)
    obs_items = None
    if obs:
        items = getattr(obs, "Obs", None) or []
        obs_items = [
            {
                "code": getattr(o, "Code", None),
                "msg": getattr(o, "Msg", None),
            }
            for o in items
        ]

    return {
        "resultado": getattr(r, "Resultado", None) or "",
        "observaciones": obs_items,
        "cab_req": dict(cmp_req),
        "fecha_proceso": getattr(r, "FchProceso", None),
    }


def _consultar_tipos(method_name, key_name, environment, auth, transport):
    """Helper para los métodos de catálogo (Tipos: Cbte/Doc/Moneda/Opcional/Tributo).

    Todos siguen el mismo patrón: piden Auth, devuelven una lista bajo
    `<{key_name}>`.
    """
    client = _build_client(environment, transport)
    try:
        r = getattr(client.service, method_name)(
            Auth=_auth(auth["cuit"], auth["token"], auth["sign"]),
        )
    except Fault as f:
        raise errors.WscdcError(code="fault", message="%s: %s" % (method_name, f))
    except ZeepTransportError as e:
        raise errors.TransportError(message="%s: %s" % (method_name, e))

    _check_errors(r, method_name)
    items = getattr(r, key_name, None) or []
    return [
        {k: getattr(it, k, None) for k in dir(it)
         if not k.startswith("_") and not callable(getattr(it, k, None))}
        for it in items
    ]


def comprobantes_tipos_consultar(auth, environment, transport):
    """Catálogo de tipos de comprobante constatables (1 FA, 6 FB, etc.)."""
    return _consultar_tipos(
        "ComprobantesTiposConsultar", "ComprobanteTipo",
        environment, auth, transport,
    )


def documentos_tipos_consultar(auth, environment, transport):
    """Catálogo de tipos de documento receptor (80 CUIT, 96 DNI, 99 SI, etc.)."""
    return _consultar_tipos(
        "DocumentosTiposConsultar", "DocumentoTipo",
        environment, auth, transport,
    )


def monedas_tipos_consultar(auth, environment, transport):
    """Catálogo de monedas (PES, DOL, EUR, etc.) — útil para sanity-check."""
    return _consultar_tipos(
        "MonedasTiposConsultar", "MonedaTipo",
        environment, auth, transport,
    )
