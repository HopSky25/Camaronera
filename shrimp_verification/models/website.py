from odoo import fields, models


class Website(models.Model):
    _inherit = "website"

    # Marcar el sitio con un campo, y no por id o por nombre, permite moverlo o
    # renombrarlo sin romper nada.
    shrimp_is_verifier_site = fields.Boolean(
        string="Sitio de verificadores",
        help="Marca este sitio como la plataforma de los verificadores: su "
             "portada es la de verificación y las rutas del marketplace "
             "redirigen al sitio principal.",
    )

    def _shrimp_verifier_site(self):
        return self.sudo().search([("shrimp_is_verifier_site", "=", True)], limit=1)

    def _shrimp_main_site(self):
        return self.sudo().search([("shrimp_is_verifier_site", "=", False)], limit=1)
