# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI (Trixocom) — Punto de Venta + Factura Electrónica",
    "version": "19.0.0.2.0",
    "category": "Accounting/Localizations/Point of Sale",
    "summary": "Emite FA-A/B/C electrónica desde POS con QR RG 4291 y CAE en el ticket",
    "description": """
Conecta el Punto de Venta de Odoo Community 19 con la facturación
electrónica argentina ya implementada en `l10n_ar_trx_edi`. Cuando una venta
de POS se factura como FA-A/B/C electrónica:

* La `account.move` generada pasa por el ``_post()`` de `l10n_ar_trx_edi`
  que emite el CAE contra WSFEv1 automáticamente (reusa todo el motor).
* El **ticket de POS** muestra el **QR de RG 4291** + el **CAE** y su
  vencimiento, encima del bloque "Need an invoice?" estándar.
* En refunds desde POS (devolución de un pedido facturado), el ``move_type``
  ``out_refund`` se vincula con la factura original via ``reversed_entry_id``
  para que el WSFEv1 reciba ``CbtesAsoc`` (asociar la NC con la FA-X
  original).

Inspirado (no copiado) en `ingadhoc/odoo-argentina-ce/l10n_ar_pos_afipws_fe`
(AGPL-3, 25 LOC). Acá agregamos:

* Exposición de los campos l10n_ar_afip_* al frontend OWL del POS.
* Override del QWeb `point_of_sale.OrderReceipt` para inyectar QR+CAE.
* Helper de URL para el QR usando el endpoint `/report/barcode/` de
  Odoo core (no requiere lib `qrcode` extra en el container).

Spec QR: https://www.afip.gob.ar/fe/qr/documentos/QR-Especificacionesv1.pdf
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "point_of_sale",
        "l10n_ar_trx_edi",
    ],
    "data": [],
    "assets": {
        "point_of_sale._assets_pos": [
            "l10n_ar_trx_pos_edi/static/src/**/*",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
