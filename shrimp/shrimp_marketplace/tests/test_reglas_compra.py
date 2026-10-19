"""Reglas de la compra decididas en la auditoría de procesos (§10–11).

D-17  precio congelado en la compra; con compras no canceladas los datos
      críticos del lote no cambian nunca más y el precio no cambia mientras
      haya compras abiertas (opción C).
D-26  toda compra directa lleva recepción, aunque la matriz no la marque.
D-27  la comisión se anula en TODA cancelación (y en la entrega parcial se
      recalcula).
D-42  un strike por compra; la suspensión se levanta sola al bajar del umbral.
D-43  factura del vendedor: plazo, recordatorios, vencida y bloqueo.
Borradores legales: solo borradores, una vez.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import producto
from .test_recepcion import subusuario
from .test_retraso_entrega import RetrasoCommon
from ..models.shrimp_purchase_rules import INVOICE_SINCE_PARAM
from ..models.shrimp_purchase_rules_policy import DONE_PARAM, MARKER

VIVOS = ("pending", "invoiced", "error", "sri_rejected")


@tagged("post_install", "-at_install")
class TestPrecioCongelado(RetrasoCommon):
    """D-17."""

    def test_precio_y_datos_criticos(self):
        lote = producto(self.env, self.s["sem"], nombre="Lote D17")
        lote.write({"price": 12.0})  # sin compras: libre
        tx = lote.execute_purchase_flow(self.s["lab"], 5.0)["transaction"]
        self.assertEqual(tx.price_unit, 12.0, "el precio queda congelado en la compra")
        self.assertTrue(lote.has_purchases())
        self.assertTrue(lote.shrimp_price_locked())
        with self.assertRaises(ValidationError) as e:
            lote.write({"price": 15.0})
        self.assertIn("compras en curso", str(e.exception))
        self.assertIn(tx.name, str(e.exception))
        with self.assertRaises(ValidationError):
            lote.write({"stage_id": self.env.ref("shrimp_marketplace.shrimp_stage_pl10").id})
        # Mismo valor: no es un cambio.
        lote.write({"price": 12.0, "name": "Lote D17 bis"})
        # Se completa la compra: el precio se libera, los datos críticos no.
        self.vendedor(tx).action_mark_dispatched(actor=self.s["sem"])
        self.comprador(tx).shrimp_confirm_reception(actor=self.s["lab"])
        self.assertEqual(tx.state, "done")
        self.assertFalse(lote.shrimp_price_locked())
        lote.write({"price": 14.0})
        self.assertEqual(tx.price_unit, 12.0, "la compra conserva su precio")
        with self.assertRaises(ValidationError) as e:
            lote.write({"stage_id": self.env.ref("shrimp_marketplace.shrimp_stage_pl10").id})
        self.assertIn("ventas completadas", str(e.exception))

    def test_compra_en_borrador_o_verificacion_bloquea(self):
        lote = producto(self.env, self.s["sem"], nombre="Lote D17 borrador")
        tx = lote.execute_purchase_flow(self.s["lab"], 5.0)["transaction"]
        # Simula una compra que aún no se cerró (verificación / firma).
        tx.sudo().write({"state": "draft"})
        self.assertTrue(lote.shrimp_price_locked())
        with self.assertRaises(ValidationError):
            lote.write({"price": 99.0})
        # Cancelada: deja de bloquear.
        tx.sudo().write({"state": "cancel"})
        self.assertFalse(lote.has_purchases())
        lote.write({"price": 99.0, "stage_id": self.env.ref(
            "shrimp_marketplace.shrimp_stage_pl10").id})

    def test_origen_bloqueado(self):
        self.assertIn("origin_pond_id", self.env["shrimp.product"]._LOCKED_AFTER_PURCHASE)
        self.assertIn("origin_allocation_ids", self.env["shrimp.product"]._LOCKED_AFTER_PURCHASE)
        lote = producto(self.env, self.s["sem"], nombre="Lote D17 origen")
        lote.execute_purchase_flow(self.s["lab"], 5.0)
        Fac = self.env["shrimp.partner.facility"].sudo()
        if "partner_id" not in Fac._fields:
            self.skipTest("Sin instalaciones")
        # Lista vacía -> vacía no es un cambio; un (6, ids) distinto sí.
        lote.write({"origin_allocation_ids": [(6, 0, [])]})


@tagged("post_install", "-at_install")
class TestRecepcionTodaDirecta(RetrasoCommon):
    """D-26."""

    def test_directa_sin_columna_lleva_recepcion(self):
        Tx = type(self.env["shrimp.transaction"])
        with patch.object(Tx, "_shrimp_reception_policy", lambda *a, **k: False):
            tx = self.comprar("sem", "lab")
        self.assertTrue(tx.reception_required)
        self.assertEqual(tx.reception_status, "waiting")
        # Sin despacho no se confirma sola («No entregada»); con despacho, sí.
        self.cron(tx.reception_deadline + timedelta(days=1))
        self.assertEqual(tx.reception_status, "not_delivered")
        tx2 = self.comprar("sem", "lab")
        self.vendedor(tx2).action_mark_dispatched(actor=self.s["sem"])
        self.cron(tx2.reception_deadline + timedelta(days=1))
        self.assertEqual((tx2.state, tx2.reception_status), ("done", "auto"))

    def test_piso_de_la_migracion_sin_strikes_retroactivos(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=20))
        # Como la deja la migración 19.0.1.35.0.
        tx._shrimp_add_marks(["d26_from:%s" % self.hoy.isoformat()])
        self.cron(self.hoy + timedelta(days=1))
        self.assertFalse(self.strikes(tx), "un atraso anterior al piso no da strike")


@tagged("post_install", "-at_install")
class TestComisionEnTodaCancelacion(RetrasoCommon):
    """D-27: todos los caminos de cancelación dejan la comisión anulada."""

    def setUp(self):
        super().setUp()
        self.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        # Muchas cancelaciones del mismo vendedor: que la suspensión no frene
        # las compras siguientes de la prueba.
        self.ICP.set_param("shrimp_marketplace.strike_suspension_threshold", "100")
        self.ICP.set_param("shrimp_marketplace.strike_warning_threshold", "100")

    def tearDown(self):
        self.ICP.set_param("shrimp_marketplace.strike_suspension_threshold", "5")
        self.ICP.set_param("shrimp_marketplace.strike_warning_threshold", "3")
        super().tearDown()

    def comision(self, tx):
        return tx.sudo().charge_ids.filtered(lambda c: c.charge_type == "commission")

    def assertAnulada(self, tx):
        tx.invalidate_recordset()
        self.assertEqual(tx.state, "cancel")
        com = self.comision(tx)
        self.assertTrue(com, "la compra tenía comisión")
        self.assertFalse(com.filtered(lambda c: c.state in VIVOS),
                         "comisión viva en %s: %s" % (tx.name, com.mapped("state")))

    def test_todos_los_caminos(self):
        # Comprador: retraso (contrapropuesta rechazada / atraso sin aviso).
        tx = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=5))
        self.assertEqual(tx.shrimp_delay_cancel_cause(), "unnotified")
        self.comprador(tx).shrimp_cancel("buyer", reason="Tarde", actor=self.s["lab"])
        self.assertAnulada(tx)
        # Comprador: no entregada.
        tx = self.comprar("sem", "lab")
        self.cron(tx.reception_deadline + timedelta(days=1))
        self.comprador(tx).shrimp_cancel("buyer", reason="Nunca llegó", actor=self.s["lab"])
        self.assertAnulada(tx)
        # Sistema: no entregada sin respuesta.
        self.ICP.set_param("shrimp_marketplace.not_delivered_cancel_days", "7")
        tx = self.comprar("sem", "lab")
        dia = tx.reception_deadline + timedelta(days=1)
        self.cron(dia)
        self.cron(dia + timedelta(days=7))
        self.assertAnulada(tx)
        # Vendedor.
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=5))
        self.vendedor(tx).shrimp_cancel("seller", reason="Mortalidad", actor=self.s["sem"])
        self.assertAnulada(tx)
        # Mutuo acuerdo.
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=5))
        self.comprador(tx).action_cancel_request("Ya no", actor=self.s["lab"])
        self.vendedor(tx).action_cancel_request_respond(True, actor=self.s["sem"])
        self.assertAnulada(tx)
        # Plataforma.
        tx = self.comprar("sem", "lab")
        tx.shrimp_cancel("platform", reason="Fraude")
        self.assertAnulada(tx)
        # Incidencia resuelta como «no se entregó».
        tx = self.comprar("sem", "lab")
        self.comprador(tx).shrimp_report_reception_issue("No llegó", actor=self.s["lab"])
        tx.action_incidence_not_delivered("No despachó")
        self.assertAnulada(tx)
        # Cualquier otro paso a «cancelada» (verificación rechazada o vencida,
        # código futuro): la red de seguridad.
        tx = self.comprar("sem", "lab")
        tx.sudo().write({"state": "cancel"})
        self.assertAnulada(tx)
        self.assertTrue(tx.message_ids.filtered(lambda m: "se anuló la comisión" in (m.body or "")))

    def test_comision_facturada_pasa_a_nota_de_credito(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=5))
        com = self.comision(tx)
        factura = self.env["account.move"].sudo().create({
            "move_type": "out_invoice", "partner_id": self.s["sem"].id})
        com.sudo().write({"state": "invoiced", "invoice_id": factura.id})
        Charge = type(self.env["shrimp.charge"])
        with patch.object(Charge, "_try_credit", lambda self, manual=False: True):
            self.vendedor(tx).shrimp_cancel("seller", reason="Lote perdido", actor=self.s["sem"])
        self.assertEqual(com.state, "to_credit", "facturada: queda por acreditar (nota de crédito)")

    def test_entrega_parcial_recalcula(self):
        tx = self.comprar("sem", "lab", qty=40.0)
        anterior = self.comision(tx)
        self.comprador(tx).shrimp_report_reception_issue("Llegó la mitad", actor=self.s["lab"])
        tx.action_incidence_partial(20.0, "Faltó la mitad")
        self.assertIn(anterior.state, ("cancelled", "to_credit", "credited"))
        nueva = self.comision(tx) - anterior
        self.assertEqual(len(nueva.filtered(lambda c: c.state in VIVOS)), 1)
        self.assertAlmostEqual(nueva.amount, 20.0 * 5 / 100.0)


@tagged("post_install", "-at_install")
class TestUnStrikePorCompra(RetrasoCommon):
    """D-42."""

    def test_maximo_un_strike_por_compra(self):
        tx = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=30))
        # Atraso sin aviso + sobre el tope + cancelación del comprador.
        self.cron(self.hoy)
        self.comprador(tx).shrimp_cancel("buyer", reason="Tarde", actor=self.s["lab"])
        self.assertEqual(len(self.strikes(tx)), 1)
        reg = tx.shrimp_cancellation()
        self.assertEqual(reg.strike_id, self.strikes(tx))
        self.assertTrue(self.strikes(tx).message_ids.filtered(
            lambda m: "como máximo un strike" in (m.body or "")))

    def _suspender(self):
        txs = [self.comprar("sem", "lab") for _i in range(5)]
        for tx in txs:
            self.Strike.sudo()._shrimp_add_strike(tx, "seller_cancel")
        Sus = self.env["shrimp.seller.suspension"].sudo()
        sus = Sus.search([("partner_id", "=", self.s["sem"].id), ("state", "=", "active")])
        self.assertTrue(sus)
        return txs, sus

    def test_suspension_se_levanta_sola_al_vencer(self):
        txs, sus = self._suspender()
        primero = self.strikes(txs[0])
        primero.sudo().write({"expires_at": fields.Datetime.now() + timedelta(days=1)})
        self.cron(self.hoy)
        self.assertEqual(sus.state, "active")
        self.cron(self.hoy + timedelta(days=2))
        self.assertEqual(sus.state, "lifted")
        self.assertFalse(sus.lifted_by_id)
        self.assertIn("automáticamente", sus.lift_note)
        self.assertTrue(self.strikes().mapped("message_ids").filtered(
            lambda m: "se levantó automáticamente" in (m.body or "")), "aviso al vendedor")
        self.env.cr.cache.pop("shrimp_suspended_partner_ids", None)
        self.assertFalse(self.s["sem"].shrimp_publishing_suspended)

    def test_suspension_se_levanta_al_anular_y_manual(self):
        txs, sus = self._suspender()
        self.strikes(txs[0]).action_annul("No fue culpa del vendedor")
        self.assertEqual(sus.state, "lifted")
        # Se vuelve a suspender y la plataforma la levanta a mano (con 5 activos).
        self.Strike.sudo()._shrimp_add_strike(self.comprar("sem", "lab"), "seller_cancel")
        sus2 = self.env["shrimp.seller.suspension"].sudo().search(
            [("partner_id", "=", self.s["sem"].id), ("state", "=", "active")])
        self.assertTrue(sus2)
        sus2.action_lift("Revisado")
        self.assertEqual(sus2.state, "lifted")
        self.assertEqual(sus2.lifted_by_id, self.env.user)


@tagged("post_install", "-at_install")
class TestFacturaVendedor(RetrasoCommon):
    """D-43."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for k, v in (("seller_invoice_days", "7"), ("seller_invoice_lock_days", "30"),
                     ("seller_invoice_reminder_1", "3"), ("seller_invoice_reminder_2", "6"),
                     ("seller_invoice_reminder_3", "7")):
            cls.ICP.set_param("shrimp_marketplace.%s" % k, v)
        cls.ICP.set_param(INVOICE_SINCE_PARAM, "2000-01-01 00:00:00")

    def completar(self):
        tx = self.comprar("sem", "lab")
        self.vendedor(tx).action_mark_dispatched(actor=self.s["sem"])
        self.comprador(tx).shrimp_confirm_reception(actor=self.s["lab"])
        self.assertEqual(tx.state, "done")
        return tx

    def recordatorios(self, tx):
        return self.mensajes(tx, "Registra la factura")

    def test_plazo_recordatorios_y_vencida(self):
        tx = self.completar()
        self.assertEqual(tx.seller_invoice_status, "pending")
        self.assertEqual(tx.seller_invoice_due_date, self.hoy + timedelta(days=7))
        self.assertEqual(tx.seller_invoice_lock_date, self.hoy + timedelta(days=30))
        self.cron(self.hoy + timedelta(days=2))
        self.assertFalse(self.recordatorios(tx))
        self.cron(self.hoy + timedelta(days=3))
        self.assertEqual(len(self.recordatorios(tx)), 1)
        self.cron(self.hoy + timedelta(days=3))
        self.assertEqual(len(self.recordatorios(tx)), 1, "no se repite")
        self.cron(self.hoy + timedelta(days=6))
        self.cron(self.hoy + timedelta(days=7))
        self.assertEqual(len(self.recordatorios(tx)), 3)
        self.cron(self.hoy + timedelta(days=8))
        self.assertTrue(self.mensajes(tx, "Factura vencida"))
        with freeze_time(datetime.combine(self.hoy + timedelta(days=8), datetime.min.time())
                         + timedelta(hours=17)):
            self.env.invalidate_all()
            self.assertTrue(tx.seller_invoice_overdue)
            self.assertEqual(tx.seller_invoice_status, "overdue")
            self.assertIn(tx, self.Tx.search([("seller_invoice_overdue", "=", True)]))
            # Registrada tarde: se acepta y queda constancia.
            self.vendedor(tx).action_register_seller_invoice(
                {"number": "001-001-000000123"}, actor=self.s["sem"])
            self.assertEqual(tx.seller_invoice_status, "registered")
            self.assertNotIn(tx, self.Tx.search([("seller_invoice_overdue", "=", True)]))
        self.assertTrue(tx.message_ids.filtered(lambda m: "fuera de plazo" in (m.body or "")))

    def test_bloqueo_a_los_30_dias(self):
        tx = self.completar()
        self.vendedor(tx).action_register_seller_invoice(
            {"number": "001-001-000000001"}, actor=self.s["sem"])
        self.vendedor(tx).action_register_seller_invoice(
            {"number": "001-001-000000002"}, actor=self.s["sem"])
        with freeze_time(datetime.combine(self.hoy + timedelta(days=31), datetime.min.time())
                         + timedelta(hours=17)):
            self.env.invalidate_all()
            self.assertEqual(tx.seller_invoice_status, "locked")
            with self.assertRaises(ValidationError):
                self.vendedor(tx).action_register_seller_invoice(
                    {"number": "001-001-000000003"}, actor=self.s["sem"])
            # Tampoco desde el back-office (usuario interno, sin sudo).
            admin = self.env.ref("base.user_admin")
            with self.assertRaises(ValidationError):
                tx.with_user(admin).write({"seller_invoice_number": "001-001-000000004"})
        self.assertEqual(tx.seller_invoice_number, "001-001-000000002")

    def test_ventas_anteriores_no_aplican(self):
        tx = self.completar()
        self.ICP.set_param(INVOICE_SINCE_PARAM, fields.Datetime.to_string(
            fields.Datetime.now() + timedelta(hours=1)))
        tx.invalidate_recordset()
        self.assertEqual(tx.seller_invoice_status, "na")
        self.cron(self.hoy + timedelta(days=8))
        self.assertFalse(self.recordatorios(tx))
        self.ICP.set_param(INVOICE_SINCE_PARAM, "2000-01-01 00:00:00")

    def test_permisos_y_destinatarios(self):
        tx = self.completar()
        sin_permiso = subusuario(self.env, self.s["sem"], "d43.ver",
                                 {"semillero": {"view"}})
        facturador = subusuario(self.env, self.s["sem"], "d43.fact",
                                {"semillero": {"view", "billing"}})
        with self.assertRaises(AccessError):
            tx.with_user(sin_permiso).sudo().action_register_seller_invoice(
                {"number": "001-001-000000009"}, actor=self.s["sem"])
        tx.with_user(facturador).sudo().action_register_seller_invoice(
            {"number": "001-001-000000010"}, actor=self.s["sem"])
        self.assertEqual(tx.seller_invoice_number, "001-001-000000010")
        destinos = tx._shrimp_invoice_recipients()
        self.assertIn(facturador.partner_id, destinos)
        self.assertNotIn(sin_permiso.partner_id, destinos)


@tagged("post_install", "-at_install")
class TestBorradoresReglas(RetrasoCommon):

    def test_solo_borradores_una_vez(self):
        Doc = self.env["shrimp.legal.document"].sudo()
        doc = Doc.search([("code", "=", "contract")], limit=1)
        if not doc or not doc.current_version_id:
            self.skipTest("Sin contrato en esta base")
        publicada = doc.current_version_id
        texto_pub = str(publicada.content)
        self.ICP.set_param(DONE_PARAM, "")
        Policy = self.env["shrimp.purchase.rules.policy"]
        Policy._shrimp_update_rules_drafts()
        borrador = doc.version_ids.filtered(lambda v: v.state == "draft")[:1]
        self.assertTrue(borrador)
        texto = str(borrador.content)
        self.assertIn(MARKER, texto)
        self.assertIn("como máximo <b>un strike</b>", texto)
        self.assertIn("Compra cancelada", texto)
        self.assertEqual(str(publicada.content), texto_pub, "la publicada no se toca")
        Policy._shrimp_update_rules_drafts(force=True)
        self.assertEqual(str(borrador.content).count(MARKER), 1)
