# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Tests del generador de QR RG 4291 — capa pura, pytest."""
import datetime
import unittest

from ..lib import qr_code


class TestQrPayload(unittest.TestCase):

    def test_basic_payload(self):
        p = qr_code.build_qr_payload(
            cae="71234567890123",
            date=datetime.date(2026, 4, 23),
            cuit=20111111111,
            pto_vta=1,
            cbte_tipo=1,
            cbte_nro=123,
            importe="121.00",
            doc_tipo_receptor=80,
            doc_nro_receptor=30222222222,
        )
        self.assertEqual(p["ver"], 1)
        self.assertEqual(p["fecha"], "2026-04-23")
        self.assertEqual(p["cuit"], 20111111111)
        self.assertEqual(p["ptoVta"], 1)
        self.assertEqual(p["tipoCmp"], 1)
        self.assertEqual(p["nroCmp"], 123)
        self.assertEqual(p["importe"], 121.0)
        self.assertEqual(p["moneda"], "PES")
        self.assertEqual(p["ctz"], 1.0)
        self.assertEqual(p["tipoDocRec"], 80)
        self.assertEqual(p["nroDocRec"], 30222222222)
        self.assertEqual(p["tipoCodAut"], "E")
        self.assertEqual(p["codAut"], 71234567890123)

    def test_caea_auth_mode_maps_to_A(self):
        p = qr_code.build_qr_payload(
            cae=1, date="2026-01-01", cuit=1, pto_vta=1, cbte_tipo=1,
            cbte_nro=1, importe=0, auth_mode="CAEA",
        )
        self.assertEqual(p["tipoCodAut"], "A")

    def test_invalid_auth_mode(self):
        with self.assertRaises(ValueError):
            qr_code.build_qr_payload(
                cae=1, date="2026-01-01", cuit=1, pto_vta=1, cbte_tipo=1,
                cbte_nro=1, importe=0, auth_mode="CAI",
            )

    def test_consumidor_final_defaults(self):
        p = qr_code.build_qr_payload(
            cae=1, date="2026-04-23", cuit=20111111111, pto_vta=1,
            cbte_tipo=6, cbte_nro=1, importe=100,
        )
        self.assertEqual(p["tipoDocRec"], 99)
        self.assertEqual(p["nroDocRec"], 0)


class TestQrUrl(unittest.TestCase):

    def test_round_trip(self):
        """build_qr_url ↔ decode_qr_url debería ser identidad."""
        original = qr_code.build_qr_payload(
            cae="71234567890123",
            date=datetime.date(2026, 4, 23),
            cuit=20111111111,
            pto_vta=1,
            cbte_tipo=1,
            cbte_nro=123,
            importe=121.00,
        )
        url = qr_code.build_qr_url(original)
        decoded = qr_code.decode_qr_url(url)
        self.assertEqual(original, decoded)

    def test_url_uses_afip_base(self):
        url = qr_code.build_qr_url({"ver": 1})
        self.assertTrue(url.startswith("https://www.afip.gob.ar/fe/qr/?p="))

    def test_url_base64_is_urlsafe_no_padding(self):
        # Genero un payload más largo para aumentar probabilidad de +/= en b64.
        p = qr_code.build_qr_payload(
            cae="71234567890123",
            date="2026-04-23",
            cuit=20999999999,
            pto_vta=9999,
            cbte_tipo=1,
            cbte_nro=99999999,
            importe=9999999.99,
        )
        url = qr_code.build_qr_url(p)
        b64 = url.split("?p=", 1)[1]
        # url-safe: sin '+' ni '/'
        self.assertNotIn("+", b64)
        self.assertNotIn("/", b64)
        # sin padding
        self.assertFalse(b64.endswith("="))

    def test_decode_rejects_non_afip_url(self):
        with self.assertRaises(ValueError):
            qr_code.decode_qr_url("https://evil.example.com/?p=abc")


if __name__ == "__main__":
    unittest.main()
