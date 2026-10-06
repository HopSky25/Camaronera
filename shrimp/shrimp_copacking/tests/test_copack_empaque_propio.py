"""Empaque propio (self-packing) y llegada tras el login.

Una cuenta con el perfil Maquilador APROBADO empaca su propio camarón (como
camaronera o como empacadora) sin solicitud, ofertas, tarifa, comisión ni
acta de dos partes, y el informe de trazabilidad dice quién empacó y en qué
planta. El mercado de terceros no cambia: nadie se adjudica su propia
solicitud.
"""
import re
from datetime import date, timedelta

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from .common import CopackCommon

CLAVE = "Clave-Selfpack-2026"
MSG_PERFIL = "Para empacar tu propio producto necesitas el perfil Maquilador"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto) or \
        re.search(r'value="([^"]+)"\s+name="csrf_token"', texto) or \
        re.search(r'csrf_token["\']?\s*[:=]\s*["\']([^"\']+)', texto)
    return m.group(1) if m else ""


def _dar_perfil_maquilador(socio, codigo="EST-SELF-01", estado="approved"):
    socio.write({"shrimp_razon_social": socio.shrimp_razon_social or socio.name,
                 "shrimp_ubicacion": socio.shrimp_ubicacion or "Guayaquil",
                 "pack_codigo_establecimiento": codigo})
    return socio.env["shrimp.partner.role"].sudo().create({
        "partner_id": socio.id, "role": "maquilador", "state": estado})


def _cosecha(env, socio, libras=30000.0, nombre="Cosecha propia"):
    return env["shrimp.product"].create({
        "name": nombre, "seller_partner_id": socio.id, "seller_role": "camaronera",
        "stage_id": env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
        "uom_id": env.ref("shrimp_marketplace.uom_libra").id,
        "presentation": "entero",
        "size_grade_id": env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1).id,
        "initial_qty": libras, "price": 2.3, "state": "published",
        "expected_delivery_date": date.today(),
    })


@tagged("post_install", "-at_install")
class TestEmpaquePropio(CopackCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.emp = cls.env["res.partner"].create({
            "name": "Exportadora selfpack", "is_company": True, "email": "exp.self@prueba.test",
            "vat_or_id": "0955500019001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Exportadora selfpack S.A.", "emp_capacidad_lb_dia": 90000})
        cls.cosecha = _cosecha(cls.env, cls.cli)

    # ------------------------------------------------------------------
    def _empacar_propio(self, socio, origen, libras, recibidas, empacadas, cajas=700):
        Orden = self.env["shrimp.copack.order"]
        orden = Orden.shrimp_create_self_packing(socio, origen, libras)
        self.assertTrue(orden.self_packing)
        self.assertEqual((orden.client_partner_id, orden.copacker_partner_id), (socio, socio))
        orden.write({"received_lb": recibidas})
        orden.action_register_reception()
        orden.write({"packed_lb": empacadas, "boxes": cajas, "packed_presentation": "entero"})
        orden.action_register_packing()
        # Sin acta de dos partes.
        self.assertEqual(orden.state, "packed")
        self.assertEqual(orden.acceptance_state, "na")
        self.assertFalse(orden.acceptance_ids)
        orden.action_self_close()
        self.assertEqual(orden.state, "closed")
        self.assertTrue(orden.self_signoff_date)
        return orden

    def test_camaronera_empaca_su_cosecha(self):
        _dar_perfil_maquilador(self.cli)
        orden = self._empacar_propio(self.cli, "p:%s" % self.cosecha.uuid_ref,
                                     20000.0, 20000.0, 19900.0)
        # Ni comisión ni cobro, ni liquidación.
        self.assertFalse(orden.es_facturable)
        self.assertEqual(orden.platform_amount, 0.0)
        self.assertEqual(orden.service_amount, 0.0)
        self.assertFalse(orden.charge_ids)
        # Mismo mecanismo de inventario que un empaque de terceros.
        self.assertTrue(orden.packed_lot_id)
        self.assertAlmostEqual(orden.packed_lot_id.available_qty, 19900.0)
        merma = orden.packing_move_ids.filtered(lambda m: m.move_type == "consumption")
        self.assertAlmostEqual(merma.qty, 100.0)
        self.assertAlmostEqual(self.cosecha.available_qty, 29900.0)
        # La venta posterior lleva el empaque propio, rotulado.
        tx = self.cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.emp, 19900.0)["transaction"]
        self.assertIn(orden, tx.copack_order_ids)
        etiqueta = orden.shrimp_plant_label()
        self.assertIn("Empaque propio — empacado por Camaronera de prueba", etiqueta)
        self.assertIn("EST-SELF-01", etiqueta)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "shrimp_marketplace.report_shrimp_full_traceability", tx.ids)[0].decode()
        self.assertIn("Empaque propio — empacado por", html)
        self.assertIn("EST-SELF-01", html)
        self.assertIn("cierre interno", html)
        # Trazabilidad pública (API / QR), si shrimp_api está instalado.
        if hasattr(tx, "_public_traceability_data"):
            data = tx.sudo()._public_traceability_data()
            propio = [p for p in data["packing"] if p.get("self_packing")]
            self.assertEqual(len(propio), 1)
            self.assertIn("Empaque propio", propio[0]["label"])
            self.assertEqual(propio[0]["establishment_code"], "EST-SELF-01")
            self.assertAlmostEqual(propio[0]["packed_lb"], 19900.0)

    def test_empacadora_empaca_su_compra(self):
        _dar_perfil_maquilador(self.emp, codigo="EST-EMP-77")
        tx = self.cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
            self.emp, 12000.0)["transaction"]
        tx.action_receive()
        orden = self._empacar_propio(self.emp, "t:%s" % tx.uuid_ref, 12000.0, 12000.0, 11950.0)
        self.assertEqual(orden.transaction_id, tx)
        self.assertTrue(orden.stock_lot_id)
        self.assertAlmostEqual(orden.stock_lot_id.available_qty, 0.0)
        self.assertAlmostEqual(orden.packed_lot_id.available_qty, 11950.0)
        tx.invalidate_recordset(["copack_order_ids"])
        self.assertIn(orden, tx.copack_order_ids)
        self.assertIn("planta EST-EMP-77", orden.shrimp_plant_label())
        self.assertFalse(self.env["shrimp.charge"].search([("copack_order_id", "=", orden.id)]))

    def test_sin_perfil_maquilador_no_se_puede(self):
        Orden = self.env["shrimp.copack.order"]
        with self.assertRaisesRegex(ValidationError, MSG_PERFIL):
            Orden.shrimp_create_self_packing(self.cli, "p:%s" % self.cosecha.uuid_ref, 1000.0)
        # Un perfil pendiente tampoco basta.
        _dar_perfil_maquilador(self.cli, estado="pending")
        with self.assertRaisesRegex(ValidationError, MSG_PERFIL):
            Orden.shrimp_create_self_packing(self.cli, "p:%s" % self.cosecha.uuid_ref, 1000.0)
        # Ni creando la orden a mano.
        with self.assertRaises(ValidationError):
            Orden.create({"self_packing": True, "client_partner_id": self.cli.id,
                          "copacker_partner_id": self.cli.id, "agreed_qty_lb": 1000.0,
                          "rate_per_lb": 0.0, "platform_rate_per_lb": 0.0})

    def test_empaque_propio_sin_tarifa_ni_lote_ajeno(self):
        _dar_perfil_maquilador(self.cli)
        Orden = self.env["shrimp.copack.order"]
        with self.assertRaises(ValidationError):
            Orden.create({"self_packing": True, "client_partner_id": self.cli.id,
                          "copacker_partner_id": self.cli.id, "agreed_qty_lb": 1000.0,
                          "product_id": self.cosecha.id,
                          "rate_per_lb": 0.15, "platform_rate_per_lb": 0.01})
        # Solo camarón propio.
        ajena = _cosecha(self.env, self.otro_cli, nombre="Cosecha ajena")
        with self.assertRaises(ValidationError):
            Orden.shrimp_create_self_packing(self.cli, "p:%s" % ajena.uuid_ref, 1000.0)
        # Se puede anular antes del cierre (no hay contraparte) y no consta.
        orden = Orden.shrimp_create_self_packing(self.cli, "p:%s" % self.cosecha.uuid_ref, 1000.0)
        orden.write({"received_lb": 1000.0})
        orden.action_register_reception()
        orden.write({"packed_lb": 990.0})
        orden.action_register_packing()
        orden.action_self_correct()
        self.assertEqual(orden.state, "received")
        orden.action_register_packing()
        orden.action_cancel()
        self.assertEqual(orden.state, "cancelled")
        self.assertFalse(orden._shrimp_consta_en_trazabilidad())
        self.assertFalse(orden.packing_move_ids)

    def test_mercado_sin_autocontratacion(self):
        """Con el perfil Maquilador, la cuenta sigue sin poder adjudicarse
        su propia solicitud pública."""
        _dar_perfil_maquilador(self.cli)
        # Dirigida a sí misma: no.
        with self.assertRaises(ValidationError):
            self.env["shrimp.copack.request"].create({
                "client_partner_id": self.cli.id, "quantity_lb": 10000, "presentation": "entero",
                "needed_from": date.today(), "needed_to": date.today() + timedelta(days=5),
                "copacker_partner_id": self.cli.id})
        # Abierta: no puede ofertar sobre la suya.
        sol = self.solicitud(dirigida=False)
        with self.assertRaises(ValidationError):
            self.env["shrimp.copack.offer"].create({
                "request_id": sol.id, "copacker_partner_id": self.cli.id,
                "rate_per_lb": 0.15, "capacity_lb": sol.quantity_lb,
                "available_from": date.today(), "available_to": date.today() + timedelta(days=5)})
        # Una orden de mercado consigo misma tampoco.
        with self.assertRaises(ValidationError):
            self.env["shrimp.copack.order"].create({
                "client_partner_id": self.cli.id, "copacker_partner_id": self.cli.id,
                "agreed_qty_lb": 1000.0, "rate_per_lb": 0.15})

    def test_flujo_de_terceros_sin_cambios(self):
        _sol, orden = self.hasta_empacar()
        self.assertFalse(orden.self_packing)
        self.assertEqual(orden.acceptance_state, "open")
        self.assertEqual(len(orden.acceptance_ids), 2)
        self.assertTrue(orden.es_facturable)
        for firma in orden.acceptance_ids:
            firma.action_accept(actor=firma.partner_id)
        self.assertEqual(orden.state, "signed")
        self.assertTrue(orden.charge_ids.filtered(lambda c: c.charge_type == "copack_platform"))
        # Un empaque de terceros no se cierra con el cierre interno.
        with self.assertRaises(ValidationError):
            orden.action_self_close()
        orden.action_close()
        self.assertEqual(orden.state, "closed")
        self.assertNotIn("Empaque propio", orden.shrimp_plant_label())


@tagged("post_install", "-at_install")
class TestEmpaquePropioPortal(HttpCase):
    """Las pantallas del portal y la llegada tras el login."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Socio = cls.env["res.partner"]
        portal = cls.env.ref("base.group_portal")
        cls.cam = Socio.create({
            "name": "Camaronera portal selfpack", "is_company": True,
            "email": "cam.selfpack@prueba.test", "vat_or_id": "0955500029001",
            "shrimp_user_type": "camaronera",
            "farm_razon_social": "Camaronera portal selfpack S.A.",
            "farm_representante": "Rep", "farm_telefono": "04-2222222", "farm_ubicacion": "Guayas"})
        cls.u_cam = cls.env["res.users"].create({
            "name": cls.cam.name, "login": "cam.selfpack@prueba.test", "password": CLAVE,
            "partner_id": cls.cam.id, "group_ids": [(6, 0, [portal.id])]})
        cls.maq = Socio.create({
            "name": "Maquila portal selfpack", "is_company": True,
            "email": "maq.selfpack@prueba.test", "vat_or_id": "0955500039001",
            "shrimp_user_type": "maquilador", "pack_razon_social": "Maquila portal S.A.",
            "pack_ubicacion": "Durán", "pack_codigo_establecimiento": "EST-PORTAL"})
        cls.u_maq = cls.env["res.users"].create({
            "name": cls.maq.name, "login": "maq.selfpack@prueba.test", "password": CLAVE,
            "partner_id": cls.maq.id, "group_ids": [(6, 0, [portal.id])]})
        cls.cosecha = _cosecha(cls.env, cls.cam, nombre="Cosecha portal")

    def url_open(self, *args, **kwargs):
        # Lo escrito por el test tiene que estar en la base antes de la
        # petición, y lo que escriba la petición tiene que verse después.
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def _post(self, url, pagina, data):
        data = dict(data, csrf_token=_csrf(pagina))
        return self.url_open(url, data=data)

    def test_portal_sin_perfil_y_con_perfil(self):
        self.authenticate(self.u_cam.login, CLAVE)
        r = self.url_open("/marketplace/copacking/self/new")
        self.assertEqual(r.status_code, 200)
        self.assertIn(MSG_PERFIL, r.text)
        self.assertIn("/marketplace/my-account", r.text)
        # Lista de órdenes: el botón está.
        r = self.url_open("/marketplace/copacking/orders")
        self.assertIn("Registrar empaque propio", r.text)

        _dar_perfil_maquilador(self.cam, codigo="EST-PORTAL-CAM")
        r = self.url_open("/marketplace/copacking/self/new?origen=p:%s" % self.cosecha.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(MSG_PERFIL, r.text)
        self.assertIn(self.cosecha.uuid_ref, r.text)
        r = self._post("/marketplace/copacking/self/new", r.text, {
            "origen_ref": "p:%s" % self.cosecha.uuid_ref, "quantity_lb": "5000"})
        self.assertEqual(r.status_code, 200)
        orden = self.env["shrimp.copack.order"].search([("client_partner_id", "=", self.cam.id)])
        self.assertEqual(len(orden), 1)
        self.assertTrue(orden.self_packing)
        self.assertIn("Empaque propio — empacado por", r.text)
        r = self._post("/marketplace/copacking/self/%s/reception" % orden.uuid_ref, r.text, {
            "received_lb": "5000", "supplies_received": "on"})
        r = self._post("/marketplace/copacking/self/%s/packing" % orden.uuid_ref, r.text, {
            "packed_lb": "4980", "boxes": "190", "packed_presentation": "entero"})
        self.assertEqual(orden.state, "packed")
        self.assertIn("Cerrar empaque propio", r.text)
        r = self._post("/marketplace/copacking/self/%s/close" % orden.uuid_ref, r.text, {})
        self.assertEqual(orden.state, "closed")
        self.assertTrue(orden.packed_lot_id)
        self.assertEqual(orden.self_signoff_user_id, self.u_cam)
        # Otra cuenta no ve ni toca este empaque.
        self.authenticate(self.u_maq.login, CLAVE)
        r = self.url_open("/marketplace/copacking/orders/%s" % orden.uuid_ref)
        self.assertNotEqual(r.status_code, 200)

    # ------------------------------------------------------------------
    # Llegada tras el login
    # ------------------------------------------------------------------
    def _login(self, login, password, redirect=None):
        self.authenticate(None, None)
        pagina = self.url_open("/web/login").text
        data = {"login": login, "password": password, "csrf_token": _csrf(pagina)}
        if redirect:
            data["redirect"] = redirect
        r = self.url_open("/web/login", data=data, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303), r.text[:300])
        return r.headers.get("Location", "")

    def test_login_portal_va_a_mi_panel(self):
        self.assertTrue(self._login(self.u_cam.login, CLAVE).endswith("/my/dashboard"))

    def test_login_respeta_redirect_explicito(self):
        destino = self._login(self.u_cam.login, CLAVE, redirect="/marketplace/copacking/orders")
        self.assertTrue(destino.endswith("/marketplace/copacking/orders"), destino)
        destino = self._login(self.u_maq.login, CLAVE, redirect="/copacker/inbox")
        self.assertTrue(destino.endswith("/copacker/inbox"), destino)

    def test_login_interno_sin_cambios(self):
        interno = self.env["res.users"].create({
            "name": "Interno selfpack", "login": "interno.selfpack@prueba.test",
            "password": CLAVE, "group_ids": [(6, 0, [self.env.ref("base.group_user").id])]})
        destino = self._login(interno.login, CLAVE)
        self.assertIn("/odoo", destino)
        self.assertNotIn("/my/dashboard", destino)

    def test_llegada_por_sitio(self):
        """El hook de cada sitio: marketplace → /my/dashboard (perfil con panel);
        empaque → bandeja del maquilador; verificadores → su bandeja; el
        resto, el destino de siempre (/my)."""
        W = self.env["website"].sudo()
        principal = W._shrimp_main_site()
        empaque = W._shrimp_platform_site("copacker")
        verif = W._shrimp_platform_site("verifier")
        self.assertEqual(principal._shrimp_login_landing(self.u_cam), "/my/dashboard")
        # El maquilador no tiene panel en el marketplace: /my de siempre.
        self.assertIsNone(principal._shrimp_login_landing(self.u_maq))
        self.assertIsNone(principal._shrimp_login_landing(self.env.ref("base.user_admin")))
        if empaque:
            self.assertEqual(empaque._shrimp_login_landing(self.u_maq), "/copacker/inbox")
            self.assertIsNone(empaque._shrimp_login_landing(self.u_cam))
        if verif:
            self.assertIsNone(verif._shrimp_login_landing(self.u_cam))
            empresa = self.env["res.partner"].search(
                [("shrimp_user_type", "=", "verificador"), ("user_ids", "!=", False)], limit=1)
            if empresa:
                self.assertEqual(verif._shrimp_login_landing(empresa.user_ids[:1]),
                                 "/verifier/inbox")
