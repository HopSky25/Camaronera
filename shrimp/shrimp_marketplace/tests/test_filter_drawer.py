"""Cajón de filtros reutilizable en las páginas del portal.

Mis productos, ventas, compras, planificación e historial de compras usan las
plantillas s_filter_toolbar / s_filter_tags / s_filter_drawer. Se comprueba:
acceso igual que antes, cajón presente, secciones según el perfil y los datos
(sin presentación/talla para quien no compra camarón, sin «Vendedor» en Mis
productos, estadíos solo los que la cuenta tiene, «Perfil» solo con varios),
conteo en vivo igual que la lista, etiquetas que conservan el resto de
parámetros y la venta en tarjeta (móvil) + tabla (escritorio).

Todo se monta aquí (sin depender de la demo); las búsquedas usan nombres «FD».
"""
import json
from datetime import date

from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios, producto

MP = "shrimp_marketplace."


@tagged("post_install", "-at_install")
class TestFilterDrawer(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = s = crear_socios(env, "fd")
        hoy = date.today()
        # Semillero: dos nauplios publicados (precios distintos) y uno en borrador.
        cls.n1 = producto(env, s["sem"], nombre="Nauplio FD uno", etapa=MP + "shrimp_stage_nauplio",
                          initial_qty=1000.0, price=10.0, expected_delivery_date=hoy)
        cls.n2 = producto(env, s["sem"], nombre="Nauplio FD dos", etapa=MP + "shrimp_stage_nauplio",
                          initial_qty=1000.0, price=12.5, location="Santa Elena")
        cls.n3 = producto(env, s["sem"], nombre="Nauplio FD borrador", etapa=MP + "shrimp_stage_nauplio",
                          publicado=False)
        # El laboratorio le compra dos veces: 150 × 10 = 1.500,00 (recibida) y 20 × 12,5.
        cls.tx1 = cls.n1.execute_purchase_flow(s["lab"], 150.0)["transaction"]
        cls.tx1.action_receive()
        cls.tx2 = cls.n2.execute_purchase_flow(s["lab"], 20.0)["transaction"]
        # Larva del laboratorio que compra la camaronera.
        cls.pl = producto(env, s["lab"], nombre="PL12 FD", etapa=MP + "shrimp_stage_pl12")
        cls.tx3 = cls.pl.execute_purchase_flow(s["cam"], 10.0)["transaction"]

    def _login(self, clave):
        self.authenticate(self.s["u_" + clave].login, CLAVE)

    def _get(self, url):
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200, url)
        return r.text

    def _count(self, url):
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200, url)
        return json.loads(r.text)["count"]

    # ------------------------------------------------------------ acceso
    def test_anonimo_va_al_login(self):
        for url in ("/marketplace/products", "/marketplace/sales", "/marketplace/purchases",
                    "/marketplace/planning", "/marketplace/purchase-history",
                    "/marketplace/sales/count", "/marketplace/purchases/count",
                    "/marketplace/products/count"):
            r = self.url_open(url, allow_redirects=False)
            self.assertIn(r.status_code, (302, 303), url)
            self.assertIn("/web/login", r.headers.get("Location", ""), url)

    # ------------------------------------------------------- Mis productos
    def test_mis_productos_semillero(self):
        self._login("sem")
        html = self._get("/marketplace/products?q=FD")
        self.assertIn('id="sFilterDrawer"', html)
        self.assertIn('id="sMktToolbar"', html)
        self.assertIn('role="dialog"', html)
        # Sin barra lateral vieja.
        self.assertNotIn("s-market-side", html)
        self.assertNotIn("s-filter-panel", html)
        # Sin «Vendedor» (es uno mismo) ni presentación/talla (no es camarón).
        self.assertNotIn('id="sf_sec_seller"', html)
        self.assertNotIn('id="fltSeller"', html)
        self.assertNotIn('id="sf_sec_presentation"', html)
        self.assertNotIn('id="sf_sec_size"', html)
        # Un solo estadío (nauplio): sin fila de chips. Un solo perfil: sin «Perfil».
        self.assertNotIn('id="sMisProdStages"', html)
        self.assertNotIn('id="sf_sec_role"', html)
        # Publicados y borrador: «Estado» sí; precios distintos: «Precio» sí.
        self.assertIn('id="sf_sec_pstate"', html)
        self.assertIn('id="sf_sec_price"', html)
        # Vista lista/cuadrícula y conteo en vivo.
        self.assertIn('data-view-key="shrimp_misprod_vista"', html)
        self.assertIn('data-count-url="/marketplace/products/count"', html)

    def test_mis_productos_conteo_y_etiquetas(self):
        self._login("sem")
        self.assertEqual(self._count("/marketplace/products/count?q=Nauplio FD"), 3)
        self.assertEqual(self._count("/marketplace/products/count?q=Nauplio FD&pstate=draft"), 1)
        self.assertEqual(self._count("/marketplace/products/count?q=Nauplio FD&price_min=11"), 1)
        html = self._get("/marketplace/products?q=FD&pstate=draft&order=price_asc")
        self.assertIn('aria-label="Filtros, 1 activos"', html)
        self.assertIn("Estado: Borradores", html)
        # Quitar la etiqueta conserva búsqueda y orden.
        self.assertIn('href="/marketplace/products?q=FD&amp;order=price_asc#listado"', html)
        self.assertIn("Nauplio FD borrador", html)
        self.assertNotIn("Nauplio FD dos", html)
        # El parámetro viejo ?seller= sigue funcionando (y se puede quitar).
        html = self._get("/marketplace/products?seller=no-existe")
        self.assertIn("Vendedor: no-existe", html)
        self.assertNotIn("Nauplio FD uno", html)

    # ----------------------------------------------------- catálogo/perfil
    def test_catalogo_secciones_segun_perfil(self):
        self._login("sem")
        html = self._get("/marketplace")
        self.assertIn('id="sFilterDrawer"', html)
        self.assertNotIn('id="sf_sec_presentation"', html)
        self.assertNotIn('id="sf_sec_size"', html)
        self._login("cam")
        html = self._get("/marketplace")
        self.assertIn('id="sf_sec_presentation"', html)
        self.assertIn('id="sf_sec_size"', html)
        # Las tallas dependen de la presentación (JS genérico data-s-dep).
        self.assertIn('data-s-dep="presentation"', html)

    # --------------------------------------------------------------- ventas
    def test_ventas_tarjetas_y_tabla(self):
        self._login("sem")
        html = self._get("/marketplace/sales")
        # Las dos vistas van en el HTML; el CSS elige (tabla ≥ 768 px, tarjetas < 768 px).
        self.assertIn("s-tx-table", html)
        self.assertIn('id="sTxCards"', html)
        self.assertIn('class="s-txc"', html)
        self.assertIn('class="s-txc-grid"', html)
        self.assertIn('data-s-tx-actions="1"', html)
        # Importes con separador de miles y 2 decimales; fechas dd/mm/aaaa.
        self.assertIn("1,500.00", html)
        fecha = self.tx1.create_date.strftime("%d/%m/%Y")
        self.assertIn(fecha, html)
        self.assertNotIn(self.tx1.create_date.strftime("%Y-%m-%d"), html)
        # Cajón: dos estados (confirmada y completada) → «Estado»; un solo
        # estadío (nauplio) y un solo perfil → ni «Estadío» ni «Perfil».
        self.assertIn('id="sf_sec_state"', html)
        self.assertNotIn('id="sf_sec_stage"', html)
        self.assertNotIn('id="sf_sec_role"', html)
        self.assertNotIn('id="sf_sec_delivery"', html)
        self.assertIn('data-count-url="/marketplace/sales/count"', html)
        # Un modal de calificación por venta (no duplicado por las dos vistas).
        self.assertEqual(html.count('id="rateBuyerModal%s"' % self.tx1.uuid_ref), 1)

    def test_ventas_conteo(self):
        self._login("sem")
        Tx = self.env["shrimp.transaction"]
        self.assertEqual(self._count("/marketplace/sales/count"),
                         Tx.search_count([("seller_partner_id", "=", self.s["sem"].id)]))
        self.assertEqual(self._count("/marketplace/sales/count?tx_state=done"), 1)
        self.assertEqual(self._count("/marketplace/sales/count?q=Nauplio FD dos"), 1)
        # Valores imposibles se descartan (no revientan).
        self.assertEqual(self._count("/marketplace/sales/count?date_from=hola&stage=x"), 2)

    def test_ventas_etiquetas_conservan_parametros(self):
        self._login("sem")
        html = self._get("/marketplace/sales?q=Nauplio&tx_state=done&order=oldest&date_from=2020-01-01")
        self.assertIn('aria-label="Filtros, 2 activos"', html)
        self.assertIn("Estado: Completada", html)
        self.assertIn("Venta desde 01/01/2020", html)
        # Quitar «Estado» conserva fecha, búsqueda y orden.
        self.assertIn('href="/marketplace/sales?date_from=2020-01-01&amp;q=Nauplio&amp;order=oldest#listado"', html)
        # «Limpiar todo» conserva solo búsqueda y orden.
        self.assertIn('href="/marketplace/sales?q=Nauplio&amp;order=oldest#listado"', html)

    # -------------------------------------------------------------- compras
    def test_compras_cajon_y_conteo(self):
        self._login("lab")
        html = self._get("/marketplace/purchases")
        self.assertIn('id="sFilterDrawer"', html)
        self.assertNotIn("s-market-side", html)
        self.assertIn("s-pc-card", html)
        self.assertIn('data-count-url="/marketplace/purchases/count"', html)
        self.assertNotIn('id="sf_sec_role"', html)
        self.assertEqual(self._count("/marketplace/purchases/count"), 2)
        self.assertEqual(self._count("/marketplace/purchases/count?tx_state=done"), 1)

    def test_compras_perfil_solo_con_varios(self):
        # Una cuenta que compró con dos perfiles ve «Compré como». (El rol
        # se fija al comprar; aquí se simula el histórico directamente.)
        self.env.cr.execute("UPDATE shrimp_transaction SET buyer_role = 'camaronera' WHERE id = %s",
                            (self.tx2.id,))
        self.env.invalidate_all()
        self._login("lab")
        html = self._get("/marketplace/purchases")
        self.assertIn('id="sf_sec_role"', html)
        self.assertEqual(self._count("/marketplace/purchases/count?role=camaronera"), 1)
        self.assertEqual(self._count("/marketplace/purchases/count?role=laboratorio"), 1)
        # La camaronera compró con un solo perfil: sin la sección.
        self._login("cam")
        self.assertNotIn('id="sf_sec_role"', self._get("/marketplace/purchases"))

    def test_planificacion_e_historial(self):
        self._login("lab")
        html = self._get("/marketplace/planning")
        self.assertIn('id="sf_sec_delivery"', html)
        self.assertIn("Próximos 7 días", html)
        self.assertEqual(self._count("/marketplace/planning/count"), 2)
        # El historial filtra en su propia página (antes llevaba a /compras).
        html = self._get("/marketplace/purchase-history")
        self.assertIn('action="/marketplace/purchase-history"', html)
        self.assertEqual(self._count("/marketplace/purchase-history/count?tx_state=done"), 1)

    def test_solicitudes_sin_filtros_vacios(self):
        self._login("lab")
        html = self._get("/marketplace/check-requests")
        # Sin solicitudes no hay estados que elegir.
        self.assertNotIn('id="sSolicitudesEstados"', html)
