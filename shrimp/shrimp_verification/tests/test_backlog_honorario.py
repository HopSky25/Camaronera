"""Backlog de cobros (decisión del usuario, 2026-10-14): el honorario de una
compra cancelada solo se factura si la verificación se realizó."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestBacklogHonorario(TransactionCase):

    def _cobro(self, tx_state, ver_vals):
        tx = self.env["shrimp.transaction"].new({"state": tx_state})
        ver = self.env["shrimp.verification"].new(ver_vals) if ver_vals is not None else False
        return self.env["shrimp.charge"].new({
            "charge_type": "verification_fee", "transaction_id": tx,
            "verification_id": ver, "amount": 300.0})

    def test_sin_verificacion_realizada_se_anula(self):
        self.assertTrue(self._cobro("cancel", {"state": "assigned"})._shrimp_backlog_should_cancel())
        self.assertTrue(self._cobro("cancel", None)._shrimp_backlog_should_cancel())

    def test_con_visita_o_veredicto_se_factura(self):
        for vals in ({"state": "rejected"}, {"state": "approved"},
                     {"state": "cancelled", "field_start_date": "2026-05-01 10:00:00"}):
            self.assertFalse(self._cobro("cancel", vals)._shrimp_backlog_should_cancel(), vals)

    def test_compra_vigente_no_se_toca(self):
        self.assertFalse(self._cobro("confirmed", {"state": "assigned"})._shrimp_backlog_should_cancel())
