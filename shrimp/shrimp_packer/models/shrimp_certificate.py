from odoo import fields, models


class ShrimpCertificate(models.Model):
    """Los certificados del catálogo se asignan por rol: el rol que agrega
    este módulo también puede tener los suyos (sus roles ya existían como
    tipo de usuario pero el catálogo no los ofrecía)."""

    _inherit = "shrimp.certificate"

    role = fields.Selection(
        selection_add=[("empacadora", "Empacadora")],
        ondelete={"empacadora": "set default"},
    )
