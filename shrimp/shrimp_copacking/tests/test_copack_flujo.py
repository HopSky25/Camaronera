from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import CopackCommon


@tagged("post_install", "-at_install")
class TestCopackFlujo(CopackCommon):
    """El recorrido del trabajo y los limites del cuadre."""

    def test_flujo_completo_cierra_todo(self):
        sol, orden = self.hasta_empacar()
        self.assertEqual(orden.state, "packed")
        self.assertEqual(orden.acceptance_state, "open")
        for firma in orden.acceptance_ids:
            firma.action_accept(actor=firma.partner_id)
        self.assertEqual(orden.state, "signed")
        orden.action_close()
        self.assertEqual(orden.state, "closed")
        # La solicitud tambien se cierra: si no, quedaba "adjudicada" de por vida.
        self.assertEqual(sol.state, "done")

    def test_el_cuadre_y_los_importes(self):
        _, orden = self.hasta_empacar(recibidas=40000, empacadas=39880)
        self.assertAlmostEqual(orden.difference_lb, 120, places=2)
        self.assertAlmostEqual(orden.difference_pct, 0.3, places=2)
        self.assertTrue(orden.cuadra)
        self.assertAlmostEqual(orden.service_amount, 39880 * 0.18, places=2)
        self.assertAlmostEqual(orden.platform_amount, 39880 * 0.01, places=2)

    def test_no_se_entrega_mas_de_lo_recibido(self):
        sol = self.solicitud()
        _, orden = self.adjudicar(sol)
        orden.write({"received_lb": 40000})
        orden.action_register_reception()
        with self.assertRaises(ValidationError):
            orden.write({"packed_lb": 41000})
            orden.action_register_packing()

    def test_tope_sobre_lo_acordado(self):
        """Una orden por 40.000 no puede facturar 50.000."""
        sol = self.solicitud(libras=40000)
        _, orden = self.adjudicar(sol)
        with self.assertRaises(ValidationError):
            orden.write({"received_lb": 50000})

    def test_el_acta_congela_las_cifras(self):
        _, orden = self.hasta_empacar(recibidas=40000, empacadas=39880)
        firma = orden.acceptance_ids[0]
        firma.action_accept(actor=firma.partner_id)
        self.assertAlmostEqual(firma.signed_received_lb, 40000, places=2)
        self.assertAlmostEqual(firma.signed_packed_lb, 39880, places=2)
        # Cambiar las libras despues NO altera lo que quedo firmado: esa es la
        # razon de ser de la foto.
        orden.sudo().write({"packed_lb": 30000})
        self.assertAlmostEqual(firma.signed_packed_lb, 39880, places=2)

    def test_disputa_se_reabre_y_se_rectifica(self):
        _, orden = self.hasta_empacar(recibidas=40000, empacadas=30000)
        cliente = orden.acceptance_ids.filtered(lambda f: f.role == "client")
        cliente.action_reject("Faltan libras", actor=self.cli)
        self.assertEqual(orden.acceptance_state, "disputed")
        self.assertFalse(orden.es_facturable, "en disputa no se factura")

        orden.action_reabrir_acta("Se recuenta el lote", actor=self.maq)
        self.assertEqual(orden.state, "received")
        self.assertEqual(orden.acceptance_state, "na")

        orden.write({"packed_lb": 39900})
        orden.action_register_packing()
        for firma in orden.acceptance_ids:
            firma.action_accept(actor=firma.partner_id)
        self.assertEqual(orden.state, "signed")
        self.assertTrue(orden.es_facturable)

        # Las firmas de la ronda anterior se archivan, no se borran: son la
        # prueba de que hubo una discusion.
        archivadas = self.env["shrimp.copack.acceptance"].with_context(
            active_test=False).search([("order_id", "=", orden.id), ("active", "=", False)])
        self.assertEqual(len(archivadas), 2)

    def test_reabrir_exige_motivo(self):
        _, orden = self.hasta_empacar(empacadas=30000)
        orden.acceptance_ids.filtered(lambda f: f.role == "client").action_reject(
            "No cuadra", actor=self.cli)
        with self.assertRaises(ValidationError):
            orden.action_reabrir_acta("", actor=self.maq)

    def test_cancelar_solicitud_cancela_la_orden(self):
        sol = self.solicitud()
        _, orden = self.adjudicar(sol)
        sol.action_cancel()
        self.assertEqual(sol.state, "cancelled")
        self.assertEqual(orden.state, "cancelled",
                         "una solicitud cancelada no puede dejar viva su orden")

    def test_no_se_cancela_una_solicitud_ya_empacada(self):
        sol, _ = self.hasta_empacar()
        with self.assertRaises(ValidationError):
            sol.action_cancel()

    def test_orden_cancelada_no_resucita(self):
        """Firmar una orden cancelada la pasaba a signed y acababa cobrada.

        Desde 19.0.1.1.0 una orden EMPACADA no se cancela (M5): el cliente no
        puede librarse así de pagar un trabajo hecho. El camino es la disputa
        y la reapertura del acta; una vez reabierta (vuelve a "recibida") sí
        se cancela, y entonces no debe quedar nada vivo ni facturable."""
        _, orden = self.hasta_empacar()
        with self.assertRaises(ValidationError):
            orden.action_cancel()
        self.assertEqual(orden.state, "packed")
        cliente = orden.acceptance_ids.filtered(lambda f: f.role == "client" and f.active)
        cliente.action_reject("No cuadra el peso", actor=self.cli)
        self.assertEqual(orden.acceptance_state, "disputed")
        orden.action_reabrir_acta("Rectificar el empaque", actor=self.cli)
        self.assertEqual(orden.state, "received")
        orden.action_cancel()
        self.assertEqual(orden.acceptance_state, "na")
        self.assertFalse(orden.acceptance_ids.filtered(lambda f: f.decision == "pending"))
        self.assertEqual(orden.state, "cancelled")
        self.assertFalse(orden.es_facturable)

    def test_lote_minimo_de_la_planta(self):
        with self.assertRaises(ValidationError):
            self.solicitud(libras=1000)      # el minimo declarado son 5000

    def test_retirar_una_oferta(self):
        sol = self.solicitud()
        oferta = self.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": self.maq.id,
            "rate_per_lb": 0.2, "capacity_lb": 40000,
            "available_from": sol.needed_from, "available_to": sol.needed_to,
        })
        oferta.action_withdraw(actor=self.maq)
        self.assertEqual(oferta.state, "withdrawn")
