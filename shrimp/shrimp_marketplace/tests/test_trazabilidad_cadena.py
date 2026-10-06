"""Trazabilidad de punta a punta y cantidades físicas.

Cubre los hallazgos del escenario semillero -> laboratorio -> camaronera ->
empacadora -> salida:
- la cadena continúa a través de la siembra (cosecha sin movimiento de origen);
- la siembra consume el lote y acumula las anteriores;
- la producción real declarada por el laboratorio;
- la salida / exportación como último eslabón (consume el lote);
- sin filas de evolución por cambios de stock/estado;
- en «Lotes» no aparece el stock que le queda al vendedor;
- certificados sin repetir; formato de fechas (zona local) y números.
"""
import base64
from datetime import date, datetime, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from .common import PDF_MIN, crear_socios, producto


@tagged("post_install", "-at_install")
class TestTrazabilidadCadena(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "trz")
        Socio = cls.env["res.partner"]
        cls.emp = Socio.create({
            "name": "Empacadora trz", "is_company": True, "email": "emp.trz@prueba.test",
            "vat_or_id": "0944400009001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora trz S.A.", "emp_capacidad_lb_dia": 50000})
        cls.libra = cls.env.ref("shrimp_marketplace.uom_libra")
        cls.cert = cls.env["shrimp.certificate"].search([], limit=1)

        # 1) Semillero publica nauplio y el laboratorio compra 900
        cls.nauplio = producto(cls.env, cls.s["sem"], nombre="Nauplio trz",
                               etapa="shrimp_marketplace.shrimp_stage_nauplio", initial_qty=1000.0,
                               expected_delivery_date=date.today())
        if cls.cert:
            cls.env["shrimp.product.certificate.line"].with_context(
                shrimp_keep_cert_status=True).create({
                    "product_id": cls.nauplio.id, "certificate_id": cls.cert.id,
                    "number": "CERT-TRZ-1", "status": "approved",
                    "attachment_id": cls.env["ir.attachment"].create({
                        "name": "cert.pdf", "datas": base64.b64encode(PDF_MIN),
                        "mimetype": "application/pdf"}).id,
                    "issue_date": date.today() - timedelta(days=10),
                    "expiry_date": date.today() + timedelta(days=300)})
        cls.tx1 = cls.nauplio.execute_purchase_flow(cls.s["lab"], 900.0)["transaction"]
        cls.tx1.action_receive()
        cls.larva = cls.tx1.result_product_id
        cls.larva.with_context(shrimp_publish_checked=True).write({
            "state": "published", "expected_delivery_date": date.today()})

    # ------------------------------------------------------------------
    def _camaronera_con_larva(self, qty=300.0):
        tx = self.larva.execute_purchase_flow(self.s["cam"], qty)["transaction"]
        tx.action_receive()
        lote = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.s["cam"].id)])
        return tx, lote

    def _piscina(self, nombre="P-01"):
        return self.env["shrimp.partner.pond"].create({"partner_id": self.s["cam"].id, "name": nombre})

    def _cosecha(self, piscina, qty=5000.0, **extra):
        vals = dict(nombre="Camarón trz", etapa="shrimp_marketplace.shrimp_stage_engorde",
                    initial_qty=qty, uom_id=self.libra.id, presentation="entero",
                    size_grade_id=self.env["shrimp.size.grade"].search(
                        [("presentation", "=", "entero")], limit=1).id,
                    origin_pond_id=piscina.id, production_date=date.today(),
                    expected_delivery_date=date.today())
        vals.update(extra)
        return producto(self.env, self.s["cam"], **vals)

    def _venta_a_empacadora(self, cosecha, qty):
        tx = cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.emp, qty)["transaction"]
        tx.action_receive()
        return tx

    # ------------------------------------------------------------------
    # 6a. La siembra consume el lote y acumula las anteriores
    # ------------------------------------------------------------------
    def test_siembra_consume_y_acumula(self):
        _tx, lote = self._camaronera_con_larva(300.0)
        p1, p2 = self._piscina("P-01"), self._piscina("P-02")
        Alloc = self.env["shrimp.lot.allocation"]
        a1 = Alloc.create({"stock_lot_id": lote.id, "pond_id": p1.id, "allocated_qty": 200.0})
        self.assertAlmostEqual(lote.available_qty, 100.0)
        self.assertEqual(a1.sowing_move_id.move_type, "sowing")
        self.assertEqual(a1.sowing_move_id.lot_id, lote)
        # La segunda siembra ya no puede usar las 300 originales.
        with self.assertRaises(ValidationError):
            Alloc.create({"stock_lot_id": lote.id, "pond_id": p2.id, "allocated_qty": 150.0})
        a2 = Alloc.create({"stock_lot_id": lote.id, "pond_id": p2.id, "allocated_qty": 100.0})
        self.assertEqual(lote.state, "consumed")
        # Cancelar devuelve la cantidad con un ajuste documentado.
        a2.state = "cancelled"
        self.assertAlmostEqual(lote.available_qty, 100.0)
        self.assertFalse(a2.sowing_move_id)
        self.assertTrue(lote.move_ids.filtered(
            lambda m: m.move_type == "adjustment" and m.direction == "in"))

    def test_tanque_del_laboratorio_no_consume_su_stock_vendible(self):
        lote = self.larva.stock_lot_ids.filtered(lambda l: l.owner_id == self.s["lab"])
        tanque = self.env["shrimp.partner.pond"].create({"partner_id": self.s["lab"].id, "name": "T-1"})
        antes = lote.available_qty
        self.env["shrimp.lot.allocation"].create(
            {"stock_lot_id": lote.id, "pond_id": tanque.id, "allocated_qty": 100.0})
        self.assertAlmostEqual(lote.available_qty, antes)

    # ------------------------------------------------------------------
    # 1. La cadena atraviesa la siembra
    # ------------------------------------------------------------------
    def test_cadena_a_traves_de_la_siembra(self):
        tx_larva, lote = self._camaronera_con_larva(300.0)
        p1, p2 = self._piscina("P-01"), self._piscina("P-02")
        Alloc = self.env["shrimp.lot.allocation"]
        a1 = Alloc.create({"stock_lot_id": lote.id, "pond_id": p1.id, "allocated_qty": 100.0,
                           "allocation_date": date.today() - timedelta(days=100)})
        Alloc.create({"stock_lot_id": lote.id, "pond_id": p2.id, "allocated_qty": 200.0,
                      "allocation_date": date.today() - timedelta(days=100)})
        cosecha = self._cosecha(p1)
        # Se propone sola la siembra de ESA piscina, anterior a la producción.
        self.assertEqual(cosecha.origin_allocation_ids, a1)

        tx = self._venta_a_empacadora(cosecha, 2000.0)
        data = tx.get_full_traceability_data()
        moves = data["moves"]
        self.assertIn(self.tx1.stock_move_ids, moves, "Falta el semillero")
        self.assertIn(tx_larva.stock_move_ids, moves, "Falta el laboratorio")
        self.assertIn(a1.sowing_move_id, moves, "Falta la siembra")
        self.assertEqual(data["allocations"], a1, "Solo la siembra de origen, no la de otra piscina")
        # Orden: semillero primero, la venta a la empacadora después.
        idx = {m.id: i for i, m in enumerate(moves)}
        self.assertLess(idx[self.tx1.stock_move_ids[:1].id], idx[tx.stock_move_ids[:1].id])
        roles = [n["role_code"] for n in tx.traceability_chain()]
        self.assertEqual(roles[:4], ["semillero", "laboratorio", "camaronera", "empacadora"])
        # Evolución y certificados de la larva/nauplio también llegan.
        self.assertIn(self.nauplio, data["products"])
        if self.cert:
            certs = tx.shrimp_trace_certificates(data)
            self.assertEqual(len(certs.filtered(lambda c: c.number == "CERT-TRZ-1")), 1,
                             "El certificado copiado al producto del laboratorio no se repite")

    def test_lotes_sin_stock_del_vendedor(self):
        _t, lote = self._camaronera_con_larva(300.0)
        p1 = self._piscina()
        self.env["shrimp.lot.allocation"].create(
            {"stock_lot_id": lote.id, "pond_id": p1.id, "allocated_qty": 100.0,
             "allocation_date": date.today() - timedelta(days=10)})
        cosecha = self._cosecha(p1)
        tx = self._venta_a_empacadora(cosecha, 1000.0)
        data = tx.get_full_traceability_data()
        lote_inicial = cosecha.stock_lot_ids.filtered(lambda l: not l.origin_move_id)
        self.assertNotIn(lote_inicial, data["lots"], "El saldo del vendedor no es de la cadena")
        self.assertTrue(data["own_lots"])
        self.assertEqual(data["own_lots"].owner_id, self.emp)

    # ------------------------------------------------------------------
    # 5. Sin ruido en «Evolución»
    # ------------------------------------------------------------------
    def test_venta_no_crea_evolucion(self):
        antes = len(self.larva.evolution_ids)
        self.larva.execute_purchase_flow(self.s["cam"], 10.0)
        self.assertEqual(len(self.larva.evolution_ids), antes)
        self.larva.sudo().write({"survival_rate": 71.0})
        self.assertEqual(len(self.larva.evolution_ids), antes + 1)

    # ------------------------------------------------------------------
    # 6d. Producción real declarada por el laboratorio
    # ------------------------------------------------------------------
    def test_produccion_declarada(self):
        lote = self.larva.stock_lot_ids.filtered(lambda l: l.owner_id == self.s["lab"])
        self.assertAlmostEqual(lote.available_qty, 900.0)
        moves = self.larva.action_declare_production(648.0, reason="Supervivencia 72 % a PL12")
        self.assertEqual(moves.move_type, "production")
        self.assertEqual(moves.direction, "out")
        self.assertAlmostEqual(moves.qty, 252.0)
        self.assertAlmostEqual(moves.qty_before, 900.0)
        self.assertAlmostEqual(moves.qty_after, 648.0)
        self.assertAlmostEqual(self.larva.available_qty, 648.0)
        # Aparece en la trazabilidad de quien compra después esa larva.
        tx, _lote = self._camaronera_con_larva(100.0)
        self.assertIn(moves, tx.get_full_traceability_data()["moves"])
        with self.assertRaises(ValidationError):
            self.larva.action_declare_production(-1)

    # ------------------------------------------------------------------
    # 11. Salida / exportación: último eslabón
    # ------------------------------------------------------------------
    def test_salida_exportacion(self):
        p1 = self._piscina()
        cosecha = self._cosecha(p1)
        tx = self._venta_a_empacadora(cosecha, 2000.0)
        lote = tx.get_full_traceability_data()["own_lots"]
        spain = self.env.ref("base.es")
        exp = self.env["shrimp.export"].shrimp_register(self.emp, {
            "date": date.today(), "destination_buyer": "Importador Valencia",
            "destination_country_id": spain.id, "dae_number": "028-2026-40-00123456",
            "container": "TGHU5566778", "boxes": 99, "confidential": True,
        }, [(lote, 1980.0)])
        self.assertEqual(exp.state, "registered")
        self.assertAlmostEqual(lote.available_qty, 20.0)
        self.assertEqual(exp.move_ids.move_type, "export")
        data = tx.get_full_traceability_data()
        self.assertIn(exp, data["exports"])
        self.assertEqual(tx.traceability_chain()[-1]["role_code"], "export")
        self.assertEqual(exp.buyer_label(), "Confidencial")
        self.assertEqual(exp.buyer_label(self.emp), "Importador Valencia")
        self.assertIsNone(exp.public_dict()["buyer"])
        # No se exporta más de lo que tiene el lote, ni de un lote ajeno.
        with self.assertRaises(ValidationError):
            self.env["shrimp.export"].shrimp_register(self.emp, {}, [(lote, 50.0)])
        with self.assertRaises(Exception):
            self.env["shrimp.export"].shrimp_register(self.s["cam"], {}, [(lote, 1.0)])
        exp.action_cancel(reason="Error de captura")
        self.assertAlmostEqual(lote.available_qty, 2000.0)

    # ------------------------------------------------------------------
    # 7 / 10. Zona horaria y formato de números
    # ------------------------------------------------------------------
    def test_formato_zona_horaria_y_numeros(self):
        Tx = self.env["shrimp.transaction"]
        utc = datetime(2026, 7, 21, 3, 0)          # 22:00 del 20/07 en Guayaquil
        self.env.user.tz = "America/Guayaquil"
        self.assertEqual(Tx.shrimp_fmt_dt(utc), "20/07/2026 22:00")
        self.assertEqual(Tx.shrimp_iso_local(utc), "2026-07-20T22:00:00-05:00")
        self.assertEqual(Tx.shrimp_local_date(utc), date(2026, 7, 20))
        self.assertEqual(Tx.shrimp_fmt_dt(date(2026, 7, 21)), "21/07/2026")
        self.assertEqual(Tx.shrimp_fmt_num(126336, 2), "126.336,00")
        self.assertEqual(Tx.shrimp_fmt_qty(40000, "lb"), "40.000 lb")
        self.assertEqual(Tx.shrimp_fmt_qty(47912.5, "lb"), "47.912,50 lb")
        self.assertEqual(Tx.shrimp_fmt_iso("2026-07-20T22:00:00-05:00"), "20/07/2026 22:00")
        # Sin zona del usuario: la de Ecuador por defecto.
        self.env.user.tz = False
        self.assertEqual(Tx._shrimp_tz().zone, "America/Guayaquil")

    def test_marca_en_documentos(self):
        self.assertTrue(self.env["shrimp.transaction"].shrimp_brand_name())
        self.assertNotEqual(self.env["shrimp.transaction"].shrimp_brand_name(), "My Website")

    def test_fecha_real_de_recepcion(self):
        self.assertTrue(self.tx1.received_date)
