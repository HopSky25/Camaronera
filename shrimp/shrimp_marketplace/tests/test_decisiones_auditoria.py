"""Decisiones de la auditoría de procesos (19.0.1.38.0).

D-30 PCR positivo bloquea el lote · D-31 caducidad de publicaciones ·
D-49 la cosecha cierra la siembra · D-13 vencimiento de certificados.
(D-28, chequeo retirado: test_stock_sin_dueno y test_coherencia.)
Todos con la fecha congelada.
"""
import base64
from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from .common import crear_socios, producto

MP = "shrimp_marketplace."
UR = "shrimp_user_registry."
HOY = date(2026, 10, 16)
AHORA = datetime(2026, 10, 16, 15, 0, 0)


@tagged("post_install", "-at_install")
class TestDecisionesAuditoria(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.freezer = freeze_time(AHORA)
        cls.freezer.start()
        cls.s = crear_socios(cls.env, "dec")
        cls.att = cls.env["ir.attachment"].create({
            "name": "c.pdf", "datas": base64.b64encode(b"%PDF-1.4 test"),
            "mimetype": "application/pdf"})

    @classmethod
    def tearDownClass(cls):
        cls.freezer.stop()
        super().tearDownClass()

    def _mensajes(self, rec, texto):
        return rec.message_ids.filtered(lambda m: texto in (m.subject or "") or texto in (m.body or ""))

    # ==================================================================
    # D-30 PCR positivo
    # ==================================================================
    def _ficha(self, prod, dias_atras=0, **pcr):
        vals = {"product_id": prod.id, "sample_date": HOY - timedelta(days=dias_atras),
                "pcr_wssv": "negativo", "pcr_ihhnv": "negativo", "pcr_ahpnd": "negativo",
                "pcr_ehp": "negativo"}
        vals.update(pcr)
        return self.env["shrimp.product.quality"].create(vals)

    def test_pcr_positivo_bloquea_y_compra_se_cancela_sin_strike(self):
        lote = producto(self.env, self.s["lab"], nombre="PL pcr dec")
        tx = lote.execute_purchase_flow(self.s["cam"], 10.0)["transaction"]
        self.assertEqual(tx.state, "confirmed")
        self._ficha(lote, dias_atras=1)
        self.assertFalse(lote.pcr_blocked)
        self.assertEqual(lote.state, "published")

        self._ficha(lote, pcr_wssv="positivo")
        self.assertTrue(lote.pcr_blocked)
        self.assertEqual(lote.state, "draft", "se despublica solo")
        self.assertIn(lote, self.env["shrimp.product"].search([("pcr_blocked", "=", True)]))
        # No se compra ni se vuelve a publicar por ningún camino.
        self.assertIn("WSSV", lote.motivo_no_comprable(self.s["cam"]))
        with self.assertRaises(ValidationError):
            lote.action_publish()
        with self.assertRaises(ValidationError):
            lote.write({"state": "published"})
        # Avisos: al vendedor (en el lote) y al comprador (en su compra).
        self.assertTrue(self._mensajes(lote, "bloqueado por PCR positivo"))
        self.assertTrue(self._mensajes(tx, "PCR positivo en el lote"))
        # El comprador cancela sin costo y sin strike al vendedor.
        self.assertEqual(tx.shrimp_buyer_cancel_cause(), "pcr_positive")
        reg = tx.shrimp_cancel("buyer", actor=self.s["cam"])
        self.assertEqual(tx.state, "cancel")
        self.assertEqual(reg.cause, "pcr_positive")
        self.assertFalse(reg.strike_id)
        self.assertFalse(self.env["shrimp.seller.strike"].search([("transaction_id", "=", tx.id)]))
        # El stock vuelve al lote, que sigue en borrador (no se republica solo).
        self.assertEqual(lote.available_qty, 100.0)
        self.assertEqual(lote.state, "draft")

    def test_ficha_positiva_no_se_corrige_y_ficha_nueva_desbloquea(self):
        lote = producto(self.env, self.s["lab"], nombre="PL pcr dec 2")
        positiva = self._ficha(lote, dias_atras=2, pcr_ehp="positivo")
        self.assertTrue(lote.pcr_blocked)
        vendedor = positiva.with_user(self.s["u_lab"]).sudo()
        with self.assertRaises(ValidationError):
            vendedor.write({"pcr_ehp": "negativo"})
        with self.assertRaises(ValidationError):
            vendedor.write({"sample_date": HOY - timedelta(days=30)})
        with self.assertRaises(ValidationError):
            vendedor.unlink()
        # Otros campos sí se corrigen (no cambian el resultado).
        vendedor.write({"notes": "Laboratorio acreditado"})
        # Una ficha nueva ANTERIOR no desbloquea; una posterior sin positivos sí.
        self._ficha(lote, dias_atras=5)
        self.assertTrue(lote.pcr_blocked)
        self._ficha(lote, dias_atras=0, pcr_ehp="no_realizado")
        self.assertFalse(lote.pcr_blocked)
        self.assertTrue(self._mensajes(lote, "desbloqueado"))
        lote.action_publish()
        self.assertEqual(lote.state, "published")

    # ==================================================================
    # D-31 Caducidad de publicaciones
    # ==================================================================
    def test_caducidad_por_disponible_hasta_aviso_y_vencimiento(self):
        lote = producto(self.env, self.s["lab"], nombre="PL cad dec",
                        available_to=AHORA + timedelta(days=2))
        Product = self.env["shrimp.product"]
        self.assertEqual(lote.publication_expires_at, AHORA + timedelta(days=2))
        res = Product._cron_publication_expiry()
        self.assertGreaterEqual(res["warned"], 1)
        self.assertEqual(lote.expiry_notice_for, AHORA + timedelta(days=2))
        self.assertEqual(len(self._mensajes(lote, "vence pronto")), 1)
        Product._cron_publication_expiry()
        self.assertEqual(len(self._mensajes(lote, "vence pronto")), 1, "sin repetir el aviso")
        with freeze_time(AHORA + timedelta(days=2, hours=1)):
            Product._cron_publication_expiry()
            self.assertEqual(lote.state, "draft")
            self.assertTrue(self._mensajes(lote, "venció"))
            with self.assertRaisesRegex(ValidationError, "Disponible hasta"):
                lote.action_publish()
            lote.available_to = AHORA + timedelta(days=20)
            lote.action_publish()
            self.assertEqual(lote.state, "published")

    def test_caducidad_por_fecha_de_entrega(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_marketplace.publication_expiry_days", "15")
        lote = producto(self.env, self.s["lab"], nombre="PL cad dec 2",
                        expected_delivery_date=HOY - timedelta(days=16))
        self.assertLess(lote.publication_expires_at, AHORA)
        self.env["shrimp.product"]._cron_publication_expiry()
        self.assertEqual(lote.state, "draft")
        with self.assertRaisesRegex(ValidationError, "entrega"):
            lote.action_publish()
        # Dentro de los 15 días sigue publicada.
        vivo = producto(self.env, self.s["lab"], nombre="PL cad dec 3",
                        expected_delivery_date=HOY - timedelta(days=10))
        self.env["shrimp.product"]._cron_publication_expiry()
        self.assertEqual(vivo.state, "published")

    def test_migracion_caducidad_no_vence_en_masa(self):
        viejo = producto(self.env, self.s["lab"], nombre="PL cad dec 4")
        viejo.write({"available_to": AHORA - timedelta(days=40)})
        futuro = producto(self.env, self.s["lab"], nombre="PL cad dec 5",
                          available_to=AHORA + timedelta(days=90))
        n = self.env["shrimp.product"]._shrimp_expiry_migrate()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(viejo.available_to, AHORA + timedelta(days=30))
        self.assertEqual(futuro.available_to, AHORA + timedelta(days=90))
        self.env["shrimp.product"]._cron_publication_expiry()
        self.assertEqual(viejo.state, "published")

    # ==================================================================
    # D-49 La cosecha cierra la siembra
    # ==================================================================
    def test_cosecha_cierra_la_siembra(self):
        env = self.env
        larva = producto(env, self.s["lab2"], nombre="Larva dec", initial_qty=1000.0,
                         expected_delivery_date=HOY)
        tx = larva.execute_purchase_flow(self.s["cam"], 300.0)["transaction"]
        tx.action_receive()
        lote = env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx.stock_move_ids.ids), ("owner_id", "=", self.s["cam"].id)])
        finca = env["shrimp.partner.facility"].create({
            "partner_id": self.s["cam"].id, "name": "Finca dec", "facility_type": "farm"})
        piscina = env["shrimp.partner.pond"].create({
            "partner_id": self.s["cam"].id, "facility_id": finca.id, "name": "Piscina dec"})
        siembra = env["shrimp.lot.allocation"].create({
            "stock_lot_id": lote.id, "pond_id": piscina.id, "allocated_qty": 150.0,
            "allocation_date": HOY - timedelta(days=90)})
        self.assertEqual(siembra.state, "allocated")
        consumido = lote.available_qty
        cosecha = producto(
            env, self.s["cam"], nombre="Cosecha dec", etapa=MP + "shrimp_stage_engorde",
            publicado=False, uom_id=env.ref(MP + "uom_libra").id, presentation="entero",
            size_grade_id=env.ref(MP + "size_entero_3040").id,
            origin_facility_id=finca.id, origin_pond_id=piscina.id, production_date=HOY)
        self.assertIn(siembra, cosecha.origin_allocation_ids)
        self.assertEqual(siembra.state, "released")
        self.assertEqual(lote.available_qty, consumido, "cerrar no mueve stock")

    # ==================================================================
    # D-13 Vencimiento de certificados
    # ==================================================================
    def test_certificados_vencen_con_avisos_sin_bloquear(self):
        env = self.env
        cert = env.ref(UR + "shrimp_certificate_registro_larvas")
        linea = env["shrimp.user.certificate.line"].create({
            "partner_id": self.s["lab"].id, "certificate_id": cert.id,
            "certificate_number": "DEC-1", "issue_date": HOY - timedelta(days=300),
            "expiry_date": HOY + timedelta(days=10), "file_attachment_id": self.att.id,
            "status": "approved"})
        lote = producto(env, self.s["lab"], nombre="PL cert dec")
        pcr = env.ref(UR + "shrimp_certificate_pcr_wssv")
        lp = env["shrimp.product.certificate.line"].with_context(shrimp_keep_cert_status=True).create({
            "product_id": lote.id, "certificate_id": pcr.id, "number": "PCR-DEC",
            "status": "approved", "attachment_id": self.att.id,
            "issue_date": HOY - timedelta(days=200), "expiry_date": HOY - timedelta(days=1)})
        self.assertEqual(linea.validity_state, "expiring")
        self.assertEqual(lp.validity_state, "expired")
        self.assertIn(lp, env["shrimp.product.certificate.line"].search(
            [("validity_state", "=", "expired")]))
        ProdLine = env["shrimp.product.certificate.line"]
        ProdLine._cron_certificate_expiry()
        self.assertEqual(linea.expiry_notice_stage, "warned")
        self.assertEqual(lp.expiry_notice_stage, "expired")
        aviso = self._mensajes(self.s["lab"], "Certificados vencidos o por vencer")
        self.assertEqual(len(aviso), 1)
        self.assertIn("lote «PL cert dec»", aviso.body)
        # Segunda corrida: nada nuevo.
        ProdLine._cron_certificate_expiry()
        self.assertEqual(len(self._mensajes(self.s["lab"], "Certificados vencidos o por vencer")), 1)
        # Al vencer: estado «Vencido» y un aviso más; el lote sigue publicado.
        with freeze_time(AHORA + timedelta(days=11)):
            ProdLine._cron_certificate_expiry()
            self.assertEqual(linea.validity_state, "expired")
            self.assertEqual(linea.expiry_notice_stage, "expired")
            self.assertTrue(linea.shrimp_is_expired())
        self.assertEqual(len(self._mensajes(self.s["lab"], "Certificados vencidos")), 2)
        self.assertEqual(lote.state, "published")
        self.assertEqual(linea.status, "approved")
        # Renovar la fecha reinicia el aviso.
        linea.expiry_date = HOY + timedelta(days=400)
        self.assertEqual(linea.expiry_notice_stage, "none")
        self.assertEqual(linea.validity_state, "valid")

    # ==================================================================
    # Borradores legales 2026-10.2
    # ==================================================================
    def test_borradores_legales(self):
        Doc = self.env["shrimp.legal.document"].sudo()
        Version = self.env["shrimp.legal.document.version"].sudo()
        doc = Doc.search([("code", "=", "contract")], limit=1)
        if not doc or not doc.current_version_id:
            self.skipTest("sin Contrato de uso vigente")
        borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1] or Version.create({
            "document_id": doc.id, "name": Version._suggest_code(doc),
            "content": doc.current_version_id.content, "change_type": "minor",
            "change_summary": "", "state": "draft"})
        publicada = doc.current_version_id.content
        self.env["shrimp.decision.policy"]._shrimp_update_decision_drafts()
        texto = str(borrador.content)
        self.assertEqual(texto.count("Publicaciones, sanidad y certificados"), 1)
        self.assertIn("PCR positivo", texto)
        self.env["shrimp.decision.policy"]._shrimp_update_decision_drafts()
        self.assertEqual(str(borrador.content).count("Publicaciones, sanidad y certificados"), 1)
        self.assertEqual(doc.current_version_id.content, publicada, "la publicada no se toca")
