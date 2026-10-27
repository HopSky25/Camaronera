import secrets


def post_init_hook(env):
    """Crea la clave del webhook si no existe. Nunca se escribe en el repositorio
    ni en el log: se copia al Worker de Cloudflare directamente desde el servidor."""
    icp = env["ir.config_parameter"].sudo()
    if not icp.get_param("shrimp_correo.token"):
        icp.set_param("shrimp_correo.token", secrets.token_urlsafe(32))
