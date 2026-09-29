# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Construcción del payload `FECAERequest` para WSFEv1.

**Diseño importante**: este módulo es puro — no importa `odoo.`. Recibe un
dict con los datos "denormalizados" del comprobante (ver `InvoiceData`
abajo) y devuelve el dict exacto que `l10n_ar_afip_ws.lib.wsfe.cae_solicitar`
pasa a AFIP.

La razón: el mapeo `account.move` → payload AFIP involucra bastantes reglas
de negocio (redondeos por alícuota, manejo de diferencia ARS/USD, condición
IVA receptor según RG 5616) que quiero poder testear sin levantar Odoo ni
mockear el ORM. Además, si mañana el payload lo arma otro caller (p.ej. un
import TXT), esta función se puede reutilizar.

Las reglas implementadas acá son las de **mercado interno (WSFEv1)**; el
payload de WSFEX tiene otros campos y va en su propio módulo cuando toque.
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

#: Códigos AFIP de alícuota de IVA → porcentaje. Valores fijos de la tabla oficial.
#: No se reciben como parámetro: si AFIP agrega una alícuota nueva, hay que editarlo
#: acá a propósito (no queremos que el cliente invente códigos).
AFIP_IVA_CODES = {
    3: Decimal("0"),     # 0%
    4: Decimal("10.5"),
    5: Decimal("21"),
    6: Decimal("27"),
    8: Decimal("5"),
    9: Decimal("2.5"),
}

#: Conceptos AFIP válidos para el WSFEv1.
CONCEPTO_PRODUCTOS = 1
CONCEPTO_SERVICIOS = 2
CONCEPTO_MIXTO = 3


def _q(v):
    """Redondea a 2 decimales con ROUND_HALF_UP (el modo que usa AFIP)."""
    return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_fecae_request(
    pto_vta,
    cbte_tipo,
    concepto,
    cbte_fecha,
    doc_tipo,
    doc_nro,
    cbte_nro,
    # Importes:
    imp_neto,
    imp_iva,
    imp_trib=0,
    imp_op_ex=0,
    imp_tot_conc=0,
    # Moneda:
    mon_id="PES",
    mon_cotiz=1,
    # Condición IVA receptor (RG 5616, obligatorio desde 2024):
    cond_iva_receptor_id=None,
    # Discriminación de IVA por alícuota:
    iva_items=None,
    # Opcionales:
    fch_serv_desde=None,
    fch_serv_hasta=None,
    fch_vto_pago=None,
    cbtes_asoc=None,
    tributos=None,
    opcionales=None,
    can_mis_mon_ext="N",
):
    """Arma el dict `FeCAEReq` exacto para `FECAESolicitar`.

    :param iva_items: lista de dicts {'codigo': int, 'base': Decimal, 'importe': Decimal}
                      con las alícuotas del comprobante. Las bases e importes
                      se redondean a 2 decimales acá. Se colapsan por código
                      si vienen duplicados (ej. dos líneas al 21% → una sola
                      entrada con base e importe sumados).
    :param cbtes_asoc: lista de dicts con los comprobantes asociados (obligatorio
                      para notas de crédito/débito).
    :param tributos: lista de dicts para Tributos (IIBB, impuestos municipales, etc.).
    :param opcionales: lista de dicts {'Id': int, 'Valor': str} para el bloque
                      `Opcionales`. Lo usan FCE MiPyME (Id=27 SCA/ADC, Id=2101 CBU,
                      Id=22 cancelación), FE-C tarjetas, etc. AFIP define el catálogo
                      vía `FEParamGetTiposOpcional`.

    :return: dict listo para mandar como `FeCAEReq` en `FECAESolicitar`.
             Estructura: {"FeCabReq": {...}, "FeDetReq": {"FECAEDetRequest": [{...}]}}.
    """
    if iva_items is None:
        iva_items = []

    # Validaciones de consistencia:
    if concepto in (CONCEPTO_SERVICIOS, CONCEPTO_MIXTO):
        if not (fch_serv_desde and fch_serv_hasta and fch_vto_pago):
            raise ValueError(
                "Para concepto Servicios/Mixto son obligatorias las fechas "
                "de servicio desde, hasta y vencimiento de pago."
            )

    # Consolidar alícuotas: si vienen 2 entradas con mismo código, las sumamos.
    ivas_consolidados = defaultdict(lambda: {"base": Decimal("0"), "importe": Decimal("0")})
    for item in iva_items:
        code = int(item["codigo"])
        if code not in AFIP_IVA_CODES:
            raise ValueError(
                "Código de alícuota AFIP %s no reconocido. Válidos: %s" %
                (code, sorted(AFIP_IVA_CODES))
            )
        ivas_consolidados[code]["base"] += Decimal(str(item["base"]))
        ivas_consolidados[code]["importe"] += Decimal(str(item["importe"]))

    alic_iva = [
        {
            "Id": code,
            "BaseImp": float(_q(values["base"])),
            "Importe": float(_q(values["importe"])),
        }
        for code, values in sorted(ivas_consolidados.items())
    ]

    # Totales. AFIP recalcula ImpTotal como suma de los demás, así que
    # es mejor que nosotros lo armemos igual en vez de pasarle el que
    # ya calculó Odoo (que podría diferir por redondeos).
    imp_neto_q = _q(imp_neto)
    imp_iva_q = _q(imp_iva)
    imp_trib_q = _q(imp_trib)
    imp_op_ex_q = _q(imp_op_ex)
    imp_tot_conc_q = _q(imp_tot_conc)
    imp_total = imp_neto_q + imp_iva_q + imp_trib_q + imp_op_ex_q + imp_tot_conc_q

    # Fecha en formato YYYYMMDD que AFIP exige.
    def _fecha_afip(d):
        if d is None:
            return None
        if hasattr(d, "strftime"):
            return d.strftime("%Y%m%d")
        # asumimos string ya en formato YYYY-MM-DD o YYYYMMDD
        s = str(d).replace("-", "")
        if len(s) != 8 or not s.isdigit():
            raise ValueError("Fecha inválida para AFIP: %r" % d)
        return s

    det = {
        "Concepto": int(concepto),
        "DocTipo": int(doc_tipo),
        "DocNro": int(doc_nro),
        "CbteDesde": int(cbte_nro),
        "CbteHasta": int(cbte_nro),
        "CbteFch": _fecha_afip(cbte_fecha),
        "ImpTotal": float(_q(imp_total)),
        "ImpTotConc": float(imp_tot_conc_q),
        "ImpNeto": float(imp_neto_q),
        "ImpOpEx": float(imp_op_ex_q),
        "ImpIVA": float(imp_iva_q),
        "ImpTrib": float(imp_trib_q),
        "MonId": mon_id,
        "MonCotiz": float(mon_cotiz),
        "CanMisMonExt": can_mis_mon_ext,
    }

    if cond_iva_receptor_id is not None:
        # RG 5616: campo opcional hasta 31/05/2026, OBLIGATORIO desde
        # 01/06/2026. El caller debería pasarlo siempre. Si vino None lo
        # omitimos del payload (compat con cuentas que todavía no migraron).
        # Cuando pase la deadline el helper de account.move siempre lo
        # devuelve con valor (default 5=CF), así que en práctica nunca
        # debería caer en None desde el flujo normal de Odoo.
        det["CondicionIVAReceptorId"] = int(cond_iva_receptor_id)

    if alic_iva:
        det["Iva"] = {"AlicIva": alic_iva}

    if concepto in (CONCEPTO_SERVICIOS, CONCEPTO_MIXTO):
        det["FchServDesde"] = _fecha_afip(fch_serv_desde)
        det["FchServHasta"] = _fecha_afip(fch_serv_hasta)
        det["FchVtoPago"] = _fecha_afip(fch_vto_pago)

    if cbtes_asoc:
        det["CbtesAsoc"] = {"CbteAsoc": list(cbtes_asoc)}
    if tributos:
        det["Tributos"] = {"Tributo": list(tributos)}
    if opcionales:
        # Cada opcional debe tener Id y Valor (ambos string en el WSDL).
        det["Opcionales"] = {
            "Opcional": [
                {"Id": str(o["Id"]), "Valor": str(o["Valor"])}
                for o in opcionales
            ]
        }

    return {
        "FeCabReq": {
            "CantReg": 1,
            "PtoVta": int(pto_vta),
            "CbteTipo": int(cbte_tipo),
        },
        "FeDetReq": {
            "FECAEDetRequest": [det],
        },
    }
