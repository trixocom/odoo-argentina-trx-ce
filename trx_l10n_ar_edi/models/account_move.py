# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Emisión electrónica de comprobantes argentinos sobre `account.move`.

Responsabilidades de este archivo:

1. Orquestar el flujo de emisión: mapear account.move a payload AFIP,
   llamar al WS, guardar CAE/vto/resultado/XML en el move.
2. Calcular el QR RG 4291 cuando hay CAE (compute override del campo que
   `l10n_ar_trx_edi_base` declaró como stub).
3. Acción manual "Enviar a ARCA" para disparar emisión fuera del `_post`.
4. Hook en `_post` — por ahora *opt-in*: solo se solicita CAE si el
   journal está marcado como electrónico. No queremos romper moves no
   argentinos ni journals preimpresos.

**Lo que este archivo NO hace**:
- Crear el secuencial del número de comprobante: eso lo hará Odoo core
  o un override posterior que sincronice con `FECompUltimoAutorizado`.
  Por ahora usamos el número que ya trae el move.
- Post-procesar observaciones en UI: solo las guardamos en texto plano;
  el wizard de consulta AFIP es otro entregable.
"""
import base64
import logging
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config as _odoo_config

from ..lib import payload as payload_lib
from ..lib import payload_fex as payload_fex_lib
from ..lib import qr_code as qr_lib

# Reutilizamos el cliente de WS del módulo afip_ws
from odoo.addons.l10n_ar_afip_ws.lib import errors as ws_errors
from odoo.addons.l10n_ar_afip_ws.lib import transport as ws_transport
from odoo.addons.l10n_ar_afip_ws.lib import wsfe as ws_wsfe
from odoo.addons.l10n_ar_afip_ws.lib import wsfex as ws_wsfex

_logger = logging.getLogger(__name__)


def _l10n_ar_in_test_mode():
    """¿Estamos corriendo tests?

    Odoo 19 eliminó ``Registry.in_test_mode()`` (existía hasta 18.0). El
    indicador vigente es ``odoo.modules.module.current_test``, que se lee en
    runtime, y ``config['test_enable']`` para los tests at-install.

    Ante cualquier problema devuelve False: en producción el comportamiento
    seguro es commitear el CAE apenas AFIP lo otorga.
    """
    try:
        from odoo.modules import module as _odoo_module

        if getattr(_odoo_module, "current_test", False):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        return bool(_odoo_config.get("test_enable"))
    except Exception:  # noqa: BLE001
        return False

_CTX_SYNC = "l10n_ar_trx_sync_afip_seq"  # sync numeracion AFIP solo al postear

# Códigos WSFEv1 que significan "ese número ya fue consumido en AFIP".
# Ante cualquiera de estos NO hay que reintentar a ciegas: hay que
# consultar el comprobante con FECompConsultar y, si es el mismo que
# estábamos emitiendo, adoptar el CAE que AFIP ya otorgó.
#   10016 → el número (o la fecha) no se corresponde con el próximo a autorizar
#   10051 → CAE ya emitido para ese comprobante
#   10100 → reproceso: AFIP ya procesó ese comprobante en una llamada previa
_WSFE_NRO_YA_CONSUMIDO = (10016, 10051, 10100)


class AccountMove(models.Model):
    _inherit = "account.move"

    # Bandera UX: True si este move se va a emitir contra WSFEv1 al
    # postear. Sirve para que el view reemplace el botón "Confirmar"
    # estándar por uno con string "Validar en AFIP/ARCA", que es más
    # explícito para el operador (sabe que ese click pega contra AFIP,
    # no es solo un cambio de estado contable).
    l10n_ar_is_electronic_invoice = fields.Boolean(
        string="Es factura electrónica AFIP",
        compute="_compute_l10n_ar_is_electronic_invoice",
        help="True si el journal está configurado para WSFEv1 y el move "
             "es una factura/NC argentina.",
    )

    @api.depends(
        "country_code", "move_type",
        "journal_id.l10n_ar_afip_pos_system",
    )
    def _compute_l10n_ar_is_electronic_invoice(self):
        for move in self:
            j = move.journal_id
            move.l10n_ar_is_electronic_invoice = bool(
                move.country_code == "AR"
                and move.move_type in ("out_invoice", "out_refund")
                and j
                and j.l10n_ar_afip_pos_system in j._L10N_AR_WSFE_POS_SYSTEMS
            )

    # --------------------------------------------------------------
    # Campos derivados
    # --------------------------------------------------------------
    def _compute_l10n_ar_afip_qr_code(self):
        """Override del stub en l10n_ar_trx_edi_base. Calcula la URL del QR RG 4291."""
        for move in self:
            if not (move.l10n_ar_afip_auth_code and move.l10n_ar_afip_auth_mode in ("CAE", "CAEA")):
                move.l10n_ar_afip_qr_code = False
                continue
            try:
                data = move._l10n_ar_get_qr_data()
                move.l10n_ar_afip_qr_code = qr_lib.build_qr_url(data)
            except Exception as e:  # pragma: no cover - defensivo
                _logger.warning("No pude calcular el QR para move %s: %s", move.id, e)
                move.l10n_ar_afip_qr_code = False

    def _l10n_ar_afip_qr_image(self, size=400):
        """PNG del QR RG 4291 ya codificado en base64, para embeber en el PDF.

        El reporte NO debe apuntar a ``/report/barcode/...``: esa URL es
        relativa y wkhtmltopdf la resuelve contra ``web.base.url``, o sea que
        sale a Internet y vuelve por el proxy/CDN publico. Si ese request
        falla (challenge de Cloudflare, DNS, firewall) el PDF se emite igual
        pero SIN el QR, que es obligatorio en el comprobante (RG 4291).
        Generando la imagen in-process el QR no depende de la red.

        Devuelve ``False`` si el move no tiene QR (sin CAE) o si la
        generacion falla, para que el template no rompa el PDF.
        """
        self.ensure_one()
        if not self.l10n_ar_afip_qr_code:
            return False
        try:
            png = self.env["ir.actions.report"].sudo().barcode(
                "QR",
                self.l10n_ar_afip_qr_code,
                width=size,
                height=size,
                quiet=0,
            )
        except Exception as e:  # pragma: no cover - defensivo
            _logger.warning(
                "No pude generar la imagen del QR para move %s: %s", self.id, e
            )
            return False
        return base64.b64encode(png)

    # --------------------------------------------------------------
    # Mapeo account.move → payload AFIP
    # --------------------------------------------------------------
    def _l10n_ar_get_qr_data(self):
        """Datos para el QR (dict listo para `qr_code.build_qr_payload`)."""
        self.ensure_one()
        cuit = (self.company_id.partner_id.vat or "").replace("-", "")
        doc_tipo, doc_nro = self._l10n_ar_get_receptor_doc()
        return qr_lib.build_qr_payload(
            cae=self.l10n_ar_afip_auth_code,
            date=self.invoice_date or fields.Date.context_today(self),
            cuit=cuit,
            pto_vta=self.journal_id.l10n_ar_afip_pos_number or 0,
            cbte_tipo=int(self.l10n_latam_document_type_id.code or 0),
            cbte_nro=self._l10n_ar_get_cbte_nro(),
            importe=self.amount_total,
            moneda=self._l10n_ar_get_afip_moneda(),
            cotizacion=self._l10n_ar_get_afip_cotizacion(),
            doc_tipo_receptor=doc_tipo,
            doc_nro_receptor=doc_nro,
            auth_mode=self.l10n_ar_afip_auth_mode,
        )

    def _l10n_ar_get_cbte_nro(self):
        """Extrae el número de comprobante (la parte después del '-')."""
        self.ensure_one()
        # l10n_ar core guarda el nombre como "000X-00000123" o "FA-A 0001-00000123"
        # según el formato del documento. Tomamos el último bloque numérico.
        name = self.name or ""
        parts = [p for p in name.replace(" ", "-").split("-") if p.strip().isdigit()]
        if parts:
            return int(parts[-1])
        raise UserError(_(
            "No pude extraer el número de comprobante del nombre %r. "
            "Verificá que el move tenga un nombre con formato 'NNNN-NNNNNNNN'."
        ) % name)

    def _l10n_ar_get_receptor_doc(self):
        """Devuelve (tipo_doc_afip, nro_doc) del partner receptor.

        Tipos AFIP: 80=CUIT, 86=CUIL, 96=DNI, 99=Consumidor Final sin ID.
        """
        self.ensure_one()
        p = self.partner_id
        # l10n_ar provee `l10n_latam_identification_type_id`; usamos su código AFIP.
        identification = p.l10n_latam_identification_type_id
        if identification and identification.l10n_ar_afip_code:
            try:
                doc_tipo = int(identification.l10n_ar_afip_code)
            except (TypeError, ValueError):
                doc_tipo = 99
        else:
            doc_tipo = 99
        vat = (p.vat or "").replace("-", "").replace(" ", "") if p.vat else ""
        if doc_tipo == 99 or not vat:
            return 99, 0
        try:
            return doc_tipo, int(vat)
        except ValueError:
            return 99, 0

    def _l10n_ar_get_afip_moneda(self):
        """Convierte currency.name a código AFIP ('PES', 'DOL', ...)."""
        self.ensure_one()
        code = self.currency_id.name
        # l10n_ar en community trae `l10n_ar_afip_code` en res.currency.
        if hasattr(self.currency_id, "l10n_ar_afip_code") and self.currency_id.l10n_ar_afip_code:
            return self.currency_id.l10n_ar_afip_code
        mapping = {"ARS": "PES", "USD": "DOL", "EUR": "060"}
        return mapping.get(code, code)

    def _l10n_ar_get_afip_cotizacion(self):
        self.ensure_one()
        if self.currency_id == self.company_id.currency_id:
            return 1
        # Rate inverso: si 1 USD = 1000 ARS y currency_id=USD,
        # Odoo guarda rate=1/1000 para USD; cotización AFIP espera ARS/USD = 1000.
        rate = self.currency_id._convert(
            1, self.company_id.currency_id, self.company_id,
            self.invoice_date or fields.Date.context_today(self),
        )
        return float(rate)

    def _l10n_ar_get_condicion_iva_receptor_id(self):
        """Código AFIP de `CondicionIVAReceptorId` (RG 5616).

        Catálogo oficial (vía FEParamGetCondicionIvaReceptor, manual WSFEv1
        v4.3 RG-4291, validado 2026-05-13):

            Id | Descripción                                     | Cmp_Clase
            ---+-------------------------------------------------+----------
             1 | IVA Responsable Inscripto                       | A / M / C
             4 | IVA Sujeto Exento                               | B / C
             5 | Consumidor Final                                | B / C
             6 | Responsable Monotributo                         | A / M / C
             7 | Sujeto No Categorizado                          | B / C
             8 | Proveedor del Exterior                          | B / C
             9 | Cliente del Exterior                            | B / C
            10 | IVA Liberado – Ley N° 19.640                    | B / C
            13 | Monotributista Social                           | A / M / C
            15 | IVA No Alcanzado                                | B / C
            16 | Monotributo Trabajador Independiente Promovido  | A / M / C

        IMPORTANTE — deadline RG 5616: a partir del **01/06/2026** AFIP
        rechaza las solicitudes de emisión sin este dato. Por eso el helper
        siempre devuelve un entero (default 5 = Consumidor Final), nunca
        None. Si el partner no tiene `l10n_ar_afip_responsibility_type_id`
        asignado, asumimos CF.
        """
        self.ensure_one()
        p = self.partner_id
        resp = p.l10n_ar_afip_responsibility_type_id
        if not resp or not resp.code:
            return 5  # Consumidor Final por defecto
        # El code de l10n_ar es el código AFIP de RESPONSABILIDAD (1=RI,
        # 4=Exento, etc.) y CondicionIVAReceptorId resulta ser EL MISMO
        # número en la mayoría de los casos. Mantenemos el mapeo explícito
        # como salvaguarda por si AFIP reasigna IDs y por documentación.
        resp_to_cond = {
            "1": 1,    # IVA Responsable Inscripto
            "4": 4,    # IVA Sujeto Exento
            "5": 5,    # Consumidor Final
            "6": 6,    # Responsable Monotributo
            "7": 7,    # Sujeto No Categorizado
            "8": 8,    # Proveedor del Exterior
            "9": 9,    # Cliente del Exterior
            "10": 10,  # IVA Liberado - Ley 19.640
            "13": 13,  # Monotributista Social
            "15": 15,  # IVA no alcanzado
            "16": 16,  # Monotributo Trabajador Independiente Promovido (Dto 444/2023)
        }
        return resp_to_cond.get(resp.code, 5)

    def _l10n_ar_get_iva_items(self):
        """Devuelve la lista de alícuotas con base e importe para AFIP.

        En Odoo 19 community l10n_ar el código AFIP del IVA vive en
        `account.tax.group.l10n_ar_vat_afip_code` (no en `account.tax` —
        eso era enterprise antiguo). Catálogo AFIP `AlicIva/Id`:
            3=0%, 4=10.5%, 5=21%, 6=27%, 8=5%, 9=2.5%.

        Recorre las líneas agrupando por ese código. Si un tax no tiene
        código AFIP (ej. percepciones, que van en ImpTrib, no en iva_items)
        lo filtra.
        """
        self.ensure_one()
        from collections import defaultdict
        acc = defaultdict(lambda: {"base": 0.0, "importe": 0.0})
        for line in self.invoice_line_ids:
            for tax in line.tax_ids:
                code = tax.tax_group_id.l10n_ar_vat_afip_code if tax.tax_group_id else None
                # Fallback para compat: si en alguna variante de l10n_ar el
                # código quedó directo en account.tax, lo leemos ahí también.
                if not code:
                    code = getattr(tax, "l10n_ar_afip_code", None)
                if not code:
                    continue
                # `price_subtotal` es la base sin IVA. Recalculamos el importe
                # `base * percent / 100` para consolidar entre líneas — no
                # confiamos en el `tax_line_id` del move porque puede tener
                # redondeos por línea que no suman al total AFIP esperado.
                base = line.price_subtotal
                try:
                    percent = float(tax.amount or 0)
                except Exception:
                    percent = 0.0
                importe = base * percent / 100.0
                acc[int(code)]["base"] += base
                acc[int(code)]["importe"] += importe
        return [
            {"codigo": code, "base": v["base"], "importe": v["importe"]}
            for code, v in acc.items()
        ]

    def _l10n_ar_get_tributos(self):
        """Devuelve la lista de Tributos para el nodo `Tributos` del WSFE.

        AFIP separa el IVA (que va en `Iva.AlicIva`) de los demás tributos
        (que van en `Tributos.Tributo`): percepción IIBB provincial,
        impuestos municipales, internos, percepción IVA RG3337, etc.

        En l10n_ar (community 19.0) el código AFIP del tributo vive en
        `account.tax.group.l10n_ar_tribute_afip_code` (Selection con
        valores `'01'..'09', '99'`). El mapeo a los Ids de WSFE es 1:1
        (`int(code)`), validado contra `FEParamGetTiposTributos` el
        2026-04-25:

            01 → 1  Impuestos nacionales
            02 → 2  Impuestos provinciales
            03 → 3  Tributos municipales
            04 → 4  Impuestos Internos
            06 → 6  Percepción de IVA
            07 → 7  Percepción de IIBB
            08 → 8  Percepciones por Tributos Municipales
            09 → 9  Otras Percepciones
            99 → 99 Otro

        Agrupa por `tax.id` (cada tax = un Tributo distinto) y suma la
        base por línea. Recalcula `Importe = BaseImp * Alic / 100` —
        igual razonamiento que en `_l10n_ar_get_iva_items`.
        """
        self.ensure_one()
        from collections import defaultdict
        acc = defaultdict(lambda: {"tax": None, "base": 0.0})
        for line in self.invoice_line_ids:
            for tax in line.tax_ids:
                code = (
                    tax.tax_group_id.l10n_ar_tribute_afip_code
                    if tax.tax_group_id else None
                )
                if not code:
                    continue
                acc[tax.id]["tax"] = tax
                acc[tax.id]["base"] += line.price_subtotal
        items = []
        for tax_id, v in acc.items():
            tax = v["tax"]
            try:
                id_tributo = int(tax.tax_group_id.l10n_ar_tribute_afip_code)
            except Exception:
                continue
            try:
                alic = float(tax.amount or 0)
            except Exception:
                alic = 0.0
            base = v["base"]
            importe = base * alic / 100.0
            items.append({
                "Id": id_tributo,
                # AFIP recorta `Desc` a 80 chars en su validador.
                "Desc": (tax.name or "")[:80],
                "BaseImp": round(base, 2),
                "Alic": round(alic, 2),
                "Importe": round(importe, 2),
            })
        return items

    def _l10n_ar_get_cbtes_asoc(self):
        """Devuelve la lista de `CbteAsoc` para NC / ND.

        AFIP exige esto para NC (doc codes 3, 8, 13) y ND (2, 7, 12) cuando
        la factura original existe. El bloque `CbteAsoc` lleva Tipo, PtoVta,
        Nro; Cuit y CbteFch son opcionales pero recomendados porque AFIP
        los valida contra sus registros.

        - NC: core Odoo setea `reversed_entry_id` al invocar `_reverse_moves`.
        - ND: l10n_ar community (si está) expone `debit_origin_id`. Si el
          campo no existe, lo ignoramos silenciosamente.
        """
        self.ensure_one()
        origin = self.reversed_entry_id if self.reversed_entry_id else self.env["account.move"]
        if not origin and "debit_origin_id" in self._fields:
            origin = self.debit_origin_id or origin
        if not origin:
            return []
        if not origin.l10n_latam_document_type_id:
            return []
        if not origin.l10n_ar_afip_auth_code:
            # Origen sin CAE ⇒ no se puede asociar a AFIP todavía. Dejamos
            # que el builder arme el request sin CbtesAsoc; si AFIP lo
            # rechaza, el usuario verá el error claro y emite primero la FA.
            return []
        cbte_nro_str = (origin.l10n_latam_document_number or "").split("-")[-1]
        if not cbte_nro_str.isdigit():
            return []
        cbte = {
            "Tipo": int(origin.l10n_latam_document_type_id.code),
            "PtoVta": origin.journal_id.l10n_ar_afip_pos_number,
            "Nro": int(cbte_nro_str),
        }
        emisor_vat = (origin.company_id.vat or "").replace("-", "").replace(" ", "")
        if emisor_vat.isdigit():
            cbte["Cuit"] = emisor_vat
        if origin.invoice_date:
            cbte["CbteFch"] = origin.invoice_date.strftime("%Y%m%d")
        return [cbte]

    def _l10n_ar_get_wsfe_payload(self):
        """Construye el dict `FeCAEReq` para `FECAESolicitar`.

        :return: dict listo para pasar a `wsfe.cae_solicitar`.
        """
        self.ensure_one()
        doc_tipo, doc_nro = self._l10n_ar_get_receptor_doc()
        # Concepto: l10n_ar community ya lo computa según los tipos de
        # producto de las líneas (method `_compute_l10n_ar_afip_concept`
        # / `_get_concept`). Valores: '1'=Productos, '2'=Servicios,
        # '3'=Productos y Servicios. Si está vacío (no AR o sin docs),
        # caemos a productos.
        concepto_str = self.l10n_ar_afip_concept or str(payload_lib.CONCEPTO_PRODUCTOS)
        concepto = int(concepto_str)
        # Tributos (percepciones IIBB, municipales, etc.). amount_tax de
        # Odoo SUMA IVA + percepciones. Para WSFE necesitamos separar:
        #   imp_iva = solo IVA (taxes con `l10n_ar_vat_afip_code`)
        #   imp_trib = solo tributos (taxes con `l10n_ar_tribute_afip_code`)
        tributos = self._l10n_ar_get_tributos()
        imp_trib = sum(t["Importe"] for t in tributos)
        # imp_iva = amount_tax - imp_trib (lo que sobra es IVA puro).
        imp_iva = round((self.amount_tax or 0.0) - imp_trib, 2)

        kwargs = dict(
            pto_vta=self.journal_id.l10n_ar_afip_pos_number,
            cbte_tipo=int(self.l10n_latam_document_type_id.code),
            concepto=concepto,
            cbte_fecha=self.invoice_date,
            doc_tipo=doc_tipo,
            doc_nro=doc_nro,
            cbte_nro=self._l10n_ar_get_cbte_nro(),
            imp_neto=self.amount_untaxed,
            imp_iva=imp_iva,
            imp_trib=imp_trib,
            imp_op_ex=0,
            imp_tot_conc=0,
            mon_id=self._l10n_ar_get_afip_moneda(),
            mon_cotiz=self._l10n_ar_get_afip_cotizacion(),
            cond_iva_receptor_id=self._l10n_ar_get_condicion_iva_receptor_id(),
            iva_items=self._l10n_ar_get_iva_items(),
        )
        if tributos:
            kwargs["tributos"] = tributos
        # Para servicios/mixto AFIP exige el período y vto de pago.
        if concepto in (payload_lib.CONCEPTO_SERVICIOS, payload_lib.CONCEPTO_MIXTO):
            kwargs["fch_serv_desde"] = self.l10n_ar_afip_service_start or self.invoice_date
            kwargs["fch_serv_hasta"] = self.l10n_ar_afip_service_end or self.invoice_date
            kwargs["fch_vto_pago"] = self.invoice_date_due or self.invoice_date
        # NC / ND: adjuntar comprobantes asociados si los hay.
        cbtes_asoc = self._l10n_ar_get_cbtes_asoc()
        if cbtes_asoc:
            kwargs["cbtes_asoc"] = cbtes_asoc
        # Opcionales (FCE Id=27 SCA/ADC, Id=2101 CBU, Id=22 cancelación, etc.)
        # — el método base devuelve [], los módulos hijos lo extienden.
        opcionales = self._l10n_ar_get_opcionales()
        if opcionales:
            kwargs["opcionales"] = opcionales
        # Política RG 5616 — pago en moneda extranjera.
        if self.currency_id != self.company_id.currency_id:
            kwargs["can_mis_mon_ext"] = self._l10n_ar_get_can_mis_mon_ext()
        return payload_lib.build_fecae_request(**kwargs)

    def _l10n_ar_get_opcionales(self):
        """Hook extensible: devuelve lista de dicts {Id, Valor} para `Opcionales`.

        Base vacío; FCE MiPyME (`account_move_fce.py`) lo extiende para
        agregar Id=27 (SCA/ADC), Id=2101 (CBU), Id=22 (es cancelación).
        Otros features pueden agregar más en sus propios overrides.
        """
        self.ensure_one()
        return []

    def _l10n_ar_get_can_mis_mon_ext(self):
        """Hook extensible: devuelve 'S' o 'N' para CanMisMonExt (RG 5616).

        Base devuelve 'N'; el override de moneda extranjera lo computa según
        la política de la empresa y la moneda del comprobante.
        """
        self.ensure_one()
        return "N"

    # --------------------------------------------------------------
    # Emisión
    # --------------------------------------------------------------
    def action_l10n_ar_request_cae(self):
        """Botón manual: solicita CAE para los moves seleccionados."""
        for move in self:
            move._l10n_ar_request_cae()

    def action_l10n_ar_retry_post_cae(self):
        """Botón "Reintentar CAE AFIP" para facturas que quedaron
        ``posted`` SIN CAE.

        Caso típico (Luis Heredia / S00005): la factura se generó
        desde el flujo de adhesión automática, AFIP no la autorizó
        (típicamente por ``l10n_latam_document_type_id`` faltante o
        partner sin categoría fiscal), y quedó en estado fantasma:
        ``state=posted``, ``name=False``, ``l10n_ar_afip_auth_code=False``.

        Estrategia: pasar a draft, garantizar que tenga doc type (lo
        recalcula el compute si ahora hay categoría fiscal en el
        partner), y reposterar — lo cual dispara el flow normal de
        emisión AFIP.

        Si tras el reintento AFIP sigue rechazando, queda en draft
        (gracias al fix del _post override): el operador ve el
        error en el chatter y puede investigar antes de reintentar
        otra vez.
        """
        for move in self:
            if move.l10n_ar_afip_auth_code:
                raise UserError(_(
                    "La factura %s ya tiene CAE %s. No corresponde "
                    "reintentar."
                ) % (move.display_name, move.l10n_ar_afip_auth_code))
            if move.state == 'posted':
                move.button_draft()
            move.action_post()
        return True

    def action_l10n_ar_validar_arca(self):
        """Wrapper UX para `action_post()` en facturas electrónicas.

        El override de `_post` ya emite el CAE automáticamente — este
        método existe solo para que el botón de la UI tenga un `name`
        distinto a `action_post` y no colisione con el botón estándar
        de Odoo en la misma vista (cuando hay dos `<button>` con el
        mismo `name`, el `position="attributes"` no discrimina bien).
        """
        return self.action_post()

    def _l10n_ar_request_cae(self):
        """Dispatcher CAE — decide si va por WSFEv1 o WSFEX según el journal."""
        self.ensure_one()

        if self.l10n_ar_afip_result == "A" and self.l10n_ar_afip_auth_code:
            raise UserError(_(
                "El comprobante %s ya tiene CAE %s. Si querés reenviar, "
                "consultá primero con AFIP (FECompConsultar)."
            ) % (self.name, self.l10n_ar_afip_auth_code))

        ws = self.journal_id._l10n_ar_afip_ws_for_emission
        if ws == "wsfe":
            return self._l10n_ar_request_cae_wsfe()
        if ws == "wsfex":
            return self._l10n_ar_request_cae_wsfex()
        raise UserError(_(
            "El diario %s no está configurado para emisión electrónica "
            "(sistema AFIP actual = %s; soportados: WSFEv1 (RLI_RLM) y "
            "WSFEXv1 (FEERCEL/FEERCELP))."
        ) % (
            self.journal_id.name,
            self.journal_id.l10n_ar_afip_pos_system or "∅",
        ))

    def _l10n_ar_request_cae_wsfe(self):
        """Flujo WSFEv1 — mercado interno.

        **Idempotencia** (incidente real en producción, 10/07/2026):
        el pedido de CAE viaja *dentro* de la transacción de Odoo. Si Postgres
        aborta esa transacción (``SERIALIZATION_FAILURE`` por concurrencia —
        típico con POS y ventas escribiendo a la vez), Odoo hace rollback y
        **re-ejecuta el RPC entero**, incluyendo este llamado. Pero AFIP no
        tiene rollback: el número ya quedó autorizado del lado de ellos, y el
        CAE que devolvió se perdió con la transacción. A partir de ahí Odoo
        insiste con un número que AFIP ya consumió y **toda** la numeración de
        ese (PV, tipo) queda trabada con 10016.

        Ningún dato que escribamos en la transacción abortada sobrevive, así
        que el único lugar donde queda constancia de que el CAE salió es AFIP.
        Por eso, ante los códigos de "número ya consumido" no abortamos:
        consultamos el comprobante con ``FECompConsultar`` y, si coincide con
        el que estábamos emitiendo, adoptamos ese CAE (ver
        ``_l10n_ar_afip_recuperar_cae``). Si no coincide, propagamos el error.
        """
        self.ensure_one()
        company = self.company_id
        environment = company.l10n_ar_afip_ws_environment or "testing"
        connection = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
            company, "wsfe", environment,
        )
        auth = connection.get_auth()

        fe_req = self._l10n_ar_get_wsfe_payload()
        det = (
            (fe_req.get("FeDetReq") or {}).get("FECAEDetRequest") or [{}]
        )[0]

        tr = ws_transport.build_transport(
            data_dir=_odoo_config.get("data_dir"),
        )
        try:
            response = ws_wsfe.cae_solicitar(
                auth=auth, fe_cae_req=fe_req,
                environment=environment, transport=tr,
            )
        except ws_errors.WsfeError as exc:
            if _wsfe_code(exc) in _WSFE_NRO_YA_CONSUMIDO and (
                self._l10n_ar_afip_recuperar_cae(
                    det, auth, environment,
                    motivo="AFIP [%s] %s" % (exc.code, exc.message),
                )
            ):
                # Recuperado: el comprobante ya estaba autorizado en AFIP y es
                # el nuestro. Queda posted con su CAE real.
                return
            raise
        finally:
            # siempre guardamos XML, incluso si hubo excepción
            self.l10n_ar_afip_xml_request = _decode_safe(tr.last_request)
            self.l10n_ar_afip_xml_response = _decode_safe(tr.last_response)

        self._l10n_ar_apply_cae_response(response)

    # --------------------------------------------------------------
    # Recuperación de CAE huérfano (idempotencia contra AFIP)
    # --------------------------------------------------------------
    def _l10n_ar_afip_recuperar_cae(self, det, auth, environment, motivo=""):
        """Consulta el comprobante en AFIP y adopta su CAE si es el nuestro.

        `det` es el dict ``FECAEDetRequest`` que le íbamos a mandar a AFIP.
        Comparamos contra lo que AFIP tiene registrado en ese número:
        fecha, importe total, y tipo/número de documento del receptor. Solo
        si **todo** coincide adoptamos el CAE — si difiere, ese número lo
        emitió otro sistema (portal AFIP, otra instancia) y hay que
        resolverlo a mano con el asistente de numeración del diario.

        :return: True si adoptamos un CAE existente, False si no hay nada
                 que recuperar (el caller re-levanta el error original).
        """
        self.ensure_one()
        cbte_nro = _int_or_none(det.get("CbteDesde"))
        if not cbte_nro:
            return False

        tr = ws_transport.build_transport(
            data_dir=_odoo_config.get("data_dir"),
        )
        try:
            remoto = ws_wsfe.comp_consultar(
                auth=auth,
                pto_vta=self.journal_id.l10n_ar_afip_pos_number,
                cbte_tipo=int(self.l10n_latam_document_type_id.code),
                cbte_nro=cbte_nro,
                environment=environment,
                transport=tr,
            )
        except Exception as exc:  # noqa: BLE001 -- si la consulta falla, no recuperamos
            _logger.warning(
                "AFIP recuperación CAE: falló FECompConsultar (PV %s tipo %s nro %s): %s",
                self.journal_id.l10n_ar_afip_pos_number,
                self.l10n_latam_document_type_id.code, cbte_nro, exc,
            )
            return False

        if not self._l10n_ar_afip_comprobante_coincide(remoto, det):
            _logger.warning(
                "AFIP recuperación CAE: el comprobante %s-%s nro %s que AFIP "
                "tiene autorizado NO coincide con el que íbamos a emitir "
                "(%s). No se adopta el CAE. Enviado=%s / AFIP=%s",
                self.journal_id.l10n_ar_afip_pos_number,
                self.l10n_latam_document_type_id.code, cbte_nro,
                self.display_name, det, remoto,
            )
            return False

        cae = str(remoto.get("CodAutorizacion") or "")
        vals = {
            "l10n_ar_afip_result": "A",
            "l10n_ar_afip_auth_mode": "CAE",
            "l10n_ar_afip_auth_code": cae,
        }
        vto = remoto.get("FchVto")
        if vto:
            vals["l10n_ar_afip_auth_code_due"] = datetime.strptime(
                str(vto), "%Y%m%d",
            ).date()
        self.write(vals)
        _logger.warning(
            "AFIP recuperación CAE: %s ya estaba autorizado en AFIP "
            "(CAE %s, vto %s). Adoptamos el CAE existente en lugar de pedir "
            "uno nuevo. Motivo: %s",
            self.display_name, cae, vto, motivo,
        )
        self.message_post(body=_(
            "<b>CAE recuperado de AFIP.</b><br/>"
            "Este comprobante ya figuraba autorizado en AFIP con el "
            "CAE <b>%(cae)s</b> (vto. %(vto)s), pero el CAE no había quedado "
            "registrado en Odoo — típicamente porque la transacción se "
            "reintentó después de que AFIP ya lo otorgara. Se adoptó el CAE "
            "existente en vez de pedir uno nuevo, evitando quemar el número "
            "y desincronizar la numeración.<br/>"
            "Respuesta de AFIP al reintento: %(motivo)s",
            cae=cae, vto=vto or "-", motivo=motivo or "-",
        ))
        return True

    def _l10n_ar_afip_comprobante_coincide(self, remoto, det):
        """True si el comprobante que AFIP ya tiene es el mismo que íbamos a emitir."""
        if not remoto:
            return False
        # Tiene que estar efectivamente autorizado con CAE.
        if not remoto.get("CodAutorizacion"):
            return False
        if (remoto.get("EmisionTipo") or "CAE") != "CAE":
            return False
        # Sin fecha no hay comparación posible — no adoptamos a ciegas.
        if not remoto.get("CbteFch") or not det.get("CbteFch"):
            return False
        pares = (
            (_int_or_none(remoto.get("CbteDesde")), _int_or_none(det.get("CbteDesde"))),
            (str(remoto.get("CbteFch") or ""), str(det.get("CbteFch") or "")),
            (_round2(remoto.get("ImpTotal")), _round2(det.get("ImpTotal"))),
            (_int_or_none(remoto.get("DocTipo")), _int_or_none(det.get("DocTipo"))),
            (_int_or_none(remoto.get("DocNro")), _int_or_none(det.get("DocNro"))),
        )
        return all(a is not None and a == b for a, b in pares)

    def _l10n_ar_request_cae_wsfex(self):
        """Flujo WSFEXv1 — Factura Electrónica de Exportación.

        Diferencias vs WSFEv1:
          1. Usa cache TA distinto (`wsfex`).
          2. Pide `FEXGetLast_ID` para incrementar el Id de transacción
             (independiente del Cbte_nro).
          3. Pide `FEXGetLast_CMP` para saber el siguiente Cbte_nro
             (community no rota la secuencia automática como hace para WSFE).
          4. Llama a `FEXAuthorize` con un payload distinto.
          5. La respuesta tiene otra estructura (`FEXResultAuth`).
        """
        self.ensure_one()

        # Pre-flight: si el journal no tiene default_account_id, Odoo va
        # a fallar al armar las account.move.line DESPUÉS de obtener el
        # CAE → quedaría un CAE huérfano en AFIP sin contraparte en
        # Odoo. Mejor abortar antes y levantar UserError clara.
        if not self.journal_id.default_account_id:
            raise UserError(_(
                "El diario %s no tiene 'Cuenta predeterminada' configurada. "
                "Si emitimos sin esto, AFIP va a tomar el CAE pero Odoo no "
                "va a poder asentar el comprobante (queda CAE huérfano). "
                "Configurá la cuenta contable en el journal antes de emitir."
            ) % self.journal_id.name)

        company = self.company_id
        environment = company.l10n_ar_afip_ws_environment or "testing"
        connection = self.env["l10n_ar.afip.ws.connection"]._get_or_create(
            company, "wsfex", environment,
        )
        auth = connection.get_auth()

        # 1) Construir el payload (necesita last_id y cbte_nro).
        tr = ws_transport.build_transport(
            data_dir=_odoo_config.get("data_dir"),
        )
        try:
            last_id = ws_wsfex.get_last_id(
                auth=auth, environment=environment, transport=tr,
            )
            last_cmp = ws_wsfex.get_last_cmp(
                auth=auth,
                pto_vta=self.journal_id.l10n_ar_afip_pos_number,
                cbte_tipo=int(self.l10n_latam_document_type_id.code or 0),
                environment=environment, transport=tr,
            )
            next_cbte_nro = int((last_cmp or {}).get("cbte_nro") or 0) + 1

            cmp_dict = self._l10n_ar_get_wsfex_payload(last_id, next_cbte_nro)

            # 2) Emitir.
            response = ws_wsfex.authorize(
                auth=auth, cmp_dict=cmp_dict,
                environment=environment, transport=tr,
            )
        finally:
            self.l10n_ar_afip_xml_request = _decode_safe(tr.last_request)
            self.l10n_ar_afip_xml_response = _decode_safe(tr.last_response)

        self._l10n_ar_apply_fex_response(response, next_cbte_nro)

    def _l10n_ar_apply_fex_response(self, response, expected_cbte_nro):
        """Vuelca el dict devuelto por `wsfex.authorize` al move."""
        self.ensure_one()
        resultado = response.get("resultado")  # 'A' o 'R'
        cae = response.get("cae")
        fch_vto_raw = response.get("cae_fecha_vto")
        cbte_nro = response.get("cbte_nro")
        motivos = response.get("motivos_obs") or ""
        reproc = response.get("reproceso") or ""

        vals = {"l10n_ar_afip_result": resultado}
        if resultado == "A":
            vals["l10n_ar_afip_auth_mode"] = "CAE"
            vals["l10n_ar_afip_auth_code"] = cae
            if fch_vto_raw:
                try:
                    vals["l10n_ar_afip_auth_code_due"] = datetime.strptime(
                        str(fch_vto_raw), "%Y%m%d"
                    ).date()
                except (TypeError, ValueError):
                    pass
            if cbte_nro and int(cbte_nro) != int(expected_cbte_nro):
                _logger.warning(
                    "WSFEX devolvió Cbte_nro=%s pero esperaba %s — posible reproceso",
                    cbte_nro, expected_cbte_nro,
                )
        elif resultado == "R":
            raise UserError(_(
                "AFIP rechazó la FA-E %s: %s"
            ) % (self.name, motivos or _("(sin detalle)")))

        if motivos:
            vals["l10n_ar_afip_observations"] = str(motivos)
        if reproc and reproc != "N":
            vals["l10n_ar_afip_observations"] = (
                (vals.get("l10n_ar_afip_observations") or "")
                + " [Reproceso=%s]" % reproc
            )
        self.write(vals)

    def _l10n_ar_get_wsfex_payload(self, last_id, cbte_nro):
        """Mapea este account.move al dict que espera `wsfex.authorize`.

        Reusa la lib pura `payload_fex.build_fex_request` — toda la lógica
        de redondeos/formato vive ahí. Acá solo hacemos el extract de
        Odoo → dict denormalizado.
        """
        self.ensure_one()
        partner = self.commercial_partner_id
        country = partner.country_id

        if not country:
            raise UserError(_(
                "El cliente '%s' no tiene país configurado — WSFEX lo exige."
            ) % partner.name)
        country_afip_code = getattr(country, "l10n_ar_afip_code", None)
        if not country_afip_code:
            raise UserError(_(
                "El país '%s' no tiene `l10n_ar_afip_code`. "
                "Cargalo desde Configuración → Países, o instalá l10n_ar."
            ) % country.name)

        # Cuit_pais_cliente: legal entity vs natural según is_company.
        if partner.is_company:
            cuit_pais = getattr(country, "l10n_ar_legal_entity_vat", None) or 0
        else:
            cuit_pais = getattr(country, "l10n_ar_natural_vat", None) or 0

        # Moneda y cotización.
        cur = self.currency_id
        company_cur = self.company_id.currency_id
        moneda_id = getattr(cur, "l10n_ar_afip_code", None) or "PES"
        if cur == company_cur:
            moneda_ctz = 1.0
        else:
            inv_rate = self.invoice_currency_rate or 1.0
            moneda_ctz = (1.0 / inv_rate) if inv_rate else 1.0

        # Items: uno por línea de factura (no agrupamos).
        # En Odoo 19 `display_type` puede ser 'product' (líneas reales),
        # 'line_section' o 'line_note'. Solo saltamos secciones y notas.
        items = []
        for line in self.invoice_line_ids:
            if line.display_type in ("line_section", "line_note"):
                continue
            uom_code = getattr(line.product_uom_id, "l10n_ar_afip_code", None) or "7"
            try:
                uom_int = int(uom_code)
            except (TypeError, ValueError):
                uom_int = 7
            items.append({
                "Pro_codigo": (line.product_id.default_code or "")[:50],
                "Pro_ds": (line.name or line.product_id.display_name or "")[:4000],
                "Pro_qty": line.quantity or 0,
                "Pro_umed": uom_int,
                "Pro_precio_uni": line.price_unit or 0,
                "Pro_total_item": line.price_subtotal or 0,
                "Pro_bonificacion": 0,  # community no separa el descuento por linea
            })

        if not items:
            raise UserError(_(
                "La factura %s no tiene líneas de producto — WSFEX exige "
                "al menos un ítem."
            ) % self.name)

        # Domicilio cliente combinado.
        domicilio_parts = [
            partner.name or "",
            partner.street or "",
            partner.street2 or "",
            partner.zip or "",
            partner.city or "",
        ]
        domicilio_cliente = " - ".join([p for p in domicilio_parts if p])

        # Tipo expo: usar concepto AFIP si está, sino default Productos.
        tipo_expo_str = self.l10n_ar_afip_concept or "1"
        try:
            tipo_expo = int(tipo_expo_str)
        except (TypeError, ValueError):
            tipo_expo = 1
        if tipo_expo not in (1, 2, 4):
            tipo_expo = 1  # AFIP solo acepta 1/2/4 en Tipo_expo

        cbte_tipo_int = int(self.l10n_latam_document_type_id.code or 19)

        # Cmps_asoc para NC/ND (refund).
        cmps_asoc = None
        if cbte_tipo_int in (20, 21):
            ref = self._l10n_ar_get_wsfex_related_invoice()
            if ref:
                cmps_asoc = [ref]

        # Incoterm — community lo trae como `account.incoterm`.
        incoterm = self.invoice_incoterm_id if hasattr(self, "invoice_incoterm_id") else None
        incoterms_code = (incoterm.code if incoterm else None) or None
        incoterms_ds = (incoterm.name if incoterm else None) or None

        # Forma de pago — usamos el name del payment_term si está.
        forma_pago = self.invoice_payment_term_id.name if self.invoice_payment_term_id else None

        return payload_fex_lib.build_fex_request(
            last_id=last_id,
            fecha_cbte=self.invoice_date,
            cbte_tipo=cbte_tipo_int,
            pto_vta=self.journal_id.l10n_ar_afip_pos_number,
            cbte_nro=cbte_nro,
            tipo_expo=tipo_expo,
            dst_cmp=country_afip_code,
            cliente=partner.name or "",
            domicilio_cliente=domicilio_cliente,
            id_impositivo=partner.vat or "",
            cuit_pais_cliente=cuit_pais,
            moneda_id=moneda_id,
            moneda_ctz=moneda_ctz,
            imp_total=self.amount_total,
            items=items,
            incoterms=incoterms_code,
            incoterms_ds=incoterms_ds,
            forma_pago=forma_pago,
            obs_comerciales=forma_pago,  # mismo string en ambos
            fecha_pago=self.invoice_date_due,
            cmps_asoc=cmps_asoc,
        )

    def _l10n_ar_get_wsfex_related_invoice(self):
        """Para NC/ND-E, devuelve el dict Cmp_asoc apuntando a la FA-E original."""
        self.ensure_one()
        # community guarda la relación en `reversed_entry_id` (refunds) o en
        # `debit_origin_id` (debits).
        related = self.reversed_entry_id or getattr(self, "debit_origin_id", False)
        if not related:
            return None
        try:
            related_cbte_tipo = int(related.l10n_latam_document_type_id.code or 0)
        except (TypeError, ValueError):
            return None
        # Cbte_nro: parsear de l10n_latam_document_number "01234-00000001" → 1.
        s = related.l10n_latam_document_number or ""
        nro = 0
        if "-" in s:
            try:
                nro = int(s.split("-")[-1])
            except (ValueError, IndexError):
                pass
        if not nro:
            return None
        return payload_fex_lib.build_cmp_asoc(
            cbte_tipo=related_cbte_tipo,
            pto_vta=related.journal_id.l10n_ar_afip_pos_number or 0,
            cbte_nro=nro,
            cuit_emisor=(related.company_id.partner_id.vat or "").replace("-", "").strip() or "0",
        )

    def _l10n_ar_apply_cae_response(self, response):
        """Vuelca el dict devuelto por `wsfe.cae_solicitar` al move."""
        self.ensure_one()
        detalle = response.get("detalle") or []
        if not detalle:
            raise UserError(_(
                "AFIP no devolvió detalle en la respuesta. XML crudo en el "
                "campo l10n_ar_afip_xml_response."
            ))
        det = detalle[0]
        resultado = det.get("Resultado")  # 'A', 'R', 'O'
        cae = det.get("CAE")
        fecha_vto_raw = det.get("CAEFchVto")  # YYYYMMDD como str
        observaciones = det.get("Observaciones")

        vals = {"l10n_ar_afip_result": resultado}
        if resultado == "A":
            vals["l10n_ar_afip_auth_mode"] = "CAE"
            vals["l10n_ar_afip_auth_code"] = cae
            if fecha_vto_raw:
                vals["l10n_ar_afip_auth_code_due"] = datetime.strptime(
                    str(fecha_vto_raw), "%Y%m%d"
                ).date()
        elif resultado == "R":
            # Rechazado — re-levantamos con el mensaje para que el usuario
            # sepa qué corregir. No dejamos el move en estado "posted con
            # auth_code vacío", cancelamos lo escrito al move.
            msg = _extract_observations_text(observaciones) or _("(sin detalle)")
            raise UserError(_(
                "AFIP rechazó el comprobante %s: %s"
            ) % (self.name, msg))
        # resultado == 'O' → observado: CAE igual se devuelve pero con
        # advertencias. Guardamos los mensajes y seguimos.

        if observaciones:
            vals["l10n_ar_afip_observations"] = _extract_observations_text(observaciones)

        self.write(vals)

    # --------------------------------------------------------------
    # Hook en _post
    # --------------------------------------------------------------
    def _post(self, soft=True):
        """Después del post estándar, dispara CAE si el journal es electrónico.

        Si AFIP rechaza el CAE, **revertimos el post** dejando el move
        en estado 'draft' otra vez. Antes (en el MVP) el move quedaba
        'posted' sin CAE — un estado inconsistente que confundía al
        operador (la factura aparecía "Registrada" sin tener validez
        fiscal). Ahora el flujo es atómico: o queda posted+CAE, o
        vuelve a draft con error claro para que el operador corrija
        y reintente.

        Errores comunes que recuperamos:
          * AFIP [10016] Número de comprobante no es el próximo a
            autorizar (típico cuando se rolled-back un intento previo
            y AFIP ya consumió el número).
          * AFIP [10018] CAE vencido.
          * AFIP [10016] Cuit invalido / no autorizado.
          * AFIP errores de red / timeout.
        """
        posted = super(
            AccountMove, self.with_context(**{_CTX_SYNC: True})
        )._post(soft=soft)
        moves_to_revert = self.env["account.move"]
        errors = []
        for move in posted:
            if move.country_code != "AR":
                continue
            if move.move_type not in ("out_invoice", "out_refund", "out_receipt"):
                continue
            if not move.journal_id._l10n_ar_afip_ws_for_emission:
                # Journal no es electrónico (WSFE/WSFEX) — no disparar CAE.
                continue
            if move.l10n_ar_afip_auth_code:
                # Ya tiene CAE (p.ej. consulta previa al post).
                continue
            try:
                move._l10n_ar_request_cae()
                # AFIP ya otorgo el CAE. Lo persistimos ACA, antes de seguir
                # con el resto de la transaccion. Si el worker se muere despues
                # (limit_time_real), si hay un lock, o si el RPC se reintenta,
                # el CAE ya esta en Odoo y el numero no se pierde. Sin esto, el
                # rollback deja el numero consumido en AFIP y perdido aca, que
                # es exactamente lo que trabo la facturacion el 07/08 y el
                # 11/08 de 2026. Es el mismo criterio que usa Odoo Enterprise.
                if not (self.env.context.get("l10n_ar_skip_cae_commit")
                        or _l10n_ar_in_test_mode()):
                    self.env.cr.commit()
            except UserError as exc:
                moves_to_revert |= move
                errors.append((move.display_name or str(move.id), str(exc)))
                _logger.warning(
                    "AFIP rechazó CAE para %s — reverting to draft. %s",
                    move.display_name, exc,
                )
            except Exception as exc:  # noqa: BLE001
                moves_to_revert |= move
                errors.append((move.display_name or str(move.id), str(exc)))
                _logger.exception(
                    "Error inesperado pidiendo CAE para %s — reverting to draft.",
                    move.display_name,
                )

        if moves_to_revert:
            # Revertimos los moves que no consiguieron CAE: button_draft()
            # resetea state=draft, libera asientos contables y devuelve
            # el número al pool de la secuencia. AFIP no autorizó nada,
            # así que no hay nada que perder.
            for m in moves_to_revert:
                try:
                    m.button_draft()
                except Exception:  # noqa: BLE001
                    _logger.exception(
                        "No se pudo revertir a borrador %s — quedará "
                        "posted sin CAE (estado inconsistente).",
                        m.display_name,
                    )

            # Construir un UserError consolidado para que el operador
            # vea TODOS los errores juntos (típico: misma causa en
            # varias facturas batched).
            msgs = "\n\n".join("• %s\n%s" % (n, e) for n, e in errors)
            raise UserError(_(
                "AFIP rechazó el CAE para %(count)d factura(s). "
                "Las revertimos al estado borrador para que puedas "
                "corregir y reintentar:\n\n%(msgs)s",
                count=len(moves_to_revert),
                msgs=msgs,
            ))
        # Trixocom: limpiar overrides de sincronizacion ya consumidos (el
        # comprobante que tomo AFIP+1 ya tiene CAE). Idempotente.
        for _mv in posted:
            if (_mv.country_code == 'AR' and _mv.l10n_ar_afip_auth_code
                    and _mv.l10n_latam_document_type_id
                    and _mv.journal_id.l10n_ar_afip_seq_override):
                _mv.journal_id._l10n_ar_afip_clear_override(
                    _mv.l10n_latam_document_type_id.code)
        return posted


    def _get_last_sequence(self, relaxed=False, with_prefix=None):
        # Trixocom: sincroniza el arranque de la secuencia WSFEv1 con AFIP.
        # Hook en _get_last_sequence (no _get_starting_sequence): para una
        # secuencia nueva el sequence_mixin fuerza seq=0 -> arrancaria en 1.
        # Devolviendo el ultimo de AFIP como 'anterior', el motor nativo
        # numera AFIP+1. Numero fijado por el sistema, nunca por el usuario.
        result = super()._get_last_sequence(relaxed=relaxed, with_prefix=with_prefix)
        # Trixocom: override tecnico de resincronizacion (asistente de
        # numeracion AFIP en el diario). Si el tecnico fijo un ultimo-AFIP
        # para este (diario, tipo), forzamos numerar AFIP+1 aun a mitad de
        # secuencia. Sin override -> comportamiento identico (cero cambios).
        if (with_prefix is None and self.env.context.get(_CTX_SYNC)
                and self.country_code == 'AR' and self.l10n_latam_document_type_id
                and self.journal_id._l10n_ar_afip_ws_for_emission == 'wsfe'):
            _ovr = self.journal_id._l10n_ar_afip_get_override(
                self.l10n_latam_document_type_id.code)
            if _ovr is not None:
                return self._get_formatted_sequence(number=int(_ovr))
        if result or with_prefix is not None:
            return result
        if not self.env.context.get(_CTX_SYNC):
            return result
        if self.country_code != 'AR' or not self.l10n_latam_use_documents:
            return result
        if not self.l10n_latam_document_type_id:
            return result
        # _l10n_ar_afip_ws_for_emission es un @property -> sin parentesis.
        if self.journal_id._l10n_ar_afip_ws_for_emission != 'wsfe':
            return result
        try:
            last_nro = self._l10n_ar_get_afip_last_authorized()
        except Exception as exc:  # noqa: BLE001 -- no romper el posteo por caida de WS
            _logger.warning(
                'l10n_ar_trx_edi seq-sync: fallo FECompUltimoAutorizado %s (PV %s tipo %s): %s',
                self.display_name, self.journal_id.l10n_ar_afip_pos_number,
                self.l10n_latam_document_type_id.code, exc)
            return result
        if not last_nro:
            return result
        _logger.info(
            'l10n_ar_trx_edi seq-sync: %s (PV %s tipo %s) arranca desde AFIP %s -> proximo %s',
            self.display_name, self.journal_id.l10n_ar_afip_pos_number,
            self.l10n_latam_document_type_id.code, last_nro, int(last_nro) + 1)
        return self._get_formatted_sequence(number=int(last_nro))

    def _l10n_ar_get_afip_last_authorized(self):
        # FECompUltimoAutorizado(PV, tipo) -> ultimo Nro autorizado (int) o 0.
        self.ensure_one()
        company = self.company_id
        environment = company.l10n_ar_afip_ws_environment or 'testing'
        connection = self.env['l10n_ar.afip.ws.connection']._get_or_create(
            company, 'wsfe', environment)
        auth = connection.get_auth()
        transport = ws_transport.CapturingTransport(
            session=ws_transport.build_afip_session(), timeout=60)
        res = ws_wsfe.comp_ultimo_autorizado(
            auth=auth, pto_vta=self.journal_id.l10n_ar_afip_pos_number,
            cbte_tipo=int(self.l10n_latam_document_type_id.code),
            environment=environment, transport=transport)
        return int((res or {}).get('cbte_nro') or 0)

def _wsfe_code(exc):
    """Código numérico de un WsfeError, o None si no es numérico ('fault', ...)."""
    return _int_or_none(getattr(exc, "code", None))


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _round2(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _decode_safe(maybe_bytes):
    if maybe_bytes is None:
        return False
    if isinstance(maybe_bytes, bytes):
        try:
            return maybe_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return maybe_bytes.decode("latin-1", errors="replace")
    return str(maybe_bytes)


def _extract_observations_text(obs):
    """Serializa el nodo `Observaciones` de WSFE a texto plano."""
    if not obs:
        return ""
    # zeep nos lo da como objeto con atributo Obs (lista de {Code, Msg}).
    items = getattr(obs, "Obs", None)
    if items is None and isinstance(obs, dict):
        items = obs.get("Obs")
    if not items:
        return str(obs)
    lines = []
    for it in items:
        code = getattr(it, "Code", None) or (it.get("Code") if isinstance(it, dict) else None)
        msg = getattr(it, "Msg", None) or (it.get("Msg") if isinstance(it, dict) else None)
        lines.append("[%s] %s" % (code, msg))
    return "\n".join(lines)
