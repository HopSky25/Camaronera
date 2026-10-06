import base64
import re

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError
from werkzeug.exceptions import NotFound, Forbidden
from urllib.parse import quote

# Reutiliza la validación real de archivos (magic bytes) del módulo de registro
from odoo.addons.shrimp_user_registry.controllers.main import (
    IMAGE_MIMETYPES, _read_validated_file, binary_headers, flash_message,
    read_upload, resolve_catalog_certificate)
from odoo.addons.shrimp_user_registry.controllers.main import current_partner

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ShrimpAccountPortalController(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _partner(self):
        return current_partner()

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
        content, real_mime, filename = read_upload(file_obj)
        if not content:
            return False

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
    @http.route("/marketplace/my-account", type="http", auth="user", website=True)
    def my_account(self, **kw):
        partner = self._partner()
        # Mi cuenta unifica los certificados: se cargan aquí para mostrarlos en
        # la misma página (antes vivían en /marketplace/my-certificates).
        lines = request.env["shrimp.user.certificate.line"].sudo().search(
            [("partner_id", "=", partner.id)], order="id desc")
        return request.render("shrimp_marketplace.my_account", {
            # sudo: la ficha incluye campos solo-internos (teléfono y
            # representante) que el dueño sí debe ver en su propia cuenta.
            "partner": partner.sudo(),
            "lines": lines,
            "certificates": self._available_certificates(partner),
            # Varios perfiles: los que tiene y los que puede agregarse.
            **self._profile_values(partner),
            "today": fields.Date.context_today(request.env.user),
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/my-account/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def my_account_save(self, **post):
        partner = self._partner()

        email = (post.get("email") or "").strip().lower()
        if email and not EMAIL_RE.match(email):
            return request.redirect("/marketplace/my-account?error=email")

        vals = {
            "phone": post.get("phone") or False,
            "city": post.get("city") or False,
        }
        if email:
            vals["email"] = email

        vals.update(self._guardar_extra(partner, post))

        try:
            partner.sudo().write(vals)
        except ValidationError as e:
            # El motivo real, no "el correo ya está en uso". Antes cualquier
            # error caía en el mismo mensaje: quien borraba su razón social
            # —que es obligatoria— leía un aviso sobre correos duplicados y no
            # tenía forma de saber qué campo arreglar.
            return request.redirect(
                "/marketplace/my-account?error=" + flash_message(
                    e.args[0] if e.args else _("No se pudo guardar.")))
        except Exception:
            return request.redirect("/marketplace/my-account?error=duplicate")

        return request.redirect("/marketplace/my-account?saved=1")

    def _guardar_extra(self, partner, post):
        """Los campos propios del eslabón de quien guarda.

        Es un punto de extensión a propósito: cada rol tiene su bloque de
        ficha y los módulos que añaden roles necesitan enganchar el suyo sin
        editar esta cadena. La empacadora vive en shrimp_packer y sin esto no
        tenía forma de guardar su perfil desde el portal.
        """
        def _f(v):
            try:
                return float(v or 0.0)
            except (TypeError, ValueError):
                return 0.0

        user_type = partner.shrimp_user_type
        if user_type == "laboratorio":
            return {
                "shrimp_razon_social": post.get("lab_razon_social") or False,
                "shrimp_ubicacion": post.get("lab_ubicacion") or False,
                "lab_global_gap": bool(post.get("lab_global_gap")),
                "lab_social_ship_partner": bool(post.get("lab_social_ship_partner")),
            }
        if user_type == "camaronera":
            return {
                "shrimp_razon_social": post.get("farm_razon_social") or False,
                "shrimp_representante": post.get("farm_representante") or False,
                "shrimp_telefono": post.get("farm_telefono") or False,
                "shrimp_ubicacion": post.get("farm_ubicacion") or False,
                "shrimp_capacity_value": _f(post.get("farm_capacidad")),
                "shrimp_capacity_unit": "ton_year",
                "farm_area_ha": _f(post.get("farm_area_ha")),
                # Casilla: viene o no viene, no llega "false".
                "farm_publicar_historial": bool(post.get("farm_publicar_historial")),
            }
        return {}

    # ==================================================================
    # 2) MIS CERTIFICADOS
    # ==================================================================
    def _profile_values(self, partner):
        """Perfiles de la cuenta para «Mi cuenta»: líneas, perfil activo y los
        que se pueden agregar (con el catálogo de certificados de cada uno)."""
        socio = partner.sudo()._shrimp_role_holder()
        Partner = request.env["res.partner"].sudo()
        es_cuenta = socio == partner.sudo()
        exclusivos = set(Partner._shrimp_types_with("exclusive_profile"))
        tiene = set(socio._shrimp_roles(include_pending=True))
        agregables = []
        if es_cuenta and not (tiene & exclusivos):
            agregables = [r for r in Partner._shrimp_addable_roles() if r not in tiene]
        certs = request.env["shrimp.certificate"].sudo().search(
            [("active", "=", True), ("role", "in", agregables + ["all"])],
            order="sequence, name") if agregables else request.env["shrimp.certificate"]
        return {
            "shrimp_role_lines": socio.shrimp_role_ids,
            "shrimp_active_role": socio.shrimp_user_type,
            "shrimp_addable_roles": [(r, Partner._shrimp_type_label(r),
                                      Partner._shrimp_type_can(r, "requires_approval"))
                                     for r in agregables],
            "shrimp_addable_certs": certs,
            "shrimp_role_label": Partner._shrimp_type_label,
        }

    def _available_certificates(self, partner):
        # Los certificados de TODOS los perfiles de la cuenta (aprobados o
        # pendientes), no solo los del activo.
        roles = partner.sudo()._shrimp_roles(include_pending=True)
        return request.env["shrimp.certificate"].sudo().search([
            ("active", "=", True),
            ("role", "in", roles + ["all"]),
        ], order="name asc")

    def _validated_certificate(self, partner, cert_id):
        # Catálogo público: por código (o id, por compatibilidad).
        return resolve_catalog_certificate(
            cert_id, partner.sudo()._shrimp_roles(include_pending=True) + ["all"])

    @http.route("/marketplace/my-certificates", type="http", auth="user", website=True)
    def my_certificates(self, **kw):
        # Los certificados se unificaron dentro de "Mi cuenta".
        return request.redirect("/marketplace/my-account")

    @http.route("/marketplace/my-certificates/add", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def my_certificate_add(self, **post):
        partner = self._partner()

        cert = self._validated_certificate(partner, post.get("certificate_id"))
        if not cert:
            return request.redirect("/marketplace/my-account?error=cert")

        file_obj = request.httprequest.files.get("file")
        if not file_obj:
            return request.redirect("/marketplace/my-account?error=file")

        try:
            att = self._create_private_attachment(
                file_obj, partner, name_prefix=f"cert_{cert.id}_")
            if not att:
                return request.redirect("/marketplace/my-account?error=file")

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
                "/marketplace/my-account?error=validation&message=%s" % flash_message(e.args[0] if e.args else ""))

        return request.redirect("/marketplace/my-account?saved=1")

    def _owned_cert_line(self, line_ref):
        """Línea de certificado propia, por su código (uuid_ref). Un id
        numérico no se acepta: responde 404."""
        line = request.env["shrimp.user.certificate.line"].sudo().resolve_ref(line_ref)
        if not line or line.partner_id.id != self._partner().id:
            raise NotFound()
        return line

    @http.route("/marketplace/my-certificates/<line_ref>/renew", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def my_certificate_renew(self, line_ref, **post):
        partner = self._partner()
        line = self._owned_cert_line(line_ref)

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
                "/marketplace/my-account?error=validation&message=%s" % flash_message(e.args[0] if e.args else ""))

        return request.redirect("/marketplace/my-account?saved=renew")

    @http.route("/marketplace/my-certificates/<line_ref>/delete", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def my_certificate_delete(self, line_ref, **post):
        line = self._owned_cert_line(line_ref)
        line.unlink()
        return request.redirect("/marketplace/my-account?saved=delete")

    @http.route("/marketplace/my-account/certificates/<line_ref>/file", type="http",
                auth="user", website=True, sitemap=False)
    def my_certificate_file(self, line_ref, **kw):
        """Entrega el certificado en línea para que el visor lo pinte dentro del
        modal. La ruta compartida /marketplace/my-certificate/<id>/file fuerza
        `Content-Disposition: attachment`, así que el iframe del visor acababa
        descargando el PDF en vez de mostrarlo. Con `download=1` sí se descarga,
        que es lo que pide el botón «Descargar» del propio visor.
        """
        line = self._owned_cert_line(line_ref)
        att = line.file_attachment_id
        if not att or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype, att.name or "certificado", download=bool(kw.get("download"))))

    # ==================================================================
    # 3) MIS LOTES / INVENTARIO
    # ==================================================================
    @http.route("/marketplace/my-lots", type="http", auth="user", website=True)
    def my_lots(self, **kw):
        partner = self._partner()
        lots = request.env["shrimp.stock.lot"].sudo().search([
            ("owner_id", "=", partner.id),
        ], order="id desc")
        Alloc = request.env["shrimp.lot.allocation"]
        can_sow = Alloc._shrimp_can_sow(partner)
        return request.render("shrimp_marketplace.my_lots", {
            "partner": partner,
            "lots": lots,
            # Lotes que se pueden sembrar desde aquí (botón «Sembrar»).
            "sowable_lot_ids": set(Alloc._shrimp_sowable_lots(partner).ids) if can_sow else set(),
        })

    # ==================================================================
    # 4) INSTALACIONES Y PISCINAS + ASIGNACIÓN
    # ==================================================================
    @http.route("/marketplace/my-facilities", type="http", auth="user", website=True)
    def my_facilities(self, **kw):
        partner = self._partner()
        Facility = request.env["shrimp.partner.facility"].sudo()
        Pond = request.env["shrimp.partner.pond"].sudo()
        Lot = request.env["shrimp.stock.lot"].sudo()
        Alloc = request.env["shrimp.lot.allocation"].sudo()

        facilities = Facility.search([("partner_id", "=", partner.id)])
        ponds = Pond.search([("partner_id", "=", partner.id)])
        can_sow = Alloc._shrimp_can_sow(partner)
        lots = Alloc._shrimp_sowable_lots(partner) if can_sow else Lot
        allocations = Alloc.search([("partner_id", "=", partner.id)])

        # Instalación seleccionada (master-detail): por su código o la primera.
        sel_facility = Facility.resolve_ref(kw.get("facility")) if kw.get("facility") else Facility
        if not (sel_facility and sel_facility.partner_id.id == partner.id):
            sel_facility = facilities[:1]  # primera o vacío
        sel_facility_id = sel_facility.id if sel_facility else False
        sel_ponds = ponds.filtered(lambda p: p.facility_id.id == sel_facility_id) if sel_facility_id else ponds.filtered(lambda p: not p.facility_id)

        # Ya no hay "piscina en edición" por URL: crear y editar (instalación y
        # piscina) ocurren en un modal que se rellena desde la propia tabla.
        return request.render("shrimp_marketplace.my_facilities", {
            "partner": partner,
            "facilities": facilities,
            "ponds": ponds,
            "sel_facility": sel_facility,
            "sel_ponds": sel_ponds,
            "is_new": bool(kw.get("new")),
            "lots": lots,
            "allocations": allocations,
            "can_sow": can_sow,
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/my-facilities/facility/create", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def facility_create(self, **post):
        partner = self._partner()
        name = (post.get("name") or "").strip()
        if not name:
            return request.redirect("/marketplace/my-facilities?error=name&new=1")
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
        return request.redirect("/marketplace/my-facilities?saved=facility&facility=%s" % fac.uuid_ref)

    def _owned(self, model, ref):
        """Registro propio (partner_id = usuario) por su uuid_ref, o 404."""
        rec = request.env[model].sudo().resolve_ref(ref)
        if not rec or rec.partner_id.id != self._partner().id:
            raise NotFound()
        return rec

    @http.route("/marketplace/my-facilities/facility/<facility_ref>/update", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def facility_update(self, facility_ref, **post):
        fac = self._owned("shrimp.partner.facility", facility_ref)
        name = (post.get("name") or "").strip()
        if not name:
            return request.redirect("/marketplace/my-facilities?facility=%s&edit=1&error=name" % fac.uuid_ref)
        fac.write({
            "name": name,
            "code": (post.get("code") or "").strip() or False,
            "facility_type": post.get("facility_type") or fac.facility_type,
            "city": (post.get("city") or "").strip() or False,
            "address": (post.get("address") or "").strip() or False,
        })
        return request.redirect("/marketplace/my-facilities?facility=%s&saved=facility" % fac.uuid_ref)

    @http.route("/marketplace/my-facilities/facility/<facility_ref>/delete",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def facility_delete(self, facility_ref, **post):
        facility = self._owned("shrimp.partner.facility", facility_ref)
        facility.unlink()
        return request.redirect("/marketplace/my-facilities?saved=facility_del")

    @http.route("/marketplace/my-facilities/pond/create", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def pond_create(self, **post):
        partner = self._partner()
        name = (post.get("name") or "").strip()
        if not name:
            return request.redirect("/marketplace/my-facilities?error=name")

        def _f(v):
            try:
                return float(v or 0.0)
            except (TypeError, ValueError):
                return 0.0

        facility_id = False
        facility_ref = False
        if post.get("facility_ref"):
            fac = request.env["shrimp.partner.facility"].sudo().resolve_ref(post.get("facility_ref"))
            if fac and fac.partner_id.id == partner.id:
                facility_id = fac.id
                facility_ref = fac.uuid_ref

        try:
            # Imagen opcional de la piscina: solo imágenes reales, máx. 5 MB.
            image_b64 = False
            content, _mime, _n = read_upload(request.httprequest.files.get("image"),
                                             allowed=IMAGE_MIMETYPES)
            if content:
                image_b64 = base64.b64encode(content)

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
                "/marketplace/my-facilities?error=validation&message=%s" % flash_message(e.args[0] if e.args else ""))
        # Volver a la instalación seleccionada tras crear la piscina.
        rf = facility_ref
        suffix = ("&facility=%s" % rf) if rf else ""
        return request.redirect("/marketplace/my-facilities?saved=pond" + suffix)

    @http.route("/marketplace/my-facilities/pond/<pond_ref>/update", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def pond_update(self, pond_ref, **post):
        pond = self._owned("shrimp.partner.pond", pond_ref)

        def _f(v):
            try:
                return float(v or 0.0)
            except (TypeError, ValueError):
                return 0.0

        name = (post.get("name") or "").strip()
        rf = pond.facility_id.uuid_ref or ""
        if not name:
            return request.redirect("/marketplace/my-facilities?facility=%s&error=name" % rf)

        vals = {
            "name": name,
            "pond_type": post.get("pond_type") or pond.pond_type,
            "capacity_mode": post.get("capacity_mode") or pond.capacity_mode,
            "length_m": _f(post.get("length_m")),
            "width_m": _f(post.get("width_m")),
            "depth_m": _f(post.get("depth_m")),
            "manual_volume_m3": _f(post.get("manual_volume_m3")),
            "location": (post.get("location") or "").strip() or False,
        }
        # El modal de edición no expone el código, y escribirlo a ciegas lo
        # borraba en cada guardado: solo se toca si el formulario lo manda.
        if "code" in post:
            vals["code"] = (post.get("code") or "").strip() or False
        try:
            # Reemplazar imagen solo si se sube una nueva (validada por contenido).
            content, _mime, _n = read_upload(request.httprequest.files.get("image"),
                                             allowed=IMAGE_MIMETYPES)
            if content:
                vals["image"] = base64.b64encode(content)
            pond.write(vals)
        except ValidationError as e:
            return request.redirect(
                "/marketplace/my-facilities?facility=%s&error=validation&message=%s"
                % (rf, flash_message(e.args[0] if e.args else "")))
        return request.redirect("/marketplace/my-facilities?facility=%s&saved=pond" % rf)

    @http.route("/marketplace/my-facilities/pond/<pond_ref>/image", type="http",
                auth="user", website=True, sitemap=False)
    def pond_image(self, pond_ref, **kw):
        pond = self._owned("shrimp.partner.pond", pond_ref)
        if not pond.image:
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
        return request.make_response(content, headers=binary_headers(
            content, mimetype, cache="private, max-age=60"))

    @http.route("/marketplace/my-facilities/pond/<pond_ref>/delete",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def pond_delete(self, pond_ref, **post):
        pond = self._owned("shrimp.partner.pond", pond_ref)
        pond.unlink()
        return request.redirect("/marketplace/my-facilities?saved=pond_del")

    # ------------------------------------------------------------------
    # SIEMBRA (lote de larva / juvenil -> piscina)
    # ------------------------------------------------------------------
    # La ruta POST de asignación existía pero el portal no tenía formulario:
    # la camaronera no podía sembrar desde la web. Ahora hay un formulario
    # propio (desde «Mi inventario» y desde cada piscina), la lista de
    # siembras por piscina y editar / cancelar mientras ninguna cosecha la
    # declare como origen. El consumo del lote (movimiento «sowing») y la
    # devolución al cancelar los hace el modelo (shrimp.lot.allocation).
    def _sowing_guard(self, partner):
        if not request.env["shrimp.lot.allocation"]._shrimp_can_sow(partner):
            raise NotFound()

    def _owned_sowing(self, alloc_ref):
        alloc = request.env["shrimp.lot.allocation"].sudo().resolve_ref(alloc_ref)
        if not alloc or alloc.partner_id.id != self._partner().id:
            raise NotFound()
        return alloc

    @staticmethod
    def _sowing_date(value):
        """Fecha de siembra del formulario: obligatoria, válida y no futura."""
        try:
            fecha = fields.Date.to_date(value) if value else None
        except (TypeError, ValueError):
            fecha = None
        if not fecha:
            raise ValidationError(_("Indica una fecha de siembra válida."))
        if fecha > fields.Date.context_today(request.env.user):
            raise ValidationError(_("La fecha de siembra no puede ser futura."))
        return fecha

    @staticmethod
    def _sowing_qty(value):
        try:
            qty = float((value or "0").replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            qty = 0.0
        if qty <= 0:
            raise ValidationError(_("La cantidad sembrada debe ser mayor a 0."))
        return qty

    def _sowing_back(self, pond):
        rf = pond.facility_id.uuid_ref if pond and pond.facility_id else ""
        return "/marketplace/my-facilities?saved=alloc" + (("&facility=%s" % rf) if rf else "")

    @http.route(["/marketplace/sowings/new", "/marketplace/sowings/<alloc_ref>/edit"],
                type="http", auth="user", website=True, sitemap=False)
    def sowing_form(self, alloc_ref=None, **kw):
        partner = self._partner()
        self._sowing_guard(partner)
        Alloc = request.env["shrimp.lot.allocation"].sudo()
        alloc = self._owned_sowing(alloc_ref) if alloc_ref else Alloc
        ponds = request.env["shrimp.partner.pond"].sudo().search(
            [("partner_id", "=", partner.id)], order="facility_id, name")
        block = alloc._shrimp_portal_edit_block() if alloc else None
        if alloc:
            lots = alloc.stock_lot_id
            sel_lot = alloc.stock_lot_id
            sel_pond = alloc.pond_id
            max_qty = (sel_lot.available_qty or 0.0) + (alloc.allocated_qty if alloc.sowing_move_id else 0.0)
        else:
            lots = Alloc._shrimp_sowable_lots(partner)
            Lot = request.env["shrimp.stock.lot"].sudo()
            sel_lot = Lot.resolve_ref(kw.get("lote")) if kw.get("lote") else Lot
            sel_lot = sel_lot if sel_lot in lots else lots[:1]
            Pond = request.env["shrimp.partner.pond"].sudo()
            sel_pond = Pond.resolve_ref(kw.get("piscina")) if kw.get("piscina") else Pond
            sel_pond = sel_pond if sel_pond in ponds else ponds[:1]
            max_qty = sel_lot.available_qty if sel_lot else 0.0
        return request.render("shrimp_marketplace.sowing_form", {
            "partner": partner,
            "alloc": alloc,
            "edit_block": block,
            "lots": lots,
            "ponds": ponds,
            "sel_lot": sel_lot,
            "sel_pond": sel_pond,
            "max_qty": max_qty,
            "today": fields.Date.context_today(request.env.user),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/my-facilities/allocate", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def lot_allocate(self, **post):
        partner = self._partner()
        self._sowing_guard(partner)
        Lot = request.env["shrimp.stock.lot"].sudo()
        Pond = request.env["shrimp.partner.pond"].sudo()
        Alloc = request.env["shrimp.lot.allocation"].sudo()

        # Lote y piscina por su código (uuid_ref), nunca por id.
        lot = Lot.resolve_ref(post.get("stock_lot_ref"))
        pond = Pond.resolve_ref(post.get("pond_ref"))

        if not lot or lot.owner_id.id != partner.id:
            raise NotFound()
        if not pond or pond.partner_id.id != partner.id:
            raise NotFound()

        def _volver(msg):
            return request.redirect(
                "/marketplace/sowings/new?lote=%s&piscina=%s&error=validation&message=%s"
                % (lot.uuid_ref, pond.uuid_ref, flash_message(msg)))

        try:
            if not Alloc._shrimp_lot_is_sowable(lot):
                raise ValidationError(_(
                    "Ese lote no se puede sembrar: solo larva, postlarva o juvenil "
                    "propios con saldo disponible."))
            vals = {
                "stock_lot_id": lot.id,
                "pond_id": pond.id,
                "allocated_qty": self._sowing_qty(post.get("allocated_qty")),
                "allocation_date": self._sowing_date(
                    post.get("allocation_date") or fields.Date.context_today(request.env.user)),
                "notes": (post.get("notes") or "").strip()[:2000] or False,
            }
            # Savepoint: si el consumo del lote falla (más de lo disponible),
            # la siembra no puede quedar creada sin descontar el lote.
            with request.env.cr.savepoint():
                Alloc.create(vals)
        except ValidationError as e:
            return _volver(e.args[0] if e.args else "")
        return request.redirect(self._sowing_back(pond))

    @http.route("/marketplace/sowings/<alloc_ref>/update", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def sowing_update(self, alloc_ref, **post):
        partner = self._partner()
        self._sowing_guard(partner)
        alloc = self._owned_sowing(alloc_ref)
        url_form = "/marketplace/sowings/%s/edit" % alloc.uuid_ref
        try:
            block = alloc._shrimp_portal_edit_block()
            if block:
                raise ValidationError(block)
            pond = alloc.pond_id
            if post.get("pond_ref"):
                pond = request.env["shrimp.partner.pond"].sudo().resolve_ref(post.get("pond_ref"))
                if not pond or pond.partner_id.id != partner.id:
                    raise NotFound()
            vals = {
                "pond_id": pond.id,
                "allocated_qty": self._sowing_qty(post.get("allocated_qty")),
                "allocation_date": self._sowing_date(post.get("allocation_date")),
                "notes": (post.get("notes") or "").strip()[:2000] or False,
            }
            with request.env.cr.savepoint():
                alloc.write(vals)
        except ValidationError as e:
            return request.redirect("%s?error=validation&message=%s"
                                    % (url_form, flash_message(e.args[0] if e.args else "")))
        return request.redirect(self._sowing_back(alloc.pond_id))

    @http.route("/marketplace/sowings/<alloc_ref>/cancel", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def sowing_cancel(self, alloc_ref, **post):
        partner = self._partner()
        self._sowing_guard(partner)
        alloc = self._owned_sowing(alloc_ref)
        pond = alloc.pond_id
        rf = pond.facility_id.uuid_ref or ""
        block = alloc._shrimp_portal_edit_block()
        if block:
            return request.redirect(
                "/marketplace/my-facilities?facility=%s&error=validation&message=%s"
                % (rf, flash_message(block)))
        # El modelo devuelve al lote lo sembrado con un ajuste documentado.
        with request.env.cr.savepoint():
            alloc.write({"state": "cancelled"})
        return request.redirect(
            "/marketplace/my-facilities?saved=alloc_cancel" + (("&facility=%s" % rf) if rf else ""))

    # ==================================================================
    # 5) SOLICITUDES DE CHEQUEO (VENDEDOR)
    # ==================================================================
    @http.route("/marketplace/check-requests", type="http", auth="user", website=True)
    def my_check_requests(self, **kw):
        partner = self._partner()
        # El usuario ve las solicitudes donde participa, ya sea como vendedor
        # (quien aprueba) o como comprador (quien la solicitó).
        domain = ["|", ("seller_partner_id", "=", partner.id),
                  ("buyer_partner_id", "=", partner.id)]
        CheckRequest = request.env["shrimp.check.request"].sudo()
        # Un solo filtro (el estado): se queda como fila de chips, sin cajón.
        # Solo los estados que la cuenta tiene; con uno no hay fila.
        presentes = set(CheckRequest.search(domain).mapped("state"))
        state = (kw.get("state") or "").strip()
        if state:
            domain.append(("state", "=", state))
        requests = CheckRequest.search(domain, order="create_date desc")
        return request.render("shrimp_marketplace.my_check_requests", {
            "partner": partner,
            "requests": requests,
            "state_options": [
                (v, l) for v, l in CheckRequest._fields["state"].selection
                if v in presentes or v == state],
            "filter_state": state,
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/check-requests/<cr_ref>/detail", type="http", auth="user",
                website=True)
    def check_request_detail(self, cr_ref, **kw):
        """Detalle completo de una solicitud de chequeo, visible para el
        comprador y el vendedor que participan en ella."""
        partner = self._partner()
        cr = request.env["shrimp.check.request"].sudo().resolve_ref(cr_ref)
        if not cr or partner.id not in (
                cr.seller_partner_id.id, cr.buyer_partner_id.id):
            raise NotFound()
        return request.render("shrimp_marketplace.check_request_detail", {
            "partner": partner,
            "cr": cr,
            "is_seller": cr.seller_partner_id.id == partner.id,
            "message": kw.get("message"),
            "error": kw.get("error"),
        })

    def _owned_check_request(self, cr_ref):
        cr = request.env["shrimp.check.request"].sudo().resolve_ref(cr_ref)
        if not cr or cr.seller_partner_id.id != self._partner().id:
            raise NotFound()
        return cr

    @http.route("/marketplace/check-requests/<cr_ref>/approve", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def check_request_approve(self, cr_ref, **post):
        cr = self._owned_check_request(cr_ref)
        if cr.state not in ("requested", "under_review"):
            return request.redirect("/marketplace/check-requests?error=state")
        try:
            cr.action_approve()
        except ValidationError as e:
            return request.redirect(
                "/marketplace/check-requests?error=validation&message=%s" % flash_message(e.args[0] if e.args else ""))
        return request.redirect("/marketplace/check-requests?saved=approved")

    @http.route("/marketplace/check-requests/<cr_ref>/reject", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def check_request_reject(self, cr_ref, **post):
        cr = self._owned_check_request(cr_ref)
        if cr.state not in ("requested", "under_review"):
            return request.redirect("/marketplace/check-requests?error=state")
        cr.action_reject()
        return request.redirect("/marketplace/check-requests?saved=rejected")

    # ==================================================================
    # 6) TRAZABILIDAD ON-SCREEN
    # ==================================================================
    @http.route("/marketplace/purchases/<tx_ref>/traceability", type="http",
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
            "trace": data,
        })
