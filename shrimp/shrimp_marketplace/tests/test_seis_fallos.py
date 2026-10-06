"""Seis fallos del portal y parámetros de Ajustes (marketplace).

1. Siembra desde el portal (formulario, botones, editar / cancelar).
3. La vitrina del vendedor solo muestra reseñas recibidas como vendedor.
4. «Registrar salida»: misma regla en botón, menú, ruta y modelo.
5. «Comisiones pagadas» suma solo comisiones; los demás cobros, aparte.
6. La fecha de la operación del certificado es la de la confirmación.
+ Ajustes › CamaronMarket: parámetros del marketplace.
"""
import base64
import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, PDF_MIN, crear_socios, csrf_de, producto


@tagged("post_install", "-at_install")
class TestSeisFallos(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "6f")
        Socio = env["res.partner"]
        portal = env.ref("base.group_portal")
        # Otra camaronera (para comprobar la propiedad de las siembras).
        cls.cam2 = Socio.create({
            "name": "Camaronera ajena 6f", "is_company": True,
            "email": "cam2.6f@prueba.test", "vat_or_id": "0911100009006",
            "shrimp_user_type": "camaronera",
            "farm_razon_social": "Cam2 S.A.", "farm_representante": "Rep",
            "farm_telefono": "04-1234567", "farm_ubicacion": "Guayas"})
        env["res.users"].create({
            "name": cls.cam2.name, "login": "cam2.6f", "partner_id": cls.cam2.id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
        cls.libra = env.ref("shrimp_marketplace.uom_libra")

        # Semillero -> laboratorio -> camaronera (larva en inventario).
        cls.nauplio = producto(env, cls.s["sem"], nombre="Nauplio 6f",
                               etapa="shrimp_marketplace.shrimp_stage_nauplio",
                               initial_qty=1000.0, expected_delivery_date=date.today())
        cls.tx1 = cls.nauplio.execute_purchase_flow(cls.s["lab"], 900.0)["transaction"]
        cls.tx1.action_receive()
        cls.larva = cls.tx1.result_product_id
        cls.larva.with_context(shrimp_publish_checked=True).write({
            "state": "published", "expected_delivery_date": date.today(),
            "stage_id": env.ref("shrimp_marketplace.shrimp_stage_pl12").id})
        cls.tx_larva = cls.larva.execute_purchase_flow(cls.s["cam"], 300.0)["transaction"]
        cls.tx_larva.action_receive()
        cls.lote = env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", cls.tx_larva.stock_move_ids.ids),
            ("owner_id", "=", cls.s["cam"].id)])
        cls.fac = env["shrimp.partner.facility"].create(
            {"partner_id": cls.s["cam"].id, "name": "Granja 6f"})
        cls.p1 = env["shrimp.partner.pond"].create(
            {"partner_id": cls.s["cam"].id, "name": "P-6F-1", "facility_id": cls.fac.id})
        cls.p2 = env["shrimp.partner.pond"].create(
            {"partner_id": cls.s["cam"].id, "name": "P-6F-2", "facility_id": cls.fac.id})

    def _login(self, login):
        self.authenticate(login, CLAVE)

    def _csrf(self, url):
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200, url)
        return csrf_de(r.text), r

    def _cosecha(self, piscina=None, qty=5000.0):
        return producto(
            self.env, self.s["cam"], nombre="Camarón 6f", etapa="shrimp_marketplace.shrimp_stage_engorde",
            initial_qty=qty, uom_id=self.libra.id, presentation="entero",
            size_grade_id=self.env["shrimp.size.grade"].search(
                [("presentation", "=", "entero")], limit=1).id,
            origin_pond_id=piscina.id if piscina else False,
            production_date=date.today(), expected_delivery_date=date.today())

    # ------------------------------------------------------------------
    # 1. Siembra desde el portal
    # ------------------------------------------------------------------
    def test_1_siembra_desde_el_portal(self):
        Alloc = self.env["shrimp.lot.allocation"]
        self._login("cam.sec6f")
        # Botones: «Sembrar» en Mi inventario y «Registrar siembra» por piscina.
        r = self.url_open("/marketplace/my-lots")
        self.assertIn("/marketplace/sowings/new?lote=%s" % self.lote.uuid_ref, r.text)
        r = self.url_open("/marketplace/my-facilities?facility=%s" % self.fac.uuid_ref)
        self.assertIn("/marketplace/sowings/new?piscina=%s" % self.p1.uuid_ref, r.text)
        self.assertIn("Registrar siembra", r.text)
        # Formulario, con el lote preseleccionado y el máximo disponible.
        token, r = self._csrf("/marketplace/sowings/new?lote=%s&piscina=%s"
                              % (self.lote.uuid_ref, self.p1.uuid_ref))
        self.assertIn('name="stock_lot_ref"', r.text)
        self.assertIn('name="allocation_date"', r.text)
        self.assertNotIn('value="%s"' % self.lote.id, r.text, "Solo códigos, nunca ids")

        def sembrar(qty, fecha=None, piscina=None):
            return self.url_open("/marketplace/my-facilities/allocate", data={
                "csrf_token": token, "stock_lot_ref": self.lote.uuid_ref,
                "pond_ref": (piscina or self.p1).uuid_ref, "allocated_qty": str(qty),
                "allocation_date": (fecha or date.today()).isoformat(),
                "notes": "Densidad 12 larvas/m²"})

        # Más de lo disponible: no se crea nada (antes quedaba una siembra
        # fantasma sin descontar el lote).
        r = sembrar(400)
        self.assertIn("error=validation", r.url)
        self.assertFalse(Alloc.search([("stock_lot_id", "=", self.lote.id)]))
        # Fecha futura: rechazada.
        r = sembrar(10, fecha=date.today() + timedelta(days=3))
        self.assertIn("error=validation", r.url)
        self.assertFalse(Alloc.search([("stock_lot_id", "=", self.lote.id)]))
        # Siembra válida: consume el lote con un movimiento «sowing».
        r = sembrar(200, fecha=date.today() - timedelta(days=2))
        self.assertEqual(r.status_code, 200)
        alloc = Alloc.search([("stock_lot_id", "=", self.lote.id)])
        self.assertEqual(len(alloc), 1)
        self.assertEqual(alloc.pond_id, self.p1)
        self.assertEqual(alloc.allocation_date, date.today() - timedelta(days=2))
        self.assertEqual(alloc.sowing_move_id.move_type, "sowing")
        self.lote.invalidate_recordset()
        self.assertAlmostEqual(self.lote.available_qty, 100.0)
        # La lista de siembras de la piscina la muestra con editar / cancelar.
        r = self.url_open("/marketplace/my-facilities?facility=%s" % self.fac.uuid_ref)
        self.assertIn("/marketplace/sowings/%s/edit" % alloc.uuid_ref, r.text)
        self.assertIn("/marketplace/sowings/%s/cancel" % alloc.uuid_ref, r.text)
        # Editar: cantidad, piscina y fecha (el lote se recalcula solo).
        token, r = self._csrf("/marketplace/sowings/%s/edit" % alloc.uuid_ref)
        r = self.url_open("/marketplace/sowings/%s/update" % alloc.uuid_ref, data={
            "csrf_token": token, "pond_ref": self.p2.uuid_ref, "allocated_qty": "250",
            "allocation_date": date.today().isoformat(), "notes": "corregida"})
        self.assertNotIn("error=", r.url)
        alloc.invalidate_recordset()
        self.lote.invalidate_recordset()
        self.assertEqual(alloc.pond_id, self.p2)
        self.assertAlmostEqual(alloc.allocated_qty, 250.0)
        self.assertAlmostEqual(self.lote.available_qty, 50.0)
        # Editar a más de lo que hay: rechazado y sin cambios.
        r = self.url_open("/marketplace/sowings/%s/update" % alloc.uuid_ref, data={
            "csrf_token": token, "allocated_qty": "301",
            "allocation_date": date.today().isoformat()})
        self.assertIn("error=validation", r.url)
        alloc.invalidate_recordset()
        self.assertAlmostEqual(alloc.allocated_qty, 250.0)
        # Otra camaronera no ve ni toca la siembra.
        self._login("cam2.6f")
        r = self.url_open("/marketplace/sowings/%s/edit" % alloc.uuid_ref)
        self.assertEqual(r.status_code, 404)
        token2, _r = self._csrf("/marketplace/my-lots")
        r = self.url_open("/marketplace/sowings/%s/cancel" % alloc.uuid_ref,
                          data={"csrf_token": token2})
        self.assertEqual(r.status_code, 404)
        # Un laboratorio no siembra en piscinas de engorde.
        self._login("lab.sec6f")
        self.assertEqual(self.url_open("/marketplace/sowings/new").status_code, 404)
        # Cancelar devuelve la cantidad con un ajuste.
        self._login("cam.sec6f")
        token, _r = self._csrf("/marketplace/my-lots")
        r = self.url_open("/marketplace/sowings/%s/cancel" % alloc.uuid_ref,
                          data={"csrf_token": token})
        alloc.invalidate_recordset()
        self.lote.invalidate_recordset()
        self.assertEqual(alloc.state, "cancelled")
        self.assertAlmostEqual(self.lote.available_qty, 300.0)
        self.assertTrue(self.lote.move_ids.filtered(
            lambda m: m.move_type == "adjustment" and m.direction == "in"))

    def test_1b_siembra_origen_de_cosecha_no_se_toca(self):
        alloc = self.env["shrimp.lot.allocation"].create({
            "stock_lot_id": self.lote.id, "pond_id": self.p1.id, "allocated_qty": 100.0,
            "allocation_date": date.today() - timedelta(days=90)})
        cosecha = self._cosecha(self.p1)
        self.assertEqual(cosecha.origin_allocation_ids, alloc)
        self.assertTrue(alloc._shrimp_portal_edit_block())
        self._login("cam.sec6f")
        token, r = self._csrf("/marketplace/sowings/%s/edit" % alloc.uuid_ref)
        self.assertIn("origen de una cosecha", r.text)
        self.url_open("/marketplace/sowings/%s/cancel" % alloc.uuid_ref, data={"csrf_token": token})
        alloc.invalidate_recordset()
        self.assertEqual(alloc.state, "allocated")
        # El lote adulto no se ofrece para sembrar; la larva sí.
        sembrables = self.env["shrimp.lot.allocation"]._shrimp_sowable_lots(self.s["cam"])
        self.assertIn(self.lote, sembrables)
        self.assertFalse(sembrables & cosecha.stock_lot_ids)

    # ------------------------------------------------------------------
    # 3. Reseñas de la vitrina
    # ------------------------------------------------------------------
    def test_3_vitrina_solo_resenas_como_vendedor(self):
        Review = self.env["shrimp.review"]
        Review.create({"direction": "to_seller", "seller_partner_id": self.s["cam"].id,
                       "reviewer_partner_id": self.cam2.id, "rating": 4,
                       "comment": "RESENA-COMO-VENDEDOR"})
        Review.create({"direction": "to_buyer", "seller_partner_id": self.s["cam"].id,
                       "reviewer_partner_id": self.s["lab"].id, "rating": 1,
                       "comment": "RESENA-COMO-COMPRADOR"})
        cam = self.s["cam"]
        cam.invalidate_recordset()
        self.assertEqual(cam.shrimp_rating_count, 1)
        self.assertAlmostEqual(cam.shrimp_rating_avg, 4.0)
        self.assertEqual(cam._shrimp_seller_reviews().mapped("comment"), ["RESENA-COMO-VENDEDOR"])
        r = self.url_open("/marketplace/sellers/%s" % cam.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn("RESENA-COMO-VENDEDOR", r.text)
        self.assertNotIn("RESENA-COMO-COMPRADOR", r.text)
        self.assertIn("4.0", r.text)

    # ------------------------------------------------------------------
    # 4. Salidas: una sola regla
    # ------------------------------------------------------------------
    def test_4_salidas_misma_regla(self):
        Export = self.env["shrimp.export"]
        W = self.env["website"]
        # El laboratorio no registra salidas: ni menú, ni botón, ni ruta, ni modelo.
        lote_lab = self.env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", self.tx1.stock_move_ids.ids),
            ("owner_id", "=", self.s["lab"].id)])
        self.assertFalse(W._shrimp_nav_can(self.s["lab"], "register_exports"))
        self.assertTrue(lote_lab._shrimp_export_block_reason())
        with self.assertRaises(ValidationError):
            Export.shrimp_register(self.s["lab"], {}, [(lote_lab, 1.0)])
        self._login("lab.sec6f")
        r = self.url_open("/marketplace/my-lots")
        self.assertNotIn("/marketplace/exports/new", r.text)
        self.assertNotIn('href="/marketplace/exports"', r.text)
        self.assertEqual(self.url_open("/marketplace/exports").status_code, 404)
        self.assertEqual(self.url_open("/marketplace/exports/new").status_code, 404)

        # La camaronera sí, pero solo de camarón adulto: la larva se siembra.
        self.assertTrue(W._shrimp_nav_can(self.s["cam"], "register_exports"))
        cosecha = self._cosecha(qty=1000.0)
        lote_adulto = cosecha.stock_lot_ids.filtered(lambda l: l.owner_id == self.s["cam"])[:1]
        self.assertTrue(lote_adulto.shrimp_can_register_export())
        self.assertFalse(self.lote.shrimp_can_register_export())
        with self.assertRaises(ValidationError):
            Export.shrimp_register(self.s["cam"], {}, [(self.lote, 1.0)])
        self._login("cam.sec6f")
        r = self.url_open("/marketplace/my-lots")
        self.assertIn("/marketplace/exports/new?lot=%s" % lote_adulto.uuid_ref, r.text)
        self.assertNotIn("/marketplace/exports/new?lot=%s" % self.lote.uuid_ref, r.text)
        token, r = self._csrf("/marketplace/exports/new")
        self.assertIn(lote_adulto.uuid_ref, r.text)
        self.assertNotIn(self.lote.uuid_ref, r.text)
        # Forzar la larva por POST: rechazado por el modelo.
        r = self.url_open("/marketplace/exports/register", data={
            "csrf_token": token, "lot_ref": self.lote.uuid_ref, "qty": "5",
            "date": date.today().isoformat()})
        self.assertIn("error=validation", r.url)
        self.assertFalse(Export.search([("partner_id", "=", self.s["cam"].id)]))
        r = self.url_open("/marketplace/exports/register", data={
            "csrf_token": token, "lot_ref": lote_adulto.uuid_ref, "qty": "400",
            "date": date.today().isoformat(), "country_code": "US"})
        exp = Export.search([("partner_id", "=", self.s["cam"].id)])
        self.assertEqual(exp.state, "registered")
        self.assertAlmostEqual(lote_adulto.available_qty, 600.0)

    # ------------------------------------------------------------------
    # 5. Reportes: comisiones separadas de los otros cobros
    # ------------------------------------------------------------------
    def test_5_reportes_comisiones(self):
        Charge = self.env["shrimp.charge"]
        cam = self.s["cam"]
        for tipo, importe in (("commission", 11.0), ("verification_fee", 222.0),
                              ("copack_platform", 33.0)):
            Charge.create({"charge_type": tipo, "payer_partner_id": cam.id,
                           "amount": importe, "state": "invoiced"})
        self._login("cam.sec6f")
        r = self.url_open("/marketplace/reports")
        self.assertEqual(r.status_code, 200)
        texto = re.sub(r"\s+", " ", r.text)
        bloque = texto[texto.index("Comisiones pagadas"):texto.index("Ingreso neto")]
        self.assertIn("11.00", bloque)
        self.assertNotIn("266.00", texto, "Las comisiones ya no suman todos los cobros")
        self.assertIn("Honorarios de verificación", bloque)
        self.assertIn("222.00", bloque)
        self.assertIn("Cuotas de empaque", bloque)
        self.assertIn("33.00", bloque)

    # ------------------------------------------------------------------
    # 6. Fecha de la operación del certificado
    # ------------------------------------------------------------------
    def test_6_fecha_de_la_operacion(self):
        tx = self.tx1
        confirmada = datetime(2026, 3, 10, 17, 0, 0)
        tx.stock_move_ids.filtered(lambda m: m.move_type == "transfer").write({"date": confirmada})
        tx.write({"sold_date": date(2026, 1, 5)})
        self.env.cr.execute("UPDATE shrimp_transaction SET create_date = %s WHERE id = %s",
                            (datetime(2025, 12, 1, 15, 0, 0), tx.id))
        tx.invalidate_recordset()
        self.assertEqual(tx.shrimp_operation_datetime(), confirmada)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "shrimp_marketplace.action_report_shrimp_full_traceability", tx.ids)[0].decode()
        fila = re.sub(r"\s+", " ", html)
        fila = fila[fila.index("Fecha de la operación"):][:300]
        self.assertIn("10/03/2026", fila)
        self.assertNotIn("05/01/2026", fila)
        self.assertNotIn("01/12/2025", fila)

    # ------------------------------------------------------------------
    # Ajustes › CamaronMarket (marketplace)
    # ------------------------------------------------------------------
    def _ajustes(self, **vals):
        conf = self.env["res.config.settings"].create(vals)
        conf.execute()
        return conf

    def test_ajustes_marketplace(self):
        ICP = self.env["ir.config_parameter"].sudo()
        conf = self.env["res.config.settings"].create({})
        # Valores de siempre como defecto.
        self.assertEqual(conf.shrimp_featured_min_reviews, int(
            ICP.get_param("shrimp_marketplace.featured_min_reviews") or 10))
        self.assertEqual(conf.shrimp_charge_max_attempts, 5)
        self.assertEqual(conf.shrimp_cert_alert_days, 30)
        Charge = self.env["shrimp.charge"]
        self.assertEqual(Charge._shrimp_max_auto_attempts(), 5)
        # Cambiarlos cambia el comportamiento (y el 0 se guarda como 0).
        self._ajustes(shrimp_featured_min_reviews=0, shrimp_charge_max_attempts=2,
                      shrimp_cert_alert_days=90)
        self.assertEqual(ICP.get_param("shrimp_marketplace.featured_min_reviews"), "0")
        self.assertEqual(self.env["shrimp.product"]._shrimp_featured_min_reviews(), 0)
        self.assertEqual(Charge._shrimp_max_auto_attempts(), 2)
        self.assertEqual(self.env["shrimp.dashboard"]._cert_alert_days(), 90)
        conf = self.env["res.config.settings"].create({})
        self.assertEqual(conf.shrimp_charge_max_attempts, 2)
        # Un cobro con 2 intentos ya no lo reintenta el cron automático.
        cobro = Charge.create({"charge_type": "commission", "payer_partner_id": self.s["cam"].id,
                               "amount": 1.0, "state": "error", "invoice_attempts": 2})
        intentados = []

        def espia(recs):
            intentados.extend(recs.ids)
            return True
        with patch.object(type(Charge), "_try_invoice", espia):
            Charge._cron_retry_invoices()
        self.assertNotIn(cobro.id, intentados)
        cobro.invoice_attempts = 1
        with patch.object(type(Charge), "_try_invoice", espia):
            Charge._cron_retry_invoices()
        self.assertIn(cobro.id, intentados)
        # Rangos validados.
        with self.assertRaises(ValidationError):
            self._ajustes(shrimp_charge_max_attempts=0)
        # Aviso de certificados: el plazo configurado manda.
        cert = self.env["shrimp.certificate"].search([], limit=1)
        if cert:
            att = self.env["ir.attachment"].create({
                "name": "c.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
            self.env["shrimp.user.certificate.line"].create({
                "partner_id": self.s["cam"].id, "certificate_id": cert.id, "status": "approved",
                "file_attachment_id": att.id,
                "issue_date": date.today() - timedelta(days=300),
                "expiry_date": date.today() + timedelta(days=60)})
            data = {"alerts": []}
            self.env["shrimp.dashboard"]._cert_alerts(self.s["cam"], data)
            self.assertTrue(data["alerts"], "60 días entra en un aviso de 90")
            self._ajustes(shrimp_cert_alert_days=30, shrimp_charge_max_attempts=2)
            data = {"alerts": []}
            self.env["shrimp.dashboard"]._cert_alerts(self.s["cam"], data)
            self.assertFalse(data["alerts"], "60 días no entra en un aviso de 30")
