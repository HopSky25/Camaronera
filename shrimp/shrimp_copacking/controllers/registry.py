from odoo import http, _
from odoo.http import request

from odoo.addons.shrimp_marketplace.controllers.utils import to_number

from .website_home import _es_sitio_maquiladores
from odoo.exceptions import ValidationError

from odoo.addons.shrimp_user_registry.controllers.main import ShrimpRegistryController



class ShrimpCopackerRegistry(ShrimpRegistryController):
    """Alta web del maquilador.

    Hasta ahora el rol existía en el modelo pero no había por dónde darse de
    alta: el registro compartido solo ofrece los roles del marketplace. Se
    resuelve como en la empacadora y el verificador, con formulario propio en
    vez de un paso más del formulario común, porque lo que declara una planta
    de maquila (su habilitación y su capacidad) no se parece a lo que declara
    quien vende camarón.
    """

    @http.route("/register/copacker", type="http", auth="public",
                website=True, sitemap=True)
    def registro_maquilador(self, **kw):
        # El asistente común con el perfil de maquilador ya marcado (en la
        # plataforma de empaque es el único que se ofrece). Desde otro sitio
        # se manda al de empaque (o, si no tiene dominio, al alta de este).
        if not _es_sitio_maquiladores():
            url = request.website.sudo()._shrimp_role_site_url(
                "maquilador", "/register/copacker")
            if url:
                return request.redirect(url, local=False)
            return request.redirect("/register")
        return self._render_signup("shrimp_user_registry.registry_form", {},
                                   preselect=["maquilador"])

    @http.route()
    def registro_form(self, **kw):
        # En el sitio de maquiladores, /register no puede llevar al formulario
        # del marketplace: ahí no hay nada que se pueda ser salvo maquilador.
        if _es_sitio_maquiladores():
            return request.redirect("/register/copacker")
        return super().registro_form(**kw)

    def _allowed_user_types(self):
        # La planta de maquila se registra (y se agrega a una cuenta) SOLO en
        # la plataforma de empaque, donde es el único perfil. En el
        # marketplace y en el de verificadores no se ofrece: allí se enlaza a
        # su alta (_signup_other_sites).
        if _es_sitio_maquiladores():
            return {"maquilador"}
        return super()._allowed_user_types() - {"maquilador"}

    def _signup_other_sites(self):
        otros = super()._signup_other_sites()
        # Solo en el marketplace: en el sitio de verificadores no se anuncia
        # el de empaque.
        if request.website.sudo().shrimp_platform == "main":
            web = request.website.sudo()
            url = web._shrimp_role_site_url("maquilador", "/register/copacker")
            sitio = web._shrimp_platform_site("copacker")
            if url and sitio:
                otros.append({"role": "maquilador", "text": _("¿Eres planta de maquila?"),
                              "site": sitio.name, "url": url})
        return otros

    def _extra_partner_vals(self, user_type, post):
        vals = super()._extra_partner_vals(user_type, post)
        if user_type != "maquilador":
            return vals

        def _txt(key):
            return " ".join((post.get(key) or "").split()) or False

        def _float(key):
            # Texto libre del formulario: coma decimal, vacío o cualquier
            # cosa. Un ValueError aquí reventaría el alta entera.
            return to_number(post.get(key), 0.0)

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
            # Perfil común (los nombres del formulario siguen siendo pack_*).
            "shrimp_razon_social": razon,
            "shrimp_ubicacion": ubicacion,
            "shrimp_representante": _txt("pack_representante"),
            "shrimp_telefono": _txt("pack_telefono"),
            # La habilitación no se exige al registrarse: hay plantas que la
            # tienen en trámite y dejarlas fuera del alta sería perderlas. Lo
            # que no se puede es empacar sin ella, y eso lo controla la orden.
            "pack_codigo_establecimiento": _txt("pack_codigo_establecimiento"),
            "pack_habilitacion_desde": post.get("pack_habilitacion_desde") or False,
            "pack_habilitacion_hasta": post.get("pack_habilitacion_hasta") or False,
            "shrimp_capacity_value": _float("pack_capacidad_lb_semana"),
            "shrimp_capacity_unit": "lb_week",
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
