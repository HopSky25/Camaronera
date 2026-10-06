from odoo import http
from odoo.http import request

from odoo.addons.shrimp_marketplace.controllers.utils import is_platform

from odoo.addons.shrimp_marketplace.controllers.marketplace import ShrimpWebsiteHome

from . import landing


def _es_sitio_maquiladores():
    """True si la petición entra por la plataforma de empaque."""
    return is_platform("copacker")


class ShrimpCopackerHome(ShrimpWebsiteHome):
    """En la plataforma del maquilador la portada es su bandeja.

    El módulo crea su propio sitio ("CamaronMarket Empaque"), pero sin este override
    la raíz "/" cae en el controlador del marketplace y sirve la landing de
    marketing de la compraventa: un sitio entero cuya portada habla de otra
    cosa y no lleva a ninguna de sus pantallas. Mismo patrón que usa
    shrimp_verification para su portada.

    El maquilador con sesión va directo a su bandeja (redirección relativa a
    propósito: la petición ya entró por el dominio correcto). Cualquier otro
    visitante ve la portada pública del servicio (copack_landing, ver
    controllers/landing.py y views/copack_landing_templates.xml).
    """

    @http.route()
    def index(self, **kw):
        if _es_sitio_maquiladores():
            # A quien no ha entrado se le ofrece el alta, no el login a secas.
            # Mandarlo a la bandeja lo dejaba en una pantalla de acceso sin
            # ninguna forma de crearse la cuenta: la puerta de entrada de una
            # plataforma nueva no puede exigir tener ya la llave.
            #
            # Ahora quien no es maquilador (anónimo, o un cliente con sesión)
            # ve la portada del servicio, que explica qué es esto y lleva al
            # alta (/register/copacker) o al login. El maquilador con sesión
            # sigue entrando directo a su bandeja.
            if landing.es_maquilador(request.env.user):
                return request.redirect("/copacker/inbox")
            return request.render("shrimp_copacking.copack_landing",
                                  landing.valores_portada())
        return super().index(**kw)
