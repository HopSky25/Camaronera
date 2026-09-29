from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from werkzeug.exceptions import NotFound, Forbidden
from urllib.parse import quote


class ShrimpCopackClient(http.Controller):
    """Las pantallas de quien necesita empacar: camaronera y empacadora.

    El orden de las rutas no es casual. Primero el directorio, porque lo
    primero que hace quien busca servicio es mirar quien lo presta y desde
    cuanto; pedir es el segundo paso, no el primero.
    """

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
        maq = request.env["res.partner"].browse(socio_id)
        if not maq.exists() or maq.shrimp_user_type != "maquilador":
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
            dirigida = request.env["res.partner"].browse(int(kw["a"]))
            if not dirigida.exists() or dirigida.shrimp_user_type != "maquilador":
                dirigida = None
        return request.render("shrimp_copacking.copack_request_form", {
            "dirigida": dirigida,
            "tallas": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/empaque/solicitar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copack_request_save(self, **post):
        self._solo_cliente()
        vals = {
            "client_partner_id": self._partner().id,
            "quantity_lb": float(post.get("quantity_lb") or 0),
            "presentation": post.get("presentation") or "entero",
            "needed_from": post.get("needed_from") or False,
            "needed_to": post.get("needed_to") or False,
            "supplies_notes": (post.get("supplies_notes") or "").strip() or False,
            "notes": (post.get("notes") or "").strip() or False,
        }
        if post.get("size_grade_id"):
            vals["size_grade_id"] = int(post["size_grade_id"])
        if post.get("copacker_partner_id"):
            vals["copacker_partner_id"] = int(post["copacker_partner_id"])
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
            destino = "/marketplace/empaque/solicitar"
            if post.get("copacker_partner_id"):
                destino += "?a=%s&" % post["copacker_partner_id"]
            else:
                destino += "?"
            mensaje = e.args[0] if e.args else str(e)
            return request.redirect("%serror=%s" % (destino, quote(str(mensaje))))
        return request.redirect("/marketplace/empaque/solicitud/%s?mensaje=publicada"
                                % sol.uuid_ref)

    @http.route("/marketplace/empaque/solicitudes", type="http", auth="user", website=True)
    def copack_requests(self, **kw):
        self._solo_cliente()
        S = request.env["shrimp.copack.request"]
        return request.render("shrimp_copacking.copack_requests", {
            "solicitudes": S.search([("client_partner_id", "=", self._partner().id)]),
            "mensaje": kw.get("mensaje"),
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
            "mi_firma": orden.sudo().acceptance_ids.filtered(lambda f: f.role == rol),
            "otra_firma": orden.sudo().acceptance_ids.filtered(lambda f: f.role != rol),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

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
        if not sol or sol.state != "published":
            raise NotFound()
        if sol.copacker_partner_id and sol.copacker_partner_id != maq:
            raise Forbidden()
        mia = request.env["shrimp.copack.offer"].sudo().search([
            ("request_id", "=", sol.id), ("copacker_partner_id", "=", maq.id)], limit=1)
        return request.render("shrimp_copacking.copacker_request", {
            "sol": sol, "maq": maq, "mi_oferta": mia,
            "error": kw.get("error"), "mensaje": kw.get("mensaje"),
        })

    @http.route("/maquilador/solicitud/<ref>/ofertar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def copacker_offer(self, ref, **post):
        maq = self._maq()
        sol = request.env["shrimp.copack.request"].sudo().resolve_ref(ref)
        if not sol or sol.state != "published":
            raise NotFound()
        # La misma comprobacion que hace la ruta de lectura. Sin ella, quien
        # conociera el enlace podia ofertar sobre una solicitud dirigida en
        # exclusiva a un competidor.
        if sol.copacker_partner_id and sol.copacker_partner_id != maq:
            raise Forbidden()
        Oferta = request.env["shrimp.copack.offer"].sudo()
        vals = {
            "request_id": sol.id, "copacker_partner_id": maq.id,
            "rate_per_lb": float(post.get("rate_per_lb") or 0),
            "capacity_lb": float(post.get("capacity_lb") or 0),
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
        return request.render("shrimp_copacking.copacker_tariff_form", {
            "t": t,
            "clientes": request.env["res.partner"].sudo().search(
                [("shrimp_user_type", "in", ("camaronera", "empacadora"))], order="name"),
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
        ordenes = request.env["shrimp.copack.order"].search([
            ("copacker_partner_id", "=", maq.id),
            ("state", "in", ("packed", "signed", "closed")),
        ])
        return request.render("shrimp_copacking.copacker_settlements", {
            "ordenes": ordenes,
            "total_servicio": sum(ordenes.mapped("service_amount")),
            "total_comision": sum(ordenes.mapped("platform_amount")),
            "total_libras": sum(ordenes.mapped("packed_lb")),
        })
