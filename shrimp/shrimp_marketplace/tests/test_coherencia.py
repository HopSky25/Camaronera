"""Coherencia entre módulos: matriz de capacidades, cobros únicos de la
plataforma, factura del vendedor, plataforma de cada sitio y migraciones."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import ShrimpSecurityCommon, producto

ADDONS = Path(__file__).resolve().parents[2]


def cargar_migracion(modulo, version, fase):
    """Importa un script de migración (sus nombres no son importables)."""
    ruta = ADDONS / modulo / "migrations" / version / ("%s-migrate.py" % fase)
    spec = importlib.util.spec_from_file_location(
        "mig_%s_%s_%s" % (modulo, version.replace(".", "_"), fase), ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tagged("post_install", "-at_install")
class TestCoherenciaMarketplace(ShrimpSecurityCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Comisión de 5 centavos por millar: así cada compra genera su cobro.
        cls.millar = cls.env.ref("shrimp_marketplace.uom_millar")
        cls.millar.sudo().commission_cents = 5.0
        cls.lote_lab = producto(cls.env, cls.s["lab"], nombre="Larva coh")

    # ------------------------------------------------------------------
    # Matriz de capacidades
    # ------------------------------------------------------------------
    def test_matriz_de_capacidades(self):
        P = self.env["res.partner"]
        self.assertTrue(self.s["sem"]._shrimp_can("sell_products"))
        self.assertTrue(self.s["lab"]._shrimp_can("buy_from_semillero"))
        self.assertFalse(self.s["cam"]._shrimp_can("buy_from_semillero"))
        self.assertTrue(self.s["cam"]._shrimp_can("buy_from_laboratorio"))
        self.assertIn("laboratorio", P._shrimp_types_with("buy_from_semillero"))
        self.assertNotIn("camaronera", P._shrimp_types_with("buy_from_semillero"))
        # Un contacto hijo hereda el tipo de su empresa.
        hijo = P.create({"name": "Contacto del lab", "parent_id": self.s["lab"].id})
        self.assertTrue(hijo._shrimp_can("buy_from_semillero"))
        # Una capacidad desconocida no la tiene nadie.
        self.assertFalse(self.s["lab"]._shrimp_can("capacidad_inventada"))

    def test_la_misma_regla_en_catalogo_y_restriccion(self):
        lote_sem = self.larva
        # El catálogo explica por qué no; la restricción lo impide igual.
        self.assertTrue(lote_sem.motivo_no_comprable(self.s["cam"]))
        self.assertFalse(lote_sem.motivo_no_comprable(self.s["lab"]))
        with self.assertRaises(ValidationError):
            lote_sem.execute_purchase_flow(self.s["cam"], 1.0)

    # ------------------------------------------------------------------
    # Un solo cobro de comisión por compra, y el mismo precio por todos los
    # caminos
    # ------------------------------------------------------------------
    def _comisiones(self, tx):
        return self.env["shrimp.charge"].search([
            ("transaction_id", "=", tx.id), ("charge_type", "=", "commission")])

    def test_comision_una_sola_vez_compra_directa(self):
        tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 4.0)["transaction"]
        cobros = self._comisiones(tx)
        self.assertEqual(len(cobros), 1)
        self.assertEqual(cobros.payer_partner_id, self.s["lab"])
        self.assertAlmostEqual(cobros.amount, 0.20)
        # Llamarla otra vez (compatibilidad) no duplica.
        self.env["shrimp.charge"].register_for_transaction(tx, 4.0)
        tx.action_confirm()
        self.assertEqual(len(self._comisiones(tx)), 1)

    def test_comision_una_sola_vez_por_solicitud_de_chequeo(self):
        cr = self.env["shrimp.check.request"].create({
            "product_id": self.lote_lab.id, "seller_partner_id": self.s["lab"].id,
            "buyer_partner_id": self.s["cam"].id, "qty": 3.0})
        cr.action_approve()
        self.assertEqual(cr.state, "approved")
        self.assertEqual(len(self._comisiones(cr.transaction_id)), 1)

    def test_precio_unificado(self):
        tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 1.0)["transaction"]
        self.assertEqual(tx.price_unit, self.lote_lab.price_for_partner(self.s["cam"]))

    # ------------------------------------------------------------------
    # Facturación del servicio: al momento, y con reintento si falla
    # ------------------------------------------------------------------
    def test_factura_de_servicio_al_comprar(self):
        tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 6.0)["transaction"]
        cobro = self._comisiones(tx)
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.invoice_id.state, "posted")
        self.assertEqual(cobro.invoice_id.partner_id, self.s["lab"])
        self.assertEqual(cobro.invoice_id.move_type, "out_invoice")
        # La factura es de SERVICIO: nunca de la mercadería.
        self.assertFalse(tx.invoice_id)
        self.assertEqual(cobro.sale_order_id.state, "sale")

    def test_reintento_tras_un_fallo(self):
        Charge = self.registry["shrimp.charge"]
        original = Charge._create_invoice_documents

        def falla(self_):
            raise UserError("Sin punto de emisión")

        with patch.object(Charge, "_create_invoice_documents", falla):
            tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 2.0)["transaction"]
        cobro = self._comisiones(tx)
        # La compra no se cae; el cobro queda con error visible y sin vínculos
        # a medias (ni pedido ni factura).
        self.assertEqual(tx.state, "confirmed")
        self.assertEqual(cobro.state, "error")
        self.assertIn("Sin punto de emisión", cobro.invoice_error)
        self.assertFalse(cobro.sale_order_id)
        self.assertFalse(cobro.invoice_id)
        self.assertEqual(cobro.invoice_attempts, 1)
        self.assertEqual(self.env["sale.order"].search_count(
            [("client_order_ref", "=", cobro.name)]), 0)
        # El cron lo reintenta y esta vez sale.
        with patch.object(Charge, "_create_invoice_documents", original):
            self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(cobro.state, "invoiced")
        self.assertEqual(cobro.invoice_id.state, "posted")
        self.assertEqual(cobro.invoice_attempts, 2)

    def test_anular_cobro_facturado_emite_nota_de_credito(self):
        tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 2.0)["transaction"]
        cobro = self._comisiones(tx)
        cobro.action_cancel_charge("prueba")
        self.assertEqual(cobro.state, "credited")
        self.assertEqual(cobro.refund_id.move_type, "out_refund")
        self.assertEqual(cobro.refund_id.state, "posted")

    def test_la_plataforma_no_factura_mercaderia(self):
        tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 1.0)["transaction"]
        with self.assertRaises(UserError):
            tx.action_generar_factura()

    # ------------------------------------------------------------------
    # Factura del vendedor (paso 2)
    # ------------------------------------------------------------------
    def test_registro_de_la_factura_del_vendedor(self):
        tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 1.0)["transaction"]
        # Solo el vendedor.
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_cam"]).sudo().action_register_seller_invoice(
                {"number": "001-001-000000123"}, actor=self.s["cam"])
        with self.assertRaises(ValidationError):
            tx.action_register_seller_invoice({"number": "123"}, actor=self.s["lab"])
        with self.assertRaises(ValidationError):
            tx.action_register_seller_invoice({"access_key": "12345"}, actor=self.s["lab"])
        tx.with_user(self.s["u_lab"]).sudo().action_register_seller_invoice(
            {"number": "001-001-000000123", "access_key": "1" * 49}, actor=self.s["lab"])
        self.assertTrue(tx.has_seller_invoice)
        self.assertEqual(tx.seller_invoice_number, "001-001-000000123")
        # El portal no la toca por RPC.
        with self.assertRaises(AccessError):
            tx.with_user(self.s["u_lab"]).write({"seller_invoice_number": "001-001-000000999"})

    def test_factura_del_vendedor_solo_con_compra_cerrada(self):
        tx = self.lote_lab.execute_purchase_flow(self.s["cam"], 1.0)["transaction"]
        tx.sudo().write({"state": "cancel"})
        with self.assertRaises(ValidationError):
            tx.action_register_seller_invoice({"number": "001-001-000000001"}, actor=self.s["lab"])

    # ------------------------------------------------------------------
    # Perfil común: los alias escriben el campo común
    # ------------------------------------------------------------------
    def test_alias_del_perfil(self):
        lab = self.s["lab"]
        self.assertEqual(lab.shrimp_razon_social, "Lab S.A.")
        lab.lab_ubicacion = "Salinas"
        self.assertEqual(lab.shrimp_ubicacion, "Salinas")
        self.assertEqual(lab.lab_ubicacion, "Salinas")
        cam = self.s["cam"]
        cam.farm_capacidad = 120.0
        self.assertEqual((cam.shrimp_capacity_value, cam.shrimp_capacity_unit), (120.0, "ton_year"))
        # La identificación estándar se llena desde vat_or_id.
        self.assertEqual(cam.vat, "0911100004001")

    def test_migracion_copia_el_perfil(self):
        cr = self.env.cr
        cr.execute("ALTER TABLE res_partner ADD COLUMN IF NOT EXISTS farm_razon_social varchar")
        cr.execute("ALTER TABLE res_partner ADD COLUMN IF NOT EXISTS farm_capacidad float8")
        cam = self.s["cam"]
        cr.execute("UPDATE res_partner SET shrimp_razon_social = NULL, shrimp_capacity_value = 0,"
                   " farm_razon_social = 'Vieja S.A.', farm_capacidad = 55, vat = NULL"
                   " WHERE id = %s", (cam.id,))
        mig = cargar_migracion("shrimp_user_registry", "19.0.1.2.0", "post")
        mig.migrate(cr, "19.0.1.1.0")
        mig.migrate(cr, "19.0.1.1.0")   # idempotente
        cam.invalidate_recordset()
        self.assertEqual(cam.shrimp_razon_social, "Vieja S.A.")
        self.assertEqual((cam.shrimp_capacity_value, cam.shrimp_capacity_unit), (55.0, "ton_year"))
        self.assertEqual(cam.vat, "0911100004001")

    # ------------------------------------------------------------------
    # Plataforma de cada sitio
    # ------------------------------------------------------------------
    def test_migracion_de_la_plataforma(self):
        cr = self.env.cr
        W = self.env["website"]
        sitio = W.create({"name": "Sitio migrado"})
        cr.execute("ALTER TABLE website ADD COLUMN IF NOT EXISTS shrimp_is_verifier_site boolean")
        cr.execute("UPDATE website SET shrimp_is_verifier_site = (id = %s)", (sitio.id,))
        # Antes de la versión 19.0.1.5.0 la columna no existía: la crea la
        # migración, sin la restricción NOT NULL que pone después el ORM.
        cr.execute("ALTER TABLE website ALTER COLUMN shrimp_platform DROP NOT NULL")
        cr.execute("UPDATE website SET shrimp_platform = NULL WHERE id = %s", (sitio.id,))
        mig = cargar_migracion("shrimp_marketplace", "19.0.1.5.0", "pre")
        mig.migrate(cr, "19.0.1.4.0")
        mig.migrate(cr, "19.0.1.4.0")   # idempotente
        sitio.invalidate_recordset()
        self.assertEqual(sitio.shrimp_platform, "verifier")
        cr.execute("SELECT count(*) FROM website WHERE shrimp_platform IS NULL")
        self.assertEqual(cr.fetchone()[0], 0)

    def test_menus_por_clave(self):
        principal = self.env["website"]._shrimp_main_site()
        M = self.env["website.menu"]
        raiz = M.search([("website_id", "=", principal.id), ("parent_id", "=", False)], limit=1)
        # Una copia vieja del menú del vendedor, sin clave (la barra nueva lo
        # llama «Vender», clave sell): se quita la que ya la tiene.
        M.search([("website_id", "=", principal.id), ("shrimp_key", "in", ("sell", "products"))]).unlink()
        menu = M.create({"name": "Nombre editado", "url": "/marketplace/products",
                         "parent_id": raiz.id, "website_id": principal.id})
        self.env["website"]._shrimp_alinear_menus_portal()
        self.assertEqual(menu.shrimp_key, "sell")
        # Un nombre que no es el de por defecto se respeta.
        self.assertEqual(menu.name, "Nombre editado")
        # Con clave, renombrarlo desde el editor ya no rompe el desplegable.
        menu.name = "Mis lotes"
        self.env["website"]._shrimp_alinear_menus_portal()
        self.assertEqual(menu.name, "Mis lotes")
        self.assertEqual(menu.shrimp_key, "sell")
