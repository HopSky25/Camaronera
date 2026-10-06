from odoo import models

# Rutas viejas (español) -> nuevas (inglés) de shrimp_verification. El redirector genérico
# (shrimp_user_registry/models/ir_http.py, _serve_fallback) responde 301/308
# desde la vieja a la nueva conservando parámetros y query string.
# Fuente única: /home/ccristhian/CamaronMarket/rutas/tools/mapping.py
_LEGACY_ROUTES = [
    ("/marketplace/buy/<product_ref>/verificar",
     "/marketplace/buy/<product_ref>/verify"),
    ("/marketplace/compras/<tx_ref>/concluir",
     "/marketplace/purchases/<tx_ref>/complete"),
    ("/marketplace/despacho/<tx_ref>", "/marketplace/dispatch/<tx_ref>"),
    ("/marketplace/despacho/<tx_ref>/guardar",
     "/marketplace/dispatch/<tx_ref>/save"),
    ("/marketplace/despacho/<tx_ref>/llegada",
     "/marketplace/dispatch/<tx_ref>/arrival"),
    ("/marketplace/verificacion/<ref>/aceptacion",
     "/marketplace/verifications/<ref>/acceptance"),
    ("/marketplace/verificacion/<ref>/aceptar",
     "/marketplace/verifications/<ref>/accept"),
    ("/marketplace/verificacion/<ref>/calificar",
     "/marketplace/verifications/<ref>/rate"),
    ("/marketplace/verificacion/<ref>/contraoferta",
     "/marketplace/verifications/<ref>/counter-offer"),
    ("/marketplace/verificacion/<ref>/declarar",
     "/marketplace/verifications/<ref>/declare"),
    ("/marketplace/verificacion/<ref>/declarar/cancelar",
     "/marketplace/verifications/<ref>/declare/cancel"),
    ("/marketplace/verificacion/<ref>/declarar/foto/<token>/eliminar",
     "/marketplace/verifications/<ref>/declare/photos/<token>/delete"),
    ("/marketplace/verificacion/<ref>/declarar/guardar",
     "/marketplace/verifications/<ref>/declare/save"),
    ("/marketplace/verificacion/<ref>/declarar/informe",
     "/marketplace/verifications/<ref>/declare/report"),
    ("/marketplace/verificacion/<ref>/deshacer",
     "/marketplace/verifications/<ref>/undo"),
    ("/marketplace/verificacion/<ref>/foto/<token>",
     "/marketplace/verifications/<ref>/photos/<token>"),
    ("/marketplace/verificacion/<ref>/rechazar",
     "/marketplace/verifications/<ref>/reject"),
    ("/marketplace/verificacion/pendiente/<tx_ref>",
     "/marketplace/verifications/pending/<tx_ref>"),
    ("/marketplace/verificador/<partner_ref>/acreditacion",
     "/marketplace/verifiers/<partner_ref>/accreditation"),
    ("/marketplace/verificadores", "/marketplace/verifiers"),
    ("/registro/verificador", "/register/verifier"),
    ("/verificador/bandeja", "/verifier/inbox"),
    ("/verificador/bandeja/count", "/verifier/inbox/count"),
    ("/verificador/perfil", "/verifier/profile"),
    ("/verificador/perfil/acreditacion", "/verifier/profile/accreditation"),
    ("/verificador/perfil/guardar", "/verifier/profile/save"),
    ("/verificador/reportes", "/verifier/reports"),
    ("/verificador/tecnicos", "/verifier/technicians"),
    ("/verificador/tecnicos/<tech_ref>/editar",
     "/verifier/technicians/<tech_ref>/edit"),
    ("/verificador/tecnicos/<tech_ref>/estado",
     "/verifier/technicians/<tech_ref>/toggle"),
    ("/verificador/tecnicos/agregar", "/verifier/technicians/add"),
    ("/verificador/verificacion/<ref>", "/verifier/verifications/<ref>"),
    ("/verificador/verificacion/<ref>/asignar",
     "/verifier/verifications/<ref>/assign"),
    ("/verificador/verificacion/<ref>/asignar-tecnico",
     "/verifier/verifications/<ref>/assign-technician"),
    ("/verificador/verificacion/<ref>/detalle",
     "/verifier/verifications/<ref>/detail"),
    ("/verificador/verificacion/<ref>/foto/<token>",
     "/verifier/verifications/<ref>/photos/<token>"),
    ("/verificador/verificacion/<ref>/foto/<token>/eliminar",
     "/verifier/verifications/<ref>/photos/<token>/delete"),
    ("/verificador/verificacion/<ref>/guardar",
     "/verifier/verifications/<ref>/save"),
    ("/verificador/verificacion/<ref>/iniciar",
     "/verifier/verifications/<ref>/start"),
    ("/verificador/verificacion/<ref>/veredicto",
     "/verifier/verifications/<ref>/verdict"),
]


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _shrimp_legacy_routes(cls):
        return super()._shrimp_legacy_routes() + _LEGACY_ROUTES
