from odoo import fields, models


class ShrimpCorreoHilo(models.Model):
    """Conversación: el correo original y sus respuestas dentro de un buzón.

    Existe para poder agrupar la bandeja por conversación con un nombre
    legible (el asunto) en lugar del Message-ID. Lo crea y mantiene
    shrimp.correo; nadie lo edita a mano."""

    _name = "shrimp.correo.hilo"
    _description = "Conversación de correo"
    _order = "id desc"

    name = fields.Char("Asunto", required=True)
    clave = fields.Char("Clave", required=True, index=True,
                        help="Message-ID del primer correo de la conversación.")
    buzon_id = fields.Many2one("shrimp.buzon", "Buzón", required=True, ondelete="cascade", index=True)
    correo_ids = fields.One2many("shrimp.correo", "hilo_id", string="Correos")

    _clave_buzon_uniq = models.Constraint("UNIQUE(buzon_id, clave)",
                                          "La conversación ya existe en ese buzón.")
