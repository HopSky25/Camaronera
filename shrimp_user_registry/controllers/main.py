# shrimp_user_registry/controllers/main.py
import base64
import re
from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.tools.mimetypes import guess_mimetype
import logging
_logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ALLOWED_MIMETYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def _read_validated_file(content):
    """Valida tamaño y tipo real (magic bytes) del contenido subido.

    Devuelve el mimetype detectado por contenido (no el header del cliente,
    que es falsificable). Lanza ValidationError si no es válido.
    """
    if len(content) > MAX_FILE_SIZE:
        raise ValidationError(_("Uno de los archivos excede el tamaño máximo permitido (5 MB)."))

    real_mime = guess_mimetype(content)
    if real_mime not in ALLOWED_MIMETYPES:
        raise ValidationError(_("Uno de los archivos no tiene un formato permitido. Solo PDF, JPG y PNG."))

    return real_mime

def _normalize_text(value):
    value = (value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def _normalize_name(value):
    return _normalize_text(value).lower()


def _normalize_email(value):
    return (value or "").strip().lower()


def _normalize_vat(value):
    value = (value or "").strip().upper()
    value = re.sub(r"[^A-Z0-9]", "", value)
    return value


def _to_float(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        raise ValidationError(_("Uno de los campos numéricos no tiene un valor válido."))


def _to_bool(value):
    return str(value or "").strip().lower() in ("1", "true", "on", "yes", "si")



class ShrimpRegistryController(http.Controller):

    @http.route("/registro", type="http", auth="public", website=True, sitemap=True)
    def registro_form(self, **kw):
        return request.render("shrimp_user_registry.registry_form", {"values": {}})


    @http.route("/registro/submit", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def registro_submit(self, **post):
        env = request.env

        user_type = (post.get("shrimp_user_type") or "").strip()
        email = _normalize_email(post.get("email"))
        password = post.get("password") or ""
        name = _normalize_text(post.get("name") or email)
        vat_or_id = _normalize_vat(post.get("vat_or_id"))

        try:
            # -----------------------------
            # 1. VALIDACIONES BÁSICAS
            # -----------------------------
            if not user_type:
                raise ValidationError(_("Debe seleccionar el tipo de usuario."))

            if not name:
                raise ValidationError(_("El nombre es obligatorio."))

            if not email:
                raise ValidationError(_("El correo es obligatorio."))

            if not EMAIL_RE.match(email):
                raise ValidationError(_("El correo no tiene un formato válido."))

            if not password:
                raise ValidationError(_("La contraseña es obligatoria."))

            if len(password) < 8:
                raise ValidationError(_("La contraseña debe tener al menos 8 caracteres."))

            if not vat_or_id:
                raise ValidationError(_("La cédula o identificación es obligatoria."))

            partner_model = env["res.partner"].sudo()
            users_model = env["res.users"].sudo()

            # -----------------------------
            # 2. VALIDACIONES DE DUPLICADOS
            # -----------------------------
            # Mensaje genérico unificado para no permitir enumerar
            # correos / cédulas / nombres ya registrados.
            generic_dup_msg = _(
                "Los datos proporcionados no se pueden usar para el registro. "
                "Verifica el correo y la identificación, o inicia sesión si ya tienes una cuenta."
            )

            if users_model.search([("login", "=", email)], limit=1):
                raise ValidationError(generic_dup_msg)

            if partner_model.search([("x_email_normalized", "=", email)], limit=1):
                raise ValidationError(generic_dup_msg)

            if partner_model.search([("x_vat_or_id_normalized", "=", vat_or_id)], limit=1):
                raise ValidationError(generic_dup_msg)

            if partner_model.search([("x_name_normalized", "=", _normalize_name(name))], limit=1):
                raise ValidationError(generic_dup_msg)

            # -----------------------------
            # 3. ARMAR VALORES DE PARTNER
            # -----------------------------
            partner_vals = {
                "name": name,
                "email": email,
                "shrimp_user_type": user_type,
                "vat_or_id": post.get("vat_or_id"),
            }

            if user_type == "laboratorio":
                partner_vals.update({
                    "lab_razon_social": _normalize_text(post.get("lab_razon_social")),
                    "lab_global_gap": _to_bool(post.get("lab_global_gap")),
                    "lab_social_ship_partner": _to_bool(post.get("lab_social_ship_partner")),
                    "lab_ubicacion": _normalize_text(post.get("lab_ubicacion")),
                })

            if user_type == "camaronera":
                partner_vals.update({
                    "farm_razon_social": _normalize_text(post.get("farm_razon_social")),
                    "farm_representante": _normalize_text(post.get("farm_representante")),
                    "farm_telefono": _normalize_text(post.get("farm_telefono")),
                    "farm_ubicacion": _normalize_text(post.get("farm_ubicacion")),
                    "farm_capacidad": _to_float(post.get("farm_capacidad"), 0.0),
                    "farm_area_ha": _to_float(post.get("farm_area_ha"), 0.0),
                })

            # -----------------------------
            # 4. CREACIÓN TRANSACCIONAL
            # -----------------------------
            with env.cr.savepoint():
                partner = partner_model.create(partner_vals)

                portal_group = env.ref("base.group_portal")
                # El campo de grupos del usuario se llama distinto según la
                # versión de Odoo (`groups_id` en 18, `group_ids` en 19).
                group_field = (
                    "group_ids" if "group_ids" in users_model._fields else "groups_id"
                )
                user = users_model.create({
                    "name": name,
                    "login": email,
                    "email": email,
                    "partner_id": partner.id,
                    group_field: [(6, 0, [portal_group.id])],
                    "password": password,
                })

                # -----------------------------
                # 5. CERTIFICADOS
                # -----------------------------
                files = request.httprequest.files
                cert_line_model = env["shrimp.user.certificate.line"].sudo()
                attachment_model = env["ir.attachment"].sudo()
                certificate_model = env["shrimp.certificate"].sudo()

                indices = set()
                for k in post.keys():
                    m = re.match(r"^cert_line_(\d+)_id$", k)
                    if m:
                        indices.add(m.group(1))

                for idx in sorted(indices, key=lambda x: int(x)):
                    cert_id = post.get(f"cert_line_{idx}_id")
                    f = files.get(f"cert_line_{idx}_file")
                    if not cert_id or not f:
                        continue

                    # Validar que el certificado exista, esté activo y aplique al rol
                    try:
                        cert_id_int = int(cert_id)
                    except (TypeError, ValueError):
                        continue
                    certificate = certificate_model.search([
                        ("id", "=", cert_id_int),
                        ("active", "=", True),
                        ("role", "in", [user_type, "all"]),
                    ], limit=1)
                    if not certificate:
                        raise ValidationError(_("Uno de los certificados seleccionados no es válido para el tipo de usuario."))

                    content = f.read()
                    if not content:
                        continue

                    real_mime = _read_validated_file(content)

                    att = attachment_model.create({
                        "name": f.filename,
                        "datas": base64.b64encode(content),
                        "res_model": "res.partner",
                        "res_id": partner.id,
                        "mimetype": real_mime,
                    })

                    cert_line_model.create({
                        "partner_id": partner.id,
                        "certificate_id": certificate.id,
                        "certificate_number": _normalize_text(post.get(f"cert_line_{idx}_number")) or False,
                        "issue_date": post.get(f"cert_line_{idx}_issue_date") or False,
                        "expiry_date": post.get(f"cert_line_{idx}_expiry_date") or False,
                        "file_attachment_id": att.id,
                        "status": "pending",
                    })

                # -----------------------------
                # 6. SEMILLERO
                # -----------------------------
                def _save_files(field_name, m2m_field):
                    lst = request.httprequest.files.getlist(field_name)
                    if not lst:
                        return

                    att_ids = []
                    for ff in lst:
                        c = ff.read()
                        if not c:
                            continue

                        real_mime = _read_validated_file(c)

                        a = attachment_model.create({
                            "name": ff.filename,
                            "datas": base64.b64encode(c),
                            "res_model": "res.partner",
                            "res_id": partner.id,
                            "mimetype": real_mime,
                        })
                        att_ids.append(a.id)

                    if att_ids:
                        partner.write({m2m_field: [(6, 0, att_ids)]})

                if user_type == "semillero":
                    _save_files("sem_photo_files", "sem_photo_attachment_ids")
                    _save_files("sem_facility_files", "sem_facility_photo_attachment_ids")

            return request.redirect("/web/login")

        except ValidationError as e:
            _logger.info("Validación fallida en /registro/submit: %s", e)
            return request.render("shrimp_user_registry.registry_form", {
                "error": e.args[0] if e.args else _("No se pudo completar el registro."),
                "values": post,
            })
        except Exception:
            _logger.exception("Error inesperado en /registro/submit")
            return request.render("shrimp_user_registry.registry_form", {
                "error": _("Ocurrió un error inesperado al procesar el registro."),
                "values": post,
            })
        

    @http.route("/registro/certificados", type="json", auth="public", website=True, csrf=False)
    def certificados_por_rol(self, role=None):
        domain = [("active", "=", True)]
        if role in ("semillero", "laboratorio", "camaronera"):
            domain += [("role", "in", [role, "all"])]
        certs = request.env["shrimp.certificate"].sudo().search(domain, order="sequence, name")
        return [{"id": c.id, "name": c.name, "issuer": c.issuer} for c in certs]
