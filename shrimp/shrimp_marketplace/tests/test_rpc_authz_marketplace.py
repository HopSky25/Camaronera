"""Autorización de los métodos públicos del marketplace llamados por RPC.

En Odoo 19 /web/dataset/call_kw es auth="user": cualquier usuario del portal
llama a los métodos públicos sobre los ids que quiera, con los argumentos
(JSON) y el contexto que quiera. Aquí se llama como lo haría ese RPC:
`call_kw` o `.with_user(portal)`, NUNCA en sudo. El camino legítimo (el
controlador o la API, que llaman en sudo tras comprobar quién es quién) se
prueba al lado para ver que sigue funcionando.
"""
from datetime import timedelta

from odoo.exceptions import AccessError
from odoo.service.model import get_public_method
from odoo.tests import tagged

from .common import PNG_1PX, producto
from .test_recepcion import subusuario
from .test_retraso_entrega import RetrasoCommon

# Métodos que solo llama el servidor: privados (@api.private).
PRIVADOS = [
    ("shrimp.charge", "action_cancel_charge"),
    ("shrimp.charge", "register_for_transaction"),
    ("shrimp.product", "execute_purchase_flow"),
    ("shrimp.product", "shrimp_photos"),
    ("shrimp.product", "shrimp_photo_by_token"),
    ("shrimp.product", "shrimp_add_photo"),
    ("shrimp.product", "shrimp_remove_photo"),
    ("shrimp.product", "shrimp_reorder_photos"),
    ("shrimp.product", "shrimp_link_account_certificate"),
    ("shrimp.product", "shrimp_add_own_certificate"),
    ("shrimp.product", "shrimp_edit_own_certificate"),
    ("shrimp.product", "shrimp_remove_certificate_lines"),
    ("shrimp.product.quality", "action_register_sample"),
    ("res.partner", "shrimp_add_account_certificate"),
    ("res.partner", "shrimp_renew_account_certificate"),
    ("shrimp.export", "shrimp_register"),
    ("shrimp.settings", "get_float"),
    ("shrimp.settings", "get_int"),
    ("shrimp.settings", "get_bool"),
    ("shrimp.transaction", "shrimp_receipt_party"),
    ("shrimp.transaction", "shrimp_receipt_service_charges"),
    ("shrimp.transaction", "shrimp_receipt_amounts"),
    ("shrimp.transaction", "shrimp_receipt_verification"),
    ("shrimp.transaction", "shrimp_receipt_public_url"),
    ("shrimp.transaction", "shrimp_receipt_qr_uri"),
]


@tagged("post_install", "-at_install")
class TestRpcAuthzMarketplace(RetrasoCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref("shrimp_marketplace.uom_millar").sudo().commission_cents = 5.0
        Member = cls.env["shrimp.account.member"].sudo()
        Member._shrimp_ensure(cls.s["u_sem"], cls.s["sem"], is_admin=True)
        Member._shrimp_ensure(cls.s["u_lab"], cls.s["lab"], is_admin=True)
        # Subusuarios que solo pueden VER (sin «vender» ni «comprar»).
        cls.sub_sem = subusuario(cls.env, cls.s["sem"], "sem.ver.rpc", {"semillero": {"view"}})
        cls.sub_lab = subusuario(cls.env, cls.s["lab"], "lab.ver.rpc", {"laboratorio": {"view"}})
        cls.ajeno = cls.s["u_lab2"]

    def rpc(self, user, model, method, ids, *args, **kwargs):
        """Lo que hace /web/dataset/call_kw (o call_button) con ese usuario,
        SIN la puerta general de ir.http (shrimp_rpc_gate), que a un usuario
        del portal le niega cualquier método de shrimp.*: así se prueba que
        cada método se protege solo."""
        publico = getattr(get_public_method, "__wrapped__", get_public_method)
        publico(self.env[model], method)
        return getattr(self.env[model].with_user(user).browse(ids), method)(*args, **kwargs)

    # ------------------------------------------------------------------
    def test_privados_no_se_llaman_por_rpc(self):
        for model, method in PRIVADOS:
            with self.assertRaises(AccessError, msg="%s.%s" % (model, method)):
                get_public_method(self.env[model], method)

    def test_anular_cobro_solo_el_servidor(self):
        tx = self.comprar("sem", "lab")
        cobro = tx.charge_ids.filtered(lambda c: c.charge_type == "commission")
        self.assertEqual(len(cobro), 1)
        estado = cobro.state
        for user in (self.ajeno, self.s["u_sem"], self.s["u_lab"]):
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.charge", "action_cancel_charge", cobro.ids, "x")
        self.assertEqual(cobro.state, estado)
        # El servidor (cancelaciones, entrega parcial...) sí.
        cobro.sudo().action_cancel_charge("Prueba")
        self.assertIn(cobro.state, ("cancelled", "to_credit", "credited"))

    def test_fotos_de_lote_ajeno_por_rpc(self):
        lote = producto(self.env, self.s["sem"], nombre="Lote fotos rpc")
        with self.assertRaises(AccessError):
            self.rpc(self.ajeno, "shrimp.product", "shrimp_add_photo", lote.ids,
                     "PHN2Zy8+", "image/svg+xml", "x.svg")
        self.assertFalse(lote.sudo().photo_attachment_ids)
        # El portal y la API (en sudo, tras comprobar el dueño) sí.
        lote.sudo().shrimp_add_photo(PNG_1PX, "image/png", "foto.png")
        self.assertEqual(len(lote.sudo().photo_attachment_ids), 1)

    def test_rechazo_automatico_solo_del_cron(self):
        entrega = self.hoy + timedelta(days=4)
        tx = self.comprar("sem", "lab", entrega=entrega)
        self.avisar(tx, entrega + timedelta(days=8))
        aviso = self.comprador(tx).action_delay_buyer_counter(
            entrega + timedelta(days=2), actor=self.s["lab"])
        self.assertEqual(aviso.state, "countered")
        # El comprador (o un ajeno) no da por rechazada su contrapropuesta
        # con auto=True: el vendedor no respondió.
        for user in (self.s["u_lab"], self.ajeno, self.sub_sem):
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.transaction", "action_delay_seller_reject", tx.ids,
                         auto=True)
        self.assertEqual(aviso.state, "countered")
        self.assertFalse(tx.shrimp_delay_cancel_cause())
        # El cron (sudo) sí.
        tx.sudo().action_delay_seller_reject(auto=True)
        self.assertEqual(aviso.state, "rejected")
        self.assertTrue(aviso.rejected_auto)

    def test_declarar_produccion_exige_vender(self):
        sem = self.s["sem"]
        lote = producto(self.env, sem, nombre="Lote produccion rpc")
        for user, kw in ((self.sub_sem, {}), (self.ajeno, {}), (self.s["u_lab"], {"actor": sem.id})):
            with self.assertRaises(AccessError, msg=user.login):
                lote.with_user(user).action_declare_production(50.0, **kw)
        self.assertEqual(lote.available_qty, 100.0)
        # Como el controlador de Mis lotes (sudo, con el socio de la petición).
        lote.with_user(self.s["u_sem"]).sudo().action_declare_production(80.0, actor=sem)
        lote.invalidate_recordset()
        self.assertEqual(lote.available_qty, 80.0)

    def test_factura_del_vendedor_sin_adjunto_ajeno(self):
        tx = self.comprar("sem", "lab")
        Att = self.env["ir.attachment"].sudo()
        ajeno = Att.create({"name": "privado.pdf", "raw": b"%PDF-1.4", "res_model": "res.partner",
                            "res_id": self.s["lab2"].id})
        with self.assertRaises(AccessError):
            self.rpc(self.ajeno, "shrimp.transaction", "action_register_seller_invoice", tx.ids,
                     {"number": "001-001-000000001"}, actor=self.s["sem"].id)
        with self.assertRaises(AccessError):
            self.rpc(self.sub_lab, "shrimp.transaction", "action_register_seller_invoice", tx.ids,
                     {"number": "001-001-000000001"})
        # El vendedor por RPC: se registra, pero sin colgar el adjunto ajeno.
        self.rpc(self.s["u_sem"], "shrimp.transaction", "action_register_seller_invoice", tx.ids,
                 {"number": "001-001-000000002", "attachment_id": ajeno.id})
        tx.invalidate_recordset()
        self.assertEqual(tx.seller_invoice_number, "001-001-000000002")
        self.assertFalse(tx.seller_invoice_attachment_id)
        # El portal (sudo) con el adjunto que acaba de crear y validar.
        propio = Att.create({"name": "factura.pdf", "raw": b"%PDF-1.4",
                             "res_model": "shrimp.transaction", "res_id": tx.id})
        tx.with_user(self.s["u_sem"]).sudo().action_register_seller_invoice(
            {"number": "001-001-000000003", "attachment_id": propio.id}, actor=self.s["sem"])
        self.assertEqual(tx.seller_invoice_attachment_id, propio)

    def test_cancelacion_de_mutuo_acuerdo_y_anulacion_de_strike(self):
        sem, lab = self.s["sem"], self.s["lab"]
        tx = self.comprar("sem", "lab")
        for user in (self.ajeno, self.sub_lab):
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.transaction", "action_cancel_request", tx.ids, "x",
                         actor=sem.id)
        self.assertFalse(tx.shrimp_cancel_request_open())
        tx.with_user(self.s["u_lab"]).sudo().action_cancel_request("Ya no la necesito", actor=lab)
        self.assertTrue(tx.shrimp_cancel_request_open())
        # Anular un strike: lo pide solo el vendedor (con «vender»).
        strike = self.Strike.sudo()._shrimp_add_strike(tx, "seller_cancel")
        for user in (self.s["u_lab"], self.ajeno, self.sub_sem):
            with self.assertRaises(AccessError, msg=user.login):
                self.rpc(user, "shrimp.seller.strike", "action_request_annulment", strike.ids,
                         "x", actor=sem.id)
        self.assertFalse(strike.shrimp_appeal_pending())
        strike.with_user(self.s["u_sem"]).sudo().action_request_annulment("Mortalidad", actor=sem)
        self.assertTrue(strike.shrimp_appeal_pending())
