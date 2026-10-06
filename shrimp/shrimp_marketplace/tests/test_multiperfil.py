"""Varios perfiles por cuenta en el marketplace.

Una cuenta laboratorio + camaronera: compra nauplios como laboratorio y larva
como camaronera; cada transacción guarda con qué rol actuó cada parte y la
trazabilidad lo muestra en cada eslabón. El perfil activo se cambia desde la
barra superior (POST con CSRF) y persiste en la cuenta.
"""
from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, ShrimpSecurityCommon, crear_socios, csrf_de, producto


def _hacer_multi(env, socio, rol_extra, datos=None):
    """Agrega un perfil (sin aprobación) a `socio` y lo devuelve."""
    return socio._shrimp_request_role(rol_extra, datos or {
        "shrimp_representante": "Rep MP", "shrimp_telefono": "04-3333333"})


@tagged("post_install", "-at_install")
class TestMultiPerfilMarketplace(ShrimpSecurityCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # El laboratorio de la cadena también es camaronera.
        cls.multi = cls.s["lab"]
        _hacer_multi(cls.env, cls.multi, "camaronera")
        cls.lote_lab2 = producto(cls.env, cls.s["lab2"], nombre="Larva lab2 MP")
        cls.lote_cam = producto(cls.env, cls.s["cam"], nombre="Juvenil cam MP",
                                etapa="shrimp_marketplace.shrimp_stage_juvenil")

    def test_el_rol_del_comprador_decide_que_puede_comprar(self):
        # Activo: laboratorio -> compra nauplios y, al mismo nivel, larva de
        # otro laboratorio; el lote de una camaronera, no.
        self.assertFalse(self.larva.motivo_no_comprable(self.multi))
        self.assertFalse(self.lote_lab2.motivo_no_comprable(self.multi))
        if self.env["res.partner"]._shrimp_type_can("laboratorio", "buy_from_camaronera"):
            # Sin shrimp_packer el lote de camaronera lo compra cualquiera.
            return
        motivo = self.lote_cam.motivo_no_comprable(self.multi)
        self.assertTrue(motivo)
        self.assertIn("Cambia tu perfil activo a «Camaronera»", motivo,
                      "sugiere el perfil que sí puede comprarlo")
        # Con el rol explícito, sí.
        self.assertFalse(self.lote_cam.motivo_no_comprable(self.multi, role="camaronera"))
        # Un perfil que no tiene, nunca.
        self.assertTrue(self.lote_lab2.motivo_no_comprable(self.multi, role="semillero"))

    def test_transacciones_guardan_el_rol_de_cada_parte(self):
        tx1 = self.larva.execute_purchase_flow(self.multi, 5.0)["transaction"]
        self.assertEqual((tx1.seller_role, tx1.buyer_role), ("semillero", "laboratorio"))
        self.assertEqual(tx1.transaction_type, "semillero_to_laboratorio")
        # El lote que recibirá (al confirmar la recepción) es del perfil con
        # el que compró.
        self.assertTrue(tx1._buyer_can_republish())
        self.assertEqual(tx1._prepare_new_product_vals()["seller_role"], "laboratorio")

        # Cambia a camaronera y compra larva de otro laboratorio.
        self.multi._shrimp_set_active_role("camaronera")
        tx2 = self.lote_lab2.execute_purchase_flow(self.multi, 3.0)["transaction"]
        self.assertEqual((tx2.seller_role, tx2.buyer_role), ("laboratorio", "camaronera"))
        self.assertEqual(tx2.transaction_type, "laboratorio_to_camaronera")
        self.assertFalse(tx2._buyer_can_republish(), "la camaronera no revende la larva")

        # Comprar como laboratorio estando activa la camaronera (rol explícito).
        tx3 = self.larva.execute_purchase_flow(self.multi, 1.0, buyer_role="laboratorio")
        self.assertEqual(tx3["transaction"].buyer_role, "laboratorio")
        # Con un rol que no tiene, no.
        with self.assertRaises(ValidationError):
            self.larva.execute_purchase_flow(self.multi, 1.0, buyer_role="semillero")

    def test_trazabilidad_muestra_el_rol_en_cada_eslabon(self):
        # La misma cuenta sale como laboratorio donde compró nauplios...
        tx1 = self.larva.execute_purchase_flow(self.multi, 5.0)["transaction"]
        cadena = tx1.traceability_chain()
        self.assertEqual([(c["name"], c["role_code"]) for c in cadena],
                         [(self.s["sem"].name, "semillero"), (self.multi.name, "laboratorio")])
        self.assertEqual(cadena[1]["role"], "Laboratorio")
        # ...y como camaronera donde compró larva.
        self.multi._shrimp_set_active_role("camaronera")
        tx2 = self.lote_lab2.execute_purchase_flow(self.multi, 2.0)["transaction"]
        self.assertEqual([c["role_code"] for c in tx2.traceability_chain()],
                         ["laboratorio", "camaronera"])
        # El cambio de perfil posterior no reescribe la historia.
        self.multi._shrimp_set_active_role("laboratorio")
        self.assertEqual(tx2.buyer_role, "camaronera")
        self.assertEqual([c["role_code"] for c in tx2.traceability_chain()][-1], "camaronera")

    def test_publicar_toma_el_perfil_activo(self):
        self.multi._shrimp_set_active_role("camaronera")
        lote = self.env["shrimp.product"].create({
            "name": "Engorde MP", "seller_partner_id": self.multi.id,
            "stage_id": self.env.ref("shrimp_marketplace.shrimp_stage_pl12").id,
            "initial_qty": 10.0, "price": 1.0,
            "uom_id": self.env.ref("shrimp_marketplace.uom_millar").id})
        self.assertEqual(lote.seller_role, "camaronera")
        self.assertEqual(lote._shrimp_seller_role(), "camaronera")
        # Un lote de un perfil que la cuenta no tiene cae al perfil activo.
        lote_sem = producto(self.env, self.s["sem"], nombre="Sem MP", seller_role="semillero")
        self.assertEqual(lote_sem._shrimp_seller_role(), "semillero")

    def test_restriccion_de_la_transaccion_usa_los_roles(self):
        Tx = self.env["shrimp.transaction"]
        with self.assertRaises(ValidationError):
            # La cuenta no tiene el perfil semillero: no puede vender como tal.
            Tx.create({
                "transaction_type": "semillero_to_laboratorio", "product_id": self.lote_lab2.id,
                "seller_partner_id": self.multi.id, "buyer_partner_id": self.s["lab2"].id,
                "seller_role": "semillero", "buyer_role": "laboratorio",
                "transaction_qty": 1.0, "sold_qty": 1.0})


@tagged("post_install", "-at_install")
class TestMultiPerfilPortal(HttpCase):
    """El selector «Perfil» de la barra y «Agregar perfil» de Mi cuenta."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "mp")
        _hacer_multi(cls.env, cls.s["lab"], "camaronera")
        cls.lote_lab2 = producto(cls.env, cls.s["lab2"], nombre="Larva portal MP")

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def _cabecera(self, ruta="/marketplace/my-account"):
        r = self.url_open(ruta)
        self.assertEqual(r.status_code, 200, ruta)
        return r.text

    def test_cambiar_perfil_desde_la_barra(self):
        self.authenticate(self.s["u_lab"].login, CLAVE)
        pagina = self._cabecera()
        self.assertIn("shrimp-profile-switch", pagina)
        self.assertIn("Perfil: Laboratorio", pagina)
        self.assertIn('data-role="camaronera"', pagina)
        token = csrf_de(pagina)
        # Sin CSRF no cambia nada.
        r = self.url_open("/my/profile/activate", data={"role": "camaronera"})
        self.assertNotEqual(r.status_code, 200)
        self.assertEqual(self.s["lab"].shrimp_user_type, "laboratorio")
        # Un perfil que la cuenta no tiene: no cambia.
        self.url_open("/my/profile/activate", data={
            "csrf_token": token, "role": "semillero", "next": "/marketplace/my-account"})
        self.assertEqual(self.s["lab"].shrimp_user_type, "laboratorio")
        # Redirección abierta: se ignora el destino externo.
        r = self.url_open("/my/profile/activate", data={
            "csrf_token": token, "role": "camaronera", "next": "//malicioso.example/x"},
            allow_redirects=False)
        self.assertNotIn("malicioso", r.headers.get("Location", ""))
        self.assertEqual(self.s["lab"].shrimp_user_type, "camaronera")
        # La barra y los permisos siguen al perfil activo: ahora le ofrece
        # comprar la larva del otro laboratorio.
        self.assertIn("Perfil: Camaronera", self._cabecera())
        self.assertFalse(self.lote_lab2.motivo_no_comprable(self.s["lab"]))
        # Persiste en la cuenta (otra sesión lo ve igual).
        self.authenticate(self.s["u_lab"].login, CLAVE)
        self.assertIn("Perfil: Camaronera", self._cabecera())

    def test_agregar_perfil_desde_mi_cuenta(self):
        self.authenticate(self.s["u_sem"].login, CLAVE)
        pagina = self._cabecera()
        self.assertIn("Mis perfiles", pagina)
        self.assertIn('action="/my/profile/add"', pagina)
        token = csrf_de(pagina)
        r = self.url_open("/my/profile/add", data={
            "csrf_token": token, "role": "laboratorio", "next": "/marketplace/my-account",
            "shrimp_razon_social": "Semillero y Lab S.A.", "shrimp_ubicacion": "Salinas"},
            allow_redirects=False)
        self.assertIn("saved=perfil", r.headers.get("Location", ""))
        socio = self.s["sem"]
        self.assertEqual(socio._shrimp_roles(), ["semillero", "laboratorio"])
        self.assertEqual(socio.shrimp_razon_social, "Semillero y Lab S.A.")
        self.assertEqual(socio.shrimp_user_type, "semillero", "agregar no cambia el activo")
        # Un tipo inventado o que no se agrega desde aquí: no.
        r = self.url_open("/my/profile/add", data={
            "csrf_token": token, "role": "admin", "next": "/marketplace/my-account"},
            allow_redirects=False)
        self.assertIn("error=", r.headers.get("Location", ""))
        self.assertNotIn("admin", socio._shrimp_roles(include_pending=True))
