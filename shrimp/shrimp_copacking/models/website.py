from odoo import api, fields, models


class Website(models.Model):
    _inherit = "website"

    # Alias de compatibilidad (una versión) de website.shrimp_platform ==
    # "copacker". Lo leen la portada, el registro y los tests.
    shrimp_is_copacker_site = fields.Boolean(
        string="Sitio de maquiladores",
        compute="_compute_shrimp_is_copacker_site",
        inverse="_inverse_shrimp_is_copacker_site",
        search="_search_shrimp_is_copacker_site",
        help="Obsoleto: usar «Plataforma». Marca este sitio como la plataforma "
             "de quienes prestan servicio de empaque: su portada es la del "
             "servicio y su menú, la bandeja de solicitudes.",
    )

    @api.depends("shrimp_platform")
    def _compute_shrimp_is_copacker_site(self):
        for rec in self:
            rec.shrimp_is_copacker_site = rec.shrimp_platform == "copacker"

    def _inverse_shrimp_is_copacker_site(self):
        for rec in self:
            if rec.shrimp_is_copacker_site:
                rec.shrimp_platform = "copacker"
            elif rec.shrimp_platform == "copacker":
                rec.shrimp_platform = "main"

    def _search_shrimp_is_copacker_site(self, operator, value):
        if operator in ("in", "not in"):
            positivo = True in value
            if operator == "not in":
                positivo = not positivo
        else:
            positivo = bool(value) if operator in ("=", "==") else not bool(value)
        return [("shrimp_platform", "=" if positivo else "!=", "copacker")]

    def _shrimp_copacker_site(self):
        return self.env["website"]._shrimp_platform_site("copacker")

    def _shrimp_login_landing(self, user):
        """En el sitio de empaque, el maquilador (perfil activo) entra a su
        bandeja tras el login, igual que al abrir la portada «/»."""
        destino = super()._shrimp_login_landing(user)
        if (not destino and self.shrimp_platform == "copacker" and user
                and not user._is_public() and not user._is_internal()
                and user.partner_id.sudo()._shrimp_role_holder().shrimp_user_type == "maquilador"):
            return "/copacker/inbox"
        return destino

    # En una instalacion nueva cada plataforma arranca con su dominio. En otro
    # entorno se cambia una vez desde Sitio web > Configuracion y el hook no lo
    # vuelve a tocar.
    _SHRIMP_DOMINIO_MAQUILADORES = "http://empaque.localhost:8069"

    _SHRIMP_MENU_MAQUILADOR = [
        ("Mi bandeja", "/copacker/inbox", 10, False, []),
        ("Trabajos", "#", 20, "copacker_jobs", [
            ("Solicitudes abiertas", "/copacker/inbox"),
            ("Órdenes en curso", "/copacker/orders"),
            ("Actas por firmar", "/copacker/orders?f=firmar"),
            ("Cerradas", "/copacker/orders?f=cerradas"),
        ]),
        ("Mis tarifas", "/copacker/tariffs", 30, False, []),
        ("Mi planta", "#", 40, "copacker_plant", [
            ("Perfil y capacidad", "/copacker/profile"),
            ("Liquidaciones", "/copacker/settlements"),
        ]),
    ]

    # ------------------------------------------------------------------
    # Autoconfiguracion. Se llama desde data/site_config.xml en cada
    # install/update: idempotente y no destructiva.
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_ensure_copack_config(self):
        W = self.env["website"].sudo()
        if not W.search([], limit=1):
            return False

        maq = W._shrimp_platform_site("copacker")
        if not maq:
            # Buscar por nombre entre los sitios del marketplace (nunca el de
            # verificadores ni el principal).
            principal = W._shrimp_main_site()
            maq = W.search([("name", "ilike", "empaque"), ("shrimp_platform", "=", "main"),
                            ("id", "!=", principal.id)], limit=1)
        if not maq:
            maq = W.create({"name": "CamaronMarket Empaque", "shrimp_platform": "copacker"})
            maq._shrimp_set_spanish_default()

        W.search([("id", "!=", maq.id), ("shrimp_platform", "=", "copacker")]).write(
            {"shrimp_platform": "main"})
        if maq.shrimp_platform != "copacker":
            maq.shrimp_platform = "copacker"
        if not maq.domain:
            maq.domain = self._SHRIMP_DOMINIO_MAQUILADORES

        W._shrimp_build_site_menu(maq, self._SHRIMP_MENU_MAQUILADOR, "copacker_jobs")
        return True


class WebsiteMenu(models.Model):
    _inherit = "website.menu"

    def _compute_visible(self):
        """Las entradas /copacker/* solo se pintan a un maquilador.

        Al visitante anonimo la ruta le pide login y a cualquier otro rol le
        devuelve 403: enseñarle el menu es ofrecerle caminos muertos. Se oculta
        por visibilidad (no se borran registros), asi que vale en bases ya
        creadas tras -u. Los disenadores del sitio lo siguen viendo para poder
        editarlo.
        """
        super()._compute_visible()
        user = self.env.user
        puede = None
        for menu in self:
            if not menu.is_visible:
                continue
            if not (menu.url or "").startswith(("/copacker/", "/maquilador/")):
                continue
            if puede is None:
                puede = (not user._is_public() and (
                    user.partner_id.sudo()._shrimp_can("provide_copack")
                    or user.has_group("website.group_website_designer")))
            if not puede:
                menu.is_visible = False
