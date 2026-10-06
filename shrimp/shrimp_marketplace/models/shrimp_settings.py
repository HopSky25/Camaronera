"""Parámetros de negocio de la plataforma (Ajustes › CamaronMarket).

Todos los valores que el administrador puede ajustar viven en
ir.config_parameter y se leen SIEMPRE con estos getters, que caen al valor de
siempre si el parámetro no existe o trae basura. Así una base que nunca abrió
Ajustes se comporta exactamente como antes.

Ojo con los campos «config_parameter» estándar de res.config.settings: al
guardar un 0 BORRAN el parámetro, y el getter volvía al valor por defecto
(poner «honorario mínimo = 0» dejaba otra vez 300). Por eso los parámetros
numéricos de la plataforma se declaran en _shrimp_param_specs() y se guardan
siempre de forma explícita, también el 0.
"""
from odoo import api, models

VERDADERO = ("1", "true", "yes", "si", "sí", "on")


class ShrimpSettings(models.AbstractModel):
    _name = "shrimp.settings"
    _description = "Parámetros de negocio de CamaronMarket"

    @api.model
    def _raw(self, key):
        return self.env["ir.config_parameter"].sudo().get_param(key)

    @api.model
    def get_float(self, key, default, minimum=None, maximum=None):
        raw = self._raw(key)
        if raw in (None, False, ""):
            return default
        try:
            valor = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            return default
        if (minimum is not None and valor < minimum) or (maximum is not None and valor > maximum):
            return default
        return valor

    @api.model
    def get_int(self, key, default, minimum=None, maximum=None):
        valor = self.get_float(key, default, minimum=minimum, maximum=maximum)
        try:
            return int(round(valor))
        except (TypeError, ValueError):
            return default

    @api.model
    def get_bool(self, key, default=False):
        raw = self._raw(key)
        if raw in (None, False, ""):
            return default
        return str(raw).strip().lower() in VERDADERO
