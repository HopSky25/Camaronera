import secrets

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpApiKey(models.Model):
    _name = "shrimp.api.key"
    _inherit = "shrimp.uuid.mixin"
    _description = "Clave de API del Marketplace"
    _order = "create_date desc"

    name = fields.Char(string="Etiqueta", required=True, help="Nombre para identificar esta clave (p. ej. 'Integración ERP').")
    key = fields.Char(string="Clave", required=True, index=True, copy=False, readonly=True,
                      default=lambda self: self._generate_key())
    user_id = fields.Many2one("res.users", string="Usuario propietario", required=True,
                              default=lambda self: self.env.user,
                              help="Las operaciones de la API se ejecutan en nombre de este usuario.")
    partner_id = fields.Many2one("res.partner", related="user_id.partner_id", store=True, string="Contacto")
    scope = fields.Selection(
        [
            ("read", "Solo lectura"),
            ("write", "Lectura y escritura"),
            ("admin", "Administrador"),
        ],
        string="Nivel de seguridad",
        default="read",
        required=True,
        help="read: solo consultar. write: crear/editar/borrar sus propios productos. "
             "admin: operar sobre productos de cualquier vendedor.",
    )
    active = fields.Boolean(default=True)
    last_used = fields.Datetime(string="Último uso", readonly=True)
    call_count = fields.Integer(string="Nº de llamadas", readonly=True, default=0)

    _sql_constraints = [
        ("key_unique", "unique(key)", "La clave de API debe ser única."),
    ]

    @api.model
    def _generate_key(self):
        return secrets.token_urlsafe(32)

    def action_regenerate_key(self):
        for rec in self:
            rec.key = self._generate_key()
            rec.call_count = 0
        return True

    @api.model
    def _authenticate(self, raw_key):
        """Devuelve el registro de clave válido para `raw_key` o False."""
        if not raw_key:
            return False
        rec = self.sudo().search([("key", "=", raw_key), ("active", "=", True)], limit=1)
        return rec or False

    def _touch(self):
        """Marca la clave como usada (auditoría ligera)."""
        self.sudo().write({
            "last_used": fields.Datetime.now(),
            "call_count": (self.call_count or 0) + 1,
        })

    def _can(self, operation):
        """operation ∈ {'read','write','admin'}. Comprueba el nivel de seguridad."""
        self.ensure_one()
        order = {"read": 0, "write": 1, "admin": 2}
        return order.get(self.scope, 0) >= order.get(operation, 99)
