# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Campos AFIP sobre `account.move`.

Acá solo *declaramos* los campos; la lógica de emisión (llamar al WS,
guardar CAE, recalcular QR) vive en `l10n_ar_trx_edi` — así este módulo
puede estar instalado para cargar datos maestros sin abrir la
posibilidad de emitir.

Campos:

- `l10n_ar_afip_auth_mode`: cómo se autorizó el comprobante (CAE/CAEA).
- `l10n_ar_afip_auth_code`: el CAE/CAEA devuelto por AFIP.
- `l10n_ar_afip_auth_code_due`: vencimiento del CAE (AFIP lo da).
- `l10n_ar_afip_result`: A=Aprobado, R=Rechazado, O=Observado, None=no enviado.
- `l10n_ar_afip_xml_request/response`: XML capturado para auditoría.
- `l10n_ar_afip_qr_code`: URL de verificación QR según RG 4291.

Nombramos todos con prefijo `l10n_ar_afip_` para que sea obvio a qué
pertenecen y para que no colisionen con los de enterprise cuando el
usuario migre de enterprise→community (aunque por diseño no interoperan).
"""
from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_ar_afip_auth_mode = fields.Selection(
        selection=[
            ("CAE", "CAE"),
            ("CAEA", "CAEA"),
            ("CAI", "CAI (preimpreso)"),
        ],
        string="Modo autorización AFIP",
        copy=False,
        readonly=True,
    )
    l10n_ar_afip_auth_code = fields.Char(
        string="Código autorización AFIP",
        copy=False,
        readonly=True,
        size=24,
    )
    l10n_ar_afip_auth_code_due = fields.Date(
        string="Vto. código AFIP",
        copy=False,
        readonly=True,
    )
    l10n_ar_afip_result = fields.Selection(
        selection=[
            ("A", "Aprobado"),
            ("R", "Rechazado"),
            ("O", "Observado"),
        ],
        string="Resultado AFIP",
        copy=False,
        readonly=True,
    )
    l10n_ar_afip_xml_request = fields.Text(
        string="XML enviado a AFIP",
        copy=False,
        readonly=True,
        groups="account.group_account_manager",
    )
    l10n_ar_afip_xml_response = fields.Text(
        string="XML recibido de AFIP",
        copy=False,
        readonly=True,
        groups="account.group_account_manager",
    )
    l10n_ar_afip_qr_code = fields.Char(
        string="URL QR AFIP",
        compute="_compute_l10n_ar_afip_qr_code",
        store=False,
    )
    l10n_ar_afip_observations = fields.Text(
        string="Observaciones AFIP",
        copy=False,
        readonly=True,
        help="Mensajes no-bloqueantes que devolvió AFIP al autorizar el comprobante.",
    )

    def _compute_l10n_ar_afip_qr_code(self):
        """Stub: `l10n_ar_trx_edi` lo override con la lógica real RG 4291.

        Lo dejamos como compute en base para que las vistas ya puedan
        referenciar el campo aunque `l10n_ar_trx_edi` no esté instalado.
        """
        for move in self:
            move.l10n_ar_afip_qr_code = False
