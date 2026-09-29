# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Cliente para obtener cotizaciones del Banco Nación (BNA).

BNA publica las cotizaciones en su página principal:

    https://www.bna.com.ar/Personas

La página tiene 2 tabs HTML:
  * **Cotización Billetes** (default): cotización para compra/venta en
    efectivo de personas físicas. Es la que se muestra públicamente y
    la que toma la mayoría de las empresas como referencia "BNA".
  * **Cotización Divisas**: cotización mayorista para transferencias
    bancarias / divisas. Suele ser un poco más baja.

Layout HTML de cada tab (estable desde hace años):

    <div class="tab-pane fade in active" id="billetes">
      <table class="table cotizacion">
        <thead>
          <tr>
            <th class="fechaCot">D/M/YYYY</th>
            <th>Compra</th>
            <th>Venta</th>
          </tr>
        </thead>
        <tbody>
          <tr><td class="tit">Dolar U.S.A</td><td>1375,00</td><td>1425,00</td></tr>
          <tr><td class="tit">Euro</td>...</tr>
          ...
        </tbody>
      </table>
    </div>

Lib pura — no importa odoo. Devuelve dict {moneda → {compra, venta}}.
"""
import logging
import re
from datetime import date, datetime

_logger = logging.getLogger(__name__)

URL_BNA = "https://www.bna.com.ar/Personas"

# Mapeo nombres BNA → ISO. Solo los relevantes para Odoo.
BNA_TO_ISO = {
    "Dolar U.S.A": "USD",
    "Euro": "EUR",
    "Real": "BRL",          # OJO: cotización por 100 unidades
    "Libra Esterlina": "GBP",
}


class BnaError(Exception):
    """Error obteniendo cotizaciones del BNA — red, parseo, layout cambió."""


def get_rates(kind="billetes", timeout=15):
    """Devuelve cotizaciones BNA por código ISO.

    :param kind: 'billetes' (default) o 'divisas'.
    :param timeout: segundos.
    :return: dict {ISO: {'compra': float, 'venta': float, 'fecha': date}}.
        Ej: {'USD': {'compra': 1375.0, 'venta': 1425.0, 'fecha': date(2026,5,4)}}
    :raises BnaError: si falla red o parseo.
    """
    try:
        import requests
    except ImportError as e:
        raise BnaError("requests no instalado: %s" % e)

    try:
        r = requests.get(URL_BNA, timeout=timeout, verify=True,
                         headers={"User-Agent": "Odoo l10n_ar_trx_edi/1.0"})
    except requests.exceptions.RequestException as e:
        raise BnaError("HTTP request falló: %s" % e)
    if r.status_code != 200:
        raise BnaError("HTTP %s" % r.status_code)

    html = r.text

    # Aislar el bloque del tab solicitado (billetes o divisas).
    div_id = "billetes" if kind == "billetes" else "divisas"
    m = re.search(r'id="%s".*?</table>' % re.escape(div_id), html, re.DOTALL)
    if not m:
        raise BnaError("No encontré div id=%r en el HTML del BNA" % div_id)
    tab_html = m.group(0)

    # Fecha del header.
    fch_m = re.search(r'class="fechaCot"[^>]*>([\d/]+)<', tab_html)
    fecha = None
    if fch_m:
        try:
            fecha = datetime.strptime(fch_m.group(1).strip(), "%d/%m/%Y").date()
        except ValueError:
            try:
                fecha = datetime.strptime(fch_m.group(1).strip(), "%-d/%-m/%Y").date()
            except (ValueError, AttributeError):
                fecha = None

    # Cada fila: <td class="tit">NOMBRE</td><td>COMPRA</td><td>VENTA</td>
    out = {}
    pattern = re.compile(
        r'<td class="tit">([^<]+)</td>\s*<td[^>]*>([\d.,]+)</td>\s*<td[^>]*>([\d.,]+)</td>',
        re.DOTALL,
    )
    for moneda_bna, compra_str, venta_str in pattern.findall(tab_html):
        moneda_bna = moneda_bna.strip().rstrip("*").strip()
        iso = BNA_TO_ISO.get(moneda_bna)
        if not iso:
            continue
        compra = _parse_number(compra_str)
        venta = _parse_number(venta_str)
        if compra is None or venta is None:
            continue
        # Real * = cotización por 100 unidades — dividir.
        if "Real" in moneda_bna:
            compra /= 100.0
            venta /= 100.0
        out[iso] = {"compra": compra, "venta": venta, "fecha": fecha}
    if not out:
        raise BnaError("No pude extraer cotizaciones del HTML BNA — layout cambió?")
    return out


def _parse_number(s):
    """'1.375,00' o '1375,00' → 1375.0."""
    if not s:
        return None
    s = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None
