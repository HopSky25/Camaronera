import datetime
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, csrf_de


def _socios(env, sufijo):
    """Empacadora y camaronera con usuario de portal, operador y gestor."""
    Socio = env["res.partner"]
    portal = env.ref("base.group_portal")
    emp = Socio.create({
        "name": "Empacadora ag%s" % sufijo, "is_company": True,
        "email": "emp.ag%s@prueba.test" % sufijo, "vat_or_id": "09333000%s001" % sufijo,
        "shrimp_user_type": "empacadora", "shrimp_account_state": "approved"})
    cam = Socio.create({
        "name": "Camaronera ag%s" % sufijo, "is_company": True,
        "email": "cam.ag%s@prueba.test" % sufijo, "vat_or_id": "09333001%s001" % sufijo,
        "shrimp_user_type": "camaronera", "farm_razon_social": "Cam ag S.A.",
        "farm_representante": "R", "farm_telefono": "04-1", "farm_ubicacion": "Guayas"})
    Users = env["res.users"].with_context(no_reset_password=True)
    u_emp = Users.create({
        "name": emp.name, "login": "emp.ag%s" % sufijo, "partner_id": emp.id,
        "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
    u_oper = Users.create({
        "name": "Operador ag%s" % sufijo, "login": "oper.ag%s" % sufijo, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("shrimp_marketplace.group_shrimp_user").id])]})
    u_gestor = Users.create({
        "name": "Gestor ag%s" % sufijo, "login": "gestor.ag%s" % sufijo, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("shrimp_marketplace.group_shrimp_manager").id])]})
    return emp, cam, u_emp, u_oper, u_gestor


@tagged("post_install", "-at_install", "shrimp_packer")
class TestAguajeLista(TransactionCase):
    """La lista de precios rige para un aguaje del calendario: obligatorio al
    publicar, y nunca uno que ya pasó. El calendario lo mantiene la
    plataforma."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.emp, cls.cam, cls.u_emp, cls.u_oper, cls.u_gestor = _socios(env, "1")
        cls.Aguaje = env["shrimp.aguaje"]
        cls.hoy = fields.Date.context_today(cls.Aguaje)
        cls.pasado = cls.Aguaje.create({
            "name": "Aguaje viejo ag", "year": 2001, "numero": 1, "fase": "nueva",
            "date_from": datetime.date(2001, 1, 1), "date_to": datetime.date(2001, 1, 4)})
        proximos = cls.Aguaje.seleccionables()
        assert len(proximos) >= 2, "el calendario sembrado debe tener aguajes próximos"
        cls.prox1, cls.prox2 = proximos[0], proximos[1]
        cls.talla = env.ref("shrimp_marketplace.size_entero_3040")

    def _vals(self, **extra):
        vals = {"name": "Lista ag", "issuer_partner_id": self.emp.id,
                "recipient_ids": [(6, 0, self.cam.ids)],
                "line_ids": [(0, 0, {"size_grade_id": self.talla.id, "uom": "kg",
                                     "quality": "ab", "price": 4.0})]}
        vals.update(extra)
        return vals

    def _L(self):
        # Como el portal: el usuario de la empacadora, en sudo.
        return self.env["shrimp.price.list"].with_user(self.u_emp).sudo()

    # ------------------------------------------------------------ lista
    def test_crear_con_aguaje_pasado_falla(self):
        with self.assertRaisesRegex(ValidationError, "ya pasó"):
            self._L().create(self._vals(aguaje_id=self.pasado.id))

    def test_crear_con_aguaje_proximo_y_propone_despacho(self):
        lista = self._L().create(self._vals(aguaje_id=self.prox1.id))
        self.assertEqual(lista.aguaje_id, self.prox1)
        self.assertEqual(lista.dispatch_from, max(self.prox1.date_from, self.hoy))
        self.assertIn(self.prox1.name, lista.aguaje_txt)
        lista.action_publish()
        self.assertEqual(lista.state, "published")

    def test_aguaje_en_curso_se_permite(self):
        # Con el reloj dentro de un aguaje: ese aguaje está en curso y vale.
        dentro = self.prox1.date_from + timedelta(days=1)
        with freeze_time(dentro):
            self.prox1.invalidate_recordset(["estado", "es_actual"])
            self.assertEqual(self.prox1.estado, "actual")
            self.assertIn(self.prox1, self.Aguaje.seleccionables())
            lista = self._L().create(self._vals(aguaje_id=self.prox1.id))
            self.assertEqual(lista.dispatch_from, dentro)
            lista.action_publish()
        # Y en cuanto termina, ya no se puede elegir.
        with freeze_time(self.prox1.date_to + timedelta(days=1)):
            self.prox1.invalidate_recordset(["estado", "es_actual"])
            self.assertEqual(self.prox1.estado, "pasado")
            self.assertNotIn(self.prox1, self.Aguaje.seleccionables())
            with self.assertRaisesRegex(ValidationError, "ya pasó"):
                self._L().create(self._vals(aguaje_id=self.prox1.id))

    def test_publicar_exige_aguaje(self):
        lista = self._L().create(self._vals())
        with self.assertRaisesRegex(ValidationError, "Elige el aguaje"):
            lista.action_publish()
        lista.write({"aguaje_id": self.prox1.id})
        lista.action_publish()
        self.assertEqual(lista.state, "published")

    def test_cambiar_a_aguaje_pasado_falla(self):
        lista = self._L().create(self._vals(aguaje_id=self.prox1.id))
        with self.assertRaisesRegex(ValidationError, "ya pasó"):
            lista.write({"aguaje_id": self.pasado.id})

    def test_historica_sigue_valiendo(self):
        # Cargada como historia (superusuario, como la demo y las migraciones).
        vieja = self.env["shrimp.price.list"].create(self._vals(
            aguaje_id=self.pasado.id, dispatch_from=datetime.date(2001, 1, 2),
            state="archived"))
        self.assertEqual(vieja.aguaje_id, self.pasado)
        sin = self.env["shrimp.price.list"].create(self._vals(state="archived"))
        # La empacadora la sigue pudiendo tocar sin cambiarle el aguaje...
        vieja_p = vieja.with_user(self.u_emp).sudo()
        vieja_p.write({"name": "Vieja renombrada", "aguaje_id": self.pasado.id})
        sin.with_user(self.u_emp).sudo().write({"name": "Sin aguaje renombrada"})
        self.assertEqual(vieja.name, "Vieja renombrada")
        # ...pero no volver a publicarla para un aguaje que ya pasó.
        vieja_p.action_back_to_draft()
        with self.assertRaisesRegex(ValidationError, "ya pasó"):
            vieja_p.action_publish()

    def test_despacho_contradictorio(self):
        with self.assertRaisesRegex(ValidationError, "ya terminó"):
            self._L().create(self._vals(
                aguaje_id=self.prox1.id, dispatch_from=self.prox1.date_to + timedelta(days=1)))
        with self.assertRaisesRegex(ValidationError, "antes de que empiece"):
            self._L().create(self._vals(
                aguaje_id=self.prox2.id, open_ended=False,
                dispatch_from=self.prox2.date_from - timedelta(days=6),
                dispatch_to=self.prox2.date_from - timedelta(days=1)))
        # Una ventana de dos semanas que arranca en el aguaje sí vale.
        lista = self._L().create(self._vals(
            aguaje_id=self.prox1.id, open_ended=False,
            dispatch_from=max(self.prox1.date_from, self.hoy),
            dispatch_to=max(self.prox1.date_from, self.hoy) + timedelta(days=13)))
        self.assertTrue(lista)

    def test_copia_propone_el_siguiente_aguaje(self):
        lista = self._L().create(self._vals(aguaje_id=self.prox1.id))
        lista.action_publish()
        accion = lista.action_duplicate_for_next()
        nueva = self.env["shrimp.price.list"].browse(accion["res_id"])
        self.assertEqual(nueva.state, "draft")
        self.assertEqual(nueva.aguaje_id, self.prox2)
        self.assertEqual(nueva.dispatch_from, self.prox2.date_from)
        self.assertEqual(len(nueva.line_ids), 1)
        # Copiar una lista vieja propone el de ahora, nunca uno pasado.
        vieja = self.env["shrimp.price.list"].create(self._vals(
            aguaje_id=self.pasado.id, state="archived"))
        copia = vieja.with_user(self.u_emp).sudo().copy(vieja._valores_copia_siguiente())
        self.assertEqual(copia.aguaje_id, self.prox1)
        self.assertGreaterEqual(copia.aguaje_id.date_to, self.hoy)

    def test_cron_no_publica_con_aguaje_pasado(self):
        lista = self.env["shrimp.price.list"].create(self._vals(
            aguaje_id=self.pasado.id, auto_publish=True, auto_publish_date=self.hoy))
        buena = self.env["shrimp.price.list"].create(self._vals(
            aguaje_id=self.prox1.id, auto_publish=True, auto_publish_date=self.hoy))
        with mute_logger("odoo.addons.shrimp_packer.models.shrimp_price_list"):
            self.env["shrimp.price.list"]._cron_auto_publish()
        self.assertEqual(lista.state, "draft")
        self.assertEqual(buena.state, "published")

    # ------------------------------------------------------------ permisos
    def test_portal_y_operador_solo_leen(self):
        for usuario in (self.u_emp, self.u_oper):
            A = self.Aguaje.with_user(usuario)
            self.assertTrue(A.search_count([]))
            self.assertTrue(self.prox1.with_user(usuario).etiqueta)
            with self.assertRaises(AccessError):
                self.prox1.with_user(usuario).write({"notes": "no"})
            with self.assertRaises(AccessError):
                A.create({"name": "x", "year": 2040, "numero": 1, "fase": "nueva",
                          "date_from": datetime.date(2040, 1, 1),
                          "date_to": datetime.date(2040, 1, 3)})
            with self.assertRaises(AccessError):
                self.pasado.with_user(usuario).unlink()
        publico = self.env.ref("base.public_user")
        self.assertTrue(self.Aguaje.with_user(publico).search_count([]))

    def test_gestor_crud_y_marca_manual(self):
        A = self.Aguaje.with_user(self.u_gestor)
        nuevo = A.create({"name": "Aguaje gestor", "year": 2041, "numero": 1, "fase": "llena",
                          "date_from": datetime.date(2041, 1, 1),
                          "date_to": datetime.date(2041, 1, 4), "origen": "calculado"})
        nuevo.write({"date_to": datetime.date(2041, 1, 5)})
        self.assertEqual(nuevo.origen, "manual")
        nuevo.write({"origen": "inocar"})
        nuevo.unlink()
        self.assertFalse(nuevo.exists())

    def test_sin_solapes(self):
        a = self.prox1
        with self.assertRaisesRegex(ValidationError, "solaparse"):
            self.Aguaje.create({"name": "Pisa", "year": a.year, "numero": 900, "fase": "nueva",
                                "date_from": a.date_to, "date_to": a.date_to + timedelta(days=1)})
        with self.assertRaises(Exception), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.Aguaje.create({"name": "Al revés", "year": 2042, "numero": 1, "fase": "nueva",
                                "date_from": datetime.date(2042, 1, 5),
                                "date_to": datetime.date(2042, 1, 1)})

    # ------------------------------------------------------------ generador
    def test_generador_sin_duplicar_ni_pisar_lo_manual(self):
        anio = 2033
        creados = self.Aguaje.sembrar_anio(anio)
        self.assertGreaterEqual(len(creados), 24)
        self.assertEqual(len(self.Aguaje.sembrar_anio(anio)), len(creados))
        self.assertEqual(self.Aguaje.search_count([("year", "=", anio)]), len(creados))
        # El gestor corrige uno (pasa a manual) y borra otro.
        ajustado, borrado = creados[3], creados[5]
        nuevo_fin = ajustado.date_to + timedelta(days=1)
        ajustado.with_user(self.u_gestor).write({"date_to": nuevo_fin, "notes": "INOCAR"})
        self.assertEqual(ajustado.origen, "manual")
        fechas_borrado = (borrado.date_from, borrado.date_to)
        borrado.with_user(self.u_gestor).unlink()
        # Sin completar: el año ya tiene aguajes, no se toca.
        self.Aguaje.sembrar_anio(anio)
        self.assertEqual(self.Aguaje.search_count([("year", "=", anio)]), len(creados) - 1)
        # Completando: vuelve solo el que faltaba; el manual queda como estaba.
        self.Aguaje.sembrar_anio(anio, completar=True)
        self.assertEqual(self.Aguaje.search_count([("year", "=", anio)]), len(creados))
        self.assertTrue(self.Aguaje.search([("date_from", "=", fechas_borrado[0]),
                                            ("date_to", "=", fechas_borrado[1])]))
        self.assertEqual(ajustado.date_to, nuevo_fin)
        self.assertEqual(ajustado.origen, "manual")
        self.assertEqual(ajustado.notes, "INOCAR")
        # Y una segunda pasada no añade nada.
        self.Aguaje.sembrar_anio(anio, completar=True)
        self.assertEqual(self.Aguaje.search_count([("year", "=", anio)]), len(creados))

    def test_asistente_del_gestor(self):
        W = self.env["shrimp.aguaje.generar"].with_user(self.u_gestor)
        W.create({"year": 2034}).action_generar()
        n = self.Aguaje.search_count([("year", "=", 2034)])
        self.assertGreaterEqual(n, 24)
        W.create({"year": 2034, "completar": True}).action_generar()
        self.assertEqual(self.Aguaje.search_count([("year", "=", 2034)]), n)
        with self.assertRaises(AccessError):
            self.env["shrimp.aguaje.generar"].with_user(self.u_oper).create({"year": 2035})

    def test_cron_asegura_aguajes_proximos(self):
        with freeze_time("2037-11-20"):
            hoy = fields.Date.context_today(self.Aguaje)
            self.Aguaje._cron_asegurar_calendario()
            futuros = self.Aguaje.search_count([("date_to", ">=", hoy)])
            self.assertGreaterEqual(futuros, self.Aguaje.MINIMO_FUTUROS)
            total = self.Aguaje.search_count([])
            self.Aguaje._cron_asegurar_calendario()
            self.assertEqual(self.Aguaje.search_count([]), total)

    def test_fases_de_fin_de_anio(self):
        # 2027 perdía la luna nueva de finales de diciembre.
        from odoo.addons.shrimp_packer.models.shrimp_aguaje import _lunas
        self.assertEqual(_lunas(2027)[-1][0], datetime.date(2027, 12, 27))

    def test_etiqueta(self):
        a = self.Aguaje.create({
            "name": "Aguaje etiqueta", "year": self.hoy.year + 20, "numero": 18, "fase": "nueva",
            "date_from": datetime.date(self.hoy.year + 20, 10, 5),
            "date_to": datetime.date(self.hoy.year + 20, 10, 9),
            "peak_from": datetime.date(self.hoy.year + 20, 10, 6),
            "peak_to": datetime.date(self.hoy.year + 20, 10, 7)})
        self.assertEqual(a.etiqueta, "Aguaje 18 · del 05/10 al 09/10/%s (máximo 06–07)"
                         % (self.hoy.year + 20))


@tagged("post_install", "-at_install", "shrimp_packer")
class TestAguajePortal(HttpCase):
    """El portal de la empacadora: selector obligatorio, sin aguajes pasados."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.emp, cls.cam, cls.u_emp, _o, _g = _socios(env, "2")
        cls.pasado = env["shrimp.aguaje"].create({
            "name": "Aguaje viejo portal", "year": 2002, "numero": 1, "fase": "nueva",
            "date_from": datetime.date(2002, 1, 1), "date_to": datetime.date(2002, 1, 4)})
        cls.prox1, cls.prox2 = env["shrimp.aguaje"].seleccionables()[:2]

    def _listas(self):
        return self.env["shrimp.price.list"].search([("issuer_partner_id", "=", self.emp.id)])

    def _guardar(self, token, **extra):
        data = {"csrf_token": token, "name": "Semana portal", "open_ended": "1",
                "recipient_refs": self.cam.uuid_ref}
        data.update(extra)
        return self.url_open("/marketplace/price-lists/save", data=data)

    def test_formulario_y_guardado(self):
        self.authenticate(self.u_emp.login, CLAVE)
        pagina = self.url_open("/marketplace/price-lists/new")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn('name="aguaje_ref"', pagina.text)
        self.assertIn(self.prox1.uuid_ref, pagina.text)
        self.assertNotIn(self.pasado.uuid_ref, pagina.text)
        self.assertIn(self.prox1.etiqueta, pagina.text)
        token = csrf_de(pagina.text)

        # Sin aguaje: no se crea.
        r = self._guardar(token)
        self.assertFalse(self._listas())
        self.assertIn("Elige el aguaje", r.text)
        # Con uno pasado (manipulando el formulario): tampoco.
        r = self._guardar(token, aguaje_ref=self.pasado.uuid_ref)
        self.assertFalse(self._listas())
        self.assertIn("ya pasó", r.text)
        # Con uno próximo: sí, y el despacho sale del aguaje.
        self._guardar(token, aguaje_ref=self.prox1.uuid_ref)
        lista = self._listas()
        self.assertEqual(len(lista), 1)
        self.assertEqual(lista.aguaje_id, self.prox1)
        self.assertTrue(lista.dispatch_from)

        # La edición no ofrece pasados y no deja cambiar a uno pasado.
        editar = self.url_open("/marketplace/price-lists/%s/edit" % lista.uuid_ref)
        self.assertNotIn(self.pasado.uuid_ref, editar.text)
        r = self._guardar(token, ref=lista.uuid_ref, aguaje_ref=self.pasado.uuid_ref)
        self.assertIn("ya pasó", r.text)
        self.assertEqual(lista.aguaje_id, self.prox1)

        # Carga por Excel: también con el aguaje, y nunca uno pasado.
        r = self.url_open("/marketplace/price-lists/%s/upload" % lista.uuid_ref,
                          data={"csrf_token": token, "aguaje_ref": self.pasado.uuid_ref})
        self.assertIn("ya pasó", r.text)
        self.assertEqual(lista.aguaje_id, self.prox1)

        # Copiar: la copia nace para el aguaje siguiente.
        self.url_open("/marketplace/price-lists/%s/duplicate" % lista.uuid_ref,
                      data={"csrf_token": token})
        copia = self._listas() - lista
        self.assertEqual(len(copia), 1)
        self.assertEqual(copia.aguaje_id, self.prox2)

        # «Rige para» en el listado de la empacadora.
        listado = self.url_open("/marketplace/price-lists")
        self.assertIn("Rige para", listado.text)
