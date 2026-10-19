from . import models
from . import controllers


def pre_init_hook(env):
    # Base reinstalada: carga la demo que le falte a las dependencias shrimp
    # antes de que la demo de este módulo la necesite (ver demo_utils).
    from odoo.addons.shrimp_user_registry.demo_utils import cargar_demo_pendiente
    cargar_demo_pendiente(env, "shrimp_packer")
