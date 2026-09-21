# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI — Cliente Web Services AFIP/ARCA",
    "version": "19.0.0.8.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "Cliente Python puro para WSAA, WSFEv1 y futuros WSFEX/WSBFE/WSCDC",
    "description": """
Cliente Python puro para los web services de AFIP/ARCA:

* WSAA — autenticación (Login Ticket Request con firma CMS).
* WSFEv1 — Factura Electrónica mercado interno (FECAESolicitar,
  FECompUltimoAutorizado, FEParamGet*, FEDummy).
* WSFEX — Factura de Exportación (a implementar Fase 2).
* WSBFE — Bono Fiscal Electrónico (a implementar Fase 2+).
* WSCDC — Constatación de Comprobantes (a implementar Fase 2).

La lógica de transporte SOAP y parseo XML vive en el paquete Python `lib/`
y es testeable sin Odoo. El modelo `l10n_ar.ws.connection` cachea los
tokens WSAA (TTL ~12 h) en base de datos por (company, ws, environment).

Depende de la librería `zeep` (package Debian `python3-zeep` o pip `zeep`).
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "l10n_ar_trx_edi_base",
    ],
    "external_dependencies": {
        "python": ["zeep"],
    },
    "data": [
        "security/ir.model.access.csv",
        "views/ws_dashboard_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
