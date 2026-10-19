"""Stock y reservas sin dueño (auditoría de procesos, hueco #9).

* Archivar/eliminar se bloquea con operaciones abiertas (todos los canales:
  la regla vive en shrimp.product.write).
* Al retirar, las solicitudes de chequeo abiertas se cancelan y liberan.
* Eliminar da de baja el stock con un movimiento «Baja» trazable.
* Un producto eliminado no se reactiva (portal ni modelo); uno archivado sí.
* Las solicitudes de chequeo tienen plazo y vencen solas.
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from .common import CLAVE, crear_socios, csrf_de, producto


@tagged("post_install", "-at_install")
class TestStockSinDueno(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "ssd")
        cls.lab, cls.cam = cls.s["lab"], cls.s["cam"]

    def _lote(self, nombre="Larva ssd"):
        return producto(self.env, self.lab, nombre=nombre, initial_qty=100.0)

    def _chequeo(self, p, qty=10.0):
        # Flujo retirado (D-28): solo se cargan como histórico.
        return self.env["shrimp.check.request"].with_context(
            shrimp_legacy_check_import=True).create({
            "product_id": p.id, "seller_partner_id": self.lab.id,
            "buyer_partner_id": self.cam.id, "qty": qty})

    # ------------------------------------------------------------------
    # Bloqueos
    # ------------------------------------------------------------------
    def test_no_se_retira_con_compra_abierta(self):
        p = self._lote()
        tx = p.execute_purchase_flow(self.cam, 10.0)["transaction"]
        tx.sudo().write({"state": "confirmed"})
        for vals in ({"active": False, "state": "draft"}, {"active": False, "state": "cancel"},
                     {"state": "cancel"}):
            with self.assertRaises(UserError) as e:
                p.write(vals)
            self.assertIn("compra(s) en curso", str(e.exception))
        self.assertTrue(p.active)
        self.assertNotEqual(p.state, "cancel")
        # Con la compra cerrada, sí.
        tx.sudo().write({"state": "done"})
        p.write({"active": False, "state": "draft"})
        self.assertFalse(p.active)

    def test_retirar_cancela_chequeos_y_libera(self):
        p = self._lote()
        chk = self._chequeo(p, 30.0)
        self.assertEqual(p.reserved_qty, 30.0)
        self.assertEqual(p.available_qty, 70.0)
        p.write({"active": False, "state": "draft"})
        self.assertEqual(chk.state, "cancelled")
        self.assertEqual(p.reserved_qty, 0.0)
        self.assertIn("retiró el producto", " ".join(chk.message_ids.mapped("body")))
        # Archivado conserva el stock y se reactiva.
        self.assertEqual(p.available_qty, 100.0)
        self.assertTrue(p.shrimp_can_reactivate())
        p.write({"active": True})
        self.assertTrue(p.active)

    # ------------------------------------------------------------------
    # Eliminar: baja de stock trazable y sin reactivación
    # ------------------------------------------------------------------
    def test_eliminar_da_de_baja_el_stock(self):
        p = self._lote()
        chk = self._chequeo(p, 5.0)
        p.write({"active": False, "state": "cancel"})
        self.assertEqual(chk.state, "cancelled")
        lotes = self.env["shrimp.stock.lot"].search([("product_id", "=", p.id)])
        self.assertTrue(lotes)
        self.assertTrue(all(l.state == "consumed" and l.available_qty == 0 for l in lotes))
        baja = self.env["shrimp.stock.move"].search([
            ("product_id", "=", p.id), ("move_type", "=", "write_off")])
        self.assertEqual(len(baja), 1)
        self.assertEqual(baja.qty, 100.0)
        self.assertEqual((baja.qty_before, baja.qty_after), (100.0, 0.0))
        self.assertEqual(p.available_qty, 0.0)
        self.assertEqual(p.state, "cancel")

    def test_eliminado_no_se_reactiva(self):
        p = self._lote()
        p.write({"active": False, "state": "cancel"})
        self.assertFalse(p.shrimp_can_reactivate())
        with self.assertRaises(UserError):
            p.write({"active": True})
        p.invalidate_recordset()
        self.assertFalse(p.active)

    # ------------------------------------------------------------------
    # Solicitudes de chequeo: flujo retirado (D-28)
    # ------------------------------------------------------------------
    def test_chequeo_retirado_no_se_crea_ni_se_resuelve(self):
        p = self._lote()
        with self.assertRaises(UserError):
            self.env["shrimp.check.request"].create({
                "product_id": p.id, "seller_partner_id": self.lab.id,
                "buyer_partner_id": self.cam.id, "qty": 1.0})
        chk = self._chequeo(p, 10.0)
        for accion in (chk.action_approve, chk.action_reject, chk.action_cancel,
                       chk.action_mark_under_review):
            with self.assertRaises(UserError):
                accion()
        self.assertEqual(chk.state, "requested")
        self.assertEqual(self.env["shrimp.check.request"]._cron_expire(), 0)

    def test_migracion_cancela_abiertos_y_libera(self):
        p = self._lote()
        abierto = self._chequeo(p, 40.0)
        cerrado = self._chequeo(p, 5.0)
        cerrado.sudo().write({"state": "rejected"})
        self.assertEqual(p.reserved_qty, 40.0)
        hechas = self.env["shrimp.check.request"]._shrimp_retire_open()
        self.assertEqual(hechas, abierto)
        self.assertEqual(abierto.state, "cancelled")
        self.assertEqual(cerrado.state, "rejected")
        self.assertEqual(p.reserved_qty, 0.0)
        self.assertEqual(p.available_qty, 100.0)
        cuerpo = " ".join(abierto.message_ids.mapped("body"))
        self.assertIn("retiró este flujo", cuerpo)
        aviso = abierto.message_ids.filtered(lambda m: "retiró este flujo" in (m.body or ""))
        self.assertTrue({self.lab, self.cam} <= set(aviso.partner_ids) or not (
            self.lab.email and self.cam.email))


@tagged("post_install", "-at_install")
class TestStockSinDuenoPortal(HttpCase):

    def setUp(self):
        super().setUp()
        self.s = crear_socios(self.env, "ssp")
        self.lab = self.s["lab"]
        self.p = producto(self.env, self.lab, nombre="Larva ssp")

    def _post(self, ruta):
        pagina = self.url_open("/marketplace/products")
        token = csrf_de(pagina.text)
        return self.url_open(ruta, data={"csrf_token": token}, allow_redirects=False)

    def test_portal_no_reactiva_eliminado(self):
        self.p.write({"active": False, "state": "cancel"})
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self._post("/marketplace/products/%s/reactivate" % self.p.uuid_ref)
        self.assertIn(r.status_code, (302, 303))
        self.assertIn("error=retire", r.headers.get("Location", ""))
        self.p.invalidate_recordset()
        self.assertFalse(self.p.active)
        self.assertEqual(self.p.state, "cancel")

    def test_portal_eliminar_bloqueado_muestra_motivo(self):
        tx = self.p.execute_purchase_flow(self.s["cam"], 10.0)["transaction"]
        tx.sudo().write({"state": "confirmed"})
        self.authenticate(self.s["u_lab"].login, CLAVE)
        r = self._post("/marketplace/products/%s/delete" % self.p.uuid_ref)
        self.assertIn("error=retire", r.headers.get("Location", ""))
        self.p.invalidate_recordset()
        self.assertTrue(self.p.active)
        pagina = self.url_open(r.headers["Location"])
        self.assertIn("compra(s) en curso", pagina.text)
