from datetime import date, timedelta

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import CopackCommon


@tagged("post_install", "-at_install")
class TestCopackSeguridad(CopackCommon):
    """Los ataques que en su dia funcionaron.

    Cada uno de estos tests corresponde a un agujero real que tuvo el modulo.
    La causa comun era que una ir.rule sin perm_* declarados vale para las
    cuatro operaciones, de modo que el dominio de LECTURA daba tambien
    escritura. Si alguno de estos vuelve a pasar, es que se reintrodujo.
    """

    def tarifa(self, destinatarios, precio=0.19):
        t = self.env["shrimp.copack.tariff"].create({
            "name": "Tarifa", "copacker_partner_id": self.maq.id,
            "valid_from": date.today() - timedelta(days=1), "open_ended": True,
            "recipient_ids": [(6, 0, [d.id for d in destinatarios])],
        })
        self.env["shrimp.copack.tariff.line"].create({
            "tariff_id": t.id, "presentation": "entero",
            "pack_format": "master 5 lb", "from_lb": 0, "rate_per_lb": precio,
        })
        t.action_publish()
        return t

    # ---------------- confidencialidad de la tarifa ----------------
    def test_la_tarifa_solo_la_ve_su_destinatario(self):
        mia = self.tarifa([self.cli], 0.19)
        ajena = self.tarifa([self.otro_cli], 0.14)
        visibles = self.env["shrimp.copack.tariff"].with_user(self.u_cli).search([])
        self.assertIn(mia, visibles)
        self.assertNotIn(ajena, visibles,
                         "un cliente no puede ver lo que la planta le cobra a otro")

    def test_el_destinatario_no_reenvia_la_tarifa(self):
        t = self.tarifa([self.cli])
        with self.assertRaises(AccessError):
            t.with_user(self.u_cli).write({"recipient_ids": [(4, self.otro_cli.id)]})

    def test_el_destinatario_no_reescribe_ni_borra_renglones(self):
        t = self.tarifa([self.cli])
        with self.assertRaises(AccessError):
            t.line_ids.with_user(self.u_cli).write({"rate_per_lb": 0.01})
        with self.assertRaises(AccessError):
            t.line_ids.with_user(self.u_cli).unlink()

    # ---------------- el acta ----------------
    def test_una_parte_no_firma_por_la_otra(self):
        _, orden = self.hasta_empacar()
        del_cliente = orden.acceptance_ids.filtered(lambda f: f.role == "client")
        with self.assertRaises(AccessError):
            del_cliente.with_user(self.u_maq).action_accept()

    def test_no_se_escribe_la_firma_ajena_por_rpc(self):
        _, orden = self.hasta_empacar()
        del_cliente = orden.acceptance_ids.filtered(lambda f: f.role == "client")
        with self.assertRaises(AccessError):
            del_cliente.with_user(self.u_maq).write({"decision": "accepted"})

    def test_firmar_exige_ser_el_titular(self):
        _, orden = self.hasta_empacar()
        del_cliente = orden.acceptance_ids.filtered(lambda f: f.role == "client")
        with self.assertRaises(AccessError):
            del_cliente.sudo().action_accept(actor=self.maq)

    # ---------------- ordenes y ofertas ----------------
    def test_el_maquilador_no_se_quita_la_comision(self):
        _, orden = self.hasta_empacar()
        with self.assertRaises(AccessError):
            orden.with_user(self.u_maq).write({"platform_rate_per_lb": 0})

    def test_el_cliente_no_cambia_la_tarifa_de_una_oferta(self):
        sol = self.solicitud()
        oferta, _ = self.adjudicar(sol)
        with self.assertRaises(AccessError):
            oferta.with_user(self.u_cli).write({"rate_per_lb": 0.001})

    def test_solo_el_cliente_acepta_la_oferta(self):
        sol = self.solicitud()
        oferta = self.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": self.maq.id,
            "rate_per_lb": 0.2, "capacity_lb": 40000,
            "available_from": sol.needed_from, "available_to": sol.needed_to,
        })
        with self.assertRaises(AccessError):
            oferta.action_accept(actor=self.maq)

    def test_el_maquilador_no_se_autoadjudica_solicitudes_abiertas(self):
        abierta = self.solicitud(dirigida=False)
        with self.assertRaises(AccessError):
            abierta.with_user(self.u_maq).write({"copacker_partner_id": self.maq.id})

    # ---------------- contactos ----------------
    def test_el_maquilador_solo_ve_a_sus_contrapartes(self):
        sol = self.solicitud()
        self.adjudicar(sol)
        vistos = self.env["res.partner"].with_user(self.u_maq).search(
            [("shrimp_user_type", "=", "camaronera")])
        self.assertIn(self.cli, vistos, "tiene que ver a su cliente")
        self.assertNotIn(self.otro_cli, vistos,
                         "no tiene por que ver camaroneras con las que no trabaja")
