from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import AccessError, UserError, ValidationError
from werkzeug.exceptions import NotFound, Forbidden
from urllib.parse import quote, urlencode, urlsplit

from odoo.addons.shrimp_user_registry.controllers.main import (
    flash_message, require_operational)
from odoo.addons.shrimp_marketplace.controllers.utils import (
    ShrimpPortalMixin, to_int, to_number)
from odoo.addons.shrimp_marketplace.models.shrimp_selection import (
    PRESENTATIONS_WITH_VALUE_ADDED)
from odoo.addons.shrimp_marketplace.controllers.filter_drawer import (
    fd_context, fd_json_count, fd_tags)

from . import landing


# Lo que llega por la barra de direcciones o por un formulario es texto libre:
# se convierte con las ayudas comunes (shrimp_marketplace.controllers.utils),
# que no revientan y dejan que rechace el valor la validacion del modelo.
_entero = to_int
_decimal = to_number


# Filtros de propiedad EXPLICITOS. Las ir.rule de este modulo solo valen para
# base.group_portal: un usuario INTERNO cuyo contacto sea camaronera,
# empacadora o maquilador no tiene ninguna, y un search([]) le devolvia las
# ordenes, solicitudes y plantas de todo el mundo. Las reglas se quedan como
# segunda linea de defensa; la primera es que el controlador pida solo lo suyo.
# Son funciones sueltas para que los tests puedan comprobar el dominio sin
# levantar una peticion HTTP.
def _dominio_plantas_visibles(socio):
    """Maquiladores que `socio` (cliente) puede ver: los del directorio y
    aquellos con los que ya tiene una orden. Es lo mismo que abren las reglas
    rule_copack_directorio y rule_copack_contrapartes para el portal."""
    contrapartes = socio.env["shrimp.copack.order"].sudo().search(
        [("client_partner_id", "=", socio.id)]).mapped("copacker_partner_id").ids
    # Varios perfiles: la propia planta (si la cuenta es también maquiladora)
    # no sale: nadie se contrata a sí mismo.
    return socio.env["res.partner"]._shrimp_role_domain("maquilador") + [
        ("id", "!=", socio.id),
        "|", ("pack_en_directorio", "=", True), ("id", "in", contrapartes)]


def _dominio_ordenes(socio):
    """Ordenes en las que `socio` es una de las dos partes."""
    return ["|", ("client_partner_id", "=", socio.id),
            ("copacker_partner_id", "=", socio.id)]


def _dominio_tarifas_recibidas(socio, maquilador):
    """Tarifas publicadas de `maquilador` dirigidas a `socio` o a su grupo."""
    return [("copacker_partner_id", "=", maquilador.id),
            ("state", "=", "published"),
            ("recipient_ids", "in", socio.shrimp_grupo_ids())]


def _dominio_bandeja_maquilador(maquilador):
    """Solicitudes publicadas que `maquilador` puede ofertar: las abiertas a
    todos y las dirigidas a el (rule_copack_request_bandeja)."""
    return [("state", "=", "published"),
            # Sus propias solicitudes (cuenta cliente + maquiladora) no.
            ("client_partner_id", "!=", maquilador.id),
            "|", ("is_open", "=", True),
            ("copacker_partner_id", "=", maquilador.id)]


class ShrimpCopackClient(ShrimpPortalMixin, http.Controller):
    """Las pantallas de quien necesita empacar: camaronera y empacadora.

    El orden de las rutas no es casual. Primero el directorio, porque lo
    primero que hace quien busca servicio es mirar quien lo presta y desde
    cuanto; pedir es el segundo paso, no el primero.
    """

    # Lo que el cliente teclea al pedir empaque. Se lista una sola vez porque
    # lo usan tres sitios: el formulario que lo pinta, el guardado que lo
    # devuelve cuando la validacion falla, y la correccion de una solicitud.
    _CAMPOS_SOLICITUD = ("quantity_lb", "presentation", "size_grade_id",
                         "needed_from", "needed_to", "supplies_notes", "notes",
                         "origen_ref")

    # ==================================================================
    # Ayudas
    # ==================================================================
    def _solo_cliente(self):
        """Corta el paso a quien no es dueño de camarón.

        El modelo ya lo impide al guardar, pero sin este corte se le abriria el
        formulario entero a un semillero para reventar al final. Una pantalla
        que no lleva a ningun sitio es peor que no tenerla.
        """
        if not self._partner()._shrimp_can("request_copack"):
            raise Forbidden()
        if self._partner()._shrimp_can("requires_approval"):
            require_operational(self._partner())

    def _planta_visible(self, ref):
        """Maquilador visible para mí (directorio o contraparte), por su
        código uuid_ref. Un id numérico, o una planta que no me toca ver,
        responde 404."""
        token = str(ref or "").strip()
        if not token or token.isdigit():
            raise NotFound()
        maq = request.env["res.partner"].search(
            [("uuid_ref", "=", token)] + _dominio_plantas_visibles(self._partner()),
            limit=1)
        if not maq:
            raise NotFound()
        return maq

    def _mi_solicitud(self, ref):
        sol = request.env["shrimp.copack.request"].sudo().resolve_ref(ref)
        if not sol:
            raise NotFound()
        if sol.client_partner_id != self._partner():
            raise Forbidden()
        return sol

    def _mi_orden(self, ref):
        orden = request.env["shrimp.copack.order"].sudo().resolve_ref(ref)
        if not orden:
            raise NotFound()
        socio = self._partner()
        if socio not in (orden.client_partner_id, orden.copacker_partner_id):
            raise Forbidden()
        return orden

    # ==================================================================
    # 1. El directorio: quien empaca y desde cuanto
    # ==================================================================
    # Servicios del directorio: tarifa «desde» declarada por la planta.
    DIR_SERVICIOS = [("entero", "Entero", "pack_desde_entero"),
                     ("cola", "Cola", "pack_desde_cola"),
                     ("valor_agregado", "Valor agregado", "pack_desde_valor_agregado")]
    DIR_ORDENES = [("", "Nombre (A–Z)"), ("tarifa", "Tarifa más baja"),
                   ("capacidad", "Mayor capacidad")]

    def _copack_directory_search(self, kw):
        """(plantas, filtros, todas) del directorio de empaque. Parámetros: q,
        servicio (repetible), habilitada=1, ubicacion, orden."""
        # Sin sudo a proposito: la regla de acceso ya deja ver solo a los
        # maquiladores que activaron aparecer en el directorio. Si esto
        # devuelve de mas, el fallo esta en la regla y hay que verlo ahi.
        Socio = request.env["res.partner"]
        dominio = Socio._shrimp_role_domain("maquilador") + [
            ("pack_en_directorio", "=", True), ("id", "!=", self._partner().id)]
        todas = Socio.search(dominio, order="name")
        servicios_ok = {c for c, _l, _f in self.DIR_SERVICIOS}
        f = {
            "q": (kw.get("q") or "").strip(),
            "servicio": [x for x in request.httprequest.args.getlist("servicio") if x in servicios_ok],
            "habilitada": "1" if (kw.get("habilitada") or "") in ("1", "on") else "",
            "ubicacion": (kw.get("ubicacion") or "").strip(),
            "orden": (kw.get("orden") or "").strip(),
        }
        if f["orden"] not in dict(self.DIR_ORDENES):
            f["orden"] = ""
        if f["q"]:
            dominio += ["|", ("name", "ilike", f["q"]), ("shrimp_ubicacion", "ilike", f["q"])]
        plantas = Socio.search(dominio, order="name")
        campos = {c: fld for c, _l, fld in self.DIR_SERVICIOS}
        for code in f["servicio"]:
            plantas = plantas.filtered(lambda m, fld=campos[code]: m[fld])
        if f["habilitada"]:
            plantas = plantas.filtered("pack_habilitacion_vigente")
        if f["ubicacion"]:
            plantas = plantas.filtered(
                lambda m: (m.shrimp_ubicacion or "").strip().lower() == f["ubicacion"].lower())
        if f["orden"] == "tarifa":
            def _desde(m):
                tarifas = [m[fld] for _c, _l, fld in self.DIR_SERVICIOS if m[fld]]
                return min(tarifas) if tarifas else float("inf")
            plantas = plantas.sorted(_desde)
        elif f["orden"] == "capacidad":
            plantas = plantas.sorted(lambda m: -(m.shrimp_capacity_value or 0.0))
        return plantas, f, todas

    @http.route("/marketplace/copacking", type="http", auth="user", website=True)
    def copack_directory(self, **kw):
        self._solo_cliente()
        plantas, f, todas = self._copack_directory_search(kw)
        # El cajón solo ofrece lo que distingue a las plantas publicadas.
        servicios = [(c, l) for c, l, fld in self.DIR_SERVICIOS
                     if any(todas.mapped(fld)) or c in f["servicio"]]
        ubicaciones = sorted({(m.shrimp_ubicacion or "").strip() for m in todas} - {""})
        vigentes = todas.filtered("pack_habilitacion_vigente")
        show = {
            "servicio": len(servicios) > 1,
            "habilitada": bool(vigentes) and len(vigentes) < len(todas) or bool(f["habilitada"]),
            "ubicacion": len(ubicaciones) > 1,
        }
        nombres = {c: l for c, l, _f in self.DIR_SERVICIOS}

        def label(group, vals):
            key, val = group[0], vals.get(group[0])
            if not val:
                return None
            if key == "servicio":
                return "Empaca: %s" % nombres.get(val, val)
            if key == "habilitada":
                return "Habilitación vigente"
            if key == "ubicacion":
                return "Ubicación: %s" % val
            return None

        url = "/marketplace/copacking"
        tags, clear_url = fd_tags(url, f, [("servicio",), ("habilitada",), ("ubicacion",)], label,
                                  keep={"q": f["q"], "orden": f["orden"]}, anchor="#listado")
        fd = fd_context(
            url, len(plantas), tags, clear_url,
            search={"name": "q", "value": f["q"], "label": "Buscar plantas",
                    "placeholder": "Buscar por nombre o ubicación…"},
            toolbar_label="Buscar y filtrar plantas de empaque",
            hidden=[("servicio", x) for x in f["servicio"]]
            + [("habilitada", f["habilitada"]), ("ubicacion", f["ubicacion"])],
            sort={"name": "orden", "value": f["orden"], "options": self.DIR_ORDENES},
            count_url="/marketplace/copacking/count",
            noun=("planta", "plantas"),
            drawer_hidden=[("q", f["q"]), ("orden", f["orden"])],
            keep=["q", "orden"],
            has_drawer=any(show.values()),
        )
        return request.render("shrimp_copacking.copack_directory", {
            "maquiladores": plantas,
            "q": f["q"],
            "hoy": fields.Date.context_today(self._partner()),
            "filters": f,
            "fd": fd,
            "fd_show": show,
            "servicio_opts": servicios,
            "ubicacion_opts": ubicaciones,
            "total_plantas": len(todas),
        })

    @http.route("/marketplace/copacking/count", type="http", auth="user", website=True,
                methods=["GET"], sitemap=False)
    def copack_directory_count(self, **kw):
        """«Ver N plantas» del cajón: mismo filtro que el directorio."""
        self._solo_cliente()
        plantas, _f, _todas = self._copack_directory_search(kw)
        return fd_json_count(len(plantas))

    @http.route("/marketplace/copacking/plants/<socio_ref>", type="http",
                auth="user", website=True)
    def copack_plant(self, socio_ref, **kw):
        self._solo_cliente()
        # Con browse().exists() el registro existe siempre y es la LECTURA de
        # shrimp_user_type la que aplica las reglas de acceso: abrir la ficha de
        # una planta que no esta en el directorio daba un AccessError (error
        # 500) en vez de un 404. El search aplica las reglas al filtrar, asi que
        # lo que no me toca ver sale como "no existe", que es lo honesto.
        # El dominio es explicito (directorio o contraparte con orden): la
        # regla solo cubre al portal, y un usuario interno veia cualquier planta.
        # Por su código (uuid_ref), nunca por id.
        maq = self._planta_visible(socio_ref)
        # La tarifa firme solo si me la dirigieron. Se filtra aqui por el
        # destinatario y la regla de acceso queda como segunda barrera.
        tarifas = request.env["shrimp.copack.tariff"].search(
            _dominio_tarifas_recibidas(self._partner(), maq))
        return request.render("shrimp_copacking.copack_plant", {
            # sudo: la ficha muestra representante/teléfono, que son
            # solo-internos por RPC; la visibilidad ya se comprobó arriba.
            "maq": maq.sudo(),
            "tarifas": tarifas.filtered("is_current"),
            "hoy": fields.Date.context_today(self._partner()),
        })

    # ==================================================================
    # 2. Pedir el servicio
    # ==================================================================
    @http.route("/marketplace/copacking/requests/new", type="http", auth="user",
                website=True, methods=["GET"])
    def copack_request_form(self, **kw):
        self._solo_cliente()
        dirigida = None
        if kw.get("a"):
            # La planta llega por su código; una que no me toca ver (o un id
            # numérico) es un 404 limpio.
            dirigida = self._planta_visible(kw["a"])
        return request.render("shrimp_copacking.copack_request_form", {
            "dirigida": dirigida,
            # Lo que el cliente puede mandar a empacar: sus compras y, si
            # vende, sus lotes. Antes no había forma de elegirlo y el empaque
            # quedaba fuera de la trazabilidad de la compra.
            "origenes": request.env["shrimp.copack.request"].sudo()._shrimp_origenes_del_cliente(
                self._partner()),
            "presentaciones": PRESENTATIONS_WITH_VALUE_ADDED,
            "tallas": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            # Lo tecleado vuelve al formulario cuando la validacion falla. Antes
            # solo sobrevivian el destinatario y el error, asi que el cliente
            # reescribia libras, fechas, insumos y observaciones cada vez.
            "datos": dict({c: (kw.get(c) or "") for c in self._CAMPOS_SOLICITUD},
                          # La talla se compara con el id del registro al pintar
                          # el desplegable: se convierte aqui y no en la
                          # plantilla, que es donde menos se puede depurar.
                          size_grade_id=_entero(kw.get("size_grade_id"), 0)),
            "aviso": kw.get("aviso"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/copacking/requests/new", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copack_request_save(self, **post):
        self._solo_cliente()
        # Las conversiones estaban FUERA del try: un "hola" en las libras o un
        # id de talla manipulado reventaba con un error 500 antes de llegar al
        # savepoint. Ahora convierten sin romper y es el modelo el que rechaza
        # el valor con un mensaje que el cliente puede entender.
        vals = {
            "client_partner_id": self._partner().id,
            "quantity_lb": _decimal(post.get("quantity_lb")),
            "presentation": post.get("presentation") or "entero",
            "needed_from": post.get("needed_from") or False,
            "needed_to": post.get("needed_to") or False,
            "supplies_notes": (post.get("supplies_notes") or "").strip() or False,
            "notes": (post.get("notes") or "").strip() or False,
        }
        if post.get("size_grade_id"):
            vals["size_grade_id"] = _entero(post["size_grade_id"]) or False
        dirigida_ref = (post.get("copacker_partner_ref") or "").strip()
        if dirigida_ref:
            # El create va en sudo: sin esta comprobacion se podia dirigir la
            # solicitud a una planta que el cliente no puede ver (fuera del
            # directorio y sin relacion previa) manipulando el formulario.
            vals["copacker_partner_id"] = self._planta_visible(dirigida_ref).id
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                # El origen (compra o lote propio) llega por su código y se
                # valida contra el cliente antes de nada.
                vals.update(request.env["shrimp.copack.request"].sudo()._shrimp_resolver_origen(
                    self._partner(), post.get("origen_ref")))
                # En sudo a proposito: el cliente se fuerza arriba al socio del
                # usuario, y asi el portal no necesita permiso de creacion sobre
                # el modelo. Ese permiso, con la regla del cliente, dejaba crear
                # solicitudes a nombre de terceros.
                sol = request.env["shrimp.copack.request"].sudo().create(vals)
                sol.action_publish()
        except (ValidationError, ValueError) as e:
            mensaje = e.args[0] if e.args else str(e)
            # Se devuelve TODO lo tecleado, no solo el destinatario: el cliente
            # ya escribio una vez libras, fechas, insumos y observaciones, y
            # perderlo por una fecha mal puesta es la forma mas rapida de que
            # abandone el formulario.
            params = {c: (post.get(c) or "") for c in self._CAMPOS_SOLICITUD}
            params["error"] = flash_message(mensaje)
            if dirigida_ref:
                params["a"] = dirigida_ref
            return request.redirect(
                "/marketplace/copacking/requests/new?%s" % urlencode(params))
        return request.redirect("/marketplace/copacking/requests/%s?mensaje=publicada"
                                % sol.uuid_ref)

    @http.route("/marketplace/copacking/requests", type="http", auth="user", website=True)
    def copack_requests(self, **kw):
        self._solo_cliente()
        S = request.env["shrimp.copack.request"]
        # Sin "mensaje": nadie redirige nunca aqui con uno. La plantilla tenia
        # un aviso de "solicitud publicada" que no se pintaba jamas.
        return request.render("shrimp_copacking.copack_requests", {
            "solicitudes": S.search([("client_partner_id", "=", self._partner().id)]),
        })

    @http.route("/marketplace/copacking/requests/<ref>", type="http", auth="user", website=True)
    def copack_request_detail(self, ref, **kw):
        self._solo_cliente()
        sol = self._mi_solicitud(ref)
        return request.render("shrimp_copacking.copack_request_detail", {
            "sol": sol,
            # Ordenadas por tarifa: lo primero que quiere ver quien compara.
            "ofertas": sol.sudo().offer_ids.filtered(
                lambda o: o.state in ("sent", "accepted")).sorted("rate_per_lb"),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/copacking/requests/<ref>/cancel", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def copack_request_cancel(self, ref, **post):
        """Retirar —y de paso corregir— lo que uno mismo publico.

        El modelo ya sabia cancelar, pero no habia por donde llamarlo: quien
        publicaba 40.000 lb queriendo 4.000 no tenia arreglo, y la solicitud
        equivocada se quedaba viva en la bandeja de todas las plantas.
        """
        self._solo_cliente()
        sol = self._mi_solicitud(ref)
        if sol.state not in ("draft", "published"):
            # Adjudicada ya hay una orden viva detras, y cancelar la solicitud
            # la dejaria huerfana. Eso se resuelve en la orden, no aqui.
            return request.redirect(
                "/marketplace/copacking/requests/%s?error=%s"
                % (sol.uuid_ref, flash_message("Esta solicitud ya no se puede cancelar "
                                       "desde aquí: hay que resolverlo en la orden.")))
        # Los valores se leen ANTES de cancelar: corregir es volver a publicar
        # con lo mismo delante, no empezar de cero.
        params = {
            "quantity_lb": ("%.2f" % sol.quantity_lb) if sol.quantity_lb else "",
            "presentation": sol.presentation or "",
            "size_grade_id": str(sol.size_grade_id.id) if sol.size_grade_id else "",
            "needed_from": str(sol.needed_from or ""),
            "needed_to": str(sol.needed_to or ""),
            "supplies_notes": sol.supplies_notes or "",
            "notes": sol.notes or "",
            "aviso": "corregida",
        }
        if sol.transaction_id:
            params["origen_ref"] = "t:%s" % sol.transaction_id.uuid_ref
        elif sol.product_id:
            params["origen_ref"] = "p:%s" % sol.product_id.uuid_ref
        if sol.copacker_partner_id:
            # Por su código: el id numérico ya no se acepta (404).
            params["a"] = sol.copacker_partner_id.uuid_ref
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                sol.sudo().action_cancel()
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/copacking/requests/%s?error=%s"
                                    % (sol.uuid_ref,
                                       flash_message(str(e.args[0] if e.args else e))))
        if post.get("corregir"):
            # No se edita una solicitud que las plantas ya estan mirando: se
            # cancela y se publica otra. Asi ninguna oferta queda colgando de
            # unas condiciones que cambiaron por detras.
            return request.redirect(
                "/marketplace/copacking/requests/new?%s" % urlencode(params))
        return request.redirect("/marketplace/copacking/requests/%s?mensaje=cancelada"
                                % sol.uuid_ref)

    @http.route("/marketplace/copacking/offers/<ref>/accept", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copack_offer_accept(self, ref, **post):
        self._solo_cliente()
        oferta = request.env["shrimp.copack.offer"].sudo().resolve_ref(ref)
        if not oferta:
            raise NotFound()
        if oferta.request_id.client_partner_id != self._partner():
            raise Forbidden()
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                orden = oferta.action_accept(actor=self._partner())
        except ValidationError as e:
            return request.redirect("/marketplace/copacking/requests/%s?error=%s"
                                    % (oferta.request_id.uuid_ref,
                                       flash_message(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=adjudicada"
                                % orden.uuid_ref)

    # ==================================================================
    # 2b. Empaque propio: la empresa empaca su camarón en su planta
    # ==================================================================
    # Sin solicitud, ofertas, tarifa, comisión ni acta de dos partes: la
    # cuenta (camaronera o empacadora con el perfil Maquilador aprobado)
    # registra el empaque de su propio lote o compra, y lo cierra con una sola
    # conformidad interna. Deja el mismo rastro de trazabilidad que un
    # empaque de terceros («Empaque propio — empacado por …»).
    def _empaque_propio_bloqueo(self):
        """None si la cuenta puede registrar empaque propio; si no, la
        respuesta (página que explica qué perfil falta y dónde agregarlo)."""
        socio = self._partner().sudo()._shrimp_role_holder()
        if socio._shrimp_self_pack_allowed():
            return None
        if not socio._shrimp_can_any("request_copack"):
            raise Forbidden()
        sitio = request.env["website"].sudo()._shrimp_copacker_site()
        estado = socio._shrimp_role_state("maquilador")
        return request.render("shrimp_copacking.copack_self_need_profile", {
            "estado_perfil": estado,
            "url_agregar": landing._url_en_sitio(sitio, "/marketplace/my-account#perfiles"),
            "url_registro": landing._url_en_sitio(sitio, "/register/copacker"),
        })

    def _mi_empaque_propio(self, ref):
        orden = self._mi_orden(ref)
        if not orden.self_packing:
            raise NotFound()
        return orden

    @http.route("/marketplace/copacking/self/new", type="http", auth="user",
                website=True, methods=["GET"], sitemap=False)
    def copack_self_form(self, **kw):
        bloqueo = self._empaque_propio_bloqueo()
        if bloqueo:
            return bloqueo
        socio = self._partner().sudo()._shrimp_role_holder()
        return request.render("shrimp_copacking.copack_self_form", {
            "planta": socio,
            "origenes": request.env["shrimp.copack.request"].sudo()._shrimp_origenes_del_cliente(socio),
            "datos": {"origen_ref": kw.get("origen") or kw.get("origen_ref") or "",
                      "quantity_lb": kw.get("quantity_lb") or "",
                      "supplies_notes": kw.get("supplies_notes") or ""},
            "error": kw.get("error"),
        })

    @http.route("/marketplace/copacking/self/new", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def copack_self_create(self, **post):
        bloqueo = self._empaque_propio_bloqueo()
        if bloqueo:
            return bloqueo
        socio = self._partner().sudo()._shrimp_role_holder()
        try:
            with request.env.cr.savepoint():
                orden = request.env["shrimp.copack.order"].sudo().shrimp_create_self_packing(
                    socio, post.get("origen_ref"), _decimal(post.get("quantity_lb")),
                    supplies_notes=(post.get("supplies_notes") or "").strip() or None)
        except (ValidationError, ValueError) as e:
            params = {k: (post.get(k) or "") for k in ("origen_ref", "quantity_lb", "supplies_notes")}
            params["error"] = flash_message(e.args[0] if e.args else str(e))
            return request.redirect("/marketplace/copacking/self/new?%s" % urlencode(params))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=propio" % orden.uuid_ref)

    def _paso_empaque_propio(self, ref, accion, vals_fn, mensaje):
        orden = self._mi_empaque_propio(ref)
        try:
            with request.env.cr.savepoint():
                vals = vals_fn()
                if vals:
                    orden.sudo().write(vals)
                accion(orden.sudo())
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (orden.uuid_ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=%s" % (orden.uuid_ref, mensaje))

    @http.route("/marketplace/copacking/self/<ref>/reception", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def copack_self_reception(self, ref, **post):
        return self._paso_empaque_propio(ref, lambda o: o.action_register_reception(), lambda: {
            "received_lb": _decimal(post.get("received_lb")),
            "supplies_received": bool(post.get("supplies_received")),
            "supplies_issue": (post.get("supplies_issue") or "").strip() or False,
        }, "recibida")

    @http.route("/marketplace/copacking/self/<ref>/packing", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def copack_self_packing(self, ref, **post):
        def vals():
            presentacion = (post.get("packed_presentation") or "").strip()
            validas = dict(PRESENTATIONS_WITH_VALUE_ADDED)
            return {
                "packed_lb": _decimal(post.get("packed_lb")),
                "boxes": _entero(post.get("boxes"), 0),
                "packed_presentation": presentacion if presentacion in validas else False,
                "packed_presentation_note": (post.get("packed_presentation_note") or "").strip() or False,
            }
        return self._paso_empaque_propio(ref, lambda o: o.action_register_packing(), vals, "empacada")

    @http.route("/marketplace/copacking/self/<ref>/correct", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def copack_self_correct(self, ref, **post):
        return self._paso_empaque_propio(ref, lambda o: o.action_self_correct(), lambda: {},
                                         "corregir")

    @http.route("/marketplace/copacking/self/<ref>/close", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def copack_self_close(self, ref, **post):
        user = request.env.user
        return self._paso_empaque_propio(ref, lambda o: o.action_self_close(user=user),
                                         lambda: {}, "cerrada")

    # ==================================================================
    # 3. Las ordenes y el acta
    # ==================================================================
    @http.route("/marketplace/copacking/orders", type="http", auth="user", website=True)
    def copack_orders(self, **kw):
        socio = self._partner()
        if not (socio._shrimp_can("request_copack") or socio._shrimp_can("provide_copack")):
            raise Forbidden()
        O = request.env["shrimp.copack.order"]
        return request.render("shrimp_copacking.copack_orders", {
            # Dominio explicito: con search([]) un usuario interno (sin las
            # reglas del portal) veia las ordenes de todos los clientes.
            "ordenes": O.search(_dominio_ordenes(socio)),
            "soy_maquilador": socio.shrimp_user_type == "maquilador",
            # Botón «Registrar empaque propio» (o el aviso de qué perfil falta).
            "puede_empaque_propio": socio.sudo()._shrimp_self_pack_allowed(),
            "es_duenio_camaron": socio.sudo()._shrimp_can_any("request_copack"),
            "mensaje": kw.get("mensaje"),
        })

    @http.route("/marketplace/copacking/orders/<ref>", type="http", auth="user", website=True)
    def copack_order_detail(self, ref, **kw):
        orden = self._mi_orden(ref)
        socio = self._partner()
        if orden.self_packing:
            return request.render("shrimp_copacking.copack_self_order_detail", {
                "orden": orden.sudo(),
                "volver_url": "/marketplace/copacking/orders",
                "mensaje": kw.get("mensaje"),
                "error": kw.get("error"),
            })
        rol = "copacker" if socio == orden.copacker_partner_id else "client"
        return request.render("shrimp_copacking.copack_order_detail", {
            "orden": orden,
            "rol": rol,
            # La orden es una sola pantalla para las dos partes a proposito,
            # pero la vuelta no puede serlo: al maquilador se le mandaba aqui
            # tras registrar recepcion o empaque y la migaja lo soltaba en la
            # zona del cliente, que no es la suya y desde la que no encuentra el
            # camino de regreso a su bandeja.
            "volver_url": ("/copacker/orders" if rol == "copacker"
                           else "/marketplace/copacking/orders"),
            "volver_txt": ("Mis órdenes en curso" if rol == "copacker"
                           else "Órdenes"),
            "mi_firma": orden.sudo().acceptance_ids.filtered(lambda f: f.role == rol),
            "otra_firma": orden.sudo().acceptance_ids.filtered(lambda f: f.role != rol),
            # Las rondas anteriores. Reabrir un acta archiva las firmas en vez
            # de borrarlas precisamente para dejar constancia de que hubo un
            # desacuerdo y sobre que cifras; guardarlo y no enseñarlo es tener
            # la prueba y no poder mostrarla.
            "firmas_archivadas": request.env["shrimp.copack.acceptance"].sudo()
            .with_context(active_test=False)
            .search([("order_id", "=", orden.id), ("active", "=", False)],
                    order="ronda desc, role"),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
            # Historial de decisiones y «deshacer mi firma» (firmas de dos partes).
            "historial": request.env["shrimp.signoff.event"].history_for(orden),
            "undo_info": (orden.sudo().acceptance_ids.filtered(lambda f: f.role == rol)[:1]
                          .signoff_undo_info(actor=socio)
                          if orden.sudo().acceptance_ids.filtered(lambda f: f.role == rol)
                          else {}),
            "undo_url": "/marketplace/copacking/orders/%s/undo-signature" % orden.uuid_ref,
        })

    @http.route("/marketplace/copacking/orders/<ref>/cancel", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copack_order_cancel(self, ref, **post):
        """Cancelar el trabajo. Lo puede pedir cualquiera de las dos partes.

        Sin esta ruta, cancelar una solicitud ya adjudicada remitia a la orden
        y alli no habia boton: el cliente que se equivocaba de cantidad se
        quedaba con un trabajo vivo y facturable que nadie podia parar.
        """
        orden = self._mi_orden(ref)
        try:
            with request.env.cr.savepoint():
                orden.sudo().action_cancel()
        except ValidationError as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=cancelada" % ref)

    @http.route("/marketplace/copacking/orders/<ref>/sign", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copack_order_sign(self, ref, **post):
        orden = self._mi_orden(ref)
        socio = self._partner()
        rol = "copacker" if socio == orden.copacker_partner_id else "client"
        firma = orden.sudo().acceptance_ids.filtered(lambda f: f.role == rol)
        if not firma:
            raise NotFound()
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                if post.get("decision") == "accepted":
                    firma.action_accept(actor=socio)
                else:
                    firma.action_reject((post.get("motivo") or "").strip(), actor=socio)
        except ValidationError as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (orden.uuid_ref, flash_message(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=firmada" % orden.uuid_ref)

    @http.route("/marketplace/copacking/orders/<ref>/undo-signature", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copack_order_unsign(self, ref, **post):
        """Cada parte deshace SU firma mientras el acta no esté cerrada."""
        orden = self._mi_orden(ref)
        socio = self._partner()
        rol = "copacker" if socio == orden.copacker_partner_id else "client"
        firma = orden.sudo().acceptance_ids.filtered(lambda f: f.role == rol)[:1]
        if not firma:
            raise NotFound()
        try:
            with request.env.cr.savepoint():
                firma.action_signoff_undo(
                    reason=(post.get("motivo") or "").strip() or None, actor=socio)
        except (ValidationError, UserError, AccessError) as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (orden.uuid_ref, flash_message(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=deshecha" % orden.uuid_ref)

    @http.route("/marketplace/copacking/orders/<ref>/reopen", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def copack_order_reopen(self, ref, **post):
        """Un acta en disputa dejaba la orden congelada para siempre.

        Reabrirla la devuelve a «recibida» para rectificar el empaque y volver a
        firmar. Pueden pedirlo las dos partes: la disputa es entre ellas, y
        dejar la llave en manos de una sola es obligar a la otra a tragar.
        """
        orden = self._mi_orden(ref)
        motivo = (post.get("motivo") or "").strip()
        if not motivo:
            # El modelo lo exige; comprobarlo aqui evita el viaje de ida y
            # vuelta con un error que no dice nada que el formulario no supiera.
            return request.redirect(
                "/marketplace/copacking/orders/%s?error=%s"
                % (orden.uuid_ref, flash_message("Para reabrir el acta hay que decir por "
                                         "qué: es lo que queda escrito de la "
                                         "rectificación.")))
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                orden.sudo().action_reabrir_acta(motivo)
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (orden.uuid_ref,
                                       flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=reabierta"
                                % orden.uuid_ref)

    @http.route("/marketplace/copacking/orders/<ref>/close", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def copack_order_close(self, ref, **post):
        """action_close() existia sin ruta: el acta se firmaba y la orden se
        quedaba en «firmada» para siempre, sin que nadie pudiera darla por
        terminada. La cierra cualquiera de las dos partes, porque a esas alturas
        las dos ya firmaron lo mismo."""
        orden = self._mi_orden(ref)
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                orden.sudo().action_close()
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (orden.uuid_ref,
                                       flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=cerrada"
                                % orden.uuid_ref)


class ShrimpCopacker(http.Controller):
    """Las pantallas de quien presta el servicio: el maquilador.

    Viven en su propio sitio, como los verificadores. El motivo no es estetico:
    el maquilador no compra ni vende camaron, asi que el menu del marketplace
    no le dice nada, y mezclarlo solo le pondria delante pantallas que no puede
    usar.
    """

    def _maq(self):
        socio = request.env.user.partner_id
        if not socio._shrimp_can("provide_copack"):
            raise Forbidden()
        # Un maquilador registrado por la web no opera hasta que la
        # administración aprueba su cuenta (M4).
        require_operational(socio)
        return socio

    def _mi_tarifa(self, ref):
        t = request.env["shrimp.copack.tariff"].sudo().resolve_ref(ref)
        if not t:
            raise NotFound()
        if t.copacker_partner_id != self._maq():
            raise Forbidden()
        return t

    def _mi_orden(self, ref):
        o = request.env["shrimp.copack.order"].sudo().resolve_ref(ref)
        if not o:
            raise NotFound()
        if o.copacker_partner_id != self._maq():
            raise Forbidden()
        return o

    def _redirigir_a_plataforma_maquiladores(self):
        """Equivalente a _redirigir_a_plataforma_verificadores de
        shrimp_verification: un GET a /copacker/* que entra por otro sitio se
        manda (302) al dominio del sitio de empaque, con la misma ruta y la
        misma query string.

        No se redirige si no hay sitio de empaque o no tiene dominio (se sirve
        en el sitio actual, como hasta ahora), si ya se esta en el, si el
        dominio apunta al mismo host (evita bucles) ni en un POST: un 302 a
        otro dominio perderia el cuerpo del formulario y la sesion/CSRF de ese
        dominio no es la misma.
        """
        hreq = request.httprequest
        if hreq.method not in ("GET", "HEAD"):
            return None
        web = getattr(request, "website", False)
        if web and web.sudo().shrimp_is_copacker_site:
            return None
        destino = request.env["website"].sudo()._shrimp_copacker_site()
        dominio = (destino.domain or "").strip().rstrip("/") if destino else ""
        if not dominio:
            return None
        if "://" not in dominio:
            dominio = "%s://%s" % (hreq.scheme, dominio)
        if urlsplit(dominio).netloc.lower() == (hreq.host or "").lower():
            return None
        ruta = hreq.path
        query = hreq.query_string.decode("utf-8", "replace") if hreq.query_string else ""
        if query:
            ruta += "?" + query
        return request.redirect(dominio + ruta, code=302, local=False)

    # ==================================================================
    # La bandeja: lo que hay pidiendo planta
    # ==================================================================
    @http.route("/copacker/inbox", type="http", auth="user", website=True)
    def copacker_inbox(self, **kw):
        salto = self._redirigir_a_plataforma_maquiladores()
        if salto:
            return salto
        maq = self._maq()
        S = request.env["shrimp.copack.request"]
        # Abiertas o dirigidas a mi, explicito: la regla de acceso solo vale
        # para el portal y un usuario interno veia las dirigidas a otros.
        solicitudes = S.search(_dominio_bandeja_maquilador(maq))
        ya_ofertadas = request.env["shrimp.copack.offer"].sudo().search([
            ("copacker_partner_id", "=", maq.id),
            ("request_id", "in", solicitudes.ids),
        ]).mapped("request_id").ids
        return request.render("shrimp_copacking.copacker_inbox", {
            "maq": maq,
            "solicitudes": solicitudes,
            "ya_ofertadas": ya_ofertadas,
            "mensaje": kw.get("mensaje"),
        })

    @http.route("/copacker/requests/<ref>", type="http", auth="user", website=True)
    def copacker_request(self, ref, **kw):
        salto = self._redirigir_a_plataforma_maquiladores()
        if salto:
            return salto
        maq = self._maq()
        sol = request.env["shrimp.copack.request"].sudo().resolve_ref(ref)
        if not sol:
            raise NotFound()
        if sol.copacker_partner_id and sol.copacker_partner_id != maq:
            raise Forbidden()
        mia = request.env["shrimp.copack.offer"].sudo().search([
            ("request_id", "=", sol.id), ("copacker_partner_id", "=", maq.id)], limit=1)
        # Antes esto era un 404 seco en cuanto la solicitud dejaba de estar
        # publicada: el maquilador volvia sobre su propia oferta y se encontraba
        # una pagina de error, sin saber si la habia perdido o si le habian
        # adjudicado el trabajo. Quien oferto o quien la recibio dirigida la
        # sigue viendo; lo que ya no puede es ofertar.
        if sol.state != "published" and not (mia or sol.copacker_partner_id == maq):
            raise NotFound()
        return request.render("shrimp_copacking.copacker_request", {
            "sol": sol, "maq": maq, "mi_oferta": mia,
            "puede_ofertar": sol.state == "published",
            "error": kw.get("error"), "mensaje": kw.get("mensaje"),
        })

    @http.route("/copacker/requests/<ref>/offer", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_offer(self, ref, **post):
        maq = self._maq()
        sol = request.env["shrimp.copack.request"].sudo().resolve_ref(ref)
        if not sol:
            raise NotFound()
        # La misma comprobacion que hace la ruta de lectura. Sin ella, quien
        # conociera el enlace podia ofertar sobre una solicitud dirigida en
        # exclusiva a un competidor.
        if sol.copacker_partner_id and sol.copacker_partner_id != maq:
            raise Forbidden()
        if sol.state != "published":
            # La pagina si se puede seguir viendo; lo que ya no admite es
            # ofertas. Decirlo es mejor que un 404 sobre un boton que estaba ahi.
            return request.redirect("/copacker/requests/%s?error=%s"
                                    % (ref, flash_message("Esta solicitud ya no admite ofertas.")))
        Oferta = request.env["shrimp.copack.offer"].sudo()
        # Las conversiones van antes del try, asi que tienen que ser de las que
        # no revientan: lo que llega del formulario es texto libre.
        vals = {
            "request_id": sol.id, "copacker_partner_id": maq.id,
            "rate_per_lb": _decimal(post.get("rate_per_lb")),
            "capacity_lb": _decimal(post.get("capacity_lb")),
            "available_from": post.get("available_from") or False,
            "available_to": post.get("available_to") or False,
            "notes": (post.get("notes") or "").strip() or False,
        }
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                mia = Oferta.search([("request_id", "=", sol.id),
                                     ("copacker_partner_id", "=", maq.id)], limit=1)
                if mia:
                    mia.write(vals)      # rectificar es normal: se edita, no se duplica
                else:
                    Oferta.create(vals)
        except (ValidationError, ValueError) as e:
            return request.redirect("/copacker/requests/%s?error=%s"
                                    % (ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/copacker/requests/%s?mensaje=ok" % ref)

    @http.route("/copacker/requests/<ref>/withdraw", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_offer_withdraw(self, ref, **post):
        """Retirar una oferta que ya no se puede cumplir.

        El modelo tenia action_withdraw() sin ruta: la planta que se quedaba sin
        cupo no podia hacer nada mas que dejarla viva y cruzar los dedos para
        que el cliente no la aceptara.
        """
        maq = self._maq()
        sol = request.env["shrimp.copack.request"].sudo().resolve_ref(ref)
        if not sol:
            raise NotFound()
        mia = request.env["shrimp.copack.offer"].sudo().search([
            ("request_id", "=", sol.id), ("copacker_partner_id", "=", maq.id)], limit=1)
        if not mia:
            raise NotFound()
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                mia.action_withdraw()
        except (ValidationError, ValueError) as e:
            return request.redirect("/copacker/requests/%s?error=%s"
                                    % (ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/copacker/requests/%s?mensaje=retirada" % ref)

    # ==================================================================
    # Las ordenes: recepcion, empaque y acta
    # ==================================================================
    MAQ_SITUACIONES = [("firmar", "Actas por firmar"), ("cerradas", "Cerradas")]

    def _copacker_orders_search(self, kw):
        """(órdenes, filtros, todas) del maquilador. Parámetros de siempre
        (f=firmar|cerradas) más q (referencia o cliente) y cliente (código)."""
        maq = self._maq()
        O = request.env["shrimp.copack.order"]
        base = [("copacker_partner_id", "=", maq.id)]
        todas = O.search(base)
        f = {
            "q": (kw.get("q") or "").strip(),
            "f": (kw.get("f") or "").strip(),
            "cliente": (kw.get("cliente") or "").strip(),
        }
        if f["f"] not in dict(self.MAQ_SITUACIONES):
            f["f"] = ""
        dominio = list(base)
        if f["f"] == "firmar":
            dominio += [("acceptance_state", "=", "open")]
        elif f["f"] == "cerradas":
            dominio += [("state", "in", ("signed", "closed"))]
        if f["q"]:
            dominio += ["|", ("name", "ilike", f["q"]), ("client_partner_id.name", "ilike", f["q"])]
        clientes = todas.mapped("client_partner_id")
        if f["cliente"]:
            # Por su código, y solo entre los clientes de MIS órdenes.
            elegido = clientes.filtered(lambda c: c.uuid_ref == f["cliente"])
            dominio += [("client_partner_id", "in", elegido.ids or [0])]
        return O.search(dominio), f, todas

    @http.route("/copacker/orders", type="http", auth="user", website=True)
    def copacker_orders(self, **kw):
        salto = self._redirigir_a_plataforma_maquiladores()
        if salto:
            return salto
        ordenes, f, todas = self._copacker_orders_search(kw)
        clientes = todas.mapped("client_partner_id").sorted(lambda c: c.name or "")
        nombres_cliente = {c.uuid_ref: c.name for c in clientes}
        show = {"f": bool(todas), "cliente": len(clientes) > 1}

        def label(group, vals):
            key, val = group[0], vals.get(group[0])
            if not val:
                return None
            if key == "f":
                return dict(self.MAQ_SITUACIONES).get(val)
            if key == "cliente":
                return "Cliente: %s" % nombres_cliente.get(val, val)
            return None

        url = "/copacker/orders"
        tags, clear_url = fd_tags(url, f, [("f",), ("cliente",)], label,
                                  keep={"q": f["q"]}, anchor="#listado")
        fd = fd_context(
            url, len(ordenes), tags, clear_url,
            search={"name": "q", "value": f["q"], "label": "Buscar órdenes",
                    "placeholder": "Referencia o cliente…"},
            toolbar_label="Buscar y filtrar órdenes",
            hidden=[("f", f["f"]), ("cliente", f["cliente"])],
            count_url="/copacker/orders/count",
            noun=("orden", "órdenes"),
            drawer_hidden=[("q", f["q"])],
            keep=["q"],
            has_drawer=any(show.values()),
        )
        return request.render("shrimp_copacking.copacker_orders", {
            "ordenes": ordenes, "filtro": f["f"],
            "mensaje": kw.get("mensaje"), "error": kw.get("error"),
            "filters": f, "fd": fd, "fd_show": show,
            "situacion_opts": self.MAQ_SITUACIONES,
            "cliente_opts": [(c.uuid_ref, c.name) for c in clientes],
        })

    @http.route("/copacker/orders/count", type="http", auth="user", website=True,
                methods=["GET"], sitemap=False)
    def copacker_orders_count(self, **kw):
        """«Ver N órdenes» del cajón: mismo dominio que la lista."""
        ordenes, _f, _todas = self._copacker_orders_search(kw)
        return fd_json_count(len(ordenes))

    @http.route("/copacker/orders/<ref>/reception", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_reception(self, ref, **post):
        orden = self._mi_orden(ref)
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                orden.sudo().write({
                    "received_lb": float(post.get("received_lb") or 0),
                    "supplies_received": bool(post.get("supplies_received")),
                    "supplies_issue": (post.get("supplies_issue") or "").strip() or False,
                })
                orden.sudo().action_register_reception()
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=recibida" % ref)

    @http.route("/copacker/orders/<ref>/packing", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_packing(self, ref, **post):
        orden = self._mi_orden(ref)
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                presentacion = (post.get("packed_presentation") or "").strip()
                valores_validos = dict(PRESENTATIONS_WITH_VALUE_ADDED)
                orden.sudo().write({
                    "packed_lb": float(post.get("packed_lb") or 0),
                    "boxes": int(post.get("boxes") or 0),
                    # Selección única; un texto libre (formulario viejo) va a la nota.
                    "packed_presentation": presentacion if presentacion in valores_validos else False,
                    "packed_presentation_note": (post.get("packed_presentation_note") or (
                        presentacion if presentacion and presentacion not in valores_validos
                        else "")).strip() or False,
                })
                orden.sudo().action_register_packing()
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/copacking/orders/%s?error=%s"
                                    % (ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/copacking/orders/%s?mensaje=empacada" % ref)

    # ==================================================================
    # Tarifas
    # ==================================================================
    @http.route("/copacker/tariffs", type="http", auth="user", website=True)
    def copacker_tariffs(self, **kw):
        salto = self._redirigir_a_plataforma_maquiladores()
        if salto:
            return salto
        maq = self._maq()
        T = request.env["shrimp.copack.tariff"]
        return request.render("shrimp_copacking.copacker_tariffs", {
            "tarifas": T.search([("copacker_partner_id", "=", maq.id)]),
            "mensaje": kw.get("mensaje"),
        })

    @http.route("/copacker/tariffs/new", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_tariff_new(self, **post):
        maq = self._maq()
        t = request.env["shrimp.copack.tariff"].sudo().create({
            "copacker_partner_id": maq.id,
            "name": (post.get("name") or "").strip() or "Tarifa de empaque",
            "valid_from": fields.Date.context_today(maq),
            "open_ended": True,
        })
        return request.redirect("/copacker/tariffs/%s" % t.uuid_ref)

    def _clientes_de_tarifa(self, maq, t):
        """Clientes que `maq` puede poner como destinatarios de la tarifa `t`."""
        # Antes esto era un search en sudo de TODAS las camaroneras y
        # empacadoras: cada maquilador veia el padron completo de clientes de la
        # plataforma, con nombre y tipo, sin haber trabajado nunca con ellos.
        # Eso es la cartera de la competencia servida en una pantalla.
        # Ahora la lista son sus contrapartes reales: quien le dirigio una
        # solicitud y aquellos con los que tiene una orden.
        Solicitud = request.env["shrimp.copack.request"].sudo()
        Orden = request.env["shrimp.copack.order"].sudo()
        ids = set(Solicitud.search(
            [("copacker_partner_id", "=", maq.id)]).mapped("client_partner_id").ids)
        ids |= set(Orden.search(
            [("copacker_partner_id", "=", maq.id)]).mapped("client_partner_id").ids)
        # Los que ya estan en la tarifa siguen saliendo aunque la relacion se
        # haya enfriado: el formulario reemplaza los destinatarios en bloque, y
        # si uno desaparece de la lista, guardar lo borraria sin avisar.
        ids |= set(t.recipient_ids.ids)
        return request.env["res.partner"].sudo().browse(sorted(ids)).filtered(
            lambda p: p._shrimp_can_any("request_copack")).sorted("name")

    @http.route("/copacker/tariffs/<ref>", type="http", auth="user", website=True)
    def copacker_tariff_edit(self, ref, **kw):
        salto = self._redirigir_a_plataforma_maquiladores()
        if salto:
            return salto
        t = self._mi_tarifa(ref)
        maq = self._maq()
        clientes = self._clientes_de_tarifa(maq, t)
        return request.render("shrimp_copacking.copacker_tariff_form", {
            "t": t,
            "clientes": clientes,
            "mensaje": kw.get("mensaje"), "error": kw.get("error"),
        })

    @http.route("/copacker/tariffs/<ref>/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_tariff_save(self, ref, **post):
        t = self._mi_tarifa(ref)
        form = request.httprequest.form
        vals = {
            "name": (post.get("name") or "").strip() or "Tarifa de empaque",
            "min_lot_lb": float(post.get("min_lot_lb") or 0),
            "payment_notes": (post.get("payment_notes") or "").strip() or False,
            "conditions": (post.get("conditions") or "").strip() or False,
            "open_ended": bool(post.get("open_ended")),
            "valid_to": post.get("valid_to") or False,
        }
        # Se fija siempre, incluso vacio: si no, desmarcar a todos no los quita.
        # Solo se aceptan los clientes que el formulario ofrece: el write va en
        # sudo, y sin este filtro un id manipulado dirigia la tarifa a
        # cualquier contacto de la plataforma.
        permitidos = {c.uuid_ref: c.id for c in self._clientes_de_tarifa(self._maq(), t)
                      if c.uuid_ref}
        vals["recipient_ids"] = [(6, 0, [permitidos[x] for x in form.getlist("recipient_refs")
                                         if x in permitidos])]
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                t.sudo().write(vals)
                # Los renglones llegan como columnas paralelas y se reemplazan en
                # bloque: editar en sitio con formularios planos es mas fragil.
                pres = form.getlist("l_presentation")
                fmts = form.getlist("l_format")
                desde = form.getlist("l_from")
                tarif = form.getlist("l_rate")
                t.sudo().line_ids.unlink()
                Line = request.env["shrimp.copack.tariff.line"].sudo()
                for i in range(len(pres)):
                    if not (fmts[i] or "").strip() or not (tarif[i] or "").strip():
                        continue
                    Line.create({
                        "tariff_id": t.id, "presentation": pres[i],
                        "pack_format": fmts[i].strip(),
                        "from_lb": float(desde[i] or 0),
                        "rate_per_lb": float(tarif[i]),
                    })
                if post.get("publicar"):
                    t.sudo().action_publish()
        except (ValidationError, ValueError) as e:
            return request.redirect("/copacker/tariffs/%s?error=%s"
                                    % (ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/copacker/tariffs/%s?mensaje=guardada" % ref)

    @http.route("/copacker/tariffs/<ref>/archive", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_tariff_archive(self, ref, **post):
        """action_archive_tariff() existia sin ruta: una tarifa equivocada se
        quedaba publicada y vigente para sus destinatarios, y desde el portal no
        habia manera de retirarla."""
        t = self._mi_tarifa(ref)
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                t.sudo().action_archive_tariff()
        except (ValidationError, ValueError) as e:
            return request.redirect("/copacker/tariffs/%s?error=%s"
                                    % (ref, flash_message(str(e.args[0] if e.args else e))))
        return request.redirect("/copacker/tariffs/%s?mensaje=archivada" % ref)

    # ==================================================================
    # Perfil y liquidaciones
    # ==================================================================
    @http.route("/copacker/profile", type="http", auth="user", website=True)
    def copacker_profile(self, **kw):
        salto = self._redirigir_a_plataforma_maquiladores()
        if salto:
            return salto
        return request.render("shrimp_copacking.copacker_profile", {
            # sudo: su propio teléfono/representante son solo-internos por RPC.
            "maq": self._maq().sudo(),
            "mensaje": kw.get("mensaje"), "error": kw.get("error"),
        })

    @http.route("/copacker/profile/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_profile_save(self, **post):
        maq = self._maq()
        def num(k):
            # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
            # lo que el modelo rechaza se queda igual en la base. Dejaba
            # solicitudes fantasma en borrador que el cliente no podia ni
            # publicar ni borrar, y libras empacadas que la validacion nego.
            try:
                with request.env.cr.savepoint():
                    return float(post.get(k) or 0)
            except ValueError:
                return 0.0
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                maq.sudo().write({
                    "shrimp_razon_social": (post.get("pack_razon_social") or "").strip() or False,
                    "shrimp_representante": (post.get("pack_representante") or "").strip() or False,
                    "shrimp_telefono": (post.get("pack_telefono") or "").strip() or False,
                    "shrimp_ubicacion": (post.get("pack_ubicacion") or "").strip() or False,
                    "pack_codigo_establecimiento": (post.get("pack_codigo_establecimiento") or "").strip() or False,
                    "pack_habilitacion_desde": post.get("pack_habilitacion_desde") or False,
                    "pack_habilitacion_hasta": post.get("pack_habilitacion_hasta") or False,
                    "pack_presentaciones": (post.get("pack_presentaciones") or "").strip() or False,
                    "pack_tarifa_nota": (post.get("pack_tarifa_nota") or "").strip() or False,
                    "shrimp_capacity_value": num("pack_capacidad_lb_semana"),
                    "shrimp_capacity_unit": "lb_week",
                    "pack_lote_minimo_lb": num("pack_lote_minimo_lb"),
                    "pack_desde_entero": num("pack_desde_entero"),
                    "pack_desde_cola": num("pack_desde_cola"),
                    "pack_desde_valor_agregado": num("pack_desde_valor_agregado"),
                    "pack_en_directorio": bool(post.get("pack_en_directorio")),
                })
        except ValidationError as e:
            return request.redirect("/copacker/profile?error=%s"
                                    % flash_message(e.args[0] if e.args else ""))
        return request.redirect("/copacker/profile?mensaje=guardado")

    @http.route("/copacker/settlements", type="http", auth="user", website=True)
    def copacker_settlements(self, **kw):
        salto = self._redirigir_a_plataforma_maquiladores()
        if salto:
            return salto
        maq = self._maq()
        # El filtro por estado metia en la liquidacion las ordenes con el acta
        # en disputa: se cobraban libras que el cliente todavia esta
        # discutiendo. Quien decide si un trabajo se cobra es el modelo, con
        # es_facturable, que ademas es store=True e indexado justo para poder
        # usarlo aqui como un criterio de busqueda normal.
        ordenes = request.env["shrimp.copack.order"].search([
            ("copacker_partner_id", "=", maq.id),
            ("es_facturable", "=", True),
        ])
        return request.render("shrimp_copacking.copacker_settlements", {
            "ordenes": ordenes,
            "total_servicio": sum(ordenes.mapped("service_amount")),
            "total_comision": sum(ordenes.mapped("platform_amount")),
            "total_libras": sum(ordenes.mapped("packed_lb")),
        })
