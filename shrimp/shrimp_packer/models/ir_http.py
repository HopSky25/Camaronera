from odoo import models

# Rutas viejas (español) -> nuevas (inglés) de shrimp_packer. El redirector genérico
# (shrimp_user_registry/models/ir_http.py, _serve_fallback) responde 301/308
# desde la vieja a la nueva conservando parámetros y query string.
# Fuente única: /home/ccristhian/CamaronMarket/rutas/tools/mapping.py
_LEGACY_ROUTES = [
    ("/empacadora/compromiso/<ref>/comprar", "/packer/commitments/<ref>/buy"),
    ("/empacadora/compromiso/<ref>/desistir", "/packer/commitments/<ref>/desist"),
    ("/empacadora/compromiso/<ref>/retirar", "/packer/commitments/<ref>/withdraw"),
    ("/empacadora/reserva/<ref>", "/packer/reservations/<ref>"),
    ("/empacadora/reserva/<ref>/comprometer", "/packer/reservations/<ref>/commit"),
    ("/empacadora/reservas", "/packer/reservations"),
    ("/empacadora/reservas/preferencia", "/packer/reservations/preference"),
    ("/marketplace/empacadora/<partner_ref>",
     "/marketplace/packers/<partner_ref>"),
    ("/marketplace/empacadoras", "/marketplace/packers"),
    ("/marketplace/empacadoras/count", "/marketplace/packers/count"),
    ("/marketplace/listas-de-precios", "/marketplace/price-lists"),
    ("/marketplace/listas-de-precios/<ref>", "/marketplace/price-lists/<ref>"),
    ("/marketplace/listas-de-precios/<ref>/archivar",
     "/marketplace/price-lists/<ref>/archive"),
    ("/marketplace/listas-de-precios/<ref>/bonificacion",
     "/marketplace/price-lists/<ref>/bonuses"),
    ("/marketplace/listas-de-precios/<ref>/bonificacion/<bonus_ref>/borrar",
     "/marketplace/price-lists/<ref>/bonuses/<bonus_ref>/delete"),
    ("/marketplace/listas-de-precios/<ref>/cargar",
     "/marketplace/price-lists/<ref>/upload"),
    ("/marketplace/listas-de-precios/<ref>/despublicar",
     "/marketplace/price-lists/<ref>/unpublish"),
    ("/marketplace/listas-de-precios/<ref>/duplicar",
     "/marketplace/price-lists/<ref>/duplicate"),
    ("/marketplace/listas-de-precios/<ref>/editar",
     "/marketplace/price-lists/<ref>/edit"),
    ("/marketplace/listas-de-precios/<ref>/eliminar",
     "/marketplace/price-lists/<ref>/delete"),
    ("/marketplace/listas-de-precios/<ref>/plantilla",
     "/marketplace/price-lists/<ref>/template"),
    ("/marketplace/listas-de-precios/<ref>/publicar",
     "/marketplace/price-lists/<ref>/publish"),
    ("/marketplace/listas-de-precios/<ref>/renglon",
     "/marketplace/price-lists/<ref>/lines"),
    ("/marketplace/listas-de-precios/<ref>/renglon/<line_ref>/borrar",
     "/marketplace/price-lists/<ref>/lines/<line_ref>/delete"),
    ("/marketplace/listas-de-precios/comparar",
     "/marketplace/price-lists/compare"),
    ("/marketplace/listas-de-precios/guardar", "/marketplace/price-lists/save"),
    ("/marketplace/listas-de-precios/nueva", "/marketplace/price-lists/new"),
    ("/marketplace/mi-historial", "/marketplace/my-track-record"),
    ("/marketplace/oferta", "/marketplace/supply"),
    ("/marketplace/precio-de-lista", "/marketplace/list-price"),
    ("/marketplace/precio-sugerido", "/marketplace/suggested-price"),
    ("/marketplace/proveedores", "/marketplace/suppliers"),
    ("/marketplace/reservas", "/marketplace/reservations"),
    ("/marketplace/reservas/<ref>", "/marketplace/reservations/<ref>"),
    ("/marketplace/reservas/<ref>/cancelar",
     "/marketplace/reservations/<ref>/cancel"),
    ("/marketplace/reservas/<ref>/cosecha",
     "/marketplace/reservations/<ref>/harvest"),
    ("/marketplace/reservas/compromiso/<ref>/aceptar",
     "/marketplace/reservations/commitments/<ref>/accept"),
    ("/marketplace/reservas/compromiso/<ref>/descartar",
     "/marketplace/reservations/commitments/<ref>/discard"),
    ("/marketplace/reservas/compromiso/<ref>/desistir",
     "/marketplace/reservations/commitments/<ref>/desist"),
    ("/marketplace/reservas/confirmacion/<ref>/deshacer",
     "/marketplace/reservations/confirmations/<ref>/undo"),
    ("/marketplace/reservas/confirmacion/<ref>/firmar",
     "/marketplace/reservations/confirmations/<ref>/sign"),
    ("/marketplace/reservas/nueva", "/marketplace/reservations/new"),
    ("/marketplace/simulador", "/marketplace/simulator"),
    ("/marketplace/simulador/<ref>", "/marketplace/simulator/<ref>"),
    ("/registro/empacadora", "/register/packer"),
]


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _shrimp_legacy_routes(cls):
        return super()._shrimp_legacy_routes() + _LEGACY_ROUTES
