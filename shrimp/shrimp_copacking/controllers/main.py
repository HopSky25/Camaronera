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
        try:
            sol = request.env["shrimp.copack.request"].create(vals)
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
        try:
            orden = oferta.action_accept()
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
        try:
            if post.get("decision") == "accepted":
                firma.action_accept()
            else:
                firma.action_reject((post.get("motivo") or "").strip())
        except ValidationError as e:
            return request.redirect("/marketplace/empaque/orden/%s?error=%s"
                                    % (orden.uuid_ref, quote(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/empaque/orden/%s?mensaje=firmada" % orden.uuid_ref)
