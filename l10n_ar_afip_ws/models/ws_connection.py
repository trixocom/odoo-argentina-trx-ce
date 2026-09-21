# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Modelo de conexión a AFIP WS — token/sign cacheados por (empresa, ws, env).

¿Por qué un modelo de cache en vez de un `ir.attachment` o un diccionario en
memoria? Tres razones:

1. **Multi-worker**: Odoo en producción corre con varios workers; un dict en
   memoria se duplica por worker y no sirve.
2. **Persistencia entre restarts**: el TA dura 12h, sería tonto tirarlo al
   reiniciar el server.
3. **Auditoría**: guardar fecha de generación y expiración permite responder
   preguntas como "¿qué TA usé cuando emití la factura 123?".

El modelo guarda **un registro por** (company_id, ws, environment). El flujo
es: `get_auth()` → si hay TA vigente, devolverlo; si no, generar uno nuevo,
guardarlo y devolverlo.
"""
import logging
from datetime import datetime, timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config as _odoo_config

from ..lib import transport, urls, wsaa

_logger = logging.getLogger(__name__)

#: margen de seguridad: si le quedan menos minutos al TA, lo renuevo.
TA_RENEWAL_MARGIN_MINUTES = 10
#: reintentos de la renovacion aislada del TA ante colision de serializacion.
TA_RENEWAL_MAX_RETRIES = 3


class L10nArAfipWsConnection(models.Model):
    _name = "l10n_ar.afip.ws.connection"
    _description = "Token cacheado de WSAA por empresa / WS / entorno"
    _rec_name = "display_name"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        ondelete="cascade",
        index=True,
    )
    ws = fields.Selection(
        [
            ("wsfe", "WSFEv1 (Factura Electrónica)"),
            ("wsfex", "WSFEX (Exportación)"),
            ("wsbfe", "WSBFE (Bono Fiscal Electrónico)"),
            ("wscdc", "WSCDC (Constatación de Comprobantes)"),
            ("wsmtxca", "WSMTXCA (con detalle de items)"),
            ("ws_sr_padron_a13", "Padrón AFIP A13 (mínimo)"),
            ("ws_sr_constancia_inscripcion", "Padrón AFIP A5 (Constancia de Inscripción completa)"),
        ],
        required=True,
        string="Servicio",
    )
    environment = fields.Selection(
        [
            ("testing", "Homologación"),
            ("production", "Producción"),
        ],
        required=True,
    )
    token = fields.Text(readonly=True)
    sign = fields.Text(readonly=True)
    generation_time = fields.Datetime(readonly=True)
    expiration_time = fields.Datetime(readonly=True)
    last_login_xml_request = fields.Text(readonly=True, string="Último XML enviado")
    last_login_xml_response = fields.Text(readonly=True, string="Último XML recibido")
    display_name = fields.Char(compute="_compute_display_name", store=True)

    _sql_constraints = [
        (
            "uniq_company_ws_env",
            "unique(company_id, ws, environment)",
            "Ya existe una conexión para esa combinación empresa/WS/entorno.",
        ),
    ]

    @api.depends("company_id", "ws", "environment")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "%s · %s · %s" % (
                rec.company_id.name or "",
                rec.ws or "",
                rec.environment or "",
            )

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    @api.model
    def _get_or_create(self, company, ws, environment):
        """Devuelve (o crea) el registro de conexión para esa combinación.

        Sucursales (`parent_id`): la conexión se resuelve SIEMPRE sobre la
        compañía raíz. Una sucursal comparte CUIT y certificado con su raíz
        (ver `l10n_ar_trx_edi_base`) y el TA de WSAA es único por (CUIT,
        servicio): si cada sucursal pidiera el suyo, AFIP rechazaría el
        segundo con "El CEE ya posee un TA válido". Se usa sudo porque el
        usuario de una sucursal no necesariamente tiene la raíz entre sus
        compañías permitidas.
        """
        company = company._l10n_ar_afip_company()
        rec = self.sudo().search([
            ("company_id", "=", company.id),
            ("ws", "=", ws),
            ("environment", "=", environment),
        ], limit=1)
        if rec:
            return rec
        return self.sudo().create({
            "company_id": company.id,
            "ws": ws,
            "environment": environment,
        })

    def _is_token_valid(self):
        """Dice si el token actual sirve o hay que renovarlo."""
        self.ensure_one()
        if not (self.token and self.sign and self.expiration_time):
            return False
        margin = timedelta(minutes=TA_RENEWAL_MARGIN_MINUTES)
        return datetime.utcnow() + margin < self.expiration_time

    def get_auth(self):
        """Devuelve dict {cuit, token, sign} listo para mandar a los WS.

        Renueva automaticamente si el TA expiro (o esta por expirar).

        La renovacion NO corre en la transaccion del llamador: ver
        `_renew_token_isolated`.
        """
        self.ensure_one()
        if self._is_token_valid():
            return {
                "cuit": self._get_cuit(),
                "token": self.token,
                "sign": self.sign,
            }
        return self._renew_token_isolated()

    def _renew_token_isolated(self):
        """Renueva el TA en una transaccion propia y devuelve el auth armado.

        POR QUE: `_renew_token()` hace `self.write(...)` sobre la fila de esta
        conexion. Si eso corre dentro de la transaccion que esta pidiendo un CAE,
        esa fila --unica por (empresa, ws, entorno)-- se vuelve un punto de
        contencion global entre todos los workers. Cuando dos transacciones la
        tocan a la vez, Postgres (REPEATABLE READ) tira `could not serialize
        access due to concurrent update` al commitear y Odoo hace rollback...
        pero AFIP no: el numero ya se consumio y queda un **CAE huerfano** que
        traba toda la numeracion. (Caso real 14/08/2026 en un POS multi-caja:
        AFIP otorgo el CAE y el commit exploto justo despues, con el UPDATE de
        esta misma tabla en el flush.)

        COMO:
        - cursor nuevo (otra conexion) -> el write no entra en la transaccion del
          llamador y no puede hacerle fallar el commit;
        - `SELECT ... FOR UPDATE` sobre la fila -> serializa a los workers que
          llegan juntos; el que pierde recibe SerializationFailure *dentro de esta
          transaccion aislada*, reintenta con snapshot fresco y encuentra el TA
          que acaba de guardar el otro (asi no le pedimos a WSAA un TA nuevo
          teniendo uno vigente, que AFIP rechaza);
        - devolvemos el auth como dict: releer la fila desde la transaccion del
          llamador daria la version vieja del token por REPEATABLE READ.

        Fallback: si la fila todavia no es visible desde otra conexion (se creo
        en la transaccion del llamador y no se commiteo), renovamos en linea como
        antes.
        """
        self.ensure_one()
        last_error = None
        for _attempt in range(TA_RENEWAL_MAX_RETRIES):
            try:
                with self.env.registry.cursor() as new_cr:
                    new_env = api.Environment(new_cr, self.env.uid, self.env.context)
                    rec = new_env[self._name].browse(self.id)
                    new_cr.execute(
                        "SELECT id FROM %s WHERE id = %%s FOR UPDATE" % self._table,
                        (self.id,),
                    )
                    if not new_cr.fetchone():
                        # la fila todavia no esta commiteada por el llamador
                        break
                    if not rec._is_token_valid():
                        rec._renew_token()
                    auth = {
                        "cuit": rec._get_cuit(),
                        "token": rec.token,
                        "sign": rec.sign,
                    }
                    new_cr.commit()
                return auth
            except psycopg2.errors.SerializationFailure as exc:
                last_error = exc
                _logger.info(
                    "WSAA: colision renovando el TA de %s, reintento con snapshot fresco",
                    self.display_name,
                )
        if last_error is not None:
            _logger.warning(
                "WSAA: no pude renovar el TA de %s en transaccion aislada (%s); "
                "renuevo en linea",
                self.display_name, last_error,
            )
        self._renew_token()
        return {
            "cuit": self._get_cuit(),
            "token": self.token,
            "sign": self.sign,
        }

    def _get_cuit(self):
        """Extrae el CUIT de la empresa (`res.partner.vat` para AR)."""
        self.ensure_one()
        partner = self.company_id.partner_id
        cuit = (partner.vat or "").replace("-", "").strip()
        if not (cuit.isdigit() and len(cuit) == 11):
            raise UserError(_(
                "La empresa %s no tiene un CUIT válido cargado (vat=%s). "
                "Poné el CUIT de 11 dígitos en el partner de la empresa."
            ) % (self.company_id.name, partner.vat))
        return cuit

    # ------------------------------------------------------------------
    # Renovación del token
    # ------------------------------------------------------------------
    def _renew_token(self):
        """Pide un TA nuevo a WSAA y lo guarda."""
        self.ensure_one()
        cert = self._get_certificate()
        # el transport lo creamos nuevo por llamada — no reutilizamos para
        # evitar estado compartido entre empresas/WS.
        tr = transport.build_transport(
            data_dir=_odoo_config.get("data_dir"),
        )

        def _sign_cms(message_bytes):
            return cert.sudo()._l10n_ar_pkcs7_sign(message_bytes)

        # OJO: `self.ws` es la KEY del Selection (descriptiva, p.ej.
        # `ws_sr_constancia_inscripcion`), pero el nombre técnico que
        # AFIP espera en `<service>` del LoginTicketRequest puede ser
        # otro (p.ej. `ws_sr_padron_a13`). El mapping vive en `urls.py`
        # como `login_service_name`.
        login_service_name = urls.get_login_service_name(self.ws, self.environment)
        try:
            ticket = wsaa.get_ticket(
                service_name=login_service_name,
                environment=self.environment,
                sign_cms=_sign_cms,
                transport=tr,
            )
        except Exception as e:
            # Persistimos el XML aunque falle, para debugging.
            self.write({
                "last_login_xml_request": _decode_safe(tr.last_request),
                "last_login_xml_response": _decode_safe(tr.last_response),
            })
            raise

        self.write({
            "token": ticket["token"],
            "sign": ticket["sign"],
            "generation_time": ticket["generation_time"],
            "expiration_time": ticket["expiration_time"],
            "last_login_xml_request": _decode_safe(tr.last_request),
            "last_login_xml_response": _decode_safe(tr.last_response),
        })
        _logger.info(
            "WSAA: TA renovado para %s (expira %s)",
            self.display_name, self.expiration_time,
        )

    def _get_certificate(self):
        """Devuelve el `certificate.certificate` a usar para esta conexión.

        Busca el cert configurado en la compañía. Si no hay, error claro.
        La relación company → certificate la define `l10n_ar_trx_edi_base`
        (campo `l10n_ar_afip_ws_environment` + `l10n_ar_afip_ws_cert_id`).
        """
        self.ensure_one()
        # sudo(): `certificate.certificate` / `certificate.key` solo son
        # legibles por base.group_system; el usuario que factura no lo es.
        cert = self.company_id.sudo().l10n_ar_afip_ws_cert_id
        if not cert:
            raise UserError(_(
                "La empresa %s no tiene un certificado AFIP configurado. "
                "Andá a Configuración → Compañías → pestaña Argentina y "
                "cargá el certificado."
            ) % self.company_id.name)
        # sanity: si el certificate está marcado como production pero la
        # conexión es testing (o viceversa), avisamos.
        expected_env = self.company_id.l10n_ar_afip_ws_environment
        if expected_env and expected_env != self.environment:
            _logger.warning(
                "La empresa %s tiene environment=%s pero se está pidiendo TA para %s",
                self.company_id.name, expected_env, self.environment,
            )
        return cert


def _decode_safe(maybe_bytes):
    """Devuelve un str para guardar en un campo Text, sin explotar por encoding."""
    if maybe_bytes is None:
        return False
    if isinstance(maybe_bytes, bytes):
        try:
            return maybe_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return maybe_bytes.decode("latin-1", errors="replace")
    return str(maybe_bytes)
