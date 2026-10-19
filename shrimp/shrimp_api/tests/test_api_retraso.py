"""Retraso de entrega desde la API (/api/v1).

- el detalle trae «delay» (campo nuevo, compatible) y las rutas viejas
  siguen igual;
- :notify-delay solo el vendedor y dentro de los límites;
- :delay-counter solo el comprador; :delay-reject / :delay-accept según a
  quién le toca; :cancel solo el comprador y solo con causa.
"""
from datetime import timedelta

from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiRetraso(ApiCase):

    def _compra(self, dias=3):
        Tx = self.env["shrimp.transaction"]
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_marketplace.delay_rules_since",
            (Tx._shrimp_today() - timedelta(days=400)).isoformat())
        hoy = Tx._shrimp_today()
        self.product.sudo().expected_delivery_date = hoy + timedelta(days=dias)
        tx = self.product.execute_purchase_flow(self.farm_a, 4.0)["transaction"]
        tx.sudo().write({"desired_date": hoy + timedelta(days=dias)})
        return tx, tx.delivery_date

    def test_flujo_aviso_contrapropuesta_rechazo_y_cancelacion(self):
        tx, entrega = self._compra()
        url = "/transactions/%s" % tx.uuid_ref
        r = self.api("GET", url, raw=self.raw_lab)
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()["delay"]
        self.assertTrue(d["can_notify"])
        self.assertEqual(d["notices_left"], 2)
        self.assertEqual(d["max_date"], (entrega + timedelta(days=15)).isoformat())
        self.assertIsNone(d["pending"])
        self.assertFalse(r.json()["reception"]["dispatched_at"], "lo de antes sigue")
        # El comprador no avisa retrasos.
        self.assertProblem(self.api("POST", url + ":notify-delay", raw=self.raw_a, body={
            "new_date": (entrega + timedelta(days=3)).isoformat(), "reason": "aguaje"}), 403)
        # Motivo inválido y fecha fuera del tope.
        self.assertProblem(self.api("POST", url + ":notify-delay", raw=self.raw_lab, body={
            "new_date": (entrega + timedelta(days=3)).isoformat(), "reason": "lluvia"}), 422)
        self.assertProblem(self.api("POST", url + ":notify-delay", raw=self.raw_lab, body={
            "new_date": (entrega + timedelta(days=30)).isoformat(), "reason": "aguaje"}), 422)
        r = self.api("POST", url + ":notify-delay", raw=self.raw_lab, body={
            "new_date": (entrega + timedelta(days=6)).isoformat(), "reason": "transport",
            "note": "Camión dañado"})
        self.assertEqual(r.status_code, 200, r.text)
        pend = r.json()["delay"]["pending"]
        self.assertEqual(pend["state"], "proposed")
        self.assertEqual(pend["waiting_for"], "buyer")
        # Comprador: contrapropone (el vendedor no puede).
        self.assertProblem(self.api("POST", url + ":delay-counter", raw=self.raw_lab, body={
            "date": (entrega + timedelta(days=2)).isoformat()}), 403)
        r = self.api("POST", url + ":delay-counter", raw=self.raw_a, body={
            "date": (entrega + timedelta(days=2)).isoformat(), "note": "Antes"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["delay"]["pending"]["waiting_for"], "seller")
        # Aún sin causa: no se puede cancelar.
        self.assertProblem(self.api("POST", url + ":cancel", raw=self.raw_a, body={}), 422)
        # Vendedor: rechaza.
        self.assertProblem(self.api("POST", url + ":delay-reject", raw=self.raw_a, body={}), 403)
        r = self.api("POST", url + ":delay-reject", raw=self.raw_lab, body={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["delay"]["history"][0]["state"], "rejected")
        r = self.api("GET", url, raw=self.raw_a)
        self.assertTrue(r.json()["delay"]["can_cancel"])
        self.assertEqual(r.json()["delay"]["cancel_reason"], "rejected")
        # Un tercero ni la ve.
        self.assertProblem(self.api("POST", url + ":cancel", raw=self.raw_b, body={}), 404)
        # El vendedor ahora también cancela por :cancel (antes del despacho, con
        # strike), pero tiene que dar el motivo.
        self.assertProblem(self.api("POST", url + ":cancel", raw=self.raw_lab, body={}), 422)
        r = self.api("POST", url + ":cancel", raw=self.raw_a, body={"note": "Compro a otro"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["state"], "cancel")
        self.assertEqual(body["delay"]["history"][-1]["state"], "cancelled")

    def test_aceptar(self):
        tx, entrega = self._compra()
        url = "/transactions/%s" % tx.uuid_ref
        nueva = entrega + timedelta(days=4)
        self.assertEqual(self.api("POST", url + ":notify-delay", raw=self.raw_lab, body={
            "new_date": nueva.isoformat(), "reason": "production"}).status_code, 200)
        # El vendedor no acepta su propia propuesta.
        self.assertProblem(self.api("POST", url + ":delay-accept", raw=self.raw_lab, body={}), 422)
        r = self.api("POST", url + ":delay-accept", raw=self.raw_a, body={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["delivery_date"], nueva.isoformat())
        self.assertEqual(r.json()["delay"]["history"][0]["agreed_date"], nueva.isoformat())
        self.assertEqual(r.json()["reception"]["can_confirm_from"], nueva.isoformat())
