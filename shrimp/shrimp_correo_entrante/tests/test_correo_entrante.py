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
To: {para}
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
        self.Correo = self.env["shrimp.correo"].sudo()
        self.Correo.search([]).unlink()
        self.gerencia = self.env.ref("shrimp_correo_entrante.buzon_gerencia_general")
        self.info = self.env.ref("shrimp_correo_entrante.buzon_info")
        self.facturacion = self.env.ref("shrimp_correo_entrante.buzon_facturacion")

    def _post(self, cuerpo, token="clave-de-prueba", para=None):
        cabeceras = {"X-Correo-Token": token, "Content-Type": "message/rfc822"}
        if para:
            cabeceras["X-Correo-Para"] = para
        return self.url_open("/correo/entrante", data=cuerpo.encode(), headers=cabeceras)

    def test_respuesta_queda_en_la_conversacion_y_en_la_bandeja(self):
        r = self._post(RESPUESTA.format(mid=self.enviado.message_id), para="gerencia_general@camaronmarket.com")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "procesado")
        self.socio.invalidate_recordset()
        cuerpos = " ".join(self.socio.message_ids.mapped("body"))
        self.assertIn("nos interesa una reunión", cuerpos)
        # Y además queda en la bandeja de Gerencia General, vinculado al contacto.
        correo = self.Correo.search([("message_id", "=", "<respuesta-1@empacadora.test>")])
        self.assertEqual(len(correo), 1)
        self.assertEqual(correo.buzon_id, self.gerencia)
        self.assertEqual((correo.carpeta, correo.direccion, correo.leido), ("entrada", "entrante", False))
        self.assertEqual((correo.vinculo_modelo, correo.vinculo_id), ("res.partner", self.socio.id))
        self.assertEqual(correo.partner_id, self.socio)
        self.assertIn("nos interesa una reunión", correo.body_html)
        self.assertEqual(correo.conversacion, self.enviado.message_id)

    def test_correo_suelto_se_guarda_en_su_buzon(self):
        antes = self.env["mail.message"].search_count([])
        r = self._post(SUELTO.format(para="info@camaronmarket.com"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "guardado")
        # message_process no crea nada (no responde a ningún registro)...
        self.assertEqual(self.env["mail.message"].search_count([]), antes)
        # ...pero la bandeja de info@ sí lo tiene.
        correo = self.Correo.search([("message_id", "=", "<suelto-1@otro.test>")])
        self.assertEqual(correo.buzon_id, self.info)
        self.assertEqual(correo.asunto, "Consulta suelta")
        self.assertIn("quisiera información", correo.resumen)
        self.assertFalse(correo.vinculo_modelo)

    def test_buzon_desconocido_se_ignora(self):
        r = self._post(SUELTO.format(para="nadie@otro.test"), para="nadie@otro.test")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "ignorado")
        self.assertFalse(self.Correo.search([("message_id", "=", "<suelto-1@otro.test>")]))

    def test_copia_oculta_por_el_destinatario_del_sobre(self):
        # Facturación no figura en To/Cc: solo el Worker sabe que iba para ella.
        r = self._post(SUELTO.format(para="cliente@otro.test"), para="facturacion@camaronmarket.com")
        self.assertEqual(r.text, "guardado")
        correo = self.Correo.search([("message_id", "=", "<suelto-1@otro.test>")])
        self.assertEqual(correo.buzon_id, self.facturacion)

    def test_no_se_duplica(self):
        crudo = SUELTO.format(para="info@camaronmarket.com, gerencia_general@camaronmarket.com")
        # El Worker entrega una vez por cada destinatario del sobre.
        self._post(crudo, para="info@camaronmarket.com")
        self._post(crudo, para="gerencia_general@camaronmarket.com")
        self._post(crudo, para="info@camaronmarket.com")
        correos = self.Correo.search([("message_id", "=", "<suelto-1@otro.test>")])
        self.assertEqual(len(correos), 2)
        self.assertEqual(correos.buzon_id, self.info | self.gerencia)

    def test_sin_clave_no_entra(self):
        r = self._post(RESPUESTA.format(mid=self.enviado.message_id), token="otra")
        self.assertEqual(r.status_code, 403)
        r = self._post(RESPUESTA.format(mid=self.enviado.message_id), token="")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.Correo.search([("message_id", "=", "<respuesta-1@empacadora.test>")]))
