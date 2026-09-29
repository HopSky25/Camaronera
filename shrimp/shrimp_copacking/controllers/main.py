from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from werkzeug.exceptions import NotFound, Forbidden
from urllib.parse import quote, urlencode


# Lo que llega por la barra de direcciones o por un formulario es texto libre,
# y un int()/float() desnudo sobre eso es un error 500 esperando a que alguien
# escriba «hola». Estas dos ayudas convierten sin reventar y dejan que rechace
# el valor la validacion del modelo, que si sabe explicarse.
def _entero(valor, por_defecto=0):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return por_defecto


def _decimal(valor, por_defecto=0.0):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return por_defecto


class ShrimpCopackClient(http.Controller):
    """Las pantallas de quien necesita empacar: camaronera y empacadora.

    El orden de las rutas no es casual. Primero el directorio, porque lo
    primero que hace quien busca servicio es mirar quien lo presta y desde
    cuanto; pedir es el segundo paso, no el primero.
    """

    # Lo que el cliente teclea al pedir empaque. Se lista una sola vez porque
    # lo usan tres sitios: el formulario que lo pinta, el guardado que lo
    # devuelve cuando la validacion falla, y la correccion de una solicitud.
    _CAMPOS_SOLICITUD = ("quantity_lb", "presentation", "size_grade_id",
                         "needed_from", "needed_to", "supplies_notes", "notes")

    # ==================================================================
    # Ayudas
    # ==================================================================
    def _partner(self):
        return request.env.user.partner_id

    def _solo_cliente(self):
        """Corta el paso a quien no es dueño de camarón.

        El modelo ya lo impide al guardar, pero sin este corte se le abriria el
        formulario entero a un semillero para reventar al final. Una pantalla
        que no lleva a ningun sitio es peor que no tenerla.
        """
        if self._partner().shrimp_user_type not in ("camaronera", "empacadora"):
            raise Forbidden()

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
    @http.route("/marketplace/empaque", type="http", auth="user", website=True)
    def copack_directory(self, **kw):
        self._solo_cliente()
        # Sin sudo a proposito: la regla de acceso ya deja ver solo a los
        # maquiladores que activaron aparecer en el directorio. Si esto
        # devuelve de mas, el fallo esta en la regla y hay que verlo ahi.
        Socio = request.env["res.partner"]
        dominio = [("shrimp_user_type", "=", "maquilador"),
                   ("pack_en_directorio", "=", True)]
        busca = (kw.get("q") or "").strip()
        if busca:
            dominio += ["|", ("name", "ilike", busca), ("pack_ubicacion", "ilike", busca)]
        return request.render("shrimp_copacking.copack_directory", {
            "maquiladores": Socio.search(dominio, order="name"),
            "q": busca,
            "hoy": fields.Date.context_today(self._partner()),
        })

    @http.route("/marketplace/empaque/planta/<int:socio_id>", type="http",
                auth="user", website=True)
    def copack_plant(self, socio_id, **kw):
        self._solo_cliente()
        # Con browse().exists() el registro existe siempre y es la LECTURA de
        # shrimp_user_type la que aplica las reglas de acceso: abrir la ficha de
        # una planta que no esta en el directorio daba un AccessError (error
        # 500) en vez de un 404. El search aplica las reglas al filtrar, asi que
        # lo que no me toca ver sale como "no existe", que es lo honesto.
        maq = request.env["res.partner"].search(
            [("id", "=", socio_id), ("shrimp_user_type", "=", "maquilador")], limit=1)
        if not maq:
            raise NotFound()
        # La tarifa firme solo si me la dirigieron. La regla de acceso ya filtra;
        # esto solo es para saber si hay algo que enseñar.
        tarifas = request.env["shrimp.copack.tariff"].search([
            ("copacker_partner_id", "=", maq.id),
            ("state", "=", "published"),
        ])
        return request.render("shrimp_copacking.copack_plant", {
            "maq": maq,
            "tarifas": tarifas.filtered("is_current"),
            "hoy": fields.Date.context_today(self._partner()),
        })

    # ==================================================================
    # 2. Pedir el servicio
    # ==================================================================
    @http.route("/marketplace/empaque/solicitar", type="http", auth="user",
                website=True, methods=["GET"])
    def copack_request_form(self, **kw):
        self._solo_cliente()
        dirigida = None
        if kw.get("a"):
            # Doble arreglo: _entero para que "?a=hola" no sea un error 500, y
            # search en vez de browse para que una planta que no me toca ver sea
            # un 404 limpio y no un AccessError al leer shrimp_user_type.
            dirigida = request.env["res.partner"].search(
                [("id", "=", _entero(kw["a"], -1)),
                 ("shrimp_user_type", "=", "maquilador")], limit=1)
            if not dirigida:
                raise NotFound()
        return request.render("shrimp_copacking.copack_request_form", {
            "dirigida": dirigida,
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

    @http.route("/marketplace/empaque/solicitar", type="http", auth="user",
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
        if post.get("copacker_partner_id"):
            vals["copacker_partner_id"] = _entero(post["copacker_partner_id"]) or False
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
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
            params["error"] = str(mensaje)
            if vals.get("copacker_partner_id"):
                params["a"] = vals["copacker_partner_id"]
            return request.redirect(
                "/marketplace/empaque/solicitar?%s" % urlencode(params))
        return request.redirect("/marketplace/empaque/solicitud/%s?mensaje=publicada"
                                % sol.uuid_ref)

    @http.route("/marketplace/empaque/solicitudes", type="http", auth="user", website=True)
    def copack_requests(self, **kw):
        self._solo_cliente()
        S = request.env["shrimp.copack.request"]
        # Sin "mensaje": nadie redirige nunca aqui con uno. La plantilla tenia
        # un aviso de "solicitud publicada" que no se pintaba jamas.
        return request.render("shrimp_copacking.copack_requests", {
            "solicitudes": S.search([("client_partner_id", "=", self._partner().id)]),
        })

    @http.route("/marketplace/empaque/solicitud/<ref>", type="http", auth="user", website=True)
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

    @http.route("/marketplace/empaque/solicitud/<ref>/cancelar", type="http",
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
                "/marketplace/empaque/solicitud/%s?error=%s"
                % (sol.uuid_ref, quote("Esta solicitud ya no se puede cancelar "
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
        if sol.copacker_partner_id:
            params["a"] = str(sol.copacker_partner_id.id)
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                sol.sudo().action_cancel()
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/empaque/solicitud/%s?error=%s"
                                    % (sol.uuid_ref,
                                       quote(str(e.args[0] if e.args else e))))
        if post.get("corregir"):
            # No se edita una solicitud que las plantas ya estan mirando: se
            # cancela y se publica otra. Asi ninguna oferta queda colgando de
            # unas condiciones que cambiaron por detras.
            return request.redirect(
                "/marketplace/empaque/solicitar?%s" % urlencode(params))
        return request.redirect("/marketplace/empaque/solicitud/%s?mensaje=cancelada"
                                % sol.uuid_ref)

    @http.route("/marketplace/empaque/oferta/<ref>/aceptar", type="http", auth="user",
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
            return request.redirect("/marketplace/empaque/solicitud/%s?error=%s"
                                    % (oferta.request_id.uuid_ref,
                                       quote(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=adjudicada"
                                % orden.uuid_ref)

    # ==================================================================
    # 3. Las ordenes y el acta
    # ==================================================================
    @http.route("/marketplace/empaque/ordenes", type="http", auth="user", website=True)
    def copack_orders(self, **kw):
        socio = self._partner()
        if socio.shrimp_user_type not in ("camaronera", "empacadora", "maquilador"):
            raise Forbidden()
        O = request.env["shrimp.copack.order"]
        return request.render("shrimp_copacking.copack_orders", {
            "ordenes": O.search([]),
            "soy_maquilador": socio.shrimp_user_type == "maquilador",
            "mensaje": kw.get("mensaje"),
        })

    @http.route("/marketplace/empaque/orden/<ref>", type="http", auth="user", website=True)
    def copack_order_detail(self, ref, **kw):
        orden = self._mi_orden(ref)
        socio = self._partner()
        rol = "copacker" if socio == orden.copacker_partner_id else "client"
        return request.render("shrimp_copacking.copack_order_detail", {
            "orden": orden,
            "rol": rol,
            # La orden es una sola pantalla para las dos partes a proposito,
            # pero la vuelta no puede serlo: al maquilador se le mandaba aqui
            # tras registrar recepcion o empaque y la migaja lo soltaba en la
            # zona del cliente, que no es la suya y desde la que no encuentra el
            # camino de regreso a su bandeja.
            "volver_url": ("/maquilador/ordenes" if rol == "copacker"
                           else "/marketplace/empaque/ordenes"),
            "volver_txt": ("Mis órdenes en curso" if rol == "copacker"
                           else "Órdenes"),
            "mi_firma": orden.sudo().acceptance_ids.filtered(lambda f: f.role == rol),
            "otra_firma": orden.sudo().acceptance_ids.filtered(lambda f: f.role != rol),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/empaque/orden/<ref>/cancelar", type="http", auth="user",
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
            return request.redirect("/marketplace/empaque/orden/%s?error=%s"
                                    % (ref, quote(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=cancelada" % ref)

    @http.route("/marketplace/empaque/orden/<ref>/firmar", type="http", auth="user",
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
            return request.redirect("/marketplace/empaque/orden/%s?error=%s"
                                    % (orden.uuid_ref, quote(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=firmada" % orden.uuid_ref)

    @http.route("/marketplace/empaque/orden/<ref>/reabrir-acta", type="http",
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
                "/marketplace/empaque/orden/%s?error=%s"
                % (orden.uuid_ref, quote("Para reabrir el acta hay que decir por "
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
            return request.redirect("/marketplace/empaque/orden/%s?error=%s"
                                    % (orden.uuid_ref,
                                       quote(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=reabierta"
                                % orden.uuid_ref)

    @http.route("/marketplace/empaque/orden/<ref>/cerrar", type="http",
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
            return request.redirect("/marketplace/empaque/orden/%s?error=%s"
                                    % (orden.uuid_ref,
                                       quote(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=cerrada"
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
        if socio.shrimp_user_type != "maquilador":
            raise Forbidden()
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

    # ==================================================================
    # La bandeja: lo que hay pidiendo planta
    # ==================================================================
    @http.route("/maquilador/bandeja", type="http", auth="user", website=True)
    def copacker_inbox(self, **kw):
        maq = self._maq()
        S = request.env["shrimp.copack.request"]
        # La regla de acceso ya devuelve solo las abiertas y las dirigidas a mi.
        solicitudes = S.search([("state", "=", "published")])
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

    @http.route("/maquilador/solicitud/<ref>", type="http", auth="user", website=True)
    def copacker_request(self, ref, **kw):
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

    @http.route("/maquilador/solicitud/<ref>/ofertar", type="http", auth="user",
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
            return request.redirect("/maquilador/solicitud/%s?error=%s"
                                    % (ref, quote("Esta solicitud ya no admite ofertas.")))
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
            return request.redirect("/maquilador/solicitud/%s?error=%s"
                                    % (ref, quote(str(e.args[0] if e.args else e))))
        return request.redirect("/maquilador/solicitud/%s?mensaje=ok" % ref)

    @http.route("/maquilador/solicitud/<ref>/retirar", type="http", auth="user",
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
            return request.redirect("/maquilador/solicitud/%s?error=%s"
                                    % (ref, quote(str(e.args[0] if e.args else e))))
        return request.redirect("/maquilador/solicitud/%s?mensaje=retirada" % ref)

    # ==================================================================
    # Las ordenes: recepcion, empaque y acta
    # ==================================================================
    @http.route("/maquilador/ordenes", type="http", auth="user", website=True)
    def copacker_orders(self, **kw):
        maq = self._maq()
        O = request.env["shrimp.copack.order"]
        dominio = [("copacker_partner_id", "=", maq.id)]
        filtro = kw.get("f")
        if filtro == "firmar":
            dominio += [("acceptance_state", "=", "open")]
        elif filtro == "cerradas":
            dominio += [("state", "in", ("signed", "closed"))]
        return request.render("shrimp_copacking.copacker_orders", {
            "ordenes": O.search(dominio), "filtro": filtro,
            "mensaje": kw.get("mensaje"), "error": kw.get("error"),
        })

    @http.route("/maquilador/orden/<ref>/recepcion", type="http", auth="user",
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
            return request.redirect("/marketplace/empaque/orden/%s?error=%s"
                                    % (ref, quote(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=recibida" % ref)

    @http.route("/maquilador/orden/<ref>/empaque", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_packing(self, ref, **post):
        orden = self._mi_orden(ref)
        # En un savepoint: Odoo valida DESPUES de escribir, asi que sin esto
        # lo que el modelo rechaza se queda igual en la base. Dejaba
        # solicitudes fantasma en borrador que el cliente no podia ni
        # publicar ni borrar, y libras empacadas que la validacion nego.
        try:
            with request.env.cr.savepoint():
                orden.sudo().write({
                    "packed_lb": float(post.get("packed_lb") or 0),
                    "boxes": int(post.get("boxes") or 0),
                    "packed_presentation": (post.get("packed_presentation") or "").strip() or False,
                })
                orden.sudo().action_register_packing()
        except (ValidationError, ValueError) as e:
            return request.redirect("/marketplace/empaque/orden/%s?error=%s"
                                    % (ref, quote(str(e.args[0] if e.args else e))))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=empacada" % ref)

    # ==================================================================
    # Tarifas
    # ==================================================================
    @http.route("/maquilador/tarifas", type="http", auth="user", website=True)
    def copacker_tariffs(self, **kw):
        maq = self._maq()
        T = request.env["shrimp.copack.tariff"]
        return request.render("shrimp_copacking.copacker_tariffs", {
            "tarifas": T.search([("copacker_partner_id", "=", maq.id)]),
            "mensaje": kw.get("mensaje"),
        })

    @http.route("/maquilador/tarifa/nueva", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_tariff_new(self, **post):
        maq = self._maq()
        t = request.env["shrimp.copack.tariff"].sudo().create({
            "copacker_partner_id": maq.id,
            "name": (post.get("name") or "").strip() or "Tarifa de empaque",
            "valid_from": fields.Date.context_today(maq),
            "open_ended": True,
        })
        return request.redirect("/maquilador/tarifa/%s" % t.uuid_ref)

    @http.route("/maquilador/tarifa/<ref>", type="http", auth="user", website=True)
    def copacker_tariff_edit(self, ref, **kw):
        t = self._mi_tarifa(ref)
        maq = self._maq()
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
        clientes = request.env["res.partner"].sudo().browse(sorted(ids)).filtered(
            lambda p: p.shrimp_user_type in ("camaronera", "empacadora")).sorted("name")
        return request.render("shrimp_copacking.copacker_tariff_form", {
            "t": t,
            "clientes": clientes,
            "mensaje": kw.get("mensaje"), "error": kw.get("error"),
        })

    @http.route("/maquilador/tarifa/<ref>/guardar", type="http", auth="user",
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
        vals["recipient_ids"] = [(6, 0, [int(x) for x in form.getlist("recipient_ids")
                                         if str(x).isdigit()])]
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
            return request.redirect("/maquilador/tarifa/%s?error=%s"
                                    % (ref, quote(str(e.args[0] if e.args else e))))
        return request.redirect("/maquilador/tarifa/%s?mensaje=guardada" % ref)

    @http.route("/maquilador/tarifa/<ref>/archivar", type="http", auth="user",
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
            return request.redirect("/maquilador/tarifa/%s?error=%s"
                                    % (ref, quote(str(e.args[0] if e.args else e))))
        return request.redirect("/maquilador/tarifa/%s?mensaje=archivada" % ref)

    # ==================================================================
    # Perfil y liquidaciones
    # ==================================================================
    @http.route("/maquilador/perfil", type="http", auth="user", website=True)
    def copacker_profile(self, **kw):
        return request.render("shrimp_copacking.copacker_profile", {
            "maq": self._maq(),
            "mensaje": kw.get("mensaje"), "error": kw.get("error"),
        })

    @http.route("/maquilador/perfil/guardar", type="http", auth="user",
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
                    "pack_razon_social": (post.get("pack_razon_social") or "").strip() or False,
                    "pack_representante": (post.get("pack_representante") or "").strip() or False,
                    "pack_telefono": (post.get("pack_telefono") or "").strip() or False,
                    "pack_ubicacion": (post.get("pack_ubicacion") or "").strip() or False,
                    "pack_codigo_establecimiento": (post.get("pack_codigo_establecimiento") or "").strip() or False,
                    "pack_habilitacion_desde": post.get("pack_habilitacion_desde") or False,
                    "pack_habilitacion_hasta": post.get("pack_habilitacion_hasta") or False,
                    "pack_presentaciones": (post.get("pack_presentaciones") or "").strip() or False,
                    "pack_tarifa_nota": (post.get("pack_tarifa_nota") or "").strip() or False,
                    "pack_capacidad_lb_semana": num("pack_capacidad_lb_semana"),
                    "pack_lote_minimo_lb": num("pack_lote_minimo_lb"),
                    "pack_desde_entero": num("pack_desde_entero"),
                    "pack_desde_cola": num("pack_desde_cola"),
                    "pack_desde_valor_agregado": num("pack_desde_valor_agregado"),
                    "pack_en_directorio": bool(post.get("pack_en_directorio")),
                })
        except ValidationError as e:
            return request.redirect("/maquilador/perfil?error=%s"
                                    % quote(e.args[0] if e.args else ""))
        return request.redirect("/maquilador/perfil?mensaje=guardado")

    @http.route("/maquilador/liquidaciones", type="http", auth="user", website=True)
    def copacker_settlements(self, **kw):
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
