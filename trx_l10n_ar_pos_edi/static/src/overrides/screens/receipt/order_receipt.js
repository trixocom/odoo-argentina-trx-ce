/** @odoo-module **/
// Patch del componente OrderReceipt para mostrar QR + CAE de RG 4291.
//
// El approach correcto en Odoo 19 es traer los datos AR via RPC on-demand
// cuando se va a renderear el ticket — overridear `_load_pos_data_fields`
// para pos.order rompe la carga del order (limita la lista de fields).
//
// Flujo:
//   1. Componente se monta → setup hook dispara fetch async.
//   2. RPC `pos.order.get_l10n_ar_invoice_data(order.id)` trae los campos
//      AFIP de la factura asociada (o null si no hay factura).
//   3. Datos van a un `state` reactivo que el template lee.

import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { generateQRCodeDataUrl } from "@point_of_sale/utils";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, useState } from "@odoo/owl";

patch(OrderReceipt.prototype, {
    setup() {
        super.setup(...arguments);
        this.arState = useState({
            invoice: null,    // dict con datos AR o null
            loading: true,
        });
        this.orm = useService("orm");
        onWillStart(async () => {
            await this._loadArInvoice();
        });
    },

    async _loadArInvoice() {
        try {
            const orderId = this.order?.id;
            if (!orderId) {
                this.arState.loading = false;
                return;
            }
            const data = await this.orm.call(
                "pos.order",
                "get_l10n_ar_invoice_data",
                [orderId],
            );
            this.arState.invoice = data || null;
        } catch (err) {
            // Silencioso — si el RPC falla, simplemente no se muestra el
            // bloque AR. No queremos romper el receipt por eso.
        } finally {
            this.arState.loading = false;
        }
    },

    get arInvoice() {
        return this.arState.invoice;
    },

    get arHasCae() {
        const inv = this.arInvoice;
        return !!(inv && inv.l10n_ar_afip_auth_code);
    },

    get arQrDataUrl() {
        const inv = this.arInvoice;
        if (!inv?.l10n_ar_afip_qr_code) return null;
        return generateQRCodeDataUrl(inv.l10n_ar_afip_qr_code, {
            width: 140,
            height: 140,
        });
    },
});
