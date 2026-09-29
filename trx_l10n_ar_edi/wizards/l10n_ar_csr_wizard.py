# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Wizard "Generar solicitud de renovación" — arma la CSR para AFIP.

Flujo del operador:

  1. Settings → Localización para Argentina → "Generar solicitud de renovación".
  2. El wizard se abre prefilled con el CN sugerido + razón social + CUIT
     de la empresa actual.
  3. El operador hace click en "Generar". El wizard:
       - genera una RSA-2048 + CSR usando `certificate.certificate.
         _l10n_ar_create_csr()`,
       - guarda la clave privada en Odoo (registro `certificate.key`)
         para que el operador no tenga que reimportarla cuando suba el
         .crt firmado por AFIP,
       - ofrece descarga del archivo `.csr` (lo que AFIP pide subir),
       - ofrece descarga *opcional* de la `.key` como backup externo
         (no es necesaria — Odoo ya la tiene).
  4. El operador descarga el `.csr`, va al portal AFIP "Administración
     de Certificados Digitales", "Agregar alias" + "Generar certificado",
     sube el archivo, y descarga el cert emitido (.crt).
  5. Carga el `.crt` en Configuración → Certificados como `certificate.
     certificate` y vincula la `certificate.key` ya creada por este
     wizard (figura con el mismo nombre que el CN).

AFIP no acepta pegar PEM en un textarea — pide subir el archivo `.csr`.
Por eso el wizard expone CSR como Binary descargable, no como Text.
"""
import base64
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class L10nArCsrWizard(models.TransientModel):
    _name = "l10n_ar.csr.wizard"
    _description = "Wizard solicitud de certificado AFIP (CSR)"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )
    common_name = fields.Char(
        string="Common Name (CN)",
        required=True,
        help=(
            "Alias del certificado tal como va a aparecer en el portal AFIP. "
            "Convención: '<aplicacion>-<entorno>', ej. 'odoo-edi-prod' o "
            "'odoo-edi-homo'. Sin espacios ni caracteres raros."
        ),
    )
    organization = fields.Char(
        string="Organización (razón social)",
        required=True,
    )
    cuit = fields.Char(
        string="CUIT (11 dígitos sin guiones)",
        required=True,
        size=11,
    )
    country = fields.Char(default="AR", required=True, size=2)
    state = fields.Selection(
        selection=[
            ("input", "Datos"),
            ("done", "Listo"),
        ],
        default="input",
    )

    # Archivos descargables generados al apretar "Generar".
    csr_filename = fields.Char(readonly=True)
    csr_data = fields.Binary(
        string="Archivo CSR (subir a AFIP)",
        readonly=True,
        attachment=False,
        help=(
            "Descargá este archivo .csr y subilo al portal AFIP en "
            "'Administración de Certificados Digitales' → "
            "'Agregar alias' → 'Generar certificado de prueba/producción'."
        ),
    )
    private_key_filename = fields.Char(readonly=True)
    private_key_data = fields.Binary(
        string="Clave privada (backup opcional)",
        readonly=True,
        attachment=False,
        help=(
            "Odoo ya guardó la clave privada internamente — vas a poder "
            "asociarla al certificado .crt cuando AFIP te lo firme. "
            "La descarga acá es solo un backup externo opcional."
        ),
    )
    key_record_id = fields.Many2one(
        "certificate.key",
        string="Clave guardada en Odoo",
        readonly=True,
        help="Registro `certificate.key` que el wizard creó para asociar al .crt.",
    )

    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        company = self.env["res.company"].browse(res.get("company_id") or self.env.company.id)
        env_label = "prod" if company.l10n_ar_afip_ws_environment == "production" else "homo"
        res.setdefault("common_name", "odoo-edi-%s" % env_label)
        res.setdefault("organization", company.name)
        cuit = (company.partner_id.vat or "").replace("-", "").replace(" ", "")
        res.setdefault("cuit", cuit)
        res.setdefault("country", company.partner_id.country_id.code or "AR")
        return res

    def action_generate(self):
        """Genera CSR + key. Persiste la key en `certificate.key` y deja
        ambos archivos disponibles para descarga."""
        self.ensure_one()
        if not (self.cuit.isdigit() and len(self.cuit) == 11):
            raise UserError(_("El CUIT tiene que ser 11 dígitos sin guiones."))
        cn = (self.common_name or "").strip()
        if not cn:
            raise UserError(_("El Common Name no puede estar vacío."))

        result = self.env["certificate.certificate"]._l10n_ar_create_csr(
            common_name=cn,
            organization=(self.organization or "").strip(),
            cuit=self.cuit,
            country=(self.country or "AR").upper(),
        )
        csr_pem = result["csr_pem"]
        key_pem = result["private_key_pem"]

        # Persistir la private key en certificate.key para que el operador
        # no tenga que reimportarla al cargar el .crt firmado por AFIP.
        # `certificate.key` se identifica por `name` único — usamos el CN.
        Key = self.env["certificate.key"]
        existing = Key.search([("name", "=", cn), ("company_id", "=", self.company_id.id)], limit=1)
        if existing:
            # No pisamos una key existente — sería destruir el binding del
            # cert vigente. El operador puede borrarla manualmente si quiere
            # regenerar.
            raise UserError(_(
                "Ya existe una clave privada con nombre %r en la empresa %s. "
                "Borrala antes de regenerar (Configuración → Certificados → "
                "Claves), o usá un Common Name distinto."
            ) % (cn, self.company_id.name))

        key_rec = Key.create({
            "name": cn,
            "company_id": self.company_id.id,
            "content": base64.b64encode(key_pem),
        })

        self.write({
            "state": "done",
            "csr_data": base64.b64encode(csr_pem),
            "csr_filename": "%s.csr" % cn,
            "private_key_data": base64.b64encode(key_pem),
            "private_key_filename": "%s.key" % cn,
            "key_record_id": key_rec.id,
        })
        _logger.info(
            "CSR AFIP generada para company=%s CN=%s key_id=%s",
            self.company_id.name, cn, key_rec.id,
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n_ar.csr.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
