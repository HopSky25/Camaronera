import re

from odoo.tests import HttpCase, tagged

from .legal_utils import signup_post


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto) or \
        re.search(r'csrf_token["\']?\s*[:=]\s*["\']([0-9a-fA-Fo]+)["\']', texto)
    return m.group(1) if m else None


@tagged("post_install", "-at_install")
class TestRegistroSeguridad(HttpCase):
    """M4: lista blanca de tipos por sitio, honeypot, límite por IP y
    aprobación de roles operativos; B5: mensajes de la URL."""

    def _alta(self, tipo, correo, ruc, **extra):
        token = _csrf(self.url_open("/register").text)
        self.assertTrue(token, "el formulario de registro debe traer CSRF")
        # 19.0.1.6.0: el asistente exige datos de empresa, una cédula/RUC
        # válido (el `ruc` de cada test ya no pasa el dígito verificador) y
        # aceptar contrato, políticas y anexo.
        datos = signup_post(self.env, [tipo], correo, csrf_token=token)
        datos.update(extra)
        return self.url_open("/register/submit", data=datos)

    def _existe(self, correo):
        return bool(self.env["res.users"].sudo().search([("login", "=", correo)]))

    def test_tipo_fuera_de_la_lista_blanca(self):
        # Verificador sin su acreditación adjunta (o sin el módulo de
        # verificación), no; un tipo inventado, nunca.
        r = self._alta("verificador", "verif.alta@prueba.test", "0955511111001")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self._existe("verif.alta@prueba.test"))
        r = self._alta("admin", "admin.alta@prueba.test", "0955511112001")
        self.assertFalse(self._existe("admin.alta@prueba.test"))
        # Un semillero sí.
        self._alta("semillero", "sem.alta@prueba.test", "0955511113001")
        self.assertTrue(self._existe("sem.alta@prueba.test"))
        socio = self.env["res.partner"].sudo().search([("email", "=", "sem.alta@prueba.test")])
        self.assertEqual(socio.shrimp_account_state, "approved")

    def test_honeypot_descarta_el_alta(self):
        r = self._alta("semillero", "bot.alta@prueba.test", "0955511114001",
                       website_hp="http://spam.example")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self._existe("bot.alta@prueba.test"))

    def test_limite_de_altas_por_ip(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.signup_rate_limit", "2")
        self._alta("semillero", "ip1.alta@prueba.test", "0955511115001")
        self._alta("semillero", "ip2.alta@prueba.test", "0955511116001")
        r = self._alta("semillero", "ip3.alta@prueba.test", "0955511117001")
        self.assertTrue(self._existe("ip1.alta@prueba.test"))
        self.assertTrue(self._existe("ip2.alta@prueba.test"))
        self.assertFalse(self._existe("ip3.alta@prueba.test"))
        self.assertIn("Demasiados intentos", r.text)
