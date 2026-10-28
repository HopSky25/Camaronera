import base64
import email.policy
from email.message import EmailMessage
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.service.model import call_kw
from odoo.tests import TransactionCase, new_test_user, tagged

# PNG de 1x1 píxel.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


def correo_multiparte(para="info@camaronmarket.com", message_id="<multi-1@cliente.test>"):
    msg = EmailMessage()
    msg["From"] = "Ana Ruiz <ana@cliente.test>"
    msg["To"] = para
    msg["Cc"] = "Pedro <pedro@cliente.test>, gerencia_general@camaronmarket.com"
    msg["Subject"] = "Cotización de camarón 16/20"
    msg["Message-ID"] = message_id
    msg["Date"] = "Mon, 26 Oct 2026 10:15:00 -0500"
    msg.set_content("Adjunto la cotización.\nSaludos, Ana")
    msg.add_alternative(
        '<html><head><style>p{color:red}</style><script>alert(1)</script></head><body>'
        '<p style="color:#123456">Adjunto la <b>cotización</b>.</p>'
        '<img src="cid:logo123@cliente"/></body></html>', subtype="html")
    # La imagen en línea va dentro de un multipart/related con el HTML.
    msg.get_payload()[1].add_related(PNG, maintype="image", subtype="png", cid="<logo123@cliente>",
                                     filename="logo.png")
    msg.add_attachment(b"%PDF-1.4 cotizacion", maintype="application", subtype="pdf",
                       filename="cotización.pdf")
    return msg.as_bytes(policy=email.policy.SMTP)


TEXTO_LATIN1 = (
    "From: =?iso-8859-1?q?Jos=E9_P=E9rez?= <jose@cliente.test>\r\n"
    "To: facturacion@camaronmarket.com\r\n"
    "Subject: =?iso-8859-1?q?Facturaci=F3n_pendiente?=\r\n"
    "Message-ID: <latin-1@cliente.test>\r\n"
    "Content-Type: text/plain; charset=iso-8859-1\r\n"
    "Content-Transfer-Encoding: 8bit\r\n\r\n"
).encode("ascii") + "Necesito la información de la factura, señor.\r\n".encode("latin-1")


@tagged("post_install", "-at_install")
class TestBandeja(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Correo = cls.env["shrimp.correo"]
        # Bandeja vacía: los conteos no dependen de lo que ya tenga la base.
        cls.env["shrimp.correo"].sudo().search([]).unlink()
        cls.info = cls.env.ref("shrimp_correo_entrante.buzon_info")
        cls.gerencia = cls.env.ref("shrimp_correo_entrante.buzon_gerencia_general")
        cls.facturacion = cls.env.ref("shrimp_correo_entrante.buzon_facturacion")
        cls.info.write({"nombre_remitente": "CamaronMarket", "firma": "<p>Equipo de Información</p>"})
        grupo = "base.group_user,shrimp_correo_entrante.group_correo_usuario"
        cls.lector = new_test_user(cls.env, login="lector_info", groups=grupo + ",sales_team.group_sale_salesman",
                                   name="Lectora Info")
        cls.otro = new_test_user(cls.env, login="sin_buzon", groups=grupo, name="Sin Buzón")
        cls.jefe = new_test_user(cls.env, login="jefe_correo",
                                 groups="base.group_user,shrimp_correo_entrante.group_correo_admin")
        cls.info.user_ids = [(4, cls.lector.id)]

    def _recibir(self, crudo, para=None):
        return self.Correo.sudo()._shrimp_recibir(crudo, para)["correos"]

    # ---------------------------------------------------------------- recepción

    def test_multiparte_con_adjunto_e_imagen_en_linea(self):
        correos = self._recibir(correo_multiparte(), "info@camaronmarket.com")
        # Iba a info@ y con copia a gerencia_general@: queda en los dos buzones.
        self.assertEqual(correos.buzon_id, self.info | self.gerencia)
        correo = correos.filtered(lambda c: c.buzon_id == self.info)
        self.assertEqual(correo.asunto, "Cotización de camarón 16/20")
        self.assertEqual(correo.email_from, "Ana Ruiz <ana@cliente.test>")
        self.assertTrue(correo.tiene_adjuntos)
        # Solo el PDF es adjunto visible; el logo va dentro del cuerpo.
        self.assertEqual(correo.attachment_ids.mapped("name"), ["cotización.pdf"])
        self.assertEqual(correo.attachment_ids.raw, b"%PDF-1.4 cotizacion")
        logo = self.env["ir.attachment"].search([("res_model", "=", "shrimp.correo"),
                                                 ("res_id", "=", correo.id), ("name", "=", "logo.png")])
        self.assertTrue(logo)
        self.assertIn("/web/image/%s?access_token=%s" % (logo.id, logo.access_token), correo.body_html)
        self.assertNotIn("cid:", correo.body_html)
        # HTML saneado: sin script, con los estilos en línea.
        self.assertNotIn("alert(1)", correo.body_html)
        self.assertIn("cotización", correo.body_html)
        self.assertIn("color", correo.body_html)
        self.assertTrue(correo.resumen.startswith("Adjunto la cotización"))
        self.assertEqual(str(correo.fecha), "2026-10-26 15:15:00")

    def test_texto_plano_latin1(self):
        correo = self._recibir(TEXTO_LATIN1)
        self.assertEqual(correo.buzon_id, self.facturacion)
        self.assertEqual(correo.asunto, "Facturación pendiente")
        self.assertIn("José Pérez", correo.email_from)
        self.assertIn("información de la factura, señor", correo.body_html)
        self.assertFalse(correo.tiene_adjuntos)

    def test_correo_dañado_no_rompe(self):
        for crudo in (b"\x00\xff basura sin cabeceras", b"", b"From: <<<\r\nTo: info@camaronmarket.com\r\n"
                      b"Content-Type: multipart/mixed; boundary=\"x\"\r\n\r\n--x\r\nContent-Type: text/plain; "
                      b"charset=desconocido\r\n\r\nhola\r\n"):
            resultado = self.Correo.sudo()._shrimp_recibir(crudo, "info@camaronmarket.com")
            self.assertIn("correos", resultado)
        # El último sí llega a info@ con el texto legible pese al charset raro.
        self.assertTrue(self.Correo.sudo().search([("buzon_id", "=", self.info.id),
                                                   ("body_html", "ilike", "hola")]))

    # ---------------------------------------------------------------- seguridad

    def test_reglas_de_registro(self):
        self._recibir(correo_multiparte(), "info@camaronmarket.com")
        self._recibir(TEXTO_LATIN1)
        Correo = self.Correo
        self.assertEqual(Correo.with_user(self.otro).search_count([]), 0)
        self.assertEqual(Correo.with_user(self.lector).search([]).buzon_id, self.info)
        self.assertEqual(Correo.with_user(self.jefe).search_count([]), Correo.sudo().search_count([]))
        self.assertEqual(self.env["shrimp.buzon"].with_user(self.lector).search([]), self.info)
        self.assertEqual(self.env["shrimp.buzon"].with_user(self.otro).search([]), self.env["shrimp.buzon"])
        # El que no tiene el buzón no puede leer ni mover un correo ajeno aunque mande su id.
        ajeno = Correo.sudo().search([("buzon_id", "=", self.facturacion.id)])
        with self.assertRaises(AccessError):
            ajeno.with_user(self.lector).action_archivar()
        with self.assertRaises(AccessError):
            ajeno.with_user(self.lector).action_responder()
        # Un usuario del portal no entra.
        portal = new_test_user(self.env, login="portal_correo", groups="base.group_portal")
        with self.assertRaises(AccessError):
            ajeno.with_user(portal).action_marcar_leido()

    def test_redactar_abre_con_el_buzon_predeterminado(self):
        """Con varios buzones, «Redactar» usa el predeterminado del usuario; si no
        lo tiene o ya no puede usarlo, el primero de los suyos."""
        self.gerencia.user_ids = [(4, self.lector.id)]
        Lector = self.Correo.with_user(self.lector)
        self.lector.shrimp_buzon_predeterminado_id = self.gerencia
        borrador = Lector.browse(Lector.action_redactar()["res_id"])
        self.assertEqual(borrador.buzon_id, self.gerencia)
        self.assertEqual(Lector.new({}).buzon_id, self.gerencia)
        self.lector.shrimp_buzon_predeterminado_id = self.facturacion  # no es suyo
        borrador = Lector.browse(Lector.action_redactar()["res_id"])
        self.assertIn(borrador.buzon_id, self.env["shrimp.buzon"]._buzones_permitidos(self.lector))
        self.assertNotEqual(borrador.buzon_id, self.facturacion)

    def test_buzon_ajeno_no_envia(self):
        with self.assertRaises(AccessError):
            self.Correo.with_user(self.lector).create({"buzon_id": self.facturacion.id, "email_to": "x@y.test"})
        borrador = self.Correo.with_user(self.lector).create({"buzon_id": self.info.id, "email_to": "x@y.test"})
        with self.assertRaises(AccessError):
            borrador.write({"buzon_id": self.facturacion.id})
        # Un borrador de otro buzón (creado por detrás) tampoco se envía.
        ajeno = self.Correo.sudo().create({"buzon_id": self.facturacion.id, "email_to": "x@y.test"})
        with self.assertRaises(AccessError):
            ajeno.with_user(self.lector).action_enviar()
        # El vínculo lo pone solo el módulo.
        with self.assertRaises(AccessError):
            borrador.write({"vinculo_modelo": "res.users", "vinculo_id": 1})
        self.assertFalse(self.env["mail.mail"].sudo().search([("email_to", "=", "x@y.test")]))

    def test_asignar_usuarios_por_login(self):
        nuevo = new_test_user(self.env, login="nueva_contadora", groups="base.group_user")
        resultado = self.env["shrimp.buzon"]._shrimp_asignar_usuarios_por_login({
            "contabilidad": ["nueva_contadora", "no_existe"],
            "facturacion@camaronmarket.com": "nueva_contadora",
            "inexistente": ["nueva_contadora"],
        })
        contabilidad = self.env.ref("shrimp_correo_entrante.buzon_contabilidad")
        self.assertIn(nuevo, contabilidad.user_ids)
        self.assertIn(nuevo, self.facturacion.user_ids)
        self.assertTrue(nuevo.has_group("shrimp_correo_entrante.group_correo_usuario"))
        self.assertEqual(resultado["faltan_logins"], ["no_existe"])
        self.assertEqual(resultado["faltan_buzones"], ["inexistente"])

    def test_crm_no_crea_oportunidades_solas_en_info(self):
        # crm le pone «info» al equipo de ventas; el módulo se lo quita.
        equipo = self.env.ref("sales_team.team_sales_department", raise_if_not_found=False)
        if equipo:
            self.assertNotEqual(equipo.alias_name, "info")

    # ---------------------------------------------------------------- envío

    def test_enviar(self):
        borrador_accion = self.Correo.with_user(self.lector).action_redactar()
        borrador = self.Correo.with_user(self.lector).browse(borrador_accion["res_id"])
        self.assertEqual(borrador.buzon_id, self.info)
        self.assertEqual(borrador.carpeta, "borradores")
        self.assertIn("Equipo de Información", borrador.body_html)
        borrador.write({
            "email_to": "Cliente Uno <uno@cliente.test>; dos@cliente.test",
            "email_cc": "tres@cliente.test",
            "email_bcc": "oculto@cliente.test",
            "asunto": "Oferta semanal",
            "body_html": "<p>Buenos días</p>" + borrador.body_html,
        })
        resultado = borrador.action_enviar()
        self.assertEqual(resultado["params"]["type"], "success")
        self.assertEqual(borrador.carpeta, "enviados")
        self.assertTrue(borrador.leido)
        self.assertTrue(borrador.message_id)
        mails = self.env["mail.mail"].sudo().search([("message_id", "=", borrador.message_id)])
        self.assertEqual(len(mails), 2)
        visible = mails.filtered(lambda m: "uno@cliente.test" in (m.email_to or ""))
        self.assertEqual(visible.email_from, '"CamaronMarket" <info@camaronmarket.com>')
        self.assertEqual(visible.reply_to, "info@camaronmarket.com")
        self.assertEqual(visible.email_cc, "tres@cliente.test")
        self.assertIn("dos@cliente.test", visible.email_to)
        self.assertIn("Buenos días", visible.body_html)
        self.assertNotIn("oculto", visible.email_to + (visible.email_cc or ""))
        oculto = mails - visible
        self.assertEqual(oculto.email_to, "oculto@cliente.test")
        self.assertIn("X-Forge-To", oculto.headers)

    def test_responder_responder_todos_y_reenviar(self):
        recibido = self._recibir(correo_multiparte(), "info@camaronmarket.com").filtered(
            lambda c: c.buzon_id == self.info).with_user(self.lector)

        respuesta = self.Correo.with_user(self.lector).browse(recibido.action_responder()["res_id"])
        self.assertEqual(respuesta.asunto, "RE: Cotización de camarón 16/20")
        self.assertEqual(respuesta.email_to, '"Ana Ruiz" <ana@cliente.test>')
        self.assertFalse(respuesta.email_cc)
        self.assertEqual(respuesta.in_reply_to, "<multi-1@cliente.test>")
        self.assertEqual(respuesta.references, "<multi-1@cliente.test>")
        self.assertIn("o_shrimp_cita", respuesta.body_html)
        self.assertIn("Adjunto la", respuesta.body_html)
        self.assertIn("Equipo de Información", respuesta.body_html)
        self.assertEqual(respuesta.hilo_id, recibido.hilo_id)
        self.assertFalse(respuesta.attachment_ids)

        todos = self.Correo.with_user(self.lector).browse(recibido.action_responder_todos()["res_id"])
        self.assertIn("pedro@cliente.test", todos.email_cc)
        self.assertIn("gerencia_general@camaronmarket.com", todos.email_cc)
        self.assertNotIn("info@camaronmarket.com", todos.email_cc or "")
        self.assertNotIn("info@camaronmarket.com", todos.email_to)

        reenvio = self.Correo.with_user(self.lector).browse(recibido.action_reenviar()["res_id"])
        self.assertEqual(reenvio.asunto, "RV: Cotización de camarón 16/20")
        self.assertFalse(reenvio.email_to)
        self.assertFalse(reenvio.in_reply_to)
        self.assertEqual(reenvio.attachment_ids.mapped("name"), ["cotización.pdf"])
        self.assertNotEqual(reenvio.attachment_ids, recibido.attachment_ids)

        # Enviar la respuesta: cabeceras de hilo para que el cliente la agrupe.
        respuesta.action_enviar()
        mail = self.env["mail.mail"].sudo().search([("message_id", "=", respuesta.message_id)])
        self.assertIn("'In-Reply-To': '<multi-1@cliente.test>'", mail.headers)
        self.assertEqual(mail.references, "<multi-1@cliente.test>")
        self.assertEqual(respuesta.carpeta, "enviados")
        # Responder a lo ya respondido no repite el prefijo.
        otra = self.Correo.with_user(self.lector).browse(respuesta.action_responder()["res_id"])
        self.assertEqual(otra.asunto, "RE: Cotización de camarón 16/20")
        self.assertEqual(otra.email_to, '"Ana Ruiz" <ana@cliente.test>')

    def test_error_de_envio_queda_a_la_vista(self):
        borrador = self.Correo.with_user(self.lector).create({
            "buzon_id": self.info.id, "email_to": "uno@cliente.test", "asunto": "Prueba", "body_html": "<p>x</p>"})
        servidor = type(self.env["ir.mail_server"])
        with patch.object(servidor, "_connect__", side_effect=ConnectionRefusedError("SMTP caído")):
            self.assertFalse(borrador.action_enviar())
        self.assertEqual(borrador.carpeta, "borradores")
        self.assertIn("SMTP caído", borrador.error_envio)
        self.assertFalse(self.env["mail.mail"].sudo().search([("subject", "=", "Prueba")]))
        # Direcciones mal escritas: error claro antes de intentar.
        borrador.email_to = "uno@cliente.test, esto-no-es-correo"
        with self.assertRaises(UserError):
            borrador.action_enviar()

    # ---------------------------------------------------------------- acciones

    def test_marcar_leido_al_abrir(self):
        correo = self._recibir(correo_multiparte(), "info@camaronmarket.com").filtered(
            lambda c: c.buzon_id == self.info).with_user(self.lector)
        Correo = self.Correo.with_user(self.lector).with_context(shrimp_correo_marcar_leido=True)
        # La lista no marca nada, aunque traiga un solo correo. Por call_kw,
        # como la llama el navegador (web_search_read es @api.model).
        call_kw(Correo, "web_search_read", [], {"domain": [("id", "=", correo.id)],
                                                "specification": {"leido": {}}, "context": {"shrimp_correo_marcar_leido": True}})
        self.assertFalse(correo.leido)
        # Sin la marca de la acción, tampoco.
        correo.web_read({"leido": {}})
        self.assertFalse(correo.leido)
        datos = call_kw(Correo, "web_read", [[correo.id]], {"specification": {"leido": {}},
                                                        "context": {"shrimp_correo_marcar_leido": True}})
        self.assertTrue(datos[0]["leido"])
        self.assertTrue(correo.leido)
        correo.action_marcar_no_leido()
        self.assertFalse(correo.leido)

    def test_carpetas(self):
        correo = self._recibir(TEXTO_LATIN1)
        self.facturacion.user_ids = [(4, self.lector.id)]
        correo = correo.with_user(self.lector)
        with self.assertRaises(UserError):
            correo.unlink()
        correo.action_archivar()
        self.assertEqual(correo.carpeta, "archivados")
        correo.action_papelera()
        self.assertEqual(correo.carpeta, "papelera")
        correo.action_restaurar()
        self.assertEqual(correo.carpeta, "archivados")
        correo.action_spam()
        correo.action_restaurar()
        self.assertEqual(correo.carpeta, "entrada")
        correo.action_destacar()
        self.assertTrue(correo.destacado)
        with self.assertRaises(UserError):
            correo.action_eliminar_definitivo()
        correo.action_papelera()
        correo.action_eliminar_definitivo()
        self.assertFalse(correo.exists())

    def test_crear_oportunidad_y_vincular_contacto(self):
        correo = self._recibir(correo_multiparte(), "info@camaronmarket.com").filtered(
            lambda c: c.buzon_id == self.info).with_user(self.lector)
        self.assertFalse(correo.partner_id)
        accion = correo.action_crear_oportunidad()
        lead = self.env["crm.lead"].browse(accion["res_id"])
        self.assertEqual(lead.name, "Cotización de camarón 16/20")
        self.assertEqual(lead.email_from, "ana@cliente.test")
        self.assertEqual(lead.contact_name, "Ana Ruiz")
        self.assertIn("cotización", lead.description)
        self.assertEqual((correo.vinculo_modelo, correo.vinculo_id), ("crm.lead", lead.id))
        self.assertIn(lead.name, correo.vinculo_nombre)
        nota = lead.message_ids.filtered(lambda m: "Creada desde el correo" in (m.body or ""))
        self.assertEqual(nota.attachment_ids.mapped("name"), ["cotización.pdf"])
        # La respuesta a ese correo queda también en el chatter de la oportunidad.
        respuesta = self.Correo.with_user(self.lector).browse(correo.action_responder()["res_id"])
        respuesta.action_enviar()
        mail = self.env["mail.mail"].sudo().search([("message_id", "=", respuesta.message_id)])
        self.assertEqual((mail.model, mail.res_id), ("crm.lead", lead.id))

        accion = correo.action_vincular_contacto()
        socio = self.env["res.partner"].browse(accion["res_id"])
        self.assertEqual(socio.email, "ana@cliente.test")
        self.assertEqual(socio.name, "Ana Ruiz")
        self.assertEqual(correo.partner_id, socio)
        # Un segundo clic abre el mismo contacto, no crea otro.
        self.assertEqual(correo.action_vincular_contacto()["res_id"], socio.id)

    def test_panel_con_no_leidos(self):
        self._recibir(correo_multiparte(), "info@camaronmarket.com")
        Correo = self.Correo.with_user(self.lector)
        valores = call_kw(Correo, "search_panel_select_range", ["buzon_id"], {"expand": True})["values"]
        self.assertEqual([v["display_name"] for v in valores], ["Información (1)"])
        carpetas = {v["id"]: v["display_name"] for v in Correo.search_panel_select_range("carpeta")["values"]}
        self.assertEqual(carpetas.get("entrada"), "Bandeja de entrada (1)")
