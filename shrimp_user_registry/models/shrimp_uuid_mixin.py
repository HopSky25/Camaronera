import uuid

from odoo import api, fields, models


class ShrimpUuidMixin(models.AbstractModel):
    """Mixin que agrega a cada registro un código de referencia alfanumérico
    (estilo UUID), independiente del id entero nativo de Odoo. Se genera
    automáticamente al crear y es único. Se usa como identificador público en
    URLs y en la API, para no exponer los ids secuenciales."""

    _name = "shrimp.uuid.mixin"
    _description = "Código de referencia UUID"

    uuid_ref = fields.Char(
        string="Código de referencia",
        index=True,
        copy=False,
        readonly=True,
        help="Identificador público alfanumérico del registro (estilo UUID).",
    )

    _sql_constraints = [
        ("uuid_ref_unique", "unique(uuid_ref)",
         "El código de referencia debe ser único."),
    ]

    @api.model
    def _generate_uuid_ref(self):
        return str(uuid.uuid4())

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("uuid_ref"):
                vals["uuid_ref"] = self._generate_uuid_ref()
        return super().create(vals_list)

    @api.model
    def browse_by_uuid(self, uuid_ref):
        """Devuelve el registro con ese código de referencia (o vacío)."""
        if not uuid_ref:
            return self.browse()
        return self.search([("uuid_ref", "=", uuid_ref)], limit=1)

    @api.model
    def resolve_ref(self, token):
        """Resuelve un token de URL/API a un registro EXCLUSIVAMENTE por su
        código alfanumérico (uuid_ref). No se aceptan ids enteros nativos: un
        id como '109' devuelve un recordset vacío (la ruta responde 404)."""
        if token is None:
            return self.browse()
        token = str(token).strip()
        if not token:
            return self.browse()
        return self.search([("uuid_ref", "=", token)], limit=1)
