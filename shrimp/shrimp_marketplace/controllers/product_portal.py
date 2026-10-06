import base64
import re

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from werkzeug.exceptions import NotFound, Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import (
    IMAGE_MIMETYPES, binary_headers, current_partner, flash_message, read_upload,
    resolve_catalog_certificate, to_int, to_number)

from .filter_drawer import fd_context, fd_json_count, fd_tags, fd_url


class ShrimpProductPortalController(http.Controller):

    def _get_current_partner(self):
        return current_partner()

    def _is_internal(self):
        # Usuario interno (empleado/administrador), no portal/público.
        return request.env.user.has_group("base.group_user")

    def _check_can_manage_products(self):
        # Cada eslabón publica lo suyo; los usuarios internos (admin) pueden
        # publicar en nombre de cualquier vendedor.
        #
        # La camaronera faltaba, y es el eje del negocio: vende el camarón
        # adulto a la empacadora. El menú "Publicar producto" se le ofrecía y
        # daba 403, la portada prometía que "las camaroneras también pueden
        # ofertar camarón", y de 91 transacciones solo UNA tenía una empacadora
        # como parte. La pata principal de la cadena no tenía por dónde
        # empezar.
        if self._is_internal():
            return True
        partner = self._get_current_partner()
        return partner._shrimp_can("sell_products")

    def _seller_partner_options(self):
        Partner = request.env["res.partner"].sudo()
        return Partner.search(
            Partner._shrimp_role_domain(Partner._shrimp_types_with("sell_products")),
            order="name asc")

    def _resolve_seller(self, post):
        """Determina el vendedor del producto.
        - Vendedor real (semillero/lab): él mismo.
        - Interno/admin: el vendedor elegido en el formulario (debe ser semillero/lab).
        Devuelve el partner vendedor o False si no es válido.
        """
        # La camaronera también vende lo suyo (el camarón de engorde a la
        # empacadora). _check_can_manage_products ya la dejaba entrar al
        # formulario, pero aquí seguía sin resolverse como vendedora: el POST
        # de creación terminaba en ?error=seller y el lote no se creaba nunca.
        partner = self._get_current_partner()
        if partner._shrimp_can("sell_products"):
            return partner
        if self._is_internal():
            # El vendedor elegido llega por su código (uuid_ref), nunca por id.
            seller = request.env["res.partner"].sudo().resolve_ref(
                post.get("seller_partner_ref") or post.get("seller_partner_id"))
            if seller and seller._shrimp_can("sell_products"):
                return seller
        return False

    @staticmethod
    def _exige_talla_comercial(product):
        """True solo en camarón de engorde, el único con talla comercial.

        Mismo criterio que usa shrimp_packer para decidir si un lote se cruza
        con las listas de precios: se mira el código del estadío, no el alcance
        de verificación. "adult" incluye al juvenil de una camaronera, que
        tampoco tiene talla de empacadora.
        """
        return (product.stage_id.code or "").strip().upper() == "ENGORDE"

    # La unidad depende del eslabón, no del catálogo: el nauplio y la larva se
    # venden por millares y el camarón adulto por libras. Marcar siempre
    # "Libras" obligaba al semillero a corregirlo en cada publicación, y si se
    # le pasaba, el precio quedaba leído como $/lb de larva.
    _UOM_POR_ROL = {
        "semillero": "millar",
        "laboratorio": "millar",
        "camaronera": "libra",
    }

    def _default_uom(self, seller=None, uom_options=None):
        """Unidad marcada por defecto según quién publica.

        Si el catálogo no tiene esa unidad (se renombró o se archivó) se cae a
        la primera disponible en vez de dejar el formulario sin selección.
        """
        if uom_options is None:
            uom_options = request.env["shrimp.uom"].sudo().search(
                [("active", "=", True)], order="sequence, name")
        partner = seller or self._get_current_partner()
        codigo = self._UOM_POR_ROL.get(partner.shrimp_user_type, "libra")
        uom = uom_options.filtered(
            lambda u: (u.code or "").strip().lower() == codigo)
        return (uom[:1] or uom_options[:1])

    # ------------------------------------------------------------------
    # Formulario de producto (crear / editar)
    # ------------------------------------------------------------------
    # Etapas que publica cada perfil, por tipo de estadío (shrimp.stage.
    # _shrimp_tipo): el semillero y el laboratorio venden nauplio y larva
    # (Nauplio … PL20); la camaronera, camarón (Juvenil y Engorde). Es lo que
    # ya muestran los datos reales; antes el formulario ofrecía las 11 etapas
    # a todos y el servidor aceptaba cualquiera.
    STAGE_TIPOS_POR_ROL = {
        "semillero": ("nauplio", "larva"),
        "laboratorio": ("nauplio", "larva"),
        "camaronera": ("camaron",),
    }
    MAX_PHOTOS = 20

    @classmethod
    def _stage_allowed_for_role(cls, stage, role):
        if not stage:
            return True
        tipos = cls.STAGE_TIPOS_POR_ROL.get(role or "")
        if not tipos:
            return True
        return stage._shrimp_tipo() in tipos

    @staticmethod
    def _stage_from_post(post):
        stage_id = to_int(post.get("stage_id"), 0)
        if not stage_id:
            return request.env["shrimp.stage"].sudo()
        return request.env["shrimp.stage"].sudo().browse(stage_id).exists()

    def _stage_size_problem(self, stage, role, presentation, size_grade_id, check_role=True):
        """Texto del problema de etapa/presentación/talla, o None si vale.

        - La etapa tiene que poder publicarla el perfil del vendedor.
        - El camarón de engorde exige presentación y talla (misma regla que
          el modelo aplica al publicar: es lo que se cruza con las listas de
          precios). En juvenil son opcionales; en nauplio/larva no aplican.
        """
        if check_role and not self._stage_allowed_for_role(stage, role):
            permitidas = request.env["shrimp.stage"].sudo().search(
                [("active", "=", True)]).filtered(
                lambda s: self._stage_allowed_for_role(s, role))
            etiqueta = request.env["res.partner"]._shrimp_type_label(role)
            return _("La etapa «%(e)s» no la puede publicar un perfil %(r)s. "
                     "Etapas permitidas: %(p)s.") % {
                "e": stage.name, "r": (etiqueta or role or "").lower(),
                "p": ", ".join(permitidas.mapped("name")) or "—"}
        if stage and request.env["shrimp.product"]._shrimp_requires_size_grade(stage) and (
                not presentation or not size_grade_id):
            return _("El camarón de engorde necesita presentación y talla.")
        return None

    def _get_product_form_options(self, owner=None, role=None):
        uom_options = request.env["shrimp.uom"].sudo().search(
            [("active", "=", True)], order="sequence, name")
        owner = owner or self._get_current_partner()
        Cert = request.env["shrimp.certificate"].sudo()
        roles_cat = [role, "all"] if role else ["semillero", "laboratorio", "camaronera", "all"]
        uom_por_rol = {}
        for rol, codigo in self._UOM_POR_ROL.items():
            u = uom_options.filtered(lambda x: (x.code or "").strip().lower() == codigo)[:1]
            if u:
                uom_por_rol[rol] = u.id
        return {
            "species_options": request.env["shrimp.species"].sudo().search(
                [("active", "=", True)], order="name asc"),
            "stage_options": request.env["shrimp.stage"].sudo().search(
                [("active", "=", True)], order="sequence asc, name asc"),
            "genetics_options": request.env["shrimp.genetics.line"].sudo().search(
                [("active", "=", True)], order="name asc"),
            "uom_options": uom_options,
            "default_uom_id": self._default_uom(uom_options=uom_options).id or False,
            "size_grade_options": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            # Instalaciones y piscinas del VENDEDOR del lote (en edición puede
            # ser un usuario interno quien edita): _origin_vals solo acepta
            # las suyas.
            "facility_options": request.env["shrimp.partner.facility"].sudo().search(
                [("partner_id", "=", owner.id)], order="name") if owner else
                request.env["shrimp.partner.facility"],
            "pond_options": request.env["shrimp.partner.pond"].sudo().search(
                [("partner_id", "=", owner.id)], order="name") if owner else
                request.env["shrimp.partner.pond"],
            # Catálogo de certificados que puede adjuntar el perfil (el modal
            # «Agregar certificado» lo lee del DOM, sin otra llamada).
            "cert_catalog": Cert.search([("active", "=", True), ("role", "in", roles_cat)],
                                        order="name asc"),
            "form_role": role or "",
            "stage_tipos_por_rol": self.STAGE_TIPOS_POR_ROL,
            "uom_por_rol": uom_por_rol,
            "today": fields.Date.context_today(request.env.user),
            "max_photos": self.MAX_PHOTOS,
        }

    def _get_valid_user_certificates(self, partner):
        today = fields.Date.context_today(request.env.user)
        return request.env["shrimp.user.certificate.line"].sudo().search([
            ("partner_id", "=", partner.id),
            ("status", "=", "approved"),
            ("file_attachment_id", "!=", False),
            "|",
            ("expiry_date", "=", False),
            ("expiry_date", ">=", today),
        ], order="id desc")

    def _get_owned_product(self, product_ref):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product:
            raise NotFound()

        partner = self._get_current_partner()
        # El vendedor gestiona lo suyo; los internos/admin pueden gestionar cualquiera.
        if product.seller_partner_id.id != partner.id and not self._is_internal():
            raise Forbidden()

        return product

    def _create_attachment(self, file_obj, product, name_prefix="", public=False, images_only=False):
        """Adjunto validado por CONTENIDO (magic bytes) y tamaño (5 MB).

        Antes se guardaba el content_type que mandaba el navegador, sin
        límite: un HTML subido como "foto" se servía luego con su tipo.
        Las fotos solo admiten imágenes; los certificados, PDF/JPG/PNG.
        Lanza ValidationError si el archivo no es válido.
        """
        content, mimetype, filename = read_upload(
            file_obj, allowed=IMAGE_MIMETYPES if images_only else None)
        if not content:
            return False

        att = request.env["ir.attachment"].sudo().create({
            "name": f"{name_prefix}{filename}" if name_prefix else filename,
            "type": "binary",
            "datas": base64.b64encode(content),
            "mimetype": mimetype,
            "res_model": "shrimp.product",
            "res_id": product.id,
            "public": False,
        })
        # Se sirven por la ruta del producto con el token del adjunto, no por
        # /web/content con su id.
        att.generate_access_token()
        return att

    @staticmethod
    def _resolve_owned(model, token, partner, partner_field="partner_id"):
        """Registro propio del usuario por su uuid_ref (nunca por id)."""
        rec = request.env[model].sudo().resolve_ref(token) if token else request.env[model].sudo()
        if rec and rec[partner_field].id == partner.id:
            return rec
        return request.env[model].sudo()

    def _origin_vals(self, post, seller):
        """Instalación y piscina de origen por su código, y solo del vendedor."""
        fac = self._resolve_owned("shrimp.partner.facility",
                                  post.get("origin_facility_ref"), seller)
        pond = self._resolve_owned("shrimp.partner.pond",
                                   post.get("origin_pond_ref"), seller)
        vals = {"origin_facility_id": fac.id or False, "origin_pond_id": pond.id or False}
        # Siembras de origen elegidas a mano (solo del vendedor). Sin ninguna
        # marcada, el modelo las propone solas a partir de la piscina.
        if post.get("origin_allocation_present"):
            refs = request.httprequest.form.getlist("origin_allocation_ref")
            Alloc = request.env["shrimp.lot.allocation"].sudo()
            elegidas = Alloc.browse()
            for ref in refs:
                alloc = Alloc.resolve_ref(ref)
                if alloc and alloc.partner_id == seller:
                    elegidas |= alloc
            if elegidas:
                vals["origin_allocation_ids"] = [(6, 0, elegidas.ids)]
        return vals

    def _price_list_val(self, post, seller):
        """Lista de precios por su código, y solo si el vendedor puede verla.

        Antes llegaba el id de cualquier lista de la base y el precio del lote
        se sincronizaba con ella: con ids correlativos se podía atar un lote a
        la lista confidencial de otra empacadora y leer sus precios.
        """
        if "price_list_id" not in request.env["shrimp.product"]._fields:
            return {}
        token = (post.get("price_list_ref") or "").strip()
        if not token:
            return {"price_list_id": False}
        lista = request.env["shrimp.price.list"].sudo().resolve_ref(token)
        if not lista or not lista.visible_para(seller):
            raise ValidationError(_("La lista de precios elegida no está disponible para ti."))
        return {"price_list_id": lista.id}

    # ------------------------------------------------------------------
    # Mis productos: búsqueda, cajón de filtros y conteo en vivo
    # ------------------------------------------------------------------
    MY_PRODUCT_STATES = [
        ("published", "Publicados"), ("draft", "Borradores"),
        ("sold", "Vendidos"), ("archived", "Archivados"),
    ]
    MY_PRODUCT_ORDERS = [
        ("", "Más recientes"),
        ("price_asc", "Precio: menor a mayor"),
        ("price_desc", "Precio: mayor a menor"),
        ("qty_desc", "Cantidad: mayor a menor"),
    ]

    @staticmethod
    def _my_products_state_domain(pstate):
        # "archived" = dado de baja (active=False); el resto restringe a
        # activos por estado.
        if pstate == "archived":
            return [("active", "=", False)]
        if pstate == "published":
            return [("active", "=", True), ("state", "=", "published")]
        if pstate == "draft":
            return [("active", "=", True), ("state", "=", "draft")]
        if pstate == "sold":
            return [("state", "=", "sold")]
        return []

    def _my_products_base_domain(self, partner):
        return [
            ("seller_partner_id", "=", partner.id),
            # Los eliminados (borrado lógico) quedan en histórico pero el usuario
            # ya no los ve ni los puede reactivar.
            ("state", "!=", "cancel"),
        ]

    def _my_products_search(self, kw):
        """(productos, filtros) de «Mis productos». La usan la página y el
        conteo del cajón (/marketplace/products/count): mismo dominio."""
        partner = self._get_current_partner()
        # Se incluyen también los productos dados de baja (archivados) para que el
        # vendedor pueda verlos y reactivarlos; se usa active_test=False.
        Product = request.env["shrimp.product"].sudo().with_context(active_test=False)

        filters = {k: (kw.get(k) or "").strip() for k in (
            "q", "stage", "location", "seller", "price_min", "price_max", "order",
            "pstate", "role")}
        if filters["pstate"] not in dict(self.MY_PRODUCT_STATES):
            filters["pstate"] = ""
        roles = dict(Product._fields["seller_role"].selection)
        if filters["role"] not in roles:
            filters["role"] = ""

        domain = self._my_products_base_domain(partner)
        q = filters["q"]
        if q:
            domain += ["|", "|",
                ("name", "ilike", q),
                ("species_id.name", "ilike", q),
                ("location", "ilike", q),
            ]
        if filters["stage"]:
            try:
                domain.append(("stage_id", "=", int(filters["stage"])))
            except (TypeError, ValueError):
                filters["stage"] = ""
        if filters["location"]:
            domain.append(("location", "ilike", filters["location"]))
        # «Vendedor» ya no se ofrece (en Mis productos el vendedor es uno mismo);
        # se sigue respetando para no romper enlaces guardados.
        if filters["seller"]:
            domain.append(("seller_partner_id.name", "ilike", filters["seller"]))
        if filters["role"]:
            domain.append(("seller_role", "=", filters["role"]))
        domain += self._my_products_state_domain(filters["pstate"])
        for key, op in (("price_min", ">="), ("price_max", "<=")):
            if filters[key]:
                try:
                    domain.append(("price", op, float(filters[key])))
                except (TypeError, ValueError):
                    filters[key] = ""

        order_map = {
            "price_asc": "price asc, create_date desc",
            "price_desc": "price desc, create_date desc",
            "qty_desc": "available_qty desc, create_date desc",
        }
        if filters["order"] not in order_map:
            filters["order"] = ""
        products = Product.search(domain, order=order_map.get(filters["order"], "create_date desc"))
        return products, filters

    def _my_products_filter_context(self, products, filters, vista):
        """Opciones del cajón según los productos que el vendedor TIENE (no
        todo el catálogo): un semillero no ve estadíos de camarón, una cuenta
        con un solo perfil no ve «Perfil», una sola ubicación no se ofrece."""
        partner = self._get_current_partner()
        Product = request.env["shrimp.product"].sudo().with_context(active_test=False)
        base_domain = self._my_products_base_domain(partner)
        base = Product.search(base_domain)

        stages = base.mapped("stage_id").sorted(lambda s: (s.sequence, s.name or ""))
        locations = sorted({p.location.strip() for p in base if p.location and p.location.strip()})
        state_opts = [(code, label) for code, label in self.MY_PRODUCT_STATES
                      if code == filters["pstate"]
                      or Product.search_count(base_domain + self._my_products_state_domain(code))]
        role_labels = dict(Product._fields["seller_role"].selection)
        role_opts = [(r, role_labels[r]) for r in role_labels
                     if r in set(base.mapped("seller_role")) or r == filters["role"]]
        prices = set(base.mapped("price"))

        show = {
            "pstate": len(state_opts) > 1,
            "role": len(role_opts) > 1,
            "location": len(locations) > 1,
            "price": len(prices) > 1,
        }

        url = "/marketplace/products"
        keep = {"q": filters["q"], "order": filters["order"], "vista": vista}
        stage_names = {str(s.id): s.name for s in stages}

        def label(group, vals):
            key = group[0]
            val = vals.get(key) or ""
            if group == ("price_min", "price_max"):
                pmin, pmax = vals.get("price_min"), vals.get("price_max")
                if pmin and pmax:
                    return "Precio $%s–$%s" % (pmin, pmax)
                if pmin:
                    return "Precio desde $%s" % pmin
                return ("Precio hasta $%s" % pmax) if pmax else None
            if not val:
                return None
            if key == "pstate":
                return "Estado: %s" % dict(self.MY_PRODUCT_STATES).get(val, val)
            if key == "stage":
                nombre = stage_names.get(val) or request.env["shrimp.stage"].sudo().browse(
                    int(val)).exists().name
                return ("Estadío: %s" % nombre) if nombre else None
            if key == "role":
                return "Perfil: %s" % role_labels.get(val, val)
            if key == "location":
                return "Ubicación: %s" % val
            if key == "seller":
                return "Vendedor: %s" % val
            return None

        groups = [("pstate",), ("stage",), ("role",), ("location",), ("price_min", "price_max"), ("seller",)]
        tags, clear_url = fd_tags(url, filters, groups, label, keep=keep, anchor="#listado")

        # Chips de estadío arriba (como en el catálogo), solo con los que hay;
        # conservan el resto de filtros.
        sin_stage = {k: v for k, v in dict(filters, vista=vista).items() if k != "stage"}
        stage_chips = [{"label": "Todas", "active": not filters["stage"],
                        "url": fd_url(url, sin_stage, "#listado")}]
        for st in stages:
            stage_chips.append({"label": st.name, "active": filters["stage"] == str(st.id),
                                "url": fd_url(url, dict(sin_stage, stage=st.id), "#listado")})

        con_todo = {k: v for k, v in filters.items() if k != "vista"}
        fd = fd_context(
            url, len(products), tags, clear_url,
            search={"name": "q", "value": filters["q"],
                    "placeholder": "Buscar por nombre, especie o ubicación…",
                    "label": "Buscar en mis productos"},
            toolbar_label="Buscar y ordenar mis productos",
            hidden=[(k, filters[k]) for k in ("stage", "pstate", "role", "location", "seller",
                                              "price_min", "price_max")] + [("vista", vista)],
            sort={"name": "order", "value": filters["order"], "options": self.MY_PRODUCT_ORDERS},
            view={"key": "shrimp_misprod_vista", "target": "misProdGrid", "path": url,
                  "current": vista, "label": "Vista de mis productos",
                  "lista_url": fd_url(url, dict(con_todo, vista="lista"), "#listado"),
                  "grid_url": fd_url(url, dict(con_todo, vista="grid"), "#listado")},
            count_url="/marketplace/products/count",
            noun=("producto", "productos"),
            drawer_hidden=[("q", filters["q"]), ("stage", filters["stage"]),
                           ("seller", filters["seller"]), ("order", filters["order"]),
                           ("vista", vista)],
            keep=["q", "order", "vista"],
            has_drawer=any(show.values()),
        )
        return {
            "fd": fd,
            "fd_show": show,
            "stage_chips": stage_chips if len(stages) > 1 else [],
            "state_opts": state_opts,
            "role_opts": role_opts,
            "location_options": locations,
        }

    @http.route("/marketplace/products", type="http", auth="user", website=True)
    def list_products(self, **kw):
        products, filters = self._my_products_search(kw)
        vista = (kw.get("vista") or "").strip()
        if vista not in ("lista", "grid"):
            vista = ""

        cover_atts = request.env["ir.attachment"].sudo().browse([])
        for product in products:
            first = product.photo_attachment_ids[:1]
            if first:
                cover_atts |= first
        if cover_atts:
            cover_atts.generate_access_token()

        values = {
            "products": products,
            "products_count": len(products),
            "stage_options": request.env["shrimp.stage"].sudo().search([]),
            "filters": filters,
            "vista": vista,
        }
        values.update(self._my_products_filter_context(products, filters, vista))
        return request.render("shrimp_marketplace.products_list", values)

    @http.route("/marketplace/products/count", type="http", auth="user", website=True,
                methods=["GET"], sitemap=False)
    def list_products_count(self, **kw):
        """Cuántos productos daría la selección del cajón («Ver N productos»)."""
        products, _filters = self._my_products_search(kw)
        return fd_json_count(len(products))

    @http.route("/marketplace/products/new", type="http", auth="user", website=True)
    def new_product_form(self, **kw):
        if not self._check_can_manage_products():
            raise Forbidden()

        partner = request.env.user.partner_id
        is_internal = self._is_internal() and not partner._shrimp_can("sell_products")

        # El vendedor real ve sus certificados; el admin elige vendedor y sube archivos.
        valid_cert_lines = (
            request.env["shrimp.user.certificate.line"]
            if is_internal else self._get_valid_user_certificates(partner)
        )

        referer = request.httprequest.referrer or ""
        fallback_url = "/marketplace/products"

        if referer and referer.startswith(request.httprequest.host_url):
            back_url = referer
        elif referer and referer.startswith("/"):
            back_url = referer
        else:
            back_url = fallback_url

        # Perfil con el que se publica: el activo del vendedor. El usuario
        # interno lo elige al escoger vendedor (el formulario lo lee de la
        # opción del vendedor y el servidor lo vuelve a comprobar).
        role = "" if is_internal else (partner.shrimp_user_type or "")
        return request.render("shrimp_marketplace.product_new_form", {
            "back_url": back_url,
            "default_user_certificates": valid_cert_lines,
            "is_internal": is_internal,
            "seller_options": self._seller_partner_options() if is_internal else None,
            "seller_partner": request.env["res.partner"] if is_internal else partner,
            "product": request.env["shrimp.product"],
            "is_edit": False,
            "uom_locked": False,
            "purchase_locked": False,
            "form_action": "/marketplace/products/create",
            **self._get_product_form_options(
                owner=None if is_internal else partner, role=role),
        })

    def _form_error(self, url, code, message=None):
        """Vuelve al formulario con un código de error (y el texto, si hay)."""
        url = f"{url}?error={code}"
        if message:
            url += "&message=" + flash_message(message)
        return request.redirect(url)

    @staticmethod
    def _post_presentation(post, stage):
        """Presentación y talla solo existen en camarón (juvenil/engorde).

        En nauplio y larva el formulario ni las muestra; si llegaran igual
        (formulario viejo, POST a mano) se descartan para no dejar un nauplio
        «entero 30/40»."""
        if not stage or stage._shrimp_tipo() != "camaron":
            return False, False
        presentation = post.get("presentation") or False
        if presentation not in ("entero", "cola"):
            presentation = False
        size_grade = request.env["shrimp.size.grade"].sudo().browse(
            to_int(post.get("size_grade_id"), 0)).exists()
        return presentation, size_grade.id or False

    @http.route("/marketplace/products/create", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def create_product(self, **post):
        if not self._check_can_manage_products():
            raise Forbidden()

        new_url = "/marketplace/products/new"
        # Resolver vendedor: él mismo (semillero/lab) o el elegido por el admin.
        seller = self._resolve_seller(post)
        if not seller:
            return self._form_error(new_url, "seller")
        seller_role = seller.shrimp_user_type

        name = (post.get("name") or "").strip()
        species_id = to_int(post.get("species_id"), 0) or False
        stage = self._stage_from_post(post)
        genetics_line_id = to_int(post.get("genetics_line_id"), 0) or False
        health_status = (post.get("health_status") or "").strip()
        uom_id = to_int(post.get("uom_id"), 0) or False
        if not uom_id:
            # Sin unidad en el POST se toma la del eslabón del vendedor (millar
            # en semillero/laboratorio, libra en camaronera), no libras fijas.
            uom_id = self._default_uom(seller=seller).id or False
        location = (post.get("location") or "").strip()

        initial_qty = to_number(post.get("initial_qty") or post.get("available_qty"))
        avg_size_mg = to_number(post.get("avg_size_mg"))
        survival_rate = to_number(post.get("survival_rate"))
        price = to_number(post.get("price"))

        expected_delivery_date = post.get("expected_delivery_date") or False
        available_from = post.get("available_from") or False
        available_to = post.get("available_to") or False

        if not name:
            return self._form_error(new_url, "name")

        if not stage:
            return self._form_error(new_url, "stage_required")

        if initial_qty <= 0:
            return self._form_error(new_url, "qty")

        if price < 0:
            return self._form_error(new_url, "price")

        presentation, size_grade_id = self._post_presentation(post, stage)
        problema = self._stage_size_problem(stage, seller_role, presentation, size_grade_id)
        if problema:
            return self._form_error(new_url, "validation", problema)

        vals = {
            "name": name,
            "seller_partner_id": seller.id,
            "species_id": species_id,
            "stage_id": stage.id,
            "genetics_line_id": genetics_line_id,
            "avg_size_mg": avg_size_mg,
            "survival_rate": survival_rate,
            "health_status": health_status or False,
            "initial_qty": initial_qty,
            "uom_id": uom_id,
            "price": price,
            "location": location or False,
            "batch_code": (post.get("batch_code") or "").strip() or False,
            "presentation": presentation,
            "size_grade_id": size_grade_id,
            **self._origin_vals(post, seller),
            "expected_delivery_date": expected_delivery_date,
            "available_from": available_from,
            "available_to": available_to,
            "seller_role": seller_role,
            "state": "draft",
            "active": True,
        }

        # Lista de precios (solo si el módulo empacadora aporta el campo). Si se
        # asigna, el modelo toma el precio de la lista.
        try:
            with request.env.cr.savepoint():
                vals.update(self._price_list_val(post, seller))
                product = request.env["shrimp.product"].sudo().create(vals)
                # Fotos: portada y orden según el formulario (photo_order).
                self._save_photos(product, post)
        except ValidationError as e:
            return self._form_error(new_url, "validation", e.args[0] if e.args else "")

        # Guardar certificados (propios del producto y los que vienen del usuario).
        try:
            with request.env.cr.savepoint():
                self._save_product_certificates(product, post)
        except ValidationError as e:
            return self._form_error(f"/marketplace/products/{product.uuid_ref}/edit",
                                    "validation", e.args[0] if e.args else "")

        # Correo: producto creado.
        self._notify_product_event(product, "shrimp_marketplace.mail_template_shrimp_product_created")

        # «Publicar» desde el formulario: se guarda y se publica con las
        # mismas validaciones que el botón del detalle. Si no se puede, el
        # producto queda en borrador y se vuelve a la edición con los motivos.
        if post.get("publish"):
            return self._publish_from_form(product)

        return request.redirect("/marketplace/products")

    def _publish_from_form(self, product):
        if product.state != "draft" or not product.active:
            return request.redirect(f"/marketplace/product/{product.uuid_ref}")
        try:
            with request.env.cr.savepoint():
                product.action_publish()
        except (ValidationError, AccessError) as e:
            return self._form_error(
                f"/marketplace/products/{product.uuid_ref}/edit", "publish",
                e.args[0] if e.args else "")
        self._notify_product_event(product, "shrimp_marketplace.mail_template_shrimp_product_published")
        return request.redirect(f"/marketplace/product/{product.uuid_ref}?published=1")

    def _save_photos(self, product, post):
        """Fotos del producto: altas, bajas, orden y portada.

        - Nuevas: el input múltiple `photo_files` (y `main_photo_file`, que
          sigue valiendo y va primero).
        - Bajas: `remove_photo_<token>` (el token del adjunto, nunca su id).
        - Orden: `photo_order`, lista separada por comas de `e:<token>`
          (foto ya guardada) y `n:<índice en photo_files>` (foto nueva). La
          primera es la portada. Lo que no venga en la lista va al final.

        Las fotos se leen en el orden de ir.attachment (`id desc`): la portada
        de todas las tarjetas es `photo_attachment_ids[:1]`. Para respetar el
        orden pedido sin tocar ese criterio, se recorre la lista desde el final
        y cada foto que no quede con un id mayor que la siguiente se vuelve a
        crear (las nuevas siempre; una ya guardada que se adelanta, como
        copia, que el almacén de archivos no duplica). La foto sustituida se
        borra solo si ningún otro producto la usa (el lote republicado por un
        comprador comparte las fotos).
        """
        files = request.httprequest.files
        nuevas = []
        principal = files.get("main_photo_file")
        if principal and principal.filename:
            contenido, mime, nombre = read_upload(principal, allowed=IMAGE_MIMETYPES)
            if contenido:
                nuevas.append(("main", (contenido, mime, "main_" + nombre)))
        subidas = []
        for file_obj in (files.getlist("photo_files") or [])[:self.MAX_PHOTOS]:
            contenido, mime, nombre = (b"", None, None)
            if file_obj and file_obj.filename:
                contenido, mime, nombre = read_upload(file_obj, allowed=IMAGE_MIMETYPES)
            subidas.append((contenido, mime, nombre) if contenido else None)

        actuales = product.photo_attachment_ids.sudo()
        tokens = {key[len("remove_photo_"):] for key, val in post.items()
                  if key.startswith("remove_photo_") and str(val) in ("1", "true", "on")}
        quitar = actuales.filtered(lambda a: a.access_token and a.access_token in tokens)
        quedan = actuales - quitar
        por_token = {a.access_token: a for a in quedan if a.access_token}

        orden, usadas_e, usadas_n = [], set(), set()
        for _kind, item in nuevas:
            orden.append(("n", item))
        for tok in (post.get("photo_order") or "").split(","):
            tok = tok.strip()
            if tok.startswith("e:"):
                att = por_token.get(tok[2:])
                if att and att.id not in usadas_e:
                    orden.append(("e", att))
                    usadas_e.add(att.id)
            elif tok.startswith("n:"):
                i = to_int(tok[2:], -1)
                if 0 <= i < len(subidas) and subidas[i] and i not in usadas_n:
                    orden.append(("n", subidas[i]))
                    usadas_n.add(i)
        for att in quedan:
            if att.id not in usadas_e:
                orden.append(("e", att))
        for i, item in enumerate(subidas):
            if item and i not in usadas_n:
                orden.append(("n", item))
        # Tope de fotos: las ya guardadas se conservan; las nuevas que no
        # quepan se descartan.
        recortado, total = [], 0
        for kind, item in orden:
            if kind == "n" and total >= self.MAX_PHOTOS:
                continue
            recortado.append((kind, item))
            total += 1
        orden = recortado

        if not quitar and all(k == "e" for k, _i in orden) and \
                [i.id for _k, i in orden] == quedan.ids:
            return  # nada cambia

        Att = request.env["ir.attachment"].sudo()
        final, tope, sustituidas = [], 0, Att.browse()
        for kind, item in reversed(orden):
            if kind == "e" and item.id > tope:
                final.append(item.id)
                tope = item.id
                continue
            if kind == "e":
                datos = {"name": item.name, "raw": item.raw, "mimetype": item.mimetype}
                sustituidas |= item
            else:
                contenido, mime, nombre = item
                datos = {"name": nombre, "raw": contenido, "mimetype": mime}
            att = Att.create({**datos, "type": "binary", "res_model": "shrimp.product",
                              "res_id": product.id, "public": False})
            att.generate_access_token()
            final.append(att.id)
            tope = att.id
        final.reverse()
        product.write({"photo_attachment_ids": [(6, 0, final)]})
        self._unlink_unused_photos(quitar | sustituidas)

    @staticmethod
    def _unlink_unused_photos(atts):
        """Borra fotos que ya no usa ningún producto (una foto puede estar
        compartida con el lote que el comprador republicó)."""
        if not atts:
            return
        otros = request.env["shrimp.product"].sudo().with_context(
            active_test=False).search([("photo_attachment_ids", "in", atts.ids)])
        (atts - otros.mapped("photo_attachment_ids")).sudo().unlink()

    def _edit_product_certificates(self, product, post):
        """Aplica cambios a líneas de certificado YA guardadas del producto.
        Solo las propias del producto (las heredadas del usuario no se editan).
        Campos: edit_cert_<uuid>_number/_issue_date/_expiry_date y opcional
        _file. La línea se identifica por su código, nunca por id; un cambio
        la devuelve a revisión."""
        Line = request.env["shrimp.product.certificate.line"].sudo()
        refs = set()
        for k in post.keys():
            m = re.match(r"^edit_cert_([0-9a-fA-F\-]{36})_(?:number|issue_date|expiry_date)$", k)
            if m:
                refs.add(m.group(1))
        for ref in refs:
            line = Line.resolve_ref(ref)
            if not line or line.product_id.id != product.id:
                continue
            if line.source_user_certificate_line_id:
                continue  # heredado del usuario: no editable
            vals = {
                "number": post.get(f"edit_cert_{ref}_number") or False,
                "issue_date": post.get(f"edit_cert_{ref}_issue_date") or False,
                "expiry_date": post.get(f"edit_cert_{ref}_expiry_date") or False,
            }
            file_obj = request.httprequest.files.get(f"edit_cert_{ref}_file")
            if file_obj and file_obj.filename:
                att = self._create_attachment(
                    file_obj, product, name_prefix=f"cert_{line.certificate_id.id}_")
                if att:
                    vals["attachment_id"] = att.id
                    product.write({"cert_attachment_ids": [(4, att.id)]})
            cambia = "attachment_id" in vals or any(
                str(line[k] or "") != str(vals[k] or "")
                for k in ("number", "issue_date", "expiry_date"))
            if cambia:
                vals["status"] = "pending"
            line.write(vals)

    def _save_product_certificates(self, product, post):
        """Crea las líneas de certificado enviadas en el formulario de crear/editar.

        Cada fila llega como prod_cert_{idx}_* . Puede ser:
          - Certificado del usuario: incluye prod_cert_{idx}_source_user_cert_line_id.
          - Certificado exclusivo del producto: id en prod_cert_{idx}_id + archivo en _file.
        """
        partner = self._get_current_partner()
        Line = request.env["shrimp.product.certificate.line"].sudo()

        indices = set()
        for k in post.keys():
            if k.startswith("prod_cert_") and k.endswith("_id"):
                try:
                    indices.add(int(k.split("_")[2]))
                except (ValueError, IndexError):
                    pass

        att_ids_for_m2m = []
        created_atts = request.env["ir.attachment"].sudo().browse([])

        for idx in sorted(indices):
            source_user_cert_line_id = post.get(f"prod_cert_{idx}_source_user_cert_line_id")
            certificate_id = post.get(f"prod_cert_{idx}_certificate_id") or post.get(f"prod_cert_{idx}_id")
            if not certificate_id:
                continue

            if source_user_cert_line_id:
                # Certificado del usuario: por su código, nunca por id.
                user_cert_line = request.env["shrimp.user.certificate.line"].sudo().resolve_ref(
                    source_user_cert_line_id)
                if not user_cert_line:
                    continue
                today = fields.Date.context_today(request.env.user)
                if user_cert_line.partner_id.id != partner.id:
                    continue
                if user_cert_line.status != "approved":
                    continue
                if user_cert_line.expiry_date and user_cert_line.expiry_date < today:
                    continue
                if not user_cert_line.file_attachment_id:
                    continue
                # Evita duplicar: si este certificado de usuario ya está asociado
                # al producto, no se vuelve a crear.
                if Line.search_count([
                    ("product_id", "=", product.id),
                    ("source_user_certificate_line_id", "=", user_cert_line.id),
                ]):
                    continue
                try:
                    with request.env.cr.savepoint():
                        Line.create({
                            "product_id": product.id,
                            "source_user_certificate_line_id": user_cert_line.id,
                            "certificate_id": user_cert_line.certificate_id.id,
                            "number": user_cert_line.certificate_number or False,
                            "issue_date": user_cert_line.issue_date or False,
                            "expiry_date": user_cert_line.expiry_date or False,
                            "attachment_id": user_cert_line.file_attachment_id.id,
                        })
                    att_ids_for_m2m.append(user_cert_line.file_attachment_id.id)
                except Exception:
                    # p. ej. duplicado (mismo certificado ya asociado): lo omitimos.
                    continue
                continue

            # Certificado exclusivo del producto: requiere archivo nuevo.
            file_obj = request.httprequest.files.get(f"prod_cert_{idx}_file")
            if not file_obj:
                continue

            catalogo = resolve_catalog_certificate(
                certificate_id, [product._shrimp_seller_role(), "all"])
            if not catalogo:
                continue
            att = self._create_attachment(file_obj, product, name_prefix=f"cert_{catalogo.id}_")
            if not att:
                continue
            try:
                with request.env.cr.savepoint():
                    Line.create({
                        "product_id": product.id,
                        "certificate_id": catalogo.id,
                        "number": post.get(f"prod_cert_{idx}_number") or False,
                        "issue_date": post.get(f"prod_cert_{idx}_issue_date") or False,
                        "expiry_date": post.get(f"prod_cert_{idx}_expiry_date") or False,
                        "attachment_id": att.id,
                    })
                created_atts |= att
                att_ids_for_m2m.append(att.id)
            except Exception:  # noqa: BLE001 - p. ej. número duplicado
                att.unlink()
                continue

        if att_ids_for_m2m:
            existing = product.cert_attachment_ids.ids
            product.write({"cert_attachment_ids": [(6, 0, list(set(existing) | set(att_ids_for_m2m)))]})
        if created_atts:
            created_atts.generate_access_token()

    def _notify_product_event(self, product, template_xmlid):
        """Envía el correo asociado (creado/publicado) al vendedor, sin romper
        el flujo. Usa el envío común de la plataforma (shrimp.notify.mixin):
        sin SMTP no se intenta y nunca lanza."""
        product.sudo()._send_template(template_xmlid, product.seller_partner_id.email)

    @http.route("/marketplace/products/<product_ref>/edit", type="http", auth="user", website=True)
    def marketplace_product_edit(self, product_ref, **kwargs):
        product = self._get_owned_product(product_ref)

        atts = product.photo_attachment_ids.sudo()
        if atts:
            atts.generate_access_token()

        partner = self._get_current_partner()
        is_internal = self._is_internal() and not partner._shrimp_can("sell_products")
        valid_cert_lines = (
            request.env["shrimp.user.certificate.line"]
            if is_internal else self._get_valid_user_certificates(partner)
        )
        # Se muestran todos los certificados válidos del vendedor; la duplicación
        # se evita en el guardado (_save_product_certificates deduplica por
        # source_user_certificate_line_id).

        # Reutiliza la MISMA plantilla que crear, en modo edición.
        locked = product.has_purchases()
        return request.render("shrimp_marketplace.product_new_form", {
            "back_url": f"/marketplace/product/{product.uuid_ref}",
            "default_user_certificates": valid_cert_lines,
            "is_internal": is_internal,
            "seller_options": self._seller_partner_options() if is_internal else None,
            "seller_partner": product.seller_partner_id,
            "product": product,
            "is_edit": True,
            "uom_locked": locked,
            "purchase_locked": locked,
            "form_action": f"/marketplace/products/{product.uuid_ref}/update",
            **self._get_product_form_options(
                owner=product.seller_partner_id, role=product._shrimp_seller_role()),
        })

    @http.route("/marketplace/products/<product_ref>/update", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_product_update(self, product_ref, **post):
        product = self._get_owned_product(product_ref)
        edit_url = f"/marketplace/products/{product.uuid_ref}/edit"
        locked = product.has_purchases()

        stage = self._stage_from_post(post) or product.stage_id
        if not locked:
            # La etapa la tiene que poder publicar el perfil del lote. Un lote
            # antiguo que ya tenía otra etapa la conserva mientras no se cambie.
            presentation, size_grade_id = self._post_presentation(post, stage)
            problema = self._stage_size_problem(
                stage, product._shrimp_seller_role(), presentation, size_grade_id,
                check_role=stage != product.stage_id)
            if problema:
                return self._form_error(edit_url, "validation", problema)
        else:
            presentation, size_grade_id = product.presentation, product.size_grade_id.id

        name = (post.get("name") or "").strip()
        if "name" in post and not name:
            return self._form_error(edit_url, "name")
        price = to_number(post.get("price"))
        if price < 0:
            return self._form_error(edit_url, "price")

        vals = {
            "name": name or product.name,
            "species_id": to_int(post.get("species_id"), 0) or False,
            "stage_id": stage.id,
            "genetics_line_id": to_int(post.get("genetics_line_id"), 0) or False,
            "avg_size_mg": to_number(post.get("avg_size_mg")),
            "survival_rate": to_number(post.get("survival_rate")),
            "location": post.get("location") or False,
            "presentation": presentation,
            "size_grade_id": size_grade_id,
            **self._origin_vals(post, product.seller_partner_id),
            "uom_id": to_int(post.get("uom_id"), 0) or product.uom_id.id,
            "price": price,
            "batch_code": (post.get("batch_code") or "").strip() or False,
            "health_status": post.get("health_status") or False,
            "expected_delivery_date": post.get("expected_delivery_date") or False,
            "available_from": post.get("available_from") or False,
            "available_to": post.get("available_to") or False,
        }

        # Si el producto ya tiene compras, los campos críticos quedan bloqueados:
        # se descartan del vals para conservar su valor actual (los inputs del
        # formulario también van deshabilitados).
        if locked:
            for campo in product._LOCKED_AFTER_PURCHASE:
                vals.pop(campo, None)

        try:
            with request.env.cr.savepoint():
                # Lista de precios (solo si el módulo empacadora aporta el campo).
                vals.update(self._price_list_val(post, product.seller_partner_id))
                product.write(vals)

                # Fotos: nuevas, quitadas (remove_photo_<token>), orden y portada.
                self._save_photos(product, post)

                # Eliminar los certificados del producto marcados con "Eliminar".
                self._remove_product_certificates(product, post)

                # Editar certificados ya guardados del producto (solo los propios,
                # no los heredados del usuario).
                self._edit_product_certificates(product, post)

                # Guardar los certificados nuevos añadidos en la edición.
                self._save_product_certificates(product, post)
        except ValidationError as e:
            return self._form_error(edit_url, "validation", e.args[0] if e.args else "")

        if post.get("publish"):
            return self._publish_from_form(product)

        return request.redirect(f"/marketplace/product/{product.uuid_ref}")

    def _remove_product_certificates(self, product, post):
        """Elimina las líneas de certificado del producto cuyo checkbox
        remove_cert_<id> venga marcado. Solo borra líneas del propio producto."""
        Line = request.env["shrimp.product.certificate.line"].sudo()
        to_remove = Line.browse()
        for key, val in post.items():
            if key.startswith("remove_cert_") and val:
                line = Line.resolve_ref(key[len("remove_cert_"):])
                if line and line.product_id.id == product.id:
                    to_remove |= line
        if to_remove:
            # Quitar también los adjuntos del m2m cert_attachment_ids del producto.
            att_ids = to_remove.mapped("attachment_id").ids
            if att_ids:
                product.sudo().write({"cert_attachment_ids": [(3, aid) for aid in att_ids]})
            to_remove.unlink()

    @http.route("/marketplace/products/<product_ref>/publish", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_product_publish(self, product_ref, **post):
        product = self._get_owned_product(product_ref)

        if not product.active:
            raise NotFound()

        if product.state != "draft":
            return request.redirect(f"/marketplace/product/{product.uuid_ref}")

        if product.available_qty <= 0:
            return request.redirect(f"/marketplace/product/{product.uuid_ref}?error=no_stock")

        # Presentación y talla solo se exigen en camarón de talla comercial.
        # Un nauplio o una post-larva no son "entero/cola" ni tienen talla de
        # la matriz comercial (30/40, 41/50…). La regla vive en el modelo
        # (action_publish); aquí solo se traduce a un aviso amable.
        if self._exige_talla_comercial(product) and (
                not product.presentation or not product.size_grade_id):
            return request.redirect(f"/marketplace/product/{product.uuid_ref}?error=size_required")

        try:
            with request.env.cr.savepoint():
                product.action_publish()
        except ValidationError as e:
            return request.redirect(f"/marketplace/product/{product.uuid_ref}?error=validation&message="
                                    + flash_message(e.args[0] if e.args else ""))
        # Correo: producto publicado.
        self._notify_product_event(product, "shrimp_marketplace.mail_template_shrimp_product_published")
        return request.redirect(f"/marketplace/product/{product.uuid_ref}?published=1")

    @http.route("/marketplace/products/<product_ref>/deactivate", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_product_deactivate(self, product_ref, **post):
        """Da de baja (archiva) un producto: sale del marketplace pero se conserva
        y puede reactivarse."""
        product = self._get_owned_product(product_ref)
        product.write({"active": False, "state": "draft"})
        return request.redirect("/marketplace/products?baja=1")

    @http.route("/marketplace/products/<product_ref>/delete", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_product_delete(self, product_ref, **post):
        """Borrado lógico: el producto NO se elimina de la base de datos (se
        conserva para histórico y trazabilidad), pero el usuario deja de verlo
        y no puede reactivarlo. Se marca active=False + state='cancel'."""
        product = request.env["shrimp.product"].sudo().with_context(
            active_test=False).resolve_ref(product_ref)
        if not product:
            raise NotFound()
        partner = self._get_current_partner()
        if product.seller_partner_id.id != partner.id and not self._is_internal():
            raise Forbidden()
        product.write({"active": False, "state": "cancel"})
        return request.redirect("/marketplace/products?eliminado=1")

    @http.route("/marketplace/products/<product_ref>/reactivate", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_product_reactivate(self, product_ref, **post):
        """Reactiva un producto dado de baja (queda en borrador para republicar)."""
        product = request.env["shrimp.product"].sudo().with_context(
            active_test=False).resolve_ref(product_ref)
        if not product:
            raise NotFound()
        partner = self._get_current_partner()
        if product.seller_partner_id.id != partner.id and not self._is_internal():
            raise Forbidden()
        product.write({"active": True})
        return request.redirect("/marketplace/products?reactivado=1")

    @http.route("/marketplace/my-certificate/<line_ref>/file", type="http", auth="user", website=True, sitemap=False)
    def download_my_certificate(self, line_ref, **kwargs):
        partner = self._get_current_partner()
        line = request.env["shrimp.user.certificate.line"].sudo().resolve_ref(line_ref)

        if not line or line.partner_id.id != partner.id:
            raise NotFound()

        att = line.file_attachment_id
        if not att or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        # En línea por defecto, y como adjunto solo si se pide con ?download=1
        # (el visor de PDF es un iframe apuntando a esta ruta).
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype, att.name or "certificado",
            download=bool(kwargs.get("download"))))

    @http.route("/marketplace/product-certificates", type="jsonrpc", auth="user", website=True)
    def marketplace_product_certs(self, **kw):
        certs = request.env["shrimp.certificate"].sudo().search([
            ("active", "=", True)
        ], order="name asc")
        # Catálogo: se identifica por su código (uuid_ref), no por id.
        return [{"ref": c.uuid_ref, "name": c.name, "issuer": c.issuer or ""} for c in certs]