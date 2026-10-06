from odoo import api, models


class Website(models.Model):
    """Logo y favicon del sitio de empaque (paleta "Coral Camarón").

    Mismo criterio que la marca de CamaronMarket Verificadores en shrimp_verification: los
    ficheros viven en el módulo y son la fuente de la verdad, así que una
    instalación nueva arranca con la marca puesta y una base existente la
    recibe con -u. Solo toca el sitio marcado como de maquiladores: el
    marketplace y CamaronMarket Verificadores conservan la suya.

    Se usan las copias optimizadas de static/src/img (logo de 600 px, ~14 KB)
    y no los originales de static/description, que pesan 0,5-0,8 MB y se
    servirían enteros en cada página.
    """

    _inherit = "website"

    _SHRIMP_COPACK_LOGO = "shrimp_copacking/static/src/img/logo.png"
    _SHRIMP_COPACK_FAVICON = "shrimp_copacking/static/src/img/favicon-circulo.png"

    @api.model
    def _shrimp_ensure_copack_brand(self):
        """Sincroniza logo y favicon del sitio de empaque. Idempotente.

        Usa el sincronizador común de shrimp_marketplace."""
        return self._shrimp_sync_brand(
            self._shrimp_copacker_site(), self._SHRIMP_COPACK_LOGO,
            self._SHRIMP_COPACK_FAVICON)

    # Si el sitio de empaque se crea o se marca más tarde (desde la
    # configuración, otro hook...), recibe la marca en ese momento y no en la
    # siguiente actualización del módulo.
    @api.model_create_multi
    def create(self, vals_list):
        sitios = super().create(vals_list)
        if any(v.get("shrimp_is_copacker_site") or v.get("shrimp_platform") == "copacker"
               for v in vals_list):
            self._shrimp_ensure_copack_brand()
        return sitios

    def write(self, vals):
        res = super().write(vals)
        if (vals.get("shrimp_is_copacker_site") or vals.get("shrimp_platform") == "copacker") \
                and not self.env.context.get("shrimp_brand_sync"):
            self._shrimp_ensure_copack_brand()
        return res
