# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Extensión de `account.journal` para emisión electrónica argentina.

**NO redefinimos los fields que ya trae `l10n_ar` community**:

- `l10n_ar_afip_pos_number` (Integer) — POS ante AFIP.
- `l10n_ar_afip_pos_system` (Selection) — valores oficiales:
    * `II_IM`      — Pre-printed Invoice (talonario)
    * `RLI_RLM`    — Online Invoice (WSFEv1, mercado interno)
    * `BFERCEL`    — Electronic Fiscal Bond (WSBFE)
    * `FEERCELP`   — Export Voucher - Billing Plus
    * `FEERCEL`    — Export Voucher - Online Invoice (WSFEX)
    * `CPERCEL`    — Product Coding - Online Voucher
    * `CF`         — External Fiscal Controller (controlador fiscal)

`l10n_ar` los genera dinámicamente desde `_get_l10n_ar_afip_pos_types_selection`;
si redeclaramos el field con una selection estática rompemos ese mecanismo y
cualquier otro módulo que lo extienda.

Este archivo queda como placeholder — por ahora no sumamos campos propios
al journal. Si más adelante hace falta (ej. flag para forzar un entorno de
WS distinto al de la company), se agrega acá.
"""
from odoo import models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    # WSFEv1 POS systems que nuestro edi soporta. Clave para `_post()`
    # hook de emisión: solo disparamos CAE en journals cuyo `pos_system`
    # esté en esta tupla. Otros sistemas AFIP (export, bond, ctrl fiscal)
    # llegarán en fases posteriores con sus propios módulos.
    #
    # `RAW_MAW` es el código de ARCA para "Factura Electrónica - Web Service"
    # y es EL valor habitual de los puntos de venta que emiten por WSFEv1.
    # Faltaba en esta tupla: los journals con RAW_MAW devolvían None en
    # `_l10n_ar_afip_ws_for_emission`, el hook de `_post()` los salteaba con
    # un `continue` silencioso y las facturas quedaban posteadas SIN CAE, sin
    # error visible para el operador. Referencia de Odoo
    # (l10n_ar_trx_edi/models/account_journal.py):
    #     res.insert(0, ('RAW_MAW', _('Electronic Invoice - Web Service')))
    #     type_mapping = {'RAW_MAW': 'wsfe', 'FEEWS': 'wsfex', 'BFEWS': 'wsbfe'}
    # `RLI_RLM` (Online Invoice / comprobantes en línea) se conserva por
    # compatibilidad con las instalaciones que ya lo tenían configurado.
    _L10N_AR_WSFE_POS_SYSTEMS = ("RAW_MAW", "RLI_RLM")

    # WSFEXv1 POS systems — Factura Electrónica de Exportación. Implementado
    # 2026-04-27 en l10n_ar_trx_edi (Fase 4 — facturas E).
    _L10N_AR_WSFEX_POS_SYSTEMS = ("FEERCEL", "FEERCELP")

    @property
    def _l10n_ar_afip_ws_for_emission(self):
        """Devuelve 'wsfe' / 'wsfex' / None según el `pos_system` del journal.

        Helper para que el código de account.move dispatchee al WS correcto.
        """
        ps = self.l10n_ar_afip_pos_system or ""
        if ps in self._L10N_AR_WSFE_POS_SYSTEMS:
            return "wsfe"
        if ps in self._L10N_AR_WSFEX_POS_SYSTEMS:
            return "wsfex"
        return None
