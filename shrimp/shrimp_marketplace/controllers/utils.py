"""Ayudas comunes de los controladores de todas las plataformas.

Antes cada módulo tenía su copia de «¿en qué sitio estoy?», de convertir el
texto del formulario a número sin reventar y de «el socio del usuario»: cinco
versiones de _entero/_decimal/_to_float y una de _partner() por controlador.
Viven aquí (y en shrimp_user_registry las que no dependen del sitio).
"""
from odoo.http import request

from odoo.addons.shrimp_user_registry.controllers.main import (  # noqa: F401
    current_partner, to_int, to_number)


def site_platform():
    """Plataforma del sitio de la petición: 'main', 'verifier' o 'copacker'."""
    web = getattr(request, "website", False)
    return (web.sudo().shrimp_platform or "main") if web else "main"


def is_platform(code):
    """True si la petición entra por el sitio de esa plataforma."""
    return site_platform() == code


def error_text(error):
    """El mensaje legible de una excepción de validación."""
    return error.args[0] if getattr(error, "args", None) else str(error)


class ShrimpPortalMixin:
    """Mezcla para controladores del portal: el socio del usuario actual."""

    def _partner(self):
        return current_partner()
