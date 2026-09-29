# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI — Percepciones y Retenciones IIBB",
    "version": "19.0.1.1.3",
    "category": "Accounting/Localizations",
    "summary": "Percepciones y retenciones IIBB provinciales: ARBA, AGIP, Santa Fe, Córdoba",
    "description": """
Soporta padrones de alícuotas IIBB provinciales y aplica percepciones y
retenciones automáticamente en facturas según el domicilio fiscal del
receptor.

Jurisdicciones implementadas en Fase 3:

* ARBA (Buenos Aires provincia)
* AGIP (Ciudad de Buenos Aires)
* API (Santa Fe)
* DGR (Córdoba)

Modelo `l10n_ar.padron.line` con campos CUIT, provincia, alícuota, fecha
desde / hasta. Wizard de actualización por import de CSV oficial de cada
provincia.

Exportación para SIAp/SICORE/SIRE en formato TXT.
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "trx_l10n_ar_edi",
    ],
    "external_dependencies": {
        "python": ["requests"],
    },
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "views/padron_arba_views.xml",
        "views/arba_ws_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
