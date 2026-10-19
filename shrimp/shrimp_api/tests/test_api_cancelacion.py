"""Cancelación general desde la API (/api/v1, shrimp_api/controllers/private_delay.py):

- el detalle trae «cancellation» (campo nuevo, compatible);
- :cancel del vendedor (antes del despacho, motivo obligatorio, strike);
- :cancel-request / -accept / -reject / -withdraw (mutuo acuerdo);
- :switch-verifier solo con la verificación vencida.
"""
from datetime import timedelta

from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiCancelacion(ApiCase):

    def _compra(self, dias=5):
        Tx = self.env["shrimp.transaction"]
        hoy = Tx._shrimp_today()
        self.product.sudo().expected_delivery_date = hoy + timedelta(days=dias)
        tx = self.product.execute_purchase_flow(self.farm_a, 4.0)["transaction"]
        tx.sudo().write({"desired_date": hoy + timedelta(days=dias)})
        return tx

    def test_vendedor_cancela(self):
        tx = self._compra()
        url = "/transactions/%s" % tx.uuid_ref
        r = self.api("GET", url, raw=self.raw_lab)
        self.assertEqual(r.status_code, 200, r.text)
        c = r.json()["cancellation"]
        self.assertTrue(c["seller_can_cancel"])
        self.assertTrue(c["can_request_mutual"])
        self.assertFalse(c["buyer_can_cancel"])
        self.assertIsNone(c["cancelled"])
        # El comprador sin causa: 422.
        self.assertProblem(self.api("POST", url + ":cancel", raw=self.raw_a, body={}), 422)
        r = self.api("POST", url + ":cancel", raw=self.raw_lab, body={"reason": "Mortalidad"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["state"], "cancel")
        self.assertEqual(body["cancellation"]["cancelled"]["by"], "seller")
        self.assertTrue(body["cancellation"]["cancelled"]["seller_fault"])

    def test_mutuo_acuerdo(self):
        tx = self._compra()
        url = "/transactions/%s" % tx.uuid_ref
        self.assertProblem(self.api("POST", url + ":cancel-request", raw=self.raw_a, body={}), 422)
        r = self.api("POST", url + ":cancel-request", raw=self.raw_a,
                     body={"reason": "Ya no hace falta"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cancellation"]["request"]["waiting_for"], "seller")
        # Un tercero no la ve; quien pidió no la acepta.
        self.assertProblem(self.api("POST", url + ":cancel-request-accept", raw=self.raw_b,
                                    body={}), 404)
        self.assertProblem(self.api("POST", url + ":cancel-request-accept", raw=self.raw_a,
                                    body={}), 422)
        r = self.api("POST", url + ":cancel-request-reject", raw=self.raw_lab, body={"note": "No"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["state"], "confirmed")
        self.api("POST", url + ":cancel-request", raw=self.raw_lab, body={"reason": "Lote perdido"})
        r = self.api("POST", url + ":cancel-request-accept", raw=self.raw_a, body={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["state"], "cancel")
        self.assertEqual(r.json()["cancellation"]["cancelled"]["by"], "mutual")

    def test_cambiar_verificadora_sin_vencer(self):
        tx = self._compra()
        url = "/transactions/%s" % tx.uuid_ref
        r = self.api("POST", url + ":switch-verifier", raw=self.raw_a,
                     body={"verifier": tx.seller_partner_id.uuid_ref})
        self.assertIn(r.status_code, (409, 422), r.text)
        self.assertProblem(self.api("POST", url + ":switch-verifier", raw=self.raw_lab,
                                    body={"verifier": tx.seller_partner_id.uuid_ref}), 403)
