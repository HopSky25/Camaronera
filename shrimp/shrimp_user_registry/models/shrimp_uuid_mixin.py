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

    # Odoo 19 ignora _sql_constraints EN SILENCIO —solo deja un
    # WARNING en el arranque— y la restriccion no llega nunca a
    # PostgreSQL. Se comprobo contra pg_constraint: ninguna de las
    # unicidades declaradas asi existia en la base.
    _uuid_ref_unique = models.Constraint(
        "unique(uuid_ref)",
        "El código de referencia debe ser único.",
    )

    def _auto_init(self):
        """Rellena el código de los registros que no lo tengan.

        Cuando un modelo existente pasa a heredar este mixin, la columna nace
        vacía para las filas que ya había, y las rutas (que resuelven SOLO por
        uuid_ref) les responderían 404. Se rellena aquí, en cada instalación y
        actualización, para que cualquier modelo que adopte el mixin quede
        cubierto sin un script de migración propio. Es idempotente: solo toca
        filas con uuid_ref NULL.
        """
        res = super()._auto_init()
        if not self._abstract and self._auto and self._table:
            cr = self.env.cr
            cr.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'uuid_ref'",
                (self._table,))
            if cr.fetchone():
                cr.execute(
                    'UPDATE "%s" SET uuid_ref = gen_random_uuid()::text '
                    'WHERE uuid_ref IS NULL' % self._table)
        return res

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
