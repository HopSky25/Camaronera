"""Modo de verificación elegido al comprar: plataforma o declarada por las partes.

* (A) «platform»: el flujo de siempre (verificadora acreditada, honorario).
* (B) «declared»: una de las partes carga y presenta el informe (verificadora
  externa o verificación propia); la otra lo acepta, rechaza o contraoferta
  con la misma ronda de aceptación. Sin verificadora, sin técnico, sin
  honorario; la comisión de la venta se cobra igual.
* No existe «no aplica».
"""
import base64
from datetime import date, timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_marketplace.tests.common import (
    CLAVE, PDF_MIN, crear_socios, csrf_de, producto)

from .common_seguridad import montar_verificacion

INFORME_LARVA = {"larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
                 "larvae_avg_size_mg": 5.0, "larvae_health_status": "good"}


def _compra_declarada(env, s, nombre, qty=10.0, **declared):
    lote = producto(env, s["lab"], nombre=nombre)
    res = lote.start_verified_purchase(s["cam"], qty, mode="declared",
                                       declared_vals=declared or None)
    return lote, res["verification"]


@tagged("post_install", "-at_install")
class TestVerificacionDeclarada(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        cls.s, cls.v_plat = montar_verificacion(cls.env, "dcl")

    def _declarada(self, nombre="Larva declarada", **kw):
        return _compra_declarada(self.env, self.s, nombre, **kw)

    def _presentar(self, v, actor, **extra):
        vals = dict(INFORME_LARVA, declared_source="self")
        vals.update(extra)
        v.action_declared_save(vals, actor)
        v.action_declared_submit(actor, notes="Lote conforme")

    def _posturas(self, v):
        return (v.acceptance_ids.filtered(lambda a: a.role == "buyer"),
                v.acceptance_ids.filtered(lambda a: a.role == "seller"))

    # ------------------------------------------------------------------
    # Compra en cada modo
    # ------------------------------------------------------------------
    def test_compra_modo_plataforma_sin_cambios(self):
        v = self.v_plat
        self.assertEqual(v.verification_mode, "platform")
        self.assertEqual(v.transaction_id.verification_mode, "platform")
        self.assertEqual(v.state, "received")
        self.assertEqual(v.verifier_partner_id, self.s["verif"])
        self.assertEqual(v.fee, 50.0)
        self.assertTrue(v.transaction_id.dispatch_id, "la plataforma sigue creando el despacho")
        self.assertIn("Verificado por", v.verification_label())
        # Sin verificadora en modo plataforma: error.
        lote = producto(self.env, self.s["lab"], nombre="Larva sin verif")
        with self.assertRaises(ValidationError):
            lote.start_verified_purchase(self.s["cam"], 5.0, None, fee=10.0)

    def test_compra_modo_declarado(self):
        lote, v = self._declarada(declared_source="external",
                                  external_verifier_name="Verificadora Externa SA",
                                  external_verifier_vat="0991234567001")
        tx = v.transaction_id
        self.assertEqual(v.verification_mode, "declared")
        self.assertEqual(tx.verification_mode, "declared")
        self.assertEqual(v.state, "declared_draft")
        self.assertEqual(tx.state, "pending_verification")
        self.assertFalse(v.verifier_partner_id)
        self.assertFalse(v.technician_partner_id)
        self.assertEqual(v.fee, 0.0)
        # Sin honorario: ningún cobro de verificación.
        self.assertFalse(v.charge_ids)
        self.assertFalse(self.env["shrimp.charge"].search([
            ("transaction_id", "=", tx.id), ("charge_type", "=", "verification_fee")]))
        # El despacho es opcional: no se crea solo.
        self.assertFalse(tx.dispatch_ids)
        # La cantidad queda reservada igual.
        self.assertEqual(lote.pending_verification_qty, 10.0)
        self.assertEqual(v.external_verifier_name, "Verificadora Externa SA")

    def test_no_aplica_imposible(self):
        lote = producto(self.env, self.s["lab"], nombre="Larva NA")
        for modo in ("na", "none", False):
            with self.assertRaises(ValidationError):
                lote.start_verified_purchase(self.s["cam"], 2.0, None, mode=modo)
        _lote, v = self._declarada("Larva NA 2")
        # Ni por la base de datos (CHECK) ni vaciando el campo obligatorio.
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self.env.cr.execute(
                "UPDATE shrimp_verification SET verification_mode = 'na' WHERE id = %s", (v.id,))
        with mute_logger("odoo.sql_db"), self.assertRaises(Exception), self.env.cr.savepoint():
            v.sudo().write({"verification_mode": False})
            v.env.flush_all()

    def test_restricciones_del_modo(self):
        _lote, v = self._declarada("Larva restr")
        with self.assertRaises(ValidationError):
            v.sudo().write({"verifier_partner_id": self.s["verif"].id})
        with self.assertRaises(ValidationError):
            v.sudo().write({"fee": 25.0})
        with self.assertRaises(ValidationError):
            v.sudo().write({"state": "approved"})
        # Declarada con verificadora elegida en la compra: no.
        lote = producto(self.env, self.s["lab"], nombre="Larva restr 2")
        with self.assertRaises(ValidationError):
            lote.start_verified_purchase(self.s["cam"], 2.0, self.s["verif"], mode="declared")
        # Un estado de la declarada en una de plataforma: no.
        with self.assertRaises(ValidationError):
            self.v_plat.sudo().write({"state": "declared"})

    # ------------------------------------------------------------------
    # Informe declarado por el comprador / por el vendedor
    # ------------------------------------------------------------------
    def test_declara_comprador_y_vendedor_acepta(self):
        lote, v = self._declarada("Larva dcl comprador")
        tx = v.transaction_id
        # Faltan datos: no se puede presentar.
        with self.assertRaises(UserError):
            v.action_declared_submit(self.s["cam"])
        self._presentar(v, self.s["cam"])
        self.assertEqual(v.state, "declared")
        self.assertTrue(v.is_final)
        self.assertEqual(v.declarant_partner_id, self.s["cam"])
        self.assertEqual(v.declarant_role, "buyer")
        self.assertEqual(v.acceptance_state, "waiting")
        self.assertEqual(tx.state, "pending_acceptance")
        comprador, vendedor = self._posturas(v)
        self.assertEqual(comprador.decision, "accepted", "presentar es suscribir")
        self.assertEqual(vendedor.decision, "pending")
        # Ya presentado, no se edita.
        with self.assertRaises(UserError):
            v.action_declared_save({"larvae_survival_rate": 50.0}, self.s["cam"])
        # El comprador no firma por el vendedor.
        with self.assertRaises(AccessError):
            vendedor.sudo().action_accept(actor=self.s["cam"])
        vendedor.sudo().action_accept(actor=self.s["lab"])
        self.assertEqual(v.acceptance_state, "closed")
        self.assertEqual(tx.state, "confirmed")
        self.assertTrue(tx.can_be_completed or tx.state == "confirmed")
        # Sin honorario; la comisión de la venta sí.
        cobros = self.env["shrimp.charge"].search([("transaction_id", "=", tx.id)])
        self.assertFalse(cobros.filtered(lambda c: c.charge_type == "verification_fee"))
        self.assertTrue(cobros.filtered(lambda c: c.charge_type == "commission"))
        self.assertFalse(v.fee_payer_partner_id)
        self.assertFalse(v.vendor_bill_id)

    def test_declara_vendedor_y_comprador_rechaza(self):
        _lote, v = self._declarada("Larva dcl vendedor")
        tx = v.transaction_id
        self._presentar(v, self.s["lab"])
        self.assertEqual(v.declarant_role, "seller")
        self.assertEqual(v.declared_other_party(), self.s["cam"])
        comprador, vendedor = self._posturas(v)
        self.assertEqual(vendedor.decision, "accepted")
        with self.assertRaises(UserError):
            comprador.sudo().action_reject(actor=self.s["cam"])      # sin motivo
        comprador.sudo().action_reject(reason="No coincide con lo pactado", actor=self.s["cam"])
        self.assertEqual(v.acceptance_state, "broken")
        self.assertEqual(tx.state, "cancel")
        self.assertFalse(v.charge_ids)

    def test_terceros_no_cargan_el_informe(self):
        _lote, v = self._declarada("Larva dcl tercero")
        for intruso in (self.s["sem"], self.s["verif"], self.s["lab2"]):
            with self.assertRaises(AccessError):
                v.action_declared_save(dict(INFORME_LARVA), intruso)
            with self.assertRaises(AccessError):
                v.action_declared_submit(intruso)
        # El informe declarado no admite datos del flujo.
        with self.assertRaises(AccessError):
            v.action_declared_save({"state": "declared"}, self.s["cam"])
        # El informe de la plataforma no se carga como declarado.
        with self.assertRaises(UserError):
            self.v_plat.action_declared_save(dict(INFORME_LARVA), self.s["cam"])

    def test_externa_exige_nombre_y_pdf(self):
        _lote, v = self._declarada("Larva dcl externa")
        v.action_declared_save(dict(INFORME_LARVA, declared_source="external"), self.s["lab"])
        with self.assertRaises(UserError) as e:
            v.action_declared_submit(self.s["lab"])
        self.assertIn("PDF", str(e.exception))
        v.action_declared_save({"external_verifier_name": "Lab Externo",
                                "external_verifier_vat": "0990000001001",
                                "declared_report_file": base64.b64encode(PDF_MIN),
                                "declared_report_filename": "informe.pdf"}, self.s["lab"])
        v.action_declared_submit(self.s["lab"])
        self.assertEqual(v.state, "declared")
        etiqueta = v.verification_label()
        self.assertIn("Verificación declarada por", etiqueta)
        self.assertIn("Lab Externo", etiqueta)
        self.assertIn("0990000001001", etiqueta)
        self.assertIn("externa", etiqueta)

    # ------------------------------------------------------------------
    # Contraoferta y deshacer
    # ------------------------------------------------------------------
    def test_contraoferta_del_comprador(self):
        _lote, v = self._declarada("Larva dcl contra")
        tx = v.transaction_id
        precio = tx.price_unit
        # El vendedor declara un informe que NO cumple (llegó menos larva).
        self._presentar(v, self.s["lab"], larvae_qty_verified=8.0, larvae_health_status="rejected")
        cumple, _m = v.cumple_lo_publicado()
        self.assertFalse(cumple)
        comprador, vendedor = self._posturas(v)
        comprador.sudo().action_counter(precio * 0.8, reason="Faltó larva", actor=self.s["cam"])
        self.assertEqual(vendedor.decision, "pending")
        # El comprador deshace su contraoferta: el vendedor recupera su aceptación.
        comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "pending")
        self.assertEqual(vendedor.decision, "accepted")
        self.assertEqual(v.state, "declared", "deshacer una contraoferta no retira la declaración")
        comprador.sudo().action_counter(precio * 0.8, actor=self.s["cam"])
        vendedor.sudo().action_accept(actor=self.s["lab"])
        self.assertEqual(v.acceptance_state, "closed")
        self.assertAlmostEqual(tx.price_unit, round(precio * 0.8, 2), places=2)
        self.assertEqual(tx.state, "confirmed")

    def test_deshacer_de_la_otra_parte(self):
        _lote, v = self._declarada("Larva dcl undo otra")
        self._presentar(v, self.s["cam"])
        comprador, vendedor = self._posturas(v)
        vendedor.sudo().action_reject(reason="Lo reviso", actor=self.s["lab"])
        # Con el declarante ya decidido, el rechazo cierra la ronda: no se deshace.
        self.assertEqual(v.acceptance_state, "broken")
        with self.assertRaises(UserError):
            vendedor.sudo().action_signoff_undo(actor=self.s["lab"])

    def test_declarante_deshace_y_vuelve_a_borrador(self):
        _lote, v = self._declarada("Larva dcl retira")
        tx = v.transaction_id
        self._presentar(v, self.s["cam"])
        comprador, vendedor = self._posturas(v)
        info = comprador.signoff_undo_info(actor=self.s["cam"])
        self.assertTrue(info["can"])
        self.assertIn("borrador", info["until"])
        comprador.sudo().action_signoff_undo(reason="Corrijo la supervivencia", actor=self.s["cam"])
        self.assertEqual(v.state, "declared_draft")
        self.assertEqual(v.acceptance_state, "na")
        self.assertFalse(v.declarant_partner_id)
        self.assertEqual(tx.state, "pending_verification")
        self.assertEqual((comprador.decision, vendedor.decision), ("pending", "pending"))
        eventos = self.env["shrimp.signoff.event"].history_for(v)
        self.assertIn("undo", eventos.mapped("kind"))
        # Ahora lo corrige y lo presenta el VENDEDOR; el comprador acepta.
        v.action_declared_save({"larvae_survival_rate": 88.0}, self.s["lab"])
        v.action_declared_submit(self.s["lab"])
        self.assertEqual(v.declarant_role, "seller")
        self.assertEqual(vendedor.decision, "accepted")
        self.assertEqual(comprador.decision, "pending")
        comprador.sudo().action_accept(actor=self.s["cam"])
        self.assertEqual(v.acceptance_state, "closed")
        self.assertEqual(tx.state, "confirmed")

    def test_vencimiento_del_plazo(self):
        _lote, v = self._declarada("Larva dcl plazo")
        self._presentar(v, self.s["lab"])
        v.sudo().acceptance_deadline = fields.Datetime.now() - timedelta(days=1)
        self.env["shrimp.verification"]._cron_vencer_plazos()
        self.assertEqual(v.acceptance_state, "closed")
        self.assertEqual(v.transaction_id.state, "confirmed")

    def test_cancelar_borrador_libera_reserva(self):
        lote, v = self._declarada("Larva dcl cancela")
        v.action_cancel()
        self.assertEqual(v.state, "cancelled")
        self.assertEqual(v.transaction_id.state, "cancel")
        self.assertEqual(lote.pending_verification_qty, 0.0)

    # ------------------------------------------------------------------
    # Conflicto de interés: solo se salta en la declarada
    # ------------------------------------------------------------------
    def test_conflicto_de_interes_solo_en_plataforma(self):
        s = crear_socios(self.env, "dcf")
        multi = s["cam"]
        multi._shrimp_request_role("verificador")
        att = self.env["ir.attachment"].create({
            "name": "acred.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        self.env["shrimp.user.certificate.line"].create({
            "partner_id": multi.id,
            "certificate_id": self.env.ref("shrimp_verification.cert_acreditacion_verificador").id,
            "file_attachment_id": att.id, "status": "approved", "certificate_number": "DCF-1",
            "issue_date": date.today() - timedelta(days=30),
            "expiry_date": date.today() + timedelta(days=365)})
        lote = producto(self.env, s["lab"], nombre="Larva DCF")
        # Plataforma: no puede verificar su propia compra.
        with self.assertRaises(ValidationError):
            lote.start_verified_purchase(multi, 2.0, multi, fee=10.0, mode="platform")
        # Declarada: la compradora (que también es verificadora) verifica
        # por su cuenta y declara; el control de independencia no aplica.
        res = lote.start_verified_purchase(multi, 2.0, mode="declared")
        v = res["verification"]
        v._check_verifier_independence()
        v.action_declared_save(dict(INFORME_LARVA, declared_source="self"), multi)
        v.action_declared_submit(multi)
        self.assertEqual(v.declarant_partner_id, multi)
        self.assertIn(multi.name, v.verification_label())
        self.assertIn("propia", v.verification_label())

    # ------------------------------------------------------------------
    # Reputación: solo la plataforma
    # ------------------------------------------------------------------
    def test_calificaciones_solo_plataforma(self):
        _lote, v = self._declarada("Larva dcl reseña")
        self._presentar(v, self.s["cam"])
        with self.assertRaises(ValidationError):
            self.env["shrimp.verifier.review"].create({
                "verification_id": v.id, "reviewer_partner_id": self.s["cam"].id, "rating": 5})
        antes = self.s["verif"].verifier_rating_count
        self.env["shrimp.verifier.review"].create({
            "verification_id": self.v_plat.id, "reviewer_partner_id": self.s["cam"].id, "rating": 4})
        self.s["verif"].invalidate_recordset()
        self.assertEqual(self.s["verif"].verifier_rating_count, antes + 1)

    # ------------------------------------------------------------------
    # Trazabilidad
    # ------------------------------------------------------------------
    def test_etiquetas_y_linea_de_tiempo(self):
        _lote, v = self._declarada("Larva dcl etiqueta")
        self.assertIn("pendiente de presentar", v.verification_label())
        self.assertEqual([p["titulo"] for p in v.stage_timeline()],
                         ["En preparación", "Presentada", "Aceptada"])
        self._presentar(v, self.s["cam"])
        self.assertIn(self.s["cam"].name, v.verification_label())
        self.assertIn("verificación propia", v.verification_label())
        plataforma = self.v_plat.verification_label()
        self.assertIn(self.s["verif"].name, plataforma)
        self.assertIn("acreditada", plataforma)
        # Parte de WhatsApp sin enlace al detalle del verificador.
        self.assertNotIn("/verifier/verifications/", v.whatsapp_report())


@tagged("post_install", "-at_install")
class TestVerificacionDeclaradaPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        cls.s, _v = montar_verificacion(cls.env, "dpo")
        cls.lote = producto(cls.env, cls.s["lab"], nombre="Larva portal declarada")

    def test_compra_y_formulario_declarado(self):
        self.authenticate(self.s["u_cam"].login, CLAVE)
        pagina = self.url_open("/marketplace/buy/%s" % self.lote.uuid_ref).text
        # Las dos opciones, con su explicación; sin «no aplica».
        self.assertIn('name="verification_mode" value="platform"', pagina)
        self.assertIn('name="verification_mode" value="declared"', pagina)
        self.assertIn("Verificación declarada por las partes", pagina)
        self.assertNotIn("No aplica", pagina)
        token = csrf_de(pagina)
        r = self.url_open("/marketplace/buy/%s/verify" % self.lote.uuid_ref, data={
            "csrf_token": token, "qty": "3", "verification_mode": "declared",
            "declared_source": "external", "external_verifier_name": "Externa Portal SA",
            "external_verifier_vat": "0990000002001"})
        v = self.env["shrimp.verification"].search(
            [("product_id", "=", self.lote.id), ("verification_mode", "=", "declared")])
        self.assertEqual(len(v), 1)
        self.assertIn("/declare", r.url)
        self.assertEqual(v.external_verifier_name, "Externa Portal SA")
        form = "/marketplace/verifications/%s/declare" % v.uuid_ref
        # El vendedor carga y presenta (con el PDF de la externa).
        self.authenticate(self.s["u_lab"].login, CLAVE)
        pagina = self.url_open(form).text
        self.assertIn("Presentar el informe", pagina)
        self.assertIn("larvae_survival_rate", pagina, "mismo formulario que el del técnico")
        token = csrf_de(pagina)
        r = self.url_open(form + "/save", data=dict(
            {k: str(val) for k, val in INFORME_LARVA.items()}, csrf_token=token,
            declared_source="external", external_verifier_name="Externa Portal SA",
            external_verifier_vat="0990000002001", accion="presentar",
            verdict_notes="Conforme"),
            files={"declared_report_file": ("informe.pdf", PDF_MIN, "application/pdf")})
        v.invalidate_recordset()
        self.assertEqual(v.state, "declared", r.url)
        self.assertEqual(v.declarant_partner_id, self.s["lab"])
        self.assertTrue(v.declared_report_file)
        # Un tercero no entra.
        self.authenticate(self.s["u_sem"].login, CLAVE)
        self.assertEqual(self.url_open(form).status_code, 403)
        # El comprador ve el panel de aceptación con la etiqueta de declarada y acepta.
        self.authenticate(self.s["u_cam"].login, CLAVE)
        panel = "/marketplace/verifications/%s/acceptance" % v.uuid_ref
        pagina = self.url_open(panel).text
        self.assertIn("Verificación declarada por", pagina)
        self.assertIn("Informe declarado", pagina)
        pdf = self.url_open(form + "/report")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        token = csrf_de(pagina)
        self.url_open("/marketplace/verifications/%s/accept" % v.uuid_ref, data={"csrf_token": token})
        v.invalidate_recordset()
        self.assertEqual(v.acceptance_state, "closed")
        # Trazabilidad privada: rotulada como declarada.
        tx = v.transaction_id
        tx.invalidate_recordset()
        traza = self.url_open("/marketplace/purchases/%s/traceability" % tx.uuid_ref)
        if traza.status_code == 200:
            self.assertIn("Verificación declarada por", traza.text)
