"""Deshacer la decisión sobre el informe de verificación (firmas de dos partes).

Reglas que se comprueban:

* un rechazo con la otra parte pendiente NO cancela la compra: surte efecto
  al cerrarse la ronda (las dos decidieron o venció el plazo);
* cada parte deshace solo SU decisión, mientras la ronda siga abierta;
* deshacer una contraoferta devuelve la propuesta anterior y la postura que
  el vendedor tenía; si el vendedor ya respondió, no se deshace;
* todo queda en el historial y en el chatter, y a la otra parte se le avisa.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, csrf_de

from .common_seguridad import abrir_ronda, montar_verificacion


@tagged("post_install", "-at_install")
class TestDeshacerAceptacion(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        cls.s, cls.v = montar_verificacion(cls.env, "dsh")
        cls.tx = cls.v.transaction_id
        cls.Evento = cls.env["shrimp.signoff.event"]

    def _posturas(self):
        return (self.v.acceptance_ids.filtered(lambda a: a.role == "buyer"),
                self.v.acceptance_ids.filtered(lambda a: a.role == "seller"))

    def _eventos(self):
        return self.Evento.history_for(self.v)

    # ------------------------------------------------------------------
    def test_deshacer_aceptar(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_accept(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "accepted")
        self.assertTrue(comprador.signoff_can_undo(actor=self.s["cam"]))
        # La otra parte ve la postura pero no la puede deshacer.
        self.assertFalse(comprador.signoff_can_undo(actor=self.s["lab"]))
        comprador.sudo().action_signoff_undo(reason="Me equivoqué de botón", actor=self.s["cam"])
        self.assertEqual(comprador.decision, "pending")
        self.assertFalse(comprador.decided_at)
        self.assertEqual(self.v.acceptance_state, "waiting")
        # Historial: la decisión (marcada como deshecha) y la reversión.
        eventos = self._eventos()
        self.assertEqual(sorted(eventos.mapped("kind")), ["decision", "undo"])
        decision = eventos.filtered(lambda e: e.kind == "decision")
        deshacer = eventos.filtered(lambda e: e.kind == "undo")
        self.assertTrue(decision.undone)
        self.assertEqual(decision.undo_event_id, deshacer)
        self.assertEqual(deshacer.previous_decision, "accepted")
        self.assertEqual(deshacer.decision, "pending")
        self.assertEqual(deshacer.partner_id, self.s["cam"])
        self.assertEqual(deshacer.reason, "Me equivoqué de botón")
        # Chatter de la verificación.
        cuerpos = " ".join(self.v.message_ids.mapped("body"))
        self.assertIn("Decisión deshecha", cuerpos)
        self.assertIn("Me equivoqué de botón", cuerpos)
        # Nada que deshacer ya.
        with self.assertRaises(UserError):
            comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        # La ronda sigue normal: las dos aceptan y se cierra.
        comprador.sudo().action_accept(actor=self.s["cam"])
        vendedor.sudo().action_accept(actor=self.s["lab"])
        self.assertEqual(self.v.acceptance_state, "closed")
        self.assertEqual(self.tx.state, "confirmed")

    def test_rechazo_no_cancela_hasta_cerrar_la_ronda_y_se_deshace(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_reject(reason="No me gusta", actor=self.s["cam"])
        # Antes, esto cancelaba la compra en el acto.
        self.assertEqual(self.v.acceptance_state, "waiting")
        self.assertEqual(self.tx.state, "pending_acceptance")
        self.assertFalse(self.tx.is_cancelled)
        comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "pending")
        self.assertEqual(self.v.acceptance_state, "waiting")
        self.assertEqual(self.tx.state, "pending_acceptance")

    def test_rechazo_surte_efecto_cuando_decide_la_otra_y_ya_no_se_deshace(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_reject(reason="No me sirve", actor=self.s["cam"])
        vendedor.sudo().action_accept(actor=self.s["lab"])
        self.assertEqual(self.v.acceptance_state, "broken")
        self.assertEqual(self.tx.state, "cancel")
        self.assertFalse(comprador.signoff_can_undo(actor=self.s["cam"]))
        with self.assertRaises(UserError):
            comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        with self.assertRaises(UserError):
            vendedor.sudo().action_signoff_undo(actor=self.s["lab"])
        self.assertEqual(self.v.acceptance_state, "broken")

    def test_no_se_deshace_tras_cerrar_y_facturar(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_accept(actor=self.s["cam"])
        vendedor.sudo().action_accept(actor=self.s["lab"])
        self.assertEqual(self.v.acceptance_state, "closed")
        for postura, socio in ((comprador, self.s["cam"]), (vendedor, self.s["lab"])):
            with self.assertRaises(UserError):
                postura.sudo().action_signoff_undo(actor=socio)
        self.assertEqual((comprador.decision, vendedor.decision), ("accepted", "accepted"))

    def test_cada_parte_solo_la_suya_y_el_verificador_ninguna(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_accept(actor=self.s["cam"])
        with self.assertRaises(AccessError):
            comprador.sudo().action_signoff_undo(actor=self.s["lab"])
        with self.assertRaises(AccessError):
            comprador.sudo().action_signoff_undo(actor=self.s["verif"])
        # Por defecto el actor es el usuario: el verificador tampoco.
        with self.assertRaises(AccessError):
            comprador.with_user(self.s["u_verif"]).sudo().action_signoff_undo()
        self.assertEqual(comprador.decision, "accepted")

    def test_deshacer_contraoferta_restaura_la_propuesta_anterior(self):
        abrir_ronda(self.v, cumple=False)
        comprador, vendedor = self._posturas()
        precio = self.tx.price_unit
        vendedor.sudo().action_accept(actor=self.s["lab"])
        comprador.sudo().action_counter(precio - 1.0, reason="Talla menor", actor=self.s["cam"])
        self.assertEqual((comprador.decision, vendedor.decision), ("counter", "pending"))
        comprador.sudo().action_signoff_undo(reason="Prefiero pensarlo", actor=self.s["cam"])
        # La propuesta anterior era «ninguna»; el vendedor recupera su aceptación.
        self.assertEqual(comprador.decision, "pending")
        self.assertFalse(comprador.counter_price)
        self.assertEqual(vendedor.decision, "accepted")
        self.assertEqual(self.tx.price_unit, precio)
        self.assertEqual(self.v.acceptance_state, "waiting")
        # Dos contraofertas: deshacer la segunda deja la primera.
        comprador.sudo().action_counter(precio - 1.0, actor=self.s["cam"])
        comprador.sudo().action_counter(precio - 2.0, actor=self.s["cam"])
        comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        self.assertEqual(comprador.decision, "counter")
        self.assertAlmostEqual(comprador.counter_price, precio - 1.0)
        # Si el vendedor ya respondió, no se deshace.
        vendedor.sudo().action_reject(reason="Ni hablar", actor=self.s["lab"])
        self.assertEqual(self.v.acceptance_state, "broken")
        with self.assertRaises(UserError):
            comprador.sudo().action_signoff_undo(actor=self.s["cam"])

    def test_bloqueo_contraoferta_respondida(self):
        """Regla explícita aunque la ronda siguiera abierta."""
        abrir_ronda(self.v, cumple=False)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_counter(self.tx.price_unit - 1.0, actor=self.s["cam"])
        vendedor.with_context(_shrimp_firma_ok=True).write({"decision": "rejected"})
        self.v.acceptance_state = "waiting"
        self.assertIn("respondió", comprador.sudo()._signoff_undo_blocker())

    def test_plazo_tras_deshacer_y_cron(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_reject(reason="x", actor=self.s["cam"])
        # Faltan dos minutos para que venza el plazo.
        self.v.sudo().acceptance_deadline = fields.Datetime.now() + timedelta(minutes=2)
        comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        # Deshacer garantiza el margen mínimo: no lo acepta el cron en 2 min.
        self.assertGreaterEqual(self.v.acceptance_deadline,
                                fields.Datetime.now() + timedelta(minutes=59))
        # Con el plazo vencido, el cron trata la postura deshecha como cualquier
        # pendiente: callar es consentir.
        self.v.sudo().acceptance_deadline = fields.Datetime.now() - timedelta(minutes=1)
        self.env["shrimp.verification"]._cron_vencer_plazos()
        self.assertEqual(self.v.acceptance_state, "closed")
        self.assertTrue(comprador.auto)
        # Una aceptación automática no se deshace.
        self.assertIn("sistema", comprador.sudo()._signoff_undo_blocker())

    def test_cron_con_rechazo_vigente_da_el_trato_por_caido(self):
        abrir_ronda(self.v)
        comprador, vendedor = self._posturas()
        comprador.sudo().action_reject(reason="x", actor=self.s["cam"])
        self.v.sudo().acceptance_deadline = fields.Datetime.now() - timedelta(minutes=1)
        self.env["shrimp.verification"]._cron_vencer_plazos()
        self.assertEqual(self.v.acceptance_state, "broken")
        self.assertEqual(self.tx.state, "cancel")

    def test_gestor_deshace_en_nombre_con_motivo(self):
        abrir_ronda(self.v)
        comprador, _vendedor = self._posturas()
        comprador.sudo().action_accept(actor=self.s["cam"])
        gestor = self.env["res.users"].create({
            "name": "Gestor dsh", "login": "gestor.dsh", "password": CLAVE,
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("shrimp_marketplace.group_shrimp_manager").id])]})
        with self.assertRaises(ValidationError):
            comprador.with_user(gestor).action_signoff_undo(on_behalf=True)
        # Un portal no puede hacerse pasar por gestor.
        with self.assertRaises(AccessError):
            comprador.with_user(self.s["u_cam"]).sudo(False).action_signoff_undo(
                reason="x", on_behalf=True)
        asistente = self.env["shrimp.signoff.undo.wizard"].with_user(gestor).with_context(
            **comprador.action_open_signoff_undo_wizard()["context"]).create(
            {"reason": "Llamó por teléfono: se equivocó"})
        asistente.action_confirm()
        self.assertEqual(comprador.decision, "pending")
        ultimo = self._eventos()[:1]
        self.assertEqual(ultimo.kind, "undo")
        self.assertTrue(ultimo.on_behalf)
        self.assertEqual(ultimo.user_id, gestor)

    def test_aviso_a_la_otra_parte(self):
        abrir_ronda(self.v)
        comprador, _vendedor = self._posturas()
        comprador.sudo().action_accept(actor=self.s["cam"])
        enviados = []

        def falso(registro, xmlid, email_to, ctx=None):
            enviados.append((xmlid, email_to, (ctx or {}).get("portal_url")))
            return True

        with patch.object(type(self.Evento), "_send_template", falso):
            comprador.sudo().action_signoff_undo(actor=self.s["cam"])
        self.assertEqual(len(enviados), 1)
        xmlid, correo, url = enviados[0]
        self.assertEqual(xmlid, "shrimp_marketplace.mail_template_signoff_reverted")
        self.assertEqual(correo, self.s["lab"].email)
        self.assertIn(self.v.uuid_ref, url)
        self.assertIn("Aviso de la decisión deshecha enviado a", " ".join(
            self.v.message_ids.mapped("body")))
        # La plantilla se puede renderizar de verdad.
        ev = self._eventos()[:1]
        tpl = self.env.ref("shrimp_marketplace.mail_template_signoff_reverted")
        cuerpo = tpl._render_field("body_html", ev.ids)[ev.id]
        self.assertIn(self.s["cam"].name, str(cuerpo))

    def test_historial_inmutable(self):
        abrir_ronda(self.v)
        comprador, _vendedor = self._posturas()
        comprador.sudo().action_accept(actor=self.s["cam"])
        ev = self._eventos()[:1]
        with self.assertRaises(AccessError):
            ev.with_user(self.env.ref("base.user_admin")).write({"reason": "otra cosa"})
        with self.assertRaises(AccessError):
            ev.sudo().write({"reason": "otra cosa"})


@tagged("post_install", "-at_install")
class TestDeshacerAceptacionPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s, cls.v = montar_verificacion(cls.env, "dsp")
        abrir_ronda(cls.v)

    def test_portal_deshacer(self):
        comprador = self.v.acceptance_ids.filtered(lambda a: a.role == "buyer")
        vendedor = self.v.acceptance_ids.filtered(lambda a: a.role == "seller")
        comprador.sudo().action_reject(reason="Por error", actor=self.s["cam"])
        self.env.flush_all()
        panel = "/marketplace/verifications/%s/acceptance" % self.v.uuid_ref
        url = "/marketplace/verifications/%s/undo" % self.v.uuid_ref
        # El vendedor no ve el botón (no decidió) y no puede deshacer la del comprador.
        self.authenticate(self.s["u_lab"].login, CLAVE)
        pagina = self.url_open(panel).text
        self.assertNotIn(url, pagina)
        self.assertIn("Historial de decisiones", pagina)
        token = csrf_de(pagina)
        self.url_open(url, data={"csrf_token": token})
        comprador.invalidate_recordset()
        self.assertEqual(comprador.decision, "rejected")
        # El comprador sí lo ve y lo usa.
        self.authenticate(self.s["u_cam"].login, CLAVE)
        pagina = self.url_open(panel).text
        self.assertIn(url, pagina)
        self.assertIn("Deshacer mi decisión", pagina)
        token = csrf_de(pagina)
        # Sin CSRF no pasa.
        r = self.url_open(url, data={"motivo": "sin token"}, allow_redirects=False)
        self.assertNotEqual(r.status_code, 303)
        comprador.invalidate_recordset()
        self.assertEqual(comprador.decision, "rejected")
        r = self.url_open(url, data={"csrf_token": token, "motivo": "Me equivoqué"},
                          allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        comprador.invalidate_recordset()
        self.assertEqual(comprador.decision, "pending")
        pagina = self.url_open(panel).text
        self.assertNotIn(url, pagina)
        self.assertIn("Me equivoqué", pagina)
        self.assertEqual(vendedor.decision, "pending")
