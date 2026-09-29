from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError

from odoo.addons.shrimp_user_registry.controllers.main import ShrimpRegistryController


def _es_sitio_maquiladores():
    """True si la petición entra por la plataforma de maquiladores."""
    web = getattr(request, "website", False)
    return bool(web and web.sudo().shrimp_is_copacker_site)


class ShrimpCopackerRegistry(ShrimpRegistryController):
    """Alta web del maquilador.

    Hasta ahora el rol existía en el modelo pero no había por dónde darse de
    alta: el registro compartido solo ofrece los roles del marketplace. Se
    resuelve como en la empacadora y el verificador, con formulario propio en
    vez de un paso más del formulario común, porque lo que declara una planta
    de maquila (su habilitación y su capacidad) no se parece a lo que declara
    quien vende camarón.
    """

    @http.route("/registro/maquilador", type="http", auth="public",
                website=True, sitemap=True)
    def registro_maquilador(self, **kw):
        return request.render("shrimp_copacking.registry_form_maquilador",
                              {"values": {}})

    @http.route()
    def registro_form(self, **kw):
        # En el sitio de maquiladores, /registro no puede llevar al formulario
        # del marketplace: ahí no hay nada que se pueda ser salvo maquilador.
        if _es_sitio_maquiladores():
            return request.redirect("/registro/maquilador")
        return super().registro_form(**kw)

    def _registro_form_template(self, user_type):
        # Al que falla el alta hay que devolverlo a SU formulario, no al común:
        # si no, pierde todo lo escrito y encima ve campos de otro rol.
        if user_type == "maquilador" or _es_sitio_maquiladores():
            return "shrimp_copacking.registry_form_maquilador"
        return super()._registro_form_template(user_type)

    def _extra_partner_vals(self, user_type, post):
        vals = super()._extra_partner_vals(user_type, post)
        if user_type != "maquilador":
            return vals

        def _txt(key):
            return " ".join((post.get(key) or "").split()) or False

        def _float(key):
            # Lo que llega del formulario es texto libre: coma decimal, campo
            # vacío o cualquier cosa. Un ValueError aquí reventaría el alta
            # entera por un precio mal tecleado.
            try:
                return float((post.get(key) or "0").replace(",", "."))
            except (TypeError, ValueError):
                return 0.0

        razon = _txt("pack_razon_social")
        ubicacion = _txt("pack_ubicacion")

        # Las mismas dos que exige el modelo, validadas aquí para que el usuario
        # lea un mensaje del formulario y no el de una restricción de base de
        # datos con los datos ya perdidos.
        if not razon:
            raise ValidationError(_("La razón social del maquilador es obligatoria."))
        if not ubicacion:
            raise ValidationError(_("La ubicación de la planta es obligatoria."))

        vals.update({
            "pack_razon_social": razon,
            "pack_ubicacion": ubicacion,
            "pack_representante": _txt("pack_representante"),
            "pack_telefono": _txt("pack_telefono"),
            # La habilitación no se exige al registrarse: hay plantas que la
            # tienen en trámite y dejarlas fuera del alta sería perderlas. Lo
            # que no se puede es empacar sin ella, y eso lo controla la orden.
            "pack_codigo_establecimiento": _txt("pack_codigo_establecimiento"),
            "pack_habilitacion_desde": post.get("pack_habilitacion_desde") or False,
            "pack_habilitacion_hasta": post.get("pack_habilitacion_hasta") or False,
            "pack_capacidad_lb_semana": _float("pack_capacidad_lb_semana"),
            "pack_lote_minimo_lb": _float("pack_lote_minimo_lb"),
            "pack_presentaciones": _txt("pack_presentaciones"),
            "pack_desde_entero": _float("pack_desde_entero"),
            "pack_desde_cola": _float("pack_desde_cola"),
            "pack_desde_valor_agregado": _float("pack_desde_valor_agregado"),
            "pack_tarifa_nota": _txt("pack_tarifa_nota"),
            # Un checkbox sin marcar no viaja en el POST, de ahí el bool() en
            # vez de un valor por defecto: el formulario lo trae marcado y
            # apagarlo tiene que ser un acto deliberado de la planta.
            "pack_en_directorio": bool(post.get("pack_en_directorio")),
        })
        return vals
