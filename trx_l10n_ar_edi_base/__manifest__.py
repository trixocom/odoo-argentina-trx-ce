# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI (Trixocom) — Base",
    "version": "19.0.0.5.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Campos y modelos base para facturación electrónica AR en Odoo Community",
    "description": """
Módulo base del paquete l10n-ar-edi-community.

Extiende los modelos core (res.company, account.journal, account.move,
res.currency, product.template) con los campos necesarios para soportar
facturación electrónica argentina. No implementa todavía la lógica de
emisión ni el cliente de web service — esos viven en l10n_ar_afip_ws y
l10n_ar_trx_edi.

Este módulo se puede instalar por sí solo para preparar la base de datos,
pero no aporta funcionalidad visible al usuario hasta que se instala
l10n_ar_trx_edi encima.
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "l10n_ar",
        "certificate",
    ],
    "data": [
        # Jerga contable AR (Debe/Haber/Saldo) sobre los idiomas AR activos — ver i18n/
        "data/l10n_ar_accounting_terms.xml",
        # "security/ir.model.access.csv",
        # "views/res_company_view.xml",
        # "views/account_journal_view.xml",
        # "views/account_move_view.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
