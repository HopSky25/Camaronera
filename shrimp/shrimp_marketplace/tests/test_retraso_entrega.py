"""«Notificar retraso de entrega», negociación de una ronda, cancelación sin
costo y strikes (models/shrimp_transaction_delay.py, shrimp_seller_strike.py).

- aviso dentro / fuera de límites (2 avisos, 15 días, gracia de 2 días);
- aceptar, contraproponer (aceptada / rechazada), aceptación tácita a las
  48 h y contrapropuesta sin respuesta (= rechazada);
- cancelación: stock de vuelta al lote del vendedor, comisión anulada,
  verificación no realizada anulada, trazabilidad coherente;
- strikes por cada causa, ninguno por un retraso avisado y aceptado,
  vencimiento a los 90 días, umbrales 3 (orden / destacados) y 5
  (suspensión), anulación y levantamiento por la plataforma;
- subcuentas, Mi panel, portal y borradores legales.
"""
from datetime import datetime, timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios, csrf_de, producto
from .test_recepcion import RecepcionCommon, subusuario
from ..models.shrimp_delay_policy import ANNEX_MARKER, DONE_PARAM, MARKER
from ..models.shrimp_seller_strike import SUSPENDED_CACHE_KEY
from ..models.shrimp_transaction_delay import DELAY_SINCE_PARAM


def _fecha(d):
    return d.strftime("%d/%m/%Y")


class RetrasoCommon(RecepcionCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for clave, valor in (("delay_max_notices", "2"), ("delay_max_days", "15"),
                             ("delay_response_hours", "48"), ("delay_grace_days", "2"),
                             ("strike_expiry_days", "90"), ("strike_warning_threshold", "3"),
                             ("strike_suspension_threshold", "5")):
            cls.ICP.set_param("shrimp_marketplace.%s" % clave, valor)
        # Las reglas cuentan desde hace mucho (en una base real, desde el -u).
        cls.ICP.set_param(DELAY_SINCE_PARAM, (cls.hoy - timedelta(days=400)).isoformat())
        cls.Strike = cls.env["shrimp.seller.strike"]

    def setUp(self):
        super().setUp()
        self.env.cr.cache.pop(SUSPENDED_CACHE_KEY, None)

    def tearDown(self):
        self.env.cr.cache.pop(SUSPENDED_CACHE_KEY, None)
        super().tearDown()

    # -- acciones como las partes ---------------------------------------
    def avisar(self, tx, fecha, motivo="aguaje", nota=None):
        return tx.with_user(self.s["u_sem"]).sudo().action_notify_delay(
            fecha, motivo, note=nota, actor=self.s["sem"])

    def comprador(self, tx):
        return tx.with_user(self.s["u_lab"]).sudo()

    def vendedor(self, tx):
        return tx.with_user(self.s["u_sem"]).sudo()

    def strikes(self, tx=None, partner=None):
        dominio = [("partner_id", "=", (partner or self.s["sem"]).id)]
        if tx:
            dominio.append(("transaction_id", "=", tx.id))
        return self.Strike.sudo().search(dominio)


@tagged("post_install", "-at_install")
class TestRetrasoNegociacion(RetrasoCommon):

    def test_aviso_dentro_y_fuera_de_limites(self):
        entrega = self.hoy + timedelta(days=5)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.assertTrue(tx.shrimp_delay_can_notify())
        self.assertEqual(tx.shrimp_delay_cap_date(), entrega + timedelta(days=15))
        # Más allá del tope (fecha original + 15 días).
        with self.assertRaises(ValidationError):
            self.avisar(tx, entrega + timedelta(days=16))
        # No es un retraso: la misma fecha o antes.
        with self.assertRaises(ValidationError):
            self.avisar(tx, entrega)
        with self.assertRaises(ValidationError):
            self.avisar(tx, entrega + timedelta(days=3), motivo="lluvia")
        # El comprador no avisa retrasos.
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_lab"]).sudo().action_notify_delay(
                entrega + timedelta(days=3), "aguaje", actor=self.s["lab"])
        aviso = self.avisar(tx, entrega + timedelta(days=3), nota="Aguaje fuerte")
        self.assertEqual(aviso.state, "proposed")
        self.assertEqual(aviso.old_date, entrega)
        self.assertEqual(aviso.original_date, entrega)
        self.assertAlmostEqual(
            (aviso.buyer_deadline - aviso.notice_date).total_seconds(), 48 * 3600, delta=5)
        self.assertTrue(self.mensajes(tx, "Retraso de entrega en tu compra"))
        self.assertEqual(tx.delivery_date, entrega, "la fecha no cambia hasta aceptar")
        # Uno a la vez.
        with self.assertRaises(ValidationError):
            self.avisar(tx, entrega + timedelta(days=4))
        self.comprador(tx).action_delay_buyer_accept(actor=self.s["lab"])
        # Segundo aviso: el tope sigue siendo la fecha ORIGINAL + 15.
        with self.assertRaises(ValidationError):
            self.avisar(tx, entrega + timedelta(days=16))
        self.avisar(tx, entrega + timedelta(days=15))
        self.comprador(tx).action_delay_buyer_accept(actor=self.s["lab"])
        self.assertEqual(tx.delivery_date, entrega + timedelta(days=15))
        # Tercero: ya no (máximo 2 por compra) y además está en el tope.
        self.assertFalse(tx.shrimp_delay_can_notify())
        self.assertEqual(tx.shrimp_delay_notices_left(), 0)
        # Pasada la gracia (2 días después de la fecha) ya no se puede avisar.
        tx2 = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=3))
        self.assertIn("se podía enviar hasta", tx2.shrimp_delay_notify_problem())
        with self.assertRaises(ValidationError):
            self.avisar(tx2, self.hoy + timedelta(days=2))
        # Dentro de la gracia sí.
        tx3 = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=2))
        self.assertTrue(tx3.shrimp_delay_can_notify())
        # Despachada: ya no.
        tx4 = self.comprar("sem", "lab", entrega=entrega)
        self.vendedor(tx4).action_mark_dispatched(actor=self.s["sem"])
        self.assertIn("despachada", tx4.shrimp_delay_notify_problem())

    def test_aceptar_reprograma_avisos_y_recepcion(self):
        entrega = self.hoy + timedelta(days=2)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.cron(self.hoy)
        self.assertEqual(len(self.mensajes(tx, "Prepara el despacho")), 1)
        self.assertIn("dlv_pre:%s" % entrega, tx.reception_notify_marks)
        nueva = entrega + timedelta(days=6)
        self.avisar(tx, nueva)
        self.comprador(tx).action_delay_buyer_accept(actor=self.s["lab"])
        tx.invalidate_recordset()
        self.assertEqual(tx.delivery_date, nueva)
        self.assertEqual(tx.reception_start_date, nueva)
        self.assertEqual(tx.reception_deadline, nueva + timedelta(days=10))
        self.assertNotIn("dlv_pre:%s" % entrega, tx.reception_notify_marks or "")
        aviso = tx.shrimp_delay_last()
        self.assertEqual(aviso.state, "accepted")
        self.assertEqual(aviso.final_date, nueva)
        self.assertTrue(self.mensajes(tx, "el comprador aceptó la nueva fecha"))
        # Los avisos de la fecha NUEVA salen otra vez.
        self.cron(nueva - timedelta(days=2))
        self.assertEqual(len(self.mensajes(tx, "Prepara el despacho")), 2)
        # La fecha vieja pasó sin despacho: no hay atraso ni strike.
        self.cron(entrega + timedelta(days=3))
        self.assertFalse(self.strikes(tx))
        self.assertFalse(tx.shrimp_delay_cancel_cause())

    def test_contrapropuesta_aceptada_y_rechazada(self):
        entrega = self.hoy + timedelta(days=4)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.avisar(tx, entrega + timedelta(days=8))
        with self.assertRaises(ValidationError):  # fuera del tope
            self.comprador(tx).action_delay_buyer_counter(entrega + timedelta(days=20),
                                                          actor=self.s["lab"])
        with self.assertRaises(ValidationError):  # la misma del vendedor
            self.comprador(tx).action_delay_buyer_counter(entrega + timedelta(days=8),
                                                          actor=self.s["lab"])
        with self.assertRaises(AccessError):  # el vendedor no contrapropone
            self.vendedor(tx).action_delay_buyer_counter(entrega + timedelta(days=5),
                                                         actor=self.s["sem"])
        aviso = self.comprador(tx).action_delay_buyer_counter(
            entrega + timedelta(days=5), note="Necesito antes", actor=self.s["lab"])
        self.assertEqual(aviso.state, "countered")
        self.assertTrue(self.mensajes(tx, "el comprador propone otra fecha"))
        # Una sola ronda.
        with self.assertRaises(ValidationError):
            self.comprador(tx).action_delay_buyer_counter(entrega + timedelta(days=6),
                                                          actor=self.s["lab"])
        with self.assertRaises(AccessError):  # el comprador no responde por el vendedor
            self.comprador(tx).action_delay_seller_accept(actor=self.s["lab"])
        self.vendedor(tx).action_delay_seller_accept(actor=self.s["sem"])
        self.assertEqual(aviso.state, "accepted")
        self.assertEqual(tx.delivery_date, entrega + timedelta(days=5))
        self.assertTrue(self.mensajes(tx, "El vendedor aceptó tu fecha"))
        # Rechazada: la fecha no cambia y el comprador puede cancelar.
        tx2 = self.comprar("sem", "lab", entrega=entrega)
        self.avisar(tx2, entrega + timedelta(days=8))
        self.comprador(tx2).action_delay_buyer_counter(entrega + timedelta(days=2),
                                                       actor=self.s["lab"])
        self.vendedor(tx2).action_delay_seller_reject(actor=self.s["sem"])
        self.assertEqual(tx2.shrimp_delay_last().state, "rejected")
        self.assertEqual(tx2.delivery_date, entrega)
        self.assertEqual(tx2.shrimp_delay_cancel_cause(), "rejected")
        self.assertIn("rechaz", tx2.shrimp_delay_notify_problem())
        self.assertTrue(self.mensajes(tx2, "Puedes cancelar sin costo"))
        self.assertFalse(self.strikes(tx2), "rechazar no da strike; cancelar sí")

    def test_aceptacion_tacita_y_contrapropuesta_sin_respuesta(self):
        entrega = self.hoy + timedelta(days=4)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.avisar(tx, entrega + timedelta(days=5))
        self.cron(self.hoy + timedelta(days=1))
        self.assertEqual(tx.shrimp_delay_last().state, "proposed", "aún dentro de las 48 h")
        self.cron(self.hoy + timedelta(days=3))
        aviso = tx.shrimp_delay_last()
        self.assertEqual(aviso.state, "tacit")
        self.assertEqual(tx.delivery_date, entrega + timedelta(days=5))
        self.assertTrue(self.mensajes(tx, "Nueva fecha de entrega"))
        self.assertFalse(self.strikes(tx), "un retraso avisado y aceptado no da strike")
        # El vendedor no responde la contrapropuesta: cuenta como rechazada.
        tx2 = self.comprar("sem", "lab", entrega=entrega)
        self.avisar(tx2, entrega + timedelta(days=5))
        self.comprador(tx2).action_delay_buyer_counter(entrega + timedelta(days=1),
                                                       actor=self.s["lab"])
        self.cron(self.hoy + timedelta(days=3))
        aviso2 = tx2.shrimp_delay_last()
        self.assertEqual(aviso2.state, "rejected")
        self.assertTrue(aviso2.rejected_auto)
        self.assertEqual(tx2.shrimp_delay_cancel_cause(), "rejected")
        self.assertTrue(self.mensajes(tx2, "contrapropuesta sin respuesta"))


@tagged("post_install", "-at_install")
class TestRetrasoCancelacion(RetrasoCommon):

    def test_cancelacion_devuelve_stock_y_anula_cobros(self):
        self.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        entrega = self.hoy + timedelta(days=3)
        lote_prod = producto(self.env, self.s["sem"], nombre="Nauplio cancelable",
                             expected_delivery_date=entrega)
        lote = self.env["shrimp.stock.lot"].search([("product_id", "=", lote_prod.id)])
        self.assertEqual(lote.available_qty, 100.0)
        tx = lote_prod.execute_purchase_flow(self.s["lab"], 40.0)["transaction"]
        tx.sudo().write({"desired_date": entrega})
        self.assertEqual(lote.available_qty, 60.0)
        comision = tx.charge_ids.filtered(lambda c: c.charge_type == "commission")
        self.assertTrue(comision)
        verif = self.env["shrimp.charge"].sudo().create({
            "charge_type": "verification_fee", "transaction_id": tx.id,
            "payer_partner_id": self.s["lab"].id, "amount": 30.0, "description": "Verificación"})
        # Sin causa todavía no se puede cancelar.
        with self.assertRaises(ValidationError):
            self.comprador(tx).action_cancel_for_delay(actor=self.s["lab"])
        self.avisar(tx, entrega + timedelta(days=6))
        self.comprador(tx).action_delay_buyer_counter(entrega + timedelta(days=1), actor=self.s["lab"])
        self.vendedor(tx).action_delay_seller_reject(actor=self.s["sem"])
        with self.assertRaises(AccessError):  # el vendedor no cancela por el comprador
            self.vendedor(tx).action_cancel_for_delay(actor=self.s["sem"])
        venta = tx.stock_move_ids.filtered(lambda m: m.move_type == "transfer")
        self.comprador(tx).action_cancel_for_delay(note="Compré a otro", actor=self.s["lab"])
        self.assertEqual(tx.state, "cancel")
        self.assertFalse(tx.reception_status)
        # Stock: vuelve al MISMO lote, con un movimiento que apunta a la venta.
        lote.invalidate_recordset()
        self.assertEqual(lote.available_qty, 100.0)
        self.assertEqual(lote.state, "available")
        devolucion = tx.stock_move_ids.filtered(lambda m: m.move_type == "adjustment")
        self.assertEqual(len(devolucion), 1)
        self.assertEqual(devolucion.direction, "in")
        self.assertEqual(devolucion.lot_id, lote)
        self.assertEqual(devolucion.parent_move_id, venta)
        self.assertEqual(devolucion.qty, 40.0)
        self.assertEqual((devolucion.qty_before, devolucion.qty_after), (60.0, 100.0))
        lote_prod.invalidate_recordset()
        self.assertEqual(lote_prod.available_qty, 100.0)
        # El comprador no recibió nada.
        self.assertFalse(self.env["shrimp.stock.lot"].search(
            [("owner_id", "=", self.s["lab"].id), ("origin_move_id", "in", tx.stock_move_ids.ids)]))
        # Trazabilidad: la venta y su reversa, sin lotes del comprador.
        data = tx.get_full_traceability_data()
        self.assertIn(venta, data["moves"])
        self.assertIn(devolucion, data["moves"])
        self.assertFalse(data["own_lots"])
        # Cobros: comisión y verificación no realizada anuladas.
        self.assertIn(comision.state, ("cancelled", "to_credit", "credited"))
        self.assertIn(verif.state, ("cancelled", "to_credit", "credited"))
        # Historial y strike.
        ultimo = tx.shrimp_delay_last()
        self.assertEqual(ultimo.state, "cancelled")
        self.assertEqual(ultimo.cancel_cause, "rejected")
        self.assertEqual(self.strikes(tx).mapped("reason"), ["buyer_cancel_delay"])
        self.assertTrue(self.mensajes(tx, "cancelada sin costo"))
        self.assertTrue(self.mensajes(tx, "cancelada por el comprador"))
        # No se cancela dos veces.
        with self.assertRaises(ValidationError):
            self.comprador(tx).action_cancel_for_delay(actor=self.s["lab"])

    def test_cancelar_lote_vendido_lo_vuelve_a_publicar(self):
        entrega = self.hoy - timedelta(days=5)
        prod = producto(self.env, self.s["sem"], nombre="Nauplio agotado",
                        expected_delivery_date=entrega)
        tx = prod.execute_purchase_flow(self.s["lab"], 100.0)["transaction"]
        tx.sudo().write({"desired_date": entrega})
        self.assertEqual(prod.state, "sold")
        # Pasó la fecha + 2 días sin despacho ni aviso.
        self.assertEqual(tx.shrimp_delay_cancel_cause(), "unnotified")
        self.comprador(tx).action_cancel_for_delay(actor=self.s["lab"])
        prod.invalidate_recordset()
        self.assertEqual(prod.available_qty, 100.0)
        self.assertEqual(prod.state, "published")
        registro = tx.delay_ids
        self.assertEqual((registro.kind, registro.cancel_cause), ("cancel", "unnotified"))

    def test_reglas_no_cuentan_atrasos_anteriores(self):
        """Compras viejas sin despacho marcado: ni strike ni cancelación."""
        tx = self.comprar("sem", "lab", entrega=self.hoy - timedelta(days=30))
        self.ICP.set_param(DELAY_SINCE_PARAM, self.hoy.isoformat())
        self.assertFalse(tx.shrimp_delay_cancel_cause())
        self.cron(self.hoy)
        self.assertFalse(self.strikes(tx))


@tagged("post_install", "-at_install")
class TestStrikes(RetrasoCommon):

    def test_strike_por_atraso_sin_aviso_una_vez(self):
        entrega = self.hoy + timedelta(days=1)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.cron(entrega + timedelta(days=2))
        self.assertFalse(self.strikes(tx), "dentro de la gracia")
        self.cron(entrega + timedelta(days=3))
        st = self.strikes(tx)
        self.assertEqual(st.mapped("reason"), ["unnotified_delay"])
        self.assertTrue(st.is_active)
        self.assertTrue(st.message_ids.filtered(lambda m: "Strike por la venta" in (m.subject or "")))
        self.assertIn("Notificar retraso", st.message_ids[0].body)
        self.assertTrue(self.mensajes(tx, "Puedes cancelar sin costo"))
        self.cron(entrega + timedelta(days=4))
        self.assertEqual(len(self.strikes(tx)), 1, "una vez por compra")
        with freeze_time(datetime.combine(entrega + timedelta(days=4), datetime.min.time())
                         + timedelta(hours=17)):
            self.assertEqual(tx.shrimp_delay_cancel_cause(), "unnotified")

    def test_strike_por_tope_y_ninguno_por_aviso_aceptado(self):
        entrega = self.hoy + timedelta(days=1)
        tx = self.comprar("sem", "lab", entrega=entrega)
        tope = entrega + timedelta(days=15)
        self.avisar(tx, tope)
        self.comprador(tx).action_delay_buyer_accept(actor=self.s["lab"])
        self.cron(tope)
        self.assertFalse(self.strikes(tx), "retraso avisado y aceptado")
        self.cron(tope + timedelta(days=1))
        self.assertEqual(self.strikes(tx).mapped("reason"), ["delay_over_cap"])
        with freeze_time(datetime.combine(tope + timedelta(days=1), datetime.min.time())
                         + timedelta(hours=17)):
            self.assertEqual(tx.shrimp_delay_cancel_cause(), "over_cap")

    def test_vencimiento_y_metrica_a_tiempo(self):
        tx = self.comprar("sem", "lab")
        strike = self.Strike._shrimp_add_strike(tx, "unnotified_delay")
        self.assertEqual(self.Strike._shrimp_active_count(self.s["sem"]), 1)
        with freeze_time(fields.Datetime.now() + timedelta(days=91)):
            self.assertFalse(strike.with_context({}).is_active)
            self.assertEqual(self.Strike._shrimp_active_count(self.s["sem"]), 0)
        # «Entregas a tiempo»: la compra con strike no cuenta como a tiempo;
        # una sin retraso sí; una con retraso aceptado no.
        self.comprador(tx).shrimp_confirm_reception(actor=self.s["lab"])
        tx_ok = self.comprar("sem", "lab")
        self.comprador(tx_ok).shrimp_confirm_reception(actor=self.s["lab"])
        tx_avisada = self.comprar("sem", "lab", entrega=self.hoy + timedelta(days=1))
        self.avisar(tx_avisada, self.hoy + timedelta(days=3))
        self.comprador(tx_avisada).action_delay_buyer_accept(actor=self.s["lab"])
        self.vendedor(tx_avisada).action_mark_dispatched(actor=self.s["sem"])
        self.comprador(tx_avisada).shrimp_confirm_reception(actor=self.s["lab"])
        self.assertTrue(tx_ok._shrimp_delivered_on_time())
        self.assertFalse(tx._shrimp_delivered_on_time())
        self.assertFalse(tx_avisada._shrimp_delivered_on_time())
        self.s["sem"].invalidate_recordset()
        self.assertLess(self.s["sem"].shrimp_ontime_pct, self.s["sem"].shrimp_delivery_ok_pct)

    def test_umbrales_anulacion_y_suspension(self):
        Product = self.env["shrimp.product"].sudo()
        self.ICP.set_param("shrimp_marketplace.featured_min_reviews", "0")
        propio = producto(self.env, self.s["sem"], nombre="Nauplio del vendedor con strikes",
                          etapa="shrimp_marketplace.shrimp_stage_nauplio")
        otro = producto(self.env, self.s["lab2"], nombre="Larva de otro", seller_role="laboratorio")
        destacados = lambda: [f["partner"] for f in Product._shrimp_featured_sellers("nauplio", limit=50)]  # noqa: E731
        self.assertIn(self.s["sem"], destacados())
        txs = [self.comprar("sem", "lab") for _i in range(5)]
        for tx in txs[:2]:
            self.Strike._shrimp_add_strike(tx, "unnotified_delay")
        self.assertFalse(self.s["sem"].shrimp_strike_penalized)
        tercero = self.Strike._shrimp_add_strike(txs[2], "buyer_cancel_delay")
        self.s["sem"].invalidate_recordset()
        self.assertTrue(self.s["sem"].shrimp_strike_penalized)
        self.assertIn("ADVERTENCIA", tercero.message_ids[0].body)
        # Penalizado: fuera de destacados y al final del orden.
        self.assertNotIn(self.s["sem"], destacados())
        ordenados = Product.shrimp_rank_with_strikes(propio | otro)
        self.assertEqual(ordenados[-1], propio)
        self.assertFalse(self.s["sem"].shrimp_publishing_suspended)
        self.Strike._shrimp_add_strike(txs[3], "unnotified_delay")
        quinto = self.Strike._shrimp_add_strike(txs[4], "delay_over_cap")
        self.s["sem"].invalidate_recordset()
        self.assertTrue(self.s["sem"].shrimp_publishing_suspended)
        self.assertIn("SUSPENDIDA", quinto.message_ids[0].body)
        sus = self.env["shrimp.seller.suspension"].sudo().search([("partner_id", "=", self.s["sem"].id)])
        self.assertEqual(sus.state, "active")
        self.assertEqual(sus.strike_count, 5)
        # Suspendido: no publica, no sale en el catálogo, no se le compra.
        borrador = producto(self.env, self.s["sem"], nombre="Nuevo lote", publicado=False)
        with self.assertRaises(ValidationError):
            borrador.with_user(self.s["u_sem"]).sudo().action_publish()
        visibles = Product.search(Product._shrimp_published_domain())
        self.assertNotIn(propio, visibles)
        self.assertIn(otro, visibles)
        self.assertIn("suspendida", propio.motivo_no_comprable(self.s["lab"]))
        # Las ventas en curso siguen.
        self.vendedor(txs[0]).action_mark_dispatched(actor=self.s["sem"])
        self.comprador(txs[0]).shrimp_confirm_reception(actor=self.s["lab"])
        self.assertEqual(txs[0].state, "done")
        # Anular: solo la plataforma y con justificación.
        with self.assertRaises(AccessError):
            quinto.with_user(self.s["u_sem"]).action_annul("No fue mi culpa")
        with self.assertRaises(UserError):
            quinto.action_annul()
        quinto.action_annul("Cierre de vía decretado por la autoridad")
        self.assertTrue(quinto.annulled)
        self.assertFalse(quinto.is_active)
        self.assertEqual(self.Strike._shrimp_active_count(self.s["sem"]), 4)
        # D-42: con 4 activos (bajo el umbral de 5) se levanta sola.
        self.s["sem"].invalidate_recordset()
        self.assertFalse(self.s["sem"].shrimp_publishing_suspended, "se levanta sola")
        self.assertEqual(sus.state, "lifted")
        self.assertFalse(sus.lifted_by_id)
        # La plataforma también puede levantarla a mano (nueva suspensión).
        self.Strike._shrimp_add_strike(self.comprar("sem", "lab"), "seller_cancel")
        self.s["sem"].invalidate_recordset()
        self.assertTrue(self.s["sem"].shrimp_publishing_suspended)
        sus = self.env["shrimp.seller.suspension"].sudo().search(
            [("partner_id", "=", self.s["sem"].id), ("state", "=", "active")])
        with self.assertRaises(AccessError):
            self.s["sem"].with_user(self.s["u_sem"]).action_lift_publishing_suspension()
        self.s["sem"].action_lift_publishing_suspension()
        self.s["sem"].invalidate_recordset()
        self.assertFalse(self.s["sem"].shrimp_publishing_suspended)
        self.assertEqual(sus.state, "lifted")
        self.assertIn(propio, Product.search(Product._shrimp_published_domain()))
        borrador.with_user(self.s["u_sem"]).sudo().action_publish()
        self.assertEqual(borrador.state, "published")
        self.assertFalse(propio.motivo_no_comprable(self.s["lab"]))


@tagged("post_install", "-at_install")
class TestRetrasoSubcuentas(RetrasoCommon):

    def test_permisos_y_destinatarios(self):
        sem = self.s["sem"]
        Member = self.env["shrimp.account.member"].sudo()
        Member._shrimp_ensure(self.s["u_sem"], sem, is_admin=True)
        solo_ver = subusuario(self.env, sem, "solo.ver.rd", {"semillero": {"view"}})
        ventas = subusuario(self.env, sem, "ventas.rd", {"semillero": {"view", "sell"}})
        lab = self.s["lab"]
        Member._shrimp_ensure(self.s["u_lab"], lab, is_admin=True)
        sin_compra = subusuario(self.env, lab, "lab.ver.rd", {"laboratorio": {"view"}})
        entrega = self.hoy + timedelta(days=3)
        tx = self.comprar("sem", "lab", entrega=entrega)
        with self.assertRaises(AccessError):
            tx.with_user(solo_ver).sudo().action_notify_delay(
                entrega + timedelta(days=2), "transport", actor=sem)
        tx.with_user(ventas).sudo().action_notify_delay(entrega + timedelta(days=2), "transport",
                                                        actor=sem)
        with self.assertRaises(AccessError):
            tx.with_user(sin_compra).sudo().action_delay_buyer_accept(actor=lab)
        tx.with_user(self.s["u_lab"]).sudo().action_delay_buyer_accept(actor=lab)
        msg = self.mensajes(tx, "el comprador aceptó la nueva fecha")
        self.assertIn(ventas.partner_id, msg.partner_ids)
        self.assertNotIn(solo_ver.partner_id, msg.partner_ids)
        strike = self.Strike._shrimp_add_strike(tx, "unnotified_delay")
        destinatarios = strike._shrimp_seller_users()
        self.assertIn(ventas.partner_id, destinatarios)
        self.assertIn(sem, destinatarios)
        self.assertNotIn(solo_ver.partner_id, destinatarios)

    def test_mi_panel(self):
        Dash = self.env["shrimp.dashboard"]
        entrega = self.hoy + timedelta(days=3)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.avisar(tx, entrega + timedelta(days=2))
        datos = Dash._panel_data(self.s["lab"], "laboratorio")
        self.assertIn("delay_to_answer", [a.get("key") for a in datos["alerts"]])
        self.comprador(tx).action_delay_buyer_counter(entrega + timedelta(days=1), actor=self.s["lab"])
        datos = Dash._panel_data(self.s["sem"], "semillero")
        self.assertIn("delay_counter", [a.get("key") for a in datos["alerts"]])
        self.Strike._shrimp_add_strike(tx, "unnotified_delay")
        datos = Dash._panel_data(self.s["sem"], "semillero")
        kpi = [k for k in datos["kpis"] if k["key"] == "strikes"]
        self.assertEqual(kpi[0]["value"], "1")
        # El comprador no ve los strikes del vendedor.
        datos = Dash._panel_data(self.s["lab"], "laboratorio")
        self.assertFalse([k for k in datos["kpis"] if k["key"] == "strikes" and k["value"] == "1"])


@tagged("post_install", "-at_install")
class TestBorradoresLegales(RetrasoCommon):

    def test_borradores_una_vez_y_sin_publicar(self):
        Doc = self.env["shrimp.legal.document"].sudo()
        Version = self.env["shrimp.legal.document.version"].sudo()
        Policy = self.env["shrimp.delay.policy"]
        self.assertTrue(self.ICP.get_param(DONE_PARAM), "los creó la instalación / el -u")
        for codigo in ("contract", "terms"):
            doc = Doc.search([("code", "=", codigo)])
            borrador = doc.version_ids.filtered(lambda v: v.state == "draft" and MARKER in (v.content or ""))
            self.assertEqual(len(borrador), 1, codigo)
            self.assertNotIn(MARKER, doc.current_version_id.content or "", "la publicada no cambia")
            self.assertIn("revisar con abogado", borrador.change_summary.lower())
            self.assertIn("Strikes del vendedor", borrador.content)
            self.assertIn("Cancelación sin costo", borrador.content)
            # Copia el texto vigente.
            self.assertIn("Ley", borrador.content)
        for rol, espera in (("laboratorio", True), ("camaronera", True), ("semillero", False)):
            doc = Doc.search([("doc_type", "=", "annex"), ("role", "=", rol)])
            hay = doc.version_ids.filtered(lambda v: v.state == "draft" and ANNEX_MARKER in (v.content or ""))
            self.assertEqual(bool(hay), espera, rol)
        antes = Version.search_count([])
        self.assertFalse(Policy._shrimp_create_policy_drafts(), "una vez por base")
        Policy._shrimp_create_policy_drafts(force=True)
        self.assertEqual(Version.search_count([]), antes, "no duplica")
        publicadas = Version.search([("state", "=", "published"), ("content", "ilike", MARKER)])
        self.assertFalse(publicadas, "nada se publica solo")


@tagged("post_install", "-at_install")
class TestRetrasoPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "rdp")
        cls.Tx = cls.env["shrimp.transaction"]
        cls.hoy = cls.Tx._shrimp_today()
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param(DELAY_SINCE_PARAM, (cls.hoy - timedelta(days=400)).isoformat())

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def compra(self, entrega):
        lote = producto(self.env, self.s["sem"], nombre="Nauplio portal rd",
                        expected_delivery_date=entrega)
        tx = lote.execute_purchase_flow(self.s["lab"], 3.0)["transaction"]
        tx.sudo().write({"desired_date": entrega})
        return tx

    def post(self, url, texto, **datos):
        datos["csrf_token"] = csrf_de(texto)
        return self.url_open(url, data=datos, allow_redirects=False)

    def test_flujo_portal_completo(self):
        entrega = self.hoy + timedelta(days=3)
        tx = self.compra(entrega)
        pagina = "/marketplace/purchases/%s/traceability" % tx.uuid_ref
        # Vendedor: ve el botón en Ventas y el formulario en la compra.
        self.authenticate(self.s["u_sem"].login, CLAVE)
        ventas = self.url_open("/marketplace/sales")
        self.assertIn("Notificar retraso", ventas.text)
        r = self.url_open(pagina)
        self.assertIn("Notificar retraso de entrega", r.text)
        nueva = entrega + timedelta(days=5)
        # Fuera del tope: error visible, nada cambia.
        r2 = self.post("/marketplace/sales/%s/delay" % tx.uuid_ref, r.text,
                       new_date=(entrega + timedelta(days=20)).isoformat(), reason="aguaje")
        self.assertIn("error=delay", r2.headers.get("Location", ""))
        self.assertFalse(tx.delay_ids)
        r2 = self.post("/marketplace/sales/%s/delay" % tx.uuid_ref, r.text,
                       new_date=nueva.isoformat(), reason="aguaje", note="Aguaje",
                       next=pagina + "#retraso")
        self.assertIn("saved=delay_notified", r2.headers.get("Location", ""))
        self.assertEqual(tx.shrimp_delay_open().state, "proposed")
        # Un tercero no responde.
        self.authenticate(self.s["u_lab2"].login, CLAVE)
        r = self.url_open("/marketplace/purchases")
        r3 = self.post("/marketplace/purchases/%s/delay/accept" % tx.uuid_ref, r.text)
        self.assertEqual(r3.status_code, 403)
        # Comprador: Mi panel, Mis compras y la página.
        self.authenticate(self.s["u_lab"].login, CLAVE)
        panel = self.url_open("/my/dashboard")
        self.assertIn("avisó un retraso", panel.text)
        lista = self.url_open("/marketplace/purchases")
        self.assertIn("Retraso: responder", lista.text)
        r = self.url_open(pagina)
        self.assertIn("Aceptar nueva fecha", r.text)
        self.assertIn("Proponer otra fecha", r.text)
        contra = entrega + timedelta(days=1)
        r2 = self.post("/marketplace/purchases/%s/delay/counter-offer" % tx.uuid_ref, r.text,
                       counter_date=contra.isoformat(), note="Antes, por favor")
        self.assertIn("saved=delay_countered", r2.headers.get("Location", ""))
        # Vendedor: responde la contrapropuesta (la rechaza).
        self.authenticate(self.s["u_sem"].login, CLAVE)
        ventas = self.url_open("/marketplace/sales")
        self.assertIn("Responder contrapropuesta", ventas.text)
        r = self.url_open(pagina)
        self.assertIn("Rechazar", r.text)
        r2 = self.post("/marketplace/sales/%s/delay/reject" % tx.uuid_ref, r.text)
        self.assertIn("saved=delay_counter_rejected", r2.headers.get("Location", ""))
        # Comprador: cancela sin costo.
        self.authenticate(self.s["u_lab"].login, CLAVE)
        lista = self.url_open("/marketplace/purchases")
        self.assertIn("Cancelar sin costo", lista.text)
        r = self.url_open(pagina)
        self.assertIn("Puedes cancelar esta compra sin costo", r.text)
        r2 = self.post("/marketplace/purchases/%s/cancel" % tx.uuid_ref, r.text, note="Ya no")
        self.assertIn("saved=delay_cancelled", r2.headers.get("Location", ""))
        self.assertEqual(tx.state, "cancel")
        r = self.url_open(pagina)
        self.assertIn("Compra cancelada sin costo", r.text)
        self.assertIn("El vendedor rechazó la contrapropuesta", r.text)
        # Perfil público: sin strikes a la vista.
        self.authenticate(None, None)
        perfil = self.url_open("/marketplace/sellers/%s" % self.s["sem"].uuid_ref)
        self.assertEqual(perfil.status_code, 200)
        self.assertNotIn("strike", perfil.text.lower())

    def test_catalogo_oculta_suspendidos(self):
        lote = producto(self.env, self.s["sem"], nombre="Nauplio suspendido rdp",
                        etapa="shrimp_marketplace.shrimp_stage_nauplio")
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self.url_open("/marketplace?q=suspendido+rdp")
        self.assertIn(lote.name, r.text)
        self.env["shrimp.seller.suspension"].sudo()._shrimp_open(self.s["sem"], 5)
        self.env.cr.cache.pop(SUSPENDED_CACHE_KEY, None)
        r = self.url_open("/marketplace?q=suspendido+rdp")
        self.assertNotIn(lote.name, r.text)


@tagged("post_install", "-at_install")
class TestRetrasoAjustes(RetrasoCommon):

    def test_ajustes_se_guardan_y_se_respetan(self):
        ajustes = self.env["res.config.settings"].create({
            "shrimp_delay_max_notices": 1, "shrimp_delay_max_days": 5,
            "shrimp_delay_response_hours": 24, "shrimp_delay_grace_days": 0,
            "shrimp_strike_expiry_days": 30, "shrimp_strike_warning": 2,
            "shrimp_strike_suspension": 4})
        ajustes.execute()
        self.assertEqual(self.ICP.get_param("shrimp_marketplace.delay_grace_days"), "0")
        self.assertEqual(self.Strike._shrimp_thresholds(), (2, 4))
        entrega = self.hoy + timedelta(days=2)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.assertEqual(tx.shrimp_delay_cap_date(), entrega + timedelta(days=5))
        with self.assertRaises(ValidationError):
            self.avisar(tx, entrega + timedelta(days=6))
        aviso = self.avisar(tx, entrega + timedelta(days=5))
        self.assertAlmostEqual(
            (aviso.buyer_deadline - aviso.notice_date).total_seconds(), 24 * 3600, delta=5)
        self.comprador(tx).action_delay_buyer_accept(actor=self.s["lab"])
        self.assertFalse(tx.shrimp_delay_can_notify(), "un solo aviso")
        # La suspensión no puede pedir menos strikes que la advertencia.
        with self.assertRaises(ValidationError):
            self.env["res.config.settings"].create({
                "shrimp_strike_warning": 4, "shrimp_strike_suspension": 3}).execute()
