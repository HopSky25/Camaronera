from odoo import models

# Rutas viejas (español) -> nuevas (inglés) de shrimp_copacking. El redirector genérico
# (shrimp_user_registry/models/ir_http.py, _serve_fallback) responde 301/308
# desde la vieja a la nueva conservando parámetros y query string.
# Fuente única: /home/ccristhian/CamaronMarket/rutas/tools/mapping.py
_LEGACY_ROUTES = [
    ("/maquilador/bandeja", "/copacker/inbox"),
    ("/maquilador/liquidaciones", "/copacker/settlements"),
    ("/maquilador/orden/<ref>/empaque", "/copacker/orders/<ref>/packing"),
    ("/maquilador/orden/<ref>/recepcion", "/copacker/orders/<ref>/reception"),
    ("/maquilador/ordenes", "/copacker/orders"),
    ("/maquilador/ordenes/count", "/copacker/orders/count"),
    ("/maquilador/perfil", "/copacker/profile"),
    ("/maquilador/perfil/guardar", "/copacker/profile/save"),
    ("/maquilador/solicitud/<ref>", "/copacker/requests/<ref>"),
    ("/maquilador/solicitud/<ref>/ofertar", "/copacker/requests/<ref>/offer"),
    ("/maquilador/solicitud/<ref>/retirar", "/copacker/requests/<ref>/withdraw"),
    ("/maquilador/tarifa/<ref>", "/copacker/tariffs/<ref>"),
    ("/maquilador/tarifa/<ref>/archivar", "/copacker/tariffs/<ref>/archive"),
    ("/maquilador/tarifa/<ref>/guardar", "/copacker/tariffs/<ref>/save"),
    ("/maquilador/tarifa/nueva", "/copacker/tariffs/new"),
    ("/maquilador/tarifas", "/copacker/tariffs"),
    ("/marketplace/empaque", "/marketplace/copacking"),
    ("/marketplace/empaque/count", "/marketplace/copacking/count"),
    ("/marketplace/empaque/oferta/<ref>/aceptar",
     "/marketplace/copacking/offers/<ref>/accept"),
    ("/marketplace/empaque/orden/<ref>", "/marketplace/copacking/orders/<ref>"),
    ("/marketplace/empaque/orden/<ref>/cancelar",
     "/marketplace/copacking/orders/<ref>/cancel"),
    ("/marketplace/empaque/orden/<ref>/cerrar",
     "/marketplace/copacking/orders/<ref>/close"),
    ("/marketplace/empaque/orden/<ref>/deshacer-firma",
     "/marketplace/copacking/orders/<ref>/undo-signature"),
    ("/marketplace/empaque/orden/<ref>/firmar",
     "/marketplace/copacking/orders/<ref>/sign"),
    ("/marketplace/empaque/orden/<ref>/reabrir-acta",
     "/marketplace/copacking/orders/<ref>/reopen"),
    ("/marketplace/empaque/ordenes", "/marketplace/copacking/orders"),
    ("/marketplace/empaque/planta/<socio_ref>",
     "/marketplace/copacking/plants/<socio_ref>"),
    ("/marketplace/empaque/propio/<ref>/cerrar",
     "/marketplace/copacking/self/<ref>/close"),
    ("/marketplace/empaque/propio/<ref>/corregir",
     "/marketplace/copacking/self/<ref>/correct"),
    ("/marketplace/empaque/propio/<ref>/empaque",
     "/marketplace/copacking/self/<ref>/packing"),
    ("/marketplace/empaque/propio/<ref>/recepcion",
     "/marketplace/copacking/self/<ref>/reception"),
    ("/marketplace/empaque/propio/nuevo", "/marketplace/copacking/self/new"),
    ("/marketplace/empaque/solicitar", "/marketplace/copacking/requests/new"),
    ("/marketplace/empaque/solicitud/<ref>",
     "/marketplace/copacking/requests/<ref>"),
    ("/marketplace/empaque/solicitud/<ref>/cancelar",
     "/marketplace/copacking/requests/<ref>/cancel"),
    ("/marketplace/empaque/solicitudes", "/marketplace/copacking/requests"),
    ("/registro/maquilador", "/register/copacker"),
]


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _shrimp_legacy_routes(cls):
        return super()._shrimp_legacy_routes() + _LEGACY_ROUTES
