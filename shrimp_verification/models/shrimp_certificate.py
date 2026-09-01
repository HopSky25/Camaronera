from odoo import fields, models


class ShrimpCertificate(models.Model):
    _name = "shrimp.certificate"
    _inherit = "shrimp.certificate"

    # El catálogo de certificados debe poder marcar acreditaciones propias del
    # verificador (p. ej. habilitación de laboratorio de análisis), no solo las
    # de los tres roles productivos.
    role = fields.Selection(
        selection_add=[("verificador", "Verificador")],
        ondelete={"verificador": "set default"},
    )
