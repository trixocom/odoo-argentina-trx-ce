# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Construcción del payload `FEXAuthorize` para WSFEXv1 (Factura E exportación).

**Diseño puro** — no importa `odoo.`. Recibe dicts denormalizados y
devuelve el dict que `l10n_ar_afip_ws.lib.wsfex.authorize` pasa a AFIP.

Estructura del payload (resumen, ver
``odoo/enterprise/l10n_ar_edi.account_move.wsfex_get_cae_request`` y la
spec oficial AFIP COMPG v2.5):

.. code-block:: python

    {
        "Id": last_id + 1,                          # secuencia interna del CUIT
        "Fecha_cbte": "20260427",                   # YYYYMMDD
        "Cbte_Tipo": "19",                          # 19=FA-E, 20=ND-E, 21=NC-E
        "Punto_vta": 1234,
        "Cbte_nro": 1,
        "Tipo_expo": 1,                             # 1=Productos, 2=Servicios, 4=Otros
        "permisos": None,                           # array de Permisos[] o None
        "Dst_cmp": 203,                             # código país AFIP (numérico)
        "Cliente": "Foreign Inc.",
        "Domicilio_cliente": "1 Main St - 10001 - New York",
        "Id_impositivo": "EIN-12-3456789",          # tax id del comprador
        "Cuit_pais_cliente": 50000000059,           # CUIT país (legal entity / natural)
        "Moneda_Id": "DOL",
        "Moneda_ctz": "1399.500000",                # 6 decimales
        "Obs_comerciales": "30 días" or None,
        "Imp_total": "100.00",
        "Obs": "" or "comentarios",
        "Forma_pago": "Wire transfer" or None,
        "Idioma_cbte": 1,                           # 1=ES, 2=EN, 3=PT
        "Incoterms": "FOB" or None,
        "Incoterms_Ds": "Free On Board" or None,
        "Permiso_existente": "N" or "S" or "",      # solo si Cbte_Tipo=19 y Tipo_expo=1
        "Items": [
            {
                "Pro_codigo": "SKU001",
                "Pro_ds": "Product A",
                "Pro_qty": "1.00",
                "Pro_umed": 7,                       # 7=unidad, 1=kg, etc.
                "Pro_precio_uni": "100.00",
                "Pro_total_item": "100.00",
                "Pro_bonificacion": "0.00",
            },
        ],
        "Cmps_asoc": [...] or None,                  # solo NC/ND
        "Fecha_pago": "20260527",                    # opcional
        "CanMisMonExt": "S" or "N" or None,          # solo si Moneda != PES
    }

El cliente (``wsfex.authorize``) convierte ``Items``, ``permisos`` y
``Cmps_asoc`` a los tipos zeep correctos antes del envío.
"""
from decimal import ROUND_HALF_UP, Decimal


# Tipos de comprobante WSFEX.
CBTE_TIPO_FA_E = 19    # Factura de Exportación
CBTE_TIPO_ND_E = 20    # Nota de Débito Exportación
CBTE_TIPO_NC_E = 21    # Nota de Crédito Exportación

# Tipo de exportación (Tipo_expo).
TIPO_EXPO_PRODUCTOS = 1
TIPO_EXPO_SERVICIOS = 2
TIPO_EXPO_OTROS = 4

# Idiomas comprobante.
IDIOMA_ES = 1
IDIOMA_EN = 2
IDIOMA_PT = 3


def _q2(v):
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q6(v):
    return Decimal(str(v or 0)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _amount_str(v):
    """Devuelve el número formateado como string con 2 decimales y punto."""
    return str(_q2(v))


def _ctz_str(v):
    """Cotización con 6 decimales."""
    return str(_q6(v))


def build_fex_request(
    *,
    last_id,
    fecha_cbte,
    cbte_tipo,
    pto_vta,
    cbte_nro,
    tipo_expo,
    dst_cmp,
    cliente,
    domicilio_cliente,
    id_impositivo,
    cuit_pais_cliente,
    moneda_id,
    moneda_ctz,
    imp_total,
    items,
    idioma_cbte=IDIOMA_ES,
    incoterms=None,
    incoterms_ds=None,
    permiso_existente=None,
    obs_comerciales=None,
    obs=None,
    forma_pago=None,
    fecha_pago=None,
    cmps_asoc=None,
    permisos=None,
    can_mis_mon_ext=None,
):
    """Arma el dict listo para `wsfex.authorize`.

    Parámetros (todos requeridos salvo los que tengan default):

    :param last_id: int — último Id de la secuencia (next = last_id+1).
    :param fecha_cbte: str ``YYYYMMDD`` o ``date``.
    :param cbte_tipo: int 19/20/21.
    :param pto_vta: int.
    :param cbte_nro: int — siguiente nro de comprobante (de FEXGetLast_CMP+1).
    :param tipo_expo: int 1/2/4.
    :param dst_cmp: int — código país AFIP (de FEXGetPARAM_DST_pais).
    :param cliente: str — razón social del cliente exterior.
    :param domicilio_cliente: str — domicilio combinado.
    :param id_impositivo: str — tax id del cliente (EIN/RUC/etc.) o "".
    :param cuit_pais_cliente: int — CUIT país (legal entity / natural).
    :param moneda_id: str — 'DOL', 'EUR', '060' (USD billete), etc.
    :param moneda_ctz: float/Decimal — 1 / invoice_currency_rate (6 decimales).
    :param imp_total: float/Decimal — total del cbte (con 2 decimales).
    :param items: list de dicts con Pro_codigo, Pro_ds, Pro_qty, Pro_umed,
                  Pro_precio_uni, Pro_total_item, Pro_bonificacion.
    :param idioma_cbte: int 1/2/3, default 1 (español).
    :param incoterms: str de 3 chars (FOB/CIF/EXW/...) o None.
    :param incoterms_ds: descripción del Incoterm (max 20 chars) o None.
    :param permiso_existente: 'S'/'N'/None. Auto-default 'N' si Cbte_Tipo=19 y
        Tipo_expo=1 (Productos), '' en otros casos. Pasar string vacío para
        forzar.
    :param obs_comerciales: str o None.
    :param obs: str o None.
    :param forma_pago: str o None.
    :param fecha_pago: ``YYYYMMDD`` o None. Solo aplica si Cbte_Tipo=19 y
        Tipo_expo in (2, 4).
    :param cmps_asoc: list de dicts (NC/ND) o None.
    :param permisos: list de dicts o None.
    :param can_mis_mon_ext: 'S'/'N'/None. AFIP exige este flag si
        moneda_id != 'PES' y Cbte_Tipo=19 (cancela en moneda extranjera?).
    :return: dict con keys exactas para FEXAuthorize.
    """
    if not items:
        raise ValueError("WSFEX exige al menos 1 ítem")

    if hasattr(fecha_cbte, "strftime"):
        fecha_cbte = fecha_cbte.strftime("%Y%m%d")
    if fecha_pago is not None and hasattr(fecha_pago, "strftime"):
        fecha_pago = fecha_pago.strftime("%Y%m%d")

    cbte_tipo_int = int(cbte_tipo)

    # Default permiso_existente.
    if permiso_existente is None:
        if cbte_tipo_int == CBTE_TIPO_FA_E and int(tipo_expo) == TIPO_EXPO_PRODUCTOS:
            permiso_existente = "N"
        else:
            permiso_existente = ""

    # Normalizar items.
    norm_items = [_normalize_item(it) for it in items]

    # Orden y casing de keys según el WSDL prod de AFIP (signature de
    # `ClsFEXRequest` que devolvió zeep al fallar):
    #   Id, Fecha_cbte, Cbte_Tipo, Punto_vta, Cbte_nro, Tipo_expo,
    #   Permiso_existente, Permisos, Dst_cmp, Cliente, Cuit_pais_cliente,
    #   Domicilio_cliente, Id_impositivo, Moneda_Id, Moneda_ctz,
    #   CanMisMonExt, Obs_comerciales, Imp_total, Obs, Cmps_asoc,
    #   Forma_pago, Incoterms, Incoterms_Ds, Idioma_cbte, Items,
    #   Opcionales, Fecha_pago, Actividades.
    # Notar `Permisos` con P mayúscula (enterprise tiene un typo histórico
    # `permisos` minúscula que probablemente funcionaba en un WSDL viejo).
    res = {
        "Id": int(last_id) + 1,
        "Fecha_cbte": fecha_cbte,
        "Cbte_Tipo": str(cbte_tipo_int),
        "Punto_vta": int(pto_vta),
        "Cbte_nro": int(cbte_nro),
        "Tipo_expo": int(tipo_expo),
        "Permiso_existente": permiso_existente,
        "Permisos": permisos,
        "Dst_cmp": int(dst_cmp),
        "Cliente": (cliente or "")[:200],
        "Cuit_pais_cliente": int(cuit_pais_cliente or 0),
        "Domicilio_cliente": (domicilio_cliente or "")[:300],
        "Id_impositivo": (id_impositivo or "")[:50],
        "Moneda_Id": moneda_id,
        "Moneda_ctz": _ctz_str(moneda_ctz),
        "Obs_comerciales": obs_comerciales,
        "Imp_total": _amount_str(imp_total),
        "Obs": obs or "",
        "Cmps_asoc": cmps_asoc,
        "Forma_pago": forma_pago,
        "Incoterms": incoterms,
        "Incoterms_Ds": (incoterms_ds[:20] if incoterms_ds else None),
        "Idioma_cbte": int(idioma_cbte or IDIOMA_ES),
        "Items": norm_items,
    }

    # Fecha_pago: solo si aplica.
    if fecha_pago and cbte_tipo_int == CBTE_TIPO_FA_E and int(tipo_expo) in (TIPO_EXPO_SERVICIOS, TIPO_EXPO_OTROS):
        res["Fecha_pago"] = fecha_pago

    # CanMisMonExt: solo si moneda != PES y Cbte_Tipo == 19.
    if moneda_id and moneda_id != "PES" and cbte_tipo_int == CBTE_TIPO_FA_E and can_mis_mon_ext:
        res["CanMisMonExt"] = can_mis_mon_ext

    return res


def _normalize_item(it):
    """Devuelve un dict con las 7 keys que AFIP espera, formateadas."""
    pro_codigo = (it.get("Pro_codigo") or "")[:50]
    pro_ds = (it.get("Pro_ds") or "")[:4000]
    pro_qty = it.get("Pro_qty")
    pro_umed = it.get("Pro_umed")
    pro_precio_uni = it.get("Pro_precio_uni")
    pro_total_item = it.get("Pro_total_item")
    pro_bonificacion = it.get("Pro_bonificacion") or 0.0

    # Items "especiales" con Pro_umed in ('97','99','00') van con qty=0 y precio=0.
    pro_umed_str = str(pro_umed) if pro_umed is not None else "7"
    if pro_umed_str in ("97", "99", "00"):
        pro_qty = 0
        pro_precio_uni = 0

    return {
        "Pro_codigo": pro_codigo,
        "Pro_ds": pro_ds,
        "Pro_qty": str(_q2(pro_qty or 0)),
        "Pro_umed": int(pro_umed) if pro_umed is not None else 7,
        "Pro_precio_uni": _amount_str(pro_precio_uni or 0),
        "Pro_total_item": _amount_str(pro_total_item or 0),
        "Pro_bonificacion": _amount_str(pro_bonificacion or 0),
    }


def build_cmp_asoc(cbte_tipo, pto_vta, cbte_nro, cuit_emisor):
    """Helper para armar un Cmp_asoc para NC/ND-E refiriendo a una FA-E original.

    Estructura WSFEX: ``{Cbte_tipo, Cbte_punto_vta, Cbte_nro, Cbte_cuit}``.
    """
    return {
        "Cbte_tipo": int(cbte_tipo),
        "Cbte_punto_vta": int(pto_vta),
        "Cbte_nro": int(cbte_nro),
        "Cbte_cuit": str(cuit_emisor),
    }
