# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Generador de URL QR de comprobantes electrónicos — RG 4291/2018 de AFIP.

Especificación oficial: el QR impreso en la factura debe apuntar a

    https://www.afip.gob.ar/fe/qr/?p=<base64url(JSON)>

donde el JSON tiene un set fijo de campos. Cualquier consumidor (cliente o
fiscalizador AFIP) escanea el QR y cae a la página oficial de validación.

Referencias:
    - RG (AFIP) N° 4291/18 — Anexo I.
    - https://www.afip.gob.ar/fe/qr/

Importante: el encoding base es **base64 URL-safe sin padding** según la
RG. Si mandás base64 estándar con `+`/`/` y `=`, la web de AFIP no valida.
"""
import base64
import json

#: Base pública oficial de AFIP para el QR. No se debe cambiar (no hay
#: una URL distinta en homologación — el QR siempre apunta a la pública
#: porque incluso en tests queremos ver el flujo real del cliente).
AFIP_QR_BASE_URL = "https://www.afip.gob.ar/fe/qr/?p="


def build_qr_payload(
    cae,
    date,
    cuit,
    pto_vta,
    cbte_tipo,
    cbte_nro,
    importe,
    moneda="PES",
    cotizacion=1,
    doc_tipo_receptor=99,
    doc_nro_receptor=0,
    auth_mode="CAE",
):
    """Arma el dict que va dentro del base64 del QR.

    :param cae: string de 14 dígitos (CAE o CAEA). Se convierte a int.
    :param date: fecha del comprobante en formato 'YYYY-MM-DD' (str) o
                 un objeto con isoformat() (date/datetime).
    :param cuit: 11 dígitos del emisor. Str o int.
    :param pto_vta: int, punto de venta.
    :param cbte_tipo: int, código de tipo de comprobante AFIP (1=FA, 6=FB, ...).
    :param cbte_nro: int, número del comprobante.
    :param importe: Decimal/float/str con 2 decimales como máximo.
    :param moneda: código AFIP de moneda, 'PES' (peso), 'DOL' (USD), etc.
    :param cotizacion: cotización vs. peso. Para PES siempre 1.
    :param doc_tipo_receptor: int AFIP. Por defecto 99 = Consumidor Final sin ID.
    :param doc_nro_receptor: int. Si el receptor no tiene ID, poner 0.
    :param auth_mode: 'CAE' o 'CAEA'. RG dice 'E' para CAE, 'A' para CAEA.
    :return: dict listo para json.dumps.
    """
    # Normalizaciones:
    if hasattr(date, "isoformat"):
        fecha_str = date.isoformat()
    else:
        fecha_str = str(date)

    cuit_int = int(str(cuit).replace("-", ""))
    importe_float = round(float(importe), 2)
    cae_int = int(str(cae))
    cotizacion_float = round(float(cotizacion), 6)

    # La RG solo admite 'E' (CAE) o 'A' (CAEA) en tipoCodAut.
    if auth_mode == "CAE":
        tipo_cod_aut = "E"
    elif auth_mode == "CAEA":
        tipo_cod_aut = "A"
    else:
        raise ValueError(
            "auth_mode debe ser 'CAE' o 'CAEA', no %r" % auth_mode
        )

    return {
        "ver": 1,
        "fecha": fecha_str,
        "cuit": cuit_int,
        "ptoVta": int(pto_vta),
        "tipoCmp": int(cbte_tipo),
        "nroCmp": int(cbte_nro),
        "importe": importe_float,
        "moneda": moneda,
        "ctz": cotizacion_float,
        "tipoDocRec": int(doc_tipo_receptor),
        "nroDocRec": int(doc_nro_receptor),
        "tipoCodAut": tipo_cod_aut,
        "codAut": cae_int,
    }


def build_qr_url(payload):
    """Convierte el dict a URL final del QR.

    Usa base64 URL-safe SIN padding — crítico para que AFIP valide.
    """
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # base64 url-safe (reemplaza +/ por -_) SIN padding '='.
    b64 = base64.urlsafe_b64encode(serialized.encode("utf-8")).decode("ascii")
    b64 = b64.rstrip("=")
    return AFIP_QR_BASE_URL + b64


def decode_qr_url(url):
    """Inverso de `build_qr_url` — útil para tests.

    :raises ValueError: si la URL no empieza por la base de AFIP o el
                        payload no es JSON.
    """
    if not url.startswith(AFIP_QR_BASE_URL):
        raise ValueError("La URL no es de AFIP: %r" % url)
    b64 = url[len(AFIP_QR_BASE_URL):]
    # Re-agregar padding que le sacamos.
    pad = (-len(b64)) % 4
    b64_padded = b64 + ("=" * pad)
    raw = base64.urlsafe_b64decode(b64_padded.encode("ascii"))
    return json.loads(raw.decode("utf-8"))
