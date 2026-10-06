"""La barra superior del marketplace, definida en UN solo lugar.

Barra (sitio principal, usuario con sesión), siempre en este orden:

    Mi panel · Marketplace · Vender ▾ · Comprar ▾ · Servicios ▾      Perfil: X ▾ · Mi cuenta ▾

«Mi panel» y «Marketplace» son website.menu planos (shrimp_key panel /
marketplace). «Vender», «Comprar» y «Servicios» son website.menu con clave
(sell / buy / services) que la plantilla shrimp_marketplace.
submenu_mi_panel_dropdown convierte en desplegables; su CONTENIDO sale de
website._shrimp_nav_entries(). «Mi cuenta ▾» es el menú de usuario de la
derecha (sección «account», plantilla shrimp_marketplace.user_dropdown_mi_cuenta).

Reglas:
* Cada entrada declara la(s) capacidad(es) que la habilitan (matriz
  res.partner._shrimp_capability_matrix) evaluadas con el perfil ACTIVO; no
  hay «if tipo == ...» en las plantillas. Si mañana la matriz da un derecho
  nuevo a un rol (p. ej. compras entre pares), la entrada aparece sola.
* Un desplegable sin entradas para el perfil activo no se pinta.
* Cada módulo AÑADE sus entradas heredando _shrimp_nav_entries() (super() +
  append), en vez de inyectar <li> por xpath.
* Una entrada puede depender de que exista una ruta (`route`): así las
  pantallas que aún se están construyendo (pre-reservas, calendario de
  producción) aparecen solas el día que se instala su controlador.

Formato de una entrada (dict):
    section   "sell" | "buy" | "services" | "account" | "panel" (solo /my)
    key       identificador estable (lo usan las pruebas y data-shrimp-nav)
    sequence  orden dentro de la sección
    label     texto, o dict {rol: texto, None: texto por defecto}
    url       ruta (puede depender del socio: se arma al construir la lista)
    icon/tone icono FontAwesome y color (mp-mi-<tone>)
    caps      capacidades; basta con UNA (vacío = cualquier socio con perfil)
    exclude   capacidades que la ocultan (ninguna debe cumplirse)
    untyped   también para el usuario con sesión sin perfil (interno)
    route     ruta que tiene que existir para mostrarla (opcional)
    group     encabezado dentro del desplegable (opcional)
    new       marca «Nuevo» (opcional)
    desc      descripción corta para la tarjeta de /my (opcional)
"""
import logging

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import RequestRedirect

from odoo import api, models
from odoo.http import request

_logger = logging.getLogger(__name__)

# Secciones de la barra, en orden. "account" es el menú «Mi cuenta ▾» de la
# derecha (no es un website.menu).
SHRIMP_NAV_SECTIONS = [
    ("sell", "Vender"),
    ("buy", "Comprar"),
    ("services", "Servicios"),
    ("account", "Mi cuenta"),
    # No es un desplegable: lo que salió de la barra y vive en Mi panel / /my.
    ("panel", "Más herramientas"),
]

# Claves de website.menu que se pintan como desplegable, y a qué sección
# corresponden. Las tres primeras son las de la barra anterior (Productos /
# Operaciones / Mi cuenta): si una base todavía no se migró, sus menús siguen
# funcionando con la estructura nueva.
SHRIMP_NAV_DROPDOWN_KEYS = {
    "sell": "sell",
    "buy": "buy",
    "services": "services",
    "products": "sell",
    "operations": "buy",
    "account": "services",
}

# Capacidades que la matriz todavía no declara: con qué roles se comportan
# mientras tanto. Si un módulo las agrega a _shrimp_capability_matrix, manda
# la matriz.
SHRIMP_NAV_CAP_FALLBACK = {
    # Instalaciones y piscinas (siembra).
    "manage_ponds": {"camaronera"},
    # Pre-reservas de larva / nauplio (F4): vende S y L; reserva L y C.
    "larva_preorders_sell": {"semillero", "laboratorio"},
    "larva_preorders_buy": {"laboratorio", "camaronera"},
    # Calendario de producción (F7).
    "production_calendar": {"semillero", "laboratorio"},
    # Salidas / exportaciones (último eslabón de la trazabilidad).
    "register_exports": {"empacadora", "camaronera"},
}

# Qué se compra a cada tipo de vendedor: texto de la entrada y pestaña del
# catálogo (controllers/larva_marketplace.py: ?tipo=).
SHRIMP_NAV_BUY_FROM = {
    "semillero": ("Comprar nauplio", "nauplio", "fa-flask"),
    "laboratorio": ("Comprar larva", "larva", "fa-tint"),
    "camaronera": ("Comprar camarón", "camaron", "fa-shopping-basket"),
}


class Website(models.Model):
    _inherit = "website"

    # ------------------------------------------------------------------
    # Capacidades
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_nav_can(self, partner, capability):
        """¿El socio, con su perfil ACTIVO, tiene `capability`?

        Además de las de la matriz entiende:
        * "buy_products": puede comprar a algún tipo de vendedor (cualquier
          buy_from_* de la matriz; se lee en cada llamada, así que una
          capacidad de compra nueva cuenta sola).
        * las de SHRIMP_NAV_CAP_FALLBACK mientras la matriz no las tenga.
        """
        if not partner:
            return False
        socio = partner.sudo()
        matriz = socio._shrimp_capability_matrix()
        if capability == "buy_products":
            return any(socio._shrimp_can(cap) for cap in matriz if cap.startswith("buy_from_"))
        if capability not in matriz and capability in SHRIMP_NAV_CAP_FALLBACK:
            return socio._shrimp_effective_type() in SHRIMP_NAV_CAP_FALLBACK[capability]
        return socio._shrimp_can(capability)

    @api.model
    def _shrimp_nav_route_exists(self, path):
        """True si alguna ruta HTTP instalada atiende `path` (sin query)."""
        ruta = (path or "").split("?")[0].split("#")[0]
        if not ruta:
            return False
        try:
            self.env["ir.http"].routing_map().bind("").match(ruta, method="GET")
        except RequestRedirect:
            return True
        except (NotFound, MethodNotAllowed):
            return False
        except Exception:  # noqa: BLE001 — sin mapa de rutas: no se muestra
            _logger.debug("No se pudo resolver la ruta %s", ruta, exc_info=True)
            return False
        return True

    # ------------------------------------------------------------------
    # Tabla de entradas
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_nav_entries(self, partner):
        """Todas las entradas posibles de la barra para `partner`.

        Cada módulo la amplía con super(). No filtra: eso lo hace
        _shrimp_nav_tree() con las capacidades del perfil activo.
        """
        socio = partner.sudo() if partner else partner
        entradas = [
            # ===== Vender =====
            {"section": "sell", "key": "publish", "sequence": 10,
             "label": {"semillero": "Publicar lote de nauplio",
                       "laboratorio": "Publicar lote de larva",
                       "camaronera": "Publicar cosecha",
                       None: "Publicar producto"},
             "url": "/marketplace/products/new", "icon": "fa-plus-circle", "tone": "green",
             "caps": ("sell_products",), "untyped": True,
             "desc": "Crea una publicación para ofrecer tu lote en el marketplace."},
            {"section": "sell", "key": "my_products", "sequence": 20,
             "label": {"laboratorio": "Mis lotes y corridas", None: "Mis lotes"},
             "url": "/marketplace/products", "icon": "fa-cubes", "tone": "blue",
             "caps": ("sell_products",), "untyped": True,
             "desc": "Los lotes que publicaste y su estado."},
            {"section": "sell", "key": "preorders_in", "sequence": 30,
             "label": {"semillero": "Reservas de laboratorios",
                       None: "Pre-reservas de camaroneras"},
             "url": "/marketplace/pre-reservations", "route": "/marketplace/pre-reservations",
             "icon": "fa-calendar-check-o", "tone": "teal", "new": True,
             "caps": ("larva_preorders_sell",),
             "desc": "Pedidos anticipados de tus clientes."},
            {"section": "sell", "key": "production_calendar", "sequence": 40,
             "label": "Calendario de producción",
             "url": "/marketplace/production-calendar", "route": "/marketplace/production-calendar",
             "icon": "fa-calendar", "tone": "amber", "new": True,
             "caps": ("production_calendar",),
             "desc": "Tu capacidad de producción en los próximos días."},
            {"section": "sell", "key": "sales", "sequence": 90,
             "label": {"semillero": "Ventas y despachos", "laboratorio": "Ventas y despachos",
                       None: "Ventas"},
             "url": "/marketplace/sales", "icon": "fa-line-chart", "tone": "green",
             "caps": ("sell_products",), "untyped": True,
             "desc": "Lo que te compraron y el estado de cada despacho."},

            # ===== Comprar =====
            # «Comprar nauplio / larva / camarón»: una por cada tipo de
            # vendedor al que el perfil activo puede comprar (más abajo).
            {"section": "buy", "key": "preorders_out", "sequence": 30,
             "label": {"laboratorio": "Reservar nauplio",
                       None: "Reservar larva para mi siembra"},
             "url": "/marketplace/pre-reservations/new", "route": "/marketplace/pre-reservations/new",
             "icon": "fa-calendar-plus-o", "tone": "teal", "new": True,
             "caps": ("larva_preorders_buy",),
             "desc": "Reserva con anticipación para tu fecha de siembra."},
            {"section": "buy", "key": "purchases", "sequence": 60,
             "label": "Mis compras",
             "url": "/marketplace/purchases", "icon": "fa-shopping-cart", "tone": "blue",
             "caps": ("buy_products",), "untyped": True,
             "desc": "Tus compras (también las planificadas) y su trazabilidad."},
            {"section": "buy", "key": "inventory", "sequence": 70,
             # El laboratorio que solo compra nauplio ve «Inventario de
             # nauplio»; si la matriz le deja comprar también larva, es su
             # inventario a secas.
             "label": {"laboratorio": ("Mi inventario" if socio and socio._shrimp_can("buy_from_laboratorio")
                                       else "Inventario de nauplio"),
                       "camaronera": "Inventario y siembra",
                       None: "Mi inventario"},
             "url": "/marketplace/my-lots", "icon": "fa-flask", "tone": "teal",
             "caps": ("buy_from_semillero", "buy_from_laboratorio"), "untyped": True,
             "desc": "Los lotes que recibiste tras tus compras."},
            # Último eslabón de la trazabilidad: lo que sale de la planta.
            {"section": "buy", "key": "exports", "sequence": 75,
             "label": "Salidas y exportaciones",
             "url": "/marketplace/exports", "route": "/marketplace/exports",
             "icon": "fa-ship", "tone": "navy",
             "caps": ("register_exports",),
             "desc": "Registra la salida o exportación de tus lotes."},

            # ===== Mi panel (no va en la barra: tarjetas de /my) =====
            # Lo secundario que salió de la barra: reportes, disponibilidad
            # del catálogo (antes «Calendario de entregas») e instalaciones.
            {"section": "panel", "key": "reports", "sequence": 10,
             "label": "Mis reportes",
             "url": "/marketplace/reports", "icon": "fa-bar-chart", "tone": "green",
             "caps": (), "untyped": True,
             "desc": "Tus ventas, compras y comisiones en gráficas."},
            {"section": "panel", "key": "availability", "sequence": 20,
             "label": "Disponibilidad del catálogo",
             "url": "/marketplace/calendar", "icon": "fa-calendar", "tone": "amber",
             "caps": (), "untyped": True,
             "desc": "Cuándo estarán listos los lotes publicados en el marketplace."},
            {"section": "panel", "key": "facilities", "sequence": 30,
             "label": "Instalaciones y piscinas",
             "url": "/marketplace/my-facilities", "icon": "fa-map-o", "tone": "teal",
             "caps": ("manage_ponds",),
             "desc": "Tus piscinas y la siembra de los lotes que compraste."},

            # ===== Mi cuenta (derecha) =====
            {"section": "account", "key": "account_data", "sequence": 10,
             "label": "Datos de la empresa",
             "url": "/marketplace/my-account", "icon": "fa-user-o", "tone": "coral",
             "caps": (), "untyped": True},
            {"section": "account", "key": "certificates", "sequence": 20,
             "label": "Certificados",
             "url": "/marketplace/my-account#certificados", "icon": "fa-certificate", "tone": "amber",
             "caps": ()},
            # Las tarjetas nativas de Odoo en /my están ocultas: el cambio de
            # contraseña (/my/security) se ofrece aquí.
            {"section": "account", "key": "security", "sequence": 80,
             "label": "Contraseña y seguridad",
             "url": "/my/security", "icon": "fa-lock", "tone": "navy",
             "caps": (), "untyped": True},
        ]
        if socio and socio.uuid_ref:
            entradas.append(
                {"section": "account", "key": "storefront", "sequence": 30,
                 "label": "Mi vitrina pública",
                 "url": "/marketplace/sellers/%s" % socio._shrimp_role_holder().uuid_ref,
                 "icon": "fa-shopping-bag", "tone": "teal",
                 "caps": ("sell_products",)})
        # En el orden de la cadena: nauplio, larva, camarón.
        for i, (tipo, (texto, pestana, icono)) in enumerate(SHRIMP_NAV_BUY_FROM.items()):
            entradas.append(
                {"section": "buy", "key": "buy_%s" % tipo, "sequence": 10 + i,
                 "label": texto, "url": "/marketplace?tipo=%s" % pestana,
                 "icon": icono, "tone": "green",
                 "caps": ("buy_from_%s" % tipo,),
                 # Quien emite listas de precios (empacadora) compra desde
                 # «Oferta disponible», que ya cruza el catálogo con sus precios.
                 "exclude": ("issue_price_lists",) if tipo == "camaronera" else (),
                 "desc": "El catálogo filtrado por lo que puedes comprar."})
        return entradas

    # ------------------------------------------------------------------
    # Árbol para el perfil activo
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_nav_partner(self):
        """El socio del usuario de la petición, o None (visitante)."""
        user = self.env.user
        if not user or user._is_public():
            return None
        return user.partner_id.sudo()

    @api.model
    def _shrimp_nav_entry_visible(self, partner, entry, role):
        if not role:
            if not entry.get("untyped"):
                return False
        else:
            caps = entry.get("caps") or ()
            if caps and not any(self._shrimp_nav_can(partner, c) for c in caps):
                return False
            if any(self._shrimp_nav_can(partner, c) for c in entry.get("exclude") or ()):
                return False
        if entry.get("route") and not self._shrimp_nav_route_exists(entry["route"]):
            return False
        return True

    @api.model
    def _shrimp_nav_tree(self, partner=None):
        """{"role": rol activo, "sections": {sección: {"key", "label",
        "items": [entradas resueltas]}}} para `partner` (por defecto, el
        usuario de la petición). Solo las secciones con entradas.

        Se calcula una vez por petición (la barra de escritorio, la del
        móvil y /my la piden varias veces)."""
        if partner is None:
            partner = self._shrimp_nav_partner()
        if not partner:
            return {"role": False, "sections": {}}
        partner = partner.sudo()
        role = partner._shrimp_effective_type() or False
        clave_cache = (partner.id, role, self.id or 0)
        cache = None
        if request:
            cache = getattr(request, "_shrimp_nav_cache", None)
            if cache is None:
                cache = {}
                try:
                    request._shrimp_nav_cache = cache
                except Exception:  # noqa: BLE001
                    cache = None
            if cache is not None and clave_cache in cache:
                return cache[clave_cache]

        etiquetas = dict(SHRIMP_NAV_SECTIONS)
        secciones = {}
        vistas = set()
        entradas = sorted(self._shrimp_nav_entries(partner),
                          key=lambda e: (e.get("sequence", 50), e.get("key", "")))
        for entrada in entradas:
            if not self._shrimp_nav_entry_visible(partner, entrada, role):
                continue
            # Sin duplicados: la misma URL una sola vez en toda la barra.
            if entrada["url"] in vistas:
                continue
            vistas.add(entrada["url"])
            texto = entrada.get("label")
            if isinstance(texto, dict):
                texto = texto.get(role, texto.get(None, ""))
            item = dict(entrada, label=texto)
            sec = secciones.setdefault(entrada["section"], {
                "key": entrada["section"],
                "label": etiquetas.get(entrada["section"], entrada["section"]),
                "items": []})
            previo = sec["items"][-1].get("group") if sec["items"] else False
            # Encabezado del grupo solo en la primera entrada de cada grupo.
            item["group_start"] = item.get("group") if item.get("group") != previo else False
            sec["items"].append(item)
        res = {"role": role, "sections": secciones}
        if cache is not None:
            cache[clave_cache] = res
        return res

    @api.model
    def _shrimp_nav_section(self, menu_key, partner=None):
        """La sección (o None) que pinta el website.menu de clave `menu_key`."""
        seccion = SHRIMP_NAV_DROPDOWN_KEYS.get(menu_key or "")
        if not seccion:
            return None
        sec = self._shrimp_nav_tree(partner)["sections"].get(seccion)
        return sec if sec and sec["items"] else None

    @api.model
    def _shrimp_nav_links(self, partner=None, sections=("sell", "buy", "services")):
        """Lista plana [(sección, key, label, url)] (pruebas y /my)."""
        arbol = self._shrimp_nav_tree(partner)
        res = []
        for seccion, _label in SHRIMP_NAV_SECTIONS:
            if seccion not in sections:
                continue
            for item in arbol["sections"].get(seccion, {}).get("items", []):
                res.append((seccion, item["key"], item["label"], item["url"]))
        return res
