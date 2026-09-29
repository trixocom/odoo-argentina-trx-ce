# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI — Libro IVA Digital + Subdiario IVA",
    "version": "19.0.0.2.2",
    "category": "Accounting/Localizations/Reporting",
    "summary": "Libro IVA Digital (RG 5616, TXT) + Subdiario IVA (PDF/XLSX) para Odoo Community",
    "description": """
Cubre dos reportes complementarios:

1. **Libro IVA Digital RG 5616** — wizard que genera los 5 TXT oficiales
   (VENTAS_CBTE, VENTAS_ALICUOTAS, COMPRAS_CBTE, COMPRAS_ALICUOTAS,
   IMPORTACION_BIENES_ALICUOTA) en un ZIP. Latín-1 + CRLF, longitudes
   fijas validadas contra la spec. Para presentar a AFIP/ARCA.

2. **Subdiario IVA Compras / Ventas** — wizard que genera reporte legible
   para el contador en formato PDF (QWeb landscape A4) y XLSX. Una hoja
   por sección, con desglose por alícuota (21/10.5/27/5/2.5%), bases
   no gravadas y exentas, percepciones (IVA, IIBB, Municipales) e
   internos. Para Compras además: Crédito Fiscal Computable + Despacho
   de Importación.

Se inspira en (no copia) `l10n_ar_reports` de Odoo Enterprise pero
funciona sobre Community sin atadura al motor `account_reports`
enterprise.

Documentación oficial Libro IVA Digital:
https://www.afip.gob.ar/libro-iva-digital/documentos/libro-iva-digital-diseno-registros.pdf
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "trx_l10n_ar_edi",
    ],
    "external_dependencies": {
        # openpyxl: requerido por el Subdiario IVA → Exportar XLSX. El
        # Libro IVA Digital (TXT) NO lo requiere.
        "python": ["openpyxl"],
    },
    "data": [
        "security/ir.model.access.csv",
        "security/account_ar_vat_line_rules.xml",
        "wizards/libro_iva_digital_wizard_view.xml",
        "wizards/subdiario_iva_wizard_view.xml",
        "views/account_ar_vat_line_views.xml",
        "reports/subdiario_iva_report.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
