# Part of l10n-ar-edi-community. See LICENSE file for full copyright and licensing details.
"""Tests del mapeo a payload FECAERequest — capa pura.

Los casos que modelo son los que más probablemente se rompan:

1. Factura B simple con 21% (caso mainstream).
2. Factura A con IVA discriminado 21% + 10.5% (dos alícuotas → una misma AlicIva).
3. Dos items con alícuota 21% → se consolidan en una sola AlicIva.
4. Factura en USD con cotización.
5. Concepto Servicios → exige FchServDesde/Hasta/VtoPago, explota si faltan.
6. Código de alícuota no reconocido → ValueError.
"""
import datetime
import unittest

from ..lib import payload


class TestBuildFecaeRequest(unittest.TestCase):

    def _base(self, **overrides):
        """Arma un kwargs 'sensato' y permite override en cada test."""
        base = dict(
            pto_vta=1, cbte_tipo=6, concepto=1,
            cbte_fecha=datetime.date(2026, 4, 23),
            doc_tipo=99, doc_nro=0,
            cbte_nro=1,
            imp_neto=100, imp_iva=21,
            cond_iva_receptor_id=5,
            iva_items=[{"codigo": 5, "base": 100, "importe": 21}],
        )
        base.update(overrides)
        return base

    def test_factura_b_simple(self):
        req = payload.build_fecae_request(**self._base())
        self.assertEqual(req["FeCabReq"]["CantReg"], 1)
        self.assertEqual(req["FeCabReq"]["PtoVta"], 1)
        self.assertEqual(req["FeCabReq"]["CbteTipo"], 6)
        det = req["FeDetReq"]["FECAEDetRequest"][0]
        self.assertEqual(det["CbteDesde"], 1)
        self.assertEqual(det["CbteHasta"], 1)
        self.assertEqual(det["CbteFch"], "20260423")
        self.assertEqual(det["ImpNeto"], 100.0)
        self.assertEqual(det["ImpIVA"], 21.0)
        self.assertEqual(det["ImpTotal"], 121.0)
        self.assertEqual(det["CondicionIVAReceptorId"], 5)
        self.assertEqual(det["Iva"]["AlicIva"], [
            {"Id": 5, "BaseImp": 100.0, "Importe": 21.0},
        ])

    def test_multi_alicuota(self):
        req = payload.build_fecae_request(**self._base(
            imp_neto=200, imp_iva=31.5,
            iva_items=[
                {"codigo": 5, "base": 100, "importe": 21},
                {"codigo": 4, "base": 100, "importe": 10.5},
            ],
        ))
        alic = req["FeDetReq"]["FECAEDetRequest"][0]["Iva"]["AlicIva"]
        self.assertEqual(len(alic), 2)
        # se ordenan por Id asc
        self.assertEqual([a["Id"] for a in alic], [4, 5])

    def test_alicuotas_duplicadas_se_consolidan(self):
        req = payload.build_fecae_request(**self._base(
            imp_neto=200, imp_iva=42,
            iva_items=[
                {"codigo": 5, "base": 100, "importe": 21},
                {"codigo": 5, "base": 100, "importe": 21},
            ],
        ))
        alic = req["FeDetReq"]["FECAEDetRequest"][0]["Iva"]["AlicIva"]
        self.assertEqual(alic, [{"Id": 5, "BaseImp": 200.0, "Importe": 42.0}])

    def test_moneda_extranjera(self):
        req = payload.build_fecae_request(**self._base(
            mon_id="DOL", mon_cotiz=1000,
        ))
        det = req["FeDetReq"]["FECAEDetRequest"][0]
        self.assertEqual(det["MonId"], "DOL")
        self.assertEqual(det["MonCotiz"], 1000.0)

    def test_concepto_servicios_requiere_fechas(self):
        with self.assertRaises(ValueError):
            payload.build_fecae_request(**self._base(concepto=2))

    def test_concepto_servicios_con_fechas(self):
        req = payload.build_fecae_request(**self._base(
            concepto=2,
            fch_serv_desde=datetime.date(2026, 4, 1),
            fch_serv_hasta=datetime.date(2026, 4, 30),
            fch_vto_pago=datetime.date(2026, 5, 10),
        ))
        det = req["FeDetReq"]["FECAEDetRequest"][0]
        self.assertEqual(det["FchServDesde"], "20260401")
        self.assertEqual(det["FchServHasta"], "20260430")
        self.assertEqual(det["FchVtoPago"], "20260510")

    def test_alicuota_desconocida(self):
        with self.assertRaises(ValueError):
            payload.build_fecae_request(**self._base(
                iva_items=[{"codigo": 77, "base": 100, "importe": 21}],
            ))

    def test_round_half_up(self):
        # importe 0.125 → AFIP redondea a 0.13 (ROUND_HALF_UP).
        req = payload.build_fecae_request(**self._base(
            imp_neto="0.125", imp_iva=0,
            iva_items=[],
        ))
        det = req["FeDetReq"]["FECAEDetRequest"][0]
        self.assertEqual(det["ImpNeto"], 0.13)

    def test_imp_total_es_suma(self):
        # pasamos imp_trib y imp_op_ex distintos de cero; imp_total debe
        # ser la suma real, no lo que le dijimos.
        req = payload.build_fecae_request(**self._base(
            imp_neto=100, imp_iva=21, imp_trib=5, imp_op_ex=10, imp_tot_conc=0,
        ))
        det = req["FeDetReq"]["FECAEDetRequest"][0]
        self.assertEqual(det["ImpTotal"], 136.0)

    def test_cbtes_asoc(self):
        req = payload.build_fecae_request(**self._base(
            cbte_tipo=3,  # Nota de crédito A
            cbtes_asoc=[
                {"Tipo": 1, "PtoVta": 1, "Nro": 5, "Cuit": 20111111111},
            ],
        ))
        det = req["FeDetReq"]["FECAEDetRequest"][0]
        self.assertEqual(len(det["CbtesAsoc"]["CbteAsoc"]), 1)

    def test_sin_cond_iva_receptor_si_none(self):
        # En homologación, antes de RG 5616, el field era opcional. Validamos
        # que no se incluya si no se pasa, para no romper compat.
        req = payload.build_fecae_request(**self._base(cond_iva_receptor_id=None))
        det = req["FeDetReq"]["FECAEDetRequest"][0]
        self.assertNotIn("CondicionIVAReceptorId", det)


if __name__ == "__main__":
    unittest.main()
