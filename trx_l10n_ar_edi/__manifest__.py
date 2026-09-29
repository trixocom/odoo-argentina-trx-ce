# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI (Trixocom) — Emisión Electrónica",
    "version": "19.0.0.8.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Emisión de comprobantes electrónicos argentinos (WSFEv1, A/B/C)",
    "description": """
Implementa la emisión de facturas, notas de crédito y notas de débito
electrónicas contra el WSFEv1 de AFIP/ARCA en Odoo 19 Community.

Funcionalidades principales:

* Botón "Enviar a ARCA" / override de `_post` en account.move para solicitar
  CAE al validar el comprobante.
* Parseo de la respuesta: CAE, vencimiento, observaciones (resultado "O") y
  errores (resultado "R"), con interpretación humanizada.
* Generación del código QR AFIP según RG 4291 incluido en el PDF de factura.
* Wizard "Consultar en ARCA" para consultar un comprobante puntual o el
  último emitido (útil para desincronización de secuencias).
* Controller `/l10n_ar_trx_edi/download_csr/<company_id>` para generar CSR que
  después se sube a WSASS (homologación) o al portal AFIP (producción).
* Reportes QWeb con leyenda legal según condición IVA del emisor.

Condiciones IVA soportadas: Responsable Inscripto (A/B/C), Monotributo (C),
Exento/No alcanzado (C).
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "l10n_ar_trx_edi_base",
        "l10n_ar_afip_ws",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "wizards/l10n_ar_csr_wizard_view.xml",
        "views/account_move_view.xml",
        "views/account_journal_view.xml",
        "wizards/afip_seq_sync_wizard_view.xml",
        "views/report_invoice_document.xml",
        "views/res_config_settings_view.xml",
        "views/res_currency_views.xml",
        # "wizards/afip_ws_consult_view.xml",  # pendiente: FECompConsultar por UI
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
