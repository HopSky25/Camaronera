from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # La comisión por unidad vendida se configura por cada Unidad de medida
    # (campo commission_cents en shrimp.uom), visible solo para el
    # administrador. En Ajustes solo hay un aviso con el enlace.

    # «Costo del chequeo» (shrimp_marketplace.check_fee) se retiró con el flujo
    # de solicitudes de chequeo (D-28): se compra directo o con verificación.
    # Texto libre: aquí sí sirve config_parameter (vacío = sin parámetro). Lo
    # leen las páginas de privacidad y el canal de derechos del titular
    # (shrimp_user_registry); sin él no se muestra ninguna dirección.
    shrimp_dpo_email = fields.Char(
        string="Correo del responsable de datos personales",
        config_parameter="shrimp.dpo_email",
        help="Correo al que llegan las solicitudes de derechos (LOPDP) y que se muestra en la "
        "Política de privacidad. Si se deja vacío, las páginas indican que se contacte al "
        "administrador de la plataforma.",
    )
    # Los parámetros de abajo NO usan config_parameter: se leen y guardan con
    # _shrimp_param_specs() para que un 0 se guarde como 0 (ver
    # models/shrimp_settings.py).
    shrimp_featured_min_reviews = fields.Integer(
        string="Mínimo de reseñas para destacados",
        default=10,
        help="Un laboratorio o naupliero solo sale en «destacados» (catálogo de "
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

    # Entregas y recepción (models/shrimp_transaction_reception.py).
    shrimp_reception_days = fields.Integer(
        string="Días para confirmar recepción",
        default=10,
        help="Días que tiene el comprador, desde el despacho o la fecha de entrega, para "
        "confirmar que recibió la compra (o reportar un problema). Si no hace nada, la "
        "recepción se confirma automáticamente y se da por cumplida la entrega del vendedor. "
        "Solo en las parejas de perfiles marcadas en la matriz.",
    )
    shrimp_reception_reminder_1 = fields.Integer(
        string="Primer recordatorio (días antes del vencimiento)",
        default=3,
        help="0 = sin este recordatorio.",
    )
    shrimp_reception_reminder_2 = fields.Integer(
        string="Segundo recordatorio (días antes del vencimiento)",
        default=1,
        help="0 = sin este recordatorio.",
    )
    shrimp_delivery_alert_days = fields.Integer(
        string="Días de anticipación para avisar entrega",
        default=2,
        help="Cuántos días antes de la fecha de entrega se avisa al vendedor (preparar el "
        "despacho) y al comprador (preparar la recepción). Además se avisa el mismo día y, si "
        "pasó la fecha sin despacho, se avisa del atraso. 0 = solo el día de la entrega y el "
        "atraso.",
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
            "shrimp_reception_days": ("shrimp_marketplace.reception_days", 10, 1, 365),
            "shrimp_reception_reminder_1": ("shrimp_marketplace.reception_reminder_1", 3, 0, 365),
            "shrimp_reception_reminder_2": ("shrimp_marketplace.reception_reminder_2", 1, 0, 365),
            "shrimp_delivery_alert_days": ("shrimp_marketplace.delivery_alert_days", 2, 0, 60),
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
        for fname in ("shrimp_reception_reminder_1", "shrimp_reception_reminder_2"):
            if fname in self._fields and self[fname] and \
                    self[fname] >= (self.shrimp_reception_days or 0):
                raise ValidationError(_(
                    "«%s» tiene que ser menor que los días para confirmar la recepción.")
                    % self._fields[fname].string)

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
