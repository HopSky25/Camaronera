import base64
import re

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import ValidationError, UserError
from werkzeug.exceptions import NotFound, Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import ShrimpRegistryController


class ShrimpVerificationPortal(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _partner(self):
        return request.env.user.partner_id

    def _is_verifier(self):
        return self._partner().shrimp_user_type == "verificador"

    def _my_verification(self, ref, editable=False):
        """Verificación de la que el usuario es el verificador asignado."""
        rec = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not rec:
            raise NotFound()
        if rec.verifier_partner_id.id != self._partner().id:
            raise Forbidden()
        if editable and rec.is_final:
            raise Forbidden()
        return rec

    def _to_float(self, value, default=0.0):
        try:
            return float(str(value).replace(",", ".") or default)
        except (TypeError, ValueError):
            return default

    # ==================================================================
    # BANDEJA DEL VERIFICADOR
    # ==================================================================
    @http.route("/verificador/bandeja", type="http", auth="user", website=True)
    def verifier_inbox(self, **kw):
        if not self._is_verifier():
            raise Forbidden()
        partner = self._partner()

        domain = [("verifier_partner_id", "=", partner.id)]
        state = (kw.get("state") or "").strip()
        if state == "open":
            domain.append(("state", "in", ["assigned", "in_field", "done"]))
        elif state:
            domain.append(("state", "=", state))

        q = (kw.get("q") or "").strip()
        if q:
            domain += ["|", "|",
                       ("name", "ilike", q),
                       ("batch_code", "ilike", q),
                       ("product_id.name", "ilike", q)]

        verifications = request.env["shrimp.verification"].sudo().search(
            domain, order="state asc, assigned_date asc")

        counters = {
            "assigned": len(verifications.filtered(lambda v: v.state == "assigned")),
            "in_field": len(verifications.filtered(lambda v: v.state == "in_field")),
            "done": len(verifications.filtered(lambda v: v.state == "done")),
        }

        return request.render("shrimp_verification.verifier_inbox", {
            "page_name": "verifier_inbox",
            "verifications": verifications,
            "counters": counters,
            "filter_state": state,
            "q": q,
            "state_options": request.env["shrimp.verification"]._fields["state"].selection,
        })

    @http.route("/verificador/verificacion/<ref>", type="http", auth="user", website=True)
    def verifier_detail(self, ref, **kw):
        rec = self._my_verification(ref)
        return request.render("shrimp_verification.verifier_detail", {
            "page_name": "verifier_detail",
            "v": rec,
            "warnings": rec.report_warnings(),
            # OJO: el parte NO se pasa por el qcontext. Odoo aplica formato de
            # cadena al contexto al renderizar la pagina de website, y el texto
            # contiene '%' (67,22%), lo que revienta con "incomplete format".
            # La plantilla llama directamente a v.whatsapp_report().
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/verificador/verificacion/<ref>/iniciar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_start(self, ref, **post):
        rec = self._my_verification(ref, editable=True)
        try:
            rec.action_start_field()
        except UserError as e:
            return request.redirect(f"/verificador/verificacion/{rec.uuid_ref}?error=1&message={e.args[0]}")
        return request.redirect(f"/verificador/verificacion/{rec.uuid_ref}?saved=started")

    @http.route("/verificador/verificacion/<ref>/guardar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_save(self, ref, **post):
        """Guarda el informe de campo. Se puede guardar por partes: una
        verificación no se completa de una sentada."""
        rec = self._my_verification(ref, editable=True)
        F = self._to_float

        vals = {
            "batch_code": (post.get("batch_code") or "").strip() or False,
            "pond_label": (post.get("pond_label") or "").strip() or False,
            "plant_name": (post.get("plant_name") or "").strip() or False,
            "harvest_date": post.get("harvest_date") or False,
            "process_date": post.get("process_date") or False,
            # 1 · peso
            "weight_sent_lb": F(post.get("weight_sent_lb")),
            "weight_plant_lb": F(post.get("weight_plant_lb")),
            "trash_lb": F(post.get("trash_lb")),
            # 2 · cuerpo o cola
            "presentation": post.get("presentation") or False,
            # 3 · metabisulfito
            "metabisulfite_ppm": F(post.get("metabisulfite_ppm")),
            "metabisulfite_limit_ppm": F(post.get("metabisulfite_limit_ppm"), 100.0),
            "metabisulfite_notes": (post.get("metabisulfite_notes") or "").strip() or False,
            # 5 · sabor
            "taste_result": post.get("taste_result") or False,
            "taste_notes": (post.get("taste_notes") or "").strip() or False,
            "smell_ok": bool(post.get("smell_ok")),
            "color_ok": bool(post.get("color_ok")),
            # gramajes
            "grams_farm": F(post.get("grams_farm")),
            "grams_plant_1": F(post.get("grams_plant_1")),
            "grams_plant_2": F(post.get("grams_plant_2")),
            # verificación de larva (cuando el producto no es camarón adulto)
            "larvae_qty_verified": F(post.get("larvae_qty_verified")),
            "larvae_survival_rate": F(post.get("larvae_survival_rate")),
            "larvae_avg_size_mg": F(post.get("larvae_avg_size_mg")),
            "larvae_health_status": post.get("larvae_health_status") or False,
            "larvae_health_notes": (post.get("larvae_health_notes") or "").strip() or False,
            # incidencias
            "incident_notes": (post.get("incident_notes") or "").strip() or False,
            "gps_latitude": F(post.get("gps_latitude")),
            "gps_longitude": F(post.get("gps_longitude")),
        }

        try:
            rec.write(vals)
            self._save_lines(rec, post)
            self._save_counts(rec, post)
            self._save_photos(rec)
        except ValidationError as e:
            msg = e.args[0] if e.args else ""
            return request.redirect(
                f"/verificador/verificacion/{rec.uuid_ref}?error=1&message={msg}")

        return request.redirect(f"/verificador/verificacion/{rec.uuid_ref}?saved=1")

    # ------------------------------------------------------------------
    # 4 · clasificación: se reemplazan las líneas enviadas por el formulario
    # ------------------------------------------------------------------
    def _save_lines(self, rec, post):
        Line = request.env["shrimp.verification.line"].sudo()
        indices = set()
        for key in post:
            if key.startswith("line_") and key.endswith("_size"):
                part = key[len("line_"):-len("_size")]
                if part.isdigit():
                    indices.add(int(part))

        if not indices:
            return

        rec.line_ids.unlink()
        seq = 10
        for idx in sorted(indices):
            size = (post.get(f"line_{idx}_size") or "").strip()
            weight = self._to_float(post.get(f"line_{idx}_weight"))
            quality = post.get(f"line_{idx}_class") or "a"
            if not size or weight <= 0:
                continue
            Line.create({
                "verification_id": rec.id,
                "quality_class": quality,
                "size_code": size,
                "weight_lb": weight,
                "sequence": seq,
            })
            seq += 10

    def _save_counts(self, rec, post):
        Count = request.env["shrimp.verification.count"].sudo()
        values = []
        for key in sorted(k for k in post if k.startswith("count_") and k.endswith("_value")):
            val = self._to_float(post.get(key))
            if val > 0:
                values.append(val)
        if not values:
            return
        rec.count_ids.unlink()
        for i, val in enumerate(values):
            Count.create({"verification_id": rec.id, "value": val, "sequence": 10 * (i + 1)})

    def _save_photos(self, rec):
        files = request.httprequest.files.getlist("photo_files") or []
        att_ids = []
        for f in files:
            content = f.read()
            if not content:
                continue
            att = request.env["ir.attachment"].sudo().create({
                "name": getattr(f, "filename", None) or "foto",
                "type": "binary",
                "datas": base64.b64encode(content),
                "mimetype": getattr(f, "content_type", None) or "image/jpeg",
                "res_model": "shrimp.verification",
                "res_id": rec.id,
                "public": False,
            })
            att_ids.append(att.id)
        if att_ids:
            rec.write({"photo_ids": [(4, aid) for aid in att_ids]})

    # ------------------------------------------------------------------
    # Veredicto
    # ------------------------------------------------------------------
    @http.route("/verificador/verificacion/<ref>/veredicto", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_verdict(self, ref, **post):
        rec = self._my_verification(ref, editable=True)
        verdict = post.get("verdict")
        notes = (post.get("verdict_notes") or "").strip()

        actions = {
            "approve": "approved",
            "approve_obs": "approved_obs",
            "reject": "rejected",
        }
        if verdict not in actions:
            return request.redirect(f"/verificador/verificacion/{rec.uuid_ref}?error=1&message=Veredicto no válido")

        try:
            if rec.state == "in_field":
                rec.action_mark_done()
            rec._close(actions[verdict], notes=notes)
            if verdict == "reject":
                rec.transaction_id.action_cancel_for_verification()
        except UserError as e:
            return request.redirect(
                f"/verificador/verificacion/{rec.uuid_ref}?error=1&message={e.args[0]}")

        return request.redirect("/verificador/bandeja?saved=verdict")

    # ==================================================================
    # LADO DEL COMPRADOR
    # ==================================================================
    @http.route("/marketplace/buy/<product_ref>/verificar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def buy_with_verification(self, product_ref, **post):
        """Graba la compra y la deja pendiente de verificación en campo."""
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active or product.state != "published":
            raise NotFound()

        buyer = self._partner()
        qty = self._to_float(post.get("qty"))
        if qty <= 0:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=qty")

        verifier = request.env["res.partner"].sudo().resolve_ref(post.get("verifier_ref") or "")
        if not verifier:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=verifier")

        fee = float(request.env["ir.config_parameter"].sudo().get_param(
            "shrimp_verification.fee") or 0.0)

        try:
            result = product.start_verified_purchase(buyer, qty, verifier, fee=fee)
        except ValidationError as e:
            msg = e.args[0] if e.args else ""
            return request.redirect(
                f"/marketplace/buy/{product.uuid_ref}?error=validation&message={msg}")

        return request.redirect(
            f"/marketplace/verificacion/pendiente/{result['transaction'].uuid_ref}")

    @http.route("/marketplace/verificacion/pendiente/<tx_ref>", type="http", auth="user", website=True)
    def purchase_pending(self, tx_ref, **kw):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()
        partner = self._partner()
        if partner.id not in (tx.buyer_partner_id.id, tx.seller_partner_id.id):
            raise Forbidden()
        return request.render("shrimp_verification.purchase_pending", {
            "tx": tx,
            "v": tx.verification_id[:1],
        })

    @http.route("/marketplace/compras/<tx_ref>/concluir", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def complete_purchase(self, tx_ref, **post):
        """El comprador concluye la compra ya verificada: aquí sí se consumen lotes."""
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()
        if tx.buyer_partner_id.id != self._partner().id:
            raise Forbidden()
        try:
            tx.action_complete_after_verification()
        except (UserError, ValidationError) as e:
            msg = e.args[0] if e.args else ""
            return request.redirect(f"/marketplace/compras?error=complete&message={msg}")
        return request.redirect(f"/marketplace/thanks/{tx.uuid_ref}?verified=1")

    # ==================================================================
    # Evidencia de campo
    # ==================================================================
    @http.route("/marketplace/verificacion/<ref>/foto/<int:attachment_id>", type="http",
                auth="user", website=True, sitemap=False)
    def verification_photo(self, ref, attachment_id, **kw):
        """Sirve una foto tomada por el verificador en campo.

        La ven las partes de la operación (comprador, vendedor y el propio
        verificador) y los usuarios internos: es la evidencia que respalda el
        veredicto.
        """
        rec = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not rec:
            raise NotFound()

        partner = self._partner()
        permitido = request.env.user.has_group("base.group_user") or partner.id in (
            rec.buyer_partner_id.id, rec.seller_partner_id.id, rec.verifier_partner_id.id)
        if not permitido:
            raise Forbidden()

        # Solo adjuntos de ESTA verificación: evita usar la ruta para leer
        # cualquier ir.attachment de la base.
        if attachment_id not in rec.photo_ids.ids:
            raise NotFound()

        att = request.env["ir.attachment"].sudo().browse(attachment_id)
        if not att.exists() or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        return request.make_response(content, headers=[
            ("Content-Type", att.mimetype or "image/jpeg"),
            ("Content-Length", str(len(content))),
            ("Cache-Control", "private, max-age=3600"),
        ])

    # ==================================================================
    # Acreditación del verificador (el "ojito" del comprador)
    # ==================================================================
    @http.route("/marketplace/verificador/<partner_ref>/acreditacion", type="http",
                auth="user", website=True, sitemap=False)
    def verifier_accreditation(self, partner_ref, **kw):
        """Muestra el documento que acredita al verificador.

        Cualquier usuario autenticado del marketplace puede consultarlo: es
        justamente la información que le permite decidir en quién confiar.
        """
        verifier = request.env["res.partner"].sudo().resolve_ref(partner_ref)
        if not verifier or verifier.shrimp_user_type != "verificador":
            raise NotFound()

        line = verifier.verifier_accreditation_line_id
        if not line or not line.file_attachment_id:
            raise NotFound()

        att = line.file_attachment_id
        if not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        filename = (att.name or "acreditacion").replace("/", "-").replace("\\", "-")
        # Por defecto se ve en el navegador; con ?download=1 se descarga.
        disposition = "attachment" if kw.get("download") else "inline"
        return request.make_response(content, headers=[
            ("Content-Type", att.mimetype or "application/octet-stream"),
            ("Content-Length", str(len(content))),
            ("Content-Disposition", '%s; filename="%s"' % (disposition, filename)),
            ("Cache-Control", "private, max-age=0"),
        ])

    # ==================================================================
    # Combo de verificadores (para el formulario de compra)
    # ==================================================================
    @http.route("/marketplace/verificadores", type="jsonrpc", auth="user", website=True)
    def verifiers_list(self, **kw):
        verifiers = request.env["res.partner"].sudo().available_verifiers()
        return [{
            "ref": v.uuid_ref,
            "name": v.name,
            "rating": round(v.shrimp_rating_avg or 0.0, 2),
            "reviews": v.shrimp_rating_count,
            "accredited": v.verifier_is_accredited,
            "city": v.city or "",
        } for v in verifiers]


class ShrimpRegistryVerifier(ShrimpRegistryController):
    """Extiende el registro público para admitir el rol de verificador.

    El endpoint original solo aceptaba los tres roles productivos, así que al
    elegir "Verificador" el formulario no cargaba ningún certificado.
    """

    def _extra_partner_vals(self, user_type, post):
        vals = super()._extra_partner_vals(user_type, post)
        if user_type != "verificador":
            return vals

        def _txt(key):
            return " ".join((post.get(key) or "").split()) or False

        razon = _txt("ver_razon_social")
        representante = _txt("ver_representante")
        telefono = _txt("ver_telefono")

        if not razon:
            raise ValidationError(_("La razón social del verificador es obligatoria."))
        if not representante:
            raise ValidationError(_("El responsable técnico es obligatorio."))
        if not telefono:
            raise ValidationError(_("El teléfono del verificador es obligatorio."))

        # Un verificador sin acreditación no puede inspeccionar nada, así que se
        # exige el documento ya en el registro y no después.
        if not self._has_accreditation_upload(post):
            raise ValidationError(_(
                "Debes adjuntar la acreditación que te autoriza a verificar "
                "(certificado con rol Verificador)."))

        vals.update({
            "ver_razon_social": razon,
            "ver_representante": representante,
            "ver_telefono": telefono,
            "ver_ubicacion": _txt("ver_ubicacion"),
            "ver_cobertura": _txt("ver_cobertura"),
            "ver_registro_num": _txt("ver_registro_num"),
        })
        return vals

    def _has_accreditation_upload(self, post):
        """True si en el formulario viene al menos un certificado de rol
        verificador con su archivo adjunto."""
        Cert = request.env["shrimp.certificate"].sudo()
        files = request.httprequest.files
        for key in post:
            m = re.match(r"^cert_line_(\d+)_id$", key)
            if not m:
                continue
            idx = m.group(1)
            if not files.get("cert_line_%s_file" % idx):
                continue
            try:
                cert = Cert.browse(int(post.get(key)))
            except (TypeError, ValueError):
                continue
            if cert.exists() and cert.role == "verificador":
                return True
        return False

    @http.route("/registro/certificados", type="json", auth="public", website=True, csrf=False)
    def certificados_por_rol(self, role=None):
        domain = [("active", "=", True)]
        if role in ("semillero", "laboratorio", "camaronera", "verificador"):
            domain += [("role", "in", [role, "all"])]
        certs = request.env["shrimp.certificate"].sudo().search(
            domain, order="sequence, name")
        return [{"id": c.id, "name": c.name, "issuer": c.issuer} for c in certs]
