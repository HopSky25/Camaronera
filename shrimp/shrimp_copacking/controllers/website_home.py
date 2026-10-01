from odoo import http
from odoo.http import request

from odoo.addons.shrimp_marketplace.controllers.marketplace import ShrimpWebsiteHome


def _es_sitio_maquiladores():
    """True si la petición entra por la plataforma del maquilador."""
    web = getattr(request, "website", False)
    return bool(web and web.sudo().shrimp_is_copacker_site)


class ShrimpCopackerHome(ShrimpWebsiteHome):
    """En la plataforma del maquilador la portada es su bandeja.

    El módulo crea su propio sitio ("CamaronMkt Empaque"), pero sin este override
    la raíz "/" cae en el controlador del marketplace y sirve la landing de
    marketing de la compraventa: un sitio entero cuya portada habla de otra
    cosa y no lleva a ninguna de sus pantallas. Mismo patrón que usa
    shrimp_verification para su portada.

    Aquí se redirige en vez de renderizar una plantilla propia porque el
    maquilador no tiene landing pública: todo lo suyo vive tras login. Se usa
    una redirección relativa a propósito —la petición ya entró por el dominio
    correcto— y, si quien llega es un visitante anónimo, /maquilador/bandeja
    es auth="user" y Odoo lo manda al login y lo devuelve aquí después.
    """

    @http.route()
    def index(self, **kw):
        if _es_sitio_maquiladores():
            # A quien no ha entrado se le ofrece el alta, no el login a secas.
            # Mandarlo a la bandeja lo dejaba en una pantalla de acceso sin
            # ninguna forma de crearse la cuenta: la puerta de entrada de una
            # plataforma nueva no puede exigir tener ya la llave.
            if request.env.user._is_public():
                return request.redirect("/registro/maquilador")
            return request.redirect("/maquilador/bandeja")
        return super().index(**kw)
