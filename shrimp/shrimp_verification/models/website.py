from odoo import api, fields, models


class Website(models.Model):
    _inherit = "website"

    # Alias de compatibilidad (una versión) de website.shrimp_platform ==
    # "verifier". Lo siguen leyendo plantillas, tests y la portada de empaque.
    shrimp_is_verifier_site = fields.Boolean(
        string="Sitio de verificadores",
        compute="_compute_shrimp_is_verifier_site",
        inverse="_inverse_shrimp_is_verifier_site",
        search="_search_shrimp_is_verifier_site",
        help="Obsoleto: usar «Plataforma». Marca este sitio como CamaronMarket Verificadores, la "
             "plataforma de los verificadores: su portada, su marca y su menú son "
             "los del verificador y su registro solo admite verificadores. Las "
             "rutas del verificador (/verifier/...) que entran por otro sitio "
             "se redirigen aquí si el sitio tiene dominio.",
    )

    @api.depends("shrimp_platform")
    def _compute_shrimp_is_verifier_site(self):
        for rec in self:
            rec.shrimp_is_verifier_site = rec.shrimp_platform == "verifier"

    def _inverse_shrimp_is_verifier_site(self):
        for rec in self:
            if rec.shrimp_is_verifier_site:
                rec.shrimp_platform = "verifier"
            elif rec.shrimp_platform == "verifier":
                rec.shrimp_platform = "main"

    def _search_shrimp_is_verifier_site(self, operator, value):
        if operator in ("in", "not in"):
            positivo = True in value
            if operator == "not in":
                positivo = not positivo
        else:
            positivo = bool(value) if operator in ("=", "==") else not bool(value)
        return [("shrimp_platform", "=" if positivo else "!=", "verifier")]

    def _shrimp_verifier_site(self):
        return self.env["website"]._shrimp_platform_site("verifier")

    def _shrimp_login_landing(self, user):
        """En el sitio de verificadores, el verificador (o su técnico) entra a
        su bandeja tras el login en vez de a /my."""
        destino = super()._shrimp_login_landing(user)
        if (not destino and self.shrimp_platform == "verifier" and user
                and not user._is_public() and not user._is_internal()
                and user.partner_id.sudo().shrimp_verifier_company()):
            return "/verifier/inbox"
        return destino

    # ------------------------------------------------------------------
    # Autoconfiguración de la plataforma de verificadores (para "solo
    # instalar"). Se llama desde data/site_config.xml en cada install/update.
    # Es IDEMPOTENTE y NO DESTRUCTIVA: solo crea lo que falta y no pisa la
    # configuración del cliente (dominios ya puestos, menús ya armados).
    # ------------------------------------------------------------------
    # Dominios por defecto para una instalación nueva. En otro entorno se
    # cambian una sola vez desde Sitio web › Configuración; el hook no los
    # vuelve a tocar si ya tienen valor.
    _SHRIMP_DOMINIO_PRINCIPAL = "http://localhost:8069"
    _SHRIMP_DOMINIO_VERIFICADORES = "http://verificadores.localhost:8069"

    # Árbol de menús del sitio de verificadores. Los desplegables con estilo
    # (navbar_dropdown_inherit) los reconocen por su CLAVE, no por el nombre.
    _SHRIMP_MENU_VERIFICADOR = [
        ("Mi bandeja", "/verifier/inbox", 10, False, []),
        ("Verificaciones", "#", 20, "verifier_jobs", [
            ("Verificaciones abiertas", "/verifier/inbox?state=open"),
            ("En campo", "/verifier/inbox?state=in_field"),
            ("Por dictaminar", "/verifier/inbox?state=done"),
            ("Ya verificadas", "/verifier/inbox?state=approved"),
            ("Todas", "/verifier/inbox"),
        ]),
        ("Mi empresa", "#", 30, "verifier_company", [
            ("Perfil y cuenta bancaria", "/verifier/profile"),
            ("Mi equipo", "/verifier/technicians"),
            ("Mi acreditación", "/marketplace/my-certificates"),
            ("Mis reportes", "/verifier/reports"),
        ]),
    ]

    @api.model
    def _shrimp_ensure_config(self):
        W = self.env["website"].sudo()
        sitios = W.search([], order="id")
        if not sitios:
            return False

        # 1) Identificar (o crear) el sitio de verificadores.
        verif = W._shrimp_platform_site("verifier")
        if not verif:
            # NUNCA robar el sitio de otra plataforma (empaque): se elige solo
            # entre los del marketplace, y nunca el principal (el de id más
            # bajo), que es el que sirve la compraventa.
            principales = W.search([("shrimp_platform", "=", "main")], order="id")
            candidatos = principales[1:]
            verif = candidatos.filtered(
                lambda s: "verificador" in (s.name or "").lower()
                or "verimar" in (s.name or "").lower())[:1]
            if not verif:
                if candidatos:
                    verif = candidatos[-1:]
                else:
                    verif = W.create({"name": "CamaronMarket Verificadores", "shrimp_platform": "verifier"})
                    verif._shrimp_set_spanish_default()
            # Solo en una instalación NUEVA (el sitio aún no era de
            # verificadores): el nombre genérico de Odoo pasa a la marca. Una
            # base existente ya tiene la plataforma puesta por la migración y
            # conserva el nombre que le dio el usuario.
            if verif.name in ("My Website 2", "Mi sitio web 2"):
                verif.name = "CamaronMarket Verificadores"

        # 2) Plataforma: uno y solo uno.
        W.search([("id", "!=", verif.id), ("shrimp_platform", "=", "verifier")]).write(
            {"shrimp_platform": "main"})
        if verif.shrimp_platform != "verifier":
            verif.shrimp_platform = "verifier"
        # La marca es «CamaronMarket Verificadores»: también en bases ya
        # creadas, el nombre genérico de Odoo o el de la marca anterior
        # (Verimar) se renombra. Un nombre puesto por el cliente no se toca.
        if verif.name in ("My Website 2", "Mi sitio web 2", "Verimar"):
            verif.name = "CamaronMarket Verificadores"

        # 3) Dominios: solo si faltan (no pisar la config del cliente).
        principal = W._shrimp_main_site()
        if principal and not principal.domain:
            principal.domain = self._SHRIMP_DOMINIO_PRINCIPAL
        if not verif.domain:
            verif.domain = self._SHRIMP_DOMINIO_VERIFICADORES

        # 4) Menú propio del verificador.
        W._shrimp_build_site_menu(verif, self._SHRIMP_MENU_VERIFICADOR, "verifier_jobs")

        # 5) Un tema a medio instalar tumba el bundle CSS del sitio: quitarlo.
        for s in W.search([]):
            if s.theme_id and s.theme_id.state != "installed":
                s.theme_id = False
        return True

    # ------------------------------------------------------------------
    # Marca CamaronMarket Verificadores (logo y favicon) del sitio de verificadores
    # ------------------------------------------------------------------
    # Los archivos viven en el módulo: así viajan con el código y una
    # instalación nueva arranca con la marca puesta. El sincronizador es el
    # común (website._shrimp_sync_brand, en shrimp_marketplace).
    _SHRIMP_LOGO = "shrimp_verification/static/description/logo.png"
    _SHRIMP_FAVICON = "shrimp_verification/static/description/icon_circle.png"

    @api.model
    def _shrimp_ensure_brand(self):
        return self._shrimp_sync_brand(
            self._shrimp_verifier_site(), self._SHRIMP_LOGO, self._SHRIMP_FAVICON)


class WebsiteMenu(models.Model):
    _inherit = "website.menu"

    # Entradas del menú del sitio de verificadores que solo puede usar la
    # cuenta de la empresa (el técnico recibe 403 en el controlador). Se
    # ocultan por visibilidad y no borrando el registro: así vale para las
    # bases ya creadas y el menú sigue siendo editable desde el website.
    _SHRIMP_URLS_SOLO_ADMIN_VERIFICADOR = ("/verifier/technicians", "/verificador/tecnicos")

    def _compute_visible(self):
        super()._compute_visible()
        partner = self.env.user.partner_id
        es_admin = None
        for menu in self:
            if not menu.is_visible:
                continue
            url = (menu.url or "").split("?", 1)[0].rstrip("/")
            if url not in self._SHRIMP_URLS_SOLO_ADMIN_VERIFICADOR:
                continue
            if es_admin is None:
                es_admin = bool(partner) and partner.sudo().shrimp_is_verifier_admin()
            if not es_admin:
                menu.is_visible = False
