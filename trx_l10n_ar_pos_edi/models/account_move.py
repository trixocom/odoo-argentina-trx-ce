# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Override de `account.move._load_pos_data_fields` para que el frontend
del POS reciba los datos AFIP/ARCA de la factura: CAE, vto, QR url,
nro de comprobante con formato l10n_latam.

Sin este override el cliente OWL del POS recibe ``['id', 'name']`` y
no tiene de dónde construir el QR ni mostrar el CAE.

Spec QR: RG 4291 (https://www.afip.gob.ar/fe/qr/).
"""
from odoo import api, models


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _load_pos_data_fields(self, config):
        """Agrega los campos AFIP/ARCA al payload que va al frontend POS."""
        result = super()._load_pos_data_fields(config)
        ar_fields = [
            "l10n_ar_afip_auth_mode",         # CAE / CAI / CAEA
            "l10n_ar_afip_auth_code",         # número CAE
            "l10n_ar_afip_auth_code_due",     # vto del CAE (date)
            "l10n_ar_afip_qr_code",           # URL completa QR RG 4291
            "l10n_ar_afip_result",            # 'A' aprobado / 'O' obs / 'R' rechazado
            "l10n_latam_document_number",     # "00006-00000123"
            "l10n_latam_document_type_id",    # FA-A / FA-B / NC-A / etc
        ]
        # Mantener uniqueness por si algún otro override ya los agregó.
        for f in ar_fields:
            if f not in result:
                result.append(f)
        return result
