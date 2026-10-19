"""Portal: en una segunda opinión cambia de verificadora el vendedor.

La plantilla «Plazos de la verificadora» ya le ofrecía el formulario a quien
pidió la segunda opinión (el vendedor), pero la ruta solo dejaba pasar al
comprador (403). Ahora entran las dos partes y el modelo decide quién elige.
"""
import re

from odoo import fields
from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE

from .common_seguridad import montar_verificacion


@tagged("post_install", "-at_install")
class TestPortalCambioVerificadora(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("shrimp_verification.contest_hours", "24")
        cls.s, v = montar_verificacion(cls.env, "pcv")
        v = v.sudo()
        v.action_assign_technician(cls.s["tec"])
        v.action_start_field()
        v.write({"larvae_qty_verified": 10.0, "larvae_survival_rate": 50.0,
                 "larvae_avg_size_mg": 5.0, "larvae_health_status": "rejected",
                 "field_start_date": fields.Datetime.now()})
        v._close("rejected", notes="Estado sanitario inaceptable")
        cls.segunda = v.action_request_second_opinion(cls.s["verif2"], actor=cls.s["lab"])
        cls.segunda.action_verifier_decline("Conflicto de interés")
        cls.tx = v.transaction_id

    def _csrf(self, url):
        html = self.url_open(url).text
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertTrue(m, "la página trae el token CSRF")
        return m.group(1), html

    def test_el_vendedor_cambia_la_verificadora_de_la_segunda_opinion(self):
        pagina = "/marketplace/purchases/%s/traceability" % self.tx.uuid_ref
        accion = "/marketplace/purchases/%s/verifier/assign" % self.tx.uuid_ref
        # El comprador no elige en una segunda opinión: no cambia nada.
        self.authenticate(self.s["u_cam"].login, CLAVE)
        tok, _html = self._csrf(pagina)
        r = self.url_open(accion, data={"csrf_token": tok, "verifier_ref": self.s["verif"].uuid_ref},
                          allow_redirects=False)
        self.assertIn(r.status_code, (302, 303), r.text[:200])
        self.assertIn("error=verdeadline", r.headers.get("Location", ""))
        self.segunda.invalidate_recordset()
        self.assertEqual(self.segunda.verifier_partner_id, self.s["verif2"])
        # El vendedor ve el formulario y lo usa.
        self.authenticate(self.s["u_lab"].login, CLAVE)
        tok, html = self._csrf(pagina)
        self.assertIn(accion, html, "la plantilla le ofrece cambiar de verificadora")
        r = self.url_open(accion, data={"csrf_token": tok, "verifier_ref": self.s["verif"].uuid_ref},
                          allow_redirects=False)
        self.assertIn("saved=verifier_switched", r.headers.get("Location", ""), r.text[:200])
        self.segunda.invalidate_recordset()
        self.assertEqual(self.segunda.verifier_partner_id, self.s["verif"])
        self.assertEqual(self.segunda.verifier_response, "pending", "la nueva también responde")
