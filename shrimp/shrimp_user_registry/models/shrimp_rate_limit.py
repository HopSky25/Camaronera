from datetime import timedelta

from odoo import api, fields, models


class ShrimpRateLimit(models.Model):
    """Intentos recientes por clave (p. ej. "signup:<ip>").

    Limitador deliberadamente simple: una fila por intento y un conteo en la
    ventana. Sin dependencias externas (Redis, nginx) para que funcione igual
    en desarrollo y en producción; el proxy puede añadir su propio límite
    encima (ver la carpeta de hardening).
    """

    _name = "shrimp.rate.limit"
    _description = "Limitador de frecuencia (intentos)"
    _log_access = False
    _order = "ts desc"

    key = fields.Char(required=True, index=True)
    ts = fields.Datetime(required=True, index=True, default=fields.Datetime.now)

    @api.model
    def hit(self, key, limit, window_seconds):
        """Registra un intento para `key`. Devuelve False si ya se alcanzó el
        límite dentro de la ventana (y en ese caso no suma otro intento)."""
        now = fields.Datetime.now()
        desde = now - timedelta(seconds=int(window_seconds))
        model = self.sudo()
        if model.search_count([("key", "=", key), ("ts", ">=", desde)]) >= int(limit):
            return False
        model.create({"key": key, "ts": now})
        return True

    @api.autovacuum
    def _gc_old_attempts(self):
        limite = fields.Datetime.now() - timedelta(days=2)
        self.sudo().search([("ts", "<", limite)]).unlink()
