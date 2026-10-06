from . import models


def post_init_hook(env):
    """Tipo de identificación de los socios que ya existían (RUC, cédula o
    pasaporte según el número). Idempotente: solo toca los que no lo tienen
    bien puesto."""
    env["res.partner"].sudo().search([
        ("shrimp_user_type", "!=", False), ("vat", "!=", False),
    ])._shrimp_sync_identification_type()
