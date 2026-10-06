import base64
import io

from werkzeug.datastructures import FileStorage

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_user_registry.controllers.main import (
    IMAGE_MIMETYPES, MAX_FILE_SIZE, read_upload)

from .common import PDF_MIN, PNG_1PX, ShrimpSecurityCommon, producto


@tagged("post_install", "-at_install")
class TestSeguridadModelo(ShrimpSecurityCommon):
    """C1, A5, M2, M3, M7, B1 a nivel de modelo (lo que se puede hacer por RPC)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tx = cls.larva.execute_purchase_flow(cls.s["lab"], 10.0)["transaction"]

    # ---------------- C1: transacciones ----------------
    def test_portal_no_escribe_ni_crea_transacciones(self):
        tx_portal = self.tx.with_user(self.s["u_lab"])
        # Puede leer la suya...
        self.assertEqual(tx_portal.price_unit, 10.0)
        # ...pero no cambiarle el precio, el estado ni las partes.
        for vals in ({"price_unit": 0.01}, {"state": "done"},
                     {"buyer_partner_id": self.s["lab2"].id}, {"location": "x"}):
            with self.assertRaises(AccessError):
                tx_portal.write(vals)
        with self.assertRaises(AccessError):
            self.env["shrimp.transaction"].with_user(self.s["u_lab"]).create({
                "transaction_type": "semillero_to_laboratorio",
                "product_id": self.larva.id, "seller_partner_id": self.s["sem"].id,
                "buyer_partner_id": self.s["lab"].id, "transaction_qty": 1, "sold_qty": 1,
            })
        # Un tercero ni siquiera la ve.
        with self.assertRaises(AccessError):
            self.tx.with_user(self.s["u_lab2"]).read(["price_unit"])

    def test_write_defensivo_fuera_de_sudo(self):
        """Aunque alguien volviera a abrir la ACL, los campos del trato no
        se mueven fuera de sudo para un usuario de portal."""
        tx = self.tx.with_user(self.s["u_lab"])
        self.assertTrue(tx._shrimp_untrusted_env())
        self.assertFalse(tx.sudo()._shrimp_untrusted_env())

    # ---------------- A5: productos ----------------
    def test_portal_no_escribe_ni_crea_productos(self):
        with self.assertRaises(AccessError):
            self.larva.with_user(self.s["u_sem"]).write({"price": 0.01})
        with self.assertRaises(AccessError):
            self.env["shrimp.product"].with_user(self.s["u_sem"]).create({
                "name": "x", "seller_partner_id": self.s["sem"].id, "initial_qty": 1, "price": 1})
        # Ni siquiera en sudo a nombre de OTRO vendedor.
        with self.assertRaises(AccessError):
            self.env["shrimp.product"].with_user(self.s["u_sem"]).sudo().create({
                "name": "x", "seller_partner_id": self.s["lab"].id,
                "seller_role": "laboratorio", "initial_qty": 1, "price": 1})
        # Un rol que no vende (la camaronera sí vende; un portal sin tipo no).
        nadie = self.env["res.users"].create({
            "name": "Sin rol", "login": "sinrol.sec", "password": "Clave-de-prueba-123",
            "group_ids": [(6, 0, [self.env.ref("base.group_portal").id])]})
        with self.assertRaises(AccessError):
            self.env["shrimp.product"].with_user(nadie).sudo().create({
                "name": "x", "seller_partner_id": nadie.partner_id.id, "initial_qty": 1, "price": 1})

    def test_publicar_valida_en_el_modelo(self):
        # Sin stock no se publica, aunque se escriba el estado directamente.
        borrador = producto(self.env, self.s["sem"], nombre="Sin stock", publicado=False)
        borrador.stock_lot_ids.write({"available_qty": 0.0, "state": "consumed"})
        borrador._compute_available_qty()
        with self.assertRaises(ValidationError):
            borrador.action_publish()
        with self.assertRaises(ValidationError):
            borrador.write({"state": "published"})
        # El camarón de engorde exige presentación y talla (la regla vive en
        # el modelo, no solo en el controlador).
        engorde = self.env["shrimp.product"].new({
            "name": "Engorde", "seller_partner_id": self.s["cam"].id, "active": True,
            "stage_id": self.env.ref("shrimp_marketplace.shrimp_stage_engorde").id})
        self.assertTrue(engorde._shrimp_publish_problems(check_stock=False))
        # Un vendedor no publica lo de otro, ni siquiera en sudo.
        otro = producto(self.env, self.s["sem"], nombre="Borrador ajeno", publicado=False)
        with self.assertRaises(AccessError):
            otro.with_user(self.s["u_lab"]).sudo().action_publish()
        otro.with_user(self.s["u_sem"]).sudo().action_publish()
        self.assertEqual(otro.state, "published")

    # ---------------- M2: reseñas ----------------
    def test_resena_solo_de_compra_cerrada_y_entre_partes(self):
        Review = self.env["shrimp.review"]
        Review.create({"direction": "to_seller", "seller_partner_id": self.s["sem"].id,
                       "reviewer_partner_id": self.s["lab"].id,
                       "transaction_id": self.tx.id, "rating": 5})
        # Una segunda reseña del mismo autor sobre la misma compra: no.
        with self.assertRaises(Exception), mute_logger("odoo.sql_db"):
            with self.env.cr.savepoint():
                Review.create({"direction": "to_seller", "seller_partner_id": self.s["sem"].id,
                               "reviewer_partner_id": self.s["lab"].id,
                               "transaction_id": self.tx.id, "rating": 1})
        # Un tercero no reseña una compra ajena.
        with self.assertRaises(ValidationError):
            Review.create({"direction": "to_seller", "seller_partner_id": self.s["sem"].id,
                           "reviewer_partner_id": self.s["lab2"].id,
                           "transaction_id": self.tx.id, "rating": 1})
        # Compra cancelada: no se califica.
        self.tx.sudo().write({"state": "cancel"})
        with self.assertRaises(ValidationError):
            Review.create({"direction": "to_buyer", "seller_partner_id": self.s["lab"].id,
                           "reviewer_partner_id": self.s["sem"].id,
                           "transaction_id": self.tx.id, "rating": 1})

    # ---------------- M3: certificados de producto ----------------
    def test_certificado_de_producto_nace_pendiente_y_solo_se_ve_aprobado(self):
        att = self.env["ir.attachment"].create({"name": "c.pdf", "datas": base64.b64encode(PDF_MIN)})
        cert = self.env.ref("shrimp_user_registry.shrimp_certificate_asc")
        Line = self.env["shrimp.product.certificate.line"]
        linea = Line.with_user(self.s["u_sem"]).sudo().create({
            "product_id": self.larva.id, "certificate_id": cert.id,
            "number": "N-1", "attachment_id": att.id, "status": "approved",
            "issue_date": "2026-01-01", "expiry_date": "2030-01-01"})
        self.assertEqual(linea.status, "pending", "desde el portal siempre entra pendiente")
        self.assertNotIn(linea, self.larva.shrimp_visible_certificate_lines())
        self.assertIn(linea, self.larva.shrimp_visible_certificate_lines(owner_view=True))
        linea.action_approve()
        self.assertIn(linea, self.larva.shrimp_visible_certificate_lines())

    # ---------------- M7: grupos internos ----------------
    def test_operador_no_borra_compras_y_el_administrador_si(self):
        operador = self.env["res.users"].create({
            "name": "Operador", "login": "operador.sec", "password": "Clave-de-prueba-123",
            "group_ids": [(6, 0, [self.env.ref("shrimp_marketplace.group_shrimp_user").id])]})
        interno_sin_grupo = self.env["res.users"].create({
            "name": "Contable", "login": "contable.sec", "password": "Clave-de-prueba-123",
            "group_ids": [(6, 0, [self.env.ref("base.group_user").id])]})
        with self.assertRaises(AccessError):
            self.tx.with_user(operador).unlink()
        with self.assertRaises(AccessError):
            self.tx.with_user(interno_sin_grupo).read(["name"])
        self.assertTrue(self.env.ref("base.user_admin").has_group(
            "shrimp_marketplace.group_shrimp_manager"))

    # ---------------- B1: subidas ----------------
    def test_subida_valida_contenido_y_tamano(self):
        def fs(contenido, nombre="x.png", ctype="image/png"):
            return FileStorage(stream=io.BytesIO(contenido), filename=nombre, content_type=ctype)
        contenido, mime, nombre = read_upload(fs(PNG_1PX), allowed=IMAGE_MIMETYPES)
        self.assertEqual(mime, "image/png")
        # HTML que se presenta como imagen: rechazado por contenido.
        with self.assertRaises(ValidationError):
            read_upload(fs(b"<html><script>alert(1)</script></html>"), allowed=IMAGE_MIMETYPES)
        # Un PDF no es una foto.
        with self.assertRaises(ValidationError):
            read_upload(fs(PDF_MIN, "a.pdf", "application/pdf"), allowed=IMAGE_MIMETYPES)
        # Más de 5 MB: rechazado.
        with self.assertRaises(ValidationError):
            read_upload(fs(PNG_1PX + b"0" * (MAX_FILE_SIZE + 10)))
