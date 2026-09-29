/** @odoo-module **/
import { ReceiptHeader } from "@point_of_sale/app/screens/receipt_screen/receipt/receipt_header/receipt_header";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onWillStart, useState } from "@odoo/owl";

// En el encabezado del ticket, cuando la venta esta facturada
// electronicamente (CAE), mostrar el nombre del comprobante (ej.
// "FA-A 00002-00000281") en lugar de "Ticket <ref>". Trae el dato por el
// mismo RPC que usa OrderReceipt.
patch(ReceiptHeader.prototype, {
    setup() {
        super.setup(...arguments);
        this.arHdr = useState({ name: null });
        this.orm = useService("orm");
        onWillStart(async () => {
            try {
                const oid = this.props.order?.id;
                if (!oid) {
                    return;
                }
                const data = await this.orm.call(
                    "pos.order", "get_l10n_ar_invoice_data", [oid]
                );
                if (data && data.l10n_ar_afip_auth_code && data.name) {
                    this.arHdr.name = data.name;
                }
            } catch (e) {
                // silencioso: si falla, queda el "Ticket <ref>" estandar
            }
        });
    },
});
