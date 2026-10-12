import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Cuentas, productos, analítica, diarios y términos de pago (puntos 2-5)
    para cada compañía con plan «ec». Idempotente: se puede volver a correr
    con env["shrimp.contabilidad.setup"]._setup_all()."""
    env["shrimp.contabilidad.setup"]._setup_all()


def uninstall_hook(env):
    """Devuelve «Informes» (account.menu_finance_reports) a su sitio original
    en Facturación/Contabilidad: este módulo lo colgó de su menú «Contabilidad»
    y, si no se devuelve, el borrado del menú raíz lo arrastraría."""
    informes = env.ref("account.menu_finance_reports", raise_if_not_found=False)
    raiz = env.ref("account.menu_finance", raise_if_not_found=False)
    if informes and raiz:
        informes.sudo().write({"parent_id": raiz.id, "sequence": 20})
        _logger.info("shrimp_contabilidad: «Informes» devuelto a account.menu_finance")
