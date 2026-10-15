"""Metadatos de las fotos de campo que sube la app (contrato de mejoras, C4).

La foto en sí va al mismo sitio que la del portal: un ir.attachment privado
de la verificación, con token de acceso, enlazado en
shrimp.verification.photo_ids. La app manda además cuándo se tomó, dónde y
una nota; el portal no los tiene, así que se guardan aquí. La ubicación es
solo para usuarios internos (la API no la devuelve).
"""

from odoo import fields, models


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    shrimp_taken_at = fields.Datetime(string="Foto tomada el")
    shrimp_note = fields.Char(string="Nota de la foto")
    shrimp_latitude = fields.Float(string="Latitud de la foto", digits=(10, 7),
                                   groups="base.group_user")
    shrimp_longitude = fields.Float(string="Longitud de la foto", digits=(10, 7),
                                    groups="base.group_user")
    shrimp_uploaded_by_id = fields.Many2one(
        "res.partner", string="Foto subida por", ondelete="set null")
