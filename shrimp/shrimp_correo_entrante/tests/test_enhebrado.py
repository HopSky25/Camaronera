from odoo.tests import TransactionCase, new_test_user, tagged

RESPUESTA = """From: {de}
To: {para}
Subject: {asunto}
Message-ID: <{mid}@cliente.test>
In-Reply-To: <reescrito-por-resend-{mid}@resend.dev>
References: <reescrito-por-resend-{mid}@resend.dev>
Content-Type: text/plain; charset=utf-8

{texto}
"""


@tagged("post_install", "-at_install")
class TestEnhebrado(TransactionCase):
    """El proveedor SMTP reescribe el Message-ID de lo que enviamos: las
    respuestas llegan con referencias que Odoo no conoce y se enhebran por
    remitente y asunto."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Correo = cls.env["shrimp.correo"].sudo()
        # Bandeja vacía: los conteos no dependen de lo que ya tenga la base.
        cls.env["shrimp.correo"].sudo().search([]).unlink()
        cls.info = cls.env.ref("shrimp_correo_entrante.buzon_info")
        cls.vendedor = new_test_user(cls.env, login="vendedor_enhebrado",
                                     groups="base.group_user,shrimp_correo_entrante.group_correo_usuario,"
                                            "sales_team.group_sale_salesman")
        cls.info.user_ids = [(4, cls.vendedor.id)]
        cls.cliente = cls.env["res.partner"].create({"name": "Marta Vélez", "email": "marta@empacadora.test"})
        cls.lead = cls.env["crm.lead"].create({"name": "Camarón 20/30 para Marta", "partner_id": cls.cliente.id,
                                               "type": "opportunity"})
        # Lo que se le envió desde el chatter de la oportunidad.
        cls.enviado = cls.lead.message_post(
            body="<p>Le enviamos la propuesta.</p>", subject="Propuesta comercial noviembre",
            partner_ids=cls.cliente.ids, message_type="comment", subtype_xmlid="mail.mt_comment")

    def _recibir(self, asunto, de="Marta Vélez <marta@empacadora.test>", mid="r1",
                 texto="Recibida, la revisamos y le respondemos.", para="info@camaronmarket.com"):
        crudo = RESPUESTA.format(de=de, para=para, asunto=asunto, mid=mid, texto=texto)
        return self.Correo._shrimp_recibir(crudo.encode(), para)

    def _mensajes_cliente(self):
        return self.lead.message_ids.filtered(lambda m: m.message_type == "email")

    def test_referencia_desconocida_pero_asunto_y_remitente_coinciden(self):
        resultado = self._recibir("RE: Propuesta comercial noviembre")
        self.assertTrue(resultado["enrutado"])
        mensaje = self._mensajes_cliente()
        self.assertEqual(len(mensaje), 1)
        self.assertIn("la revisamos", mensaje.body)
        self.assertEqual(mensaje.author_id, self.cliente)
        self.assertEqual(mensaje.message_id, "<r1@cliente.test>")
        # La fila de la bandeja queda vinculada a la oportunidad.
        correo = resultado["correos"]
        self.assertEqual((correo.vinculo_modelo, correo.vinculo_id), ("crm.lead", self.lead.id))

    def test_remitente_distinto_no_se_publica(self):
        resultado = self._recibir("RE: Propuesta comercial noviembre", de="Otro <otro@ajeno.test>")
        self.assertFalse(resultado["enrutado"])
        self.assertFalse(self._mensajes_cliente())
        self.assertFalse(resultado["correos"].vinculo_modelo)
        # Asunto distinto, mismo remitente: tampoco.
        resultado = self._recibir("RE: Otra cosa", mid="r2")
        self.assertFalse(resultado["enrutado"])
        self.assertFalse(self._mensajes_cliente())

    def test_prefijos_anidados(self):
        resultado = self._recibir("RE: RV: Re:  Propuesta comercial   noviembre")
        self.assertTrue(resultado["enrutado"])
        self.assertEqual(len(self._mensajes_cliente()), 1)

    def test_mensaje_sin_asunto_usa_el_nombre_del_registro(self):
        self.lead.message_post(body="<p>¿Pudo ver los precios?</p>", partner_ids=self.cliente.ids,
                               message_type="comment", subtype_xmlid="mail.mt_comment")
        resultado = self._recibir("Re: Camarón 20/30 para Marta", mid="r3")
        self.assertTrue(resultado["enrutado"])
        self.assertEqual(len(self._mensajes_cliente()), 1)

    def test_remitente_sin_contacto_se_crea(self):
        # Enviado a una dirección suelta (sin contacto), registrada en outgoing_email_to.
        self.lead.message_post(body="<p>Hola</p>", subject="Muestras de laboratorio",
                               message_type="comment", subtype_xmlid="mail.mt_comment",
                               outgoing_email_to="lab@nuevo.test")
        resultado = self._recibir("RE: Muestras de laboratorio", de="Laboratorio Nuevo <lab@nuevo.test>",
                                  mid="r4")
        self.assertTrue(resultado["enrutado"])
        autor = self._mensajes_cliente().author_id
        self.assertEqual(autor.email, "lab@nuevo.test")
        self.assertEqual(autor.name, "Laboratorio Nuevo")

    def test_respuesta_a_un_envio_de_la_bandeja(self):
        Correo = self.env["shrimp.correo"].with_user(self.vendedor)
        borrador = Correo.create({"buzon_id": self.info.id, "email_to": "marta@empacadora.test",
                                  "asunto": "Oferta semanal", "body_html": "<p>Precios</p>"})
        borrador.action_enviar()
        resultado = self._recibir("RE: RV: Oferta semanal", mid="r5")
        respuesta = resultado["correos"]
        # Sin referencias válidas: se agrupa en la misma conversación por asunto.
        self.assertEqual(respuesta.conversacion, borrador.conversacion)
        self.assertEqual(respuesta.hilo_id, borrador.hilo_id)
        self.assertEqual(borrador.hilo_cantidad, 2)
        # No tenía registro detrás: no se publica en ningún chatter.
        self.assertFalse(resultado["enrutado"])

    def test_referencia_a_la_bandeja_lleva_al_registro_vinculado(self):
        # Un envío desde la bandeja vinculado a la oportunidad, cuya copia en
        # mail.message ya no existe (p. ej. se borró la cola): la referencia
        # al Message-ID guardado en la bandeja basta para llegar al registro.
        enviado = self.Correo.create({
            "buzon_id": self.info.id, "direccion": "saliente", "carpeta": "enviados",
            "email_to": "marta@empacadora.test", "asunto": "Contrato",
            "message_id": "<guardado-1@camaronmarket.com>",
            "vinculo_modelo": "crm.lead", "vinculo_id": self.lead.id,
        })
        crudo = ("From: marta@empacadora.test\nTo: info@camaronmarket.com\nSubject: Firmado\n"
                 "Message-ID: <r6@cliente.test>\nIn-Reply-To: <guardado-1@camaronmarket.com>\n"
                 "Content-Type: text/plain; charset=utf-8\n\nAdjunto el contrato firmado.\n")
        resultado = self.Correo._shrimp_recibir(crudo.encode(), "info@camaronmarket.com")
        self.assertTrue(resultado["enrutado"])
        self.assertIn("contrato firmado", self._mensajes_cliente().body)
        self.assertEqual(resultado["correos"].hilo_id, enviado.hilo_id)

    def test_referencias_validas_siguen_por_message_process(self):
        crudo = ("From: marta@empacadora.test\nTo: info@camaronmarket.com\nSubject: Otra cosa\n"
                 "Message-ID: <r7@cliente.test>\nIn-Reply-To: %s\n"
                 "Content-Type: text/plain; charset=utf-8\n\nRespuesta normal.\n") % self.enviado.message_id
        resultado = self.Correo._shrimp_recibir(crudo.encode(), "info@camaronmarket.com")
        self.assertTrue(resultado["enrutado"])
        mensaje = self._mensajes_cliente()
        self.assertEqual(mensaje.parent_id, self.enviado)
