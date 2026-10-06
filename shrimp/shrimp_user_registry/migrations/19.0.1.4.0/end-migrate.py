"""19.0.1.4.0 — rutas web en inglés.

Las rutas del portal pasaron del español al inglés (/registro -> /register,
/verificador/bandeja -> /verifier/inbox, /marketplace/compras ->
/marketplace/purchases...). Las rutas viejas siguen funcionando como 301/308
(ir.http._serve_fallback), pero las URL GUARDADAS en la base no siguen al
código:

* website.menu: las copias de cada sitio (y las plantillas sin sitio) se
  crearon con la URL vieja; se reescriben conservando ?query y #fragmento.
* website.rewrite.url_to (hoy ninguna) se reescribe igual.
* Vistas COW (ir.ui.view con website_id) con rutas viejas: solo se avisan en
  el log (las edita una persona desde el editor).
* El sitemap cacheado (ir.attachment /sitemap-*) se borra para que se
  regenere con las URL nuevas.

Corre como `end-` para que la tabla de rutas viejas (que cada módulo shrimp
extiende en su ir.http) esté completa. Idempotente: una ruta nueva nunca
coincide con un patrón viejo.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"active_test": False})
    IrHttp = env["ir.http"]
    menus = 0
    for menu in env["website.menu"].search([("url", "=like", "/%")]):
        nueva = IrHttp._shrimp_legacy_rewrite_url(menu.url)
        if nueva != menu.url:
            _logger.info("website.menu %s (%s): %s -> %s", menu.id, menu.name, menu.url, nueva)
            menu.url = nueva
            menus += 1
    reglas = 0
    for regla in env["website.rewrite"].search([("url_to", "=like", "/%")]):
        nueva = IrHttp._shrimp_legacy_rewrite_url(regla.url_to)
        if nueva != regla.url_to:
            regla.url_to = nueva
            reglas += 1
    # Vistas personalizadas por sitio con rutas viejas: solo aviso.
    viejas = [v for v, _n in IrHttp._shrimp_legacy_routes()]
    prefijos = sorted({v.split("/<")[0] for v in viejas})
    cr.execute("SELECT id, key FROM ir_ui_view WHERE website_id IS NOT NULL "
               "AND arch_db::text LIKE ANY(%s)", [["%%%s%%" % p for p in prefijos]])
    for vid, key in cr.fetchall():
        _logger.warning("Vista personalizada %s (%s) contiene rutas viejas en español: "
                        "revisarla desde el editor del sitio", vid, key)
    sitemaps = env["ir.attachment"].search([("url", "=like", "/sitemap%")])
    n_sitemaps = len(sitemaps)
    sitemaps.unlink()
    _logger.info("shrimp_user_registry 19.0.1.4.0: %s menús y %s redirecciones con rutas "
                 "en inglés; %s sitemaps borrados", menus, reglas, n_sitemaps)
