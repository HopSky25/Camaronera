"""Recepción por el comprador desde la API (/api/v1).

- el detalle de la compra trae «reception» (campo nuevo, compatible);
- :dispatch lo hace solo el vendedor; :receive (con o sin nota) y
  :report-issue solo el comprador;
- :receive sin cuerpo sigue funcionando como antes.
"""
from datetime import timedelta

from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiRecepcion(ApiCase):

    def _compra(self):
        hoy = self.env["shrimp.transaction"]._shrimp_today()
        self.product.sudo().expected_delivery_date = hoy + timedelta(days=3)
        tx = self.product.execute_purchase_flow(self.farm_a, 4.0)["transaction"]
        return tx

    def test_despacho_y_recepcion(self):
        tx = self._compra()
        r = self.api("GET", "/transactions/%s" % tx.uuid_ref, raw=self.raw_a)
        self.assertEqual(r.status_code, 200, r.text)
        rec = r.json()["reception"]
        self.assertTrue(rec["required"])
        self.assertEqual(rec["status"], "waiting")
        self.assertIsNone(rec["dispatched_at"])
        # El comprador no puede marcar el despacho; el vendedor sí.
        self.assertProblem(self.api("POST", "/transactions/%s:dispatch" % tx.uuid_ref,
                                    raw=self.raw_a, body={}), 403)
        r = self.api("POST", "/transactions/%s:dispatch" % tx.uuid_ref, raw=self.raw_lab,
                     body={"note": "Sale hoy"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["reception"]["dispatched_at"])
        # El vendedor no confirma por el comprador.
        self.assertProblem(self.api("POST", "/transactions/%s:receive" % tx.uuid_ref,
                                    raw=self.raw_lab, body={}), 403)
        # Campo desconocido -> 422.
        self.assertProblem(self.api("POST", "/transactions/%s:receive" % tx.uuid_ref,
                                    raw=self.raw_a, body={"foto": "x"}), 422)
        r = self.api("POST", "/transactions/%s:receive" % tx.uuid_ref, raw=self.raw_a,
                     body={"note": "Completo"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["state"], "done")
        self.assertEqual(body["reception"]["status"], "received")
        self.assertEqual(body["reception"]["note"], "Completo")
        self.assertFalse(body["reception"]["auto_confirmed"])

    def test_receive_sin_cuerpo_y_reporte(self):
        tx = self._compra()
        tx.sudo().write({"desired_date": tx.desired_date - timedelta(days=5)})
        r = self.api("POST", "/transactions/%s:receive" % tx.uuid_ref, raw=self.raw_a)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["state"], "done")
        tx2 = self._compra()
        self.assertProblem(self.api("POST", "/transactions/%s:report-issue" % tx2.uuid_ref,
                                    raw=self.raw_a, body={}), 422)
        r = self.api("POST", "/transactions/%s:report-issue" % tx2.uuid_ref, raw=self.raw_a,
                     body={"note": "Llegó la mitad"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["reception"]["status"], "incidence")
        self.assertEqual(r.json()["reception"]["incidence"]["note"], "Llegó la mitad")
        self.assertProblem(self.api("POST", "/transactions/%s:report-issue" % tx2.uuid_ref,
                                    raw=self.raw_b, body={"note": "x"}), 404)
