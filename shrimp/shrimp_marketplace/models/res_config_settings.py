from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # La comisión por unidad vendida se configura por cada Unidad de medida
    # (campo commission_cents en shrimp.uom), visible solo para el
    # administrador. En Ajustes solo hay un aviso con el enlace.

    shrimp_check_fee = fields.Float(
        string="Costo del chequeo",
        config_parameter="shrimp_marketplace.check_fee",
        default=0.0,
        help="Valor fijo que se cobra al comprador por enviar un equipo a verificar "
        "el producto cuando solicita un chequeo. Se cobra además del valor del producto.",
    )
    # Los parámetros de abajo NO usan config_parameter: se leen y guardan con
    # _shrimp_param_specs() para que un 0 se guarde como 0 (ver
    # models/shrimp_settings.py).
    shrimp_featured_min_reviews = fields.Integer(
        string="Mínimo de reseñas para destacados",
        default=10,
        help="Un laboratorio o semillero solo sale en «destacados» (catálogo de "
        "larva/nauplio y página de inicio) si tiene al menos estas reseñas como "
        "vendedor. Si no llegan suficientes, se muestran solo los que cumplen "
        "(o se oculta la sección si no cumple ninguno).",
    )
    shrimp_cert_alert_days = fields.Integer(
        string="Aviso de certificados por vencer (días)",
        default=30,
        help="Mi panel avisa de los certificados del socio que vencen dentro de "
        "estos días.",
    )
    shrimp_charge_max_attempts = fields.Integer(
        string="Reintentos automáticos de facturación",
        default=5,
        help="Cuántas veces intenta el proceso automático emitir la factura de un "
        "cobro de la plataforma antes de dejarlo para revisión manual (botón "
        "«Reintentar» en el cobro).",
    )

    # ------------------------------------------------------------------
    # Parámetros de negocio guardados a mano (también el 0)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_param_specs(self):
        """{campo: (clave ir.config_parameter, valor por defecto, mínimo, máximo)}.
        Cada módulo suma los suyos con super()."""
        return {
            "shrimp_featured_min_reviews": ("shrimp_marketplace.featured_min_reviews", 10, 0, None),
            "shrimp_cert_alert_days": ("shrimp_marketplace.cert_alert_days", 30, 1, 3650),
            "shrimp_charge_max_attempts": ("shrimp_marketplace.charge_max_attempts", 5, 1, 100),
        }

    @api.model
    def get_values(self):
        res = super().get_values()
        S = self.env["shrimp.settings"]
        for fname, (key, default, minimo, maximo) in self._shrimp_param_specs().items():
            tipo = self._fields[fname].type
            if tipo == "boolean":
                res[fname] = S.get_bool(key, default)
            elif tipo == "integer":
                res[fname] = S.get_int(key, default, minimum=minimo, maximum=maximo)
            else:
                res[fname] = S.get_float(key, default, minimum=minimo, maximum=maximo)
        return res

    def _shrimp_check_params(self):
        """Rangos de cada parámetro y reglas entre ellos (cada módulo amplía)."""
        self.ensure_one()
        for fname, (_key, _d, minimo, maximo) in self._shrimp_param_specs().items():
            field = self._fields[fname]
            if field.type == "boolean":
                continue
            valor = self[fname] or 0
            if (minimo is not None and valor < minimo) or (maximo is not None and valor > maximo):
                if maximo is None:
                    rango = _("mayor o igual a %s") % minimo
                else:
                    rango = _("entre %(a)s y %(b)s") % {"a": minimo, "b": maximo}
                raise ValidationError(_("«%(campo)s» tiene que estar %(rango)s.") % {
                    "campo": field.string, "rango": rango})

    def set_values(self):
        for rec in self:
            rec._shrimp_check_params()
        super().set_values()
        ICP = self.env["ir.config_parameter"].sudo()
        for fname, (key, _default, _mi, _ma) in self._shrimp_param_specs().items():
            tipo = self._fields[fname].type
            valor = self[fname]
            if tipo == "boolean":
                nuevo = "True" if valor else "False"
            elif tipo == "integer":
                nuevo = str(int(valor or 0))
            else:
                nuevo = repr(float(valor or 0.0))
            actual = ICP.get_param(key)
            if actual is not False and actual is not None:
                # «250.00» y 250.0 son lo mismo: no se reescribe sin cambio.
                try:
                    if tipo != "boolean" and float(actual) == float(nuevo):
                        continue
                except (TypeError, ValueError):
                    pass
                if actual == nuevo:
                    continue
            ICP.set_param(key, nuevo)
