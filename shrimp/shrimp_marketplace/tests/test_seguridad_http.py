import base64

from odoo.tests import HttpCase, tagged

from .common import CLAVE, PDF_MIN, PNG_1PX, crear_socios, csrf_de, producto


@tagged("post_install", "-at_install")
class TestSeguridadHttp(HttpCase):
    """C1 (factura por GET), C3 (calendario), A1 (ids numéricos → 404),
    M2 (calificar), M3 (certificados públicos), B5 (mensajes de la URL)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "h")
        cls.larva = producto(cls.env, cls.s["sem"], nombre="Lote HTTP")
        foto = cls.env["ir.attachment"].create({
            "name": "f.png", "datas": base64.b64encode(PNG_1PX), "mimetype": "image/png",
            "res_model": "shrimp.product", "res_id": cls.larva.id})
        foto.generate_access_token()
        cls.foto = foto
        cls.larva.photo_attachment_ids = [(6, 0, foto.ids)]
        cls.tx = cls.larva.execute_purchase_flow(cls.s["lab"], 5.0)["transaction"]
        cls.fac = cls.env["shrimp.partner.facility"].create({
            "partner_id": cls.s["sem"].id, "name": "Granja HTTP"})
        cls.cr_req = cls.env["shrimp.check.request"].create({
            "product_id": cls.larva.id, "seller_partner_id": cls.s["sem"].id,
            "buyer_partner_id": cls.s["lab"].id, "qty": 1.0})

    def _login(self, clave):
        self.authenticate(self.s["u_" + clave].login, CLAVE)

    # ---------------- C1: /factura/pdf sin efectos ----------------
    def test_factura_get_no_crea_ni_contabiliza(self):
        movimientos = self.env["account.move"].search_count([])
        pedidos = self.env["sale.order"].search_count([])
        self._login("lab")
        r = self.url_open("/marketplace/purchases/%s/invoice/pdf" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn("SIN VALOR TRIBUTARIO", r.text)
        self.tx.invalidate_recordset()
        self.assertFalse(self.tx.invoice_id)
        self.assertEqual(self.env["account.move"].search_count([]), movimientos)
        self.assertEqual(self.env["sale.order"].search_count([]), pedidos)
        # Un tercero: 404. Compra no cerrada: 404.
        self._login("lab2")
        r = self.url_open("/marketplace/purchases/%s/invoice/pdf" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 404)
        self.tx.sudo().write({"state": "cancel"})
        self._login("lab")
        r = self.url_open("/marketplace/purchases/%s/invoice/pdf" % self.tx.uuid_ref)
        self.assertEqual(r.status_code, 404)

    # ---------------- C3: calendario ----------------
    def test_calendario_no_usa_innerhtml_con_datos(self):
        producto(self.env, self.s["sem"], nombre='<img src=x onerror="alert(1)">')
        r = self.url_open("/marketplace/calendar")
        self.assertEqual(r.status_code, 200)
        self.assertIn("function safeUrl", r.text)
        self.assertIn("textContent", r.text)
        self.assertNotIn("(ev.meta?.name || ev.title || 'Producto') +", r.text)
        self.assertNotIn("sec.innerHTML = html", r.text)

    # ---------------- A1: ids numéricos ----------------
    def test_foto_por_token_y_no_por_id(self):
        base = "/marketplace/product/%s/photo/" % self.larva.uuid_ref
        self.assertEqual(self.url_open(base + str(self.foto.id)).status_code, 404)
        r = self.url_open(base + self.foto.access_token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        # El producto por id numérico tampoco.
        self.assertEqual(self.url_open("/marketplace/product/%d" % self.larva.id).status_code, 404)

    def test_rutas_del_portal_rechazan_ids(self):
        self._login("sem")
        r = self.url_open("/marketplace/check-requests/%d/detail" % self.cr_req.id)
        self.assertEqual(r.status_code, 404)
        r = self.url_open("/marketplace/check-requests/%s/detail" % self.cr_req.uuid_ref)
        self.assertEqual(r.status_code, 200)
        pagina = self.url_open("/marketplace/my-facilities").text
        token = csrf_de(pagina)
        self.assertTrue(token)
        r = self.url_open("/marketplace/my-facilities/facility/%d/update" % self.fac.id,
                          data={"csrf_token": token, "name": "Hackeada"})
        self.assertEqual(r.status_code, 404)
        self.fac.invalidate_recordset()
        self.assertEqual(self.fac.name, "Granja HTTP")
        r = self.url_open("/marketplace/my-facilities/facility/%s/update" % self.fac.uuid_ref,
                          data={"csrf_token": token, "name": "Granja renombrada"})
        self.assertIn(r.status_code, (200, 303))
        self.fac.invalidate_recordset()
        self.assertEqual(self.fac.name, "Granja renombrada")
        # El HTML de la página no expone ids de instalaciones.
        pagina = self.url_open("/marketplace/my-facilities").text
        self.assertNotIn("facility/%d/" % self.fac.id, pagina)
        self.assertIn(self.fac.uuid_ref, pagina)

    def test_certificado_de_usuario_por_codigo(self):
        att = self.env["ir.attachment"].create({
            "name": "c.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        linea = self.env["shrimp.user.certificate.line"].create({
            "partner_id": self.s["sem"].id,
            "certificate_id": self.env.ref("shrimp_user_registry.shrimp_certificate_asc").id,
            "file_attachment_id": att.id, "certificate_number": "U-1",
            "issue_date": "2026-01-01", "expiry_date": "2030-01-01"})
        self._login("sem")
        self.assertEqual(self.url_open(
            "/marketplace/my-account/certificates/%d/file" % linea.id).status_code, 404)
        self.assertEqual(self.url_open(
            "/marketplace/my-certificate/%d/file" % linea.id).status_code, 404)
        r = self.url_open("/marketplace/my-account/certificates/%s/file" % linea.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")

    # ---------------- M3: certificado de producto pendiente ----------------
    def test_certificado_pendiente_no_es_publico(self):
        att = self.env["ir.attachment"].create({
            "name": "pc.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        linea = self.env["shrimp.product.certificate.line"].create({
            "product_id": self.larva.id,
            "certificate_id": self.env.ref("shrimp_user_registry.shrimp_certificate_asc").id,
            "number": "P-1", "attachment_id": att.id, "status": "pending",
            "issue_date": "2026-01-01", "expiry_date": "2030-01-01"})
        token = att.generate_access_token()[0]
        url = "/marketplace/product/%s/certificate/%s" % (self.larva.uuid_ref, token)
        self.assertEqual(self.url_open(url).status_code, 404)
        self.assertEqual(self.url_open(
            "/marketplace/product/%s/certificate/%d" % (self.larva.uuid_ref, att.id)).status_code, 404)
        linea.action_approve()
        self.assertEqual(self.url_open(url).status_code, 200)

    # ---------------- M2: calificar ----------------
    def test_no_se_califica_una_compra_cancelada(self):
        self.tx.sudo().write({"state": "cancel"})
        self._login("lab")
        token = csrf_de(self.url_open("/marketplace/purchases").text)
        r = self.url_open("/marketplace/purchases/%s/rate" % self.tx.uuid_ref,
                          data={"csrf_token": token, "rating": "5"}, allow_redirects=False)
        self.assertIn("rating_state", r.headers.get("Location", ""))
        self.assertFalse(self.env["shrimp.review"].search([("transaction_id", "=", self.tx.id)]))

    # ---------------- B5: mensajes de la URL ----------------
    def test_mensajes_de_la_url_no_se_reflejan(self):
        self._login("sem")
        r = self.url_open("/marketplace/my-account?error=Tu+cuenta+fue+suspendida+llama+al+0999")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Tu cuenta fue suspendida", r.text)
        r = self.url_open("/marketplace/my-account?error=validation&message=Texto+inyectado+aqui")
        self.assertNotIn("Texto inyectado aqui", r.text)


@tagged("post_install", "-at_install")
class TestFlujosPortal(HttpCase):
    """Con el portal en SOLO LECTURA, los flujos legítimos siguen funcionando
    porque los controladores escriben en sudo tras validar quién es quién."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "f")
        cls.larva = producto(cls.env, cls.s["sem"], nombre="Lote flujo")

    def test_compra_y_resena_desde_el_portal(self):
        self.authenticate(self.s["u_lab"].login, CLAVE)
        token = csrf_de(self.url_open("/marketplace/buy/%s" % self.larva.uuid_ref).text)
        r = self.url_open("/marketplace/buy/%s/confirm" % self.larva.uuid_ref,
                          data={"csrf_token": token, "qty": "3"})
        self.assertEqual(r.status_code, 200)
        tx = self.env["shrimp.transaction"].search([
            ("product_id", "=", self.larva.id), ("buyer_partner_id", "=", self.s["lab"].id)])
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(tx.price_unit, 10.0)
        r = self.url_open("/marketplace/purchases/%s/rate" % tx.uuid_ref,
                          data={"csrf_token": token, "rating": "4", "comment": "bien"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.env["shrimp.review"].search([("transaction_id", "=", tx.id)]))

    def test_alta_de_producto_con_foto_validada(self):
        self.authenticate(self.s["u_sem"].login, CLAVE)
        token = csrf_de(self.url_open("/marketplace/products/new").text)
        datos = {"csrf_token": token, "name": "Nuevo lote portal", "initial_qty": "50",
                 "price": "12", "stage_id": str(self.env.ref("shrimp_marketplace.shrimp_stage_pl12").id)}
        self.url_open("/marketplace/products/create", data=datos,
                      files={"main_photo_file": ("a.png", PNG_1PX, "image/png")})
        nuevo = self.env["shrimp.product"].search([("name", "=", "Nuevo lote portal")])
        self.assertEqual(len(nuevo), 1)
        self.assertEqual(nuevo.seller_partner_id, self.s["sem"])
        self.assertEqual(nuevo.photo_attachment_ids.mimetype, "image/png")
        # Un HTML disfrazado de foto no se guarda.
        datos["name"] = "Lote con foto falsa"
        self.url_open("/marketplace/products/create", data=datos,
                      files={"main_photo_file": ("a.png", b"<html><script>x</script>", "image/png")})
        self.assertFalse(self.env["shrimp.product"].search([("name", "=", "Lote con foto falsa")]))
        # Publicar pasa por action_publish.
        self.url_open("/marketplace/products/%s/publish" % nuevo.uuid_ref, data={"csrf_token": token})
        nuevo.invalidate_recordset()
        self.assertEqual(nuevo.state, "published")
