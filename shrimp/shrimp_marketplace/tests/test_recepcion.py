"""Recepción por el comprador con confirmación automática y avisos de la
fecha de entrega (models/shrimp_transaction_reception.py).

- el comprador confirma (nota y foto) y sube su inventario;
- si no hace nada en N días (Ajustes), el cron la confirma sola, una vez;
- los recordatorios y los avisos de entrega no se repiten;
- «Reportar un problema» deja la compra con incidencia (el cron no la toca)
  y la plataforma la resuelve;
- toda compra directa lleva recepción (D-26);
- la comisión no se mueve (se cobra al confirmar la compra);
- subusuarios: permisos para actuar y destinatarios de los avisos;
- portal: rutas, página de la compra y Mi panel.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, PNG_1PX, ShrimpSecurityCommon, crear_socios, csrf_de, producto


def subusuario(env, empresa, login, permisos):
    contacto = env["res.partner"].sudo().create({
        "name": "Sub %s" % login, "email": "%s@prueba.test" % login,
        "parent_id": empresa.id, "type": "contact"})
    user = env["res.users"].sudo().create({
        "name": contacto.name, "login": login, "partner_id": contacto.id, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
    env["shrimp.account.member"].sudo()._shrimp_ensure(user, empresa, permissions=permisos)
    return user


class RecepcionCommon(ShrimpSecurityCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Tx = cls.env["shrimp.transaction"]
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param("shrimp_marketplace.reception_days", "10")
        cls.ICP.set_param("shrimp_marketplace.reception_reminder_1", "3")
        cls.ICP.set_param("shrimp_marketplace.reception_reminder_2", "1")
        cls.ICP.set_param("shrimp_marketplace.delivery_alert_days", "2")
        cls.hoy = cls.Tx._shrimp_today()

    def comprar(self, vendedor_key="sem", comprador_key="lab", entrega=None, qty=5.0):
        lote = producto(self.env, self.s[vendedor_key], nombre="Lote rc %s" % vendedor_key,
                        expected_delivery_date=entrega or self.hoy)
        tx = lote.execute_purchase_flow(self.s[comprador_key], qty)["transaction"]
        # La compra de un naupliero toma la fecha del lote; la de un
        # laboratorio, desired_date = la del lote: las dos quedan en `entrega`.
        tx.sudo().write({"desired_date": entrega or self.hoy})
        tx.invalidate_recordset()
        return tx

    def mensajes(self, tx, asunto):
        return tx.sudo().message_ids.filtered(lambda m: asunto in (m.subject or ""))

    def cron(self, dia):
        with freeze_time(datetime.combine(dia, datetime.min.time()) + timedelta(hours=17)):
            self.env.invalidate_all()
            self.Tx._cron_reception_and_delivery()
        self.env.invalidate_all()


@tagged("post_install", "-at_install")
class TestRecepcion(RecepcionCommon):

    # ------------------------------------------------------------------
    def test_aplica_por_pareja_y_estado_inicial(self):
        tx = self.comprar("sem", "lab")
        self.assertEqual(tx.state, "confirmed")
        self.assertTrue(tx.reception_required)
        self.assertEqual(tx.reception_status, "waiting")
        self.assertEqual(tx.reception_start_date, self.hoy)
        self.assertEqual(tx.reception_deadline, self.hoy + timedelta(days=10))
        tx2 = self.comprar("lab", "cam")
        self.assertTrue(tx2.reception_required, "laboratorio → camaronera lleva recepción")
        tx3 = self.comprar("lab", "lab2")
        self.assertTrue(tx3.reception_required, "laboratorio ↔ laboratorio lleva recepción")

    def test_fecha_pasada_no_nace_vencida(self):
        """Un lote con la fecha de entrega ya pasada: el plazo empieza el día
        de la compra, no en esa fecha (si no, el cron la confirmaría ya)."""
        tx = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=30))
        self.assertEqual(tx.reception_start_date, self.hoy)
        self.assertEqual(tx.reception_deadline, self.hoy + timedelta(days=10))
        self.cron(self.hoy + timedelta(days=1))
        self.assertEqual(tx.state, "confirmed")

    def test_comprador_confirma_con_nota_y_foto(self):
        futura = self.hoy + timedelta(days=5)
        tx = self.comprar("sem", "lab", entrega=futura)
        comprador = self.s["u_lab"]
        # Antes de la fecha de entrega y sin despacho: todavía no.
        with self.assertRaises(ValidationError):
            tx.with_user(comprador).sudo().shrimp_confirm_reception(actor=self.s["lab"])
        # El vendedor marca el despacho: ya se puede, y el plazo arranca hoy.
        tx.with_user(self.s["u_sem"]).sudo().action_mark_dispatched(
            note="Sale hoy", actor=self.s["sem"])
        self.assertTrue(tx.dispatched_at)
        self.assertEqual(tx.reception_start_date, self.hoy)
        self.assertTrue(self.mensajes(tx, "fue despachada"))
        # Otra parte no puede confirmar por el comprador.
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_sem"]).sudo().shrimp_confirm_reception(actor=self.s["sem"])
        tx.with_user(comprador).sudo().shrimp_confirm_reception(
            note="Llegó completo", actor=self.s["lab"],
            photo={"content": PNG_1PX, "mimetype": "image/png", "name": "foto.png"})
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "received")
        self.assertFalse(tx.reception_auto_confirmed)
        self.assertEqual(tx.reception_note, "Llegó completo")
        self.assertTrue(tx.reception_photo_id)
        self.assertEqual(tx.received_by_id, comprador)
        lotes = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.s["lab"].id)])
        self.assertEqual(sum(lotes.mapped("initial_qty")), 5.0, "inventario del comprador")
        self.assertTrue(self.mensajes(tx, "el comprador confirmó la recepción"))
        # Una foto que no es imagen se rechaza.
        tx2 = self.comprar("sem", "lab")
        with self.assertRaises(ValidationError):
            tx2.with_user(comprador).sudo().shrimp_confirm_reception(
                actor=self.s["lab"], photo={"content": b"x", "mimetype": "text/html", "name": "a"})

    def test_confirmacion_automatica_tras_n_dias(self):
        tx = self.comprar("sem", "lab")
        # Solo con despacho marcado (sin despacho: «No entregada», ver
        # test_cancelacion_compra.TestNoEntregada).
        tx.with_user(self.s["u_sem"]).sudo().action_mark_dispatched(actor=self.s["sem"])
        limite = tx.reception_deadline
        cobros_antes = tx.charge_ids
        # El último día todavía es del comprador.
        self.cron(limite)
        self.assertEqual(tx.state, "confirmed")
        # Al día siguiente la confirma el cron.
        self.cron(limite + timedelta(days=1))
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "auto")
        self.assertTrue(tx.reception_auto_confirmed)
        self.assertFalse(tx.received_by_id)
        self.assertTrue(self.env["shrimp.stock.lot"].search_count([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.s["lab"].id)]))
        self.assertTrue(self.mensajes(tx, "confirmada automáticamente"))
        self.assertTrue(self.mensajes(tx, "recepción confirmada"))
        # Cuenta para la reputación del vendedor como entrega cumplida.
        self.s["sem"].invalidate_recordset()
        self.assertGreaterEqual(self.s["sem"].shrimp_delivery_count, 1)
        self.assertGreaterEqual(self.s["sem"].shrimp_delivery_auto_count, 1)
        self.assertEqual(self.s["sem"].shrimp_delivery_ok_pct, 100.0)
        # La comisión no se mueve: se cobró al confirmar la compra.
        self.assertEqual(tx.charge_ids, cobros_antes)
        # Volver a correr el cron no hace nada más.
        n = len(tx.message_ids)
        self.cron(limite + timedelta(days=2))
        self.assertEqual(len(tx.message_ids), n)

    def test_recordatorios_sin_duplicados(self):
        tx = self.comprar("sem", "lab")
        limite = tx.reception_deadline
        asunto = "Confirma la recepción"
        self.cron(limite - timedelta(days=4))
        self.assertFalse(self.mensajes(tx, asunto))
        self.cron(limite - timedelta(days=3))
        self.assertEqual(len(self.mensajes(tx, asunto)), 1)
        self.cron(limite - timedelta(days=3))
        self.cron(limite - timedelta(days=2))
        self.assertEqual(len(self.mensajes(tx, asunto)), 1, "no se repite")
        self.cron(limite - timedelta(days=1))
        self.assertEqual(len(self.mensajes(tx, asunto)), 2)
        self.cron(limite)
        self.assertEqual(len(self.mensajes(tx, asunto)), 2)
        # Si el primer cron llega tarde (ya dentro del último umbral) sale UN
        # solo recordatorio.
        tx2 = self.comprar("sem", "lab")
        self.cron(tx2.reception_deadline - timedelta(days=1))
        self.assertEqual(len(self.mensajes(tx2, asunto)), 1)
        self.cron(tx2.reception_deadline)
        self.assertEqual(len(self.mensajes(tx2, asunto)), 1)

    def test_incidencia_detiene_y_la_resuelve_la_plataforma(self):
        tx = self.comprar("sem", "lab")
        with self.assertRaises(ValidationError):
            tx.with_user(self.s["u_lab"]).sudo().shrimp_report_reception_issue(
                "  ", actor=self.s["lab"])
        tx.with_user(self.s["u_lab"]).sudo().shrimp_report_reception_issue(
            "Llegó la mitad", actor=self.s["lab"])
        self.assertEqual(tx.reception_status, "incidence")
        self.assertTrue(tx.reception_had_incidence)
        self.assertTrue(self.mensajes(tx, "Incidencia en la venta"))
        # El cron no la confirma aunque venza el plazo.
        self.cron(tx.reception_deadline + timedelta(days=5))
        self.assertEqual(tx.state, "confirmed")
        # Un usuario de portal no resuelve incidencias.
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_lab"]).action_incidence_reopen()
        # La plataforma reabre: el plazo vuelve a empezar hoy.
        tx.action_incidence_reopen()
        self.assertEqual(tx.reception_status, "waiting")
        self.assertEqual(tx.reception_start_date, self.hoy)
        # Otra incidencia y la plataforma la da por recibida.
        tx.with_user(self.s["u_lab"]).sudo().shrimp_report_reception_issue(
            "Sigue faltando", actor=self.s["lab"])
        tx.action_incidence_resolve_received()
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.reception_status, "received")
        self.s["sem"].invalidate_recordset()
        self.assertLess(self.s["sem"].shrimp_delivery_ok_pct, 100.0, "la incidencia cuenta")

    def test_pareja_sin_recepcion_igual_la_lleva(self):
        """D-26: aunque la matriz no marque la pareja, toda compra directa
        lleva la confirmación de recepción (antes quedaba confirmada para
        siempre)."""
        Tx = self.env.registry["shrimp.transaction"]
        with patch.object(Tx, "_shrimp_reception_policy", lambda *a, **k: False):
            tx = self.comprar("lab", "cam")
        self.assertTrue(tx.reception_required)
        self.assertEqual(tx.reception_status, "waiting")
        self.assertTrue(tx.reception_start_date)

    def test_ajustes_se_respetan(self):
        tx = self.comprar("sem", "lab")
        settings = self.env["res.config.settings"].create({
            "shrimp_reception_days": 5, "shrimp_reception_reminder_1": 2,
            "shrimp_reception_reminder_2": 0, "shrimp_delivery_alert_days": 4})
        settings.execute()
        self.assertEqual(self.ICP.get_param("shrimp_marketplace.reception_days"), "5")
        self.assertEqual(self.ICP.get_param("shrimp_marketplace.reception_reminder_2"), "0")
        tx.invalidate_recordset()
        self.assertEqual(tx.reception_deadline, self.hoy + timedelta(days=5))
        self.assertEqual(self.Tx._shrimp_reception_reminders(), [2])
        tx.with_user(self.s["u_sem"]).sudo().action_mark_dispatched(actor=self.s["sem"])
        self.cron(self.hoy + timedelta(days=6))
        self.assertEqual(tx.reception_status, "auto")
        # Un recordatorio que no es menor que el plazo no se acepta.
        with self.assertRaises(ValidationError):
            self.env["res.config.settings"].create({
                "shrimp_reception_days": 5, "shrimp_reception_reminder_1": 5}).execute()

    def test_cobro_y_factura_del_vendedor(self):
        self.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        tx = self.comprar("sem", "lab")
        comision = tx.charge_ids.filtered(lambda c: c.charge_type == "commission")
        self.assertTrue(comision, "la comisión se cobra al confirmar la compra")
        # La factura de la mercadería se puede registrar antes de recibir.
        tx.with_user(self.s["u_sem"]).sudo().action_register_seller_invoice(
            {"number": "001-001-000000123"}, actor=self.s["sem"])
        tx.with_user(self.s["u_lab"]).sudo().shrimp_confirm_reception(actor=self.s["lab"])
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.charge_ids.filtered(lambda c: c.charge_type == "commission"), comision,
                         "recibir no cobra ni duplica la comisión")
        # Tampoco la confirmación automática.
        tx2 = self.comprar("sem", "lab")
        tx2.with_user(self.s["u_sem"]).sudo().action_mark_dispatched(actor=self.s["sem"])
        cobros = tx2.charge_ids
        self.cron(tx2.reception_deadline + timedelta(days=1))
        self.assertEqual(tx2.state, "done")
        self.assertEqual(tx2.charge_ids, cobros)

    def test_avisos_de_entrega_antes_hoy_y_atraso(self):
        entrega = self.hoy + timedelta(days=4)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.cron(entrega - timedelta(days=3))
        self.assertFalse(self.mensajes(tx, "Prepara el despacho"))
        self.cron(entrega - timedelta(days=2))
        self.assertEqual(len(self.mensajes(tx, "Prepara el despacho")), 1)
        self.assertEqual(len(self.mensajes(tx, "llega pronto")), 1)
        self.cron(entrega - timedelta(days=1))
        self.assertEqual(len(self.mensajes(tx, "Prepara el despacho")), 1, "sin duplicados")
        self.cron(entrega)
        self.assertEqual(len(self.mensajes(tx, "Hoy es la entrega")), 1)
        self.assertEqual(len(self.mensajes(tx, "Hoy llega tu compra")), 1)
        self.cron(entrega)
        self.assertEqual(len(self.mensajes(tx, "Hoy es la entrega")), 1)
        self.cron(entrega + timedelta(days=1))
        self.assertEqual(len(self.mensajes(tx, "Entrega atrasada")), 1)
        self.assertEqual(len(self.mensajes(tx, "está atrasada")), 1)
        tx.invalidate_recordset()
        with freeze_time(datetime.combine(entrega + timedelta(days=1), datetime.min.time())
                         + timedelta(hours=17)):
            self.assertTrue(tx.shrimp_delivery_overdue())
        self.cron(entrega + timedelta(days=2))
        self.assertEqual(len(self.mensajes(tx, "Entrega atrasada")), 1)
        # Despachada a tiempo: ni «prepara el despacho» ni atraso al vendedor.
        tx2 = self.comprar("sem", "lab", entrega=entrega)
        tx2.with_user(self.s["u_sem"]).sudo().action_mark_dispatched(actor=self.s["sem"])
        self.cron(entrega - timedelta(days=1))
        self.assertFalse(self.mensajes(tx2, "Prepara el despacho"))
        self.assertTrue(self.mensajes(tx2, "llega pronto"))
        self.cron(entrega + timedelta(days=1))
        self.assertFalse(self.mensajes(tx2, "Entrega atrasada"))

    def test_cambio_de_fecha_vuelve_a_avisar(self):
        entrega = self.hoy + timedelta(days=1)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.cron(self.hoy)
        self.assertEqual(len(self.mensajes(tx, "llega pronto")), 1)
        tx.sudo().write({"desired_date": entrega + timedelta(days=3)})
        self.cron(self.hoy + timedelta(days=2))
        self.assertEqual(len(self.mensajes(tx, "llega pronto")), 2)

    def test_migracion_no_confirma_compras_viejas(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=30))
        # Como estaba antes de instalar esto.
        tx.sudo().write({"reception_required": False, "reception_status": False})
        self.Tx._shrimp_backfill_reception()
        self.assertTrue(tx.reception_required)
        self.assertEqual(tx.reception_start_date, self.hoy, "el plazo empieza hoy")
        self.cron(self.hoy + timedelta(days=1))
        self.assertEqual(tx.state, "confirmed")
        self.assertFalse(self.mensajes(tx, "Entrega atrasada"), "sin tanda de avisos viejos")
        self.cron(self.hoy + timedelta(days=11))
        # Sin despacho no se confirma sola: queda «No entregada».
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.reception_status, "not_delivered")


@tagged("post_install", "-at_install")
class TestRecepcionSubcuentas(RecepcionCommon):

    def test_permisos_y_destinatarios(self):
        lab = self.s["lab"]
        Member = self.env["shrimp.account.member"].sudo()
        Member._shrimp_ensure(self.s["u_lab"], lab, is_admin=True)
        sin_permiso = subusuario(self.env, lab, "solo.ver.rc", {"laboratorio": {"view"}})
        comprador = subusuario(self.env, lab, "compras.rc", {"laboratorio": {"view", "buy"}})
        tx = self.comprar("sem", "lab")
        with self.assertRaises(AccessError):
            tx.with_user(sin_permiso).sudo().shrimp_confirm_reception(actor=lab)
        with self.assertRaises(AccessError):
            tx.with_user(sin_permiso).sudo().shrimp_report_reception_issue("x", actor=lab)
        destinatarios = self.Tx._shrimp_party_users(lab, "laboratorio", "buy")
        self.assertIn(self.s["u_lab"], destinatarios, "el administrador")
        self.assertIn(comprador, destinatarios, "subusuario con permiso de comprar")
        self.assertNotIn(sin_permiso, destinatarios)
        tx.with_user(comprador).sudo().shrimp_confirm_reception(actor=lab)
        self.assertEqual(tx.state, "done")
        self.assertEqual(tx.received_by_id, comprador)
        # El aviso de «despachada» llegó a los dos usuarios del comprador.
        tx2 = self.comprar("sem", "lab")
        tx2.with_user(self.s["u_sem"]).sudo().action_mark_dispatched(actor=self.s["sem"])
        msg = self.mensajes(tx2, "fue despachada")
        self.assertIn(comprador.partner_id, msg.partner_ids)
        self.assertIn(self.s["lab"], msg.partner_ids)
        self.assertNotIn(sin_permiso.partner_id, msg.partner_ids)


@tagged("post_install", "-at_install")
class TestRecepcionPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "rcp")
        cls.Tx = cls.env["shrimp.transaction"]
        cls.hoy = cls.Tx._shrimp_today()

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def compra(self, entrega):
        lote = producto(self.env, self.s["sem"], nombre="Nauplio portal rc",
                        expected_delivery_date=entrega)
        tx = lote.execute_purchase_flow(self.s["lab"], 3.0)["transaction"]
        tx.sudo().write({"desired_date": entrega})
        return tx

    def test_flujo_portal(self):
        tx = self.compra(self.hoy + timedelta(days=3))
        pagina = "/marketplace/purchases/%s/traceability" % tx.uuid_ref
        # Vendedor: ve «Marcar como despachado» y lo marca.
        self.authenticate(self.s["u_sem"].login, CLAVE)
        r = self.url_open(pagina)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Entrega y recepción", r.text)
        self.assertIn("Marcar como despachado", r.text)
        r = self.url_open("/marketplace/sales/%s/dispatch" % tx.uuid_ref, data={
            "csrf_token": csrf_de(r.text), "note": "Sale hoy", "next": pagina + "#recepcion"},
            allow_redirects=False)
        self.assertIn("saved=dispatched", r.headers.get("Location", ""))
        self.assertTrue(tx.dispatched_at)
        # Un tercero no puede marcar ni confirmar.
        self.authenticate(self.s["u_lab2"].login, CLAVE)
        r = self.url_open("/marketplace/purchases")
        r = self.url_open("/marketplace/purchases/%s/receive" % tx.uuid_ref,
                          data={"csrf_token": csrf_de(r.text)}, allow_redirects=False)
        self.assertEqual(r.status_code, 403)
        # Comprador: cuenta atrás y formulario; confirma con nota y foto.
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self.url_open(pagina)
        self.assertIn("días para confirmar la recepción", r.text)
        self.assertIn("Reportar un problema", r.text)
        lista = self.url_open("/marketplace/purchases")
        self.assertIn("recvModal%s" % tx.uuid_ref, lista.text)
        self.assertIn("para confirmar", lista.text)
        r = self.url_open("/marketplace/purchases/%s/receive" % tx.uuid_ref, data={
            "csrf_token": csrf_de(lista.text), "note": "Todo bien", "next": "/marketplace/purchases"},
            files={"photo": ("foto.png", PNG_1PX, "image/png")}, allow_redirects=False)
        self.assertIn("saved=received", r.headers.get("Location", ""))
        self.assertEqual(tx.state, "done")
        self.assertTrue(tx.reception_photo_id)
        foto = self.url_open("/marketplace/purchases/%s/reception/reception" % tx.uuid_ref)
        self.assertEqual(foto.status_code, 200)
        r = self.url_open(pagina)
        self.assertIn("Recibido", r.text)

    def test_reportar_problema_y_panel(self):
        tx = self.compra(self.hoy - timedelta(days=1))
        self.authenticate(self.s["u_lab"].login, CLAVE)
        panel = self.url_open("/my/dashboard")
        self.assertEqual(panel.status_code, 200)
        self.assertIn("Próximas entregas", panel.text)
        self.assertIn("Entregas atrasadas", panel.text)
        self.assertIn("espera que confirmes la recepción", panel.text)
        r = self.url_open("/marketplace/purchases/%s/report" % tx.uuid_ref, data={
            "csrf_token": csrf_de(panel.text), "note": "Llegó la mitad",
            "next": "/marketplace/purchases/%s/traceability#recepcion" % tx.uuid_ref},
            allow_redirects=False)
        self.assertIn("saved=incidence", r.headers.get("Location", ""))
        self.assertEqual(tx.reception_status, "incidence")
        r = self.url_open("/marketplace/purchases/%s/traceability" % tx.uuid_ref)
        self.assertIn("Problema reportado por el comprador", r.text)
        # El vendedor ve la incidencia en su panel.
        self.authenticate(self.s["u_sem"].login, CLAVE)
        panel = self.url_open("/my/dashboard")
        self.assertIn("1 venta con una incidencia reportada por el comprador", panel.text)
