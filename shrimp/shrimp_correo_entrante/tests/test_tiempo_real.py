import json

from odoo.tests import TransactionCase, new_test_user, tagged

from .test_bandeja import correo_multiparte

NUEVO = "shrimp_correo/nuevo"
ACTUALIZADO = "shrimp_correo/actualizado"


@tagged("post_install", "-at_install")
class TestTiempoReal(TransactionCase):
    """Los avisos del bus que mantienen la bandeja al día en el navegador."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Correo = cls.env["shrimp.correo"]
        cls.env["shrimp.correo"].sudo().search([]).unlink()
        cls.info = cls.env.ref("shrimp_correo_entrante.buzon_info")
        cls.gerencia = cls.env.ref("shrimp_correo_entrante.buzon_gerencia_general")
        cls.facturacion = cls.env.ref("shrimp_correo_entrante.buzon_facturacion")
        grupo = "base.group_user,shrimp_correo_entrante.group_correo_usuario"
        cls.lector = new_test_user(cls.env, login="tr_lector_info", groups=grupo, name="Lectora Info")
        cls.de_facturacion = new_test_user(cls.env, login="tr_facturacion", groups=grupo,
                                           name="Solo Facturación")
        cls.sin_buzon = new_test_user(cls.env, login="tr_sin_buzon", groups=grupo, name="Sin Buzón")
        cls.sin_correo = new_test_user(cls.env, login="tr_sin_correo", groups="base.group_user",
                                       name="Sin la app de correo")
        cls.jefe = new_test_user(cls.env, login="tr_jefe_correo",
                                 groups="base.group_user,shrimp_correo_entrante.group_correo_admin")
        cls.info.user_ids = [(4, cls.lector.id)]
        cls.facturacion.user_ids = [(4, cls.de_facturacion.id)]

    def setUp(self):
        super().setUp()
        # Lo que haya quedado de la preparación no cuenta.
        self.env.cr.precommit.run()
        self.ultimo = self.env["bus.bus"].sudo().search([], order="id desc", limit=1).id

    def _avisos(self, tipo):
        """{partner_id: [payload, ...]} de los avisos `tipo` enviados desde setUp."""
        self.env.cr.precommit.run()  # el bus crea sus registros al confirmar
        avisos = {}
        for registro in self.env["bus.bus"].sudo().search([("id", ">", self.ultimo)], order="id"):
            mensaje = json.loads(registro.message)
            if mensaje["type"] != tipo:
                continue
            canal = json.loads(registro.channel)
            self.assertEqual(canal[:2], [self.env.cr.dbname, "res.partner"])
            avisos.setdefault(canal[2], []).append(mensaje["payload"])
        return avisos

    def _recibir(self, crudo, para=None):
        return self.Correo.sudo()._shrimp_recibir(crudo, para)["correos"]

    def test_correo_nuevo_avisa_a_quien_ve_el_buzon(self):
        crudo = correo_multiparte(para="info@camaronmarket.com", message_id="<tr-1@cliente.test>")
        correos = self._recibir(crudo, "info@camaronmarket.com")
        en_info = correos.filtered(lambda c: c.buzon_id == self.info)
        en_gerencia = correos.filtered(lambda c: c.buzon_id == self.gerencia)
        self.assertTrue(en_info and en_gerencia)

        avisos = self._avisos(NUEVO)
        for aviso in sum(avisos.values(), []):
            self.assertIsInstance(aviso.pop("creado"), int)
        # La lectora de info@ se entera solo del correo de info@.
        self.assertEqual(avisos[self.lector.partner_id.id], [{
            "id": en_info.id,
            "buzon_id": self.info.id,
            "buzon_nombre": self.info.name,
            "de": "Ana Ruiz",
            "asunto": "Cotización de camarón 16/20",
            "tiene_adjuntos": True,
        }])
        # El administrador del correo ve todos los buzones: los dos avisos.
        self.assertEqual(sorted(a["id"] for a in avisos[self.jefe.partner_id.id]),
                         sorted(correos.ids))
        # Quien no ve esos buzones no recibe nada (ni sabe que llegó algo).
        for usuario in (self.sin_buzon, self.sin_correo, self.de_facturacion):
            self.assertNotIn(usuario.partner_id.id, avisos, usuario.login)

    def test_correo_repetido_no_vuelve_a_avisar(self):
        crudo = correo_multiparte(para="facturacion@camaronmarket.com", message_id="<tr-2@cliente.test>")
        self.assertTrue(self._recibir(crudo))
        self.assertEqual(len(self._avisos(NUEVO)[self.de_facturacion.partner_id.id]), 1)
        self.ultimo = self.env["bus.bus"].sudo().search([], order="id desc", limit=1).id
        # El Worker reintenta la misma entrega: no hay correo nuevo ni aviso.
        self.assertFalse(self._recibir(crudo))
        self.assertFalse(self._avisos(NUEVO))

    def test_correo_sin_buzon_no_avisa(self):
        crudo = correo_multiparte(para="alguien@otro.test", message_id="<tr-3@cliente.test>")
        crudo = crudo.replace(b"gerencia_general@camaronmarket.com", b"nadie@otro.test")
        self.assertFalse(self._recibir(crudo))
        self.assertFalse(self._avisos(NUEVO))

    def test_leido_y_no_leido_avisan_una_vez_por_usuario(self):
        correos = self._recibir(correo_multiparte(message_id="<tr-4@cliente.test>"), "info@camaronmarket.com")
        correos |= self._recibir(correo_multiparte(message_id="<tr-5@cliente.test>"), "info@camaronmarket.com")
        de_info = correos.filtered(lambda c: c.buzon_id == self.info)
        self.assertEqual(len(de_info), 2)
        self.ultimo = self.env["bus.bus"].sudo().search([], order="id desc", limit=1).id

        de_info.with_user(self.lector).action_marcar_leido()
        avisos = self._avisos(ACTUALIZADO)
        # Dos correos marcados, un solo aviso por usuario.
        self.assertEqual(avisos[self.lector.partner_id.id],
                         [{"buzon_ids": [self.info.id], "correo_ids": sorted(de_info.ids)}])
        self.assertEqual(len(avisos[self.jefe.partner_id.id]), 1)
        for usuario in (self.sin_buzon, self.sin_correo, self.de_facturacion):
            self.assertNotIn(usuario.partner_id.id, avisos, usuario.login)

        de_info[0].with_user(self.lector).action_marcar_no_leido()
        self.assertIn(self.lector.partner_id.id, self._avisos(ACTUALIZADO))

    def test_enviar_avisa_a_los_del_buzon(self):
        accion = self.Correo.with_user(self.lector).action_redactar()
        borrador = self.Correo.with_user(self.lector).browse(accion["res_id"])
        borrador.write({"email_to": "cliente@cliente.test", "asunto": "Oferta"})
        self.ultimo = self.env["bus.bus"].sudo().search([], order="id desc", limit=1).id
        # Guardar un borrador no mueve contadores: no hay aviso.
        borrador.write({"asunto": "Oferta semanal"})
        self.assertFalse(self._avisos(ACTUALIZADO))

        resultado = borrador.action_enviar()
        self.assertEqual(resultado["params"]["type"], "success")
        self.assertEqual(borrador.carpeta, "enviados")
        avisos = self._avisos(ACTUALIZADO)
        for usuario in (self.lector, self.jefe):
            self.assertEqual(avisos[usuario.partner_id.id],
                             [{"buzon_ids": [self.info.id], "correo_ids": [borrador.id]}])
        self.assertNotIn(self.sin_buzon.partner_id.id, avisos)
        self.assertNotIn(self.de_facturacion.partner_id.id, avisos)
        # Lo recibido no se anuncia como nuevo cuando se envía algo.
        self.assertFalse(self._avisos(NUEVO))

    def test_sin_cambios_que_importen_no_avisa(self):
        correo = self._recibir(correo_multiparte(message_id="<tr-6@cliente.test>"), "info@camaronmarket.com")
        correo = correo.filtered(lambda c: c.buzon_id == self.info)
        self.ultimo = self.env["bus.bus"].sudo().search([], order="id desc", limit=1).id
        correo.with_user(self.lector).action_destacar()
        self.assertFalse(self._avisos(ACTUALIZADO))
