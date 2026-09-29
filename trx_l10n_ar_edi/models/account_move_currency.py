# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Política RG 5616/2024 — pago en moneda extranjera (`CanMisMonExt`).

RG 5616 introdujo el campo `CanMisMonExt` en el FECAESolicitar para que
el emisor declare si el comprobante en moneda extranjera (USD/EUR/etc.)
se va a **cancelar** en esa misma moneda o se va a pesificar para el
pago.

  - 'S' (Sí, en moneda extranjera): ARCA bloquea el cambio de cotización
    y exige que la `MonCotiz` sea exactamente la del último día hábil
    publicado por el BNA.
  - 'N' (No, se pesifica): cualquier `MonCotiz` razonable (entre el 2%%
    y 400%% de la oficial) es aceptada.

El default lo decide la **política de la empresa** (`res.company.
l10n_ar_payment_foreign_currency_policy`):

  - 'no': siempre 'N' (default conservador). Las USD se pesifican.
  - 'yes': siempre 'S'. Para empresas que solo cobran en divisa.
  - 'depends_currency': 'S' si la cuenta a cobrar (cuenta de payment_term
    en el move) está configurada con una currency forzada distinta de
    la moneda compañía; sino 'N'. Refleja el caso de empresas con
    cuentas multi-moneda.

Sólo aplica a comprobantes con currency_id != company_currency_id —
para los ARS no tiene sentido y AFIP rechaza el campo.
"""
from odoo import _, api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    l10n_ar_payment_foreign_currency = fields.Selection(
        selection=[
            ("S", "Sí (cancelación en moneda extranjera)"),
            ("N", "No (se cancela en moneda local)"),
        ],
        string="Pago en moneda extranjera (RG 5616)",
        compute="_compute_l10n_ar_payment_foreign_currency",
        store=True,
        readonly=False,
        copy=False,
        help=(
            "Indica a ARCA si esta factura se va a cancelar en su moneda "
            "original (RG 5616/2024). El default se calcula según la "
            "política de la empresa, pero podés sobreescribirlo factura "
            "por factura. Sólo aplica si la moneda del comprobante no es "
            "la moneda de la empresa."
        ),
    )

    @api.depends(
        "currency_id", "company_currency_id", "company_id",
        "company_id.l10n_ar_payment_foreign_currency_policy",
        "line_ids.account_id",
        "line_ids.account_id.currency_id",
    )
    def _compute_l10n_ar_payment_foreign_currency(self):
        """Computa el default del flag según la política de la empresa.

        Si el move ya tenía un valor manual (compute store readonly=False),
        el ORM lo respeta — solo computamos para los que no tienen valor.
        """
        for move in self:
            # Solo aplica si la moneda no es la de la empresa.
            if (move.country_code != "AR"
                    or not move.currency_id
                    or move.currency_id == move.company_currency_id):
                move.l10n_ar_payment_foreign_currency = False
                continue

            # Si el operador ya seteó un valor manual, no lo pisamos.
            if move.l10n_ar_payment_foreign_currency:
                continue

            policy = move.company_id.l10n_ar_payment_foreign_currency_policy or "no"
            if policy == "yes":
                move.l10n_ar_payment_foreign_currency = "S"
            elif policy == "no":
                move.l10n_ar_payment_foreign_currency = "N"
            elif policy == "depends_currency":
                # Buscar la línea de payment_term (la de la cuenta a cobrar/pagar)
                # y mirar si su cuenta tiene una currency forzada distinta de
                # la moneda compañía.
                payment_terms = move.line_ids.filtered(
                    lambda aml: aml.display_type == "payment_term"
                )
                account = payment_terms.account_id[:1]
                if (account.currency_id
                        and account.currency_id != move.company_currency_id):
                    move.l10n_ar_payment_foreign_currency = "S"
                else:
                    move.l10n_ar_payment_foreign_currency = "N"
            else:
                move.l10n_ar_payment_foreign_currency = "N"

    def _l10n_ar_get_can_mis_mon_ext(self):
        """Override del hook base — devuelve el valor del campo (default 'N')."""
        self.ensure_one()
        return self.l10n_ar_payment_foreign_currency or "N"
