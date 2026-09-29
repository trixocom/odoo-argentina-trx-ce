# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI — Cotejo Mis Comprobantes",
    "version": "19.0.2.0.3",
    "category": "Accounting/Localizations/Reporting",
    "summary": "Importa Mis Comprobantes y coteja diferencias con Odoo",
    "description": """
Importa archivos XLS descargados del portal "Mis Comprobantes" de
AFIP/ARCA y los coteja contra los asientos registrados en Odoo.

Genera un reporte de diferencias:

* Comprobantes que están en AFIP pero no en Odoo (facturas perdidas o no
  contabilizadas aún).
* Comprobantes que están en Odoo pero no en AFIP (emisiones que fallaron y
  no fueron detectadas en el momento).
* Diferencias de monto entre los dos sistemas.

Útil como control mensual complementario al Libro IVA Digital.

Funcionalidades:

* Crear batch (Emitidos / Recibidos), subir el XLS del portal ARCA y
  parsearlo automáticamente. Detecta el header de columnas por keywords
  (sin importar variaciones de tildes/orden).
* Match por CAE (clave) y/o (tipo + pos + nro). Tolerancia 0.01 en
  importes.
* Estados ``ok`` / ``solo_afip`` / ``solo_odoo`` / ``amount_diff``.
* Stat buttons en el form del batch para abrir cada categoría filtrada.
* Re-cotejo on-demand después de cargar facturas faltantes.
* Reporte (list view) bajo Contabilidad → Reportes → Cotejo Mis
  Comprobantes.
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "trx_l10n_ar_edi",
        "mail",
    ],
    "external_dependencies": {
        "python": ["openpyxl"],
    },
    "data": [
        "security/ir.model.access.csv",
        "data/sequence.xml",
        "views/mis_comprobantes_views.xml",
        "views/res_config_settings_view.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
