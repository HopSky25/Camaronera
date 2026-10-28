from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # Con varios buzones por persona, «Redactar» abre con este. Sin él saldría el
    # primero de la lista, y Cristhian (Presidencia) terminaba escribiendo como
    # Gerencia General.
    shrimp_buzon_predeterminado_id = fields.Many2one(
        "shrimp.buzon", "Buzón de correo predeterminado",
        help="Buzón con el que se abre un correo nuevo. Debe ser uno de los buzones del usuario.")
