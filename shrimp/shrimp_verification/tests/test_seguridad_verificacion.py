from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import (
    CLAVE, PNG_1PX, csrf_de, producto)

from .common_seguridad import abrir_ronda, montar_verificacion


@tagged("post_install", "-at_install")
class TestSeguridadVerificacion(TransactionCase):
    """C2 (aceptación), A3 (escritura del informe), A1/A3 (técnicos), M1, M6."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "vm")

    def _posturas(self):
        comprador = self.v.acceptance_ids.filtered(lambda a: a.role == "buyer")
        vendedor = self.v.acceptance_ids.filtered(lambda a: a.role == "seller")
        return comprador, vendedor

    # ---------------- A3: el portal no escribe el informe ----------------
    def test_portal_no_escribe_verificacion_lineas_ni_conteos(self):
        v_verif = self.v.with_user(self.s["u_verif"])
        self.assertEqual(v_verif.name, self.v.name)          # lo lee
        for vals in ({"fee": 0.0}, {"state": "approved"}, {"weight_plant_lb": 1.0}):
            with self.assertRaises(AccessError):
                v_verif.write(vals)
        with self.assertRaises(AccessError):
            self.env["shrimp.verification.line"].with_user(self.s["u_verif"]).create({
                "verification_id": self.v.id, "quality_class": "a",
                "size_code": "30/40", "weight_lb": 10})
        with self.assertRaises(AccessError):
            self.env["shrimp.verification.count"].with_user(self.s["u_verif"]).create({
                "verification_id": self.v.id, "value": 10})
        # Ni el honorario en sudo si quien escribe es del portal.
        with self.assertRaises(AccessError):
            self.v.with_user(self.s["u_verif"]).sudo().write({"fee": 9999.0})

    def test_informe_cerrado_no_se_modifica(self):
        abrir_ronda(self.v)
        operador = self.env["res.users"].create({
            "name": "Operador v", "login": "operador.vm", "password": CLAVE,
            "group_ids": [(6, 0, [self.env.ref("shrimp_marketplace.group_shrimp_user").id])]})
        with self.assertRaises(UserError):
            self.v.with_user(operador).write({"larvae_qty_verified": 1.0})
        with self.assertRaises(UserError):
            self.env["shrimp.verification.count"].with_user(operador).create({
                "verification_id": self.v.id, "value": 3})
        # Los datos de proceso sí siguen su curso.
        self.v.with_user(operador).write({"buyer_notified": True})

    # ---------------- C2: cada parte firma la suya ----------------
    def test_una_parte_no_acepta_por_la_otra(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        with self.assertRaises(AccessError):
            comprador.sudo().action_accept(actor=self.s["lab"])          # vendedor por comprador
        with self.assertRaises(AccessError):
            vendedor.sudo().action_accept(actor=self.s["verif"])         # verificador por vendedor
        with self.assertRaises(AccessError):
            # Sin actor explícito el actor es el usuario de la sesión.
            comprador.with_user(self.s["u_lab"]).sudo().action_reject(reason="no")
        self.assertEqual(comprador.decision, "pending")
        self.assertEqual(vendedor.decision, "pending")
        comprador.sudo().action_accept(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "accepted")

    def test_la_firma_no_se_escribe_directo(self):
        abrir_ronda(self.v)
        comprador, _vendedor = self._posturas()
        with self.assertRaises(AccessError):
            comprador.with_user(self.s["u_cam"]).write({"decision": "accepted"})
        with self.assertRaises(AccessError):
            comprador.sudo().write({"decision": "counter", "counter_price": 0.01})
        self.assertEqual(comprador.decision, "pending")

    def test_contraoferta_solo_por_su_metodo_y_con_sus_reglas(self):
        abrir_ronda(self.v, cumple=False)
        comprador, vendedor = self._posturas()
        with self.assertRaises(UserError):
            vendedor.sudo().action_counter(5.0, actor=self.s["lab"])     # solo el comprador
        with self.assertRaises(AccessError):
            comprador.sudo().action_counter(5.0, actor=self.s["lab"])    # y solo el suyo
        with self.assertRaises(UserError):
            comprador.sudo().action_counter(10.0, actor=self.s["cam"])   # no >= precio pactado
        comprador.sudo().action_counter(8.0, reason="no cumplió", actor=self.s["cam"])
        self.assertEqual(comprador.decision, "counter")
        self.assertEqual(comprador.counter_price, 8.0)

    def test_contraoferta_rechazada_si_el_informe_cumple(self):
        abrir_ronda(self.v, cumple=True)
        comprador, _ = self._posturas()
        with self.assertRaises(UserError):
            comprador.sudo().action_counter(8.0, actor=self.s["cam"])

    # ---------------- A3: asignación de técnico ----------------
    def test_tecnico_debe_ser_activo_de_la_empresa_y_ajeno_a_las_partes(self):
        for malo in (self.s["tec_ajeno"], self.s["tec_baja"], self.s["cam"], self.s["lab"]):
            with self.assertRaises(ValidationError):
                with self.env.cr.savepoint():
                    self.v.action_assign_technician(malo)
        self.v.action_assign_technician(self.s["tec"])
        self.assertEqual(self.v.technician_partner_id, self.s["tec"])
        self.v.action_assign_technician(self.s["verif"])       # la propia empresa vale

    # ---------------- M1: compra verificada respeta el rol ----------------
    def test_compra_verificada_respeta_la_cadena(self):
        lote = producto(self.env, self.s["sem"], nombre="Larva m1")
        with self.assertRaises(ValidationError):
            lote.start_verified_purchase(self.s["cam"], 5.0, self.s["verif"])

    # ---------------- M6: datos bancarios ----------------
    def test_tecnico_no_lee_la_cuenta_bancaria_por_rpc(self):
        empresa = self.s["verif"].with_user(self.s["u_tec"])
        self.assertEqual(empresa.name, self.s["verif"].name)   # la ficha sí la ve
        for campo in ("ver_bank_account_number", "ver_bank_holder_id"):
            with self.assertRaises(AccessError):
                empresa.read([campo])
        # El teléfono/representante de una camaronera tampoco por RPC.
        with self.assertRaises(AccessError):
            self.s["cam"].with_user(self.s["u_cam"]).read(["farm_telefono"])


@tagged("post_install", "-at_install")
class TestSeguridadVerificacionHttp(HttpCase):
    """Rutas del verificador: fotos por token y técnicos por código."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "vh")
        sitio = cls.env["website"].sudo()._shrimp_verifier_site()
        if sitio:
            sitio.domain = False
        import base64
        cls.foto = cls.env["ir.attachment"].create({
            "name": "campo.png", "datas": base64.b64encode(PNG_1PX), "mimetype": "image/png",
            "res_model": "shrimp.verification", "res_id": cls.v.id})
        cls.v.photo_ids = [(6, 0, cls.foto.ids)]
        cls.token = cls.v.photo_token(cls.foto)

    def test_foto_por_token_no_por_id(self):
        self.authenticate(self.s["u_verif"].login, CLAVE)
        base = "/verifier/verifications/%s/photos/" % self.v.uuid_ref
        self.assertEqual(self.url_open(base + str(self.foto.id)).status_code, 404)
        self.assertEqual(self.url_open(base + self.token).status_code, 200)
        # Las partes la ven por la ruta del marketplace, también por token.
        self.authenticate(self.s["u_cam"].login, CLAVE)
        base = "/marketplace/verifications/%s/photos/" % self.v.uuid_ref
        self.assertEqual(self.url_open(base + str(self.foto.id)).status_code, 404)
        self.assertEqual(self.url_open(base + self.token).status_code, 200)

    def test_tecnicos_por_codigo(self):
        self.authenticate(self.s["u_verif"].login, CLAVE)
        pagina = self.url_open("/verifier/technicians").text
        token = csrf_de(pagina)
        self.assertTrue(token)
        self.assertNotIn("/verifier/technicians/%d/" % self.s["tec"].id, pagina)
        r = self.url_open("/verifier/technicians/%d/toggle" % self.s["tec"].id,
                          data={"csrf_token": token})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(self.s["tec"].active)
        r = self.url_open("/verifier/technicians/%s/toggle" % self.s["tec_ajeno"].uuid_ref,
                          data={"csrf_token": token})
        self.assertEqual(r.status_code, 404)
        r = self.url_open("/verifier/technicians/%s/toggle" % self.s["tec"].uuid_ref,
                          data={"csrf_token": token})
        self.assertEqual(r.status_code, 200)
        self.s["tec"].invalidate_recordset()
        self.assertFalse(self.s["tec"].active)

    def test_asignar_tecnico_por_codigo_y_de_la_empresa(self):
        self.authenticate(self.s["u_verif"].login, CLAVE)
        token = csrf_de(self.url_open("/verifier/verifications/%s" % self.v.uuid_ref).text)
        url = "/verifier/verifications/%s/assign" % self.v.uuid_ref
        for valor in (str(self.s["tec"].id), self.s["tec_ajeno"].uuid_ref, self.s["cam"].uuid_ref):
            r = self.url_open(url, data={"csrf_token": token, "technician_ref": valor},
                              allow_redirects=False)
            self.assertIn("choose_tech", r.headers.get("Location", ""))
        self.v.invalidate_recordset()
        self.assertFalse(self.v.technician_partner_id)
        self.url_open(url, data={"csrf_token": token, "technician_ref": self.s["tec"].uuid_ref})
        self.v.invalidate_recordset()
        self.assertEqual(self.v.technician_partner_id, self.s["tec"])


@tagged("post_install", "-at_install")
class TestFlujoVerificacionPortal(HttpCase):
    """Compra con verificación y firma del informe, por las pantallas."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "vf")

    def test_compra_verificada_y_firma_por_el_portal(self):
        lote = producto(self.env, self.s["lab"], nombre="Larva portal vf")
        self.authenticate(self.s["u_cam"].login, CLAVE)
        token = csrf_de(self.url_open("/marketplace/buy/%s" % lote.uuid_ref).text)
        self.url_open("/marketplace/buy/%s/verify" % lote.uuid_ref, data={
            "csrf_token": token, "qty": "4", "verifier_ref": self.s["verif"].uuid_ref})
        tx = self.env["shrimp.transaction"].search([("product_id", "=", lote.id)])
        self.assertEqual(tx.state, "pending_verification")
        self.assertTrue(tx.verification_id)
        # Firma del informe: cada parte la suya, por la pantalla.
        abrir_ronda(self.v)
        r = self.url_open("/marketplace/verifications/%s/accept" % self.v.uuid_ref,
                          data={"csrf_token": token})
        self.assertEqual(r.status_code, 200)
        comprador = self.v.acceptance_ids.filtered(lambda a: a.role == "buyer")
        vendedor = self.v.acceptance_ids.filtered(lambda a: a.role == "seller")
        self.assertEqual(comprador.decision, "accepted")
        self.assertEqual(vendedor.decision, "pending")
