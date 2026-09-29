# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Constatación de comprobantes recibidos via WSCDC.

Espejo del flujo de emisión (`account_move.py`) pero del lado del
**comprador**: dada una factura de proveedor con CAE/CAI/CAEA, le pega
al WS Constatación De Comprobantes para verificar que ARCA realmente la
autorizó. Útil para evitar tomar crédito fiscal de facturas apócrifas.

Política de uso (controlada por `res.company.l10n_ar_supplier_validation_type`):

  - **no_disponible**: el botón "Constatar en ARCA" no se muestra. La
    feature está apagada.
  - **disponible**: el botón aparece en facturas IN. Operador manual.
  - **requerido**: además, el override de `_post()` valida automática-
    mente las facturas IN con CAE/CAI/CAEA antes de permitir confirmar.
    Si ARCA rechaza, el post se aborta.

Resultado WSCDC (campo `l10n_ar_afip_verification_result`):
  - 'A' Aprobado: el comprobante existe y matchea con AFIP.
  - 'O' Observado: existe pero algún campo no matchea (importe, fecha,
    receptor) — generalmente un typo de carga.
  - 'R' Rechazado: no existe en AFIP. Posible factura apócrifa.

Cuando ARCA devuelve `Observaciones` (matching parcial), las posteamos
al chatter de la factura para que el operador pueda revisarlas.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.l10n_ar_afip_ws.lib import transport as ws_transport
from odoo.addons.l10n_ar_afip_ws.lib import wscdc as ws_wscdc
from odoo.addons.l10n_ar_afip_ws.lib import errors as ws_errors

_logger = logging.getLogger(__name__)


# Tipos de comprobante que WSCDC acepta para constatar. AFIP publica la lista
# completa en `ComprobantesTiposConsultar`; estos son los más usuales que
# llegan del proveedor (FA, NC, ND clases A/B/C, FCE MiPyME, comprobantes M).
# Si llega uno fuera de la lista, igual lo intentamos: WSCDC va a contestar
# con un error específico que el operador puede leer.
WSCDC_CBTE_TIPOS = {
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
    34, 35, 39, 40, 51, 52, 53, 54, 60, 61, 63, 64,
    91, 99,
    201, 202, 203, 206, 207, 208, 211, 212, 213,  # FCE
}


class AccountMove(models.Model):
    _inherit = "account.move"

    # ------------------------------------------------------------------
    # Campos
    # ------------------------------------------------------------------
    l10n_ar_supplier_validation_available = fields.Boolean(
        string="WSCDC disponible para esta factura",
        compute="_compute_l10n_ar_supplier_validation_available",
        help=(
            "True si esta factura es candidata a constatar en ARCA "
            "(es factura de proveedor argentina con CAE/CAI/CAEA y la "
            "empresa tiene la feature habilitada)."
        ),
    )
    l10n_ar_afip_verification_result = fields.Selection(
        selection=[
            ("A", "Aprobado"),
            ("O", "Observado"),
            ("R", "Rechazado"),
        ],
        string="Resultado constatación ARCA",
        copy=False,
        readonly=True,
        tracking=True,
    )
    l10n_ar_afip_verification_date = fields.Datetime(
        string="Fecha constatación ARCA",
        copy=False,
        readonly=True,
    )
    l10n_ar_afip_verification_observations = fields.Text(
        string="Observaciones constatación ARCA",
        copy=False,
        readonly=True,
    )
    l10n_ar_afip_verification_xml_request = fields.Text(
        string="WSCDC: XML enviado",
        copy=False,
        readonly=True,
        groups="account.group_account_manager",
    )
    l10n_ar_afip_verification_xml_response = fields.Text(
        string="WSCDC: XML recibido",
        copy=False,
        readonly=True,
        groups="account.group_account_manager",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends(
        "country_code", "move_type",
        "l10n_ar_afip_auth_code", "l10n_ar_afip_auth_mode",
        "company_id.l10n_ar_supplier_validation_type",
    )
    def _compute_l10n_ar_supplier_validation_available(self):
        for move in self:
            move.l10n_ar_supplier_validation_available = bool(
                move.country_code == "AR"
                and move.move_type in ("in_invoice", "in_refund")
                and move.l10n_ar_afip_auth_code
                and move.l10n_ar_afip_auth_mode in ("CAE", "CAEA", "CAI")
                and move.company_id.l10n_ar_supplier_validation_type
                in ("disponible", "requerido")
            )

    # ------------------------------------------------------------------
    # Construcción del request
    # ------------------------------------------------------------------
    def _l10n_ar_get_wscdc_payload(self):
        """Arma el dict `CmpReq` para `ComprobanteConstatar`.

        Detecta si es factura IN (recibida de proveedor) o OUT
        (auto-constatación de una emitida por nosotros) e invierte
        emisor/receptor acorde:

        * **Factura IN** (in_invoice / in_refund):
          - emisor = `commercial_partner_id` (el proveedor).
          - receptor = `company_id.partner_id` (nosotros).

        * **Factura OUT** (out_invoice / out_refund — auto-constatar):
          - emisor = `company_id.partner_id` (nosotros).
          - receptor = `commercial_partner_id` (cliente).

        OJO con tipos: WSCDC valida `CuitEmisor` como **int**
        (`xs:long`); si lo mandás como string, AFIP responde "cuit del
        emisor no corresponde" (obs 102) aunque el número sea correcto.
        """
        self.ensure_one()
        if not self.l10n_ar_afip_auth_code:
            raise UserError(_(
                "La factura %s no tiene CAE/CAI/CAEA cargado. Cargá el "
                "código de autorización antes de constatar."
            ) % self.display_name)
        if not self.l10n_ar_afip_auth_mode:
            raise UserError(_(
                "La factura %s no tiene modo de autorización (CAE/CAEA/CAI). "
                "Indicalo en la pestaña AFIP."
            ) % self.display_name)
        if not self.invoice_date:
            raise UserError(_(
                "La factura %s no tiene fecha de comprobante."
            ) % self.display_name)
        if not self.l10n_latam_document_type_id:
            raise UserError(_(
                "La factura %s no tiene tipo de documento argentino."
            ) % self.display_name)

        is_outbound = self.move_type in ("out_invoice", "out_refund")

        if is_outbound:
            emisor_partner = self.company_id.partner_id
            receptor_partner = self.commercial_partner_id
        else:
            emisor_partner = self.commercial_partner_id
            receptor_partner = self.company_id.partner_id

        # Emisor: WSCDC exige CUIT (11 dígitos) — AFIP no autoriza CAE
        # a sujetos sin CUIT, así que en la práctica el emisor SIEMPRE
        # tiene CUIT.
        emisor_vat = (emisor_partner.vat or "").replace("-", "").replace(" ", "")
        if not emisor_vat.isdigit() or len(emisor_vat) != 11:
            raise UserError(_(
                "El emisor %s no tiene un CUIT válido (vat=%s). "
                "WSCDC necesita el CUIT del emisor para constatar."
            ) % (emisor_partner.display_name, emisor_partner.vat))

        # Punto de venta + número del comprobante.
        # `l10n_latam_document_number` está en formato "0003-00000123".
        # En IN puede estar también en `ref` cuando el operador lo cargó
        # ahí. En OUT siempre está en l10n_latam_document_number.
        doc_num = self.l10n_latam_document_number or self.ref or ""
        pto_vta, cbte_nro = self._l10n_ar_split_doc_number(doc_num)

        # Receptor: tipo+nº de doc. Puede no ser CUIT (consumidor final
        # con DNI o sin doc en FB).
        receptor_doc_tipo, receptor_doc_nro = self._l10n_ar_get_doc_for(receptor_partner)

        cbte_tipo = int(self.l10n_latam_document_type_id.code or 0)
        return {
            "CbteModo": self.l10n_ar_afip_auth_mode,
            "CuitEmisor": int(emisor_vat),
            "PtoVta": pto_vta,
            "CbteTipo": cbte_tipo,
            "CbteNro": cbte_nro,
            "CbteFch": self.invoice_date.strftime("%Y%m%d"),
            "ImpTotal": "%.2f" % (self.amount_total or 0.0),
            "CodAutorizacion": self.l10n_ar_afip_auth_code,
            "DocTipoReceptor": str(receptor_doc_tipo),
            "DocNroReceptor": str(receptor_doc_nro),
        }

    def _l10n_ar_split_doc_number(self, doc_num):
        """Parsea 'FA-A 0003-00000123' o '0003-00000123' a (3, 123)."""
        self.ensure_one()
        digits_only = []
        for chunk in doc_num.replace(" ", "-").split("-"):
            chunk = chunk.strip()
            if chunk.isdigit():
                digits_only.append(int(chunk))
        if len(digits_only) < 2:
            raise UserError(_(
                "No pude parsear el número de comprobante %r de la factura %s. "
                "Esperaba formato 'NNNN-NNNNNNNN'."
            ) % (doc_num, self.display_name))
        return digits_only[-2], digits_only[-1]

    def _l10n_ar_get_emitter_doc(self):
        """Devuelve (tipo_doc, nro_doc) de NUESTRA empresa.

        Helper legado para compat con código viejo que asumía factura IN.
        Equivalente a `_l10n_ar_get_doc_for(self.company_id.partner_id)`.
        """
        self.ensure_one()
        return self._l10n_ar_get_doc_for(self.company_id.partner_id)

    def _l10n_ar_get_doc_for(self, partner):
        """Devuelve (tipo_doc, nro_doc) AFIP del partner dado.

        - 80 (CUIT) si tiene VAT de 11 dígitos.
        - 96 (DNI) si tiene `l10n_latam_identification_type_id` con
          afip_code='96' y un VAT numérico (típico consumidor final con
          DNI).
        - 99 (Sin identificar) en cualquier otro caso. AFIP acepta
          DocNroReceptor=0 en ese caso.
        """
        self.ensure_one()
        if not partner:
            return 99, 0
        vat = (partner.vat or "").replace("-", "").replace(" ", "")
        # CUIT
        if vat.isdigit() and len(vat) == 11:
            return 80, int(vat)
        # Identification type explícito (DNI, etc.)
        idt = partner.l10n_latam_identification_type_id
        if idt and idt.l10n_ar_afip_code:
            try:
                code = int(idt.l10n_ar_afip_code)
            except (TypeError, ValueError):
                code = 99
            if vat.isdigit():
                return code, int(vat)
        return 99, 0

    # ------------------------------------------------------------------
    # Acción + flujo
    # ------------------------------------------------------------------
    def action_l10n_ar_constatar_arca(self):
        """Botón manual: constatar las facturas seleccionadas en ARCA."""
        for move in self:
            move._l10n_ar_verify_on_arca()

    def _l10n_ar_verify_on_arca(self):
        """Ejecuta WSCDC para este move y guarda el resultado."""
        self.ensure_one()

        cbte_tipo = int(self.l10n_latam_document_type_id.code or 0)
        if cbte_tipo not in WSCDC_CBTE_TIPOS:
            raise UserError(_(
                "El tipo de comprobante %s (código %s) no es constatable "
                "por WSCDC. AFIP solo permite constatar comprobantes "
                "electrónicos de venta (FA, NC, ND clases A/B/C/M y FCE)."
            ) % (self.l10n_latam_document_type_id.display_name, cbte_tipo))

        company = self.company_id
        environment = company.l10n_ar_afip_ws_environment or "testing"
        connection = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
            company, "wscdc", environment,
        )
        auth = connection.get_auth()

        cmp_req = self._l10n_ar_get_wscdc_payload()

        tr = ws_transport.CapturingTransport(
            session=ws_transport.build_afip_session(), timeout=60,
        )
        try:
            response = ws_wscdc.comprobante_constatar(
                auth=auth, cmp_req=cmp_req,
                environment=environment, transport=tr,
            )
        finally:
            self.l10n_ar_afip_verification_xml_request = _decode_safe(tr.last_request)
            self.l10n_ar_afip_verification_xml_response = _decode_safe(tr.last_response)

        self._l10n_ar_apply_wscdc_response(response)

    def _l10n_ar_apply_wscdc_response(self, response):
        """Vuelca el dict devuelto por `wscdc.comprobante_constatar` al move."""
        self.ensure_one()
        resultado = response.get("resultado") or ""
        observaciones = response.get("observaciones") or []

        obs_text = ""
        if observaciones:
            obs_text = "\n".join(
                "[%s] %s" % (o.get("code", "?"), o.get("msg", ""))
                for o in observaciones
            )

        self.write({
            "l10n_ar_afip_verification_result": resultado,
            "l10n_ar_afip_verification_date": fields.Datetime.now(),
            "l10n_ar_afip_verification_observations": obs_text or False,
        })

        # Posteamos al chatter para que quede en el historial — útil para
        # auditoría y para que el operador vea el resultado sin abrir la
        # tab AFIP.
        result_label = dict(self._fields["l10n_ar_afip_verification_result"].selection).get(
            resultado, resultado or "(sin resultado)"
        )
        body = _("Constatación ARCA: <b>%s</b>") % result_label
        if obs_text:
            body += "<br/><pre>%s</pre>" % obs_text
        self.message_post(body=body)

    # ------------------------------------------------------------------
    # Override _post — bloqueo cuando está "Requerido"
    # ------------------------------------------------------------------
    def _post(self, soft=True):
        """Antes de postear facturas IN, si la company está en 'requerido' las constata.

        Si el resultado no es A (Aprobado) ni O (Observado), aborta el post
        para que el operador revise la factura antes de tomarla en su libro
        de IVA compras.
        """
        # Filtramos las que necesitan validación previa.
        # OJO: `filtered(bound_method)` llama bound_method(rec), pero como
        # self._l10n_ar_needs_pre_post_verification ya está bindeado a self,
        # Python lo pasa como (self, rec) → TypeError. Usar lambda explícita.
        to_check = self.filtered(lambda m: m._l10n_ar_needs_pre_post_verification())
        for move in to_check:
            try:
                move._l10n_ar_verify_on_arca()
            except (UserError, ws_errors.AfipWsError) as e:
                # Si la constatación falla (red, timeout, error AFIP), NO
                # bloqueamos el post — eso sería romper el flujo del cliente
                # cada vez que ARCA está caído. Solo dejamos un warning en
                # el log y en el chatter de la factura.
                _logger.warning(
                    "WSCDC pre-post falló para %s: %s. Permito el post pero el "
                    "operador debería reintentar manualmente.",
                    move.display_name, e,
                )
                move.message_post(body=_(
                    "WSCDC: no pude constatar la factura antes de postear "
                    "(%(err)s). Reintentá manualmente desde 'Constatar en ARCA'."
                ) % {"err": e})

        # Después de intentar constatar, si quedó alguna en R, bloqueamos.
        rejected = to_check.filtered(
            lambda m: m.l10n_ar_afip_verification_result == "R"
        )
        if rejected:
            raise UserError(_(
                "ARCA rechazó la constatación de las siguientes facturas — "
                "no se pueden postear:\n%s\n\nVerificá manualmente en el "
                "portal de ARCA y revisá el chatter de cada factura."
            ) % "\n".join(" • " + m.display_name for m in rejected))

        return super()._post(soft=soft)

    def _l10n_ar_needs_pre_post_verification(self):
        """True si esta factura tiene que ser constatada en _post."""
        self.ensure_one()
        return bool(
            self.country_code == "AR"
            and self.move_type in ("in_invoice", "in_refund")
            and self.l10n_ar_afip_auth_code
            and self.l10n_ar_afip_auth_mode in ("CAE", "CAEA", "CAI")
            and self.company_id.l10n_ar_supplier_validation_type == "requerido"
            and self.l10n_ar_afip_verification_result not in ("A", "O")
            and int(self.l10n_latam_document_type_id.code or 0) in WSCDC_CBTE_TIPOS
        )


def _decode_safe(maybe_bytes):
    if maybe_bytes is None:
        return False
    if isinstance(maybe_bytes, bytes):
        try:
            return maybe_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return maybe_bytes.decode("latin-1", errors="replace")
    return str(maybe_bytes)
