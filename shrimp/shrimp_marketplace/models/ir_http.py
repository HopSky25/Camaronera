import logging

from odoo import models
from odoo.http import request

_logger = logging.getLogger(__name__)

# Rutas viejas (español) -> nuevas (inglés) de shrimp_marketplace. El redirector genérico
# (shrimp_user_registry/models/ir_http.py, _serve_fallback) responde 301/308
# desde la vieja a la nueva conservando parámetros y query string.
# Fuente única: /home/ccristhian/CamaronMarket/rutas/tools/mapping.py
_LEGACY_ROUTES = [
    ("/marketplace/avisame", "/marketplace/notify-me"),
    ("/marketplace/certificados_producto", "/marketplace/product-certificates"),
    ("/marketplace/compras", "/marketplace/purchases"),
    ("/marketplace/compras/<tx_ref>/calificar",
     "/marketplace/purchases/<tx_ref>/rate"),
    ("/marketplace/compras/<tx_ref>/factura-vendedor",
     "/marketplace/purchases/<tx_ref>/seller-invoice"),
    ("/marketplace/compras/<tx_ref>/factura/pdf",
     "/marketplace/purchases/<tx_ref>/invoice/pdf"),
    ("/marketplace/compras/<tx_ref>/recibir",
     "/marketplace/purchases/<tx_ref>/receive"),
    ("/marketplace/compras/<tx_ref>/trazabilidad",
     "/marketplace/purchases/<tx_ref>/traceability"),
    ("/marketplace/compras/<tx_ref>/trazabilidad/pdf",
     "/marketplace/purchases/<tx_ref>/traceability/pdf"),
    ("/marketplace/compras/count", "/marketplace/purchases/count"),
    ("/marketplace/mi-cuenta", "/marketplace/my-account"),
    ("/marketplace/mi-cuenta/certificado/<line_ref>/archivo",
     "/marketplace/my-account/certificates/<line_ref>/file"),
    ("/marketplace/mi-cuenta/guardar", "/marketplace/my-account/save"),
    ("/marketplace/mis-certificados", "/marketplace/my-certificates"),
    ("/marketplace/mis-certificados/<line_ref>/eliminar",
     "/marketplace/my-certificates/<line_ref>/delete"),
    ("/marketplace/mis-certificados/<line_ref>/renovar",
     "/marketplace/my-certificates/<line_ref>/renew"),
    ("/marketplace/mis-certificados/agregar", "/marketplace/my-certificates/add"),
    ("/marketplace/mis-instalaciones", "/marketplace/my-facilities"),
    ("/marketplace/mis-instalaciones/asignar",
     "/marketplace/my-facilities/allocate"),
    ("/marketplace/mis-instalaciones/facility/<facility_ref>/actualizar",
     "/marketplace/my-facilities/facility/<facility_ref>/update"),
    ("/marketplace/mis-instalaciones/facility/<facility_ref>/eliminar",
     "/marketplace/my-facilities/facility/<facility_ref>/delete"),
    ("/marketplace/mis-instalaciones/facility/crear",
     "/marketplace/my-facilities/facility/create"),
    ("/marketplace/mis-instalaciones/pond/<pond_ref>/actualizar",
     "/marketplace/my-facilities/pond/<pond_ref>/update"),
    ("/marketplace/mis-instalaciones/pond/<pond_ref>/eliminar",
     "/marketplace/my-facilities/pond/<pond_ref>/delete"),
    ("/marketplace/mis-instalaciones/pond/<pond_ref>/imagen",
     "/marketplace/my-facilities/pond/<pond_ref>/image"),
    ("/marketplace/mis-instalaciones/pond/crear",
     "/marketplace/my-facilities/pond/create"),
    ("/marketplace/mis-lotes", "/marketplace/my-lots"),
    ("/marketplace/mis-lotes/<lot_ref>/mover-perfil",
     "/marketplace/my-lots/<lot_ref>/move-profile"),
    ("/marketplace/mis-lotes/<lot_ref>/produccion",
     "/marketplace/my-lots/<lot_ref>/production"),
    ("/marketplace/planificacion", "/marketplace/planning"),
    ("/marketplace/planificacion/count", "/marketplace/planning/count"),
    ("/marketplace/product/<product_ref>/calidad/<quality_ref>/informe",
     "/marketplace/product/<product_ref>/quality/<quality_ref>/report"),
    ("/marketplace/producto/<product_ref>", "/marketplace/product/<product_ref>"),
    ("/marketplace/producto/<product_ref>/foto/<token>",
     "/marketplace/product/<product_ref>/photo/<token>"),
    ("/marketplace/producto/<product_ref>/portada",
     "/marketplace/product/<product_ref>/cover"),
    ("/marketplace/productos-comprados", "/marketplace/purchase-history"),
    ("/marketplace/productos-comprados/count",
     "/marketplace/purchase-history/count"),
    ("/marketplace/products/<product_ref>/baja",
     "/marketplace/products/<product_ref>/deactivate"),
    ("/marketplace/products/<product_ref>/calidad",
     "/marketplace/products/<product_ref>/quality"),
    ("/marketplace/products/<product_ref>/calidad/<quality_ref>/eliminar",
     "/marketplace/products/<product_ref>/quality/<quality_ref>/delete"),
    ("/marketplace/products/<product_ref>/calidad/<quality_ref>/muestreo",
     "/marketplace/products/<product_ref>/quality/<quality_ref>/sampling"),
    ("/marketplace/products/<product_ref>/calidad/guardar",
     "/marketplace/products/<product_ref>/quality/save"),
    ("/marketplace/products/<product_ref>/eliminar",
     "/marketplace/products/<product_ref>/delete"),
    ("/marketplace/products/<product_ref>/mover-perfil",
     "/marketplace/products/<product_ref>/move-profile"),
    ("/marketplace/products/<product_ref>/muestreo",
     "/marketplace/products/<product_ref>/sampling"),
    ("/marketplace/products/<product_ref>/reactivar",
     "/marketplace/products/<product_ref>/reactivate"),
    ("/marketplace/reportes", "/marketplace/reports"),
    ("/marketplace/salidas", "/marketplace/exports"),
    ("/marketplace/salidas/<export_ref>/anular",
     "/marketplace/exports/<export_ref>/cancel"),
    ("/marketplace/salidas/nueva", "/marketplace/exports/new"),
    ("/marketplace/salidas/registrar", "/marketplace/exports/register"),
    ("/marketplace/siembras/<alloc_ref>/actualizar",
     "/marketplace/sowings/<alloc_ref>/update"),
    ("/marketplace/siembras/<alloc_ref>/cancelar",
     "/marketplace/sowings/<alloc_ref>/cancel"),
    ("/marketplace/siembras/<alloc_ref>/editar",
     "/marketplace/sowings/<alloc_ref>/edit"),
    ("/marketplace/siembras/nueva", "/marketplace/sowings/new"),
    ("/marketplace/solicitudes", "/marketplace/check-requests"),
    ("/marketplace/solicitudes/<cr_ref>/aprobar",
     "/marketplace/check-requests/<cr_ref>/approve"),
    ("/marketplace/solicitudes/<cr_ref>/detalle",
     "/marketplace/check-requests/<cr_ref>/detail"),
    ("/marketplace/solicitudes/<cr_ref>/rechazar",
     "/marketplace/check-requests/<cr_ref>/reject"),
    ("/marketplace/vendedor/<partner_ref>", "/marketplace/sellers/<partner_ref>"),
    ("/marketplace/vendedor/<partner_ref>/avatar",
     "/marketplace/sellers/<partner_ref>/avatar"),
    ("/marketplace/ventas", "/marketplace/sales"),
    ("/marketplace/ventas/<tx_ref>/calificar-comprador",
     "/marketplace/sales/<tx_ref>/rate-buyer"),
    ("/marketplace/ventas/<tx_ref>/factura",
     "/marketplace/sales/<tx_ref>/invoice"),
    ("/marketplace/ventas/count", "/marketplace/sales/count"),
    ("/mi-panel", "/my/dashboard"),
]


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _shrimp_legacy_routes(cls):
        return super()._shrimp_legacy_routes() + _LEGACY_ROUTES

    # ------------------------------------------------------------------
    # Perfiles por sitio: cada sitio trabaja con SU perfil
    # ------------------------------------------------------------------
    # Rutas en las que no se toca el perfil activo: el login, la aceptación
    # de documentos, el propio cambio de perfil, el alta y lo técnico.
    _SHRIMP_SITE_ROLE_EXEMPT = (
        "/web", "/website/", "/my/legal", "/my/privacy", "/my/profile/",
        "/my/site-profile", "/register", "/logout", "/shrimp_", "/odoo",
    )
    # Último perfil usado en cada sitio (sesión, que es por dominio): al
    # volver de otro sitio se recupera ese y no el primero de la lista.
    _SHRIMP_SITE_ROLE_KEY = "shrimp_site_role_%s"

    @classmethod
    def _shrimp_site_role_gate(cls, endpoint):
        """Alinea el perfil ACTIVO de la cuenta con el sitio de la petición
        (website._shrimp_align_active_role): en el marketplace, una cuenta que
        navegaba como verificadora o maquilador vuelve a su último perfil del
        marketplace; en los sitios de verificadores y de empaque, a su perfil
        propio. Solo GET de páginas web de usuarios de portal."""
        hreq = request.httprequest
        if hreq.method not in ("GET", "HEAD"):
            return
        if getattr(request.dispatcher, "routing_type", "http") != "http":
            return
        routing = getattr(endpoint, "routing", None) or {}
        if not routing.get("website"):
            return
        web = getattr(request, "website", None)
        user = request.env.user
        if not web or not user or user._is_public() or user._is_internal():
            return
        ruta = hreq.path or "/"
        if ruta.startswith(cls._SHRIMP_SITE_ROLE_EXEMPT):
            return
        if getattr(request.env.cr, "readonly", False):
            return
        web = web.sudo()
        if "shrimp_platform" not in web._fields:
            return
        clave = cls._SHRIMP_SITE_ROLE_KEY % web.id
        holder = user.sudo()._shrimp_account_company()
        # Perfil activo POR USUARIO (CR-12).
        activo = holder.with_user(user).sudo()._shrimp_effective_type() if holder else False
        if not activo:
            return
        if web._shrimp_role_allowed_here(activo):
            if request.session.get(clave) != activo:
                request.session[clave] = activo
            return
        with request.env.cr.savepoint():
            nuevo = web._shrimp_align_active_role(user, preferred=request.session.get(clave))
        if nuevo and nuevo != activo:
            request.session[clave] = nuevo

    @classmethod
    def _dispatch(cls, endpoint):
        try:
            cls._shrimp_site_role_gate(endpoint)
        except Exception:  # noqa: BLE001 - nunca tumbar la página por esto
            _logger.exception("shrimp: no se pudo alinear el perfil activo con el sitio")
        return super()._dispatch(endpoint)
