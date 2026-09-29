# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Settings AFIP/ARCA en `res.company` que viven en l10n_ar_trx_edi.

Acá ponemos los campos que necesitan que la **emisión** esté instalada
(no son solo metadata como los de `l10n_ar_trx_edi_base`):

- `l10n_ar_supplier_validation_type`: política para constatar facturas de
  proveedor en ARCA via WSCDC. 3 estados: no_disponible / disponible /
  requerido. Si "requerido", `_post()` bloquea facturas IN sin
  constatar.
- `l10n_ar_fce_transmission_type`: SCA / ADC para FCE MiPyME (RG
  4919/2021). Default empresa; el move puede sobreescribirlo.
- `l10n_ar_payment_foreign_currency`: política RG 5616/2024 para informar
  a ARCA si el pago es en moneda extranjera. yes / no / depends_currency.

Nota de diseño: usamos campos directos en `res.company` (no
`ir.config_parameter` como hace enterprise). Es más simple para
multi-empresa y se pueden setear con XML data por defecto.
"""
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # ------------------------------------------------------------------
    # WSCDC — constatación de facturas de proveedor
    # ------------------------------------------------------------------
    l10n_ar_supplier_validation_type = fields.Selection(
        selection=[
            ("no_disponible", "No disponible"),
            ("disponible", "Disponible"),
            ("requerido", "Requerido"),
        ],
        string="Verificar validez facturas proveedor en ARCA",
        default="no_disponible",
        help=(
            "Permite o requiere validar facturas de proveedor en ARCA "
            "(WSCDC) para documentos que tengan número CAE, CAI o CAEA.\n\n"
            " • No disponible: el botón 'Constatar en ARCA' no se muestra.\n"
            " • Disponible: el botón aparece en facturas IN, el operador "
            "constata manualmente.\n"
            " • Requerido: antes de postear una factura IN con CAE/CAI/CAEA, "
            "Odoo intenta constatarla. Si el resultado no es 'A' o 'O', "
            "no se permite el post.\n\n"
            "Solo aplica a tipos de comprobante constatables por WSCDC. "
            "Si el WS no soporta el tipo, el botón igual hace la consulta "
            "y devuelve un mensaje claro."
        ),
    )

    # ------------------------------------------------------------------
    # FCE MiPyME — opción transmisión (RG 4919/2021)
    # ------------------------------------------------------------------
    l10n_ar_fce_transmission_type = fields.Selection(
        selection=[
            ("SCA", "SCA - TRANSFERENCIA AL SISTEMA DE CIRCULACION ABIERTA"),
            ("ADC", "ADC - AGENTE DE DEPOSITO COLECTIVO"),
        ],
        string="Opción transmisión FCE MiPyME (default)",
        help=(
            "Valor por defecto que se enviará a ARCA al validar comprobantes "
            "FCE MiPyME (Factura de Crédito Electrónica). Requisito RG "
            "4919/2021. Cada factura puede sobreescribir este valor."
        ),
    )

    # ------------------------------------------------------------------
    # Cron diario de cotización oficial AFIP
    # ------------------------------------------------------------------
    l10n_ar_afip_auto_currency_rate = fields.Boolean(
        string="Cotización USD/EUR diaria automática",
        default=False,
        help=(
            "Si está activo, un cron diario actualiza la cotización oficial "
            "del día para cada moneda extranjera y la guarda en "
            "`res.currency.rate`."
        ),
    )
    l10n_ar_currency_rate_source = fields.Selection(
        selection=[
            ("dolarapi", "BNA Oficial (DolarApi — recomendado)"),
            ("bna", "BNA — scraping Cotización Billetes Venta"),
            ("bna_divisas", "Dólar Mayorista (BNA divisas)"),
        ],
        string="Fuente de cotización",
        default="dolarapi",
        help=(
            "BNA Oficial vía DolarApi (recomendado): API JSON pública "
            "mantenida que mirrors el BNA Oficial. Sin scraping, sin certs.\n"
            "BNA Billetes scraping: scrapea www.bna.com.ar/Personas. "
            "Misma cotización que la opción anterior, pero más frágil si "
            "BNA cambia el HTML.\n"
            "Dólar Mayorista: cotización mayorista del BNA (transferencias "
            "bancarias). Es la que usa AFIP de referencia, suele estar "
            "1-2 % más baja que BNA Billetes."
        ),
    )

    # ------------------------------------------------------------------
    # Política pago moneda extranjera (RG 5616/2024)
    # ------------------------------------------------------------------
    l10n_ar_payment_foreign_currency_policy = fields.Selection(
        selection=[
            ("yes", "Sí"),
            ("no", "No"),
            ("depends_currency", "Depende de la moneda de la cuenta"),
        ],
        string="Política pago moneda extranjera (default)",
        default="no",
        help=(
            "Por defecto que se usará para informar a ARCA si el pago se "
            "hará en moneda extranjera, requerido por RG 5616/2024:\n\n"
            " • Sí → siempre informa a ARCA que los pagos serán en moneda "
            "extranjera.\n"
            " • No → no reporta pagos en moneda extranjera por defecto.\n"
            " • Depende de la moneda de la cuenta → según la moneda de la "
            "cuenta a cobrar/pagar usada en la transacción.\n\n"
            "Sólo aplica a comprobantes con moneda distinta a la moneda "
            "de la empresa."
        ),
    )
