"""D-29: la página pública /t/<token> y su API solo para compras confirmadas o
completadas; cancelada con sello; las partes la retiran o renuevan desde la
compra; la rotación por API sigue igual (y vuelve a publicarla)."""
import re

from odoo.tests import tagged

from .common import ApiCase


@tagged("post_install", "-at_install", "shrimp_api")
class TestTrazabilidadPublicaD29(ApiCase):

    def _tx(self, qty=3):
        return self.product.execute_purchase_flow(self.farm_a, qty)["transaction"]

    def _public(self, token):
        return self.url_open("/api/v1/public/traceability/%s" % token)

    def _csrf(self, url):
        r = self.url_open(url)
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text) or \
            re.search(r'csrf_token["\']?\s*[:=]\s*["\']([0-9a-fA-Fo]+)["\']', r.text)
        return m.group(1)

    def test_borrador_no_tiene_pagina(self):
        tx = self._tx()
        tx.sudo().write({"state": "draft"})
        self.assertEqual(tx.shrimp_trace_public_status(), "unavailable")
        self.assertEqual(self.url_open("/t/%s" % tx.trace_token).status_code, 404)
        self.assertProblem(self._public(tx.trace_token), 404)
        self.assertFalse(tx.traceability_qr_uri(), "sin página no hay QR en el certificado")

    def test_confirmada_y_cancelada_con_sello(self):
        tx = self._tx()
        self.assertEqual(self.url_open("/t/%s" % tx.trace_token).status_code, 200)
        data = self._public(tx.trace_token).json()
        self.assertFalse(data["cancelled"])
        tx.shrimp_cancel("platform", reason="Prueba D-29")
        page = self.url_open("/t/%s" % tx.trace_token)
        self.assertEqual(page.status_code, 200)
        self.assertIn("trace_cancelled_stamp", page.text)
        self.assertIn("Compra cancelada", page.text)
        data = self._public(tx.trace_token).json()
        self.assertTrue(data["cancelled"])
        self.assertEqual(data["status"], "cancel")

    def test_retirar_y_renovar_desde_el_portal(self):
        tx = self._tx()
        token = tx.trace_token
        self.authenticate("a.api", "Clave-api-123")
        compra = "/marketplace/purchases/%s/traceability" % tx.uuid_ref
        html = self.url_open(compra).text
        self.assertIn("shrimp_trace_public", html)
        self.assertIn("Retirar página pública", html)
        r = self.url_open("/marketplace/purchases/%s/traceability/withdraw" % tx.uuid_ref,
                          data={"csrf_token": self._csrf(compra)}, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertEqual(tx.shrimp_trace_public_status(), "withdrawn")
        self.assertEqual(self.url_open("/t/%s" % token).status_code, 404)
        self.assertProblem(self._public(token), 404)
        r = self.url_open("/marketplace/purchases/%s/traceability/renew" % tx.uuid_ref,
                          data={"csrf_token": self._csrf(compra)}, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertNotEqual(tx.trace_token, token)
        self.assertEqual(tx.shrimp_trace_public_status(), "public")
        self.assertEqual(self.url_open("/t/%s" % token).status_code, 404, "el QR viejo no vale")
        self.assertEqual(self.url_open("/t/%s" % tx.trace_token).status_code, 200)

    def test_ajeno_no_retira(self):
        from odoo.exceptions import AccessError
        tx = self._tx()
        with self.assertRaises(AccessError):
            tx.with_user(self.u_b).sudo().action_withdraw_public_trace(actor=self.farm_b)
        self.assertEqual(tx.shrimp_trace_public_status(), "public")
        # El vendedor también puede (es parte de la compra).
        tx.with_user(self.u_lab).sudo().action_withdraw_public_trace(actor=self.lab)
        self.assertEqual(tx.shrimp_trace_public_status(), "withdrawn")

    def test_api_rotar_vuelve_a_publicar(self):
        tx = self._tx()
        tx.action_withdraw_public_trace()
        self.assertEqual(tx.shrimp_trace_public_status(), "withdrawn")
        r = self.api("POST", "/transactions/%s/trace-token:rotate" % tx.uuid_ref, raw=self.raw_a)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("/t/", r.json()["public_url"])
        self.assertEqual(tx.shrimp_trace_public_status(), "public")
        trace = self.api("GET", "/transactions/%s/traceability" % tx.uuid_ref, raw=self.raw_a).json()
        self.assertEqual(trace["public_status"], "public")
        self.assertIn("/t/", trace["public_url"])
