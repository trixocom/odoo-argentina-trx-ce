# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Campos AFIP/ARCA sobre `res.company`.

El diseño aquí busca balance entre:
- Multi-empresa: cada compañía tiene su propio certificado y entorno.
- Simplicidad: un solo cert + environment por compañía; si en el futuro
  una compañía necesita certs distintos por servicio, se agrega un modelo
  relación en vez de reescribir esto.

Los campos que definimos acá son los que `l10n_ar_afip_ws` necesita para
saber qué cert usar y contra qué entorno hablar. La pantalla de edición
(vista XML) vive en este módulo, para que el usuario los pueda cargar
incluso antes de instalar los módulos de emisión.

Sucursales (`parent_id`, Odoo 17+)
----------------------------------
Una compañía con `parent_id` es un local más de la MISMA razón social que
su raíz (`root_id`): mismo CUIT, misma condición frente al IVA, mismo
certificado y mismo entorno AFIP. Odoo ya comparte plan de cuentas e
impuestos con la raíz; acá extendemos ese mecanismo nativo
(`_get_company_root_delegated_field_names`) a la identidad fiscal y a la
configuración AFIP, así:

* al crear una sucursal se copian de la raíz (y quedan readonly en el form);
* al modificar la raíz se propagan a todas sus sucursales;
* una sucursal no puede tener valores distintos a su raíz (constraint de base).

Con eso, todo el código que lee `company_id.partner_id.vat`,
`company_id.l10n_ar_afip_ws_cert_id`, etc. sobre la sucursal obtiene lo
mismo que sobre la raíz, sin tocar cada módulo. La conexión WSAA (token de
acceso) se comparte a nivel raíz: ver `l10n_ar_afip_ws`.
"""
from odoo import api, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_ar_afip_ws_environment = fields.Selection(
        selection=[
            ("testing", "Homologación (AFIP Testing)"),
            ("production", "Producción"),
        ],
        string="Entorno AFIP",
        default="testing",
        help=(
            "Determina contra qué servidores de AFIP emite esta empresa. "
            "Homologación para probar; Producción para facturar en serio. "
            "El cambio a Producción requiere un certificado distinto. "
            "En una sucursal se hereda de la compañía raíz (mismo CUIT)."
        ),
    )
    l10n_ar_afip_ws_cert_id = fields.Many2one(
        "certificate.certificate",
        string="Certificado AFIP",
        domain="[('company_id', 'in', (False, id))]",
        help=(
            "Certificado X.509 emitido por AFIP (homologación o producción, "
            "según el entorno). Subilo al módulo de Certificados antes de "
            "seleccionarlo acá. En una sucursal se hereda de la compañía raíz."
        ),
    )
    # CUIT ya lo provee `l10n_ar` vía `partner_id.vat` — no duplicamos.

    # Configuración AFIP almacenada en res.company que una sucursal comparte
    # con su raíz. Van por el mecanismo nativo de campos delegados (copia al
    # crear, propagación al escribir la raíz, constraint de igualdad, readonly
    # en el form de la sucursal).
    L10N_AR_ROOT_DELEGATED_FIELDS = [
        "l10n_ar_afip_start_date",
        "l10n_ar_afip_ws_environment",
        "l10n_ar_afip_ws_cert_id",
    ]
    # Identidad fiscal que vive en el PARTNER de la compañía (related en
    # res.company, definidos por base/l10n_ar). No pueden ir en la lista de
    # delegados: el constraint de base corre en `_create` antes de que se
    # escriban los related, y fallaría. Se sincronizan a mano al partner de
    # la sucursal (create) y se propagan desde la raíz (write).
    L10N_AR_ROOT_PARTNER_FIELDS = [
        "vat",
        "l10n_ar_afip_responsibility_type_id",
        "l10n_ar_gross_income_number",
        "l10n_ar_gross_income_type",
    ]

    def _get_company_root_delegated_field_names(self):
        return super()._get_company_root_delegated_field_names() + [
            fname for fname in self.L10N_AR_ROOT_DELEGATED_FIELDS if fname in self._fields
        ]

    def _l10n_ar_root_partner_vals(self, root):
        """Valores de identidad fiscal del partner de `root` para copiar al
        partner de una sucursal."""
        rp = root.partner_id
        vals = {}
        if rp.l10n_latam_identification_type_id:
            vals["l10n_latam_identification_type_id"] = rp.l10n_latam_identification_type_id.id
        if rp.vat:
            vals["vat"] = rp.vat
        for fname in ("l10n_ar_afip_responsibility_type_id", "l10n_ar_gross_income_type"):
            if fname in rp._fields:
                val = rp[fname]
                vals[fname] = val.id if hasattr(val, "id") else val
        if "l10n_ar_gross_income_number" in rp._fields:
            vals["l10n_ar_gross_income_number"] = rp.l10n_ar_gross_income_number
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        # base crea el partner de la compañía ANTES de copiar los campos
        # delegados: inyectamos CUIT y país en los vals para que el partner
        # de la sucursal nazca ya con el CUIT de la raíz (tipo de
        # identificación CUIT por default en l10n_ar).
        for vals in vals_list:
            parent_id = vals.get("parent_id")
            if not parent_id or vals.get("partner_id"):
                continue
            parent = self.browse(parent_id)
            if parent.vat and not vals.get("vat"):
                vals["vat"] = parent.vat
            if parent.country_id and not vals.get("country_id"):
                vals["country_id"] = parent.country_id.id
        companies = super().create(vals_list)
        # Identidad fiscal completa (tipo de identificación, condición IVA,
        # IIBB) al partner de cada sucursal nueva.
        for company in companies.filtered("parent_id"):
            company.partner_id.sudo().write(self._l10n_ar_root_partner_vals(company.root_id))
        return companies

    def write(self, vals):
        res = super().write(vals)
        changed = set(vals) & set(self.L10N_AR_ROOT_PARTNER_FIELDS)
        if changed:
            # Propagar la identidad fiscal de la raíz a los partners de sus
            # sucursales (misma razón social).
            for company in self.filtered(lambda c: not c.parent_id and c.child_ids):
                branches = self.sudo().search([
                    ("id", "child_of", company.id),
                    ("id", "!=", company.id),
                ])
                pvals = self._l10n_ar_root_partner_vals(company)
                branches.partner_id.sudo().write({k: v for k, v in pvals.items() if k in vals or k == "l10n_latam_identification_type_id"})
        return res

    def _l10n_ar_afip_company(self):
        """Compañía que 'habla' con AFIP por esta compañía: la raíz.

        Una sucursal comparte CUIT, certificado y entorno con su raíz, y el
        ticket de acceso WSAA es único por (CUIT, servicio): pedir uno por
        sucursal haría que AFIP rechace el segundo ("ya posee un TA válido").
        """
        self.ensure_one()
        return self.root_id if self.parent_id else self
