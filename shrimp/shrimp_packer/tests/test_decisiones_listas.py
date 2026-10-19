"""Decisiones D-15 y D-48 (auditoría de procesos) sobre las listas de precios.

D-15
* Una lista publicada no se edita (renglones, precios, bonificaciones,
  destinatarios, cabecera) ni vuelve a borrador ni se elimina.
* «Duplicar y corregir» crea un borrador igual; al publicarlo reemplaza a la
  original (archivada como «reemplazada»).
* Un compromiso «a lista» guarda la foto de precios al firmarse: las listas
  posteriores no lo alteran. Sin lista al firmar, manda la del día de cosecha.
* No se quita de una corrección a un destinatario con compromisos «a lista»
  vivos, ni de una declaración publicada a una empacadora comprometida.

D-48
* Una lista «hasta segunda orden» caduca a los N días (Ajustes) salvo que se
  renueve; aviso a la empacadora unos días antes; el botón Renovar extiende.
* La primera lista que recibe un productor avisa igual que las siguientes.
* No se avisa a socios que no pueden operar (ME-24).
"""
from datetime import date, timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, crear_socios, csrf_de, producto
from odoo.addons.shrimp_packer.models.shrimp_product import LB_POR_KG

HOY = "2026-10-16 15:00:00"


@freeze_time(HOY)
@tagged("post_install", "-at_install", "shrimp_packer")
class TestDecisionesListas(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "dl")
        cls.cam = cls.s["cam"]
        Socio = env["res.partner"]
        cls.cam2 = Socio.create({
            "name": "Camaronera dl2", "is_company": True, "email": "cam2.dl@prueba.test",
            "vat_or_id": "0933388002001", "shrimp_user_type": "camaronera",
            "farm_razon_social": "Cam dl2 S.A.", "farm_representante": "R",
            "farm_telefono": "04-1", "farm_ubicacion": "Guayas"})
        cls.emp = Socio.create({
            "name": "Empacadora dl", "is_company": True, "email": "emp.dl@prueba.test",
            "vat_or_id": "0933388001001", "shrimp_user_type": "empacadora",
            "shrimp_account_state": "approved",
            "emp_razon_social": "Empacadora dl S.A.", "emp_capacidad_lb_dia": 50000})
        cls.talla = env.ref("shrimp_marketplace.size_entero_3040")
        cls.Lista = env["shrimp.price.list"]

    # ------------------------------------------------------------------
    def _lista(self, precio=4.4, destinatarios=None, publicar=True, **extra):
        vals = {"name": "Semana dl", "issuer_partner_id": self.emp.id,
                "recipient_ids": [(6, 0, (destinatarios or self.cam).ids)],
                "line_ids": [(0, 0, {"size_grade_id": self.talla.id, "uom": "kg",
                                     "quality": "ab", "price": precio})],
                "bonus_ids": [(0, 0, {"name": "SMALL", "amount": 0.05})]}
        vals.update(extra)
        lista = self.Lista.create(vals)
        if publicar:
            lista.action_publish()
        return lista

    def _corregir(self, lista, precio):
        nueva = self.Lista.browse(lista.action_duplicate_correct()["res_id"])
        nueva.line_ids.write({"price": precio})
        return nueva

    def _declaracion(self, dias=20, empacadoras=None):
        env = self.env
        pond = env["shrimp.partner.pond"].create({"partner_id": self.cam.id, "name": "P-dl"})
        esperada = date.today() + timedelta(days=dias)
        d = env["shrimp.harvest.forecast"].create({
            "farmer_partner_id": self.cam.id, "expected_date": esperada,
            "expected_lb": 30000.0, "presentation": "entero",
            "size_grade_id": self.talla.id, "pond_id": pond.id,
            "recipient_ids": [(6, 0, (empacadoras or self.emp).ids)]})
        d.action_publish(actor=self.cam)
        c = env["shrimp.harvest.commitment"].create({
            "forecast_id": d.id, "packer_partner_id": self.emp.id,
            "committed_lb": 20000.0, "price_mode": "lista", "price_floor_per_lb": 1.0,
            "valid_until": esperada - timedelta(days=1)})
        return d, c

    # ================================================================ D-15
    def test_publicada_no_se_edita(self):
        lista = self._lista()
        linea = lista.line_ids
        for intento in (
            lambda: linea.write({"price": 1.0}),
            lambda: lista.write({"line_ids": [(1, linea.id, {"price": 1.0})]}),
            lambda: self.env["shrimp.price.list.line"].create({
                "price_list_id": lista.id, "size_grade_id": self.env.ref(
                    "shrimp_marketplace.size_entero_4050").id,
                "uom": "kg", "quality": "ab", "price": 3.0}),
            lambda: linea.unlink(),
            lambda: lista.bonus_ids.write({"amount": 9.0}),
            lambda: lista.write({"recipient_ids": [(4, self.cam2.id)]}),
            lambda: lista.write({"name": "Otra"}),
            lambda: lista.write({"advance_pct": 50}),
            lambda: lista.action_back_to_draft(),
            lambda: lista.write({"state": "draft"}),
            lambda: lista.unlink(),
        ):
            with self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    intento()
        self.assertEqual(lista.state, "published")
        self.assertAlmostEqual(lista.line_ids.price, 4.4)
        # Archivar sí se puede, pero después tampoco se edita ni se republica.
        lista.action_archive_list()
        self.assertEqual(lista.archive_reason, "manual")
        with self.assertRaises(ValidationError):
            lista.line_ids.write({"price": 2.0})
        with self.assertRaises(ValidationError):
            lista.action_publish()

    def test_borrador_si_se_edita_y_elimina(self):
        lista = self._lista(publicar=False)
        lista.line_ids.write({"price": 5.0})
        lista.write({"recipient_ids": [(4, self.cam2.id)], "name": "Corregida"})
        lista.unlink()

    def test_duplicar_y_corregir_reemplaza(self):
        original = self._lista()
        nueva = self._corregir(original, 5.0)
        self.assertEqual(nueva.state, "draft")
        self.assertEqual(nueva.supersedes_id, original)
        self.assertEqual(nueva.recipient_ids, original.recipient_ids)
        self.assertEqual(nueva.name, original.name)
        self.assertEqual(len(nueva.bonus_ids), 1)
        # Pedirla otra vez devuelve el mismo borrador, no uno nuevo.
        self.assertEqual(original.action_duplicate_correct()["res_id"], nueva.id)
        self.assertTrue(original.is_current, "la original rige mientras se corrige")
        nueva.action_publish()
        self.assertEqual(original.state, "archived")
        self.assertEqual(original.archive_reason, "replaced")
        self.assertEqual(original.superseded_by_id, nueva)
        self.assertEqual(self.Lista.visibles_para(self.cam), nueva)
        # Solo se corrige lo publicado.
        with self.assertRaises(ValidationError):
            original.action_duplicate_correct()

    def test_reemplaza_aunque_no_compita_en_ventana(self):
        """La corrección reemplaza a la original aunque sea de otra ventana."""
        original = self._lista()
        nueva = self._corregir(original, 5.0)
        nueva.write({"dispatch_from": date.today() + timedelta(days=5)})
        nueva.action_publish()
        self.assertTrue(nueva.is_upcoming)
        self.assertEqual(original.state, "archived")

    def test_foto_de_precios_al_firmar(self):
        lista = self._lista(precio=4.4)
        d, c = self._declaracion()
        self.assertFalse(c.price_snapshot_at)
        c.action_accept(actor=self.cam)
        self.assertEqual(c.price_snapshot_list_id, lista)
        esperado = 4.4 / LB_POR_KG
        self.assertAlmostEqual(c.price_snapshot[str(self.talla.id)], esperado, places=5)
        # La empacadora corrige su lista a la baja y la publica: no toca lo firmado.
        nueva = self._corregir(lista, 2.2)
        nueva.action_publish()
        d.action_registrar_cosecha(actual_lb=30000.0, actual_size_grade_id=self.talla.id,
                                   actual_date=d.expected_date, actor=self.cam)
        self.assertEqual(c.state, "honored")
        self.assertAlmostEqual(c.settled_price_per_lb, esperado, places=2)  # monetario, 2 decimales
        self.assertEqual(c.price_list_id, lista)
        self.assertFalse(c.floor_applied)

    def test_foto_talla_no_cotizada_paga_piso(self):
        self._lista(precio=4.4)
        d, c = self._declaracion()
        c.action_accept(actor=self.cam)
        otra = self.env.ref("shrimp_marketplace.size_entero_4050")
        precio, _pasos, piso, _lista = c._precio_liquidado(otra, d.expected_date)
        self.assertTrue(piso)
        self.assertAlmostEqual(precio, 1.0)

    def test_sin_lista_al_firmar_usa_la_del_dia(self):
        d, c = self._declaracion()
        c.action_accept(actor=self.cam)
        self.assertTrue(c.price_snapshot_at)
        self.assertFalse(c.price_snapshot)
        lista = self._lista(precio=6.6)
        precio, _p, piso, usada = c._precio_liquidado(self.talla, d.expected_date)
        self.assertEqual(usada, lista)
        self.assertAlmostEqual(precio, 6.6 / LB_POR_KG, places=4)
        self.assertFalse(piso)

    def test_no_quitar_destinatario_con_compromiso(self):
        original = self._lista(destinatarios=self.cam | self.cam2)
        _d, _c = self._declaracion()  # compromiso «a lista» enviado a la camaronera
        nueva = self.Lista.browse(original.action_duplicate_correct()["res_id"])
        with self.assertRaisesRegex(ValidationError, "compromisos"), self.env.cr.savepoint():
            nueva.write({"recipient_ids": [(3, self.cam.id)]})
        self.assertIn(self.cam, nueva.recipient_ids)
        # Quitar a quien no tiene compromisos sí se puede.
        nueva.write({"recipient_ids": [(3, self.cam2.id)]})
        nueva.action_publish()
        self.assertEqual(original.state, "archived")

    def test_declaracion_no_quita_empacadora_comprometida(self):
        emp2 = self.env["res.partner"].create({
            "name": "Empacadora dl2", "is_company": True, "email": "emp2.dl@prueba.test",
            "vat_or_id": "0933388003001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora dl2 S.A.", "emp_capacidad_lb_dia": 50000})
        d, _c = self._declaracion(empacadoras=self.emp | emp2)
        with self.assertRaisesRegex(ValidationError, "compromiso vivo"), self.env.cr.savepoint():
            d.write({"recipient_ids": [(3, self.emp.id)]})
        self.assertIn(self.emp, d.recipient_ids)
        d.write({"recipient_ids": [(3, emp2.id)]})
        self.assertEqual(d.recipient_ids, self.emp)

    # ================================================================ D-48
    def test_caducidad_aviso_renovacion_y_archivo(self):
        lista = self._lista()
        self.assertTrue(lista.open_ended)
        self.assertEqual(lista.expires_on, date(2026, 11, 15))
        cerrada = self._lista(open_ended=False, dispatch_from=date.today(),
                              dispatch_to=date.today() + timedelta(days=60),
                              destinatarios=self.cam2)
        self.assertFalse(cerrada.expires_on)
        enviados = []

        def falso(this, xmlid, email_to, ctx=None):
            # Solo las listas de este test (la demo trae otras «hasta segunda orden»).
            if this.issuer_partner_id == self.emp:
                enviados.append((this.id, xmlid, email_to))
            return True

        Clase = type(self.Lista)
        with patch.object(Clase, "_send_template", falso):
            # Lejos de la fecha: nada.
            self.Lista._cron_caducidad()
            self.assertFalse(enviados)
            with freeze_time("2026-11-12 15:00:00"):
                self.Lista._cron_caducidad()
                self.assertEqual(enviados, [(lista.id, "shrimp_packer.mail_template_lista_por_caducar",
                                             self.emp.email)])
                self.assertTrue(lista.expiry_notice_sent)
                self.Lista._cron_caducidad()
                self.assertEqual(len(enviados), 1, "un solo aviso")
                lista.action_renew()
                self.assertEqual(lista.expires_on, date(2026, 12, 12))
                self.assertFalse(lista.expiry_notice_sent)
            with freeze_time("2026-12-13 15:00:00"):
                lista.invalidate_recordset(["is_current"])
                self.assertFalse(lista.is_current, "caducada aunque el cron no haya pasado")
                self.assertNotIn(lista, self.Lista.search([("is_current", "=", True)]))
                self.Lista._cron_caducidad()
                self.assertEqual(lista.state, "archived")
                self.assertEqual(lista.archive_reason, "expired")
                self.assertEqual(cerrada.state, "published", "las de fecha fija no caducan así")
        with self.assertRaises(ValidationError):
            cerrada.action_renew()

    def test_caducidad_configurable(self):
        self.env["ir.config_parameter"].sudo().set_param("shrimp_packer.price_list_expiry_days", "10")
        lista = self._lista()
        self.assertEqual(lista.expires_on, date(2026, 10, 26))
        futura = self._lista(dispatch_from=date(2026, 11, 1), destinatarios=self.cam2)
        self.assertEqual(futura.expires_on, date(2026, 11, 11), "cuenta desde que arranca")

    def test_no_avisa_caducidad_a_empacadora_no_operativa(self):
        lista = self._lista()
        self.emp.shrimp_account_state = "pending"
        enviados = []
        with patch.object(type(self.Lista), "_send_template",
                          lambda this, *a, **k: (this.issuer_partner_id == self.emp
                                                 and enviados.append(a)) or True):
            with freeze_time("2026-11-13 15:00:00"):
                self.Lista._cron_caducidad()
        self.assertFalse(enviados)
        self.assertFalse(lista.expiry_notice_sent)

    def test_primera_lista_avisa_y_no_a_no_operativos(self):
        for cam in (self.cam, self.cam2):
            producto(self.env, cam, nombre="Cosecha dl", etapa="shrimp_marketplace.shrimp_stage_engorde",
                     initial_qty=5000.0, uom_id=self.env.ref("shrimp_marketplace.uom_libra").id,
                     presentation="entero", size_grade_id=self.talla.id,
                     expected_delivery_date=date.today())
        self.cam2.shrimp_account_state = "pending"
        avisos = []

        def falso(this, destinatario, cambios, primera=False):
            avisos.append((destinatario, cambios, primera))

        with patch.object(type(self.Lista), "_enviar_aviso_cambios", falso):
            primera = self._lista(precio=4.4, destinatarios=self.cam | self.cam2)
            self.assertEqual([a[0] for a in avisos], [self.cam])
            destinatario, cambios, es_primera = avisos[0]
            self.assertTrue(es_primera)
            self.assertIsNone(cambios[0]["antes"])
            self.assertAlmostEqual(cambios[0]["ahora"], 4.4)
            # La siguiente avisa como siempre: solo si cambió el precio.
            avisos.clear()
            self._corregir(primera, 5.5).action_publish()
            self.assertEqual(len(avisos), 1)
            self.assertFalse(avisos[0][2])
            self.assertAlmostEqual(avisos[0][1][0]["antes"], 4.4)

    def test_correo_primera_lista_se_renderiza(self):
        lista = self._lista(publicar=False)
        plantilla = self.env.ref("shrimp_packer.mail_template_precios_cambiaron")
        cambios = [{"talla": "30/40", "presentacion": "entero", "antes": None, "ahora": 4.4,
                    "uom": "Kg", "cantidad": 100.0, "lotes": 1, "efecto": 440.0, "nuevo": True}]
        cuerpo = plantilla.with_context(primera=True, cambios=cambios, destinatario="X")._render_field(
            "body_html", lista.ids)[lista.id]
        self.assertIn("nuevo", cuerpo)
        self.assertIn("primera lista", cuerpo)
        caduca = self.env.ref("shrimp_packer.mail_template_lista_por_caducar")
        lista.expires_on = date(2026, 11, 15)
        cuerpo = caduca.with_context(dias=3, destinatario="X")._render_field(
            "body_html", lista.ids)[lista.id]
        self.assertIn("15/11/2026", cuerpo)

    def test_avisos_de_lotes_no_van_a_empacadora_no_aprobada(self):
        from odoo.addons.shrimp_packer.models.shrimp_lot_alert import ShrimpLotAlert
        self.emp.write({"emp_aviso_frecuencia": "diario", "shrimp_account_state": "pending"})
        vistos = []

        def digest(this, packer, *a, **k):
            vistos.append(packer)
            return []

        lote = producto(self.env, self.cam, nombre="Lote aviso dl",
                        etapa="shrimp_marketplace.shrimp_stage_engorde",
                        presentation="entero", size_grade_id=self.talla.id,
                        uom_id=self.env.ref("shrimp_marketplace.uom_libra").id)
        with patch.object(ShrimpLotAlert, "_lotes_candidatos", lambda this, ahora: lote), \
                patch.object(ShrimpLotAlert, "_digest_para", digest):
            self.env["shrimp.lot.alert"]._cron_avisar_lotes()
            self.assertNotIn(self.emp, vistos)
            self.emp.shrimp_account_state = "approved"
            self.env["shrimp.lot.alert"]._cron_avisar_lotes()
            self.assertIn(self.emp, vistos)


@tagged("post_install", "-at_install", "shrimp_packer")
class TestDecisionesListasPortal(HttpCase):
    """El portal de la empacadora: una publicada no se edita; se corrige y se renueva."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "dlp")
        cls.emp = env["res.partner"].create({
            "name": "Empacadora dlp", "is_company": True, "email": "emp.dlp@prueba.test",
            "vat_or_id": "0933388009001", "shrimp_user_type": "empacadora",
            "shrimp_account_state": "approved",
            "emp_razon_social": "Empacadora dlp S.A.", "emp_capacidad_lb_dia": 50000})
        env["res.users"].with_context(no_reset_password=True).create({
            "name": cls.emp.name, "login": "emp.dlp", "partner_id": cls.emp.id,
            "password": CLAVE, "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
        cls.lista = env["shrimp.price.list"].create({
            "name": "Lista dlp", "issuer_partner_id": cls.emp.id,
            "recipient_ids": [(6, 0, cls.s["cam"].ids)],
            "line_ids": [(0, 0, {"size_grade_id": env.ref("shrimp_marketplace.size_entero_3040").id,
                                 "uom": "kg", "quality": "ab", "price": 4.4})]})
        cls.lista.action_publish()

    def test_portal_publicada(self):
        self.authenticate("emp.dlp", CLAVE)
        r = self.url_open("/marketplace/price-lists/%s/edit" % self.lista.uuid_ref)
        self.assertNotIn("/edit", r.url)
        self.assertIn("Duplicar y corregir", r.text)
        self.assertIn("Renovar", r.text)
        token = csrf_de(r.text)
        # Despublicar ya no existe: se explica y no cambia nada.
        self.url_open("/marketplace/price-lists/%s/unpublish" % self.lista.uuid_ref,
                      data={"csrf_token": token})
        self.assertEqual(self.lista.state, "published")
        # Renovar extiende la caducidad.
        self.lista.with_context(shrimp_lista_sin_bloqueo=True).expires_on = \
            fields.Date.today() + timedelta(days=2)
        self.url_open("/marketplace/price-lists/%s/renew" % self.lista.uuid_ref,
                      data={"csrf_token": token})
        self.assertEqual(self.lista.expires_on, fields.Date.today() + timedelta(days=30))
        # Duplicar y corregir abre el borrador que la reemplaza.
        r = self.url_open("/marketplace/price-lists/%s/correct" % self.lista.uuid_ref,
                          data={"csrf_token": token})
        nueva = self.env["shrimp.price.list"].search([("supersedes_id", "=", self.lista.id)])
        self.assertEqual(nueva.state, "draft")
        self.assertIn("/marketplace/price-lists/%s/edit" % nueva.uuid_ref, r.url)
        self.assertIn("corrige", r.text)
        # Eliminar una publicada no se puede.
        self.url_open("/marketplace/price-lists/%s/delete" % self.lista.uuid_ref,
                      data={"csrf_token": token})
        self.assertTrue(self.lista.exists())
