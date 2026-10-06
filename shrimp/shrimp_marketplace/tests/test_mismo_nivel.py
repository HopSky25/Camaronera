"""Compras al MISMO nivel de la cadena (semillero ↔ semillero, laboratorio ↔
laboratorio; camaronera ↔ camaronera se prueba en shrimp_packer, donde el
adulto exige verificación).

- la matriz habilita la pareja y la transacción toma su tipo propio
  (<rol>_to_<rol>), con los roles guardados;
- el comprador recibe un producto PROPIO que puede volver a vender, y la
  trazabilidad muestra los dos eslabones del mismo nivel;
- las parejas prohibidas siguen prohibidas y comprarse a sí mismo (la misma
  entidad comercial) nunca se permite;
- el catálogo y el botón «Comprar» siguen la misma regla.
"""
from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, ShrimpSecurityCommon, crear_socios, producto
from .test_coherencia import cargar_migracion


def otro_semillero(env, sufijo="mn"):
    return env["res.partner"].create({
        "name": "Semillero dos %s" % sufijo, "is_company": True,
        "email": "sem2.%s@prueba.test" % sufijo, "vat_or_id": "0911100009%s" % sufijo,
        "shrimp_user_type": "semillero"})


def recibir(tx):
    """Confirma la recepción ya (la fecha de entrega del lote es futura; se
    pone ayer porque el «hoy» UTC puede ir por delante del de la zona)."""
    tx.sudo().write({"desired_date": date.today() - timedelta(days=1)})
    tx.action_receive()
    return tx.result_product_id


@tagged("post_install", "-at_install")
class TestMismoNivelMarketplace(ShrimpSecurityCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sem2 = otro_semillero(cls.env)
        cls.lote_lab = producto(cls.env, cls.s["lab"], nombre="Larva lab MN")
        cls.Tx = cls.env["shrimp.transaction"]

    # ------------------------------------------------------------------
    def test_matriz_y_tipo_por_pareja_de_roles(self):
        P = self.env["res.partner"]
        self.assertTrue(P._shrimp_type_can("semillero", "buy_from_semillero"))
        self.assertTrue(P._shrimp_type_can("laboratorio", "buy_from_laboratorio"))
        self.assertTrue(P._shrimp_type_can("camaronera", "buy_from_camaronera"))
        # Las de siempre siguen.
        self.assertTrue(P._shrimp_type_can("laboratorio", "buy_from_semillero"))
        self.assertTrue(P._shrimp_type_can("camaronera", "buy_from_laboratorio"))
        # Saltarse eslabones o comprar aguas arriba, no.
        self.assertFalse(P._shrimp_type_can("camaronera", "buy_from_semillero"))
        self.assertFalse(P._shrimp_type_can("semillero", "buy_from_laboratorio"))
        tipo = self.Tx._shrimp_tx_type
        self.assertEqual(tipo("semillero", "semillero"), "semillero_to_semillero")
        self.assertEqual(tipo("laboratorio", "laboratorio"), "laboratorio_to_laboratorio")
        self.assertEqual(tipo("camaronera", "camaronera"), "camaronera_to_camaronera")
        self.assertEqual(tipo("semillero", "laboratorio"), "semillero_to_laboratorio")
        self.assertEqual(tipo("laboratorio", "camaronera"), "laboratorio_to_camaronera")
        self.assertEqual(tipo("camaronera", "empacadora"), "camaronera_to_buyer")

    def test_semillero_a_semillero_de_punta_a_punta(self):
        self.assertFalse(self.larva.motivo_no_comprable(self.sem2))
        tx = self.larva.execute_purchase_flow(self.sem2, 20.0)["transaction"]
        self.assertEqual(tx.transaction_type, "semillero_to_semillero")
        self.assertEqual((tx.seller_role, tx.buyer_role), ("semillero", "semillero"))
        self.assertEqual(tx.sold_qty, 20.0, "lo que vende un semillero se registra como vendido")
        self.assertEqual(tx.state, "confirmed")
        self.assertTrue(tx._buyer_can_republish())
        nuevo = recibir(tx)
        self.assertEqual(tx.state, "done")
        # Nace un producto PROPIO del comprador, del rol con el que compró.
        self.assertTrue(nuevo)
        self.assertEqual(nuevo.seller_partner_id, self.sem2)
        self.assertEqual(nuevo.seller_role, "semillero")
        self.assertEqual(nuevo.state, "draft")
        self.assertAlmostEqual(nuevo.available_qty, 20.0)
        lote = nuevo.stock_lot_ids
        self.assertEqual(lote.owner_id, self.sem2)
        self.assertEqual(lote.origin_move_id.transaction_id, tx)
        # Lo revende a un laboratorio: la cadena tiene los dos semilleros.
        nuevo.action_publish()
        tx2 = nuevo.execute_purchase_flow(self.s["lab"], 5.0)["transaction"]
        self.assertEqual(tx2.transaction_type, "semillero_to_laboratorio")
        self.assertEqual(tx2.stock_move_ids.parent_move_id, tx.stock_move_ids)
        cadena = tx2.traceability_chain()
        self.assertEqual([(c["name"], c["role_code"]) for c in cadena], [
            (self.s["sem"].name, "semillero"), (self.sem2.name, "semillero"),
            (self.s["lab"].name, "laboratorio")])

    def test_laboratorio_a_laboratorio_de_punta_a_punta(self):
        self.assertFalse(self.lote_lab.motivo_no_comprable(self.s["lab2"]))
        tx = self.lote_lab.execute_purchase_flow(self.s["lab2"], 30.0)["transaction"]
        self.assertEqual(tx.transaction_type, "laboratorio_to_laboratorio")
        self.assertEqual((tx.seller_role, tx.buyer_role), ("laboratorio", "laboratorio"))
        # La comisión se cobra como en cualquier compra.
        self.assertEqual(len(tx.charge_ids.filtered(lambda c: c.charge_type == "commission")),
                         1 if self.lote_lab.uom_id.commission_cents else 0)
        nuevo = recibir(tx)
        self.assertEqual((nuevo.seller_partner_id, nuevo.seller_role), (self.s["lab2"], "laboratorio"))
        self.assertEqual(nuevo.stage_id, self.lote_lab.stage_id)
        nuevo.action_publish()
        tx2 = nuevo.execute_purchase_flow(self.s["cam"], 10.0)["transaction"]
        self.assertEqual(tx2.transaction_type, "laboratorio_to_camaronera")
        self.assertEqual([c["role_code"] for c in tx2.traceability_chain()],
                         ["laboratorio", "laboratorio", "camaronera"])

    # ------------------------------------------------------------------
    def test_parejas_prohibidas_siguen_prohibidas(self):
        # Camaronera -> lote de semillero; semillero -> lote de laboratorio.
        for lote, comprador in ((self.larva, self.s["cam"]), (self.lote_lab, self.s["sem"])):
            self.assertTrue(lote.motivo_no_comprable(comprador))
            with self.assertRaises(ValidationError):
                lote.execute_purchase_flow(comprador, 1.0)
        # El tipo se deriva de la pareja de roles: quien graba el tipo «de la
        # cadena» para una compra de mismo nivel (datos viejos, demos,
        # integraciones) obtiene el correcto, y al revés.
        tx = self.Tx.create({
            "transaction_type": "semillero_to_laboratorio", "product_id": self.larva.id,
            "seller_partner_id": self.s["sem"].id, "buyer_partner_id": self.sem2.id,
            "transaction_qty": 1.0, "sold_qty": 1.0})
        self.assertEqual(tx.transaction_type, "semillero_to_semillero")
        # Cambiar el comprador lo recalcula.
        tx.write({"buyer_partner_id": self.s["lab"].id})
        self.assertEqual((tx.buyer_role, tx.transaction_type), ("laboratorio", "semillero_to_laboratorio"))
        # Un tipo que no es del rol del vendedor no se corrige: se rechaza.
        with self.assertRaises(ValidationError):
            self.Tx.create({
                "transaction_type": "laboratorio_to_laboratorio", "product_id": self.larva.id,
                "seller_partner_id": self.s["sem"].id, "buyer_partner_id": self.sem2.id,
                "transaction_qty": 1.0})

    def test_no_se_compra_a_si_mismo(self):
        P = self.env["res.partner"]
        # Un contacto de la propia empresa vendedora.
        hijo = P.create({"name": "Técnico del semillero MN", "parent_id": self.s["sem"].id})
        self.assertTrue(self.s["sem"]._shrimp_same_entity_as(hijo))
        self.assertFalse(self.s["sem"]._shrimp_same_entity_as(self.sem2))
        motivo = self.larva.motivo_no_comprable(hijo)
        self.assertIn("No puedes comprarte a ti mismo", motivo)
        with self.assertRaises(ValidationError):
            self.larva.execute_purchase_flow(hijo, 1.0)
        with self.assertRaises(ValidationError):
            self.Tx.create({
                "transaction_type": "semillero_to_semillero", "product_id": self.larva.id,
                "seller_partner_id": self.s["sem"].id, "buyer_partner_id": hijo.id,
                "seller_role": "semillero", "buyer_role": "semillero",
                "transaction_qty": 1.0, "sold_qty": 1.0})
        # La misma cuenta con otro perfil (laboratorio + camaronera): su
        # propio lote no lo compra con ninguno.
        lab = self.s["lab"]
        lab._shrimp_request_role("camaronera", {
            "shrimp_representante": "Rep MN", "shrimp_telefono": "04-3333333"})
        self.assertEqual(self.lote_lab.motivo_no_comprable(lab), "Es tu propio lote.")
        self.assertTrue(self.lote_lab.motivo_no_comprable(lab, role="camaronera"))
        with self.assertRaises(ValidationError):
            self.lote_lab.execute_purchase_flow(lab, 1.0, buyer_role="camaronera")
        # El mismo RUC es la misma entidad aunque sean dos fichas.
        gemelo = P.create({"name": "Ficha duplicada sem MN", "vat_or_id": self.s["sem"].vat_or_id})
        self.assertTrue(self.s["sem"]._shrimp_same_entity_as(gemelo))

    def test_migracion_retipifica_compras_de_mismo_nivel(self):
        tx_mismo = self.larva.execute_purchase_flow(self.sem2, 2.0)["transaction"]
        tx_cadena = self.larva.execute_purchase_flow(self.s["lab"], 2.0)["transaction"]
        self.env.flush_all()
        # Una compra vieja de mismo nivel que quedó con el tipo de la cadena.
        self.env.cr.execute("UPDATE shrimp_transaction SET transaction_type = "
                            "'semillero_to_laboratorio' WHERE id = %s", [tx_mismo.id])
        mig = cargar_migracion("shrimp_marketplace", "19.0.1.9.0", "post")
        mig.migrate(self.env.cr, "19.0.1.8.0")
        mig.migrate(self.env.cr, "19.0.1.8.0")       # idempotente
        self.env.invalidate_all()
        self.assertEqual(tx_mismo.transaction_type, "semillero_to_semillero")
        self.assertEqual(tx_cadena.transaction_type, "semillero_to_laboratorio")


@tagged("post_install", "-at_install")
class TestMismoNivelPortal(HttpCase):
    """Catálogo y ficha: el laboratorio ve y puede comprar la larva de otro
    laboratorio; el semillero no la ve."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "mnh")
        cls.lote = producto(cls.env, cls.s["lab2"], nombre="Larva mismo nivel MNH")

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def test_catalogo_y_boton_comprar(self):
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self.url_open("/marketplace?q=MNH")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Larva mismo nivel MNH", r.text)
        r = self.url_open("/marketplace/product/%s" % self.lote.uuid_ref)
        self.assertIn("/marketplace/buy/%s" % self.lote.uuid_ref, r.text)
        self.assertEqual(self.url_open("/marketplace/buy/%s" % self.lote.uuid_ref).status_code, 200)
        # El semillero no compra larva de laboratorio: ni en el catálogo ni en la ficha.
        self.authenticate(self.s["u_sem"].login, CLAVE)
        self.assertNotIn("Larva mismo nivel MNH", self.url_open("/marketplace?q=MNH").text)
        r = self.url_open("/marketplace/product/%s" % self.lote.uuid_ref)
        self.assertNotIn("/marketplace/buy/%s" % self.lote.uuid_ref, r.text)
        self.assertEqual(self.url_open("/marketplace/buy/%s" % self.lote.uuid_ref).status_code, 403)
        # El dueño tampoco se compra su propio lote.
        self.authenticate(self.s["u_lab2"].login, CLAVE)
        r = self.url_open("/marketplace/product/%s" % self.lote.uuid_ref)
        self.assertNotIn('href="/marketplace/buy/%s"' % self.lote.uuid_ref, r.text)
