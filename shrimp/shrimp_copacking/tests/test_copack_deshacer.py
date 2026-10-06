"""Deshacer la firma del acta de empaque (firmas de dos partes).

* Se deshace la firma propia mientras el acta siga abierta o en disputa.
* Deshacer el único «no conforme» saca el acta de la disputa.
* Con las dos conformes el acta se cierra y se registra la comisión: ya no.
* Una firma archivada por la reapertura tampoco.
"""
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, csrf_de

from .common import CopackCommon


@tagged("post_install", "-at_install")
class TestCopackDeshacer(CopackCommon):

    def _firmas(self, orden):
        return (orden.acceptance_ids.filtered(lambda f: f.role == "client"),
                orden.acceptance_ids.filtered(lambda f: f.role == "copacker"))

    def test_deshacer_conforme(self):
        _sol, orden = self.hasta_empacar()
        cli, maq = self._firmas(orden)
        cli.sudo().action_accept(actor=self.cli)
        self.assertTrue(cli.signoff_can_undo(actor=self.cli))
        cli.sudo().action_signoff_undo(reason="Firmé antes de revisar", actor=self.cli)
        self.assertEqual(cli.decision, "pending")
        self.assertFalse(cli.decided_by_uid)
        self.assertFalse(cli.signed_packed_lb)
        self.assertEqual(orden.acceptance_state, "open")
        eventos = self.env["shrimp.signoff.event"].history_for(orden)
        self.assertEqual(sorted(eventos.mapped("kind")), ["decision", "undo"])
        self.assertIn("Firmé antes de revisar", " ".join(orden.message_ids.mapped("body")))

    def test_deshacer_no_conforme_saca_de_la_disputa(self):
        _sol, orden = self.hasta_empacar()
        cli, maq = self._firmas(orden)
        cli.sudo().action_reject("No cuadran las cajas", actor=self.cli)
        self.assertEqual(orden.acceptance_state, "disputed")
        maq.sudo().action_accept(actor=self.maq)
        self.assertEqual(orden.acceptance_state, "disputed")
        # La disputa no es definitiva: el cliente rectifica su firma.
        cli.sudo().action_signoff_undo(actor=self.cli)
        self.assertEqual(orden.acceptance_state, "open")
        self.assertEqual(orden.state, "packed")
        self.assertFalse(orden.charge_ids)
        cli.sudo().action_accept(actor=self.cli)
        self.assertEqual(orden.acceptance_state, "closed")
        self.assertEqual(orden.state, "signed")

    def test_no_se_deshace_el_acta_cerrada(self):
        _sol, orden = self.hasta_empacar()
        cli, maq = self._firmas(orden)
        cli.sudo().action_accept(actor=self.cli)
        maq.sudo().action_accept(actor=self.maq)
        self.assertEqual(orden.state, "signed")
        for firma, socio in ((cli, self.cli), (maq, self.maq)):
            self.assertFalse(firma.signoff_can_undo(actor=socio))
            with self.assertRaises(UserError):
                firma.sudo().action_signoff_undo(actor=socio)
        orden.action_close()
        with self.assertRaises(UserError):
            cli.sudo().action_signoff_undo(actor=self.cli)

    def test_no_se_deshace_la_firma_de_un_acta_reabierta(self):
        _sol, orden = self.hasta_empacar()
        cli, _maq = self._firmas(orden)
        cli.sudo().action_reject("No cuadra", actor=self.cli)
        orden.action_reabrir_acta("Se rectifica", actor=self.maq)
        self.assertFalse(cli.active)
        with self.assertRaises(UserError):
            cli.sudo().action_signoff_undo(actor=self.cli)

    def test_no_se_deshace_la_firma_ajena(self):
        _sol, orden = self.hasta_empacar()
        cli, maq = self._firmas(orden)
        cli.sudo().action_accept(actor=self.cli)
        with self.assertRaises(AccessError):
            cli.sudo().action_signoff_undo(actor=self.maq)
        with self.assertRaises(AccessError):
            cli.sudo().action_signoff_undo(actor=self.otro_cli)
        self.assertEqual(cli.decision, "accepted")

    def test_aviso_al_maquilador(self):
        _sol, orden = self.hasta_empacar()
        cli, _maq = self._firmas(orden)
        cli.sudo().action_accept(actor=self.cli)
        enviados = []

        def falso(registro, xmlid, email_to, ctx=None):
            enviados.append(email_to)
            return False

        with patch.object(type(self.env["shrimp.signoff.event"]), "_send_template", falso):
            cli.sudo().action_signoff_undo(actor=self.cli)
        self.assertEqual(enviados, [self.maq.email])
        self.assertIn("No se pudo enviar por correo el aviso", " ".join(
            orden.message_ids.mapped("body")))


@tagged("post_install", "-at_install")
class TestCopackDeshacerPortal(CopackCommon, HttpCase):

    def test_portal_deshacer_firma(self):
        self.u_cli.password = CLAVE
        self.u_maq.password = CLAVE
        _sol, orden = self.hasta_empacar()
        cli = orden.acceptance_ids.filtered(lambda f: f.role == "client")
        cli.sudo().action_reject("Por error", actor=self.cli)
        self.env.flush_all()
        pagina_url = "/marketplace/copacking/orders/%s" % orden.uuid_ref
        url = pagina_url + "/undo-signature"
        # El maquilador no firmó: no tiene nada que deshacer, y no toca la del cliente.
        self.authenticate(self.u_maq.login, CLAVE)
        pagina = self.url_open(pagina_url).text
        self.assertNotIn(url, pagina)
        self.assertIn("Historial de decisiones", pagina)
        self.url_open(url, data={"csrf_token": csrf_de(pagina)})
        cli.invalidate_recordset()
        self.assertEqual(cli.decision, "rejected")
        self.authenticate(self.u_cli.login, CLAVE)
        pagina = self.url_open(pagina_url).text
        self.assertIn(url, pagina)
        r = self.url_open(url, data={"csrf_token": csrf_de(pagina), "motivo": "Me equivoqué"},
                          allow_redirects=False)
        self.assertIn("deshecha", r.headers.get("Location", ""))
        cli.invalidate_recordset()
        orden.invalidate_recordset()
        self.assertEqual(cli.decision, "pending")
        self.assertEqual(orden.acceptance_state, "open")
