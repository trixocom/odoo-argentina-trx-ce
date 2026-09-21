# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Acciones CAEA accesibles desde res_company / settings."""
import logging
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_ar_caea_enabled = fields.Boolean(
        string="Régimen CAEA habilitado",
        default=False,
        help=(
            "Activá esto SOLO si la empresa está adherida al régimen "
            "CAEA en el portal AFIP (RG 2926). Cuando está activo:\n\n"
            "• Cron diario 09:00 solicita automáticamente el CAEA de la "
            "próxima quincena cuando se entra en ventana (días 11-15 ó "
            "27-fin del mes).\n"
            "• Cron diario 03:00 rinde a AFIP los comprobantes "
            "emitidos con CAEA y marca como 'sin movimiento' los CAEA "
            "que no se usaron.\n"
            "• Si WSFEv1 da timeout al postear, el sistema cae automá-"
            "ticamente al CAEA vigente y emite el comprobante igual.\n\n"
            "Si NO estás adherido, dejá esto desactivado para evitar "
            "errores de los crones contra AFIP."
        ),
    )

    def _get_company_root_delegated_field_names(self):
        # El régimen CAEA es por CUIT: una sucursal lo hereda de su raíz.
        return super()._get_company_root_delegated_field_names() + ["l10n_ar_caea_enabled"]

    def action_l10n_ar_request_caea(self):
        """Pide CAEA para la próxima quincena disponible.

        Lógica: si hoy es <= día 15, ofrecer la quincena 2 del mes en
        curso (orden=2). Si hoy es > 15, ofrecer la quincena 1 del mes
        siguiente (orden=1). En ambos casos con anticipación de hasta 5
        días previos al inicio.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        if today.day <= 15:
            periodo = today.strftime("%Y%m")
            orden = 2
        else:
            # Q1 del mes siguiente.
            month = today.month + 1
            year = today.year
            if month > 12:
                month = 1
                year += 1
            periodo = "%04d%02d" % (year, month)
            orden = 1

        # El CAEA es por CUIT: siempre sobre la compañía raíz.
        rec = self.env["l10n_ar.caea"].request_caea(self._l10n_ar_afip_company(), periodo, orden)
        return {
            "type": "ir.actions.act_window",
            "name": _("CAEA solicitado"),
            "res_model": "l10n_ar.caea",
            "res_id": rec.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_l10n_ar_view_caea(self):
        """Abre la lista de CAEAs de la empresa."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("CAEA — %s") % self.name,
            "res_model": "l10n_ar.caea",
            "view_mode": "list,form",
            "domain": [("company_id", "=", self._l10n_ar_afip_company().id)],
        }

    def action_l10n_ar_view_caea_log(self):
        """Abre el log de auditoría CAEA."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Log CAEA — %s") % self.name,
            "res_model": "l10n_ar.caea.log",
            "view_mode": "list,form",
            "domain": [("company_id", "=", self._l10n_ar_afip_company().id)],
        }
