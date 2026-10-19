"""Decisiones del usuario sobre la auditoría de procesos (copacking).

D-14 (CR-14) acta: plazo de firma con recordatorios; el silencio del cliente
vale conformidad; mediación de la plataforma en la disputa; facturable y en
el certificado solo desde «firmada»; «por cobrar (sin firmar)» aparte; si la
planta cancela, la solicitud se reabre a las demás.

D-34 (AL-24) con el camarón en planta la planta no cancela sola (acta de
devolución firmada por el cliente o aprobación de la plataforma); antes de
recibir cancela con motivo; la compra de origen cancelada cancela en cascada
o bloquea; la planta pendiente no opera (regla en el modelo).

D-47 (ME-23) remanente de una adjudicación parcial publicado solo;
vencimiento de solicitudes y ofertas; el cliente rechaza ofertas; una regla de
cancelación para portal y API; la orden firmada se cierra sola.

Todas las pruebas corren con la hora congelada.
"""
from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, tagged

from .common import CopackCommon

HOY = date(2026, 10, 19)
AHORA = datetime(2026, 10, 19, 15, 0, 0)


@tagged("post_install", "-at_install")
class TestCopackDecisiones(CopackCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Socio = cls.env["res.partner"]
        cls.maq2 = Socio.create({
            "name": "Segunda planta", "is_company": True, "email": "maq2@prueba.test",
            "vat_or_id": "0955500031001", "shrimp_user_type": "maquilador",
            "pack_razon_social": "Segunda planta S.A.", "pack_ubicacion": "Durán",
            "pack_codigo_establecimiento": "99777", "pack_capacidad_lb_semana": 90000,
        })
        cls.env["res.users"].create({
            "name": cls.maq2.name, "login": "maq2@prueba.test", "partner_id": cls.maq2.id,
            "group_ids": [(6, 0, [cls.env.ref("base.group_portal").id])]})
        cls.admin = cls.env["res.users"].create({
            "name": "Admin empaque", "login": "admin.cpk@prueba.test",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("shrimp_copacking.group_copacking_manager").id])]})

    # ------------------------------------------------------------------ ayudas
    def _sol(self, libras=40000, dirigida=False):
        s = self.env["shrimp.copack.request"].create({
            "client_partner_id": self.cli.id, "quantity_lb": libras, "presentation": "entero",
            "needed_from": HOY, "needed_to": HOY + timedelta(days=30),
            "copacker_partner_id": self.maq.id if dirigida else False,
        })
        s.action_publish()
        return s

    def _oferta(self, sol, maq=None, capacidad=None, tarifa=0.18):
        return self.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": (maq or self.maq).id,
            "rate_per_lb": tarifa, "capacity_lb": capacidad or sol.quantity_lb,
            "available_from": HOY, "available_to": HOY + timedelta(days=20),
        })

    def _empacada(self, recibidas=40000, empacadas=39900):
        sol = self._sol()
        orden = self._oferta(sol).action_accept(actor=self.cli)
        orden.write({"received_lb": recibidas})
        orden.action_register_reception()
        orden.write({"packed_lb": empacadas})
        orden.action_register_packing()
        return sol, orden

    def _firma(self, orden, rol):
        return orden.acceptance_ids.filtered(lambda f: f.role == rol)

    # ================================================================== D-14
    def test_acta_plazo_recordatorio_y_silencio(self):
        with freeze_time(AHORA):
            _sol, orden = self._empacada()
            self.assertEqual(orden.acceptance_state, "open")
            self.assertEqual(orden.acceptance_deadline, AHORA + timedelta(hours=48))
            # Empacada y a la firma: NO es facturable ni consta en el certificado.
            self.assertFalse(orden.es_facturable)
            self.assertFalse(orden._shrimp_consta_en_trazabilidad())
            self._firma(orden, "copacker").action_accept(actor=self.maq)
        with freeze_time(AHORA + timedelta(hours=25)):
            orden._cron_copack_acta()
            self.assertIn("r24", orden.acceptance_reminder_marks)
            self.assertEqual(orden.state, "packed")
        with freeze_time(AHORA + timedelta(hours=48, minutes=1)):
            orden._cron_copack_acta()
            self.assertEqual(orden.state, "signed")
            self.assertTrue(orden.tacit_acceptance)
            cli = self._firma(orden, "client")
            self.assertEqual(cli.decision, "accepted")
            self.assertTrue(cli.auto)
            self.assertTrue(orden.es_facturable)
            self.assertTrue(orden.charge_ids.filtered(lambda c: c.charge_type == "copack_platform"))
            # La firma por silencio no se deshace.
            self.assertFalse(cli.signoff_can_undo(actor=self.cli))
        # Cierre automático pasado el plazo (24 h por defecto).
        with freeze_time(AHORA + timedelta(hours=73)):
            orden._cron_copack_acta()
            self.assertEqual(orden.state, "closed")
            self.assertEqual(orden.request_id.state, "done")

    def test_disputa_mediacion_de_la_plataforma(self):
        with freeze_time(AHORA):
            _sol, orden = self._empacada()
            self._firma(orden, "client").action_reject("Faltan 300 lb", actor=self.cli)
            self.assertEqual(orden.acceptance_state, "disputed")
            self.assertTrue(orden.dispute_date)
            self.assertTrue(orden.activity_ids, "la plataforma recibe la actividad de mediación")
            # Una disputa no vence por silencio.
            with freeze_time(AHORA + timedelta(days=5)):
                orden._cron_copack_acta()
            self.assertEqual(orden.acceptance_state, "disputed")
            # Solo la plataforma media.
            with self.assertRaises(AccessError):
                orden.with_user(self.u_maq).action_mediation_uphold("Lo doy por bueno")
            wiz = self.env["shrimp.copack.platform.wizard"].with_user(self.admin).create({
                "order_id": orden.id, "action": "uphold",
                "note": "Revisadas las guías de remisión: las libras cuadran."})
            wiz.action_confirm()
            self.assertEqual(orden.state, "signed")
            self.assertEqual(orden.mediation_resolution, "upheld")
            self.assertTrue(orden.es_facturable)
            self.assertFalse(orden.activity_ids)

    def test_mediacion_reabre_el_acta(self):
        with freeze_time(AHORA):
            _sol, orden = self._empacada()
            self._firma(orden, "client").action_reject("No cuadra", actor=self.cli)
            orden.with_user(self.admin).action_mediation_reopen("Que la planta rectifique")
            self.assertEqual(orden.state, "received")
            self.assertEqual(orden.mediation_resolution, "reopened")

    def test_planta_cancela_antes_de_recibir_y_se_reabre(self):
        with freeze_time(AHORA):
            sol = self._sol()
            mia = self._oferta(sol)
            otra = self._oferta(sol, maq=self.maq2, tarifa=0.2)
            orden = mia.action_accept(actor=self.cli)
            self.assertEqual(otra.closed_reason, "award")
            # Sin motivo, no.
            with self.assertRaises(ValidationError):
                orden.action_cancel(actor=self.maq)
            orden.action_cancel(actor=self.maq, reason="Se dañó la cámara de frío")
            self.assertEqual(orden.state, "cancelled")
            self.assertEqual(orden.cancelled_by, "copacker")
            self.assertEqual(mia.state, "cancelled")
            self.assertEqual(sol.state, "published")
            self.assertFalse(sol.order_id)
            self.assertEqual(otra.state, "sent", "la oferta descartada por la adjudicación vuelve")
            # La planta que canceló no vuelve a ofertar.
            with self.assertRaises(ValidationError):
                mia.write({"rate_per_lb": 0.15})
            # El cliente adjudica a la otra.
            nueva = otra.action_accept(actor=self.cli)
            self.assertEqual(sol.order_id, nueva)

    def test_cliente_cancela_la_orden_cancela_la_solicitud(self):
        with freeze_time(AHORA):
            sol = self._sol()
            orden = self._oferta(sol).action_accept(actor=self.cli)
            orden.action_cancel(actor=self.cli)
            self.assertEqual(orden.cancelled_by, "client")
            self.assertEqual(sol.state, "cancelled")

    # ================================================================== D-34
    def test_con_camaron_en_planta_no_cancela_sola(self):
        with freeze_time(AHORA):
            sol = self._sol()
            orden = self._oferta(sol).action_accept(actor=self.cli)
            orden.write({"received_lb": 40000})
            orden.action_register_reception()
            with self.assertRaises(ValidationError):
                orden.action_cancel(actor=self.maq, reason="No puedo")
            # El cliente sí puede (es su camarón).
            self.assertEqual(orden.shrimp_cancel_problem(actor=self.cli), "")
            acta = orden.action_request_return("Avería de la línea", returned_lb=40000,
                                               actor=self.maq)
            self.assertEqual(orden.pending_cancellation_id, acta)
            # Con la cancelación pendiente el trabajo se detiene.
            orden.write({"packed_lb": 39000})
            with self.assertRaises(ValidationError):
                orden.action_register_packing()
            # Solo el cliente firma el acta de devolución.
            with self.assertRaises(AccessError):
                acta.action_client_sign(True, actor=self.maq)
            acta.action_client_sign(True, actor=self.cli)
            self.assertEqual(orden.state, "cancelled")
            self.assertEqual(sol.state, "published", "la solicitud se reabre")

    def test_acta_de_devolucion_rechazada_y_aprobacion_de_plataforma(self):
        with freeze_time(AHORA):
            sol = self._sol()
            orden = self._oferta(sol).action_accept(actor=self.cli)
            orden.write({"received_lb": 40000})
            orden.action_register_reception()
            acta = orden.action_request_return("Avería", returned_lb=40000, actor=self.maq)
            acta.action_client_sign(False, note="Esperen la reparación", actor=self.cli)
            self.assertEqual(orden.state, "received")
            self.assertFalse(orden.pending_cancellation_id)
            pedido = orden.action_request_platform_cancel("La reparación tarda 3 semanas",
                                                         actor=self.maq)
            self.assertEqual(pedido.kind, "platform")
            self.assertTrue(orden.activity_ids)
            wiz = self.env["shrimp.copack.platform.wizard"].with_user(self.admin).create({
                "order_id": orden.id, "action": "approve_cancel",
                "note": "Avería comprobada; el cliente recupera su camarón."})
            wiz.action_confirm()
            self.assertEqual(orden.state, "cancelled")
            self.assertEqual(orden.cancelled_by, "platform")
            self.assertEqual(sol.state, "published")

    def test_planta_no_operativa_no_oferta_ni_recibe(self):
        with freeze_time(AHORA):
            sol = self._sol()
            orden = self._oferta(sol).action_accept(actor=self.cli)
            self.maq2.shrimp_account_state = "pending"
            with self.assertRaises(ValidationError):
                self._oferta(self._sol(), maq=self.maq2)
            self.maq.shrimp_account_state = "pending"
            orden.write({"received_lb": 40000})
            with self.assertRaises(ValidationError):
                orden.action_register_reception()

    @freeze_time(AHORA)
    def test_compra_de_origen_cancelada(self):
        emp = self.env["res.partner"].create({
            "name": "Exportadora cascada", "is_company": True, "email": "exp.casc@prueba.test",
            "vat_or_id": "0955500032001", "shrimp_user_type": "empacadora",
            "emp_razon_social": "Exportadora cascada S.A.", "emp_capacidad_lb_dia": 90000})
        cosecha = self.env["shrimp.product"].create({
            "name": "Cosecha cascada", "seller_partner_id": self.cli.id,
            "seller_role": "camaronera",
            "stage_id": self.env.ref("shrimp_marketplace.shrimp_stage_engorde").id,
            "uom_id": self.env.ref("shrimp_marketplace.uom_libra").id,
            "presentation": "entero",
            "size_grade_id": self.env["shrimp.size.grade"].search(
                [("presentation", "=", "entero")], limit=1).id,
            "initial_qty": 60000.0, "price": 2.3, "state": "published",
            "expected_delivery_date": date.today(),
        })

        def compra(libras):
            return cosecha.with_context(shrimp_verified_flow=True).execute_purchase_flow(
                emp, libras)["transaction"]

        def pedir(tx, libras):
            s = self.env["shrimp.copack.request"].create({
                "client_partner_id": emp.id, "quantity_lb": libras, "presentation": "entero",
                "needed_from": date.today(), "needed_to": date.today() + timedelta(days=9),
                "transaction_id": tx.id, "product_id": cosecha.id})
            s.action_publish()
            return s

        # Orden adjudicada sin recibir + solicitud publicada: cascada.
        tx = compra(20000.0)
        self.assertEqual(tx.state, "confirmed")
        sol1 = pedir(tx, 10000.0)
        orden = self._oferta(sol1, capacidad=10000.0).action_accept(actor=emp)
        sol2 = pedir(tx, 10000.0)
        tx.shrimp_cancel("platform", reason="Prueba de cascada")
        self.assertEqual(orden.state, "cancelled")
        self.assertEqual(orden.cancelled_by, "system")
        self.assertEqual(sol1.state, "cancelled")
        self.assertEqual(sol2.state, "cancelled")
        # Con el camarón ya en planta, la compra no se cancela.
        tx2 = compra(20000.0)
        sol3 = pedir(tx2, 10000.0)
        orden3 = self._oferta(sol3, capacidad=10000.0).action_accept(actor=emp)
        orden3.write({"received_lb": 10000.0})
        orden3.action_register_reception()
        with self.assertRaises(ValidationError):
            tx2.shrimp_cancel("platform", reason="No debería")
        self.assertNotEqual(tx2.state, "cancel")

    # ================================================================== D-47
    def test_adjudicacion_parcial_publica_el_remanente(self):
        with freeze_time(AHORA):
            sol = self._sol(libras=40000)
            oferta = self._oferta(sol, capacidad=25000)
            orden = oferta.action_accept(actor=self.cli)
            self.assertEqual(orden.agreed_qty_lb, 25000)
            self.assertEqual(sol.quantity_lb, 25000)
            hija = sol.child_request_ids
            self.assertEqual(len(hija), 1)
            self.assertEqual(hija.quantity_lb, 15000)
            self.assertEqual(hija.state, "published")
            self.assertTrue(hija.is_open)
            self.assertEqual(hija.parent_request_id, sol)

    def test_parcial_en_solicitud_dirigida_no_choca_con_el_lote_minimo(self):
        with freeze_time(AHORA):
            sol = self._sol(libras=8000, dirigida=True)   # mínimo de la planta: 5.000
            orden = self._oferta(sol, capacidad=4000).action_accept(actor=self.cli)
            self.assertEqual(orden.agreed_qty_lb, 4000)
            self.assertEqual(sol.child_request_ids.quantity_lb, 4000)
            self.assertFalse(sol.child_request_ids.copacker_partner_id)

    def test_vencimiento_de_solicitudes_y_ofertas(self):
        with freeze_time(AHORA):
            self.env["ir.config_parameter"].sudo().set_param(
                "shrimp_copacking.request_expiry_days", "10")
            self.env["ir.config_parameter"].sudo().set_param(
                "shrimp_copacking.offer_expiry_days", "3")
            sol = self._sol()
            self.assertEqual(sol.expires_on, HOY + timedelta(days=10))
            oferta = self._oferta(sol)
            self.assertEqual(oferta.expires_on, HOY + timedelta(days=3))
        with freeze_time(AHORA + timedelta(days=4)):
            self.env["shrimp.copack.request"]._cron_copack_expiry()
            self.assertEqual(oferta.state, "expired")
            self.assertEqual(sol.state, "published")
            with self.assertRaises(ValidationError):
                oferta.action_accept(actor=self.cli)
            # La planta la renueva editándola.
            oferta.write({"rate_per_lb": 0.17})
            self.assertEqual(oferta.state, "sent")
            self.assertEqual(oferta.expires_on, HOY + timedelta(days=7))
        with freeze_time(AHORA + timedelta(days=9)):
            self.env["shrimp.copack.request"]._cron_copack_expiry()
            self.assertTrue(sol.expiry_reminder_sent, "aviso el día antes")
        with freeze_time(AHORA + timedelta(days=11)):
            self.env["shrimp.copack.request"]._cron_copack_expiry()
            self.assertEqual(sol.state, "expired")
            self.assertEqual(oferta.state, "expired")
            # Una solicitud vencida se puede cancelar (para volver a publicarla).
            self.assertEqual(sol.shrimp_cancel_problem(actor=self.cli), "")

    def test_el_cliente_rechaza_una_oferta(self):
        with freeze_time(AHORA):
            sol = self._sol()
            oferta = self._oferta(sol)
            with self.assertRaises(AccessError):
                oferta.action_reject(actor=self.maq)
            oferta.action_reject(actor=self.cli, reason="Muy cara")
            self.assertEqual((oferta.state, oferta.closed_reason), ("rejected", "client"))
            self.assertEqual(oferta.reject_reason, "Muy cara")
            with self.assertRaises(ValidationError):
                oferta.action_accept(actor=self.cli)
            oferta.write({"rate_per_lb": 0.12})
            self.assertEqual(oferta.state, "sent", "mejorarla la vuelve a enviar")

    def test_una_regla_de_cancelacion_de_solicitud(self):
        with freeze_time(AHORA):
            sol = self._sol()
            orden = self._oferta(sol).action_accept(actor=self.cli)
            # Otro no la cancela.
            self.assertTrue(sol.shrimp_cancel_problem(actor=self.otro_cli))
            with self.assertRaises(AccessError):
                sol.action_cancel(actor=self.otro_cli)
            # Adjudicada y sin empacar: se cancela con su orden.
            self.assertEqual(sol.shrimp_cancel_problem(actor=self.cli), "")
            sol.action_cancel(actor=self.cli)
            self.assertEqual((sol.state, orden.state), ("cancelled", "cancelled"))
            # Empacada: no (el camino es el acta).
            sol2, orden2 = self._empacada()
            self.assertTrue(sol2.shrimp_cancel_problem(actor=self.cli))
            with self.assertRaises(ValidationError):
                sol2.action_cancel(actor=self.cli)

    def test_borradores_legales(self):
        """La instalación / el -u agregan la cláusula a un BORRADOR del anexo
        (las versiones publicadas no se tocan) y no la duplican."""
        Version = self.env["shrimp.legal.document.version"]
        marca = "Actas, plazos y cancelaciones de maquila"
        doc = self.env.ref("shrimp_copacking.legal_doc_annex_maquilador")
        borrador = doc.version_ids.filtered(lambda v: v.state == "draft")
        self.assertEqual(len(borrador), 1)
        self.assertIn(marca, str(borrador.content))
        self.assertNotIn(marca, str(doc.current_version_id.content))
        antes = Version.search_count([])
        self.assertFalse(self.env["shrimp.copack.legal"]._shrimp_create_drafts(force=True))
        self.assertEqual(Version.search_count([]), antes)


@tagged("post_install", "-at_install")
class TestCopackDecisionesPortal(HttpCase):
    """Las pantallas nuevas se pintan para cada parte (plazo del acta, acta de
    devolución, «por cobrar (sin firmar)», rechazar ofertas, vencida)."""

    CLAVE = "Clave-decisiones-2026"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Socio = cls.env["res.partner"]
        portal = cls.env.ref("base.group_portal")
        cls.maq = Socio.create({
            "name": "Planta portal", "is_company": True, "email": "maq.pd@prueba.test",
            "vat_or_id": "0955500041001", "shrimp_user_type": "maquilador",
            "pack_razon_social": "Planta portal S.A.", "pack_ubicacion": "Guayaquil",
            "pack_codigo_establecimiento": "99551"})
        cls.cli = Socio.create({
            "name": "Camaronera portal", "is_company": True, "email": "cli.pd@prueba.test",
            "vat_or_id": "0955500042001", "shrimp_user_type": "camaronera",
            "farm_razon_social": "Camaronera portal S.A.", "farm_representante": "R",
            "farm_telefono": "04-0000000", "farm_ubicacion": "Guayas"})
        for socio, login in ((cls.maq, "maq.pd"), (cls.cli, "cli.pd")):
            cls.env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "password": cls.CLAVE, "group_ids": [(6, 0, [portal.id])]})
        sitio = cls.env["website"].sudo()._shrimp_copacker_site()
        if sitio:
            sitio.domain = False

    def _sol(self):
        s = self.env["shrimp.copack.request"].create({
            "client_partner_id": self.cli.id, "quantity_lb": 30000, "presentation": "entero",
            "needed_from": date.today(), "needed_to": date.today() + timedelta(days=10)})
        s.action_publish()
        return s

    def _oferta(self, sol, lb=30000):
        return self.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": self.maq.id, "rate_per_lb": 0.2,
            "capacity_lb": lb, "available_from": date.today(),
            "available_to": date.today() + timedelta(days=8)})

    def _ver(self, login, url, *textos):
        self.authenticate(login, self.CLAVE)
        resp = self.url_open(url)
        self.assertEqual(resp.status_code, 200, url)
        for t in textos:
            self.assertIn(t, resp.text, "%s: falta «%s»" % (url, t))
        return resp

    @freeze_time(AHORA)
    def test_pantallas(self):
        # Solicitud con oferta: el cliente puede rechazarla.
        sol = self._sol()
        oferta = self._oferta(sol, lb=20000)
        self._ver("cli.pd", "/marketplace/copacking/requests/%s" % sol.uuid_ref,
                  "Rechazar la oferta", "vence el")
        # Adjudicación parcial: el remanente aparece enlazado.
        orden = oferta.action_accept(actor=self.cli)
        self._ver("cli.pd", "/marketplace/copacking/requests/%s" % sol.uuid_ref,
                  "Remanente publicado")
        # Recibida: la planta ve «Devolver el camarón».
        orden.write({"received_lb": 20000})
        orden.action_register_reception()
        self._ver("maq.pd", "/marketplace/copacking/orders/%s" % orden.uuid_ref,
                  "Devolver el camarón")
        orden.action_request_return("Avería", returned_lb=20000, actor=self.maq)
        self._ver("cli.pd", "/marketplace/copacking/orders/%s" % orden.uuid_ref,
                  "Firmar el acta de devolución")
        orden.pending_cancellation_id.action_client_sign(False, actor=self.cli)
        # Empacada: plazo del acta y «por cobrar (sin firmar)».
        orden.write({"packed_lb": 19900})
        orden.action_register_packing()
        self._ver("cli.pd", "/marketplace/copacking/orders/%s" % orden.uuid_ref,
                  "Plazo para firmar")
        self._ver("maq.pd", "/copacker/settlements", "Por cobrar (sin firmar)", orden.name)
        # Vencida: «Publicar de nuevo».
        hija = sol.child_request_ids
        hija._shrimp_expire()
        self._ver("cli.pd", "/marketplace/copacking/requests/%s" % hija.uuid_ref,
                  "Publicar de nuevo")
