import base64
import re
from urllib.parse import quote

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError, UserError
from werkzeug.exceptions import NotFound, Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import (
    IMAGE_MIMETYPES, ShrimpRegistryController, binary_headers, client_ip,
    flash_message, read_upload, resolve_catalog_certificate, throttle)
from odoo.addons.shrimp_marketplace.controllers.marketplace import _por_token
from odoo.addons.shrimp_marketplace.controllers.marketplace import ShrimpWebsiteHome
from odoo.addons.shrimp_marketplace.controllers.transaction_portal import (
    ShrimpTransactionPortalController)
from odoo.addons.shrimp_marketplace.controllers.utils import (
    current_partner, is_platform, to_int, to_number)
from odoo.addons.shrimp_marketplace.controllers.filter_drawer import (
    fd_context, fd_json_count, fd_tags)


def _es_sitio_verificadores():
    """True si la petición entra por la plataforma de verificadores (CamaronMarket Verificadores)."""
    return is_platform("verifier")


def _url_otro_sitio(destino, ruta):
    """URL absoluta de `ruta` en el sitio `destino`, o la ruta tal cual si ese
    sitio no tiene dominio configurado."""
    dominio = (destino.domain or "").strip().rstrip("/") if destino else ""
    return (dominio + ruta) if dominio else ruta


class ShrimpVerifierHome(ShrimpWebsiteHome):
    """En la plataforma de verificadores la portada es la suya, no la del
    marketplace."""

    @http.route()
    def index(self, **kw):
        if _es_sitio_verificadores():
            return request.render("shrimp_verification.verifier_landing", {})
        return super().index(**kw)


class ShrimpVerificationPortal(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _partner(self):
        return current_partner()

    def _empresa(self):
        """Empresa verificadora del usuario actual (sea admin o técnico)."""
        return self._partner().shrimp_verifier_company()

    def _is_verifier(self):
        return bool(self._empresa())

    def _es_admin_empresa(self):
        return self._partner().shrimp_is_verifier_admin()

    def _dominio_bandeja(self):
        """El admin ve todo lo de su empresa; el técnico, solo lo suyo."""
        yo = self._partner()
        if self._es_admin_empresa():
            return [("verifier_partner_id", "=", self._empresa().id)]
        return [("technician_partner_id", "=", yo.id)]

    def _my_verification(self, ref, editable=False):
        """Verificación accesible para el usuario: el admin de la empresa a la
        que se asignó, o el técnico que la tiene asignada."""
        rec = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not rec:
            raise NotFound()
        yo = self._partner()
        permitido = (
            (self._es_admin_empresa() and rec.verifier_partner_id == self._empresa())
            or rec.technician_partner_id == yo
        )
        if not permitido:
            raise Forbidden()
        if editable and rec.is_final:
            raise Forbidden()
        return rec

    def _to_float(self, value, default=0.0):
        return to_number(value, default)

    # ==================================================================
    # BANDEJA DEL VERIFICADOR
    # ==================================================================
    def _redirigir_a_plataforma_verificadores(self, ruta):
        """Si se entra a una ruta de verificación por el sitio del marketplace,
        se manda al sitio de verificadores (si existe y tiene dominio)."""
        if _es_sitio_verificadores():
            return None
        destino = request.env["website"].sudo()._shrimp_verifier_site()
        if not destino or not destino.domain:
            return None
        return request.redirect(_url_otro_sitio(destino, ruta), local=False)

    # Atajos de estado que no son un estado del modelo.
    _INBOX_GROUPS = [("open", "Abiertas"), ("pending", "Por iniciar")]

    def _inbox_search(self, kw):
        """(verificaciones, filtros, todas) de la bandeja. Parámetros de
        siempre (state, q) más tech (código del técnico, solo el admin)."""
        domain_base = self._dominio_bandeja()
        domain = list(domain_base)
        estados = dict(request.env["shrimp.verification"]._fields["state"].selection)
        f = {
            "q": (kw.get("q") or "").strip(),
            "state": (kw.get("state") or "").strip(),
            "tech": (kw.get("tech") or "").strip(),
        }
        if f["state"] not in estados and f["state"] not in dict(self._INBOX_GROUPS):
            f["state"] = ""
        if f["state"] == "open":
            domain.append(("state", "in", ["assigned", "in_field", "done"]))
        elif f["state"] == "pending":
            # "Por iniciar": recibidas (aún sin técnico) + asignadas
            domain.append(("state", "in", ["received", "assigned"]))
        elif f["state"]:
            domain.append(("state", "=", f["state"]))
        if f["q"]:
            domain += ["|", "|",
                       ("name", "ilike", f["q"]),
                       ("batch_code", "ilike", f["q"]),
                       ("product_id.name", "ilike", f["q"])]
        # Técnico: solo el admin reparte trabajo; al técnico su bandeja ya es
        # solo suya y el parámetro se ignora.
        if f["tech"] and self._es_admin_empresa():
            tecnico = self._empresa().field_tech_ids.filtered(lambda t: t.uuid_ref == f["tech"])
            domain.append(("technician_partner_id", "in", tecnico.ids or [0]))
        else:
            f["tech"] = ""
        V = request.env["shrimp.verification"].sudo()
        # Se ordena por la CITA primero. La pregunta del técnico al abrir la
        # bandeja es "¿cuál me toca antes?", y la respuesta es la hora a la que
        # llega cada camión, no el estado ni la fecha en que le asignaron el
        # trabajo. Las órdenes cuyo vendedor todavía no declaró hora caen al
        # final (PostgreSQL pone los nulos al final en un ASC) y ahí es donde
        # tienen que estar: son las que hay que reclamar, no las que hay que
        # atender ahora.
        verifications = V.search(domain, order="dispatch_eta asc, state asc, assigned_date asc")
        return verifications, f, V.search(domain_base)

    @http.route("/verifier/inbox", type="http", auth="user", website=True)
    def verifier_inbox(self, **kw):
        salto = self._redirigir_a_plataforma_verificadores("/verifier/inbox")
        if salto:
            return salto
        if not self._is_verifier():
            raise Forbidden()

        verifications, f, todas = self._inbox_search(kw)
        state = f["state"]

        # Los contadores son GLOBALES (todas las órdenes del verificador),
        # no dependen del filtro activo: si no, al filtrar por un estado los
        # demás caían a 0.
        counters = {
            "pending": len(todas.filtered(lambda v: v.state in ("received", "assigned"))),
            "in_field": len(todas.filtered(lambda v: v.state == "in_field")),
            "done": len(todas.filtered(lambda v: v.state == "done")),
            "total": len(todas),
        }

        state_options = request.env["shrimp.verification"]._fields["state"].selection
        es_admin = self._es_admin_empresa()
        tecnicos = self._empresa().field_tech_ids.filtered("active")
        # Cajón: los estados que hay en la bandeja (más los atajos), y
        # «Técnico» solo para el admin con más de un técnico.
        presentes = set(todas.mapped("state"))
        estado_opts = [g for g in self._INBOX_GROUPS] + [
            (v, l) for v, l in state_options if v in presentes or v == state]
        tech_opts = [(t.uuid_ref, t.name) for t in tecnicos] if es_admin else []
        show = {"state": len(presentes) > 1 or bool(state), "tech": len(tech_opts) > 1}
        etiquetas = dict(estado_opts)
        nombres_tecnico = dict(tech_opts)

        def label(group, vals):
            key, val = group[0], vals.get(group[0])
            if not val:
                return None
            if key == "state":
                return "Estado: %s" % etiquetas.get(val, val)
            if key == "tech":
                return "Técnico: %s" % nombres_tecnico.get(val, val)
            return None

        url = "/verifier/inbox"
        tags, clear_url = fd_tags(url, f, [("state",), ("tech",)], label,
                                  keep={"q": f["q"]}, anchor="#listado")
        fd = fd_context(
            url, len(verifications), tags, clear_url,
            search={"name": "q", "value": f["q"], "label": "Buscar verificaciones",
                    "placeholder": "Buscar por referencia, lote o producto…"},
            toolbar_label="Buscar y filtrar verificaciones",
            hidden=[("state", state), ("tech", f["tech"])],
            count_url="/verifier/inbox/count",
            noun=("verificación", "verificaciones"),
            drawer_hidden=[("q", f["q"])],
            keep=["q"],
            has_drawer=any(show.values()),
        )

        return request.render("shrimp_verification.verifier_inbox", {
            "page_name": "verifier_inbox",
            "verifications": verifications,
            "counters": counters,
            "es_admin": es_admin,
            "tecnicos": tecnicos,
            "filter_state": state,
            "q": f["q"],
            "state_options": state_options,
            "filters": f,
            "fd": fd,
            "fd_show": show,
            "estado_opts": estado_opts,
            "tech_opts": tech_opts,
        })

    @http.route("/verifier/inbox/count", type="http", auth="user", website=True,
                methods=["GET"], sitemap=False)
    def verifier_inbox_count(self, **kw):
        """«Ver N verificaciones» del cajón: mismo dominio que la bandeja."""
        if not self._is_verifier():
            raise Forbidden()
        verifications, _f, _todas = self._inbox_search(kw)
        return fd_json_count(len(verifications))

    @http.route("/verifier/profile", type="http", auth="user", website=True)
    def verifier_profile(self, **kw):
        salto = self._redirigir_a_plataforma_verificadores("/verifier/profile")
        if salto:
            return salto
        if not self._is_verifier():
            raise Forbidden()
        return request.render("shrimp_verification.verifier_profile", {
            "page_name": "verifier_profile",
            # sudo: los datos bancarios son solo-internos por RPC; la
            # plantilla los muestra completos al admin y enmascarados al técnico.
            "empresa": self._empresa().sudo(),
            "es_admin": self._es_admin_empresa(),
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/verifier/profile/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_profile_save(self, **post):
        if not self._es_admin_empresa():
            raise Forbidden()
        empresa = self._empresa()

        def T(k):
            return (post.get(k) or "").strip() or False

        def I(k):
            try:
                return int(float((post.get(k) or "").replace(",", ".")))
            except (TypeError, ValueError):
                return 0

        def F(k):
            try:
                return float((post.get(k) or "").replace(",", "."))
            except (TypeError, ValueError):
                return 0.0

        vals = {
            # Empresa
            "shrimp_razon_social": T("ver_razon_social"),
            "shrimp_representante": T("ver_representante"),
            "shrimp_telefono": T("ver_telefono"),
            "shrimp_ubicacion": T("ver_ubicacion"),
            "ver_cobertura": T("ver_cobertura"),
            "ver_registro_num": T("ver_registro_num"),
            # Cuenta bancaria
            "ver_bank_name": T("ver_bank_name"),
            "ver_bank_account_type": post.get("ver_bank_account_type") or False,
            "ver_bank_account_number": T("ver_bank_account_number"),
            "ver_bank_holder": T("ver_bank_holder"),
            "ver_bank_holder_id": T("ver_bank_holder_id"),
            # Contacto operativo
            "ver_email_avisos": T("ver_email_avisos"),
            "ver_whatsapp": T("ver_whatsapp"),
            "ver_horario": T("ver_horario"),
            # Cobertura y logística
            "ver_provincias": T("ver_provincias"),
            "ver_radio_km": I("ver_radio_km"),
            "ver_tiempo_respuesta": T("ver_tiempo_respuesta"),
            "ver_equipo_propio": bool(post.get("ver_equipo_propio")),
            # Capacidades técnicas
            "ver_analisis_tipos": T("ver_analisis_tipos"),
            "ver_equipos": T("ver_equipos"),
            "shrimp_capacity_value": I("ver_capacidad_lotes_dia"),
            "shrimp_capacity_unit": "lots_day",
            # Acreditación
            "ver_entidad_acredita": T("ver_entidad_acredita"),
            "ver_acred_vigencia": post.get("ver_acred_vigencia") or False,
            # Facturación
            "ver_ruc": T("ver_ruc"),
            "ver_razon_fiscal": T("ver_razon_fiscal"),
            "ver_dir_fiscal": T("ver_dir_fiscal"),
            # Tarifa
            "ver_fee_base": F("ver_fee_base"),
            "ver_fee_por_lb": F("ver_fee_por_lb"),
        }

        # Certificado de acreditación (PDF): solo se actualiza si suben uno
        # nuevo, y solo si de verdad es un PDF de hasta 5 MB.
        archivo = request.httprequest.files.get("ver_acred_cert")
        if archivo and archivo.filename:
            try:
                contenido, _mime, nombre = read_upload(archivo, allowed={"application/pdf"})
            except ValidationError as e:
                return request.redirect("/verifier/profile?error=1&message="
                                        + flash_message(e.args[0] if e.args else ""))
            if contenido:
                vals["ver_acred_cert"] = base64.b64encode(contenido)
                vals["ver_acred_cert_name"] = nombre

        empresa.sudo().write(vals)
        return request.redirect("/verifier/profile?saved=1")

    @http.route("/verifier/profile/accreditation", type="http", auth="user",
                website=True, sitemap=False)
    def verifier_profile_accreditation(self, **kw):
        """El PDF de acreditación de MI empresa (antes se enlazaba con
        /web/content?model=res.partner&id=<id>)."""
        if not self._is_verifier():
            raise Forbidden()
        empresa = self._empresa().sudo()
        if not empresa.ver_acred_cert:
            raise NotFound()
        contenido = base64.b64decode(empresa.ver_acred_cert)
        return request.make_response(contenido, headers=binary_headers(
            contenido, "application/pdf", empresa.ver_acred_cert_name or "acreditacion.pdf",
            download=bool(kw.get("download"))))

    @http.route("/verifier/reports", type="http", auth="user", website=True)
    def verifier_reports(self, **kw):
        salto = self._redirigir_a_plataforma_verificadores("/verifier/reports")
        if salto:
            return salto
        if not self._is_verifier():
            raise Forbidden()
        empresa = self._empresa()
        V = request.env["shrimp.verification"].sudo()
        recs = V.search([("verifier_partner_id", "=", empresa.id)])

        def n(state):
            return len(recs.filtered(lambda r: r.state == state))

        finalizadas = recs.filtered(lambda r: r.state in ("approved", "approved_obs", "rejected"))
        aprobadas = recs.filtered(lambda r: r.state in ("approved", "approved_obs"))
        rechazadas = recs.filtered(lambda r: r.state == "rejected")
        con_yield = recs.filtered(lambda r: r.total_processed_lb)
        avg_yield = (sum(con_yield.mapped("yield_pct")) / len(con_yield)) if con_yield else 0.0
        honorarios = sum(recs.mapped("verifier_amount"))

        stats = {
            "total": len(recs),
            "abiertas": n("assigned") + n("in_field") + n("done") + n("received"),
            "aprobadas": len(aprobadas),
            "rechazadas": len(rechazadas),
            "finalizadas": len(finalizadas),
            "tasa_aprob": (100.0 * len(aprobadas) / len(finalizadas)) if finalizadas else 0.0,
            "avg_yield": avg_yield,
            "honorarios": honorarios,
        }
        return request.render("shrimp_verification.verifier_reports", {
            "page_name": "verifier_reports",
            "empresa": empresa,
            "stats": stats,
            "ultimas": recs.sorted(lambda r: r.create_date or r.id, reverse=True)[:10],
        })

    @http.route("/verifier/verifications/<ref>", type="http", auth="user", website=True)
    def verifier_detail(self, ref, **kw):
        salto = self._redirigir_a_plataforma_verificadores(
            "/verifier/verifications/%s" % ref)
        if salto:
            return salto
        rec = self._my_verification(ref)
        # Paso 1: mientras la orden no tenga técnico asignado (estado
        # 'received'), la pantalla es la de asignación. La verificación en sí
        # (paso 2) solo aparece una vez asignada.
        if rec.state == "received":
            return self._render_verifier_assign(rec, kw)
        return request.render("shrimp_verification.verifier_detail", {
            "page_name": "verifier_detail",
            "v": rec,
            "es_admin": self._es_admin_empresa(),
            "tecnicos": self._empresa().field_tech_ids.filtered("active"),
            "taste_criteria": request.env["shrimp.taste.criterion"].sudo().search(
                [("active", "=", True)]),
            "warnings": rec.report_warnings(),
            # OJO: el parte NO se pasa por el qcontext. Odoo aplica formato de
            # cadena al contexto al renderizar la pagina de website, y el texto
            # contiene '%' (67,22%), lo que revienta con "incomplete format".
            # La plantilla llama directamente a v.whatsapp_report().
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/verifier/verifications/<ref>/detail", type="http", auth="user", website=True)
    def verifier_full_detail(self, ref, **kw):
        """Vista de solo lectura con TODA la información de la verificación."""
        salto = self._redirigir_a_plataforma_verificadores(
            "/verifier/verifications/%s/detail" % ref)
        if salto:
            return salto
        rec = self._my_verification(ref)
        return request.render("shrimp_verification.verifier_full_detail", {
            "page_name": "verifier_full_detail",
            "v": rec,
        })

    @http.route("/verifier/verifications/<ref>/assign-technician", type="http",
                auth="user", website=True)
    def verifier_assign_page(self, ref, **kw):
        """Paso 1 en su propia pantalla: asignar (o reasignar) el técnico."""
        salto = self._redirigir_a_plataforma_verificadores(
            "/verifier/verifications/%s/assign-technician" % ref)
        if salto:
            return salto
        rec = self._my_verification(ref)
        return self._render_verifier_assign(rec, kw)

    def _render_verifier_assign(self, rec, kw):
        return request.render("shrimp_verification.verifier_assign", {
            "page_name": "verifier_assign",
            "v": rec,
            "es_admin": self._es_admin_empresa(),
            "tecnicos": self._empresa().field_tech_ids.filtered("active"),
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/verifier/verifications/<ref>/start", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_start(self, ref, **post):
        rec = self._my_verification(ref, editable=True)
        # Puede iniciar el técnico asignado o el administrador de la empresa.
        if not (rec.technician_partner_id == self._partner() or self._es_admin_empresa()):
            return request.redirect(
                "/verifier/verifications/%s?error=1&message=%s" % (
                    rec.uuid_ref,
                    flash_message("Solo el técnico asignado o el administrador pueden iniciar la verificación.")))
        try:
            rec.action_start_field()
        except UserError as e:
            return request.redirect(f"/verifier/verifications/{rec.uuid_ref}?error=1&message="
                                    + flash_message(e.args[0] if e.args else ""))
        return request.redirect(f"/verifier/verifications/{rec.uuid_ref}?saved=started")

    @http.route("/verifier/verifications/<ref>/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_save(self, ref, **post):
        """Guarda el informe de campo. Se puede guardar por partes: una
        verificación no se completa de una sentada."""
        rec = self._my_verification(ref, editable=True)
        vals = self._report_vals(post)

        try:
            with request.env.cr.savepoint():
                rec.write(vals)
                self._save_lines(rec, post)
                self._save_counts(rec, post)
                self._save_photos(rec)
        except (ValidationError, UserError) as e:
            msg = flash_message(e.args[0] if e.args else "")
            return request.redirect(
                f"/verifier/verifications/{rec.uuid_ref}?error=1&message={msg}")

        return request.redirect(f"/verifier/verifications/{rec.uuid_ref}?saved=1")

    def _report_vals(self, post):
        """Campos del informe tal como llegan del formulario. Lo usan el
        informe del técnico y el informe DECLARADO por las partes: es el mismo
        formulario (shrimp_verification.verification_report_fields)."""
        F = self._to_float

        # Criterios de cata marcados como correctos (checkboxes múltiples).
        crit_ids = [int(x) for x in request.httprequest.form.getlist("taste_criteria")
                    if str(x).isdigit()]

        return {
            # El lote y la piscina vienen definidos por el vendedor y son de
            # solo lectura: no se toman del POST para que no puedan alterarse.
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
            "taste_criteria_ok_ids": [(6, 0, crit_ids)],
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
        vistas = set()
        for idx in sorted(indices):
            size = (post.get(f"line_{idx}_size") or "").strip()
            weight = self._to_float(post.get(f"line_{idx}_weight"))
            quality = post.get(f"line_{idx}_class") or "a"
            if not size or weight <= 0:
                continue
            # Una sola fila por clase (A, B, C).
            if quality in vistas:
                raise ValidationError(_(
                    "Solo puede haber una fila por clase; la clase '%s' está repetida."
                ) % quality.upper())
            vistas.add(quality)
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
        """Fotos de evidencia: solo imágenes reales (magic bytes), máx. 5 MB
        cada una y 30 por guardado. El tipo NO se toma del navegador."""
        files = (request.httprequest.files.getlist("photo_files") or [])[:30]
        att_ids = []
        for f in files:
            content, mimetype, nombre = read_upload(f, allowed=IMAGE_MIMETYPES)
            if not content:
                continue
            att = request.env["ir.attachment"].sudo().create({
                "name": nombre or "foto",
                "type": "binary",
                "datas": base64.b64encode(content),
                "mimetype": mimetype,
                "res_model": "shrimp.verification",
                "res_id": rec.id,
                "public": False,
            })
            att.generate_access_token()
            att_ids.append(att.id)
        if att_ids:
            rec.write({"photo_ids": [(4, aid) for aid in att_ids]})

    @http.route("/verifier/verifications/<ref>/photos/<token>", type="http",
                auth="user", website=True, sitemap=False)
    def verifier_photo(self, ref, token, **kw):
        """Sirve una foto de evidencia desde el sitio del verificador (mismo
        dominio y misma autorización que la pantalla de la verificación).
        La foto se identifica por el token del adjunto, no por su id."""
        rec = self._my_verification(ref)
        att = _por_token(rec.photo_ids, token)
        if not att or not att.datas:
            raise NotFound()
        content = base64.b64decode(att.datas)
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype or "image/jpeg", cache="private, max-age=3600"))

    @http.route("/verifier/verifications/<ref>/photos/<token>/delete",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def verifier_photo_delete(self, ref, token, **post):
        """Elimina una foto de evidencia ya adjunta (inmediato, sin recargar)."""
        rec = self._my_verification(ref, editable=True)
        att = _por_token(rec.photo_ids, token)
        if att:
            rec.write({"photo_ids": [(3, att.id)]})
            att.sudo().unlink()
        return request.make_response("ok")

    # ------------------------------------------------------------------
    # Veredicto
    # ------------------------------------------------------------------
    @http.route("/verifier/verifications/<ref>/verdict", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_verdict(self, ref, **post):
        rec = self._my_verification(ref, editable=True)
        # Firma quien fue a campo, o el admin de su empresa si el técnico no
        # está disponible.
        if not rec._puede_dictaminar(self._partner()):
            raise Forbidden()
        verdict = post.get("verdict")
        notes = (post.get("verdict_notes") or "").strip()

        actions = {
            "approve": "approved",
            "approve_obs": "approved_obs",
            "reject": "rejected",
        }
        if verdict not in actions:
            return request.redirect(f"/verifier/verifications/{rec.uuid_ref}?error=1&message=invalid_verdict")

        try:
            # Los tres pasos van dentro de un savepoint porque son uno solo: o
            # la verificación queda dictaminada, o se queda como estaba.
            #
            # Sin esto, un dictamen que _close rechaza —por ejemplo un "rechazar"
            # sin escribir el motivo— dejaba el registro en "done" de todas
            # formas, porque action_mark_done ya había escrito y el except no
            # revertía nada. El técnico veía un estado que no había alcanzado.
            #
            # Se mantiene el orden original a propósito: action_mark_done exige
            # el informe completo, y esa comprobación tiene que seguir corriendo
            # antes de rechazar.
            with request.env.cr.savepoint():
                if rec.state == "in_field":
                    rec.action_mark_done()
                rec._close(actions[verdict], notes=notes)
                if verdict == "reject":
                    rec.transaction_id.action_cancel_for_verification()
        except UserError as e:
            return request.redirect(
                f"/verifier/verifications/{rec.uuid_ref}?error=1&message="
                + flash_message(e.args[0] if e.args else ""))

        return request.redirect("/verifier/inbox?saved=verdict")

    # ==================================================================
    # LADO DEL COMPRADOR
    # ==================================================================
    @http.route("/marketplace/buy/<product_ref>/verify", type="http", auth="user",
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

        # Modo de verificación: lo elige el comprador. Sin «no aplica». Un
        # POST antiguo (solo con verificador) se entiende como «plataforma».
        mode = post.get("verification_mode") or ("platform" if post.get("verifier_ref") else "")
        if mode not in ("platform", "declared"):
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=mode")

        verifier, fee, declared_vals = None, 0.0, None
        if mode == "platform":
            verifier = request.env["res.partner"].sudo().resolve_ref(post.get("verifier_ref") or "")
            if not verifier:
                return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=verifier")
            fee = request.env["shrimp.verification.fee"].sudo().compute(qty)
        else:
            fuente = post.get("declared_source") if post.get("declared_source") in ("external", "self") \
                else "external"
            declared_vals = {
                "declared_source": fuente,
                "external_verifier_name": (post.get("external_verifier_name") or "").strip()[:200] or False,
                "external_verifier_vat": (post.get("external_verifier_vat") or "").strip()[:20] or False,
            }

        try:
            result = product.start_verified_purchase(
                buyer, qty, verifier, fee=fee, mode=mode, declared_vals=declared_vals)
        except ValidationError as e:
            msg = flash_message(e.args[0] if e.args else "")
            return request.redirect(
                f"/marketplace/buy/{product.uuid_ref}?error=validation&message={msg}")

        if mode == "declared":
            return request.redirect(
                "/marketplace/verifications/%s/declare" % result["verification"].uuid_ref)
        return request.redirect(
            f"/marketplace/verifications/pending/{result['transaction'].uuid_ref}")

    @http.route("/marketplace/verifications/pending/<tx_ref>", type="http", auth="user", website=True)
    def purchase_pending(self, tx_ref, **kw):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()
        partner = self._partner()
        if partner.id not in (tx.buyer_partner_id.id, tx.seller_partner_id.id):
            raise Forbidden()
        return request.render("shrimp_verification.purchase_pending", {
            "tx": tx,
            "v": tx.verification_ids[:1],
        })

    @http.route("/marketplace/purchases/<tx_ref>/complete", type="http", auth="user",
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
            msg = flash_message(e.args[0] if e.args else "")
            return request.redirect(f"/marketplace/purchases?error=complete&message={msg}")
        return request.redirect(f"/marketplace/thanks/{tx.uuid_ref}?verified=1")

    # ==================================================================
    # Aceptación del informe por comprador y vendedor
    # ==================================================================
    def _verificacion_de_parte(self, ref):
        """Devuelve (verificación, postura) validando que quien entra es parte.

        Un tercero no puede ni mirar el informe, y ninguna de las dos partes
        puede decidir por la otra: cada una solo maneja su propia postura.
        """
        v = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not v:
            raise NotFound()
        partner = self._partner()
        if partner == v.buyer_partner_id:
            rol = "buyer"
        elif partner == v.seller_partner_id:
            rol = "seller"
        else:
            raise Forbidden()
        postura = v.acceptance_ids.filtered(lambda a: a.role == rol)[:1]
        return v, postura

    @http.route("/marketplace/verifications/<ref>/acceptance", type="http", auth="user",
                website=True)
    def acceptance_panel(self, ref, **kw):
        v, postura = self._verificacion_de_parte(ref)
        cumple, motivos = v.cumple_lo_publicado()
        otra = (v.acceptance_ids - postura)[:1]
        return request.render("shrimp_verification.verification_acceptance", {
            "v": v,
            "tx": v.transaction_id,
            "postura": postura,
            "otra": otra,
            "cumple": cumple,
            "motivos": motivos,
            "contraoferta": v.acceptance_ids.filtered(lambda a: a.decision == "counter")[:1],
            "error": kw.get("error"),
            # Historial de decisiones y «deshacer mi decisión» (firmas de dos partes).
            "historial": request.env["shrimp.signoff.event"].history_for(v),
            "undo_info": postura.signoff_undo_info(actor=self._partner()) if postura else {},
            "undo_url": "/marketplace/verifications/%s/undo" % v.uuid_ref,
        })

    def _volver_al_panel(self, v, error=None):
        destino = "/marketplace/verifications/%s/acceptance" % v.uuid_ref
        if error:
            destino += "?error=%s" % flash_message(error)
        return request.redirect(destino)

    @http.route("/marketplace/verifications/<ref>/accept", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def acceptance_accept(self, ref, **post):
        v, postura = self._verificacion_de_parte(ref)
        if not postura:
            raise NotFound()
        try:
            postura.sudo().action_accept(reason=post.get("reason") or None,
                                         actor=self._partner())
        except (UserError, ValidationError, AccessError) as e:
            return self._volver_al_panel(v, e.args[0] if e.args else "")
        return self._volver_al_panel(v)

    @http.route("/marketplace/verifications/<ref>/reject", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def acceptance_reject(self, ref, **post):
        v, postura = self._verificacion_de_parte(ref)
        if not postura:
            raise NotFound()
        try:
            postura.sudo().action_reject(reason=(post.get("reason") or "").strip() or None,
                                         actor=self._partner())
        except (UserError, ValidationError, AccessError) as e:
            return self._volver_al_panel(v, e.args[0] if e.args else "")
        return self._volver_al_panel(v)

    @http.route("/marketplace/verifications/<ref>/counter-offer", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def acceptance_counter(self, ref, **post):
        v, postura = self._verificacion_de_parte(ref)
        if not postura:
            raise NotFound()
        try:
            precio = float((post.get("precio") or "0").replace(",", "."))
        except ValueError:
            return self._volver_al_panel(v, _("El precio propuesto no es un número válido."))
        try:
            postura.sudo().action_counter(precio, reason=(post.get("reason") or "").strip() or None,
                                          actor=self._partner())
        except (UserError, ValidationError, AccessError) as e:
            return self._volver_al_panel(v, e.args[0] if e.args else "")
        return self._volver_al_panel(v)

    @http.route("/marketplace/verifications/<ref>/undo", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def acceptance_undo(self, ref, **post):
        """La parte deshace SU decisión mientras la ronda siga abierta."""
        v, postura = self._verificacion_de_parte(ref)
        if not postura:
            raise NotFound()
        try:
            with request.env.cr.savepoint():
                postura.sudo().action_signoff_undo(
                    reason=(post.get("motivo") or "").strip() or None, actor=self._partner())
        except (UserError, ValidationError, AccessError) as e:
            return self._volver_al_panel(v, e.args[0] if e.args else "")
        return self._volver_al_panel(v)

    # ==================================================================
    # Verificación DECLARADA por las partes
    # ==================================================================
    # Cualquiera de las dos partes carga el borrador (mismo formulario que el
    # informe del técnico); la que lo presenta queda como declarante y la otra
    # lo confirma en el panel de aceptación de siempre.
    def _declarada_de_parte(self, ref):
        v = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not v:
            raise NotFound()
        if v.verification_mode != "declared":
            raise NotFound()
        partner = self._partner()
        if partner not in (v.buyer_partner_id, v.seller_partner_id):
            raise Forbidden()
        return v, partner

    def _volver_a_declarar(self, v, error=None, saved=None):
        destino = "/marketplace/verifications/%s/declare" % v.uuid_ref
        if error:
            destino += "?error=1&message=%s" % flash_message(error)
        elif saved:
            destino += "?saved=%s" % saved
        return request.redirect(destino)

    @http.route("/marketplace/verifications/<ref>/declare", type="http", auth="user",
                website=True)
    def declared_form(self, ref, **kw):
        v, partner = self._declarada_de_parte(ref)
        if v.state != "declared_draft" and v.acceptance_ids:
            # Ya presentada: la pantalla es la de aceptación (muestra el informe).
            return request.redirect("/marketplace/verifications/%s/acceptance" % v.uuid_ref)
        return request.render("shrimp_verification.declared_report", {
            "page_name": "declared_report",
            "v": v,
            "tx": v.transaction_id,
            "role": v._declared_role_of(partner),
            "puede_editar": v.state == "declared_draft",
            "taste_criteria": request.env["shrimp.taste.criterion"].sudo().search(
                [("active", "=", True)]),
            "warnings": v.report_warnings() if v.state == "declared_draft" else [],
            "missing": v._missing_declared_fields() if v.state == "declared_draft" else [],
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    @http.route("/marketplace/verifications/<ref>/declare/save", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def declared_save(self, ref, **post):
        """Guarda el borrador y, si se pulsó «Presentar», lo presenta: las dos
        cosas en un savepoint (o queda presentado con lo guardado, o nada)."""
        v, partner = self._declarada_de_parte(ref)
        vals = self._report_vals(post)
        fuente = post.get("declared_source")
        vals.update({
            "declared_source": fuente if fuente in ("external", "self") else "external",
            "external_verifier_name": (post.get("external_verifier_name") or "").strip()[:200] or False,
            "external_verifier_vat": (post.get("external_verifier_vat") or "").strip()[:20] or False,
        })
        presentar = post.get("accion") == "presentar"
        try:
            with request.env.cr.savepoint():
                pdf = request.httprequest.files.get("declared_report_file")
                contenido, _mime, nombre = read_upload(pdf, allowed={"application/pdf"})
                if contenido:
                    vals["declared_report_file"] = base64.b64encode(contenido)
                    vals["declared_report_filename"] = nombre
                v.action_declared_save(vals, partner)
                self._save_lines(v, post)
                self._save_counts(v, post)
                self._save_photos(v)
                if presentar:
                    v.action_declared_submit(partner, notes=post.get("verdict_notes"))
        except (ValidationError, UserError, AccessError) as e:
            return self._volver_a_declarar(v, error=e.args[0] if e.args else "")
        if presentar:
            return request.redirect("/marketplace/verifications/%s/acceptance" % v.uuid_ref)
        return self._volver_a_declarar(v, saved="1")

    @http.route("/marketplace/verifications/<ref>/declare/cancel", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def declared_cancel(self, ref, **post):
        """El COMPRADOR desiste de la compra mientras el informe declarado
        sigue en preparación: se libera la reserva. El vendedor no puede
        cancelarle la compra al comprador."""
        v, partner = self._declarada_de_parte(ref)
        if partner != v.buyer_partner_id:
            raise Forbidden()
        if v.state != "declared_draft":
            return self._volver_a_declarar(v, error=_(
                "El informe ya se presentó: ahora se resuelve en la aceptación de las partes."))
        v.action_cancel()
        return request.redirect("/marketplace/purchases")

    @http.route("/marketplace/verifications/<ref>/declare/report", type="http", auth="user",
                website=True, sitemap=False)
    def declared_report_pdf(self, ref, **kw):
        """El PDF de la verificadora externa: lo ven las partes y el personal interno."""
        v = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not v or v.verification_mode != "declared" or not v.declared_report_file:
            raise NotFound()
        partner = self._partner()
        if not (request.env.user.has_group("base.group_user")
                or partner in (v.buyer_partner_id, v.seller_partner_id)):
            raise Forbidden()
        contenido = base64.b64decode(v.declared_report_file)
        return request.make_response(contenido, headers=binary_headers(
            contenido, "application/pdf", cache="private, max-age=600"))

    @http.route("/marketplace/verifications/<ref>/declare/photos/<token>/delete",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def declared_photo_delete(self, ref, token, **post):
        v, partner = self._declarada_de_parte(ref)
        if v.state != "declared_draft":
            raise Forbidden()
        att = _por_token(v.photo_ids, token)
        if att:
            v.write({"photo_ids": [(3, att.id)]})
            att.sudo().unlink()
        return request.make_response("ok")

    # ==================================================================
    # Técnicos de campo (solo el admin de la empresa)
    # ==================================================================
    @http.route("/verifier/technicians", type="http", auth="user", website=True)
    def my_technicians(self, **kw):
        if not self._es_admin_empresa():
            raise Forbidden()
        empresa = self._empresa()
        tecnicos = empresa.field_tech_ids
        # Carga de trabajo de cada uno, para repartir con criterio.
        Verif = request.env["shrimp.verification"].sudo()
        Review = request.env["shrimp.verifier.review"].sudo()
        carga = {}
        for t in tecnicos:
            reseñas = Review.search([("technician_partner_id", "=", t.id)],
                                    order="create_date desc")
            carga[t.id] = {
                "abiertas": Verif.search_count([
                    ("technician_partner_id", "=", t.id),
                    ("state", "in", ["assigned", "in_field", "done"])]),
                "cerradas": Verif.search_count([
                    ("technician_partner_id", "=", t.id),
                    ("state", "in", ["approved", "approved_obs", "rejected"])]),
                "resenas": reseñas,
                "ultimo_comentario": next(
                    (r.comment for r in reseñas if r.comment), ""),
            }
        return request.render("shrimp_verification.my_technicians", {
            "page_name": "my_technicians",
            "empresa": empresa,
            "tecnicos": tecnicos,
            "carga": carga,
            "roles": request.env["shrimp.tech.role"].sudo().search([]),
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    def _tech_role(self, post):
        """Cargo del técnico por su código (uuid_ref)."""
        Role = request.env["shrimp.tech.role"].sudo()
        return Role.resolve_ref(post.get("tech_role_ref")) or Role

    def _mi_tecnico(self, tech_ref):
        """Técnico de MI empresa, por su código. Un id numérico da 404."""
        tecnico = request.env["res.partner"].sudo().with_context(
            active_test=False).resolve_ref(tech_ref)
        if not tecnico or not tecnico.shrimp_is_field_tech \
                or tecnico.parent_id != self._empresa():
            raise NotFound()
        return tecnico

    @http.route("/verifier/technicians/add", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def technician_add(self, **post):
        if not self._es_admin_empresa():
            raise Forbidden()
        empresa = self._empresa()
        # Cada alta crea un usuario con contraseña: se limita la frecuencia por
        # empresa y por IP para que la pantalla no sirva para fabricar cuentas.
        if not (throttle("team:%s" % empresa.id, "shrimp_verification.team_rate_limit",
                         "shrimp_verification.team_rate_window", 20, 3600)
                and throttle("team-ip:%s" % client_ip(), "shrimp_verification.team_rate_limit",
                             "shrimp_verification.team_rate_window", 20, 3600)):
            return request.redirect("/verifier/technicians?error=1&message=rate_limited")

        def _txt(k):
            return " ".join((post.get(k) or "").split())

        nombre = _txt("name")
        correo = (post.get("email") or "").strip().lower()
        clave = post.get("password") or ""

        if not nombre:
            return request.redirect("/verifier/technicians?error=1&message=name_required")
        if not correo or "@" not in correo:
            return request.redirect("/verifier/technicians?error=1&message=email_invalid")
        if len(clave) < 8:
            return request.redirect(
                "/verifier/technicians?error=1&message=password_short")

        Users = request.env["res.users"].sudo()
        if Users.search_count([("login", "=", correo)]):
            return request.redirect(
                "/verifier/technicians?error=1&message=email_taken")

        role = self._tech_role(post)

        try:
            with request.env.cr.savepoint():
                tecnico = request.env["res.partner"].sudo().create({
                    "name": nombre,
                    "email": correo,
                    "phone": _txt("phone") or False,
                    "tech_role_id": role.id or False,
                    "function": role.name or "Técnico de campo",
                    "parent_id": empresa.id,
                    "shrimp_is_field_tech": True,
                })
                portal = request.env.ref("base.group_portal")
                campo = "group_ids" if "group_ids" in Users._fields else "groups_id"
                Users.create({
                    "name": nombre, "login": correo, "email": correo,
                    "partner_id": tecnico.id, campo: [(6, 0, [portal.id])],
                    "password": clave,
                })
        except Exception as e:  # noqa: BLE001
            return request.redirect(
                "/verifier/technicians?error=1&message=%s" % flash_message(str(e)[:120]))

        return request.redirect("/verifier/technicians?saved=1")

    @http.route("/verifier/technicians/<tech_ref>/toggle", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def technician_toggle(self, tech_ref, **post):
        """Da de baja o vuelve a activar a un técnico. No se borra: sus
        verificaciones pasadas deben seguir mostrando quién las hizo."""
        if not self._es_admin_empresa():
            raise Forbidden()
        tecnico = self._mi_tecnico(tech_ref)
        nuevo = not tecnico.active
        usuario = request.env["res.users"].sudo().with_context(active_test=False).search(
            [("partner_id", "=", tecnico.id)], limit=1)
        # Odoo no deja archivar un contacto con usuario activo: al dar de baja
        # se archiva primero el usuario; al reactivar, primero el contacto.
        # (El contacto viene con active_test=False para poder encontrar a los
        # dados de baja; se escribe sin ese contexto porque la comprobación de
        # Odoo buscaría también los usuarios ya archivados.)
        tecnico = tecnico.with_context(active_test=True)
        if nuevo:
            tecnico.active = True
            if usuario:
                usuario.active = True
        else:
            if usuario:
                usuario.active = False
            tecnico.active = False
        return request.redirect("/verifier/technicians?saved=estado")

    @http.route("/verifier/technicians/<tech_ref>/edit", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def technician_edit(self, tech_ref, **post):
        """Edita los datos de un técnico (nombre, contacto, cargo) y, si se
        indica, su correo de acceso y una nueva contraseña."""
        if not self._es_admin_empresa():
            raise Forbidden()
        tecnico = self._mi_tecnico(tech_ref)

        def _txt(k):
            return " ".join((post.get(k) or "").split())

        nombre = _txt("name")
        correo = (post.get("email") or "").strip().lower()
        clave = post.get("password") or ""
        if not nombre:
            return request.redirect("/verifier/technicians?error=1&message=name_required")
        if not correo or "@" not in correo:
            return request.redirect("/verifier/technicians?error=1&message=email_invalid")
        if clave and len(clave) < 8:
            return request.redirect(
                "/verifier/technicians?error=1&message=password_short")

        Users = request.env["res.users"].sudo()
        usuario = Users.search([("partner_id", "=", tecnico.id)], limit=1)
        # El correo es la clave de acceso: si cambia, no debe chocar con otra cuenta.
        otro = Users.search([("login", "=", correo), ("partner_id", "!=", tecnico.id)], limit=1)
        if otro:
            return request.redirect(
                "/verifier/technicians?error=1&message=email_taken")

        role = self._tech_role(post)
        try:
            with request.env.cr.savepoint():
                tecnico.write({
                    "name": nombre,
                    "email": correo,
                    "phone": _txt("phone") or False,
                    "tech_role_id": role.id or False,
                    "function": role.name or tecnico.function,
                })
                if usuario:
                    vals = {"login": correo, "email": correo, "name": nombre}
                    if clave:
                        vals["password"] = clave
                    usuario.write(vals)
        except Exception as e:  # noqa: BLE001
            return request.redirect(
                "/verifier/technicians?error=1&message=%s" % flash_message(str(e)[:120]))
        return request.redirect("/verifier/technicians?saved=1")

    @http.route("/verifier/verifications/<ref>/assign", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def assign_technician(self, ref, **post):
        if not self._es_admin_empresa():
            raise Forbidden()
        rec = self._my_verification(ref, editable=True)
        empresa = self._empresa()
        # Solo un técnico ACTIVO de mi empresa (o la propia empresa), por su
        # código; nunca un partner arbitrario por id. El modelo lo vuelve a
        # comprobar (_check_technician_belongs_to_company).
        token = (post.get("technician_ref") or "").strip()
        candidatos = empresa.sudo().field_tech_ids.filtered("active") | empresa.sudo()
        tecnico = candidatos.filtered(lambda p: p.uuid_ref and p.uuid_ref == token)[:1]
        if not tecnico:
            return request.redirect(
                "/verifier/verifications/%s?error=1&message=choose_tech" % rec.uuid_ref)
        try:
            with request.env.cr.savepoint():
                rec.action_assign_technician(tecnico)
        except (UserError, ValidationError) as e:
            return request.redirect("/verifier/verifications/%s?error=1&message=%s" % (
                rec.uuid_ref, flash_message(str(e.args[0] if e.args else ""))))
        return request.redirect("/verifier/verifications/%s?saved=asignada" % rec.uuid_ref)

    # ==================================================================
    # Evidencia de campo
    # ==================================================================
    @http.route("/marketplace/verifications/<ref>/photos/<token>", type="http",
                auth="user", website=True, sitemap=False)
    def verification_photo(self, ref, token, **kw):
        """Sirve una foto tomada por el verificador en campo.

        La ven las partes de la operación (comprador, vendedor y el propio
        verificador) y los usuarios internos: es la evidencia que respalda el
        veredicto.
        """
        rec = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not rec:
            raise NotFound()

        partner = self._partner()
        empresa = partner.shrimp_verifier_company() if hasattr(partner, "shrimp_verifier_company") else False
        permitido = (
            request.env.user.has_group("base.group_user")
            or partner.id in (rec.buyer_partner_id.id, rec.seller_partner_id.id, rec.verifier_partner_id.id)
            or (empresa and empresa.id == rec.verifier_partner_id.id)   # admin o técnico de la empresa
            or partner.id == rec.technician_partner_id.id
        )
        if not permitido:
            raise Forbidden()

        # Solo adjuntos de ESTA verificación, por su token (no por id).
        att = _por_token(rec.photo_ids, token)
        if not att or not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype or "image/jpeg", cache="private, max-age=3600"))

    # ==================================================================
    # Acreditación del verificador (el "ojito" del comprador)
    # ==================================================================
    @http.route("/marketplace/verifiers/<partner_ref>/accreditation", type="http",
                auth="user", website=True, sitemap=False)
    def verifier_accreditation(self, partner_ref, **kw):
        """Muestra el documento que acredita al verificador.

        Cualquier usuario autenticado del marketplace puede consultarlo: es
        justamente la información que le permite decidir en quién confiar.
        """
        verifier = request.env["res.partner"].sudo().resolve_ref(partner_ref)
        if not verifier or not verifier._shrimp_has_role("verificador"):
            raise NotFound()

        line = verifier.verifier_accreditation_line_id
        if not line or not line.file_attachment_id:
            raise NotFound()

        att = line.file_attachment_id
        if not att.datas:
            raise NotFound()

        content = base64.b64decode(att.datas)
        # Por defecto se ve en el navegador; con ?download=1 se descarga.
        return request.make_response(content, headers=binary_headers(
            content, att.mimetype, att.name or "acreditacion",
            download=bool(kw.get("download"))))

    # ==================================================================
    # Reseñas del verificador
    # ==================================================================
    @http.route("/marketplace/verifications/<ref>/rate", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def rate_verification(self, ref, **post):
        """El comprador califica el trabajo de verificación."""
        rec = request.env["shrimp.verification"].sudo().resolve_ref(ref)
        if not rec:
            raise NotFound()

        comprador = self._partner()
        if rec.buyer_partner_id != comprador:
            raise Forbidden()
        # Solo tiene sentido calificar un trabajo terminado, y solo el de una
        # verificadora de la plataforma (la declarada no tiene a quién).
        if rec.verification_mode != "platform":
            return request.redirect("/marketplace/purchases?error=verif_declarada")
        if rec.state not in ("approved", "approved_obs", "rejected"):
            return request.redirect("/marketplace/purchases?error=verif_pendiente")

        def _nota(clave):
            try:
                valor = int(post.get(clave) or 0)
            except (TypeError, ValueError):
                return 0
            return valor if 1 <= valor <= 5 else 0

        estrellas = _nota("rating")
        if not estrellas:
            return request.redirect("/marketplace/purchases?error=rating")

        Review = request.env["shrimp.verifier.review"].sudo()
        vals = {
            "rating": estrellas,
            "comment": (post.get("comment") or "").strip() or False,
            "punctuality": _nota("punctuality"),
            "thoroughness": _nota("thoroughness"),
            "communication": _nota("communication"),
        }
        existente = Review.search([("verification_id", "=", rec.id)], limit=1)
        if existente:
            existente.write(vals)
        else:
            vals.update({"verification_id": rec.id, "reviewer_partner_id": comprador.id})
            Review.create(vals)
        return request.redirect("/marketplace/purchases?saved=verif_review")

    # ==================================================================
    # Combo de verificadores (para el formulario de compra)
    # ==================================================================
    @http.route("/marketplace/verifiers", type="jsonrpc", auth="user", website=True)
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

    def _allowed_user_types(self):
        # En la plataforma de verificadores solo se registra un verificador;
        # en el resto de sitios, nunca.
        if _es_sitio_verificadores():
            return {"verificador"}
        return super()._allowed_user_types() - {"verificador"}

    def _extra_partner_vals(self, user_type, post):
        vals = super()._extra_partner_vals(user_type, post)

        # Cada plataforma registra lo suyo. Se comprueba en el servidor porque
        # ocultar la opción en el formulario no impide falsificar el POST.
        en_sitio_verificadores = _es_sitio_verificadores()
        if user_type == "verificador" and not en_sitio_verificadores:
            raise ValidationError(_(
                "El registro de verificadores se hace desde la plataforma de "
                "verificación, no desde aquí."))
        if user_type != "verificador" and en_sitio_verificadores:
            raise ValidationError(_(
                "Esta plataforma es solo para verificadores. Para registrarte "
                "como semillero, laboratorio o camaronera, usa el sitio del "
                "marketplace."))

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

        def _int(key):
            return int(to_number(post.get(key), 0.0))

        def _float(key):
            return to_number(post.get(key), 0.0)

        vals.update({
            # Perfil común (los nombres del formulario siguen siendo ver_*).
            "shrimp_razon_social": razon,
            "shrimp_representante": representante,
            "shrimp_telefono": telefono,
            "shrimp_ubicacion": _txt("ver_ubicacion"),
            "ver_cobertura": _txt("ver_cobertura"),
            "ver_registro_num": _txt("ver_registro_num"),
            # Contacto operativo
            "ver_email_avisos": _txt("ver_email_avisos"),
            "ver_whatsapp": _txt("ver_whatsapp"),
            "ver_horario": _txt("ver_horario"),
            # Cobertura y logística
            "ver_provincias": _txt("ver_provincias"),
            "ver_radio_km": _int("ver_radio_km"),
            "ver_tiempo_respuesta": _txt("ver_tiempo_respuesta"),
            "ver_equipo_propio": bool(post.get("ver_equipo_propio")),
            # Capacidades técnicas
            "ver_analisis_tipos": _txt("ver_analisis_tipos"),
            "ver_equipos": _txt("ver_equipos"),
            "shrimp_capacity_value": _int("ver_capacidad_lotes_dia"),
            "shrimp_capacity_unit": "lots_day",
            # Acreditación
            "ver_entidad_acredita": _txt("ver_entidad_acredita"),
            "ver_acred_vigencia": post.get("ver_acred_vigencia") or False,
            # Facturación (el RUC fiscal es también la identificación estándar)
            "ver_ruc": _txt("ver_ruc"),
            "vat": re.sub(r"[^0-9A-Za-z]", "", _txt("ver_ruc") or "").upper() or False,
            "ver_razon_fiscal": _txt("ver_razon_fiscal"),
            "ver_dir_fiscal": _txt("ver_dir_fiscal"),
            # Tarifa
            "ver_fee_base": _float("ver_fee_base"),
            "ver_fee_por_lb": _float("ver_fee_por_lb"),
            # Cuenta bancaria
            "ver_bank_name": _txt("ver_bank_name"),
            "ver_bank_account_type": post.get("ver_bank_account_type") or False,
            "ver_bank_account_number": _txt("ver_bank_account_number"),
            "ver_bank_holder": _txt("ver_bank_holder"),
            "ver_bank_holder_id": _txt("ver_bank_holder_id"),
        })
        return vals

    # ------------------------------------------------------------------
    # Registro dedicado del verificador
    # ------------------------------------------------------------------
    @http.route("/register/verifier", type="http", auth="public",
                website=True, sitemap=True)
    def registro_verificador(self, **kw):
        return request.render("shrimp_verification.registry_form_verifier",
                              {"values": {}})

    @http.route()
    def registro_form(self, **kw):
        # En el sitio de verificadores, /register lleva a su formulario propio.
        if _es_sitio_verificadores():
            return request.redirect("/register/verifier")
        return super().registro_form(**kw)

    def _registro_form_template(self, user_type):
        if user_type == "verificador" or _es_sitio_verificadores():
            return "shrimp_verification.registry_form_verifier"
        return super()._registro_form_template(user_type)

    def _post_registration(self, partner, user_type, post):
        super()._post_registration(partner, user_type, post)
        if user_type != "verificador":
            return
        # Alta opcional del equipo de técnicos: cada fila con nombre, correo y
        # contraseña válidos crea un contacto hijo + su usuario portal. Las
        # filas incompletas o con correo ya usado se ignoran sin romper el alta.
        Users = request.env["res.users"].sudo()
        Partner = request.env["res.partner"].sudo()
        Role = request.env["shrimp.tech.role"].sudo()
        portal = request.env.ref("base.group_portal")
        campo = "group_ids" if "group_ids" in Users._fields else "groups_id"

        idxs = set()
        for k in post:
            m = re.match(r"^team_(\d+)_email$", k)
            if m:
                idxs.add(m.group(1))

        # Como mucho 20 técnicos en el alta: cada fila crea un usuario.
        for idx in sorted(idxs, key=lambda x: int(x))[:20]:
            nombre = " ".join((post.get("team_%s_name" % idx) or "").split())
            correo = (post.get("team_%s_email" % idx) or "").strip().lower()
            clave = post.get("team_%s_password" % idx) or ""
            if not (nombre and correo and "@" in correo and len(clave) >= 8):
                continue
            if Users.search_count([("login", "=", correo)]):
                continue
            role = Role.resolve_ref(post.get("team_%s_role" % idx)) or Role
            tecnico = Partner.create({
                "name": nombre, "email": correo,
                "tech_role_id": role.id or False,
                "function": role.name or "Técnico de campo",
                "parent_id": partner.id, "shrimp_is_field_tech": True,
            })
            Users.create({
                "name": nombre, "login": correo, "email": correo,
                "partner_id": tecnico.id, campo: [(6, 0, [portal.id])],
                "password": clave,
            })

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
            cert = resolve_catalog_certificate(post.get(key), ["verificador"])
            if cert:
                return True
        return False

    def _certificate_roles(self):
        # El verificador pide sus certificados (acreditación) en su sitio.
        return super()._certificate_roles() | {"verificador"}


class ShrimpVerificationThanks(ShrimpTransactionPortalController):
    """El comprobante «¡Compra realizada!» no tiene sentido mientras la compra
    verificada no se cerró: quien llega a él con la compra pendiente de
    verificación o de las firmas va a la pantalla de su verificación."""

    @http.route()
    def marketplace_thanks(self, tx_ref, **kw):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if tx and tx.state in ("pending_verification", "pending_acceptance") \
                and current_partner() in (tx.buyer_partner_id, tx.seller_partner_id):
            return request.redirect("/marketplace/verifications/pending/%s" % tx.uuid_ref)
        return super().marketplace_thanks(tx_ref, **kw)
