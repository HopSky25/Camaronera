"""El lote que recibe la empacadora refleja el peso verificado en planta
(hallazgo 6c) y el despacho sale en la trazabilidad (hallazgo 4)."""
from datetime import date, datetime, timedelta

from odoo.tests import tagged

from .common_seguridad import montar_verificacion
from odoo.tests import TransactionCase


@tagged("post_install", "-at_install")
class TestTrazabilidadPlanta(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, _v = montar_verificacion(cls.env, "pla")
        cls.emp = cls.env["res.partner"].create({
            "name": "Empacadora pla", "is_company": True, "email": "emp.pla@prueba.test",
            "vat_or_id": "0966600009001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Empacadora pla S.A.", "emp_capacidad_lb_dia": 90000})
        cls.cosecha = cls.env["shrimp.product"].create({
            "name": "Cosecha pla", "seller_partner_id": cls.s["cam"].id, "seller_role": "camaronera",
            "stage_id": cls.env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "uom_id": cls.env.ref("shrimp_marketplace.uom_libra").id, "presentation": "entero",
            "size_grade_id": cls.env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1).id,
            "initial_qty": 40000.0, "price": 2.3, "state": "published",
            "expected_delivery_date": date.today()})

    def _compra_verificada(self, enviado=40000.0, planta=39640.0, basura=120.0):
        res = self.cosecha.start_verified_purchase(self.emp, enviado, self.s["verif"], fee=50.0)
        tx, ver = res["transaction"], res["verification"]
        ver.write({"scope": "adult", "weight_sent_lb": enviado, "weight_plant_lb": planta,
                   "trash_lb": basura, "state": "approved", "acceptance_state": "closed"})
        tx.action_complete_after_verification()
        tx.write({"desired_date": date.today()})
        tx.action_receive()
        lote = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.emp.id)])
        return tx, ver, lote

    def test_peso_en_planta_ajusta_el_lote(self):
        tx, ver, lote = self._compra_verificada()
        self.assertAlmostEqual(lote.available_qty, 39640.0 - 120.0)
        ajuste = lote.move_ids.filtered(lambda m: m.move_type == "adjustment")
        basura = lote.move_ids.filtered(lambda m: m.move_type == "consumption")
        self.assertAlmostEqual(ajuste.qty, 360.0)
        self.assertEqual(ajuste.direction, "out")
        self.assertIn(ver.name, ajuste.reason)
        self.assertAlmostEqual(basura.qty, 120.0)
        # Ambos constan en la trazabilidad de la compra.
        moves = tx.get_full_traceability_data()["moves"]
        self.assertIn(ajuste, moves)
        self.assertIn(basura, moves)
        # Idempotente.
        tx._shrimp_apply_plant_weight()
        self.assertEqual(len(lote.move_ids), 2)

    def test_despacho_en_pdf_y_api_publica_sin_datos_personales(self):
        tx, _ver, _lote = self._compra_verificada(planta=40000.0, basura=0.0)
        disp = tx._ensure_dispatch()
        disp.sudo().write({
            "harvest_date": date.today() - timedelta(days=1),
            "farm_departure": datetime.now() - timedelta(hours=8),
            "eta": datetime.now() - timedelta(hours=2),
            "carrier_name": "Transportes Secretos", "vehicle_plate": "XYZ-9999",
            "carrier_phone": "0999999999"})
        disp.sudo().write({"actual_arrival": datetime.now() - timedelta(hours=1, minutes=50)})
        html = self.env["ir.actions.report"]._render_qweb_html(
            "shrimp_marketplace.report_shrimp_full_traceability", tx.ids)[0].decode()
        self.assertIn("Despacho a planta", html)
        self.assertIn("XYZ-9999", html, "El certificado privado sí lleva la placa")
        if "_public_traceability_data" in type(tx).__dict__ or hasattr(tx, "_public_traceability_data"):
            data = tx._public_traceability_data()
            texto = str(data)
            self.assertTrue(data["dispatch"]["actual_arrival"].endswith("-05:00"))
            self.assertNotIn("XYZ-9999", texto)
            self.assertNotIn("0999999999", texto)
            self.assertNotIn("Transportes Secretos", texto)

    def test_certificado_pdf_cadena_en_tabla_indicadores_sin_aceptacion(self):
        """Certificado: la cadena de custodia es una tabla (rol, empresa, qué
        hizo, referencia), los indicadores salen en el servidor sin SVG y la
        ronda de aceptación de las partes ya no se imprime."""
        tx, ver, _lote = self._compra_verificada()
        report = self.env["ir.actions.report"]

        def render():
            return report._render_qweb_html(
                "shrimp_marketplace.report_shrimp_full_traceability", tx.ids)[0].decode()

        # Sin mediciones ni rendimiento verificado: la sección no sale (ni el título).
        self.assertFalse(tx.traceability_indicators())
        html = render()
        self.assertNotIn("Indicadores de cultivo", html)
        self.assertIn("Qué hizo", html)
        self.assertRegex(html, r"Compró 40\.000 (lb|libras) \(verificada\)")
        self.assertIn(tx.name, html)
        self.assertIn("Verificación en campo", html)
        self.assertNotIn("Aceptación de las partes", html)
        self.assertNotIn("<svg", html)
        # Con mediciones del producto: tabla con barras (supervivencia y peso).
        self.env["shrimp.product.evolution"].create([
            {"product_id": self.cosecha.id, "date": datetime(2026, 5, 1, 12, 0),
             "avg_size_mg": 9000.0, "survival_rate": 70.0},
            {"product_id": self.cosecha.id, "date": datetime(2026, 6, 1, 12, 0),
             "avg_size_mg": 18000.0, "survival_rate": 60.0},
        ])
        filas = tx.traceability_indicators()
        etiquetas = [f["label"] for f in filas]
        self.assertTrue(any(e.startswith("Supervivencia") for e in etiquetas), etiquetas)
        self.assertIn("Peso promedio", etiquetas)
        html = render()
        self.assertIn("Indicadores de cultivo", html)
        self.assertIn("18,0 g", html)
        self.assertIn("st-bar", html)
