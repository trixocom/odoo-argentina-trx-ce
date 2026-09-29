/** @odoo-module **/
// Suprimir descarga automática del PDF de la factura post-pago en POS
// para companies argentinas. El PDF "Factura A 01001-..." que abre Odoo
// en una pestaña nueva no aporta — el ticket POS ya tiene el QR + CAE
// (l10n_ar_trx_pos_edi/static/src/overrides/screens/receipt/order_receipt.xml)
// y eso cubre los requisitos de RG 4291.
//
// Si la company NO es AR, mantenemos el comportamiento default (descarga
// el PDF). Eso lo hace inocuo para multi-company donde haya otras
// localizaciones.

import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { patch } from "@web/core/utils/patch";

patch(OrderPaymentValidation.prototype, {
    shouldDownloadInvoice() {
        const companyCode =
            this.pos?.config?.company_id?.country_id?.code ||
            this.order?.company?.country_id?.code;
        if (companyCode === "AR") {
            return false;
        }
        return super.shouldDownloadInvoice();
    },
});
