import base64

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from werkzeug.exceptions import NotFound, Forbidden


class ShrimpProductPortalController(http.Controller):

    def _get_current_partner(self):
        return request.env.user.partner_id

    def _is_internal(self):
        # Usuario interno (empleado/administrador), no portal/público.
        return request.env.user.has_group("base.group_user")

    def _check_can_manage_products(self):
        # Semillero/Laboratorio publican lo suyo; los usuarios internos (admin)
        # pueden publicar en nombre de cualquier vendedor.
        if self._is_internal():
            return True
        partner = self._get_current_partner()
        return partner.shrimp_user_type in ("semillero", "laboratorio")

    def _seller_partner_options(self):
        return request.env["res.partner"].sudo().search(
            [("shrimp_user_type", "in", ["semillero", "laboratorio"])], order="name asc")

    def _resolve_seller(self, post):
        """Determina el vendedor del producto.
        - Vendedor real (semillero/lab): él mismo.
        - Interno/admin: el vendedor elegido en el formulario (debe ser semillero/lab).
        Devuelve el partner vendedor o False si no es válido.
        """
        partner = self._get_current_partner()
        if partner.shrimp_user_type in ("semillero", "laboratorio"):
            return partner
        if self._is_internal():
            try:
                seller_id = int(post.get("seller_partner_id") or 0)
            except (TypeError, ValueError):
                return False
            seller = request.env["res.partner"].sudo().browse(seller_id)
            if seller.exists() and seller.shrimp_user_type in ("semillero", "laboratorio"):
                return seller
        return False

    def _get_product_form_options(self):
        return {
            "species_options": request.env["shrimp.species"].sudo().search(
                [("active", "=", True)], order="name asc"),
            "stage_options": request.env["shrimp.stage"].sudo().search(
                [("active", "=", True)], order="sequence asc, name asc"),
            "genetics_options": request.env["shrimp.genetics.line"].sudo().search(
                [("active", "=", True)], order="name asc"),
            "uom_options": request.env["shrimp.uom"].sudo().search(
                [("active", "=", True)], order="sequence, name"),
            "size_grade_options": request.env["shrimp.size.grade"].sudo().search(
                [("active", "=", True)], order="presentation, sequence, name"),
            "facility_options": request.env["shrimp.partner.facility"].sudo().search(
                [("partner_id", "=", self._get_current_partner().id)], order="name"),
            "pond_options": request.env["shrimp.partner.pond"].sudo().search(
                [("partner_id", "=", self._get_current_partner().id)], order="name"),
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

    def _create_attachment(self, file_obj, product, name_prefix="", public=False):
        if not file_obj:
            return False

        try:
            file_obj.stream.seek(0)
        except Exception:
            try:
                file_obj.seek(0)
            except Exception:
                pass

        content = file_obj.read()
        if not content:
            return False

        filename = getattr(file_obj, "filename", None) or "archivo"
        content_type = getattr(file_obj, "content_type", None) or "application/octet-stream"

        return request.env["ir.attachment"].sudo().create({
            "name": f"{name_prefix}{filename}" if name_prefix else filename,
            "type": "binary",
            "datas": base64.b64encode(content),
            "mimetype": content_type,
            "res_model": "shrimp.product",
            "res_id": product.id,
            "public": public,
        })

    @http.route("/marketplace/products", type="http", auth="user", website=True)
    def list_products(self, **kw):
        partner = self._get_current_partner()
        Product = request.env["shrimp.product"].sudo()

        q = (kw.get("q") or "").strip()
        stage = (kw.get("stage") or "").strip()
        location = (kw.get("location") or "").strip()
        seller = (kw.get("seller") or "").strip()
        price_min_raw = (kw.get("price_min") or "").strip()
        price_max_raw = (kw.get("price_max") or "").strip()
        order = (kw.get("order") or "").strip()

        domain = [
            ("active", "=", True),
            ("seller_partner_id", "=", partner.id),
        ]

        if q:
            domain += ["|", "|",
                ("name", "ilike", q),
                ("species_id.name", "ilike", q),
                ("location", "ilike", q),
            ]

        Stage = request.env["shrimp.stage"].sudo()
        stage_options = Stage.search([])

        if stage:
            try:
                domain.append(("stage_id", "=", int(stage)))
            except (TypeError, ValueError):
                pass

        if location:
            domain.append(("location", "ilike", location))

        if seller:
            domain.append(("seller_partner_id.name", "ilike", seller))

        if price_min_raw:
            try:
                domain.append(("price", ">=", float(price_min_raw)))
            except Exception:
                pass

        if price_max_raw:
            try:
                domain.append(("price", "<=", float(price_max_raw)))
            except Exception:
                pass

        order_map = {
            "price_asc": "price asc, create_date desc",
            "price_desc": "price desc, create_date desc",
            "qty_desc": "available_qty desc, create_date desc",
        }
        search_order = order_map.get(order, "create_date desc")

        products = Product.search(domain, order=search_order)

        cover_atts = request.env["ir.attachment"].sudo().browse([])
        for product in products:
            first = product.photo_attachment_ids[:1]
            if first:
                cover_atts |= first

        if cover_atts:
            cover_atts.generate_access_token()

        return request.render("shrimp_marketplace.products_list", {
            "products": products,
            "products_count": len(products),
            "stage_options": stage_options,
            "filters": {
                "q": q,
                "stage": stage,
                "location": location,
                "seller": seller,
                "price_min": price_min_raw,
                "price_max": price_max_raw,
                "order": order,
            },
        })

    @http.route("/marketplace/products/new", type="http", auth="user", website=True)
    def new_product_form(self, **kw):
        if not self._check_can_manage_products():
            raise Forbidden()

        partner = request.env.user.partner_id
        is_internal = self._is_internal() and partner.shrimp_user_type not in ("semillero", "laboratorio")

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

        return request.render("shrimp_marketplace.product_new_form", {
            "back_url": back_url,
            "default_user_certificates": valid_cert_lines,
            "is_internal": is_internal,
            "seller_options": self._seller_partner_options() if is_internal else None,
            "product": request.env["shrimp.product"],
            "is_edit": False,
            "uom_locked": False,
            "form_action": "/marketplace/products/create",
            **self._get_product_form_options(),
        })

    @http.route("/marketplace/products/create", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def create_product(self, **post):
        partner = self._get_current_partner()
        if not self._check_can_manage_products():
            raise Forbidden()

        def _to_float(value, default=0.0):
            try:
                return float(value or default)
            except Exception:
                return default

        # Resolver vendedor: él mismo (semillero/lab) o el elegido por el admin.
        seller = self._resolve_seller(post)
        if not seller:
            return request.redirect("/marketplace/products/new?error=seller")
        seller_role = seller.shrimp_user_type

        name = (post.get("name") or "").strip()
        species_id = int(post.get("species_id")) if post.get("species_id") else False
        stage_id = int(post.get("stage_id")) if post.get("stage_id") else False
        genetics_line_id = int(post.get("genetics_line_id")) if post.get("genetics_line_id") else False
        health_status = (post.get("health_status") or "").strip()
        uom_id = int(post.get("uom_id")) if post.get("uom_id") else False
        if not uom_id:
            libra = request.env.ref("shrimp_marketplace.uom_libra", raise_if_not_found=False)
            uom_id = libra.id if libra else False
        location = (post.get("location") or "").strip()

        initial_qty = _to_float(post.get("initial_qty") or post.get("available_qty"))
        avg_size_mg = _to_float(post.get("avg_size_mg"))
        survival_rate = _to_float(post.get("survival_rate"))
        price = _to_float(post.get("price"))

        expected_delivery_date = post.get("expected_delivery_date") or False
        available_from = post.get("available_from") or False
        available_to = post.get("available_to") or False

        if not name:
            return request.redirect("/marketplace/products/new?error=name")

        if initial_qty <= 0:
            return request.redirect("/marketplace/products/new?error=qty")

        if price < 0:
            return request.redirect("/marketplace/products/new?error=price")

        vals = {
            "name": name,
            "seller_partner_id": seller.id,
            "species_id": species_id,
            "stage_id": stage_id,
            "genetics_line_id": genetics_line_id,
            "avg_size_mg": avg_size_mg,
            "survival_rate": survival_rate,
            "health_status": health_status or False,
            "initial_qty": initial_qty,
            "uom_id": uom_id,
            "price": price,
            "location": location or False,
            "presentation": post.get("presentation") or False,
            "size_grade_id": int(post.get("size_grade_id")) if post.get("size_grade_id") else False,
            "origin_facility_id": int(post.get("origin_facility_id")) if post.get("origin_facility_id") else False,
            "origin_pond_id": int(post.get("origin_pond_id")) if post.get("origin_pond_id") else False,
            "expected_delivery_date": expected_delivery_date,
            "available_from": available_from,
            "available_to": available_to,
            "seller_role": seller_role,
            "state": "draft",
            "active": True,
        }

        try:
            product = request.env["shrimp.product"].sudo().create(vals)
        except ValidationError as e:
            return request.redirect("/marketplace/products/new?error=validation&message=%s" % e.args[0])

        attachment_model = request.env["ir.attachment"].sudo()
        created_photo_atts = attachment_model.browse([])
        created_cert_atts = attachment_model.browse([])

        main_att = False
        main_photo = request.httprequest.files.get("main_photo_file")
        if main_photo:
            main_att = self._create_attachment(main_photo, product, name_prefix="main_", public=True)
            if main_att:
                created_photo_atts |= main_att

        photo_ids = []
        for file_obj in (request.httprequest.files.getlist("photo_files") or []):
            att = self._create_attachment(file_obj, product, public=True)
            if att:
                created_photo_atts |= att
                photo_ids.append(att.id)

        final_photo_ids = []
        if main_att:
            final_photo_ids.append(main_att.id)
        final_photo_ids += photo_ids

        if final_photo_ids:
            product.write({"photo_attachment_ids": [(6, 0, final_photo_ids)]})

        # Guardar certificados (propios del producto y los que vienen del usuario).
        self._save_product_certificates(product, post)

        if created_photo_atts:
            created_photo_atts.generate_access_token()

        # Correo: producto creado.
        self._notify_product_event(product, "shrimp_marketplace.mail_template_shrimp_product_created")

        return request.redirect("/marketplace/products")

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
                user_cert_line = request.env["shrimp.user.certificate.line"].sudo().browse(int(source_user_cert_line_id))
                if not user_cert_line.exists():
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

            att = self._create_attachment(file_obj, product, name_prefix=f"cert_{certificate_id}_")
            if not att:
                continue
            try:
                with request.env.cr.savepoint():
                    Line.create({
                        "product_id": product.id,
                        "certificate_id": int(certificate_id),
                        "number": post.get(f"prod_cert_{idx}_number") or False,
                        "issue_date": post.get(f"prod_cert_{idx}_issue_date") or False,
                        "expiry_date": post.get(f"prod_cert_{idx}_expiry_date") or False,
                        "attachment_id": att.id,
                    })
                created_atts |= att
                att_ids_for_m2m.append(att.id)
            except Exception:
                att.unlink()
                continue

        if att_ids_for_m2m:
            existing = product.cert_attachment_ids.ids
            product.write({"cert_attachment_ids": [(6, 0, list(set(existing) | set(att_ids_for_m2m)))]})
        if created_atts:
            created_atts.generate_access_token()

    def _notify_product_event(self, product, template_xmlid):
        """Envía el correo asociado (creado/publicado) al vendedor, sin romper el flujo."""
        try:
            template = request.env.ref(template_xmlid, raise_if_not_found=False)
            if not template:
                return
            email_to = product.seller_partner_id.email
            if not email_to:
                return
            template.sudo().send_mail(
                product.id, force_send=True,
                email_values={"email_to": email_to},
            )
        except Exception:
            # Nunca bloquear la operación por un fallo de correo.
            pass

    @http.route("/marketplace/products/<product_ref>/edit", type="http", auth="user", website=True)
    def marketplace_product_edit(self, product_ref, **kwargs):
        product = self._get_owned_product(product_ref)

        atts = product.photo_attachment_ids.sudo()
        if atts:
            atts.generate_access_token()

        partner = self._get_current_partner()
        is_internal = self._is_internal() and partner.shrimp_user_type not in ("semillero", "laboratorio")
        valid_cert_lines = (
            request.env["shrimp.user.certificate.line"]
            if is_internal else self._get_valid_user_certificates(partner)
        )
        # Se muestran todos los certificados válidos del vendedor; la duplicación
        # se evita en el guardado (_save_product_certificates deduplica por
        # source_user_certificate_line_id).

        # Reutiliza la MISMA plantilla que crear, en modo edición.
        return request.render("shrimp_marketplace.product_new_form", {
            "back_url": f"/marketplace/product/{product.uuid_ref}",
            "default_user_certificates": valid_cert_lines,
            "is_internal": is_internal,
            "seller_options": self._seller_partner_options() if is_internal else None,
            "product": product,
            "is_edit": True,
            "uom_locked": product.has_purchases(),
            "form_action": f"/marketplace/products/{product.uuid_ref}/update",
            **self._get_product_form_options(),
        })

    @http.route("/marketplace/products/<product_ref>/update", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def marketplace_product_update(self, product_ref, **post):
        product = self._get_owned_product(product_ref)

        def _to_float(value, default=0.0):
            try:
                return float(value or default)
            except Exception:
                return default

        vals = {
            "name": post.get("name") or product.name,
            "species_id": int(post.get("species_id")) if post.get("species_id") else False,
            "stage_id": int(post.get("stage_id")) if post.get("stage_id") else product.stage_id.id,
            "genetics_line_id": int(post.get("genetics_line_id")) if post.get("genetics_line_id") else False,
            "avg_size_mg": _to_float(post.get("avg_size_mg")),
            "survival_rate": _to_float(post.get("survival_rate")),
            "location": post.get("location") or False,
            "presentation": post.get("presentation") or False,
            "size_grade_id": int(post.get("size_grade_id")) if post.get("size_grade_id") else False,
            "origin_facility_id": int(post.get("origin_facility_id")) if post.get("origin_facility_id") else False,
            "origin_pond_id": int(post.get("origin_pond_id")) if post.get("origin_pond_id") else False,
            "uom_id": (int(post.get("uom_id")) if post.get("uom_id") else product.uom_id.id),
            "price": _to_float(post.get("price")),
            "health_status": post.get("health_status") or False,
            "expected_delivery_date": post.get("expected_delivery_date") or False,
            "available_from": post.get("available_from") or False,
            "available_to": post.get("available_to") or False,
        }

        try:
            product.write(vals)
        except ValidationError as e:
            return request.redirect(
                f"/marketplace/products/{product.uuid_ref}/edit?error=validation&message={e.args[0]}"
            )

        # Fotos nuevas añadidas en la edición: se suman a las existentes.
        new_photo_ids = []
        for file_obj in (request.httprequest.files.getlist("photo_files") or []):
            att = self._create_attachment(file_obj, product, public=True)
            if att:
                new_photo_ids.append(att.id)
        if new_photo_ids:
            att_recs = request.env["ir.attachment"].sudo().browse(new_photo_ids)
            att_recs.generate_access_token()
            product.write({"photo_attachment_ids": [(4, aid) for aid in new_photo_ids]})

        # Eliminar los certificados del producto marcados con "Eliminar".
        self._remove_product_certificates(product, post)

        # Guardar los certificados nuevos añadidos en la edición (mismo naming que en crear).
        self._save_product_certificates(product, post)

        return request.redirect(f"/marketplace/product/{product.uuid_ref}")

    def _remove_product_certificates(self, product, post):
        """Elimina las líneas de certificado del producto cuyo checkbox
        remove_cert_<id> venga marcado. Solo borra líneas del propio producto."""
        Line = request.env["shrimp.product.certificate.line"].sudo()
        to_remove = Line.browse()
        for key, val in post.items():
            if key.startswith("remove_cert_") and val:
                try:
                    cid = int(key[len("remove_cert_"):])
                except ValueError:
                    continue
                line = Line.browse(cid)
                if line.exists() and line.product_id.id == product.id:
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

        # Presentación y talla son obligatorias para publicar.
        if not product.presentation or not product.size_grade_id:
            return request.redirect(f"/marketplace/product/{product.uuid_ref}?error=size_required")

        product.write({"state": "published", "published_date": fields.Datetime.now()})
        # Correo: producto publicado.
        self._notify_product_event(product, "shrimp_marketplace.mail_template_shrimp_product_published")
        return request.redirect(f"/marketplace/product/{product.uuid_ref}?published=1")

    @http.route("/marketplace/my-certificate/<int:line_id>/file", type="http", auth="user", website=True, sitemap=False)
    def download_my_certificate(self, line_id, **kwargs):
        partner = self._get_current_partner()
        line = request.env["shrimp.user.certificate.line"].sudo().browse(line_id)

        if not line.exists() or line.partner_id.id != partner.id:
            raise NotFound()

        att = line.file_attachment_id
        if not att or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        filename = (att.name or "certificado").replace("/", "-").replace("\\", "-")

        return request.make_response(content, headers=[
            ("Content-Type", att.mimetype or "application/octet-stream"),
            ("Content-Length", str(len(content))),
            ("Content-Disposition", f'attachment; filename="{filename}"'),
            ("Cache-Control", "private, max-age=0"),
        ])

    @http.route("/marketplace/certificados_producto", type="json", auth="user", website=True)
    def marketplace_product_certs(self, **kw):
        certs = request.env["shrimp.certificate"].sudo().search([
            ("active", "=", True)
        ], order="name asc")
        return [{"id": c.id, "name": c.name, "issuer": c.issuer or ""} for c in certs]