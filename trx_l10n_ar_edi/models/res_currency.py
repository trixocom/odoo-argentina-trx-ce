# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Cron diario que actualiza las cotizaciones de monedas extranjeras.

2 fuentes soportadas (configurable per-company en
`l10n_ar_currency_rate_source`):

  * **BNA Billetes** (default) — cotización pública del Banco Nación al
    cierre del día (Compra/Venta), tabla de "Personas". Es la que la
    mayoría de las empresas usa como referencia. Fuente: scraping de
    https://www.bna.com.ar/Personas
  * **BNA Divisas** — cotización mayorista del BNA (transferencias
    bancarias), suele estar 1-2 % más baja que billetes.
  * **AFIP** — `FEParamGetCotizacion` del WSFEv1. Suele estar 1 día
    atrasada vs BNA.

Se usa siempre el rate **VENTA** (lo que el banco vende USD por ARS).

Conversión Odoo:
  Odoo res.currency.rate.rate = 1 / venta_ARS
  (cuántas unidades de moneda extranjera vale 1 ARS)
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ResCurrency(models.Model):
    _inherit = "res.currency"

    @api.model
    def _cron_l10n_ar_update_rates_from_afip(self):
        """Cron diario — dispatch según `l10n_ar_currency_rate_source`."""
        Company = self.env["res.company"].sudo()
        active_companies = Company.search([
            ("l10n_ar_afip_auto_currency_rate", "=", True),
        ])
        if not active_companies:
            return True

        for company in active_companies:
            source = company.l10n_ar_currency_rate_source or "dolarapi"
            # Backwards compat: 'afip' fue removido como opción visible.
            # Si quedó configurado en alguna company, fallback a dolarapi.
            if source == "afip":
                source = "dolarapi"
                company.sudo().write({"l10n_ar_currency_rate_source": "dolarapi"})
            try:
                if source == "dolarapi":
                    self._update_rates_dolarapi(company)
                elif source in ("bna", "bna_divisas"):
                    kind = "billetes" if source == "bna" else "divisas"
                    self._update_rates_bna(company, kind=kind)
                else:
                    _logger.warning(
                        "Fuente de cotización desconocida en %s: %r",
                        company.name, source,
                    )
            except Exception as e:
                _logger.exception(
                    "Cotización %s para %s falló: %s",
                    source, company.name, e,
                )
        return True

    @api.model
    def _update_rates_dolarapi(self, company):
        """DolarApi.com — API JSON oficial-mirror del BNA. Default."""
        from ..lib import dolarapi
        Rate = self.env["res.currency.rate"].sudo()

        rates = dolarapi.get_rates()
        today = fields.Date.context_today(self)
        for iso, data in rates.items():
            currency = self.search([("name", "=", iso)], limit=1)
            if not currency:
                continue
            venta = data.get("venta")
            if not venta or venta <= 0:
                continue
            odoo_rate = 1.0 / venta
            existing = Rate.search([
                ("currency_id", "=", currency.id),
                ("name", "=", today),
                ("company_id", "=", company.id),
            ], limit=1)
            if existing:
                if abs(existing.rate - odoo_rate) > 1e-9:
                    existing.write({"rate": odoo_rate})
                    _logger.info(
                        "DolarApi: actualizado %s %s = %.8f (1 %s = %.4f ARS)",
                        company.name, iso, odoo_rate, iso, venta,
                    )
            else:
                Rate.create({
                    "currency_id": currency.id,
                    "company_id": company.id,
                    "name": today,
                    "rate": odoo_rate,
                })
                _logger.info(
                    "DolarApi: creado %s %s = %.8f (1 %s = %.4f ARS)",
                    company.name, iso, odoo_rate, iso, venta,
                )

    @api.model
    def _update_rates_bna(self, company, kind="billetes"):
        """Scrapea BNA y actualiza los rates de USD/EUR/GBP/BRL.

        Toma el precio de **VENTA** (lo que el banco vende moneda extranjera).
        """
        from ..lib import bna
        Rate = self.env["res.currency.rate"].sudo()

        rates = bna.get_rates(kind=kind)
        if not rates:
            _logger.warning("BNA: no devolvió cotizaciones (kind=%s)", kind)
            return

        today = fields.Date.context_today(self)
        for iso, data in rates.items():
            currency = self.search([("name", "=", iso)], limit=1)
            if not currency:
                continue
            venta = data.get("venta")
            if not venta or venta <= 0:
                continue
            odoo_rate = 1.0 / venta
            existing = Rate.search([
                ("currency_id", "=", currency.id),
                ("name", "=", today),
                ("company_id", "=", company.id),
            ], limit=1)
            if existing:
                if abs(existing.rate - odoo_rate) > 1e-9:
                    existing.write({"rate": odoo_rate})
                    _logger.info(
                        "BNA %s: actualizado %s %s = %.8f (1 %s = %.4f ARS)",
                        kind, company.name, iso, odoo_rate, iso, venta,
                    )
            else:
                Rate.create({
                    "currency_id": currency.id,
                    "company_id": company.id,
                    "name": today,
                    "rate": odoo_rate,
                })
                _logger.info(
                    "BNA %s: creado %s %s = %.8f (1 %s = %.4f ARS)",
                    kind, company.name, iso, odoo_rate, iso, venta,
                )

