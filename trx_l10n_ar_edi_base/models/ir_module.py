# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Jerga contable AR: etiqueta de campo (modelo, campo) -> término AR.
# Las columnas del Libro Diario/Mayor en Argentina son Debe / Haber / Saldo
# (no Débito / Crédito / Balance, que es lo que traen tanto es_419 como es_AR).
_AR_FIELD_LABELS = {
    ("account.move.line", "debit"): "Debe",
    ("account.move.line", "credit"): "Haber",
    ("account.move.line", "balance"): "Saldo",
    ("account.analytic.account", "debit"): "Debe",
    ("account.analytic.account", "credit"): "Haber",
}

# Idiomas sobre los que aplicamos la jerga, en orden de preferencia. Solo se
# tocan los que estén ACTIVOS en la instancia: escribir con with_context(lang=X)
# sobre un idioma que no está instalado revienta con
# `UserError: Invalid language code: X` y aborta cualquier `-u`.
_AR_LANGS = ("es_AR", "es_419")


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    @api.model
    def _l10n_ar_terms_langs(self):
        """Idiomas AR **activos** en la instancia, en orden de preferencia.

        `res.lang.search` filtra por `active` (active_test por defecto), así que
        acá solo entran idiomas realmente instalados.
        """
        codes = (
            self.env["res.lang"]
            .sudo()
            .search([("code", "in", list(_AR_LANGS))])
            .mapped("code")
        )
        return [code for code in _AR_LANGS if code in codes]

    @api.model
    def _l10n_ar_apply_accounting_terms(self):
        """Aplica la jerga contable argentina (Debe/Haber/Saldo + Movimientos
        contables) sobre los idiomas AR activos de la instancia.

        Por qué así y no solo con el .po:
          * Odoo rotula las columnas del mayor como Débito/Crédito/Balance tanto
            en es_419 como en es_AR.
          * "Debit"/"Credit" son msgid mixtos code+modelo en `account`. Durante un
            `-u`, el paso de reflexión de campos ("verifying fields for every
            extended model") re-deriva field_description de account.move.line
            traduciendo el string fuente vía el catálogo de código, revirtiendo
            cualquier override que hayamos hecho durante la carga de datos. Por eso
            el `<function>` de instalación no alcanza para esos dos campos.
          * En cambio, escribir field_description por ORM en el server ya levantado
            (post-carga) SÍ persiste. De ahí que esto se dispare además vía ir.cron
            (nextcall en el pasado) que corre apenas termina el update/restart.
            Es idempotente.

        Cubre dos caminos:
          1. Recarga i18n/<lang>.po con overwrite=True (nombres de menú/acción
             "Journal Items" -> "Movimientos contables" y cuentas analíticas).
          2. Escritura directa por ORM de las etiquetas de los campos del mayor.

        Si la instancia no tiene ningún idioma de `_AR_LANGS` activo, no hay nada
        que traducir: se loguea y se sale sin tocar nada. Nunca se escribe sobre un
        idioma no instalado, que es lo que abortaba el `-u all`.
        """
        langs = self._l10n_ar_terms_langs()
        if not langs:
            _logger.info(
                "l10n_ar_trx_edi_base: ningún idioma AR activo (%s); se omite la jerga contable",
                ", ".join(_AR_LANGS),
            )
            return True

        Field = self.env["ir.model.fields"].sudo()
        # Early-return idempotente: si la etiqueta clave ya está en AR en todos los
        # idiomas activos, no hay nada que hacer (evita trabajo en cada corrida
        # diaria del cron).
        debit = Field._get("account.move.line", "debit")
        debit_label = _AR_FIELD_LABELS[("account.move.line", "debit")]
        if debit and all(
            debit.with_context(lang=lang).field_description == debit_label
            for lang in langs
        ):
            return True

        # _load_module_terms ignora los idiomas para los que el módulo no tiene .po.
        self._load_module_terms(["l10n_ar_trx_edi_base"], langs, overwrite=True)

        changed = []
        for lang in langs:
            for (model_name, field_name), value in _AR_FIELD_LABELS.items():
                fld = Field._get(model_name, field_name)
                if not fld:
                    continue
                current = fld.with_context(lang=lang).field_description
                if current != value:
                    fld.with_context(lang=lang).field_description = value
                    changed.append("%s.%s [%s]" % (model_name, field_name, lang))

        if changed:
            _logger.info(
                "l10n_ar_trx_edi_base: jerga contable AR aplicada en %s",
                ", ".join(changed),
            )
        return True
