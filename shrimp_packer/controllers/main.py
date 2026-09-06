from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError, UserError
from werkzeug.exceptions import NotFound, Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import ShrimpRegistryController


class ShrimpPackerRegistry(ShrimpRegistryController):
    """Alta de la empacadora.

    Formulario propio y no un paso más del registro compartido: la empacadora
    no publica productos ni tiene lotes, lo que necesita declarar es su planta,
    sus certificaciones y a qué mercados exporta. Mezclarlo con el registro de
    los productores llenaría ese formulario de campos que no aplican.
    """

    @http.route("/registro/empacadora", type="http", auth="public",
                website=True, sitemap=True)
    def registro_empacadora(self, **kw):
        return request.render("shrimp_packer.registry_form_packer", {"values": {}})

    def _registro_form_template(self, user_type):
        if user_type == "empacadora":
            return "shrimp_packer.registry_form_packer"
        return super()._registro_form_template(user_type)

    def _extra_partner_vals(self, user_type, post):
        vals = super()._extra_partner_vals(user_type, post)
        if user_type != "empacadora":
            return vals

        def _t(clave):
            return (post.get(clave) or "").strip() or False

        def _f(clave):
            try:
                return float((post.get(clave) or "0").replace(",", "."))
            except ValueError:
                return 0.0

        vals.update({
            "emp_razon_social": _t("emp_razon_social"),
            "emp_representante": _t("emp_representante"),
            "emp_contacto_comercial": _t("emp_contacto_comercial"),
            "emp_telefono": _t("emp_telefono"),
            "emp_codigo_exportador": _t("emp_codigo_exportador"),
            "emp_planta_nombre": _t("emp_planta_nombre"),
            "emp_planta_ubicacion": _t("emp_planta_ubicacion"),
            "emp_capacidad_lb_dia": _f("emp_capacidad_lb_dia"),
            "emp_cert_bap": bool(post.get("emp_cert_bap")),
            "emp_cert_asc": bool(post.get("emp_cert_asc")),
            "emp_cert_haccp": bool(post.get("emp_cert_haccp")),
            "emp_cert_otras": _t("emp_cert_otras"),
            "emp_aprobacion_sanitaria": _t("emp_aprobacion_sanitaria"),
            "emp_mercado_asia": bool(post.get("emp_mercado_asia")),
            "emp_mercado_europa": bool(post.get("emp_mercado_europa")),
            "emp_mercado_norteamerica": bool(post.get("emp_mercado_norteamerica")),
            "emp_mercado_local": bool(post.get("emp_mercado_local")),
        })
        return vals


class ShrimpPriceListPortal(http.Controller):
    """Listas de precios de compra en el portal.

    Cualquiera con cuenta puede consultar las listas publicadas —esa es la
    gracia: saber a cuánto pagan antes de ofrecer— y cualquiera puede publicar
    la suya, porque en esta cadena el mismo actor compra y vende según el
    eslabón.
    """

    def _partner(self):
        return request.env.user.partner_id

    def _mi_lista(self, ref, editable=False):
        lista = request.env["shrimp.price.list"].sudo().resolve_ref(ref)
        if not lista:
            raise NotFound()
        if editable and lista.issuer_partner_id != self._partner():
            raise Forbidden()
        if not editable and not lista.visible_para(self._partner()):
            raise Forbidden()
        return lista

    # ==================================================================
    # Perfil público de la empacadora
    # ==================================================================
    # Es público a propósito: la camaronera quiere saber a quién le está
    # vendiendo antes de decidir, y a la empacadora le conviene que la
    # encuentren. Lo confidencial son los precios, no las certificaciones.
    @http.route("/marketplace/empacadoras", type="http", auth="public", website=True)
    def packers_directory(self, **kw):
        P = request.env["res.partner"].sudo()
        empacadoras = P.empacadoras_activas()
        busca = (kw.get("q") or "").strip().lower()
        if busca:
            empacadoras = empacadoras.filtered(
                lambda e: busca in (e.name or "").lower()
                or busca in (e.emp_planta_ubicacion or "").lower())
        return request.render("shrimp_packer.packers_directory", {
            "empacadoras": empacadoras,
            "q": kw.get("q") or "",
        })

    @http.route("/marketplace/empacadora/<partner_ref>", type="http", auth="public",
                website=True)
    def packer_profile(self, partner_ref, **kw):
        emp = request.env["res.partner"].sudo().resolve_ref(partner_ref)
        if not emp or emp.shrimp_user_type != "empacadora":
            raise NotFound()

        # Si quien mira tiene una lista vigente de esta empacadora, se le
        # ofrece el atajo: es lo que va a buscar a continuación.
        mi_lista = request.env["shrimp.price.list"].browse()
        if not request.env.user._is_public():
            mi_lista = request.env["shrimp.price.list"].sudo().visibles_para(
                request.env.user.partner_id).filtered(
                lambda l: l.issuer_partner_id == emp)[:1]

        return request.render("shrimp_packer.packer_profile", {
            "emp": emp,
            "mi_lista": mi_lista,
        })

    # ==================================================================
    # Consulta
    # ==================================================================
    @http.route("/marketplace/listas-de-precios", type="http", auth="user", website=True)
    def price_lists(self, **kw):
        L = request.env["shrimp.price.list"].sudo()
        # Solo las que le tocan: publicadas, vigentes y dirigidas a él o a su
        # empresa madre. Nunca las de otro productor.
        vigentes = L.visibles_para(self._partner())
        mias = L.search([("issuer_partner_id", "=", self._partner().id)])
        return request.render("shrimp_packer.price_lists_page", {
            "vigentes": vigentes,
            "mias": mias,
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/listas-de-precios/comparar", type="http", auth="user",
                website=True)
    def price_list_compare(self, **kw):
        """El comparador: qué paga cada empacadora por la misma talla.

        Va antes que la ruta de detalle en el archivo por claridad, pero no por
        necesidad: "comparar" no colisiona con un uuid.
        """
        L = request.env["shrimp.price.list"].sudo()
        partner = self._partner()
        combinaciones = L.combinaciones_disponibles(partner)
        if not combinaciones:
            return request.render("shrimp_packer.price_list_compare", {
                "combinaciones": [], "sel": None, "datos": {}, "cantidad": 0.0})

        elegida = kw.get("combo") or combinaciones[0]["clave"]
        sel = next((c for c in combinaciones if c["clave"] == elegida), combinaciones[0])
        try:
            cantidad = float((kw.get("cantidad") or "0").replace(",", "."))
        except ValueError:
            cantidad = 0.0

        datos = L.comparativa(partner, sel["presentation"], sel["channel"],
                              sel["quality"], cantidad)
        return request.render("shrimp_packer.price_list_compare", {
            "combinaciones": combinaciones,
            "sel": sel,
            "datos": datos,
            "cantidad": cantidad,
        })

    @http.route("/marketplace/listas-de-precios/<ref>", type="http", auth="user",
                website=True)
    def price_list_detail(self, ref, **kw):
        lista = self._mi_lista(ref)
        return request.render("shrimp_packer.price_list_detail", {
            "lista": lista,
            "entero": lista.matriz("entero"),
            "cola": lista.matriz("cola"),
            "es_mia": lista.issuer_partner_id == self._partner(),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    # ==================================================================
    # Publicación
    # ==================================================================
    @http.route("/marketplace/listas-de-precios/nueva", type="http", auth="user",
                website=True)
    def price_list_new(self, **kw):
        return request.render("shrimp_packer.price_list_form", {
            "lista": None,
            "tallas": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/listas-de-precios/guardar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def price_list_save(self, **post):
        L = request.env["shrimp.price.list"].sudo()
        ref = (post.get("ref") or "").strip()
        lista = self._mi_lista(ref, editable=True) if ref else None

        def _f(clave, por_defecto=0.0):
            try:
                return float((post.get(clave) or "").replace(",", ".") or por_defecto)
            except ValueError:
                return por_defecto

        def _i(clave):
            try:
                return int(post.get(clave) or 0)
            except ValueError:
                return 0

        vals = {
            "name": (post.get("name") or "").strip() or _("Lista de precios"),
            "issue_date": post.get("issue_date") or fields.Date.context_today(request.env.user),
            "dispatch_from": post.get("dispatch_from") or False,
            "dispatch_to": post.get("dispatch_to") or False,
            "open_ended": bool(post.get("open_ended")),
            "quality_conditions": (post.get("quality_conditions") or "").strip() or False,
            "advance_pct": _f("advance_pct"),
            "advance_days": _i("advance_days"),
            "balance_days": _i("balance_days"),
            "payment_notes": (post.get("payment_notes") or "").strip() or False,
        }
        destinatarios = [int(x) for x in request.httprequest.form.getlist("recipient_ids")
                         if str(x).isdigit()]
        if destinatarios:
            vals["recipient_ids"] = [(6, 0, destinatarios)]
        try:
            if lista:
                lista.write(vals)
            else:
                vals["issuer_partner_id"] = self._partner().id
                lista = L.create(vals)
        except (ValidationError, UserError) as e:
            destino = "/marketplace/listas-de-precios/%s" % ref if ref \
                else "/marketplace/listas-de-precios/nueva"
            return request.redirect("%s?error=%s" % (destino, (e.args[0] if e.args else "")))

        return request.redirect(
            "/marketplace/listas-de-precios/%s/editar?mensaje=guardada" % lista.uuid_ref)

    @http.route("/marketplace/listas-de-precios/<ref>/editar", type="http", auth="user",
                website=True)
    def price_list_edit(self, ref, **kw):
        lista = self._mi_lista(ref, editable=True)
        return request.render("shrimp_packer.price_list_form", {
            "lista": lista,
            "tallas": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/listas-de-precios/<ref>/renglon", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def price_list_add_line(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        Line = request.env["shrimp.price.list.line"].sudo()
        talla = request.env["shrimp.size.grade"].sudo().browse(
            int(post.get("size_grade_id") or 0))
        if not talla.exists():
            return request.redirect(
                "/marketplace/listas-de-precios/%s/editar?error=%s"
                % (ref, _("Elige una talla.")))
        canal = post.get("channel") or False
        if talla.presentation == "entero":
            canal = False
        try:
            precio = float((post.get("price") or "0").replace(",", "."))
        except ValueError:
            return request.redirect(
                "/marketplace/listas-de-precios/%s/editar?error=%s"
                % (ref, _("El precio no es un número válido.")))

        vals = {
            "price_list_id": lista.id,
            "size_grade_id": talla.id,
            "channel": canal,
            "quality": post.get("quality") or "a",
            "uom": post.get("uom") or ("kg" if talla.presentation == "entero" else "lb"),
            "price": precio,
        }
        # Si ya existe ese renglón se actualiza: es lo que espera quien está
        # corrigiendo un precio, en vez de un error de duplicado.
        existente = Line.search([
            ("price_list_id", "=", lista.id),
            ("size_grade_id", "=", talla.id),
            ("channel", "=", canal),
            ("quality", "=", vals["quality"]),
        ], limit=1)
        try:
            existente.write({"price": precio, "uom": vals["uom"]}) if existente \
                else Line.create(vals)
        except (ValidationError, UserError) as e:
            return request.redirect(
                "/marketplace/listas-de-precios/%s/editar?error=%s"
                % (ref, (e.args[0] if e.args else "")))
        return request.redirect("/marketplace/listas-de-precios/%s/editar?mensaje=renglon" % ref)

    @http.route("/marketplace/listas-de-precios/<ref>/renglon/<int:line_id>/borrar",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_del_line(self, ref, line_id, **post):
        lista = self._mi_lista(ref, editable=True)
        linea = request.env["shrimp.price.list.line"].sudo().browse(line_id)
        if linea.exists() and linea.price_list_id == lista:
            linea.unlink()
        return request.redirect("/marketplace/listas-de-precios/%s/editar" % ref)

    @http.route("/marketplace/listas-de-precios/<ref>/bonificacion", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_add_bonus(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        nombre = (post.get("name") or "").strip()
        if not nombre:
            return request.redirect("/marketplace/listas-de-precios/%s/editar" % ref)
        try:
            importe = float((post.get("amount") or "0").replace(",", "."))
        except ValueError:
            importe = 0.0
        request.env["shrimp.price.list.bonus"].sudo().create({
            "price_list_id": lista.id, "name": nombre, "amount": importe,
            "note": (post.get("note") or "").strip() or False,
        })
        return request.redirect("/marketplace/listas-de-precios/%s/editar?mensaje=bono" % ref)

    @http.route("/marketplace/listas-de-precios/<ref>/bonificacion/<int:bonus_id>/borrar",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_del_bonus(self, ref, bonus_id, **post):
        lista = self._mi_lista(ref, editable=True)
        bono = request.env["shrimp.price.list.bonus"].sudo().browse(bonus_id)
        if bono.exists() and bono.price_list_id == lista:
            bono.unlink()
        return request.redirect("/marketplace/listas-de-precios/%s/editar" % ref)

    @http.route("/marketplace/listas-de-precios/<ref>/publicar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_publish(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        try:
            lista.action_publish()
        except (ValidationError, UserError) as e:
            return request.redirect(
                "/marketplace/listas-de-precios/%s/editar?error=%s"
                % (ref, (e.args[0] if e.args else "")))
        return request.redirect("/marketplace/listas-de-precios/%s?mensaje=publicada" % ref)

    @http.route("/marketplace/listas-de-precios/<ref>/archivar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_archive(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        lista.action_archive_list()
        return request.redirect("/marketplace/listas-de-precios?mensaje=archivada")
