# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Settings de Argentina EDI expuestos en Configuración → Contabilidad.

Replica la sección "Localización para Argentina" de enterprise:

- Webservices ARCA: entorno + cert + key (campos de res.company existentes
  en l10n_ar_trx_edi_base).
- Verificar validez facturas proveedor en AFIP (WSCDC).
- Opción transmisión FCE MiPyME (SCA/ADC).
- Política pago moneda extranjera (RG 5616).

Botones:
- Conexiones de prueba (FEDummy + Padrón Dummy).
- Generar solicitud de renovación cert (CSR).

Los settings son `company_dependent` — cada empresa los define por
separado.
"""
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # ------------------------------------------------------------------
    # WSCDC
    # ------------------------------------------------------------------
    l10n_ar_supplier_validation_type = fields.Selection(
        related="company_id.l10n_ar_supplier_validation_type",
        readonly=False,
    )

    # ------------------------------------------------------------------
    # FCE MiPyME
    # ------------------------------------------------------------------
    l10n_ar_fce_transmission_type = fields.Selection(
        related="company_id.l10n_ar_fce_transmission_type",
        readonly=False,
    )

    # ------------------------------------------------------------------
    # Política pago moneda extranjera RG 5616
    # ------------------------------------------------------------------
    l10n_ar_payment_foreign_currency_policy = fields.Selection(
        related="company_id.l10n_ar_payment_foreign_currency_policy",
        readonly=False,
    )

    # ------------------------------------------------------------------
    # Settings de cert/entorno (ya existían en res.company via
    # l10n_ar_trx_edi_base) — los exponemos acá para que aparezcan agrupados
    # con los demás de Argentina.
    # ------------------------------------------------------------------
    l10n_ar_afip_ws_environment = fields.Selection(
        related="company_id.l10n_ar_afip_ws_environment",
        readonly=False,
    )
    l10n_ar_afip_ws_cert_id = fields.Many2one(
        related="company_id.l10n_ar_afip_ws_cert_id",
        readonly=False,
    )

    # ------------------------------------------------------------------
    # Cron cotización oficial AFIP
    # ------------------------------------------------------------------
    l10n_ar_afip_auto_currency_rate = fields.Boolean(
        related="company_id.l10n_ar_afip_auto_currency_rate",
        readonly=False,
    )
    l10n_ar_currency_rate_source = fields.Selection(
        related="company_id.l10n_ar_currency_rate_source",
        readonly=False,
    )

    def action_l10n_ar_update_rates_now(self):
        """Smoke manual: corre el cron de cotizaciones inmediato."""
        self.ensure_one()
        self.execute()
        self.env["res.currency"]._cron_l10n_ar_update_rates_from_afip()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Cotizaciones AFIP",
                "message": "Actualizadas. Verificá Configuración → Monedas → Tasas.",
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Acciones (botones)
    # ------------------------------------------------------------------
    def l10n_ar_action_test_connections(self):
        """Hace ping FEDummy a todos los WS configurados — diagnóstico rápido.

        Devuelve una notificación con el estado app/db/auth de cada WS.
        """
        self.ensure_one()
        company = self.company_id
        results = []
        for ws_key in ("wsfe", "wscdc"):
            try:
                conn = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
                    company, ws_key, company.l10n_ar_afip_ws_environment or "testing",
                )
                # Para diagnóstico solo nos importa que el TA se renueve OK.
                conn.get_auth()
                results.append("✓ %s: TA OK (vence %s)" % (
                    ws_key, conn.expiration_time,
                ))
            except Exception as e:
                results.append("✗ %s: %s" % (ws_key, e))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Conexiones AFIP/ARCA",
                "message": "\n".join(results),
                "type": "info",
                "sticky": True,
            },
        }

    def l10n_ar_action_create_csr(self):
        """Abre el wizard que arma el CSR para pedir un cert nuevo / renovar."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Solicitud de certificado AFIP",
            "res_model": "l10n_ar.csr.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_company_id": self.company_id.id,
            },
        }
