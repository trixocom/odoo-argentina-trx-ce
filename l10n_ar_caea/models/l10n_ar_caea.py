# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Modelo `l10n_ar.caea` — un CAEA por (company, periodo, orden).

Cada quincena se solicita un CAEA distinto:

  * orden=1: 1° al 15° del mes
  * orden=2: 16° al fin del mes

El CAEA se solicita dentro de los **5 días previos** al inicio de la
quincena (RG 2926). Una vez asignado:

  * `state='active'`: vigente. Se puede usar para emitir.
  * `state='expired'`: pasó la fecha de vencimiento de la quincena.
  * `state='reported'`: ya fueron rendidos todos los comprobantes a AFIP.

Topea informativa: AFIP exige rendir los comprobantes dentro de los **8
días corridos** posteriores al cierre de la quincena. Si no se rinde,
hay penalidades.
"""
import logging
from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_ar_afip_ws.lib import transport as ws_transport
from odoo.addons.l10n_ar_afip_ws.lib import wsfe_caea as ws_caea

_logger = logging.getLogger(__name__)


class L10nArCaea(models.Model):
    _name = "l10n_ar.caea"
    _description = "CAEA — Código de Autorización Electrónico Anticipado"
    _inherit = ["mail.thread"]
    _order = "fch_vig_desde desc"
    _rec_name = "code"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    code = fields.Char(
        string="CAEA",
        size=14,
        required=True,
        copy=False,
        tracking=True,
    )
    periodo = fields.Char(
        string="Período (YYYYMM)",
        size=6,
        required=True,
        tracking=True,
        help="Período al que corresponde el CAEA (ej. '202605').",
    )
    orden = fields.Selection(
        selection=[
            ("1", "Primera quincena (1-15)"),
            ("2", "Segunda quincena (16-fin)"),
        ],
        required=True,
        tracking=True,
    )
    fch_vig_desde = fields.Date(string="Vigente desde", required=True, tracking=True)
    fch_vig_hasta = fields.Date(string="Vigente hasta", required=True, tracking=True)
    fch_topea_inf = fields.Date(
        string="Tope rendición",
        required=True,
        tracking=True,
        help="Fecha límite para informar comprobantes emitidos a AFIP "
             "(8 días corridos después del cierre de la quincena).",
    )
    fch_proceso = fields.Date(string="Fecha solicitud", readonly=True)
    state = fields.Selection(
        selection=[
            ("active", "Vigente"),
            ("expired", "Vencido"),
            ("reported", "Rendido"),
        ],
        default="active",
        tracking=True,
    )
    move_count = fields.Integer(
        compute="_compute_move_count",
        string="Comprobantes con este CAEA",
    )
    move_pending_count = fields.Integer(
        compute="_compute_move_count",
        string="Pendientes de rendir",
    )

    _sql_constraints = [
        ("uniq_company_periodo_orden",
         "unique(company_id, periodo, orden)",
         "Ya existe un CAEA para esa company en ese período y orden."),
    ]

    @api.depends("code")
    def _compute_move_count(self):
        Move = self.env["account.move"]
        for rec in self:
            domain = [("l10n_ar_afip_auth_code", "=", rec.code),
                      ("l10n_ar_afip_auth_mode", "=", "CAEA")]
            rec.move_count = Move.search_count(domain)
            rec.move_pending_count = Move.search_count(
                domain + [("l10n_ar_caea_rendido", "=", False)]
            )

    # ------------------------------------------------------------------
    # Solicitud
    # ------------------------------------------------------------------
    @api.model
    def request_caea(self, company, periodo, orden, triggered_by="manual"):
        """Llama `FECAEASolicitar` y persiste el resultado + log auditoría.

        :param triggered_by: 'manual' / 'cron' — sólo se usa para el log.
        :raises UserError: si AFIP devuelve error o no asigna CAEA.
        """
        Log = self.env["l10n_ar.caea.log"]
        # El CAEA lo otorga AFIP por CUIT: una sucursal (parent_id) usa el
        # de su compañía raíz.
        company = company._l10n_ar_afip_company()
        existing = self.search([
            ("company_id", "=", company.id),
            ("periodo", "=", periodo),
            ("orden", "=", str(orden)),
        ], limit=1)

        environment = company.l10n_ar_afip_ws_environment or "testing"
        connection = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
            company, "wsfe", environment,
        )
        auth = connection.get_auth()
        tr = ws_transport.CapturingTransport(
            session=ws_transport.build_afip_session(), timeout=60,
        )

        # Si ya existe, intento `FECAEAConsultar` para refrescar datos.
        if existing:
            try:
                response = ws_caea.caea_consultar(
                    auth=auth, periodo=periodo, orden=int(orden),
                    environment=environment, transport=tr,
                )
                Log._record_event(
                    self.env, company, "consultar", success=True,
                    message="Consulta exitosa CAEA %s/%s — %s" % (periodo, orden, response.get("caea") or "(sin código)"),
                    caea=existing,
                    xml_request=_safe_xml(tr.last_request),
                    xml_response=_safe_xml(tr.last_response),
                    triggered_by=triggered_by,
                )
                if response.get("caea"):
                    existing.write({
                        "code": response["caea"],
                        "fch_vig_desde": _to_date(response.get("fch_vig_desde")) or existing.fch_vig_desde,
                        "fch_vig_hasta": _to_date(response.get("fch_vig_hasta")) or existing.fch_vig_hasta,
                        "fch_topea_inf": _to_date(response.get("fch_topea_inf")) or existing.fch_topea_inf,
                    })
                    return existing
            except Exception as e:
                Log._record_event(
                    self.env, company, "consultar", success=False,
                    message="Error consultando CAEA %s/%s" % (periodo, orden),
                    caea=existing, error_msg=str(e),
                    xml_request=_safe_xml(tr.last_request),
                    xml_response=_safe_xml(tr.last_response),
                    triggered_by=triggered_by,
                )
                _logger.warning("FECAEAConsultar falló para %s: %s", existing.code, e)
            return existing

        # Solicitud nueva.
        try:
            response = ws_caea.caea_solicitar(
                auth=auth, periodo=periodo, orden=int(orden),
                environment=environment, transport=tr,
            )
        except Exception as e:
            Log._record_event(
                self.env, company, "solicitar", success=False,
                message="Error solicitando CAEA %s/%s" % (periodo, orden),
                error_msg=str(e),
                xml_request=_safe_xml(tr.last_request),
                xml_response=_safe_xml(tr.last_response),
                triggered_by=triggered_by,
            )
            # Re-raise para UI (manual) o silenciar (cron lo loguea)
            if triggered_by == "manual":
                raise UserError(_("AFIP rechazó la solicitud: %s") % e)
            _logger.warning("CAEA solicitar falló (cron) para %s/%s: %s", periodo, orden, e)
            return self.browse()

        caea_code = response.get("caea")
        if not caea_code:
            Log._record_event(
                self.env, company, "solicitar", success=False,
                message="AFIP no devolvió CAEA",
                error_msg=str(response),
                xml_request=_safe_xml(tr.last_request),
                xml_response=_safe_xml(tr.last_response),
                triggered_by=triggered_by,
            )
            if triggered_by == "manual":
                raise UserError(_("AFIP no devolvió CAEA. Respuesta: %s") % response)
            return self.browse()

        rec = self.create({
            "company_id": company.id,
            "code": caea_code,
            "periodo": str(response.get("periodo") or periodo),
            "orden": str(response.get("orden") or orden),
            "fch_vig_desde": _to_date(response.get("fch_vig_desde")),
            "fch_vig_hasta": _to_date(response.get("fch_vig_hasta")),
            "fch_topea_inf": _to_date(response.get("fch_topea_inf")),
            "fch_proceso": _to_date(response.get("fch_proceso")) or fields.Date.today(),
            "state": "active",
        })
        Log._record_event(
            self.env, company, "solicitar", success=True,
            message="CAEA %s asignado para %s/%s — vigente %s..%s" % (
                caea_code, rec.periodo, rec.orden, rec.fch_vig_desde, rec.fch_vig_hasta,
            ),
            caea=rec,
            xml_request=_safe_xml(tr.last_request),
            xml_response=_safe_xml(tr.last_response),
            triggered_by=triggered_by,
        )
        rec.message_post(body=_(
            "CAEA <b>%s</b> obtenido para período %s/%s. Vigente del %s al %s. "
            "Tope informativo: %s."
        ) % (rec.code, rec.periodo, rec.orden, rec.fch_vig_desde,
             rec.fch_vig_hasta, rec.fch_topea_inf))
        return rec

    # ------------------------------------------------------------------
    # Cron solicitud automática
    # ------------------------------------------------------------------
    @api.model
    def _cron_request_caea(self):
        """Cron diario 09:00 — solicita CAEA de la próxima quincena si
        estamos en ventana (11-15 ó 27-fin) y no existe ya.

        Sólo corre para companies con `l10n_ar_caea_enabled=True`.
        """
        today = fields.Date.context_today(self)
        Company = self.env["res.company"].sudo()
        # Solo compañías raíz: las sucursales comparten el CAEA de su CUIT.
        companies = Company.search([
            ("l10n_ar_caea_enabled", "=", True),
            ("parent_id", "=", False),
        ])
        for company in companies:
            try:
                self._cron_request_for_company(company, today)
            except Exception as e:
                _logger.exception(
                    "Cron CAEA solicitar — error company=%s: %s", company.name, e,
                )

    @api.model
    def _cron_request_for_company(self, company, today):
        """Lógica per-company: detecta ventana y solicita si corresponde."""
        from calendar import monthrange
        d = today.day
        month = today.month
        year = today.year

        # Ventana 1: días 11-15 → solicitar Q2 mes en curso (vigente 16-fin)
        if 11 <= d <= 15:
            periodo = "%04d%02d" % (year, month)
            orden = 2
            return self.request_caea(company, periodo, orden, triggered_by="cron")

        # Ventana 2: días 27-fin → solicitar Q1 mes siguiente (vigente 1-15)
        last_day = monthrange(year, month)[1]
        if 27 <= d <= last_day:
            next_month = month + 1
            next_year = year
            if next_month > 12:
                next_month = 1
                next_year += 1
            periodo = "%04d%02d" % (next_year, next_month)
            orden = 1
            return self.request_caea(company, periodo, orden, triggered_by="cron")

        # Fuera de ventana — no hacer nada.
        return self.browse()

    @api.model
    def find_active(self, company, target_date=None):
        """Devuelve el CAEA vigente para `target_date` (default hoy).

        Vigente = state=active AND fch_vig_desde <= date <= fch_vig_hasta.
        """
        d = target_date or fields.Date.context_today(self)
        company = company._l10n_ar_afip_company()
        return self.search([
            ("company_id", "=", company.id),
            ("state", "=", "active"),
            ("fch_vig_desde", "<=", d),
            ("fch_vig_hasta", ">=", d),
        ], order="fch_vig_desde desc", limit=1)

    @api.model
    def _cron_check_expiry(self):
        """Cron: marca como `expired` los CAEA cuya quincena ya terminó."""
        today = fields.Date.context_today(self)
        expired = self.search([
            ("state", "=", "active"),
            ("fch_vig_hasta", "<", today),
        ])
        expired.write({"state": "expired"})

    def action_view_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Comprobantes con CAEA %s") % self.code,
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("l10n_ar_afip_auth_code", "=", self.code),
                       ("l10n_ar_afip_auth_mode", "=", "CAEA")],
        }


def _safe_xml(b):
    """bytes → str (utf-8 safe). None si vacío."""
    if not b:
        return None
    if isinstance(b, bytes):
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1", errors="replace")
    return str(b)


def _to_date(s):
    """'YYYYMMDD' → date. None si no parsea."""
    if not s:
        return False
    s = str(s)
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return False
    if "-" in s:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return False
    return False
