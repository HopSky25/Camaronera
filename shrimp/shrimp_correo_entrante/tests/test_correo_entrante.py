from odoo.tests import HttpCase, tagged

RESPUESTA = """From: Juan Perez <juan@empacadora.test>
To: gerencia_general@camaronmarket.com
Subject: Re: Presentación CamaronMarket
Message-ID: <respuesta-1@empacadora.test>
In-Reply-To: {mid}
References: {mid}
Content-Type: text/plain; charset=utf-8

Gracias, nos interesa una reunión el martes.
"""

SUELTO = """From: Alguien <alguien@otro.test>
To: info@camaronmarket.com
Subject: Consulta suelta
Message-ID: <suelto-1@otro.test>
Content-Type: text/plain; charset=utf-8

Hola, quisiera información.
"""


@tagged("post_install", "-at_install")
class TestCorreoEntrante(HttpCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("shrimp_correo.token", "clave-de-prueba")
        self.socio = self.env["res.partner"].create({"name": "Empacadora Prueba", "email": "juan@empacadora.test"})
        self.enviado = self.socio.message_post(body="Le escribimos desde CamaronMarket", message_type="comment",
                                               subtype_xmlid="mail.mt_comment")

    def _post(self, cuerpo, token="clave-de-prueba"):
        return self.url_open("/correo/entrante", data=cuerpo.encode(), headers={"X-Correo-Token": token,
                                                                                "Content-Type": "message/rfc822"})

    def test_respuesta_queda_en_la_conversacion(self):
        r = self._post(RESPUESTA.format(mid=self.enviado.message_id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "procesado")
        self.socio.invalidate_recordset()
        cuerpos = " ".join(self.socio.message_ids.mapped("body"))
        self.assertIn("nos interesa una reunión", cuerpos)

    def test_correo_suelto_se_ignora_sin_crear_nada(self):
        antes = self.env["mail.message"].search_count([])
        r = self._post(SUELTO)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "ignorado")
        self.assertEqual(self.env["mail.message"].search_count([]), antes)

    def test_sin_clave_no_entra(self):
        r = self._post(RESPUESTA.format(mid=self.enviado.message_id), token="otra")
        self.assertEqual(r.status_code, 403)
        r = self._post(RESPUESTA.format(mid=self.enviado.message_id), token="")
        self.assertEqual(r.status_code, 403)
