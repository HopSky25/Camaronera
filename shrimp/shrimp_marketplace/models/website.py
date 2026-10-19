import base64

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import file_open
from odoo.tools.image import image_process

# Plataformas que comparten la base. Cada sitio web es una de ellas; el campo
# reemplaza a los booleanos shrimp_is_verifier_site / shrimp_is_copacker_site
# que definía cada módulo (se conservan una versión como alias).
SHRIMP_PLATFORMS = [
    ("main", "Marketplace (CamaronMarket)"),
    ("verifier", "CamaronMarket Verificadores"),
    ("copacker", "Empaque (CamaronMarket Empaque)"),
]

# Claves estables de los menús del portal. La barra superior convierte estos
# menús en desplegables; antes los reconocía por su NOMBRE literal, y el día
# que alguien renombraba "Productos" desde el editor del sitio, el
# desplegable desaparecía con todas sus entradas.
SHRIMP_MENU_KEYS = [
    # Barra del marketplace: Mi panel · Marketplace · Vender · Comprar ·
    # Servicios (ver models/website_navbar.py).
    ("panel", "Mi panel"),
    ("marketplace", "Marketplace"),
    ("sell", "Vender"),
    ("buy", "Comprar"),
    ("services", "Servicios"),
    # Claves de la barra anterior (Productos / Operaciones / Mi cuenta). Se
    # conservan para que una base sin migrar siga pintando sus desplegables;
    # _shrimp_alinear_menus_portal() las convierte en sell / buy / services.
    ("products", "Productos (antiguo, = Vender)"),
    ("operations", "Operaciones (antiguo, = Comprar)"),
    ("account", "Mi cuenta (antiguo, = Servicios)"),
    ("verifier_jobs", "Verificaciones (verificadores)"),
    ("verifier_company", "Mi empresa (verificadores)"),
    ("copacker_jobs", "Trabajos (empaque)"),
    ("copacker_plant", "Mi planta (empaque)"),
]

# Menús que el visitante sin sesión sí ve, por plataforma: el catálogo
# público del marketplace es la puerta de entrada para quien aún no tiene
# cuenta.
SHRIMP_PUBLIC_MENU_URLS = {
    "main": ("/marketplace",),
}

# Menús que el usuario de portal CON sesión ya no ve en el marketplace: el
# logo lleva a la portada (que lo manda a su panel) y «Contáctanos» está en
# el pie de página. El usuario interno (quien edita el sitio) los sigue viendo.
SHRIMP_HIDDEN_WITH_SESSION_URLS = {
    "main": ("/", "/contactus"),
}


class WebsiteMenu(models.Model):
    _inherit = "website.menu"

    def _compute_visible(self):
        """El menú de opciones es del usuario con cuenta, en las tres
        plataformas: al visitante sin sesión solo se le muestran el logo,
        «Iniciar sesión», el botón de alta (ver navbar_dropdown.xml) y los
        menús públicos de SHRIMP_PUBLIC_MENU_URLS (en el marketplace,
        «Marketplace»). Se decide aquí, una vez, y vale para la barra de
        escritorio y para el menú lateral del móvil. Las páginas siguen
        siendo accesibles por su URL; solo se oculta la navegación."""
        super()._compute_visible()
        if self.env.user._is_public():
            for menu in self:
                plataforma = menu.website_id.sudo().shrimp_platform or "main"
                publicas = SHRIMP_PUBLIC_MENU_URLS.get(plataforma, ())
                if menu.is_visible and menu.url in publicas:
                    continue
                menu.is_visible = False
        elif self.env.user.share:
            rol_con_panel = None
            for menu in self:
                if not menu.is_visible:
                    continue
                plataforma = menu.website_id.sudo().shrimp_platform or "main"
                if menu.url in SHRIMP_HIDDEN_WITH_SESSION_URLS.get(plataforma, ()):
                    menu.is_visible = False
                elif menu.shrimp_key == "panel":
                    # «Mi panel» solo para los perfiles que tienen panel
                    # (/my/dashboard manda a /my al verificador o al
                    # maquilador).
                    if rol_con_panel is None:
                        Dash = self.env["shrimp.dashboard"].sudo()
                        rol_con_panel = Dash._panel_supported(
                            Dash._panel_active_role(self.env.user.partner_id))
                    menu.is_visible = rol_con_panel

    shrimp_key = fields.Selection(
        SHRIMP_MENU_KEYS, string="Clave de menú de la plataforma", index=True,
        help="Identifica el menú para pintarlo como desplegable de la "
             "plataforma, sin depender de su nombre (que se puede editar).")


class Website(models.Model):
    _inherit = "website"

    shrimp_platform = fields.Selection(
        SHRIMP_PLATFORMS, string="Plataforma", default="main", required=True,
        index=True,
        help="Qué plataforma sirve este sitio: su portada, su marca y su menú. "
             "El marketplace es el sitio principal; CamaronMarket Verificadores el de los "
             "verificadores y el de empaque el de las plantas maquiladoras.")

    # ------------------------------------------------------------------
    # Consultas por plataforma
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_platform_site(self, platform):
        """El sitio de una plataforma (el de id más bajo si hubiera varios)."""
        return self.sudo().search([("shrimp_platform", "=", platform)], order="id", limit=1)

    @api.model
    def _shrimp_main_site(self):
        """El sitio del marketplace: ni el de verificadores ni el de empaque."""
        return self._shrimp_platform_site("main")

    def _shrimp_sitios_principales(self):
        """Todos los sitios del marketplace (los que no son de otra plataforma)."""
        return self.env["website"].sudo().search([("shrimp_platform", "=", "main")], order="id")

    @api.model
    def _shrimp_current_platform(self, website=None):
        web = website or self.env["website"].get_current_website()
        return web.sudo().shrimp_platform if web else "main"

    # ------------------------------------------------------------------
    # Perfiles por sitio
    # ------------------------------------------------------------------
    # El perfil PROPIO de cada plataforma: la verificadora se registra, se
    # agrega, acepta su anexo y opera solo en CamaronMarket Verificadores; el
    # maquilador (planta de maquila), solo en CamaronMarket Empaque. El
    # marketplace admite todos los demás (naupliero, laboratorio, camaronera,
    # empacadora...). Los roles van como texto: este módulo no depende de los
    # módulos que los definen.
    _SHRIMP_PLATFORM_ROLE = {"verifier": "verificador", "copacker": "maquilador"}

    @api.model
    def _shrimp_role_platform(self, role):
        """Plataforma donde se usa `role` ('main' para los del marketplace)."""
        for plataforma, rol in self._SHRIMP_PLATFORM_ROLE.items():
            if rol == role:
                return plataforma
        return "main"

    def _shrimp_role_allowed_here(self, role):
        """True si `role` se puede registrar, agregar, activar y usar en este
        sitio (sin sitio: el marketplace)."""
        plataforma = (self[:1].sudo().shrimp_platform or "main") if self else "main"
        return bool(role) and self._shrimp_role_platform(role) == plataforma

    def _shrimp_site_roles(self, roles):
        """Los `roles` (en su orden) que se usan en este sitio."""
        return [r for r in (roles or []) if self._shrimp_role_allowed_here(r)]

    @api.model
    def _shrimp_role_site_url(self, role, path="/"):
        """URL de `path` en el sitio donde se usa `role`: absoluta si ese
        sitio tiene dominio; None si no existe o no tiene dominio (la ruta
        relativa caería en el sitio actual)."""
        sitio = self._shrimp_platform_site(self._shrimp_role_platform(role))
        dominio = (sitio.domain or "").strip().rstrip("/") if sitio else ""
        if not dominio:
            return None
        if "://" not in dominio:
            dominio = "https://" + dominio
        return dominio + path

    def _shrimp_offsite_links(self, roles, path="/web/login"):
        """[{role, label, site, url}] de los `roles` que NO se usan en este
        sitio, con el enlace a `path` en su propio sitio (solo los sitios con
        dominio configurado)."""
        Partner = self.env["res.partner"]
        res = []
        for r in roles or []:
            if self._shrimp_role_allowed_here(r):
                continue
            url = self._shrimp_role_site_url(r, path)
            if not url:
                continue
            sitio = self._shrimp_platform_site(self._shrimp_role_platform(r))
            res.append({"role": r, "label": Partner._shrimp_type_label(r),
                        "site": sitio.name, "url": url})
        return res

    def _shrimp_site_fallback_role(self, user, preferred=None):
        """Perfil aprobado de la cuenta de `user` que se usa en este sitio y
        que el usuario puede activar (el preferido si vale; si no, el primero
        en el orden de los perfiles), o None."""
        holder = user.sudo()._shrimp_account_company()
        if not holder:
            return None
        holder = holder.with_user(user).sudo()
        candidatos = [r for r in self._shrimp_site_roles(holder._shrimp_roles())
                      if holder._shrimp_user_allows(None, r)]
        if preferred and preferred in candidatos:
            return preferred
        return candidatos[0] if candidatos else None

    def _shrimp_align_active_role(self, user, preferred=None):
        """Si el perfil ACTIVO de `user` no se usa en este sitio y la cuenta
        tiene otro aprobado (y permitido a este usuario) que sí, lo activa.
        Desde CR-12 el perfil activo es POR USUARIO: cambiarlo aquí no se lo
        cambia a los demás miembros de la cuenta; al volver al otro sitio se
        vuelve a alinear.

        Devuelve el perfil activo resultante: puede seguir siendo de otro
        sitio si la cuenta no tiene ninguno de este."""
        self.ensure_one()
        if not user or user._is_public() or user._is_internal():
            return None
        user = user.sudo()
        holder = user._shrimp_account_company()
        # Perfil activo POR USUARIO (CR-12): el de este usuario, no el de la empresa.
        activo = holder.with_user(user).sudo()._shrimp_effective_type() if holder else False
        if not activo or self._shrimp_role_allowed_here(activo):
            return activo
        # Solo quien maneja los perfiles de la cuenta (su dueño o un miembro
        # del equipo): un técnico de campo no cambia el perfil de la empresa.
        if holder != user.partner_id and not user._shrimp_member():
            return activo
        nuevo = self._shrimp_site_fallback_role(user, preferred=preferred)
        if not nuevo:
            return activo
        try:
            holder.with_user(user).sudo()._shrimp_set_active_role(nuevo)
        except (UserError, ValidationError):
            return activo
        return nuevo

    # Botón principal de la cabecera para el visitante sin sesión: el alta
    # de cada plataforma.
    _SHRIMP_HEADER_CTA = {
        "main": {"label": "Registrarse", "url": "/register",
                 "class": "shrimp-header-cta--main"},
        "verifier": {"label": "Ser verificador", "url": "/register/verifier",
                     "class": "shrimp-header-cta--verifier"},
        # cl-header-cta: la clase que ya usa la portada de empaque.
        "copacker": {"label": "Registrar mi planta", "url": "/register/copacker",
                     "class": "shrimp-header-cta--copacker cl-header-cta"},
    }

    def _shrimp_header_cta(self):
        self.ensure_one()
        return self._SHRIMP_HEADER_CTA.get(self.shrimp_platform or "main")

    def _shrimp_login_landing(self, user):
        """Página de llegada tras el login de un usuario de PORTAL en este
        sitio, o None para dejar el destino de siempre (/my).

        En el marketplace: /my/dashboard si el perfil activo tiene panel. Cada
        plataforma (verificadores, empaque) lo amplía con su propia bandeja.
        Los usuarios internos no pasan por aquí (siguen al backend)."""
        self.ensure_one()
        if not user or user._is_public() or user._is_internal():
            return None
        # Cada sitio trabaja con SU perfil: si el activo es de otro sitio y
        # la cuenta tiene uno de este, se cambia antes de decidir la llegada.
        activo = self._shrimp_align_active_role(user)
        if (self.shrimp_platform or "main") == "main":
            if activo and not self._shrimp_role_allowed_here(activo):
                # Solo tiene perfiles de otro sitio (p. ej. una verificadora
                # que entra por el marketplace): se le explica y se le enlaza.
                return "/my/site-profile"
            # El perfil activo es POR USUARIO (CR-12): se lee como `user`.
            Dashboard = self.env["shrimp.dashboard"].with_user(user).sudo()
            role = Dashboard._panel_active_role(user.partner_id)
            if role and Dashboard._panel_supported(role):
                return "/my/dashboard"
        return None

    # ------------------------------------------------------------------
    # Idioma: las tres plataformas se sirven en español
    # ------------------------------------------------------------------
    _SHRIMP_IDIOMAS = ("es_EC", "es_419", "es_ES")

    @api.model
    def _shrimp_spanish_lang(self):
        Lang = self.env["res.lang"].sudo().with_context(active_test=True)
        for code in self._SHRIMP_IDIOMAS:
            lang = Lang.search([("code", "=", code)], limit=1)
            if lang:
                return lang
        return Lang.search([("code", "=like", "es_%")], limit=1)

    def _shrimp_set_spanish_default(self):
        """Pone el español (es_EC, si no es_419...) como idioma por defecto de
        estos sitios, si está instalado. No quita ningún otro idioma.

        Se usa al crear un sitio de la plataforma y en la migración; no en
        cada actualización, para no pisar la elección del administrador."""
        lang = self._shrimp_spanish_lang()
        if not lang:
            return False
        for sitio in self.sudo():
            vals = {}
            if lang not in sitio.language_ids:
                vals["language_ids"] = [(4, lang.id)]
            if sitio.default_lang_id != lang:
                vals["default_lang_id"] = lang.id
            if vals:
                if "language_ids" in vals:
                    sitio.write({"language_ids": vals.pop("language_ids")})
                if vals:
                    sitio.write(vals)
        return True

    # ------------------------------------------------------------------
    # Alineación de los menús del portal
    # ------------------------------------------------------------------
    # Los website.menu de cada sitio NO son los registros del módulo: Odoo
    # saca una copia al crear el sitio, y desde ese momento la copia deja de
    # seguir al XML. Esto corre en cada instalación/actualización y devuelve
    # nombre, URL y CLAVE (shrimp_key) a las copias del marketplace. Es
    # idempotente y no borra ni crea menús.
    # Barra del marketplace (models/website_navbar.py), en este orden:
    # (clave, nombre, url, secuencia, claves viejas, nombres por defecto
    #  viejos, urls viejas sin clave). Las urls viejas incluyen las rutas en
    #  español anteriores al cambio a inglés (19.0.1.15.0).
    _SHRIMP_NAV_MENUS = [
        ("panel", "Mi panel", "/my/dashboard", 15, (), (), ("/my/dashboard", "/mi-panel")),
        ("marketplace", "Marketplace", "/marketplace", 20, (), (), ("/marketplace",)),
        ("sell", "Vender", "/marketplace/products", 25, ("products",),
         ("Productos", "Mi panel"), ("/marketplace/products",)),
        ("buy", "Comprar", "/marketplace/purchases", 26, ("operations",),
         ("Operaciones",), ("/marketplace/purchases", "/marketplace/compras")),
        ("services", "Servicios", "/marketplace/copacking", 27, ("account",),
         ("Mi cuenta",), ("/marketplace/copacking", "/marketplace/empaque")),
    ]
    # Sitios (ids) a los que ya se les CREARON los menús que faltaban: si
    # después el cliente borra uno desde el editor, no se vuelve a crear...
    _SHRIMP_NAV_PARAM = "shrimp_marketplace.navbar_v2_sites"
    # ...salvo los OBLIGATORIOS: «Mi panel» es la entrada a /my/dashboard y
    # se recrea siempre que falte. En la base Pruebas los menús del sitio se
    # rehicieron (2026-10-12) después de que la marca anterior ya tenía el
    # sitio como «hecho», y «Mi panel» no volvió a crearse: el naupliero no
    # tenía cómo llegar a su panel desde la barra.
    _SHRIMP_NAV_REQUIRED = ("panel",)

    @api.model
    def _shrimp_alinear_menus_portal(self):
        """Deja la barra de cada sitio del marketplace en
        «Mi panel · Marketplace · Vender · Comprar · Servicios».

        Corre en cada instalación/actualización (data/menu_config.xml) y en
        la migración 19.0.1.9.0. Es idempotente y no borra nada:
        * un menú que ya tiene su clave nueva no se toca (ni nombre, ni URL,
          ni orden: lo que el cliente cambió desde el editor se respeta);
        * un menú con clave vieja (products / operations / account) o sin
          clave pero con la URL de siempre se convierte: clave nueva, URL y
          orden de la barra, y el nombre solo si era el nombre por defecto;
        * si falta un menú (Servicios) se crea UNA vez por sitio; «Mi panel»
          (obligatorio, _SHRIMP_NAV_REQUIRED) se crea siempre que falte.
        Los sitios de las otras plataformas (verificadores, empaque) tienen
        su propio árbol y no se tocan. Devuelve cuántos menús cambió/creó.
        """
        M = self.env["website.menu"].sudo()
        ICP = self.env["ir.config_parameter"].sudo()
        hechos = {x for x in (ICP.get_param(self._SHRIMP_NAV_PARAM) or "").split(",") if x}
        cambios = 0
        for sitio in self._shrimp_sitios_principales():
            raiz = sitio.menu_id or M.search(
                [("website_id", "=", sitio.id), ("parent_id", "=", False)], limit=1)
            if not raiz:
                continue
            menus = M.search([("website_id", "=", sitio.id), ("parent_id", "!=", False)])
            # Copias muy viejas: «Mi panel» apuntando a /my era el menú del
            # vendedor (hoy Vender).
            for viejo in menus.filtered(lambda m: m.name == "Mi panel" and m.url == "/my"
                                        and m.shrimp_key in (False, "products")):
                viejo.write({"url": "/marketplace/products", "shrimp_key": "products"})
                cambios += 1
            for clave, nombre, url, seq, claves_viejas, nombres_viejos, urls in self._SHRIMP_NAV_MENUS:
                if menus.filtered(lambda m, c=clave: m.shrimp_key == c):
                    continue
                viejo = menus.filtered(lambda m, cv=claves_viejas: m.shrimp_key in cv)[:1]
                if not viejo:
                    viejo = menus.filtered(
                        lambda m, u=urls: not m.shrimp_key and m.url in u
                        and m.parent_id == raiz)[:1]
                if viejo:
                    vals = {"shrimp_key": clave, "url": url, "sequence": seq}
                    if not viejo.name or viejo.name in nombres_viejos:
                        vals["name"] = nombre
                    viejo.write(vals)
                    cambios += 1
                elif str(sitio.id) not in hechos or clave in self._SHRIMP_NAV_REQUIRED:
                    menus |= M.create({"name": nombre, "url": url, "parent_id": raiz.id,
                                       "sequence": seq, "website_id": sitio.id,
                                       "shrimp_key": clave})
                    cambios += 1
            hechos.add(str(sitio.id))
        # Plantilla (sin sitio) de «Mi panel»: la copia Odoo al crear un
        # sitio nuevo. Se crea con website_id explícito para que NO se
        # duplique en los sitios que ya existen (verificadores, empaque).
        principal = self.env.ref("website.main_menu", raise_if_not_found=False)
        if principal:
            if not M.search_count([("website_id", "=", False), ("shrimp_key", "=", "panel")]):
                M.create({"name": "Mi panel", "url": "/my/dashboard", "parent_id": principal.id,
                          "sequence": 15, "website_id": False, "shrimp_key": "panel"})
                cambios += 1
            hechos.add("tpl")
        nuevo = ",".join(sorted(hechos))
        if nuevo != (ICP.get_param(self._SHRIMP_NAV_PARAM) or ""):
            ICP.set_param(self._SHRIMP_NAV_PARAM, nuevo)
        return cambios

    # ------------------------------------------------------------------
    # Árbol de menús de una plataforma (verificadores, empaque)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_build_site_menu(self, site, tree, marker_key):
        """Arma el árbol de menús de `site` si todavía no lo tiene.

        `tree`: [(nombre, url, secuencia, clave_o_False, [(nombre, url), ...])].
        `marker_key`: clave (shrimp_key) cuya presencia indica que el árbol ya
        está armado; así no se rehace en cada actualización y se respetan los
        cambios posteriores del cliente. Antes la marca era un NOMBRE.
        """
        M = self.env["website.menu"].sudo()
        if M.search_count([("website_id", "=", site.id), ("shrimp_key", "=", marker_key)]):
            return False
        raiz = M.search([("website_id", "=", site.id), ("parent_id", "=", False)], limit=1)
        if not raiz:
            return False
        # Si el árbol ya existía (de una versión que marcaba por nombre), solo
        # se le ponen las claves.
        hechos = False
        for nombre, url, _seq, clave, _hijos in tree:
            if not clave:
                continue
            existente = M.search([("website_id", "=", site.id), ("parent_id", "=", raiz.id),
                                  ("name", "=", nombre)], limit=1)
            if existente:
                existente.shrimp_key = clave
                hechos = True
        if hechos:
            return True
        # Árbol nuevo: se limpian los menús por defecto que Odoo copió al
        # crear el sitio.
        M.search([("id", "child_of", raiz.id), ("id", "!=", raiz.id)]).unlink()
        for nombre, url, seq, clave, hijos in tree:
            padre = M.create({"name": nombre, "url": url, "parent_id": raiz.id,
                              "sequence": seq, "website_id": site.id,
                              "shrimp_key": clave or False})
            for i, (hn, hu) in enumerate(hijos):
                M.create({"name": hn, "url": hu, "parent_id": padre.id,
                          "sequence": 10 + i * 10, "website_id": site.id})
        return True

    # ------------------------------------------------------------------
    # Marca (logo y favicon) por plataforma
    # ------------------------------------------------------------------
    # Los ficheros del módulo son la FUENTE DE LA VERDAD: al actualizar, el
    # sitio se resincroniza con ellos. Una sola implementación para las tres
    # plataformas (antes había tres copias del mismo código).
    _SHRIMP_MP_LOGO = "shrimp_marketplace/static/description/logo.png"
    _SHRIMP_MP_FAVICON = "shrimp_marketplace/static/description/icon_circle.png"

    @api.model
    def _shrimp_leer_imagen(self, ruta):
        try:
            with file_open(ruta, "rb") as f:
                return base64.b64encode(f.read())
        except Exception:
            return False

    @api.model
    def _shrimp_favicon_procesado(self, datos):
        """El favicon tal y como quedará guardado.

        website._handle_favicon() lo recorta a un ICO de 256x256 al escribir,
        así que comparar contra el PNG original daría siempre distinto y se
        reescribiría en cada actualización.
        """
        try:
            return base64.b64encode(image_process(
                base64.b64decode(datos), size=(256, 256),
                crop="center", output_format="ICO"))
        except Exception:
            return False

    @api.model
    def _shrimp_sync_brand(self, sitios, logo_path, favicon_path):
        """Sincroniza logo y favicon de `sitios` con los ficheros. Idempotente:
        solo escribe cuando hay diferencia. Devuelve cuántos sitios cambió."""
        logo = self._shrimp_leer_imagen(logo_path) if logo_path else False
        favicon = self._shrimp_leer_imagen(favicon_path) if favicon_path else False
        if not logo and not favicon:
            return 0
        favicon_final = self._shrimp_favicon_procesado(favicon) if favicon else False
        cambiados = 0
        for sitio in sitios:
            vals = {}
            if logo and sitio.logo != logo:
                vals["logo"] = logo
            if favicon_final and sitio.favicon != favicon_final:
                vals["favicon"] = favicon
            if vals:
                sitio.with_context(shrimp_brand_sync=True).write(vals)
                cambiados += 1
        return cambiados

    @api.model
    def _shrimp_ensure_marketplace_brand(self):
        """Logo y favicon de los sitios del marketplace. De paso, el sitio
        principal que aún tenga el nombre genérico de Odoo pasa a llamarse
        «CamaronMarket» (un nombre puesto por el cliente no se toca)."""
        principal = self._shrimp_main_site()
        if principal and principal.name in ("My Website", "Mi sitio web"):
            principal.name = "CamaronMarket"
        return self._shrimp_sync_brand(
            self._shrimp_sitios_principales(), self._SHRIMP_MP_LOGO, self._SHRIMP_MP_FAVICON)
