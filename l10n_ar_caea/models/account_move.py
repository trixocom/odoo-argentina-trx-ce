# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Extensiones a `account.move` para soporte CAEA (RG 5785/2025).

* `l10n_ar_caea_id`: link al `l10n_ar.caea` que se usó al emitir.
* `l10n_ar_caea_rendido`: True si el comprobante ya fue informado a AFIP
  vía `FECAEARegInformativo`.
* `l10n_ar_caea_emission_dt`: fecha/hora REAL de emisión (relevante para
  comprobantes emitidos offline desde POS y para CbteFchHsGen).
* Emisión en PV exclusivo CAEA: los moves posteados en un journal con
  `l10n_ar_afip_pos_caea` reciben el CAEA vigente automáticamente, sin
  tocar la red (hook en `_post`).
* Contingencia reactiva: si WSFEv1 da `TransportError`, el move se
  RE-RUTEA al diario de contingencia (`l10n_ar_caea_journal_id`) y se
  numera en el PV exclusivo CAEA — exigencia de RG 5785/2025. (Antes se
  estampaba el CAEA sobre el PV CAE, lo cual AFIP rechaza al rendir.)
* Contingencia preventiva: si `afip_webservice_monitor` está instalado y
  reporta WSFE caído, se re-rutea directo sin esperar el timeout.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_ar_afip_ws.lib import errors as ws_errors
from odoo.addons.l10n_ar_afip_ws.lib import transport as ws_transport
from odoo.addons.l10n_ar_afip_ws.lib import wsfe_caea as ws_caea

_logger = logging.getLogger(__name__)


def _safe(b):
    """bytes → str (utf-8 safe). None si vacío."""
    if not b:
        return None
    if isinstance(b, bytes):
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1", errors="replace")
    return str(b)


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_ar_caea_id = fields.Many2one(
        "l10n_ar.caea",
        string="CAEA usado",
        copy=False,
        readonly=True,
        index=True,
    )
    l10n_ar_caea_rendido = fields.Boolean(
        string="CAEA rendido",
        copy=False,
        readonly=True,
        help="True si el comprobante ya fue informado a AFIP vía "
             "FECAEARegInformativo. Los moves con CAEA pendientes de "
             "rendir se procesan por cron diario antes de la fecha tope.",
    )
    l10n_ar_caea_rendido_date = fields.Datetime(
        string="Fecha rendición CAEA",
        copy=False,
        readonly=True,
    )
    l10n_ar_caea_emission_dt = fields.Datetime(
        string="Emisión real (CAEA)",
        copy=False,
        readonly=True,
        help="Fecha/hora real en que se emitió el comprobante en "
             "contingencia (p.ej. en el POS offline). Se usa como "
             "CbteFchHsGen en la rendición FECAEARegInformativo.",
    )

    # ------------------------------------------------------------------
    # Emisión — dispatcher y contingencia
    # ------------------------------------------------------------------
    def _l10n_ar_request_cae(self):
        """Override del dispatcher CAE.

        * Journal PV exclusivo CAEA → aplica CAEA local, jamás WSFE.
          (Cubre invocaciones manuales; el hook `_post` de l10n_ar_trx_edi ya
          saltea estos journals porque no tienen WS de emisión.)
        * Preempción: si el monitor reporta WSFE caído → re-ruteo directo
          al diario CAEA sin esperar el timeout de 60s.
        """
        self.ensure_one()
        if self.journal_id.l10n_ar_afip_pos_caea:
            return self._l10n_ar_emit_on_caea_journal()
        if self._l10n_ar_caea_should_preempt():
            _logger.info(
                "Monitor reporta WSFE caído — re-ruteo preventivo a CAEA: %s",
                self.display_name,
            )
            return self._l10n_ar_reroute_to_caea(reason="preempt_monitor")
        return super()._l10n_ar_request_cae()

    def _l10n_ar_request_cae_wsfe(self):
        """Override con fallback a CAEA si WSFE no responde.

        A diferencia de versiones anteriores (<19.0.2.0.0), el fallback
        NO estampa el CAEA sobre el PV CAE: re-rutea el comprobante al
        diario de contingencia (PV exclusivo CAEA) como exige la norma.
        """
        self.ensure_one()
        try:
            return super()._l10n_ar_request_cae_wsfe()
        except ws_errors.TransportError as e:
            if not self.company_id.l10n_ar_caea_enabled:
                raise
            _logger.warning(
                "WSFEv1 transport error en %s — re-ruteando a CAEA: %s",
                self.display_name, e,
            )
            return self._l10n_ar_reroute_to_caea(reason=str(e))

    def _l10n_ar_caea_should_preempt(self):
        """True si conviene ni intentar WSFE (monitor dice caído).

        Usa el estado CACHEADO del monitor (sin re-chequear contra AFIP)
        para decidir rápido. Guardas: módulo monitor instalado, CAEA
        habilitado, diario de contingencia mapeado y CAEA vigente.
        """
        self.ensure_one()
        if not self.company_id.l10n_ar_caea_enabled:
            return False
        if not self.journal_id.l10n_ar_caea_journal_id:
            return False
        if "afip.service.status" not in self.env:
            return False
        try:
            available = self.env["afip.service.status"].sudo().is_afip_available("wsfe")
        except Exception:  # noqa: BLE001 — el monitor nunca debe romper la emisión
            return False
        if available:
            return False
        return bool(self.env["l10n_ar.caea"].find_active(
            self.company_id,
            target_date=self.invoice_date or fields.Date.context_today(self),
        ))

    def _l10n_ar_reroute_to_caea(self, reason=""):
        """Mueve este comprobante al diario de contingencia y le aplica
        el CAEA vigente. El número se genera en la secuencia local del
        PV exclusivo CAEA (sin red).

        Se llama con el move `posted` (desde el hook de l10n_ar_trx_edi) o
        `draft`. Si estaba posted: draft → cambio de diario → repost.
        El repost no pide CAE porque el move ya sale con auth_code.
        """
        self.ensure_one()
        company = self.company_id
        caea_journal = self.journal_id.l10n_ar_caea_journal_id
        if not caea_journal:
            raise UserError(_(
                "WSFEv1 no responde y el diario %s no tiene configurado "
                "un 'Diario de contingencia CAEA'. Configuralo en el "
                "diario (pestaña Asientos contables) apuntando al PV "
                "exclusivo CAEA habilitado en ARCA."
            ) % self.journal_id.name)
        caea = self.env["l10n_ar.caea"].find_active(
            company,
            target_date=self.invoice_date or fields.Date.context_today(self),
        )
        if not caea:
            raise UserError(_(
                "WSFEv1 no respondió y no hay CAEA vigente para %s. "
                "Solicitá un CAEA desde Configuración → Localización "
                "Argentina → 'Solicitar CAEA' antes del próximo período."
            ) % company.name)

        old_name = self.name
        old_journal_name = self.journal_id.name
        was_posted = self.state == "posted"
        if was_posted:
            self.button_draft()
        self.write({"journal_id": caea_journal.id, "name": "/"})
        self._l10n_ar_apply_caea(caea)
        self.env["l10n_ar.caea.log"]._record_event(
            self.env, company, "solicitar", success=True,
            caea=caea,
            message="Contingencia CAEA: %s re-ruteado de %s a PV %s. Motivo: %s" % (
                old_name, old_journal_name,
                caea_journal.l10n_ar_afip_pos_number, reason or "WSFE caído",
            ),
            triggered_by="post_fallback",
        )
        if was_posted:
            self.action_post()

    def _l10n_ar_emit_on_caea_journal(self):
        """Emisión directa en un PV exclusivo CAEA (sin red)."""
        self.ensure_one()
        if self.l10n_ar_afip_auth_code:
            return
        caea = self.env["l10n_ar.caea"].find_active(
            self.company_id,
            target_date=self.invoice_date or fields.Date.context_today(self),
        )
        if not caea:
            raise UserError(_(
                "No hay CAEA vigente para emitir en el diario de "
                "contingencia %s. Solicitá un CAEA primero."
            ) % self.journal_id.name)
        self._l10n_ar_apply_caea(caea)

    def _l10n_ar_apply_caea(self, caea):
        """Asigna el CAEA al move como auth_code, sin pegarle a AFIP.

        El comprobante queda emitido off-line; se rinde después con el
        cron (`FECAEARegInformativo`).
        """
        self.ensure_one()
        self.write({
            "l10n_ar_afip_auth_mode": "CAEA",
            "l10n_ar_afip_auth_code": caea.code,
            "l10n_ar_afip_auth_code_due": caea.fch_vig_hasta,
            "l10n_ar_afip_result": "A",  # asumido — se confirma al rendir
            "l10n_ar_caea_id": caea.id,
            "l10n_ar_caea_rendido": False,
            "l10n_ar_caea_emission_dt": (
                self.l10n_ar_caea_emission_dt or fields.Datetime.now()
            ),
            "l10n_ar_afip_observations": _(
                "Comprobante emitido en modo CAEA por contingencia. "
                "Pendiente de rendición a AFIP (FECAEARegInformativo)."
            ),
        })
        self.message_post(body=_(
            "📦 Emitido con CAEA <b>%s</b> (modo contingencia). "
            "Pendiente de rendición."
        ) % caea.code)
        _logger.info(
            "Move %s emitido con CAEA %s — pendiente rendición.",
            self.name, caea.code,
        )

    # ------------------------------------------------------------------
    # Hook _post — diarios CAEA emiten sin red
    # ------------------------------------------------------------------
    def _post(self, soft=True):
        """Tras el post (y el hook CAE de l10n_ar_trx_edi, que saltea los
        diarios CAEA por no tener WS), aplica el CAEA vigente a los
        comprobantes posteados en PV exclusivos CAEA.

        Si no hay CAEA vigente, revertimos a borrador con error claro —
        misma semántica atómica que el hook de l10n_ar_trx_edi.
        """
        posted = super()._post(soft=soft)
        to_revert = self.env["account.move"]
        errors = []
        for move in posted:
            if move.country_code != "AR":
                continue
            if move.move_type not in ("out_invoice", "out_refund", "out_receipt"):
                continue
            if not move.journal_id.l10n_ar_afip_pos_caea:
                continue
            if move.l10n_ar_afip_auth_code:
                continue
            try:
                move._l10n_ar_emit_on_caea_journal()
            except UserError as exc:
                to_revert |= move
                errors.append((move.display_name or str(move.id), str(exc)))
        if to_revert:
            for m in to_revert:
                try:
                    m.button_draft()
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "No se pudo revertir a borrador %s.", m.display_name,
                    )
            msgs = "\n\n".join("• %s\n%s" % (n, e) for n, e in errors)
            raise UserError(_(
                "No se pudo emitir con CAEA %(count)d comprobante(s):\n\n%(msgs)s",
                count=len(to_revert), msgs=msgs,
            ))
        return posted

    # ------------------------------------------------------------------
    # Rendición informativa
    # ------------------------------------------------------------------
    @api.model
    def _cron_caea_rendir(self):
        """Cron diario: rinde a AFIP los CAEA — comprobantes emitidos
        con `FECAEARegInformativo` y CAEA sin uso con
        `FECAEASinMovimientoInformar`.

        Sólo corre para companies con `l10n_ar_caea_enabled=True`.
        """
        Company = self.env["res.company"].sudo()
        # Solo compañías raíz: el CAEA y su rendición son por CUIT; los
        # comprobantes de las sucursales se buscan con child_of.
        active_companies = Company.search([
            ("l10n_ar_caea_enabled", "=", True),
            ("parent_id", "=", False),
        ])
        if not active_companies:
            return True

        # Por cada company habilitada, procesar CAEAs cuyo cierre haya
        # pasado (fch_vig_hasta < hoy) y aún estén en estado active.
        today = fields.Date.context_today(self)
        Caea = self.env["l10n_ar.caea"]
        for company in active_companies:
            caeas_a_rendir = Caea.search([
                ("company_id", "=", company.id),
                ("state", "=", "active"),
                ("fch_vig_hasta", "<", today),
            ])
            for caea in caeas_a_rendir:
                try:
                    self._l10n_ar_caea_rendir_caea(caea)
                except Exception as e:
                    _logger.exception(
                        "Cron CAEA rendir — error caea=%s: %s", caea.code, e,
                    )

            # También rendimos comprobantes con CAEA aún vigente si están
            # cerca del tope informativo (próximos 3 días) — para no
            # acumular hasta el último día.
            pending_close = self.search([
                ("company_id", "child_of", company.id),
                ("l10n_ar_afip_auth_mode", "=", "CAEA"),
                ("l10n_ar_caea_rendido", "=", False),
                ("state", "=", "posted"),
                ("l10n_ar_caea_id", "!=", False),
            ])
            if pending_close:
                self._l10n_ar_caea_rendir_pending(pending_close)
        return True

    @api.model
    def _l10n_ar_caea_rendir_caea(self, caea):
        """Rinde un CAEA específico: si tiene moves pendientes →
        FECAEARegInformativo. Si no → FECAEASinMovimientoInformar
        por cada punto de venta CAEA. Marca como `reported` al final."""
        moves = self.search([
            ("company_id", "child_of", caea.company_id.id),
            ("l10n_ar_afip_auth_mode", "=", "CAEA"),
            ("l10n_ar_caea_id", "=", caea.id),
            ("l10n_ar_caea_rendido", "=", False),
            ("state", "=", "posted"),
        ])
        if moves:
            self._l10n_ar_caea_rendir_pending(moves)
            # Si ya quedaron todos rendidos → marcar caea reported
            still_pending = moves.filtered(lambda m: not m.l10n_ar_caea_rendido)
            if not still_pending:
                caea.write({"state": "reported"})
            # PVs CAEA que NO emitieron nada en la quincena igual deben
            # informarse "sin movimiento".
            used_journals = moves.mapped("journal_id")
            self._l10n_ar_caea_sin_movimiento(caea, exclude_journals=used_journals)
        else:
            # Sin movimiento en todos los PV CAEA de la company.
            self._l10n_ar_caea_sin_movimiento(caea)
            caea.write({"state": "reported"})

    @api.model
    def _l10n_ar_caea_rendir_pending(self, moves):
        """Agrupa moves por (journal, cbte_tipo) y manda batches."""
        grouped = {}
        for m in moves:
            key = (m.journal_id.id, int(m.l10n_latam_document_type_id.code or 0))
            grouped.setdefault(key, self.browse())
            grouped[key] |= m
        for (journal_id, cbte_tipo), batch in grouped.items():
            try:
                batch._l10n_ar_caea_rendir_batch()
            except Exception as e:
                _logger.exception(
                    "Error rindiendo batch CAEA journal=%s tipo=%s: %s",
                    journal_id, cbte_tipo, e,
                )

    @api.model
    def _l10n_ar_caea_sin_movimiento(self, caea, exclude_journals=None):
        """Informa a AFIP los PV CAEA que cierran la quincena sin
        movimientos (`FECAEASinMovimientoInformar`).

        RG 5785/2025: SOLO se informan los puntos de venta exclusivos
        CAEA. Informar un PV CAE acá es un error (AFIP lo rechaza).
        """
        Log = self.env["l10n_ar.caea.log"]
        company = caea.company_id
        environment = company.l10n_ar_afip_ws_environment or "testing"
        Journal = self.env["account.journal"].sudo()
        journals = Journal.search([
            ("company_id", "child_of", company.id),
            ("l10n_ar_afip_pos_caea", "=", True),
            ("l10n_ar_afip_pos_number", "!=", False),
        ])
        if exclude_journals:
            journals -= exclude_journals
        if not journals:
            Log._record_event(
                self.env, company, "sin_movimiento", success=False,
                caea=caea,
                message="No hay journals PV CAEA para informar sin movimiento",
                triggered_by="cron",
            )
            return

        connection = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
            company, "wsfe", environment,
        )
        auth = connection.get_auth()
        for journal in journals:
            tr = ws_transport.CapturingTransport(
                session=ws_transport.build_afip_session(), timeout=60,
            )
            try:
                response = ws_caea.caea_sin_movimiento_informar(
                    auth=auth, pto_vta=journal.l10n_ar_afip_pos_number,
                    caea=caea.code,
                    environment=environment, transport=tr,
                )
                Log._record_event(
                    self.env, company, "sin_movimiento", success=True,
                    caea=caea, message="Sin movimiento informado POS=%s — %s" % (
                        journal.l10n_ar_afip_pos_number, response.get("resultado") or "(sin resultado)",
                    ),
                    xml_request=_safe(tr.last_request),
                    xml_response=_safe(tr.last_response),
                    triggered_by="cron",
                )
            except Exception as e:
                Log._record_event(
                    self.env, company, "sin_movimiento", success=False,
                    caea=caea, message="Error sin movimiento POS=%s" % journal.l10n_ar_afip_pos_number,
                    error_msg=str(e),
                    xml_request=_safe(tr.last_request),
                    xml_response=_safe(tr.last_response),
                    triggered_by="cron",
                )
                _logger.warning(
                    "FECAEASinMovimientoInformar falló POS=%s caea=%s: %s",
                    journal.l10n_ar_afip_pos_number, caea.code, e,
                )

    def _l10n_ar_caea_rendir_batch(self):
        """Manda este recordset (mismo company+journal+cbte_tipo) a
        `FECAEARegInformativo` y loguea en `l10n_ar.caea.log`."""
        if not self:
            return
        Log = self.env["l10n_ar.caea.log"]
        first = self[0]
        company = first.company_id
        journal = first.journal_id
        cbte_tipo = int(first.l10n_latam_document_type_id.code or 0)
        caea = first.l10n_ar_caea_id
        environment = company.l10n_ar_afip_ws_environment or "testing"
        connection = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
            company, "wsfe", environment,
        )
        auth = connection.get_auth()
        tr = ws_transport.CapturingTransport(
            session=ws_transport.build_afip_session(), timeout=60,
        )

        det_list = []
        for m in self:
            base = m._l10n_ar_get_wsfe_payload()
            det = base["FeDetReq"][0]["FECAEDetRequest"].copy()
            det["CAEA"] = m.l10n_ar_afip_auth_code
            det["CbteFchHsGen"] = m._l10n_ar_caea_get_fch_hs_gen()
            det_list.append({"FECAEADetRequest": det})

        req = {
            "FeCabReq": {
                "CantReg": len(det_list),
                "PtoVta": journal.l10n_ar_afip_pos_number,
                "CbteTipo": cbte_tipo,
            },
            "FeDetReq": det_list,
        }

        try:
            response = ws_caea.caea_reg_informativo(
                auth=auth, fe_caea_reg_inf_req=req,
                environment=environment, transport=tr,
            )
        except Exception as e:
            Log._record_event(
                self.env, company, "rendir", success=False,
                caea=caea, message="Error rindiendo batch journal=%s tipo=%s" % (journal.name, cbte_tipo),
                error_msg=str(e),
                affected_count=len(self),
                xml_request=_safe(tr.last_request),
                xml_response=_safe(tr.last_response),
                triggered_by="cron",
            )
            raise

        result_by_nro = {
            d["CbteDesde"]: d["Resultado"]
            for d in response.get("detalle") or []
            if d.get("CbteDesde")
        }
        rendidos = self.browse()
        for m in self:
            try:
                nro = m._l10n_ar_get_cbte_nro()
            except Exception:
                continue
            if result_by_nro.get(nro) == "A":
                rendidos |= m

        rendidos.write({
            "l10n_ar_caea_rendido": True,
            "l10n_ar_caea_rendido_date": fields.Datetime.now(),
        })
        for m in rendidos:
            m.message_post(body=_(
                "📤 Rendición CAEA confirmada por AFIP (FECAEARegInformativo)."
            ))

        Log._record_event(
            self.env, company, "rendir",
            success=(len(rendidos) == len(self)),
            caea=caea,
            message="Rendidos %s/%s — journal=%s tipo=%s" % (
                len(rendidos), len(self), journal.name, cbte_tipo,
            ),
            affected_count=len(rendidos),
            xml_request=_safe(tr.last_request),
            xml_response=_safe(tr.last_response),
            triggered_by="cron",
        )
        _logger.info(
            "Batch CAEA rendido: %s/%s comprobantes aprobados",
            len(rendidos), len(self),
        )

    def _l10n_ar_caea_get_fch_hs_gen(self):
        """CbteFchHsGen (YYYYMMDDHHMMSS, hora local AR) para la rendición.

        Usa la fecha/hora real de emisión si la tenemos (POS offline);
        si no, invoice_date a las 08:00 (comportamiento histórico).
        """
        self.ensure_one()
        if self.l10n_ar_caea_emission_dt:
            local_dt = fields.Datetime.context_timestamp(
                self.with_context(tz=self.company_id.partner_id.tz
                                  or "America/Argentina/Buenos_Aires"),
                self.l10n_ar_caea_emission_dt,
            )
            return local_dt.strftime("%Y%m%d%H%M%S")
        if self.invoice_date:
            return self.invoice_date.strftime("%Y%m%d") + "080000"
        return fields.Date.context_today(self).strftime("%Y%m%d") + "080000"
