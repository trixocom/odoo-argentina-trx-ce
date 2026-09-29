# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Herramientas técnicas de numeración AFIP a nivel `account.journal`.

Agrega, para diarios electrónicos WSFEv1, una acción (visible solo en modo
debug / usuarios técnicos) que consulta `FECompUltimoAutorizado` para cada
tipo de comprobante del punto de venta y permite **sincronizar** la
numeración de Odoo con AFIP cuando quedaron desalineados (p. ej. porque se
emitió un comprobante fuera del flujo normal y AFIP quedó adelantado).

El "override" que fija esta pantalla NO reescribe secuencias a mano: guarda
en el diario, por código de tipo de comprobante, el último número que AFIP
declara autorizado. El hook de numeración (`account.move._get_last_sequence`)
lo consume en la próxima emisión para numerar AFIP+1, y se auto-limpia una
vez que el comprobante obtiene CAE. Es el mismo mecanismo determinístico que
ya usa el arranque de secuencia, extendido para corregir drift a mitad de
secuencia bajo control del técnico.
"""
import json
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_ar_afip_ws.lib import transport as ws_transport
from odoo.addons.l10n_ar_afip_ws.lib import wsfe as ws_wsfe

_logger = logging.getLogger(__name__)


class AccountJournal(models.Model):
    _inherit = "account.journal"

    # Mapa JSON {codigo_tipo_cbte: ultimo_nro_AFIP} fijado por el técnico
    # desde el asistente de sincronización. Lo consume account.move al
    # numerar la próxima emisión (ver _get_last_sequence) y se limpia solo.
    l10n_ar_afip_seq_override = fields.Char(
        string="Override numeración AFIP (técnico)",
        copy=False,
        help="Uso interno. JSON {codigo_tipo: ultimo_nro_AFIP} para "
        "resincronizar la numeración con FECompUltimoAutorizado. "
        "Se aplica en la próxima emisión y se auto-limpia al obtener CAE.",
    )

    def _l10n_ar_afip_last_authorized(self, cbte_tipo):
        """FECompUltimoAutorizado(PV del diario, cbte_tipo) -> int (0 si no hay).

        Consulta de solo lectura: NO consume número ni altera nada en AFIP.
        """
        self.ensure_one()
        company = self.company_id
        environment = company.l10n_ar_afip_ws_environment or "testing"
        connection = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
            company, "wsfe", environment)
        auth = connection.get_auth()
        # build_transport() da el transporte con cache de WSDL y timeouts
        # separados. Armarlo a mano dejaba esta consulta fuera de la cache: el
        # 12/08/2026, con AFIP devolviendo 503 en el WSDL, la emision seguia
        # andando y el diagnostico no.
        transport = ws_transport.build_transport()
        res = ws_wsfe.comp_ultimo_autorizado(
            auth=auth, pto_vta=self.l10n_ar_afip_pos_number,
            cbte_tipo=int(cbte_tipo), environment=environment,
            transport=transport)
        return int((res or {}).get("cbte_nro") or 0)

    def _l10n_ar_afip_relevant_doctypes(self):
        """Tipos de comprobante a diagnosticar para este diario.

        Los tipos efectivamente emitidos por el diario (distinct sobre moves
        posteados) que además tengan código numérico AFIP. Es determinístico:
        solo muestra lo que este PV realmente usó.
        """
        self.ensure_one()
        moves = self.env["account.move"].search([
            ("journal_id", "=", self.id),
            ("state", "=", "posted"),
            ("l10n_latam_document_type_id", "!=", False),
        ])
        dts = moves.mapped("l10n_latam_document_type_id").filtered(
            lambda d: d.code and d.code.isdigit())
        return dts.sorted(key=lambda d: int(d.code))

    def _l10n_ar_afip_odoo_last(self, document_type):
        """Último número emitido en Odoo para (este diario, tipo)."""
        self.ensure_one()
        last = self.env["account.move"].search([
            ("journal_id", "=", self.id),
            ("l10n_latam_document_type_id", "=", document_type.id),
            ("state", "=", "posted"),
            ("name", "not in", (False, "/")),
        ], limit=1, order="id desc")
        # No confiamos en el orden por id para el número; tomamos el máximo real.
        nums = []
        for m in self.env["account.move"].search([
            ("journal_id", "=", self.id),
            ("l10n_latam_document_type_id", "=", document_type.id),
            ("state", "=", "posted"),
        ]):
            try:
                nums.append(int((m.name or "").split("-")[-1]))
            except (ValueError, IndexError):
                continue
        return max(nums) if nums else 0

    def action_l10n_ar_afip_check_last_authorized(self):
        """Abre el asistente de diagnóstico/sincronización de numeración AFIP."""
        self.ensure_one()
        if self._l10n_ar_afip_ws_for_emission != "wsfe":
            raise UserError(_(
                "El diario %s no es un diario de emisión electrónica WSFEv1.",
                self.display_name))
        wizard = self.env["l10n_ar.afip.seq.sync.wizard"].create({
            "journal_id": self.id,
        })
        wizard._populate_lines()
        return {
            "type": "ir.actions.act_window",
            "name": _("AFIP · Último autorizado / Sincronizar numeración"),
            "res_model": "l10n_ar.afip.seq.sync.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    # -- helpers de override consumidos por account.move --

    def _l10n_ar_afip_get_override(self, doc_code):
        self.ensure_one()
        raw = self.l10n_ar_afip_seq_override
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        val = data.get(str(doc_code))
        return int(val) if val is not None else None

    def _l10n_ar_afip_set_override(self, doc_code, last_nro):
        self.ensure_one()
        try:
            data = json.loads(self.l10n_ar_afip_seq_override or "{}")
        except (ValueError, TypeError):
            data = {}
        data[str(doc_code)] = int(last_nro)
        self.l10n_ar_afip_seq_override = json.dumps(data)

    def _l10n_ar_afip_clear_override(self, doc_code):
        self.ensure_one()
        if not self.l10n_ar_afip_seq_override:
            return
        try:
            data = json.loads(self.l10n_ar_afip_seq_override)
        except (ValueError, TypeError):
            return
        if str(doc_code) in data:
            data.pop(str(doc_code))
            self.l10n_ar_afip_seq_override = json.dumps(data) if data else False
