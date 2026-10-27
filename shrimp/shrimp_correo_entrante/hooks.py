import logging
import secrets

_logger = logging.getLogger(__name__)


def asegurar_token(env):
    """Crea la clave del webhook si no existe. Nunca se escribe en el repositorio
    ni en el log: se copia al Worker de Cloudflare directamente desde el servidor."""
    icp = env["ir.config_parameter"].sudo()
    if not icp.get_param("shrimp_correo.token"):
        icp.set_param("shrimp_correo.token", secrets.token_urlsafe(32))


def liberar_alias_de_buzones(env):
    """Al instalarse, crm le pone al equipo de ventas el alias «info»: con un
    dominio de alias configurado, cada correo a info@ crearía una oportunidad
    sola. Las direcciones de los buzones las lee la gente en la bandeja y la
    oportunidad se crea a mano («Crear oportunidad»), así que se quitan los
    alias de oportunidades que choquen con un buzón."""
    if "crm.lead" not in env:
        return
    locales = {d.split("@")[0] for d in env["shrimp.buzon"].sudo().with_context(
        active_test=False).search([]).mapped("direccion") if d}
    if not locales:
        return
    alias = env["mail.alias"].sudo().search([
        ("alias_name", "in", list(locales)),
        ("alias_model_id.model", "=", "crm.lead"),
    ])
    for a in alias:
        _logger.info("Correo: se quita el alias «%s» de oportunidades (es un buzón de la bandeja)",
                     a.alias_name)
    if alias:
        alias.write({"alias_name": False})


def post_init_hook(env):
    asegurar_token(env)
    liberar_alias_de_buzones(env)
