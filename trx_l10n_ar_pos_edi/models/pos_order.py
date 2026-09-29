# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Linking de refunds POS → factura original argentina.

Cuando el cajero hace un *Refund* desde POS sobre un pedido que ya fue
facturado electrónicamente (FA-A/B/C con CAE), la NC que se genera tiene
que apuntar via ``reversed_entry_id`` a la factura original. Eso es lo
que dispara el ``CbtesAsoc`` en el payload WSFEv1 (necesario para que
AFIP acepte la NC-A/B/C contra la FA-X original).

Sin este override, la NC quedaría "huérfana" y AFIP rechazaría con
código `1023` ("Comprobante asociado obligatorio para NC clase A").

Inspirado (no copiado) en ingadhoc/odoo-argentina-ce/l10n_ar_pos_afipws_fe
(AGPL-3, 25 LOC).
"""
from odoo import _, api, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    @api.model
    def get_l10n_ar_invoice_data(self, order_id):
        """RPC para que el frontend del ticket POS traiga los datos AFIP de
        la factura asociada al pos.order on-demand.

        El approach de override `_load_pos_data_fields` rompe la carga del
        order (limita la lista de fields) — mejor traer los datos via RPC
        cuando se va a renderizar el ticket. Bajo costo (1 query, 1 record).

        :param order_id: ID (int) o UUID (str) del pos.order. Los pedidos
            que todavía no se sincronizaron llegan del frontend con su
            ``uuid`` en lugar del id numérico — si se usa ese valor en un
            ``browse`` directo, el ORM arma un IN (char por char) y explota
            con ``invalid input syntax for type integer``.
        :return: dict con los campos AR de la factura, o None.
        """
        if isinstance(order_id, int) or (
            isinstance(order_id, str) and order_id.isdigit()
        ):
            order = self.browse(int(order_id)).exists()
        else:
            order = self.search([("uuid", "=", str(order_id))], limit=1)
        if not order or not order.account_move:
            return None
        am = order.account_move
        return {
            "id": am.id,
            "name": am.name,
            "l10n_ar_afip_auth_mode": am.l10n_ar_afip_auth_mode or False,
            "l10n_ar_afip_auth_code": am.l10n_ar_afip_auth_code or False,
            "l10n_ar_afip_auth_code_due": (
                am.l10n_ar_afip_auth_code_due
                and am.l10n_ar_afip_auth_code_due.isoformat()
                or False
            ),
            "l10n_ar_afip_qr_code": am.l10n_ar_afip_qr_code or False,
            "l10n_ar_afip_result": am.l10n_ar_afip_result or False,
            "l10n_latam_document_number": am.l10n_latam_document_number or False,
            "l10n_latam_document_type_id": (
                am.l10n_latam_document_type_id
                and [
                    am.l10n_latam_document_type_id.id,
                    am.l10n_latam_document_type_id.name,
                ]
                or False
            ),
        }

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()

        # Si este pedido es un refund de otro POS order argentino con CAE,
        # vinculamos la NC con la factura original.
        if not self.refunded_order_id:
            return vals

        invoice_ids = self.refunded_order_id.mapped("account_move").filtered(
            lambda x: (
                x.company_id.country_id.code == "AR"
                and x.is_invoice()
                and x.move_type == "out_invoice"
                # Solo consideramos facturas con CAE — emitidas electrónicamente.
                and x.l10n_ar_afip_auth_code
            )
        )

        if len(invoice_ids) > 1:
            raise UserError(_(
                "Solo se puede hacer refund de una factura por vez. El POS "
                "order tiene %d facturas asociadas con CAE."
            ) % len(invoice_ids))

        if len(invoice_ids) == 1:
            vals["reversed_entry_id"] = invoice_ids[0].id
        return vals
