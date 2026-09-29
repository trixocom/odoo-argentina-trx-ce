# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Asistente técnico: consulta FECompUltimoAutorizado y sincroniza numeración.

Muestra, por tipo de comprobante del punto de venta del diario, el último
número autorizado por AFIP frente al último emitido en Odoo, y permite fijar
un override para que la próxima emisión numere AFIP+1 (útil cuando AFIP quedó
adelantado por una emisión fuera del flujo normal). Solo lectura salvo el
botón de sincronizar, que escribe el override en el diario.
"""
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AfipSeqSyncWizard(models.TransientModel):
    _name = "l10n_ar.afip.seq.sync.wizard"
    _description = "AFIP · Sincronización de numeración con FECompUltimoAutorizado"

    journal_id = fields.Many2one(
        "account.journal", string="Diario", required=True, readonly=True)
    pos_number = fields.Integer(
        related="journal_id.l10n_ar_afip_pos_number", string="Punto de venta")
    line_ids = fields.One2many(
        "l10n_ar.afip.seq.sync.wizard.line", "wizard_id", string="Comprobantes")

    def _populate_lines(self):
        """Consulta AFIP por cada tipo y arma las líneas de diagnóstico."""
        self.ensure_one()
        self.line_ids.unlink()
        journal = self.journal_id
        vals = []
        for dt in journal._l10n_ar_afip_relevant_doctypes():
            try:
                afip_last = journal._l10n_ar_afip_last_authorized(dt.code)
                err = False
            except Exception as exc:  # noqa: BLE001 -- mostrar el error, no romper
                afip_last = 0
                err = str(exc)
                _logger.warning(
                    "AFIP seq-sync wizard: fallo consulta PV %s tipo %s: %s",
                    journal.l10n_ar_afip_pos_number, dt.code, exc)
            odoo_last = journal._l10n_ar_afip_odoo_last(dt)
            if err:
                status = _("Error de consulta: %s") % err
            elif afip_last == odoo_last:
                status = _("OK · sincronizado")
            elif afip_last > odoo_last:
                status = _("AFIP adelantado (falta registrar %d comprob.)") % (
                    afip_last - odoo_last)
            else:
                status = _("Odoo adelantado (revisar)")
            vals.append((0, 0, {
                "document_type_id": dt.id,
                "doc_code": dt.code,
                "afip_last": afip_last,
                "odoo_last": odoo_last,
                "afip_error": err or False,
                "status": status,
                "to_sync": bool(not err and afip_last > odoo_last),
            }))
        self.line_ids = vals

    def action_refresh(self):
        self.ensure_one()
        self._populate_lines()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_sync(self):
        """Fija el override AFIP+1 para las líneas marcadas."""
        self.ensure_one()
        to_do = self.line_ids.filtered("to_sync")
        if not to_do:
            raise UserError(_(
                "No hay líneas marcadas para sincronizar. Marcá las que tengan "
                "AFIP adelantado."))
        applied = []
        for line in to_do:
            if line.afip_error:
                continue
            if line.afip_last <= line.odoo_last:
                # Nada que hacer si AFIP no está adelantado.
                continue
            self.journal_id._l10n_ar_afip_set_override(
                line.doc_code, line.afip_last)
            applied.append("%s: próxima emisión = %d" % (
                line.document_type_id.display_name, line.afip_last + 1))
            _logger.info(
                "AFIP seq-sync: override fijado diario %s tipo %s -> proximo %d "
                "(por %s)",
                self.journal_id.display_name, line.doc_code,
                line.afip_last + 1, self.env.user.login)
        if not applied:
            raise UserError(_(
                "Ninguna línea aplicable (AFIP no está adelantado en las "
                "marcadas)."))
        message = _(
            "Sincronización preparada. La próxima emisión de cada tipo tomará "
            "el número de AFIP+1:\n\n%s\n\nEl ajuste se aplica al validar el "
            "próximo comprobante y se limpia solo al obtener CAE."
        ) % "\n".join("• %s" % a for a in applied)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Numeración sincronizada"),
                "message": message,
                "type": "success",
                "sticky": True,
            },
        }


class AfipSeqSyncWizardLine(models.TransientModel):
    _name = "l10n_ar.afip.seq.sync.wizard.line"
    _description = "AFIP · Línea de diagnóstico de numeración"

    wizard_id = fields.Many2one(
        "l10n_ar.afip.seq.sync.wizard", required=True, ondelete="cascade")
    document_type_id = fields.Many2one(
        "l10n_latam.document.type", string="Tipo de comprobante", readonly=True)
    doc_code = fields.Char(string="Cód. AFIP", readonly=True)
    afip_last = fields.Integer(string="Último AFIP", readonly=True)
    odoo_last = fields.Integer(string="Último Odoo", readonly=True)
    afip_error = fields.Char(string="Error", readonly=True)
    status = fields.Char(string="Estado", readonly=True)
    to_sync = fields.Boolean(string="Sincronizar")
