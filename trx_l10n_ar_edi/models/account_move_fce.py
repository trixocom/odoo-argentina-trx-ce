# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Factura de Crédito Electrónica MiPyME (RG 4919/2021).

FCE MiPyME es un régimen donde una PyME emite a una empresa grande una
factura electrónica con plazo de pago largo (típicamente 30/60/90 días).
La factura se carga en un sistema centralizado donde se puede transferir
a terceros (descuento bancario / factoring) antes del vencimiento, hasta
que el deudor la cancela o vence.

Tipos de comprobante FCE:
  - 201 / 206 / 211: FCE A / B / C (la factura propiamente dicha).
  - 202 / 207 / 212: NC FCE A / B / C.
  - 203 / 208 / 213: ND FCE A / B / C.

Información requerida en el payload WSFEv1 vía bloque `Opcionales`:

  - Id=27, Valor='SCA' o 'ADC':
    "Sistema de Circulación Abierta" (SCA, transferencia electrónica entre
    sujetos) o "Agente Depositario Colectivo" (ADC, custodia centralizada
    en CV/MAV). El emisor lo elige al momento de emitir. RG 4919/2021.
  - Id=2101, Valor=CBU del emisor:
    A dónde se acredita el pago si el receptor cancela vía CBU. Solo
    aplica a FCE MiPyME (no a NC/ND).
  - Id=22, Valor='S' / 'N':
    En NC/ND FCE: indica si el documento original fue rechazado por el
    receptor. Solo en NC/ND FCE.

El campo del move `l10n_ar_fce_transmission_type` permite override por
factura del default de la company.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


# Códigos AFIP de los tipos de comprobante que son FCE MiPyME.
FCE_DOC_CODES = {201, 206, 211}            # facturas
FCE_REFUND_DOC_CODES = {202, 203, 207, 208, 212, 213}  # NC + ND


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_ar_is_mipyme_fce = fields.Boolean(
        string="Es FCE MiPyME",
        compute="_compute_l10n_ar_is_mipyme_fce",
        help="True si el tipo de documento es FCE (201/206/211 o sus NC/ND).",
    )
    l10n_ar_fce_transmission_type = fields.Selection(
        selection=[
            ("SCA", "SCA - TRANSFERENCIA AL SISTEMA DE CIRCULACION ABIERTA"),
            ("ADC", "ADC - AGENTE DE DEPOSITO COLECTIVO"),
        ],
        string="FCE: Opción de transmisión",
        compute="_compute_l10n_ar_fce_transmission_type",
        store=True,
        readonly=False,
        copy=False,
        help=(
            "Sólo aplica a comprobantes FCE MiPyME. Si no se indica, se "
            "usa el valor por defecto de la empresa. RG 4919/2021."
        ),
    )
    l10n_ar_afip_fce_is_cancellation = fields.Boolean(
        string="FCE: ¿Es cancelación de la factura original?",
        copy=False,
        help=(
            "Sólo en NC/ND FCE. True si el documento original fue rechazado "
            "explícitamente por el receptor (lo informamos a ARCA en el "
            "Opcional Id=22 con valor 'S')."
        ),
    )

    @api.depends("l10n_latam_document_type_id")
    def _compute_l10n_ar_is_mipyme_fce(self):
        for move in self:
            try:
                code = int(move.l10n_latam_document_type_id.code or 0)
            except (TypeError, ValueError):
                code = 0
            move.l10n_ar_is_mipyme_fce = (
                code in FCE_DOC_CODES or code in FCE_REFUND_DOC_CODES
            )

    @api.depends("l10n_latam_document_type_id", "company_id")
    def _compute_l10n_ar_fce_transmission_type(self):
        """Default desde la empresa cuando es FCE; sino vacío.

        Hacemos compute store=True readonly=False (compute con default
        editable) para que el operador pueda overridear factura por factura.
        """
        for move in self:
            if not move.l10n_ar_is_mipyme_fce:
                move.l10n_ar_fce_transmission_type = False
                continue
            if not move.l10n_ar_fce_transmission_type:
                move.l10n_ar_fce_transmission_type = (
                    move.company_id.l10n_ar_fce_transmission_type or False
                )

    def _is_mipyme_fce(self):
        """Helper compatibilidad con nomenclatura enterprise."""
        self.ensure_one()
        try:
            code = int(self.l10n_latam_document_type_id.code or 0)
        except (TypeError, ValueError):
            return False
        return code in FCE_DOC_CODES

    def _is_mipyme_fce_refund(self):
        """True si es NC o ND de FCE MiPyME."""
        self.ensure_one()
        try:
            code = int(self.l10n_latam_document_type_id.code or 0)
        except (TypeError, ValueError):
            return False
        return code in FCE_REFUND_DOC_CODES

    def _l10n_ar_get_opcionales(self):
        """Extiende el hook base con los Opcionales de FCE."""
        opcionales = super()._l10n_ar_get_opcionales()

        # FCE factura: Id=27 (SCA/ADC obligatorio) + Id=2101 (CBU) si tiene
        if self._is_mipyme_fce():
            transmission = self.l10n_ar_fce_transmission_type
            if not transmission:
                raise UserError(_(
                    "Factura FCE MiPyME %s: tenés que indicar la 'Opción de "
                    "transmisión' (SCA o ADC) antes de emitir. Configurá un "
                    "default en Configuración → Contabilidad → Localización "
                    "para Argentina, o setealo en la pestaña AFIP."
                ) % self.display_name)
            opcionales.append({"Id": 27, "Valor": transmission})

            cbu = self._l10n_ar_get_emitter_cbu()
            if cbu:
                opcionales.append({"Id": 2101, "Valor": cbu})

        # NC/ND FCE: Id=22 indica si fue cancelación
        if self._is_mipyme_fce_refund():
            valor = "S" if self.l10n_ar_afip_fce_is_cancellation else "N"
            opcionales.append({"Id": 22, "Valor": valor})

        return opcionales

    def _l10n_ar_get_emitter_cbu(self):
        """Devuelve el CBU del emisor (nuestra company) para FCE.

        Lo buscamos en `partner_bank_id` del propio move (preferido) o, si
        no hay, en el primer res.partner.bank de la empresa con CBU.
        """
        self.ensure_one()
        bank = self.partner_bank_id
        if bank and getattr(bank, "acc_type", None) == "cbu":
            return bank.acc_number
        # Fallback: buscar el primer CBU de la empresa.
        company_partner = self.company_id.partner_id
        bank = company_partner.bank_ids.filtered(
            lambda b: getattr(b, "acc_type", None) == "cbu"
        )[:1]
        if bank:
            return bank.acc_number
        return None
