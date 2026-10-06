"""Mover producto propio entre los perfiles de la misma cuenta.

«Si soy laboratorio y camaronera y lo compré como laboratorio, debo poder
moverlo a como camaronera»: transferencia INTERNA (sin precio, comisión,
factura ni verificación), trazable, total o parcial, solo a un perfil
aprobado que pueda tener ese estadío; y el cambio de perfil de un producto
publicado entero, solo si el perfil destino lo vende y no hay operaciones
abiertas.
"""
from datetime import date

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

from .common import CLAVE, crear_socios, csrf_de, producto

TEXTO = "Transferencia interna: Laboratorio → Camaronera (misma empresa)"


def _multi(socio, rol):
    return socio._shrimp_request_role(rol, {
        "shrimp_representante": "Rep PT", "shrimp_telefono": "04-5555555",
        "shrimp_razon_social": socio.name + " S.A.", "shrimp_ubicacion": "Guayas"})


def _comprar_y_recibir(product, comprador, qty, **kw):
    tx = product.execute_purchase_flow(comprador, qty, **kw)["transaction"]
    tx.action_receive()
    return tx


class _Base(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "pt")
        cls.multi = cls.s["lab"]
        _multi(cls.multi, "camaronera")
        cls.larva_sem = producto(cls.env, cls.s["sem"], nombre="PL12 semillero PT",
                                 expected_delivery_date=date.today())
        # Compra como LABORATORIO (su perfil activo).
        cls.tx1 = _comprar_y_recibir(cls.larva_sem, cls.multi, 40.0)
        cls.P = cls.tx1.result_product_id
        cls.L = cls.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", cls.tx1.stock_move_ids.ids), ("owner_id", "=", cls.multi.id)])


@tagged("post_install", "-at_install")
class TestProfileTransferLot(_Base):

    def test_lote_comprado_queda_en_el_perfil_con_el_que_compro(self):
        self.assertEqual(self.tx1.buyer_role, "laboratorio")
        self.assertEqual(self.P.seller_role, "laboratorio")
        self.assertEqual(self.L.held_role, "laboratorio")
        self.assertEqual([c for c, _l in self.L._shrimp_profile_transfer_targets()], ["camaronera"])

    def test_mover_todo_el_lote(self):
        t = self.L.action_transfer_profile("camaronera", reason="La siembro")
        self.assertEqual((t.kind, t.from_role, t.to_role), ("lot", "laboratorio", "camaronera"))
        self.assertAlmostEqual(t.qty, 40.0)
        self.assertEqual(self.L.state, "consumed")
        self.assertAlmostEqual(self.L.available_qty, 0.0)
        hijo = t.new_lot_ids
        self.assertEqual(len(hijo), 1)
        self.assertEqual((hijo.held_role, hijo.parent_lot_id, hijo.owner_id),
                         ("camaronera", self.L, self.multi))
        self.assertAlmostEqual(hijo.available_qty, 40.0)
        # Producto propio del perfil camaronera, en borrador, que nace del P.
        P2 = hijo.product_id
        self.assertNotEqual(P2, self.P)
        self.assertEqual((P2.seller_role, P2.state, P2.profile_origin_product_id),
                         ("camaronera", "draft", self.P))
        self.assertAlmostEqual(P2.available_qty, 40.0)
        self.assertAlmostEqual(self.P.available_qty, 0.0)
        # Movimiento interno trazable, encadenado a la compra original.
        move = t.move_ids
        self.assertEqual((move.move_type, move.from_role, move.to_role, move.direction),
                         ("profile_transfer", "laboratorio", "camaronera", "out"))
        self.assertEqual(move.parent_move_id, self.L.origin_move_id)
        self.assertEqual(hijo.origin_move_id, move)
        self.assertEqual(move.reason, TEXTO)
        self.assertFalse(move.dest_partner_id)
        self.assertFalse(move.transaction_id, "no es una venta")
        # Ni transacción ni cobro nuevos.
        self.assertEqual(self.env["shrimp.transaction"].search_count([
            ("buyer_partner_id", "=", self.multi.id)]), 1)
        # Constancia en el chatter del lote y del producto.
        self.assertTrue(any(TEXTO in (m.body or "") for m in self.L.message_ids))
        self.assertTrue(any(TEXTO in (m.body or "") for m in P2.message_ids))

    def test_mover_parcial_reutiliza_el_producto_del_perfil(self):
        t1 = self.L.action_transfer_profile("camaronera", qty=15.0)
        self.assertAlmostEqual(self.L.available_qty, 25.0)
        self.assertEqual(self.L.state, "available")
        t2 = self.L.action_transfer_profile("camaronera", qty=5.0)
        self.assertEqual(t1.target_product_id, t2.target_product_id, "mismo producto destino")
        self.assertAlmostEqual(t1.target_product_id.available_qty, 20.0)
        self.assertAlmostEqual(self.L.available_qty, 20.0)
        # Volver al laboratorio: el lote vuelve al producto raíz (P).
        hijo = t1.new_lot_ids
        t3 = hijo.action_transfer_profile("laboratorio", qty=10.0)
        self.assertEqual(t3.target_product_id, self.P)
        self.assertEqual(t3.new_lot_ids.held_role, "laboratorio")
        self.assertAlmostEqual(self.P.available_qty, 30.0)
        # No más de lo que hay.
        with self.assertRaises(ValidationError):
            self.L.action_transfer_profile("camaronera", qty=999.0)
        with self.assertRaises(ValidationError):
            self.L.action_transfer_profile("camaronera", qty=0)

    def test_no_a_un_perfil_pendiente_ni_ajeno(self):
        self.env["shrimp.partner.role"].create({
            "partner_id": self.multi.id, "role": "semillero", "state": "pending"})
        with self.assertRaisesRegex(ValidationError, "pendiente"):
            self.L.action_transfer_profile("semillero")
        # El mismo perfil: nada que mover.
        with self.assertRaises(ValidationError):
            self.L.action_transfer_profile("laboratorio")
        # Un perfil que la cuenta no tiene.
        ajeno = producto(self.env, self.s["lab2"], nombre="Larva ajena PT")
        lote_otro = ajeno.stock_lot_ids[:1]
        with self.assertRaisesRegex(ValidationError, "no tiene aprobado"):
            lote_otro.action_transfer_profile("camaronera")
        self.assertAlmostEqual(self.L.available_qty, 40.0)

    def test_no_a_un_perfil_que_no_puede_tener_el_estadio(self):
        nauplio = producto(self.env, self.s["sem"], nombre="Nauplio PT",
                           etapa="shrimp_marketplace.shrimp_stage_nauplio",
                           expected_delivery_date=date.today())
        tx = _comprar_y_recibir(nauplio, self.multi, 10.0)
        lote = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.multi.id)])
        self.assertFalse(lote._shrimp_profile_transfer_targets())
        with self.assertRaisesRegex(ValidationError, "no puede manejar nauplio"):
            lote.action_transfer_profile("camaronera")

    def test_solo_la_cuenta_duena(self):
        ajeno = self.s["u_cam"]
        with self.assertRaises(AccessError):
            self.L.with_user(ajeno).action_transfer_profile(
                "camaronera", actor=self.s["cam"])
        # El dueño desde el portal (sudo con su usuario), sí.
        t = self.L.with_user(self.s["u_lab"]).sudo().action_transfer_profile(
            "camaronera", qty=1.0, actor=self.multi)
        self.assertEqual(t.user_id, self.s["u_lab"])

    def test_sembrar_lo_movido_a_camaronera_consume_el_lote(self):
        hijo = self.L.action_transfer_profile("camaronera", qty=12.0).new_lot_ids
        pond = self.env["shrimp.partner.pond"].create({"partner_id": self.multi.id, "name": "PT-1"})
        alloc = self.env["shrimp.lot.allocation"].create({
            "stock_lot_id": hijo.id, "pond_id": pond.id, "allocated_qty": 12.0})
        self.assertTrue(alloc.sowing_move_id)
        self.assertEqual(hijo.state, "consumed")
        # La siembra cuelga de la transferencia interna en la cadena.
        self.assertEqual(alloc.sowing_move_id.parent_move_id, hijo.origin_move_id)

    def test_trazabilidad_muestra_el_paso_interno(self):
        t = self.L.action_transfer_profile("camaronera")
        data = self.tx1.get_full_traceability_data()
        self.assertIn(t.move_ids, data["moves"])
        self.assertIn(t.new_lot_ids, data["lots"])
        cadena = self.tx1.traceability_chain()
        self.assertEqual([(c["name"], c["role_code"]) for c in cadena], [
            (self.s["sem"].name, "semillero"), (self.multi.name, "laboratorio"),
            (self.multi.name, "camaronera")])
        pasos = self.tx1.shrimp_profile_transfers(data)
        self.assertEqual(pasos[0]["label"], TEXTO)
        # Aguas arriba: una venta posterior del lote vuelto al laboratorio
        # enseña las dos transferencias internas y la compra original.
        hijo = t.new_lot_ids
        t2 = hijo.action_transfer_profile("laboratorio")
        self.assertEqual(t2.target_product_id, self.P)
        self.P.with_context(shrimp_publish_checked=True).write({"state": "published"})
        tx2 = self.P.execute_purchase_flow(self.s["cam"], 10.0)["transaction"]
        moves = tx2.get_full_traceability_data()["moves"]
        self.assertIn(t.move_ids, moves)
        self.assertIn(t2.move_ids, moves)
        self.assertIn(self.tx1.stock_move_ids, moves)
        self.assertEqual([c["role_code"] for c in tx2.traceability_chain()],
                         ["semillero", "laboratorio", "camaronera", "laboratorio", "camaronera"])
        # El PDF de trazabilidad lo enseña (sin precios: es un paso interno).
        html, _fmt = self.env["ir.actions.report"]._render_qweb_html(
            "shrimp_marketplace.report_shrimp_full_traceability", tx2.ids)
        self.assertIn("Transferencia interna", html.decode())


@tagged("post_install", "-at_install")
class TestProfileChangeProduct(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "pc")
        cls.sem = cls.s["sem"]
        # Semillero que además es laboratorio: los dos venden nauplio.
        _multi(cls.sem, "laboratorio")
        cls.nauplio = producto(cls.env, cls.sem, nombre="Nauplio PC",
                               etapa="shrimp_marketplace.shrimp_stage_nauplio",
                               expected_delivery_date=date.today())

    def test_cambiar_perfil_del_producto(self):
        self.assertEqual(self.nauplio.seller_role, "semillero")
        self.assertIn("laboratorio", [c for c, _l in self.nauplio._shrimp_profile_change_targets()])
        # Una compra abierta (sin recibir) lo impide.
        tx = self.nauplio.execute_purchase_flow(self.s["lab2"], 30.0)["transaction"]
        self.assertEqual(tx.state, "confirmed")
        with self.assertRaisesRegex(ValidationError, "abierta"):
            self.nauplio.action_change_profile("laboratorio")
        tx.action_receive()
        self.assertTrue(self.nauplio.has_purchases(), "seller_role estaba bloqueado por la compra")
        lote = self.env["shrimp.stock.lot"].search([
            ("product_id", "=", self.nauplio.id), ("owner_id", "=", self.sem.id),
            ("state", "=", "available")])
        t = self.nauplio.action_change_profile("laboratorio", reason="Lo vendo como lab")
        self.assertEqual(self.nauplio.seller_role, "laboratorio")
        self.assertEqual((t.kind, t.from_role, t.to_role), ("product", "semillero", "laboratorio"))
        self.assertAlmostEqual(t.qty, 70.0)
        self.assertEqual(lote.state, "consumed")
        self.assertEqual(lote.held_role, "semillero", "el historial conserva el perfil anterior")
        nuevo = t.new_lot_ids
        self.assertEqual((nuevo.product_id, nuevo.held_role), (self.nauplio, "laboratorio"))
        self.assertAlmostEqual(self.nauplio.available_qty, 70.0)
        self.assertEqual(self.nauplio.state, "published")
        # La venta ya hecha conserva el rol con el que se hizo.
        self.assertEqual(tx.seller_role, "semillero")
        # Otras escrituras de seller_role siguen bloqueadas tras la compra.
        with self.assertRaises(ValidationError):
            self.nauplio.write({"seller_role": "semillero"})

    def test_el_destino_tiene_que_vender_ese_estadio(self):
        multi = self.s["lab"]
        _multi(multi, "camaronera")
        larva = producto(self.env, multi, nombre="Larva PC")
        self.assertEqual(larva.seller_role, "laboratorio")
        with self.assertRaisesRegex(ValidationError, "no puede vender larva"):
            larva.action_change_profile("camaronera")
        # Una cantidad sí puede pasar (la camaronera tiene larva para sembrar).
        t = larva.action_transfer_qty_to_profile("camaronera", 20.0)
        self.assertEqual(t.target_product_id.seller_role, "camaronera")
        self.assertAlmostEqual(larva.available_qty, 80.0)
        self.assertEqual([c for c, _l in larva.shrimp_profile_move_targets()], ["camaronera"])

    def test_chequeo_activo_bloquea(self):
        self.env["shrimp.check.request"].create({
            "product_id": self.nauplio.id, "seller_partner_id": self.sem.id,
            "buyer_partner_id": self.s["lab2"].id, "qty": 5.0})
        with self.assertRaisesRegex(ValidationError, "chequeo"):
            self.nauplio.action_change_profile("laboratorio")
        # Mover una cantidad sí, pero sin tocar lo reservado.
        self.assertAlmostEqual(self.nauplio.available_qty, 95.0)
        with self.assertRaises(ValidationError):
            self.nauplio.stock_lot_ids[:1].action_transfer_profile("laboratorio", qty=100.0)
        self.nauplio.stock_lot_ids[:1].action_transfer_profile("laboratorio", qty=95.0)


@tagged("post_install", "-at_install")
class TestProfileTransferPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "pp")
        cls.multi = cls.s["lab"]
        _multi(cls.multi, "camaronera")
        larva = producto(cls.env, cls.s["sem"], nombre="PL12 portal PT",
                         expected_delivery_date=date.today())
        cls.tx = _comprar_y_recibir(larva, cls.multi, 40.0)
        cls.lote = cls.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", cls.tx.stock_move_ids.ids), ("owner_id", "=", cls.multi.id)])

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def test_mover_desde_mi_inventario(self):
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self.url_open("/marketplace/my-lots")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Mover a otro perfil", r.text)
        self.assertIn("/marketplace/my-lots/%s/move-profile" % self.lote.uuid_ref, r.text)
        token = csrf_de(r.text)
        ruta = "/marketplace/my-lots/%s/move-profile" % self.lote.uuid_ref
        # Sin CSRF no se mueve nada.
        r = self.url_open(ruta, data={"to_role": "camaronera", "qty": "10"})
        self.assertNotEqual(r.status_code, 200)
        self.assertAlmostEqual(self.lote.available_qty, 40.0)
        # Con CSRF, parcial.
        r = self.url_open(ruta, data={"csrf_token": token, "to_role": "camaronera", "qty": "10",
                                      "reason": "Para sembrar"}, allow_redirects=False)
        self.assertIn("pt=ok", r.headers.get("Location", ""))
        self.assertAlmostEqual(self.lote.available_qty, 30.0)
        t = self.env["shrimp.profile.transfer"].search([("partner_id", "=", self.multi.id)])
        self.assertEqual((t.from_role, t.to_role, t.reason), ("laboratorio", "camaronera", "Para sembrar"))
        # Un perfil inválido vuelve con el motivo, sin mover.
        r = self.url_open(ruta, data={"csrf_token": token, "to_role": "semillero", "qty": "1"})
        self.assertIn("no tiene aprobado", r.text)
        self.assertAlmostEqual(self.lote.available_qty, 30.0)
        # El historial y la trazabilidad privada lo enseñan.
        r = self.url_open("/marketplace/my-lots")
        self.assertIn("Movimientos entre mis perfiles", r.text)
        self.assertIn(TEXTO, r.text)
        r = self.url_open("/marketplace/purchases/%s/traceability" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn(TEXTO, r.text)

    def test_otro_socio_no_mueve_mi_lote(self):
        self.authenticate(self.s["u_cam"].login, CLAVE)
        r = self.url_open("/marketplace/my-lots")
        token = csrf_de(r.text)
        r = self.url_open("/marketplace/my-lots/%s/move-profile" % self.lote.uuid_ref,
                          data={"csrf_token": token, "to_role": "camaronera"})
        self.assertEqual(r.status_code, 404)
        self.assertAlmostEqual(self.lote.available_qty, 40.0)

    def test_mover_desde_la_ficha_del_producto(self):
        P = self.tx.result_product_id
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self.url_open("/marketplace/product/%s" % P.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn("ptProductModal", r.text)
        token = csrf_de(r.text)
        r = self.url_open("/marketplace/products/%s/move-profile" % P.uuid_ref, data={
            "csrf_token": token, "to_role": "camaronera", "mode": "qty", "qty": "5"},
            allow_redirects=False)
        self.assertIn("pt=ok", r.headers.get("Location", ""))
        self.assertAlmostEqual(P.available_qty, 35.0)
        # Producto entero: la larva no la vende una camaronera -> explica el motivo.
        r = self.url_open("/marketplace/products/%s/move-profile" % P.uuid_ref, data={
            "csrf_token": token, "to_role": "camaronera", "mode": "all"})
        self.assertIn("no puede vender larva", r.text)
        self.assertEqual(P.seller_role, "laboratorio")
