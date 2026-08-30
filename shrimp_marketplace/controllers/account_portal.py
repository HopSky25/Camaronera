import base64
import re

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from werkzeug.exceptions import NotFound, Forbidden

# Reutiliza la validación real de archivos (magic bytes) del módulo de registro
from odoo.addons.shrimp_user_registry.controllers.main import _read_validated_file

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ShrimpAccountPortalController(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _partner(self):
        return request.env.user.partner_id

    def _create_private_attachment(self, file_obj, partner, name_prefix=""):
        """Crea un ir.attachment privado validando el tipo real del contenido."""
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

        real_mime = _read_validated_file(content)
        filename = getattr(file_obj, "filename", None) or "archivo"

        return request.env["ir.attachment"].sudo().create({
            "name": f"{name_prefix}{filename}" if name_prefix else filename,
            "type": "binary",
            "datas": base64.b64encode(content),
            "mimetype": real_mime,
            "res_model": "res.partner",
            "res_id": partner.id,
            "public": False,
        })

    # ==================================================================
    # 1) MI PERFIL
    # ==================================================================
    @http.route("/marketplace/mi-cuenta", type="http", auth="user", website=True)
    def my_account(self, **kw):
        partner = self._partner()
        return request.render("shrimp_marketplace.my_account", {
            "partner": partner,
            "saved": kw.get("saved"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/mi-cuenta/guardar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def my_account_save(self, **post):
        partner = self._partner()

        email = (post.get("email") or "").strip().lower()
        if email and not EMAIL_RE.match(email):
            return request.redirect("/marketplace/mi-cuenta?error=email")

        vals = {
            "phone": post.get("phone") or False,
            "city": post.get("city") or False,
        }
        if email:
            vals["email"] = email

        user_type = partner.shrimp_user_type
        if user_type == "laboratorio":
            vals.update({
                "lab_razon_social": post.get("lab_razon_social") or False,
                "lab_ubicacion": post.get("lab_ubicacion") or False,
                "lab_global_gap": bool(post.get("lab_global_gap")),
                "lab_social_ship_partner": bool(post.get("lab_social_ship_partner")),
            })
        elif user_type == "camaronera":
            def _f(v):
                try:
                    return float(v or 0.0)
                except (TypeError, ValueError):
                    return 0.0
            vals.update({
                "farm_razon_social": post.get("farm_razon_social") or False,
                "farm_representante": post.get("farm_representante") or False,
                "farm_telefono": post.get("farm_telefono") or False,
                "farm_ubicacion": post.get("farm_ubicacion") or False,
                "farm_capacidad": _f(post.get("farm_capacidad")),
                "farm_area_ha": _f(post.get("farm_area_ha")),
            })

        try:
            partner.sudo().write(vals)
        except Exception:
            return request.redirect("/marketplace/mi-cuenta?error=duplicate")

        return request.redirect("/marketplace/mi-cuenta?saved=1")

    # ==================================================================
    # 2) MIS CERTIFICADOS
    # ==================================================================
    def _available_certificates(self, partner):
        return request.env["shrimp.certificate"].sudo().search([
            ("active", "=", True),
            ("role", "in", [partner.shrimp_user_type, "all"]),
        ], order="name asc")

    def _validated_certificate(self, partner, cert_id):
        try:
            cert_id_int = int(cert_id)
        except (TypeError, ValueError):
            return False
        return request.env["shrimp.certificate"].sudo().search([
            ("id", "=", cert_id_int),
            ("active", "=", True),
            ("role", "in", [partner.shrimp_user_type, "all"]),
        ], limit=1)

    @http.route("/marketplace/mis-certificados", type="http", auth="user", website=True)
    def my_certificates(self, **kw):
        partner = self._partner()
        lines = request.env["shrimp.user.certificate.line"].sudo().search([
            ("partner_id", "=", partner.id),
        ], order="id desc")
        today = fields.Date.context_today(request.env.user)

        return request.render("shrimp_marketplace.my_certificates", {
            "partner": partner,
            "lines": lines,
            "today": today,
            "certificates": self._available_certificates(partner),
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/mis-certificados/agregar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def my_certificate_add(self, **post):
        partner = self._partner()

        cert = self._validated_certificate(partner, post.get("certificate_id"))
        if not cert:
            return request.redirect("/marketplace/mis-certificados?error=cert")

        file_obj = request.httprequest.files.get("file")
        if not file_obj:
            return request.redirect("/marketplace/mis-certificados?error=file")

        try:
            att = self._create_private_attachment(
                file_obj, partner, name_prefix=f"cert_{cert.id}_")
            if not att:
                return request.redirect("/marketplace/mis-certificados?error=file")

            request.env["shrimp.user.certificate.line"].sudo().create({
                "partner_id": partner.id,
                "certificate_id": cert.id,
                "certificate_number": (post.get("certificate_number") or "").strip() or False,
                "issue_date": post.get("issue_date") or False,
                "expiry_date": post.get("expiry_date") or False,
                "file_attachment_id": att.id,
                "status": "pending",
            })
        except ValidationError as e:
            return request.redirect(
                "/marketplace/mis-certificados?error=validation&message=%s" % (e.args[0] if e.args else ""))

        return request.redirect("/marketplace/mis-certificados?saved=1")

    def _owned_cert_line(self, line_id):
        line = request.env["shrimp.user.certificate.line"].sudo().browse(int(line_id))
        if not line.exists() or line.partner_id.id != self._partner().id:
            raise NotFound()
        return line

    @http.route("/marketplace/mis-certificados/<int:line_id>/renovar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def my_certificate_renew(self, line_id, **post):
        partner = self._partner()
        line = self._owned_cert_line(line_id)

        vals = {
            "certificate_number": (post.get("certificate_number") or "").strip() or False,
            "issue_date": post.get("issue_date") or False,
            "expiry_date": post.get("expiry_date") or False,
            # Al renovar vuelve a revisión
            "status": "pending",
        }

        file_obj = request.httprequest.files.get("file")
        try:
            if file_obj and getattr(file_obj, "filename", ""):
                att = self._create_private_attachment(
                    file_obj, partner, name_prefix=f"cert_{line.certificate_id.id}_")
                if att:
                    vals["file_attachment_id"] = att.id
            line.write(vals)
        except ValidationError as e:
            return request.redirect(
                "/marketplace/mis-certificados?error=validation&message=%s" % (e.args[0] if e.args else ""))

        return request.redirect("/marketplace/mis-certificados?saved=renew")

    @http.route("/marketplace/mis-certificados/<int:line_id>/eliminar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def my_certificate_delete(self, line_id, **post):
        line = self._owned_cert_line(line_id)
        line.unlink()
        return request.redirect("/marketplace/mis-certificados?saved=delete")

    # ==================================================================
    # 3) MIS LOTES / INVENTARIO
    # ==================================================================
    @http.route("/marketplace/mis-lotes", type="http", auth="user", website=True)
    def my_lots(self, **kw):
        partner = self._partner()
        lots = request.env["shrimp.stock.lot"].sudo().search([
            ("owner_id", "=", partner.id),
        ], order="id desc")
        return request.render("shrimp_marketplace.my_lots", {
            "partner": partner,
            "lots": lots,
        })

    # ==================================================================
    # 4) INSTALACIONES Y PISCINAS + ASIGNACIÓN
    # ==================================================================
    @http.route("/marketplace/mis-instalaciones", type="http", auth="user", website=True)
    def my_facilities(self, **kw):
        partner = self._partner()
        Facility = request.env["shrimp.partner.facility"].sudo()
        Pond = request.env["shrimp.partner.pond"].sudo()
        Lot = request.env["shrimp.stock.lot"].sudo()
        Alloc = request.env["shrimp.lot.allocation"].sudo()

        facilities = Facility.search([("partner_id", "=", partner.id)])
        ponds = Pond.search([("partner_id", "=", partner.id)])
        lots = Lot.search([("owner_id", "=", partner.id), ("available_qty", ">", 0)])
        allocations = Alloc.search([("partner_id", "=", partner.id)])

        # Instalación seleccionada (master-detail): por parámetro o la primera.
        try:
            sel_id = int(kw.get("facility")) if kw.get("facility") else False
        except (TypeError, ValueError):
            sel_id = False
        sel_facility = Facility.browse(sel_id) if sel_id else Facility
        if not (sel_facility and sel_facility.exists() and sel_facility.partner_id.id == partner.id):
            sel_facility = facilities[:1]  # primera o vacío
        sel_facility_id = sel_facility.id if sel_facility else False
        sel_ponds = ponds.filtered(lambda p: p.facility_id.id == sel_facility_id) if sel_facility_id else ponds.filtered(lambda p: not p.facility_id)

        # Piscina en edición (si aplica)
        try:
            edit_pond_id = int(kw.get("edit_pond")) if kw.get("edit_pond") else False
        except (TypeError, ValueError):
            edit_pond_id = False
        edit_pond = Pond.browse(edit_pond_id) if edit_pond_id else Pond
        if edit_pond and (not edit_pond.exists() or edit_pond.partner_id.id != partner.id):
            edit_pond = Pond

        return request.render("shrimp_marketplace.my_facilities", {
            "partner": partner,
            "facilities": facilities,
            "ponds": ponds,
            "sel_facility": sel_facility,
            "sel_ponds": sel_ponds,
            "is_new": bool(kw.get("new")),
            "is_edit_fac": bool(kw.get("edit")),
            "edit_pond": edit_pond,
            "lots": lots,
            "allocations": allocations,
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/mis-instalaciones/facility/crear", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def facility_create(self, **post):
        partner = self._partner()
        name = (post.get("name") or "").strip()
        if not name:
            return request.redirect("/marketplace/mis-instalaciones?error=name&new=1")
        fac = request.env["shrimp.partner.facility"].sudo().create({
            "partner_id": partner.id,
            "name": name,
            "code": (post.get("code") or "").strip() or False,
            "facility_type": post.get("facility_type") or "farm",
            "city": (post.get("city") or "").strip() or False,
            "province": (post.get("province") or "").strip() or False,
            "address": (post.get("address") or "").strip() or False,
        })
        # Seleccionar la instalación recién creada.
        return request.redirect("/marketplace/mis-instalaciones?saved=facility&facility=%s" % fac.id)

    @http.route("/marketplace/mis-instalaciones/facility/<int:facility_id>/actualizar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def facility_update(self, facility_id, **post):
        partner = self._partner()
        fac = request.env["shrimp.partner.facility"].sudo().browse(facility_id)
        if not fac.exists() or fac.partner_id.id != partner.id:
            raise NotFound()
        name = (post.get("name") or "").strip()
        if not name:
            return request.redirect("/marketplace/mis-instalaciones?facility=%s&edit=1&error=name" % facility_id)
        fac.write({
            "name": name,
            "code": (post.get("code") or "").strip() or False,
            "facility_type": post.get("facility_type") or fac.facility_type,
            "city": (post.get("city") or "").strip() or False,
            "address": (post.get("address") or "").strip() or False,
        })
        return request.redirect("/marketplace/mis-instalaciones?facility=%s&saved=facility" % facility_id)

    @http.route("/marketplace/mis-instalaciones/facility/<int:facility_id>/eliminar",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def facility_delete(self, facility_id, **post):
        partner = self._partner()
        facility = request.env["shrimp.partner.facility"].sudo().browse(facility_id)
        if not facility.exists() or facility.partner_id.id != partner.id:
            raise NotFound()
        facility.unlink()
        return request.redirect("/marketplace/mis-instalaciones?saved=facility_del")

    @http.route("/marketplace/mis-instalaciones/pond/crear", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def pond_create(self, **post):
        partner = self._partner()
        name = (post.get("name") or "").strip()
        if not name:
            return request.redirect("/marketplace/mis-instalaciones?error=name")

        def _f(v):
            try:
                return float(v or 0.0)
            except (TypeError, ValueError):
                return 0.0

        facility_id = False
        if post.get("facility_id"):
            fac = request.env["shrimp.partner.facility"].sudo().browse(int(post.get("facility_id")))
            if fac.exists() and fac.partner_id.id == partner.id:
                facility_id = fac.id

        # Imagen opcional de la piscina.
        image_b64 = False
        img = request.httprequest.files.get("image")
        if img:
            content = img.read()
            if content:
                image_b64 = base64.b64encode(content)

        try:
            request.env["shrimp.partner.pond"].sudo().create({
                "partner_id": partner.id,
                "facility_id": facility_id,
                "name": name,
                "code": (post.get("code") or "").strip() or False,
                "pond_type": post.get("pond_type") or "earth",
                "capacity_mode": post.get("capacity_mode") or "dimensions",
                "length_m": _f(post.get("length_m")),
                "width_m": _f(post.get("width_m")),
                "depth_m": _f(post.get("depth_m")),
                "manual_volume_m3": _f(post.get("manual_volume_m3")),
                "location": (post.get("location") or "").strip() or False,
                "image": image_b64,
            })
        except ValidationError as e:
            return request.redirect(
                "/marketplace/mis-instalaciones?error=validation&message=%s" % (e.args[0] if e.args else ""))
        # Volver a la instalación seleccionada tras crear la piscina.
        rf = post.get("redirect_facility") or facility_id
        suffix = ("&facility=%s" % rf) if rf else ""
        return request.redirect("/marketplace/mis-instalaciones?saved=pond" + suffix)

    @http.route("/marketplace/mis-instalaciones/pond/<int:pond_id>/actualizar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def pond_update(self, pond_id, **post):
        partner = self._partner()
        pond = request.env["shrimp.partner.pond"].sudo().browse(pond_id)
        if not pond.exists() or pond.partner_id.id != partner.id:
            raise NotFound()

        def _f(v):
            try:
                return float(v or 0.0)
            except (TypeError, ValueError):
                return 0.0

        name = (post.get("name") or "").strip()
        rf = pond.facility_id.id or ""
        if not name:
            return request.redirect("/marketplace/mis-instalaciones?facility=%s&edit_pond=%s&error=name" % (rf, pond_id))

        vals = {
            "name": name,
            "code": (post.get("code") or "").strip() or False,
            "pond_type": post.get("pond_type") or pond.pond_type,
            "capacity_mode": post.get("capacity_mode") or pond.capacity_mode,
            "length_m": _f(post.get("length_m")),
            "width_m": _f(post.get("width_m")),
            "depth_m": _f(post.get("depth_m")),
            "manual_volume_m3": _f(post.get("manual_volume_m3")),
            "location": (post.get("location") or "").strip() or False,
        }
        # Reemplazar imagen solo si se sube una nueva.
        img = request.httprequest.files.get("image")
        if img:
            content = img.read()
            if content:
                vals["image"] = base64.b64encode(content)

        try:
            pond.write(vals)
        except ValidationError as e:
            return request.redirect(
                "/marketplace/mis-instalaciones?facility=%s&edit_pond=%s&error=validation&message=%s"
                % (rf, pond_id, e.args[0] if e.args else ""))
        return request.redirect("/marketplace/mis-instalaciones?facility=%s&saved=pond" % rf)

    @http.route("/marketplace/mis-instalaciones/pond/<int:pond_id>/imagen", type="http",
                auth="user", website=True, sitemap=False)
    def pond_image(self, pond_id, **kw):
        partner = self._partner()
        pond = request.env["shrimp.partner.pond"].sudo().browse(pond_id)
        if not pond.exists() or pond.partner_id.id != partner.id or not pond.image:
            raise NotFound()
        content = base64.b64decode(pond.image)
        if content[:8].startswith(b"\x89PNG"):
            mimetype = "image/png"
        elif content[:2] == b"\xff\xd8":
            mimetype = "image/jpeg"
        elif content[:6] in (b"GIF87a", b"GIF89a"):
            mimetype = "image/gif"
        else:
            mimetype = "image/jpeg"
        return request.make_response(content, headers=[
            ("Content-Type", mimetype),
            ("Content-Length", str(len(content))),
            ("Cache-Control", "private, max-age=60"),
        ])

    @http.route("/marketplace/mis-instalaciones/pond/<int:pond_id>/eliminar",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def pond_delete(self, pond_id, **post):
        partner = self._partner()
        pond = request.env["shrimp.partner.pond"].sudo().browse(pond_id)
        if not pond.exists() or pond.partner_id.id != partner.id:
            raise NotFound()
        pond.unlink()
        return request.redirect("/marketplace/mis-instalaciones?saved=pond_del")

    @http.route("/marketplace/mis-instalaciones/asignar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def lot_allocate(self, **post):
        partner = self._partner()
        Lot = request.env["shrimp.stock.lot"].sudo()
        Pond = request.env["shrimp.partner.pond"].sudo()

        lot = Lot.browse(int(post.get("stock_lot_id") or 0))
        pond = Pond.browse(int(post.get("pond_id") or 0))

        if not lot.exists() or lot.owner_id.id != partner.id:
            raise NotFound()
        if not pond.exists() or pond.partner_id.id != partner.id:
            raise NotFound()

        try:
            qty = float(post.get("allocated_qty") or 0.0)
        except (TypeError, ValueError):
            qty = 0.0

        try:
            request.env["shrimp.lot.allocation"].sudo().create({
                "stock_lot_id": lot.id,
                "pond_id": pond.id,
                "allocated_qty": qty,
                "notes": (post.get("notes") or "").strip() or False,
            })
        except ValidationError as e:
            return request.redirect(
                "/marketplace/mis-instalaciones?error=validation&message=%s" % (e.args[0] if e.args else ""))
        return request.redirect("/marketplace/mis-instalaciones?saved=alloc")

    # ==================================================================
    # 5) SOLICITUDES DE CHEQUEO (VENDEDOR)
    # ==================================================================
    @http.route("/marketplace/solicitudes", type="http", auth="user", website=True)
    def my_check_requests(self, **kw):
        partner = self._partner()
        # El usuario ve las solicitudes donde participa, ya sea como vendedor
        # (quien aprueba) o como comprador (quien la solicitó).
        domain = ["|", ("seller_partner_id", "=", partner.id),
                  ("buyer_partner_id", "=", partner.id)]
        state = (kw.get("state") or "").strip()
        if state:
            domain.append(("state", "=", state))
        requests = request.env["shrimp.check.request"].sudo().search(
            domain, order="create_date desc")
        return request.render("shrimp_marketplace.my_check_requests", {
            "partner": partner,
            "requests": requests,
            "state_options": request.env["shrimp.check.request"]._fields["state"].selection,
            "filter_state": state,
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    def _owned_check_request(self, cr_id):
        cr = request.env["shrimp.check.request"].sudo().browse(int(cr_id))
        if not cr.exists() or cr.seller_partner_id.id != self._partner().id:
            raise NotFound()
        return cr

    @http.route("/marketplace/solicitudes/<int:cr_id>/aprobar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def check_request_approve(self, cr_id, **post):
        cr = self._owned_check_request(cr_id)
        if cr.state not in ("requested", "under_review"):
            return request.redirect("/marketplace/solicitudes?error=state")
        try:
            cr.action_approve()
        except ValidationError as e:
            return request.redirect(
                "/marketplace/solicitudes?error=validation&message=%s" % (e.args[0] if e.args else ""))
        return request.redirect("/marketplace/solicitudes?saved=approved")

    @http.route("/marketplace/solicitudes/<int:cr_id>/rechazar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def check_request_reject(self, cr_id, **post):
        cr = self._owned_check_request(cr_id)
        if cr.state not in ("requested", "under_review"):
            return request.redirect("/marketplace/solicitudes?error=state")
        cr.action_reject()
        return request.redirect("/marketplace/solicitudes?saved=rejected")

    # ==================================================================
    # 6) TRAZABILIDAD ON-SCREEN
    # ==================================================================
    @http.route("/marketplace/compras/<tx_ref>/trazabilidad", type="http",
                auth="user", website=True)
    def traceability_screen(self, tx_ref, **kw):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()

        partner = self._partner()
        is_internal = request.env.user.has_group("base.group_user")
        if not is_internal and partner.id not in (tx.buyer_partner_id.id, tx.seller_partner_id.id):
            raise Forbidden()

        data = tx.get_full_traceability_data()
        return request.render("shrimp_marketplace.traceability_screen", {
            "tx": tx,
            "moves": data["moves"],
            "lots": data["lots"],
            "allocations": data["allocations"],
            "evolutions": data["evolutions"],
        })
