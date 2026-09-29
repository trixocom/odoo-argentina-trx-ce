# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Cliente para DolarApi (https://dolarapi.com).

API pública mantenida que mirrors la cotización del BNA y otras
referencias del mercado argentino. Más estable que scrapear el HTML
del BNA directamente.

Endpoints relevantes:
  GET /v1/dolares/oficial      → BNA (Cotización Billetes USD)
  GET /v1/cotizaciones/eur     → Euro oficial
  GET /v1/cotizaciones/brl     → Real
  GET /v1/cotizaciones         → Lista todas las monedas oficiales

Response shape (cada endpoint):
  {
    "moneda": "USD",
    "casa": "oficial",
    "nombre": "Oficial",
    "compra": 1375.0,
    "venta": 1425.0,
    "fechaActualizacion": "2026-05-04T20:00:00.000Z"
  }

Lib pura — no importa odoo. Devuelve dict {ISO: {compra, venta, fecha}}.
"""
import logging
from datetime import date, datetime

_logger = logging.getLogger(__name__)

URL_BASE = "https://dolarapi.com"

# Endpoints a consultar — solo los oficiales del BNA.
# La key es el código ISO de res.currency, el value es el path.
ENDPOINTS = {
    "USD": "/v1/dolares/oficial",
    "EUR": "/v1/cotizaciones/eur",
    "BRL": "/v1/cotizaciones/brl",
    "CLP": "/v1/cotizaciones/clp",
    "UYU": "/v1/cotizaciones/uyu",
}


class DolarApiError(Exception):
    """Error obteniendo cotización desde dolarapi.com."""


def get_rates(timeout=15):
    """Devuelve cotizaciones oficiales por código ISO.

    :param timeout: segundos por request.
    :return: dict {ISO: {compra, venta, fecha}}.
    :raises DolarApiError: si todas las consultas fallan.
    """
    try:
        import requests
    except ImportError as e:
        raise DolarApiError("requests no instalado: %s" % e)

    out = {}
    errors = []
    for iso, path in ENDPOINTS.items():
        url = URL_BASE + path
        try:
            r = requests.get(
                url, timeout=timeout, verify=True,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Odoo l10n_ar_trx_edi/1.0",
                },
            )
        except requests.exceptions.RequestException as e:
            errors.append("%s: %s" % (iso, e))
            continue
        if r.status_code != 200:
            errors.append("%s: HTTP %s" % (iso, r.status_code))
            continue
        try:
            data = r.json()
        except ValueError as e:
            errors.append("%s: JSON inválido: %s" % (iso, e))
            continue
        compra = data.get("compra")
        venta = data.get("venta")
        fecha_str = data.get("fechaActualizacion")
        fecha = None
        if fecha_str:
            try:
                # ISO 8601 "2026-05-04T20:00:00.000Z"
                fecha = datetime.fromisoformat(fecha_str.replace("Z", "+00:00")).date()
            except ValueError:
                fecha = None
        if compra is None or venta is None:
            errors.append("%s: response sin compra/venta: %r" % (iso, data))
            continue
        out[iso] = {
            "compra": float(compra),
            "venta": float(venta),
            "fecha": fecha,
            "casa": data.get("casa"),
            "nombre": data.get("nombre"),
        }

    if not out:
        raise DolarApiError("Todas las consultas fallaron: %s" % "; ".join(errors))
    if errors:
        _logger.warning("DolarApi: errores parciales: %s", "; ".join(errors))
    return out
