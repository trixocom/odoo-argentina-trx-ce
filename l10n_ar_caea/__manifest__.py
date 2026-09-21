# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
{
    "name": "Argentina EDI — CAEA",
    "version": "19.0.2.1.0",
    "category": "Accounting/Localizations/EDI",
    "summary": "CAEA — Código de Autorización Electrónico Anticipado (régimen de contingencia)",
    "description": """
Soporte CAEA (RG 5785/2025, ex RG 2926) para emisión electrónica argentina
en Odoo 19 Community. Régimen de contingencia: el contribuyente solicita un
código por anticipado para una quincena, y lo usa cuando WSFEv1 está caído /
hay problemas de red.

Funcionalidades:

* Modelo `l10n_ar.caea` con un registro por (company, periodo, orden).
* Botón "Solicitar CAEA" en Configuración → Localización Argentina.
* Cron diario que marca CAEA vencidos.
* Cron diario que rinde a AFIP los comprobantes emitidos con CAEA
  pendientes (`FECAEARegInformativo`) e informa "sin movimiento"
  (`FECAEASinMovimientoInformar`) los PV CAEA sin uso.
* **PV exclusivos CAEA** (RG 5785/2025): `account.journal` gana
  `l10n_ar_afip_pos_caea` (el diario ES un PV de contingencia; numera
  con secuencia local, sin red) y `l10n_ar_caea_journal_id` (diario de
  contingencia al que se re-rutea un diario CAE cuando WSFE falla).
* Contingencia reactiva: `TransportError` de WSFEv1 → re-ruteo del
  comprobante al diario CAEA (draft → cambio de diario → repost con
  numeración del PV CAEA).
* Contingencia preventiva: si `afip_webservice_monitor` está instalado
  y reporta WSFE caído, se re-rutea directo sin esperar timeout.
* `l10n_ar_caea_emission_dt`: hora real de emisión → `CbteFchHsGen`
  en la rendición (clave para POS offline, ver `l10n_ar_pos_caea`).

Periodos:
  * Q1 (orden=1): 1 al 15 del mes.
  * Q2 (orden=2): 16 al fin del mes.

Plazos AFIP (RG 5785/2025):
  * Solicitud: desde 5 días previos al inicio de la quincena, o durante ella.
  * Rendición: hasta 8 días corridos después del cierre.
    """,
    "author": "Trixocom",
    "website": "https://trixocom.com",
    "license": "LGPL-3",
    "depends": [
        "l10n_ar_trx_edi",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "views/l10n_ar_caea_views.xml",
        "views/l10n_ar_caea_log_views.xml",
        "views/account_journal_views.xml",
        "views/res_config_settings_view.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
