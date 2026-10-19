"""Hueco #6: la puerta de documentos legales MAYORES también frena los POST
de formularios web (antes solo los GET)."""
import re

from odoo.tests import HttpCase, new_test_user, tagged

CLAVE = "Clave-segura-123"


@tagged("post_install", "-at_install")
class TestPuertaLegalPost(HttpCase):

    def test_post_sin_aceptar_va_a_aceptar(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.privacy_gate_in_tests", "1")
        new_test_user(self.env, login="post.gate", password=CLAVE, groups="base.group_portal",
                      name="Socio post gate")
        self.authenticate("post.gate", CLAVE)
        # /my/data está exenta (derechos del titular): da el csrf.
        pagina = self.url_open("/my/data")
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', pagina.text).group(1)
        r = self.url_open("/my/profile/activate", data={"csrf_token": token, "role": "camaronera"},
                          allow_redirects=False, headers={"Referer": self.base_url() + "/my/home"})
        self.assertIn(r.status_code, (302, 303))
        self.assertIn("/my/legal/accept?redirect=", r.headers["Location"])
        self.assertIn("%2Fmy%2Fhome", r.headers["Location"])
        # Las rutas exentas siguen funcionando por POST.
        r = self.url_open("/my/data/request", data={"csrf_token": token, "tipo": "acceso",
                                                    "detalle": "x"}, allow_redirects=False)
        self.assertIn("/my/data?ok=1", r.headers["Location"])
