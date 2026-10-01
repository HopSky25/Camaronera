from odoo import api, fields, models


class Website(models.Model):
    _inherit = "website"

    # Marcar el sitio con un campo, y no por id o por nombre, permite moverlo o
    # renombrarlo sin romper nada. Mismo criterio que el sitio de verificadores.
    shrimp_is_copacker_site = fields.Boolean(
        string="Sitio de maquiladores",
        help="Marca este sitio como la plataforma de quienes prestan servicio "
             "de empaque: su portada es la bandeja de solicitudes.",
    )

    def _shrimp_copacker_site(self):
        return self.sudo().search([("shrimp_is_copacker_site", "=", True)], limit=1)

    # En una instalacion nueva cada plataforma arranca con su dominio. En otro
    # entorno se cambia una vez desde Sitio web > Configuracion y el hook no lo
    # vuelve a tocar.
    _SHRIMP_DOMINIO_MAQUILADORES = "http://empaque.localhost:8069"

    _SHRIMP_MENU_MAQUILADOR = [
        ("Mi bandeja", "/maquilador/bandeja", 10, []),
        ("Trabajos", "#", 20, [
            ("Solicitudes abiertas", "/maquilador/bandeja"),
            ("Órdenes en curso", "/maquilador/ordenes"),
            ("Actas por firmar", "/maquilador/ordenes?f=firmar"),
            ("Cerradas", "/maquilador/ordenes?f=cerradas"),
        ]),
        ("Mis tarifas", "/maquilador/tarifas", 30, []),
        ("Mi planta", "#", 40, [
            ("Perfil y capacidad", "/maquilador/perfil"),
            ("Liquidaciones", "/maquilador/liquidaciones"),
        ]),
    ]

    # ------------------------------------------------------------------
    # Autoconfiguracion. Se llama desde data/site_config.xml en cada
    # install/update: idempotente y no destructiva.
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_ensure_copack_config(self):
        W = self.env["website"].sudo()
        sitios = W.search([], order="id")
        if not sitios:
            return False

        maq = W.search([("shrimp_is_copacker_site", "=", True)], limit=1)
        if not maq:
            # Buscar por nombre, pero NUNCA robar el sitio de verificadores:
            # si se lo quitara, esa plataforma se quedaria sin portada.
            candidatos = W.search([("name", "ilike", "empaque")])
            if "shrimp_is_verifier_site" in W._fields:
                candidatos = candidatos.filtered(lambda s: not s.shrimp_is_verifier_site)
            maq = candidatos[:1]
        if not maq:
            maq = W.create({"name": "CamaronMkt Empaque"})

        W.search([("id", "!=", maq.id)]).write({"shrimp_is_copacker_site": False})
        if not maq.shrimp_is_copacker_site:
            maq.shrimp_is_copacker_site = True
        if not maq.domain:
            maq.domain = self._SHRIMP_DOMINIO_MAQUILADORES

        self._shrimp_build_copacker_menu(maq)
        return True

    def _shrimp_build_copacker_menu(self, maq):
        M = self.env["website.menu"].sudo()
        # Si ya esta armado no se toca: asi se respetan los cambios que haga
        # despues el cliente y no se rehace en cada actualizacion.
        if M.search_count([("website_id", "=", maq.id), ("name", "=", "Mis tarifas")]):
            return
        raiz = M.search([("website_id", "=", maq.id), ("parent_id", "=", False)], limit=1)
        if not raiz:
            return
        M.search([("id", "child_of", raiz.id), ("id", "!=", raiz.id)]).unlink()

        def crear(nombre, url, parent, seq):
            return M.create({"name": nombre, "url": url, "parent_id": parent,
                             "sequence": seq, "website_id": maq.id})

        for nombre, url, seq, hijos in self._SHRIMP_MENU_MAQUILADOR:
            padre = crear(nombre, url, raiz.id, seq)
            for i, (hn, hu) in enumerate(hijos):
                crear(hn, hu, padre.id, 10 + i * 10)
