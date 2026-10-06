from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError, UserError
from werkzeug.exceptions import NotFound, Forbidden
from urllib.parse import quote

from odoo.addons.shrimp_user_registry.controllers.main import (
    ShrimpRegistryController, flash_message, read_upload, require_operational)
from odoo.addons.shrimp_marketplace.controllers.account_portal import (
    ShrimpAccountPortalController)
from odoo.addons.shrimp_marketplace.controllers.transaction_portal import (
    ShrimpTransactionPortalController,
)
from odoo.addons.shrimp_marketplace.controllers.utils import current_partner
from odoo.addons.shrimp_marketplace.controllers.filter_drawer import (
    fd_context, fd_date_label, fd_json_count, fd_tags, fd_url)


class ShrimpPackerPurchase(ShrimpTransactionPortalController):
    """Quién compra a quién ya lo decide la matriz de capacidades (la regla
    del modelo que aplica el controlador base). Aquí solo se añade que una
    cuenta que nace pendiente de aprobación (la empacadora) no compra hasta
    que la administración la aprueba."""

    def _check_buyer_can_buy_product(self, buyer_partner, product):
        res = super()._check_buyer_can_buy_product(buyer_partner, product)
        if buyer_partner._shrimp_can("requires_approval"):
            require_operational(buyer_partner)
        return res


class ShrimpPackerRegistry(ShrimpRegistryController):
    """Alta de la empacadora.

    Formulario propio y no un paso más del registro compartido: la empacadora
    no publica productos ni tiene lotes, lo que necesita declarar es su planta,
    sus certificaciones y a qué mercados exporta. Mezclarlo con el registro de
    los productores llenaría ese formulario de campos que no aplican.
    """

    @http.route("/register/packer", type="http", auth="public",
                website=True, sitemap=True)
    def registro_empacadora(self, **kw):
        return request.render("shrimp_packer.registry_form_packer", {"values": {}})

    def _allowed_user_types(self):
        tipos = super()._allowed_user_types()
        # Solo donde se registran los roles del marketplace (no en las
        # plataformas propias de verificadores o maquiladores).
        if tipos & self._signup_types():
            tipos = tipos | {"empacadora"}
        return tipos

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
            # Perfil común (los nombres del formulario siguen siendo emp_*).
            "shrimp_razon_social": _t("emp_razon_social"),
            "shrimp_representante": _t("emp_representante"),
            "shrimp_telefono": _t("emp_telefono"),
            "shrimp_ubicacion": _t("emp_planta_ubicacion"),
            "shrimp_capacity_value": _f("emp_capacidad_lb_dia"),
            "shrimp_capacity_unit": "lb_day",
            "emp_contacto_comercial": _t("emp_contacto_comercial"),
            "emp_codigo_exportador": _t("emp_codigo_exportador"),
            "emp_planta_nombre": _t("emp_planta_nombre"),
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
        return current_partner()

    def _solo_empacadora(self):
        """Corta el paso a quien no emite listas.

        Una lista de precios la publica la empacadora y va dirigida a
        camaroneras. El modelo ya lo impide al guardar (_check_partes), pero
        sin este corte el editor completo se le abría a un semillero o a una
        camaronera: 200, formulario entero, y el error solo al guardar. Una
        pantalla que no lleva a ningun sitio es peor que no tenerla.
        """
        if not self._partner()._shrimp_can("issue_price_lists"):
            raise Forbidden()
        # Y además aprobada por la administración (M4).
        require_operational(self._partner())

    def _mi_lista(self, ref, editable=False):
        lista = request.env["shrimp.price.list"].sudo().resolve_ref(ref)
        if not lista:
            raise NotFound()
        if editable and lista.issuer_partner_id != self._partner():
            raise Forbidden()
        if editable:
            require_operational(self._partner())
        if not editable and not lista.visible_para(self._partner()):
            raise Forbidden()
        return lista

    # ==================================================================
    # La oferta disponible de lo que compra la empacadora
    # ==================================================================
    @http.route("/marketplace/supply", type="http", auth="user", website=True)
    def packer_supply(self, **kw):
        partner = self._partner()
        if partner.shrimp_user_type != "empacadora":
            raise Forbidden()
        require_operational(partner)
        L = request.env["shrimp.price.list"].sudo()
        lista = L.search([
            ("issuer_partner_id", "=", partner.id),
            ("state", "=", "published"),
        ]).filtered("is_current")[:1]
        return request.render("shrimp_packer.packer_supply", {
            "lista": lista,
            "filas": lista.oferta_disponible() if lista else [],
        })

    # ==================================================================
    # Precio sugerido al publicar un lote
    # ==================================================================
    @http.route("/marketplace/suggested-price", type="jsonrpc", auth="user")
    def suggested_price(self, presentation=None, size_grade_id=None, **kw):
        """Qué le pagan hoy por esa talla, para proponerlo al publicar el lote.

        Va por jsonrpc porque el formulario de publicación es de otro módulo:
        así se enriquece sin tocar su plantilla más que con un bloque y un
        script, que es lo que menos conflictos genera.
        """
        partner = self._partner()
        try:
            talla_id = int(size_grade_id or 0)
        except (TypeError, ValueError):
            return {}
        if not presentation or not talla_id:
            return {}

        L = request.env["shrimp.price.list"].sudo()
        listas = L.visibles_para(partner)
        if not listas:
            return {"sin_listas": True}

        lineas = listas.mapped("line_ids").filtered(
            lambda l: l.size_grade_id.id == talla_id
            and l.presentation == presentation
            and (presentation != "cola" or l.channel == "directa"))
        if not lineas:
            return {"sin_precio": True, "listas": len(listas)}

        mejor = max(lineas, key=lambda l: l.price)
        return {
            "precio": round(mejor.price, 2),
            "uom": mejor.uom,
            "empacadora": mejor.price_list_id.issuer_partner_id.name or "",
            "lista": mejor.price_list_id.name or "",
            "cuantas": len({l.price_list_id.issuer_partner_id.id for l in lineas}),
        }

    @http.route("/marketplace/list-price", type="jsonrpc", auth="user")
    def list_price(self, price_list_ref=None, presentation=None, size_grade_id=None, **kw):
        """Precio de una lista concreta para una talla/presentación, ya
        convertido a $/Lb (la unidad del lote). Para autocompletar al asignar
        una lista al producto.

        La lista llega por su código (uuid_ref) y solo responde si el usuario
        puede VERLA (visible_para): antes bastaba un id correlativo para leer
        los precios confidenciales de cualquier lista de la base."""
        from odoo.addons.shrimp_packer.models.shrimp_product import LB_POR_KG
        try:
            talla_id = int(size_grade_id or 0)
        except (TypeError, ValueError):
            return {}
        if not price_list_ref or not talla_id or not presentation:
            return {}
        pl = request.env["shrimp.price.list"].sudo().resolve_ref(price_list_ref)
        if not pl or not pl.visible_para(self._partner()):
            return {}
        lineas = pl.line_ids.filtered(
            lambda l: l.size_grade_id.id == talla_id
            and l.presentation == presentation
            and (presentation != "cola" or l.channel == "directa"))
        if not lineas:
            return {"sin_precio": True}
        linea = max(lineas, key=lambda l: l.price)
        precio = linea.price
        if linea.uom == "kg":
            precio = precio / LB_POR_KG
        return {"precio": round(precio, 2), "uom": "lb"}

    # ==================================================================
    # Perfil público de la empacadora
    # ==================================================================
    # Es público a propósito: la camaronera quiere saber a quién le está
    # vendiendo antes de decidir, y a la empacadora le conviene que la
    # encuentren. Lo confidencial son los precios, no las certificaciones.
    # ------------------------------------------------------------------
    # Directorio de empacadoras: búsqueda + cajón de filtros
    # ------------------------------------------------------------------
    PACKER_CERTS = [("bap", "BAP", "emp_cert_bap"), ("asc", "ASC", "emp_cert_asc"),
                    ("haccp", "HACCP", "emp_cert_haccp")]
    PACKER_MARKETS = [("asia", "Asia", "emp_mercado_asia"), ("europa", "Europa", "emp_mercado_europa"),
                      ("norteamerica", "Norteamérica", "emp_mercado_norteamerica"),
                      ("local", "Mercado local", "emp_mercado_local")]
    PACKER_ORDERS = [("", "Nombre (A–Z)"), ("capacidad", "Mayor capacidad")]

    def _packers_search(self, kw):
        """(empacadoras, filtros, todas). Parámetros: q, cert (repetible),
        mercado (repetible), ubicacion, orden. La usan la página y el conteo."""
        todas = request.env["res.partner"].sudo().empacadoras_activas()
        args = request.httprequest.args
        certs_ok = {c for c, _l, _f in self.PACKER_CERTS}
        mercados_ok = {m for m, _l, _f in self.PACKER_MARKETS}
        f = {
            "q": (kw.get("q") or "").strip(),
            "cert": [c for c in args.getlist("cert") if c in certs_ok],
            "mercado": [m for m in args.getlist("mercado") if m in mercados_ok],
            "ubicacion": (kw.get("ubicacion") or "").strip(),
            "orden": (kw.get("orden") or "").strip(),
        }
        if f["orden"] not in dict(self.PACKER_ORDERS):
            f["orden"] = ""
        empacadoras = todas
        busca = f["q"].lower()
        if busca:
            empacadoras = empacadoras.filtered(
                lambda e: busca in (e.name or "").lower()
                or busca in (e.shrimp_ubicacion or "").lower())
        campos = {c: fld for c, _l, fld in self.PACKER_CERTS + self.PACKER_MARKETS}
        for code in f["cert"] + f["mercado"]:
            empacadoras = empacadoras.filtered(lambda e, fld=campos[code]: e[fld])
        if f["ubicacion"]:
            empacadoras = empacadoras.filtered(
                lambda e: (e.shrimp_ubicacion or "").strip().lower() == f["ubicacion"].lower())
        if f["orden"] == "capacidad":
            empacadoras = empacadoras.sorted(lambda e: -(e.shrimp_capacity_value or 0.0))
        return empacadoras, f, todas

    @http.route("/marketplace/packers", type="http", auth="public", website=True)
    def packers_directory(self, **kw):
        empacadoras, f, todas = self._packers_search(kw)
        # Solo se ofrece lo que distingue a las empacadoras que HAY: una
        # certificación que ninguna declara o una única ubicación no filtran nada.
        certs = [(c, l) for c, l, fld in self.PACKER_CERTS if any(todas.mapped(fld)) or c in f["cert"]]
        mercados = [(m, l) for m, l, fld in self.PACKER_MARKETS if any(todas.mapped(fld)) or m in f["mercado"]]
        ubicaciones = sorted({(e.shrimp_ubicacion or "").strip() for e in todas} - {""})
        show = {"cert": bool(certs), "mercado": bool(mercados), "ubicacion": len(ubicaciones) > 1}
        nombres = {c: l for c, l, _f in self.PACKER_CERTS + self.PACKER_MARKETS}

        def label(group, vals):
            key, val = group[0], vals.get(group[0])
            if not val:
                return None
            if key == "cert":
                return "Certificación: %s" % nombres.get(val, val)
            if key == "mercado":
                return "Exporta a: %s" % nombres.get(val, val)
            if key == "ubicacion":
                return "Ubicación: %s" % val
            return None

        url = "/marketplace/packers"
        tags, clear_url = fd_tags(url, f, [("cert",), ("mercado",), ("ubicacion",)], label,
                                  keep={"q": f["q"], "orden": f["orden"]}, anchor="#listado")
        fd = fd_context(
            url, len(empacadoras), tags, clear_url,
            search={"name": "q", "value": f["q"], "label": "Buscar empacadoras",
                    "placeholder": "Buscar por nombre o ubicación de la planta…"},
            toolbar_label="Buscar y filtrar empacadoras",
            hidden=[("cert", c) for c in f["cert"]] + [("mercado", m) for m in f["mercado"]]
            + [("ubicacion", f["ubicacion"])],
            sort={"name": "orden", "value": f["orden"], "options": self.PACKER_ORDERS},
            count_url="/marketplace/packers/count",
            noun=("empacadora", "empacadoras"),
            drawer_hidden=[("q", f["q"]), ("orden", f["orden"])],
            keep=["q", "orden"],
            has_drawer=any(show.values()),
        )
        return request.render("shrimp_packer.packers_directory", {
            "empacadoras": empacadoras,
            "q": f["q"],
            "filters": f,
            "fd": fd,
            "fd_show": show,
            "cert_opts": certs,
            "mercado_opts": mercados,
            "ubicacion_opts": ubicaciones,
            "total_empacadoras": len(todas),
        })

    @http.route("/marketplace/packers/count", type="http", auth="public", website=True,
                methods=["GET"], sitemap=False)
    def packers_directory_count(self, **kw):
        """«Ver N empacadoras» del cajón: mismo filtro que el directorio."""
        empacadoras, _f, _todas = self._packers_search(kw)
        return fd_json_count(len(empacadoras))

    @http.route("/marketplace/packers/<partner_ref>", type="http", auth="public",
                website=True)
    def packer_profile(self, partner_ref, **kw):
        emp = request.env["res.partner"].sudo().resolve_ref(partner_ref)
        if not emp or not emp._shrimp_has_role("empacadora"):
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
    @http.route("/marketplace/price-lists", type="http", auth="user", website=True)
    def price_lists(self, **kw):
        L = request.env["shrimp.price.list"].sudo()
        # Solo las que le tocan: publicadas, vigentes y dirigidas a él o a su
        # empresa madre. Nunca las de otro productor.
        partner = self._partner()
        # En la consulta sí se muestran las próximas: la lista se reparte con
        # días de antelación para que el productor planifique la cosecha.
        recibidas = L.visibles_para(partner, incluir_futuras=True)
        return request.render("shrimp_packer.price_lists_page", {
            "vigentes": recibidas.filtered("is_current"),
            "proximas": recibidas.filtered("is_upcoming"),
            # La sección de "las que publicas tú" solo tiene sentido para una
            # empacadora: el modelo no deja que otro rol publique.
            "es_empacadora": partner.shrimp_user_type == "empacadora",
            # Oculta el borrador fantasma que deja un "Nueva" sin tocar nada:
            # borrador con nombre por defecto, sin precios/bonos/destinatarios.
            "mias": L.search([("issuer_partner_id", "=", partner.id)]).filtered(
                lambda l: not (
                    l.state == "draft" and l.name == _("Lista de precios")
                    and not l.line_ids and not l.bonus_ids and not l.recipient_ids)),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    def _compare_fd(self, values):
        """Barra («Qué comparar» + Filtros), etiquetas y cajón del comparador.

        La combinación es lo que se compara (va a la vista en la barra, como
        el orden en otras listas); empacadoras, cantidad y fecha de cosecha
        afinan el resultado y viven en el cajón. No hay conteo en vivo (el
        resultado es una tabla comparativa, no una lista): el botón dice
        «Aplicar»."""
        args = request.httprequest.args
        empacadoras = values.get("empacadoras") or request.env["res.partner"].browse()
        por_ref = {e.uuid_ref: e.name for e in empacadoras if e.uuid_ref}
        elegidas_url = [x for x in args.getlist("emp") if x in por_ref]
        sel = values.get("sel")
        combo = sel["clave"] if sel else ""
        cantidad = values.get("cantidad") or 0.0
        fecha = values.get("fecha") or ""
        f = {"emp": elegidas_url,
             "cantidad": ("{:.0f}".format(cantidad) if cantidad else ""),
             "fecha": fecha}

        def label(group, vals):
            key, val = group[0], vals.get(group[0])
            if not val:
                return None
            if key == "emp":
                return "Empacadora: %s" % por_ref.get(val, val)
            if key == "cantidad":
                return "Cantidad: {:,.0f}".format(float(val))
            if key == "fecha":
                return "Cosecha: %s" % fd_date_label(val)
            return None

        url = "/marketplace/price-lists/compare"
        tags, clear_url = fd_tags(url, f, [("emp",), ("cantidad",), ("fecha",)], label,
                                  keep={"combo": combo})
        show = {"emp": len(empacadoras) > 1}
        return {
            "fd": fd_context(
                url, 0, tags, clear_url,
                toolbar_label="Qué comparar",
                hidden=[("emp", e) for e in elegidas_url]
                + [("filtrar", "1" if "filtrar" in args else ""),
                   ("cantidad", f["cantidad"]), ("fecha", fecha)],
                drawer_hidden=[("combo", combo)],
                keep=["combo"],
                apply_label="Aplicar",
                anchor="#comparativa",
            ),
            "fd_show": show,
        }

    @http.route("/marketplace/price-lists/compare", type="http", auth="user",
                website=True)
    def price_list_compare(self, **kw):
        response = self._price_list_compare(**kw)
        qcontext = getattr(response, "qcontext", None)
        if qcontext is not None and qcontext.get("combinaciones"):
            qcontext.update(self._compare_fd(qcontext))
        return response

    def _price_list_compare(self, **kw):
        """El comparador: qué paga cada empacadora por la misma talla.

        Va antes que la ruta de detalle en el archivo por claridad, pero no por
        necesidad: "comparar" no colisiona con un uuid.
        """
        L = request.env["shrimp.price.list"].sudo()
        partner = self._partner()
        combinaciones = L.combinaciones_disponibles(partner)
        empacadoras = L.empacadoras_con_lista(partner)
        if not combinaciones:
            return request.render("shrimp_packer.price_list_compare", {
                "combinaciones": [], "sel": None, "datos": {}, "cantidad": 0.0,
                "empacadoras": empacadoras, "elegidas": [], "sin_seleccion": False,
                "cotizan": set(), "tope": L.MAX_COMPARAR, "recortadas": False,
                "fecha": ""})

        elegida = kw.get("combo") or combinaciones[0]["clave"]
        sel = next((c for c in combinaciones if c["clave"] == elegida), combinaciones[0])
        try:
            cantidad = float((kw.get("cantidad") or "0").replace(",", "."))
        except ValueError:
            cantidad = 0.0

        # El día que piensa cosechar. Es opcional y si no la pone no se supone
        # ninguna: la pantalla dirá que sin fecha no puede decirle quién
        # recibe, que es distinto de decirle que todas reciben. Una fecha mal
        # escrita se descarta en vez de reventar la pantalla.
        fecha = (kw.get("fecha") or "").strip()
        try:
            fecha = fields.Date.to_date(fecha) if fecha else False
        except (ValueError, TypeError):
            fecha = False

        # Cuáles cotizan la combinación elegida. Se calcula antes que nada
        # porque de aquí sale también la selección por defecto.
        cotizan = set(L.comparativa(
            partner, sel["presentation"], sel["channel"], sel["quality"]
        ).get("listas", L.browse()).mapped("issuer_partner_id").ids)

        tope = L.MAX_COMPARAR
        crudas = request.httprequest.args.getlist("emp")
        if crudas:
            # Las empacadoras viajan en la URL por su código, nunca por id.
            por_ref = {e.uuid_ref: e.id for e in empacadoras if e.uuid_ref}
            elegidas = [por_ref[x] for x in crudas if x in por_ref]
        elif "filtrar" in request.httprequest.args:
            elegidas = []
        else:
            # Al llegar a la pantalla se marcan las primeras que sí cotizan la
            # combinación: arrancar con columnas vacías no ayuda a nadie.
            prefiere = [e.id for e in empacadoras if e.id in cotizan]
            resto = [e.id for e in empacadoras if e.id not in cotizan]
            elegidas = (prefiere + resto)[:tope]

        # El tope se aplica en el servidor y no solo en el navegador: la
        # selección viaja en la URL y se puede editar a mano.
        recortadas = len(elegidas) > tope
        elegidas = elegidas[:tope]

        # Desmarcarlas todas es una acción deliberada: mejor decirlo que
        # mostrar la tabla completa como si el filtro no existiera.
        if not elegidas:
            return request.render("shrimp_packer.price_list_compare", {
                "combinaciones": combinaciones, "sel": sel, "datos": {},
                "cantidad": cantidad, "empacadoras": empacadoras,
                "elegidas": [], "sin_seleccion": True, "cotizan": cotizan,
                "tope": tope, "recortadas": False,
                "fecha": fecha.isoformat() if fecha else ""})

        datos = L.comparativa(partner, sel["presentation"], sel["channel"],
                              sel["quality"], cantidad, emisores=elegidas,
                              fecha=fecha)
        return request.render("shrimp_packer.price_list_compare", {
            # Se devuelve tal como viaja en la URL para que el <input type=date>
            # la conserve al refrescar el filtro.
            "fecha": fecha.isoformat() if fecha else "",
            "cotizan": cotizan,
            "tope": tope,
            "recortadas": recortadas,
            "combinaciones": combinaciones,
            "sel": sel,
            "datos": datos,
            "cantidad": cantidad,
            "empacadoras": empacadoras,
            "elegidas": elegidas,
            "sin_seleccion": False,
        })

    @http.route("/marketplace/price-lists/<ref>", type="http", auth="user",
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
    @http.route("/marketplace/price-lists/new", type="http", auth="user",
                website=True)
    def price_list_new(self, **kw):
        self._solo_empacadora()
        # Flujo único: en lugar de pedir primero "guardar cabecera" para recién
        # habilitar precios/Excel/bonificaciones, creamos un borrador al vuelo y
        # llevamos directo al formulario completo. Al dar "Nueva" se ve todo de
        # una vez. El borrador no lo ven los destinatarios hasta publicar.
        L = request.env["shrimp.price.list"].sudo()
        partner = self._partner()
        # Un GET no crea nada (antes creaba un borrador en cada visita, también
        # por un prefetch o un bot). Si ya hay un borrador vacío se reutiliza;
        # si no, se muestra el formulario sin lista y el borrador nace en el
        # primer "Guardar" (POST con CSRF a /guardar).
        predet = _("Lista de precios")
        borrador = L.search([
            ("issuer_partner_id", "=", partner.id),
            ("state", "=", "draft"),
            ("name", "=", predet),
            ("line_ids", "=", False),
            ("bonus_ids", "=", False),
            ("recipient_ids", "=", False),
        ], limit=1)
        if borrador:
            return request.redirect(
                "/marketplace/price-lists/%s/edit" % borrador.uuid_ref)
        return request.render("shrimp_packer.price_list_form", {
            "lista": None,
            "es_nueva": True,
            # El aguaje en curso y los próximos; los pasados nunca.
            "aguajes": request.env["shrimp.aguaje"].sudo().seleccionables(),
            "tallas": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/price-lists/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def price_list_save(self, **post):
        L = request.env["shrimp.price.list"].sudo()
        ref = (post.get("ref") or "").strip()
        if not ref:
            self._solo_empacadora()
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
            "auto_publish": bool(post.get("auto_publish")),
            # La fecha solo se guarda si la publicación automática está activa;
            # si no, se limpia para no dejar una programación colgada.
            "auto_publish_date": (post.get("auto_publish_date") or False)
                                 if post.get("auto_publish") else False,
            "quality_conditions": (post.get("quality_conditions") or "").strip() or False,
            "advance_pct": _f("advance_pct"),
            "advance_days": _i("advance_days"),
            "balance_days": _i("balance_days"),
            "payment_notes": (post.get("payment_notes") or "").strip() or False,
        }
        # El aguaje se elige del calendario de la plataforma y llega por su
        # código. Es obligatorio al crear la lista; en una lista que ya lo
        # tiene no se puede quitar. Solo una lista vieja que nunca lo tuvo se
        # puede seguir guardando sin él. Que no haya pasado lo valida el
        # modelo (y su mensaje es el que ve la empacadora).
        aguaje = request.env["shrimp.aguaje"].sudo().resolve_ref(post.get("aguaje_ref"))
        if aguaje:
            vals["aguaje_id"] = aguaje.id
        elif not lista or lista.aguaje_id:
            destino = "/marketplace/price-lists/%s/edit" % lista.uuid_ref if lista \
                else "/marketplace/price-lists/new"
            return request.redirect("%s?error=%s" % (destino, flash_message(
                _("Elige el aguaje al que rige la lista: el actual o uno próximo."))))
        # Destinatarios: se fija siempre (incluso vacío) para que desmarcar
        # todas las camaroneras realmente las quite. Llegan por su código
        # (uuid_ref) y solo pueden ser camaroneras.
        refs = [x for x in request.httprequest.form.getlist("recipient_refs") if x]
        Partner = request.env["res.partner"].sudo()
        destinatarios = Partner.search([
            ("uuid_ref", "in", refs)] + Partner._shrimp_role_domain("camaronera")).ids if refs else []
        vals["recipient_ids"] = [(6, 0, destinatarios)]

        # Precios por talla: llegan como columnas paralelas (una entrada por
        # fila de la tabla). Se guardan JUNTO con la cabecera —"Guardar
        # borrador" persiste toda la lista— reemplazando las líneas actuales.
        if post.get("tiene_tabla_precios"):
            form = request.httprequest.form
            SizeGrade = request.env["shrimp.size.grade"].sudo()
            Line = request.env["shrimp.price.list.line"]
            UOM = Line._UOM_POR_PRESENTACION
            CAL = Line._CALIDADES_POR_PRESENTACION
            tallas_in = form.getlist("linea_talla")
            canales_in = form.getlist("linea_canal")
            calid_in = form.getlist("linea_calidad")
            precios_in = form.getlist("linea_precio")
            orden, secuencia = {}, []
            for i, tid in enumerate(tallas_in):
                if not str(tid).isdigit():
                    continue
                talla = SizeGrade.browse(int(tid))
                if not talla.exists():
                    continue
                precio_raw = (precios_in[i] if i < len(precios_in) else "").strip()
                if precio_raw == "":
                    destino = "/marketplace/price-lists/%s/edit" % lista.uuid_ref \
                        if lista else "/marketplace/price-lists/new"
                    return request.redirect("%s?error=%s" % (
                        destino, flash_message(_("Toda talla agregada debe tener un precio."))))
                try:
                    precio = float(precio_raw.replace(",", "."))
                except ValueError:
                    destino = "/marketplace/price-lists/%s/edit" % lista.uuid_ref \
                        if lista else "/marketplace/price-lists/new"
                    return request.redirect("%s?error=%s" % (
                        destino, flash_message(_("Hay un precio que no es un número válido."))))
                pres = talla.presentation
                canal = (canales_in[i] if i < len(canales_in) else "") or False
                if pres == "entero":
                    canal = False
                elif not canal:
                    canal = "directa"
                cal = (calid_in[i] if i < len(calid_in) else "") or ""
                permit = CAL.get(pres, ())
                if permit and cal not in permit:
                    cal = permit[0]
                clave = (talla.id, canal, cal or "a")
                if clave not in orden:
                    secuencia.append(clave)
                orden[clave] = {
                    "size_grade_id": talla.id,
                    "channel": canal,
                    "quality": cal or "a",
                    "uom": UOM.get(pres, "lb"),
                    "price": precio,
                }
            vals["line_ids"] = [(5, 0, 0)] + [(0, 0, orden[c]) for c in secuencia]

        try:
            # En un savepoint: si una regla del modelo rechaza la lista (p. ej.
            # un aguaje que ya pasó), no queda nada a medias en la base aunque
            # aquí se capture el error para mostrarlo.
            with request.env.cr.savepoint():
                if lista:
                    lista.write(vals)
                else:
                    vals["issuer_partner_id"] = self._partner().id
                    lista = L.create(vals)
        except (ValidationError, UserError) as e:
            lista = lista.exists() if lista else None
            destino = "/marketplace/price-lists/%s/edit" % lista.uuid_ref if lista \
                else "/marketplace/price-lists/new"
            return request.redirect("%s?error=%s" % (destino, flash_message(e.args[0] if e.args else "")))

        return request.redirect(
            "/marketplace/price-lists/%s/edit?mensaje=guardada" % lista.uuid_ref)

    @http.route("/marketplace/price-lists/<ref>/edit", type="http", auth="user",
                website=True)
    def price_list_edit(self, ref, **kw):
        lista = self._mi_lista(ref, editable=True)
        # Borrador recién creado y sin tocar: mostramos Referencia y Fecha de
        # emisión en blanco (aunque en BD lleven un valor por defecto válido,
        # porque el modelo los exige). El guardado repone los defaults si el
        # usuario los deja vacíos.
        es_nueva = (lista.state == "draft" and lista.name == _("Lista de precios")
                    and not lista.line_ids and not lista.bonus_ids
                    and not lista.recipient_ids)
        return request.render("shrimp_packer.price_list_form", {
            "lista": lista,
            "es_nueva": es_nueva,
            # Vigentes y proximos. Los pasados no se ofrecen —una lista para un
            # aguaje que ya termino no sirve— pero se conserva el que ya tenga
            # asignado para no borrarselo al guardar.
            "aguajes": request.env["shrimp.aguaje"].sudo().seleccionables(
                incluir=lista.aguaje_id),
            "tallas": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/price-lists/<ref>/lines", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def price_list_add_line(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        Line = request.env["shrimp.price.list.line"].sudo()
        talla = request.env["shrimp.size.grade"].sudo().browse(
            int(post.get("size_grade_id") or 0))
        if not talla.exists():
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(_("Elige una talla."))))
        canal = post.get("channel") or False
        if talla.presentation == "entero":
            canal = False
        try:
            precio = float((post.get("price") or "0").replace(",", "."))
        except ValueError:
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(_("El precio no es un número válido."))))

        # La unidad NO se toma del formulario: la fija la presentación. El
        # entero se cotiza en $/Kg y la cola en $/Lb, y un renglón de entero
        # guardado en libras produce un valor de lote con un 120 % de error
        # —el factor kilo/libra— sin que nada avise. Era un desplegable más,
        # y quien carga sesenta precios a mano lo iba a errar tarde o temprano.
        Line_ = request.env["shrimp.price.list.line"]
        unidad = Line_._UOM_POR_PRESENTACION.get(talla.presentation, "lb")
        # Igual con la calidad: si llega una que esa presentación no tiene, se
        # cae a la primera válida en vez de rechazar la carga entera.
        permitidas = Line_._CALIDADES_POR_PRESENTACION.get(talla.presentation, ())
        calidad = post.get("quality") or ""
        if permitidas and calidad not in permitidas:
            calidad = permitidas[0]

        vals = {
            "price_list_id": lista.id,
            "size_grade_id": talla.id,
            "channel": canal,
            "quality": calidad or "a",
            "uom": unidad,
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
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/price-lists/%s/edit?mensaje=renglon" % ref)

    @http.route("/marketplace/price-lists/<ref>/lines/<line_ref>/delete",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_del_line(self, ref, line_ref, **post):
        lista = self._mi_lista(ref, editable=True)
        linea = request.env["shrimp.price.list.line"].sudo().resolve_ref(line_ref)
        if linea and linea.price_list_id == lista:
            linea.unlink()
        return request.redirect("/marketplace/price-lists/%s/edit" % ref)

    @http.route("/marketplace/price-lists/<ref>/bonuses", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_add_bonus(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        nombre = (post.get("name") or "").strip()
        if not nombre:
            return request.redirect("/marketplace/price-lists/%s/edit" % ref)
        try:
            importe = float((post.get("amount") or "0").replace(",", "."))
        except ValueError:
            importe = 0.0
        request.env["shrimp.price.list.bonus"].sudo().create({
            "price_list_id": lista.id, "name": nombre, "amount": importe,
            "note": (post.get("note") or "").strip() or False,
        })
        return request.redirect("/marketplace/price-lists/%s/edit?mensaje=bono" % ref)

    @http.route("/marketplace/price-lists/<ref>/bonuses/<bonus_ref>/delete",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_del_bonus(self, ref, bonus_ref, **post):
        lista = self._mi_lista(ref, editable=True)
        bono = request.env["shrimp.price.list.bonus"].sudo().resolve_ref(bonus_ref)
        if bono and bono.price_list_id == lista:
            bono.unlink()
        return request.redirect("/marketplace/price-lists/%s/edit" % ref)

    # ==================================================================
    # Carga por Excel
    # ==================================================================
    @http.route("/marketplace/price-lists/<ref>/template", type="http",
                auth="user", website=True)
    def price_list_template(self, ref, **kw):
        lista = self._mi_lista(ref, editable=True)
        contenido = lista.plantilla_excel()
        nombre = "Precios-%s.xlsx" % (lista.name or "lista").replace("/", "-")
        return request.make_response(contenido, headers=[
            ("Content-Type",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Length", str(len(contenido))),
            ("Content-Disposition", 'attachment; filename="%s"' % nombre.replace('"', "")),
            ("X-Content-Type-Options", "nosniff"),
        ])

    @http.route("/marketplace/price-lists/<ref>/upload", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_upload(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        # El aguaje también se elige al cargar por Excel: la planilla trae los
        # precios, pero para qué marea rigen lo dice la empacadora aquí.
        aguaje = request.env["shrimp.aguaje"].sudo().resolve_ref(post.get("aguaje_ref"))
        if not aguaje and not lista.aguaje_id:
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(_("Elige el aguaje al que rige la lista: el actual o uno próximo."))))
        if aguaje and aguaje != lista.aguaje_id:
            try:
                with request.env.cr.savepoint():
                    lista.write({"aguaje_id": aguaje.id})
            except (ValidationError, UserError) as e:
                return request.redirect(
                    "/marketplace/price-lists/%s/edit?error=%s"
                    % (ref, flash_message(e.args[0] if e.args else "")))
        archivo = post.get("archivo")
        if not archivo:
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(_("Elige un archivo."))))
        try:
            # Solo .xlsx real (contenido, no extensión) y hasta 5 MB.
            contenido, _mime, _n = read_upload(archivo, allowed={
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/zip"})
        except ValidationError:
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(_("El archivo debe ser una planilla Excel (.xlsx) de hasta 5 MB."))))
        except Exception:  # noqa: BLE001
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(_("No se pudo leer el archivo."))))

        resumen, errores = lista.cargar_excel(contenido)
        if errores:
            # Se muestran los primeros: una lista de cincuenta errores no la
            # lee nadie, y con arreglar los primeros suele caer el resto.
            texto = " · ".join(errores[:5])
            if len(errores) > 5:
                texto += _(" (y %s más)") % (len(errores) - 5)
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s" % (ref, flash_message(texto)))

        detalle = _("%(total)s precios cargados: %(nuevos)s nuevos, "
                    "%(cambiados)s con cambio de precio, %(quitados)s quitados.") % resumen
        return request.redirect(
            "/marketplace/price-lists/%s/edit?mensaje=%s" % (ref, flash_message(detalle)))

    @http.route("/marketplace/price-lists/<ref>/publish", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_publish(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        try:
            lista.action_publish()
        except (ValidationError, UserError) as e:
            return request.redirect(
                "/marketplace/price-lists/%s/edit?error=%s"
                % (ref, flash_message(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/price-lists/%s?mensaje=publicada" % ref)

    @http.route("/marketplace/price-lists/<ref>/duplicate", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_duplicate(self, ref, **post):
        """Parte de la lista anterior para armar la siguiente.

        Es como se trabaja de verdad: nadie escribe sesenta precios de cero
        cada semana, se toma la de la semana pasada y se mueven tres o cuatro.
        La copia nace en borrador y para el aguaje SIGUIENTE al de la lista de
        partida, con las fechas de despacho que salen de él: así no se publica
        por descuido con la vigencia vieja.
        """
        lista = self._mi_lista(ref, editable=True)
        try:
            with request.env.cr.savepoint():
                nueva = lista.copy(lista._valores_copia_siguiente())
        except (ValidationError, UserError) as e:
            return request.redirect("/marketplace/price-lists?error=%s"
                                    % flash_message(e.args[0] if e.args else ""))
        if nueva.aguaje_id:
            aviso = _("Copia creada para el %s. Revisa la referencia, las fechas y "
                      "los precios antes de publicar.") % nueva.aguaje_id.etiqueta
        else:
            aviso = _("Copia creada. Elige el aguaje, cambia la referencia y las "
                      "fechas antes de publicar.")
        return request.redirect(
            "/marketplace/price-lists/%s/edit?mensaje=%s"
            % (nueva.uuid_ref, flash_message(aviso)))

    @http.route("/marketplace/price-lists/<ref>/archive", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_archive(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        lista.action_archive_list()
        return request.redirect("/marketplace/price-lists?mensaje=archivada")

    @http.route("/marketplace/price-lists/<ref>/unpublish", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_unpublish(self, ref, **post):
        """Quita la lista de la vista de los destinatarios devolviéndola a
        borrador, para corregirla y volver a publicar."""
        lista = self._mi_lista(ref, editable=True)
        lista.action_back_to_draft()
        return request.redirect("/marketplace/price-lists?mensaje=despublicada")

    @http.route("/marketplace/price-lists/<ref>/delete", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def price_list_delete(self, ref, **post):
        lista = self._mi_lista(ref, editable=True)
        lista.unlink()
        return request.redirect("/marketplace/price-lists?mensaje=eliminada")


    # ==================================================================
    # A quién le compro: el rendimiento real de cada proveedor
    # ==================================================================
    @http.route("/marketplace/suppliers", type="http", auth="user", website=True)
    def packer_suppliers(self, orden=None, **kw):
        """Lo verificado en planta, ordenado por conveniencia de compra.

        Solo para la empacadora, y no por celo: la tabla cruza el parte de
        planta de SUS compras con el nombre de sus proveedores. Para cualquier
        otro eslabón sería, o una página vacía, o el historial de calidad de un
        tercero que no le corresponde leer.
        """
        self._solo_empacadora()
        return request.render("shrimp_packer.packer_suppliers", {
            "r": request.env["shrimp.proveedor.ranking"].sudo().ranking(
                self._partner(), orden=orden or "puntaje"),
        })


class ShrimpPackerAccount(ShrimpAccountPortalController):
    """La empacadora edita su propio perfil público.

    Su ficha —planta, capacidad, aprobación sanitaria, código de exportador,
    BAP/ASC/HACCP y mercados— es lo primero que mira un productor antes de
    decidir a quién le despacha, y se fijaba UNA sola vez en el alta. Después
    solo se podía cambiar desde el backoffice, o sea llamando a soporte: una
    certificación que caduca o una planta que amplía capacidad se quedaban
    desactualizadas en la única pantalla que el cliente usa para elegir.

    Se extiende el guardado del módulo base en vez de editarlo: los campos
    emp_* son de este módulo, y el rol empacadora también.

    Y por el otro lado, la camaronera guarda aquí el costo de su dinero: es el
    supuesto con el que este módulo traduce "te paga a 21 días" a dólares de
    hoy, y el campo vive en shrimp_packer porque la comparación también.
    """

    def _guardar_extra(self, partner, post):
        res = super()._guardar_extra(partner, post)

        def _f(v):
            try:
                return float((v or "0").replace(",", "."))
            except (TypeError, ValueError, AttributeError):
                return 0.0

        # El costo del dinero de la camaronera. Es el supuesto con el que el
        # comparador traduce "paga a 21 días" a dólares de hoy, así que tiene
        # que poder cambiarlo ella desde su propia pantalla y no por soporte:
        # una cifra que depende de una suposición que el usuario no controla
        # es una cifra que no puede discutir.
        #
        # En blanco se guarda 0, que significa "no la he fijado": entonces se
        # usa el supuesto del sector y la pantalla lo dice. No se escribe el
        # valor por defecto en su ficha, porque entonces parecería suyo.
        if partner.shrimp_user_type == "camaronera":
            res["farm_tasa_descuento_anual"] = max(
                _f(post.get("farm_tasa_descuento_anual")), 0.0)

        if partner.shrimp_user_type != "empacadora":
            return res

        res.update({
            "shrimp_razon_social": post.get("emp_razon_social") or False,
            "shrimp_representante": post.get("emp_representante") or False,
            "emp_contacto_comercial": post.get("emp_contacto_comercial") or False,
            "shrimp_telefono": post.get("emp_telefono") or False,
            "emp_codigo_exportador": post.get("emp_codigo_exportador") or False,
            "emp_planta_nombre": post.get("emp_planta_nombre") or False,
            "shrimp_ubicacion": post.get("emp_planta_ubicacion") or False,
            "shrimp_capacity_value": _f(post.get("emp_capacidad_lb_dia")),
            "shrimp_capacity_unit": "lb_day",
            "emp_aprobacion_sanitaria": post.get("emp_aprobacion_sanitaria") or False,
            # Certificaciones: son booleanos, así que hay que escribir también
            # el False cuando se desmarcan. Si solo se escribieran las marcadas
            # no habría forma de retirar una certificación vencida.
            "emp_cert_bap": bool(post.get("emp_cert_bap")),
            "emp_cert_asc": bool(post.get("emp_cert_asc")),
            "emp_cert_haccp": bool(post.get("emp_cert_haccp")),
            "emp_cert_otras": post.get("emp_cert_otras") or False,
            "emp_mercado_asia": bool(post.get("emp_mercado_asia")),
            "emp_mercado_europa": bool(post.get("emp_mercado_europa")),
            "emp_mercado_norteamerica": bool(post.get("emp_mercado_norteamerica")),
            "emp_mercado_local": bool(post.get("emp_mercado_local")),
        })
        return res


class ShrimpHarvestSimulator(http.Controller):
    """Simulador de cosecha: ¿cosecho ahora o espero?

    La pregunta que se hace un camaronero cada semana. El sistema ya tenía las
    dos mitades de la respuesta —cuánto crece su lote y cuánto paga cada
    empacadora por cada talla— y no las cruzaba.

    Va en un controlador propio y no dentro de ShrimpPriceListPortal porque el
    corte de acceso es el contrario: aquella pantalla es de la empacadora que
    publica listas, y esta es de la camaronera que las recibe.
    """

    def _partner(self):
        return current_partner()

    def _solo_camaronera(self):
        """Corta el paso a quien no cosecha.

        El simulador proyecta el crecimiento de un lote propio contra las
        listas que uno recibe. Para una empacadora sería una pantalla sin
        lotes, y para un laboratorio o un semillero un camino muerto: su
        producto no se vende por talla comercial. Es el mismo criterio que
        _solo_empacadora() aplicado al otro extremo de la cadena.
        """
        if self._partner().shrimp_user_type != "camaronera":
            raise Forbidden()

    def _lotes_simulables(self, partner):
        """Los lotes suyos que se pueden simular.

        Solo engorde con talla: es el único camarón que se vende por talla a
        una empacadora, y la talla es el peldaño desde el que se cuenta el
        salto. Un juvenil de 3 g no tiene talla comercial y ofrecerlo en el
        desplegable sería ofrecer una pantalla que solo sabe decir que no.
        """
        return request.env["shrimp.product"].sudo().search([
            ("seller_partner_id", "=", partner.id),
            ("state", "in", ("draft", "published")),
            ("verification_scope", "=", "adult"),
            ("stage_id.code", "=", "ENGORDE"),
            ("size_grade_id", "!=", False),
        ], order="name")

    @http.route(["/marketplace/simulator", "/marketplace/simulator/<ref>"],
                type="http", auth="user", website=True)
    def harvest_simulator(self, ref=None, **kw):
        self._solo_camaronera()
        partner = self._partner()
        lotes = self._lotes_simulables(partner)

        lote = lotes[:1]
        token = ref or kw.get("lote")
        if token:
            elegido = request.env["shrimp.product"].sudo().resolve_ref(token)
            if not elegido:
                raise NotFound()
            # El lote ajeno no se simula ni se insinúa: 403, no una pantalla
            # vacía. El peso medio y la cantidad de un lote son información
            # comercial de su dueño.
            if elegido.seller_partner_id != partner:
                raise Forbidden()
            lote = elegido

        # Por defecto se cosechan las mismas libras de hoy. Es el supuesto
        # conservador y el que no exige creerse ninguna proyección de biomasa;
        # el otro modo está a un clic y la pantalla explica qué cambia.
        modo = "biomasa" if kw.get("cantidad") == "biomasa" else "constante"

        return request.render("shrimp_packer.harvest_simulator", {
            "lotes": lotes,
            "lote": lote,
            "modo": modo,
            "sim": lote.simulador_cosecha(modo) if lote else {},
        })


class ShrimpFarmerTrackRecord(ShrimpHarvestSimulator):
    """Mi historial verificado: lo que la camaronera puede demostrar.

    El rendimiento de cada camaronera lleva tiempo medido por un tercero y
    guardado lote a lote, pero hasta ahora solo lo leía la empacadora en su
    ranking de proveedores. El productor —que es quien lo produjo— no tenía
    forma de verlo ni de usarlo: salía a vender diciendo 'cómpreme este lote'
    con la misma voz que cualquiera, y su historial de rendimiento, que es su
    mejor argumento, se quedaba del otro lado del mostrador.

    Hereda de ShrimpHarvestSimulator por el corte de acceso: _solo_camaronera
    es exactamente la misma pregunta —¿este usuario cosecha?— y tenerla
    escrita dos veces es tener dos sitios donde equivocarse el día que el rol
    cambie de nombre.
    """

    @http.route("/marketplace/my-track-record", type="http", auth="user", website=True)
    def farmer_track_record(self, **kw):
        """Su propia trayectoria, con la misma cuenta que ve su comprador.

        Solo la camaronera, y solo la suya: el corte no es de confidencialidad
        —cada quien puede ver su propio historial— sino de sentido. Para una
        empacadora esta pantalla saldría vacía para siempre, porque ella no
        vende camarón que se verifique en planta, y ya tiene la mitad que le
        toca en /marketplace/suppliers.
        """
        self._solo_camaronera()
        partner = self._partner()
        return request.render("shrimp_packer.farmer_track_record", {
            "h": request.env["shrimp.proveedor.ranking"].sudo().historial(partner),
        })
