# shrimp_user_registry/controllers/main.py
import base64
import re
from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError, UserError
from odoo.tools.mimetypes import guess_mimetype
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from werkzeug.exceptions import Forbidden
import logging
_logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ALLOWED_MIMETYPES = {"application/pdf", "image/jpeg", "image/png"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


IMAGE_MIMETYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _read_validated_file(content, allowed=None):
    """Valida tamaño y tipo real (magic bytes) del contenido subido.

    Devuelve el mimetype detectado por contenido (no el header del cliente,
    que es falsificable). Lanza ValidationError si no es válido.
    `allowed` restringe los tipos (por defecto PDF, JPG y PNG).
    """
    allowed = allowed or ALLOWED_MIMETYPES
    if len(content) > MAX_FILE_SIZE:
        raise ValidationError(_("Uno de los archivos excede el tamaño máximo permitido (5 MB)."))

    real_mime = guess_mimetype(content)
    if real_mime not in allowed:
        if allowed <= IMAGE_MIMETYPES:
            raise ValidationError(_("Uno de los archivos no es una imagen válida (JPG, PNG, WEBP o GIF)."))
        raise ValidationError(_("Uno de los archivos no tiene un formato permitido. Solo PDF, JPG y PNG."))

    return real_mime


def read_upload(file_obj, allowed=None):
    """Lee un archivo subido (werkzeug FileStorage) y lo valida por contenido.

    Devuelve (contenido, mimetype_real, nombre_saneado) o (b"", None, None) si
    no viene archivo o viene vacío. Lanza ValidationError si excede 5 MB o si
    el tipo real no está permitido. Nunca se usa el content_type del cliente.
    Para no cargar en memoria un archivo enorme se lee como máximo 5 MB + 1.
    """
    if not file_obj:
        return b"", None, None
    try:
        file_obj.stream.seek(0)
    except Exception:  # noqa: BLE001
        try:
            file_obj.seek(0)
        except Exception:  # noqa: BLE001
            pass
    content = file_obj.read(MAX_FILE_SIZE + 1)
    if not content:
        return b"", None, None
    mime = _read_validated_file(content, allowed=allowed)
    nombre = (getattr(file_obj, "filename", None) or "archivo")
    nombre = re.sub(r'[\\/"\r\n<>]', "_", nombre)[:200] or "archivo"
    return content, mime, nombre


def safe_filename(name, default="archivo"):
    """Nombre apto para Content-Disposition (sin comillas, barras ni saltos)."""
    name = re.sub(r'[\\/"\r\n;]', "-", (name or default))
    return name[:200] or default


def binary_headers(content, mimetype, filename=None, download=False, cache="private, max-age=0"):
    """Cabeceras seguras para servir un binario desde un controlador propio.

    Siempre `X-Content-Type-Options: nosniff` (el navegador no reinterpreta un
    PDF o una imagen como HTML) y `Content-Disposition` explícito.
    """
    disposition = "attachment" if download else "inline"
    headers = [
        ("Content-Type", mimetype or "application/octet-stream"),
        ("Content-Length", str(len(content))),
        ("X-Content-Type-Options", "nosniff"),
        ("Cache-Control", cache),
    ]
    if filename is not None or download:
        headers.append(("Content-Disposition", '%s; filename="%s"' % (
            disposition, safe_filename(filename or "archivo"))))
    else:
        headers.append(("Content-Disposition", disposition))
    return headers


# ----------------------------------------------------------------------
# Mensajes en la URL (?message=...)
# ----------------------------------------------------------------------
# Antes las pantallas pintaban el texto libre que viniera en ?message=, así
# que un enlace preparado podía poner en boca de la plataforma cualquier
# frase ("Tu cuenta fue suspendida, llama a..."). Ahora la URL solo lleva un
# CÓDIGO: o uno fijo de la tabla, o "flash", que significa "el texto está
# guardado en la sesión del propio usuario" (lo escribió el servidor, no el
# enlace). Un código desconocido no muestra nada.
MESSAGE_TEXTS = {
    "generic": "No se pudo completar la operación. Revisa los datos e inténtalo de nuevo.",
    "name_required": "El nombre es obligatorio.",
    "email_invalid": "El correo no es válido.",
    "password_short": "La contraseña debe tener al menos 8 caracteres.",
    "email_taken": "Ese correo ya tiene una cuenta.",
    "choose_tech": "Elige un técnico.",
    "invalid_verdict": "Veredicto no válido.",
    "rate_limited": "Demasiados intentos. Espera un momento y vuelve a intentarlo.",
}

_FLASH_KEY = "shrimp_flash_message"


def flash_message(text):
    """Guarda `text` en la sesión y devuelve el código a poner en la URL."""
    text = str(text or "").strip()
    if not text:
        return "generic"
    try:
        request.session[_FLASH_KEY] = text[:500]
    except Exception:  # noqa: BLE001 - sin sesión (p. ej. jsonrpc)
        return "generic"
    return "flash"


def pop_message(code):
    """Texto a mostrar para el código de la URL (o None)."""
    if not code:
        return None
    if code == "flash":
        try:
            return request.session.pop(_FLASH_KEY, None) or MESSAGE_TEXTS["generic"]
        except Exception:  # noqa: BLE001
            return MESSAGE_TEXTS["generic"]
    return MESSAGE_TEXTS.get(code)


def throttle(key, limit_param, window_param, default_limit, default_window):
    """Limitador simple por clave (p. ej. IP). Devuelve True si se permite.

    Guarda cada intento en shrimp.rate.limit; los límites se ajustan con
    parámetros del sistema. Deja constancia aun cuando la operación después
    falle: un atacante que prueba datos inválidos también consume cupo.
    """
    icp = request.env["ir.config_parameter"].sudo()
    try:
        limit = int(icp.get_param(limit_param) or default_limit)
        window = int(icp.get_param(window_param) or default_window)
    except (TypeError, ValueError):
        limit, window = default_limit, default_window
    if limit <= 0:
        return True
    return request.env["shrimp.rate.limit"].sudo().hit(key, limit, window)


class AccountPendingApproval(Forbidden):
    """La cuenta existe pero un administrador aún no la aprobó (o la
    rechazó). ir.http la convierte en una página explicativa con 403."""
    description = "Tu cuenta está pendiente de aprobación por la administración."


def require_operational(partner):
    """Corta el paso a las funciones operativas si la cuenta no está aprobada."""
    if not partner or not partner.shrimp_is_operational():
        raise AccountPendingApproval()
    return True


def client_ip():
    return (request.httprequest.remote_addr or "0.0.0.0")[:64]

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



def resolve_catalog_certificate(token, roles):
    """Certificado del CATÁLOGO (shrimp.certificate) activo para esos roles.

    Solo por su código uuid_ref, como el resto de registros de la plataforma:
    un id numérico ya no resuelve (el formulario recibe los códigos de
    /register/certificates).
    """
    token = str(token or "").strip()
    Cert = request.env["shrimp.certificate"].sudo()
    if not token or token.isdigit():
        return Cert.browse()
    return Cert.search([("active", "=", True), ("role", "in", list(roles)),
                        ("uuid_ref", "=", token)], limit=1)


def to_int(valor, por_defecto=0):
    """int() que no revienta con texto libre del formulario o de la URL."""
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return por_defecto


def to_number(valor, por_defecto=0.0):
    """float() tolerante: acepta coma decimal y devuelve el defecto si no es
    un número. La validación con mensaje la hace después el modelo."""
    try:
        return float(str(valor).strip().replace(",", ".") or por_defecto)
    except (TypeError, ValueError):
        return por_defecto


def current_partner():
    """Socio del usuario de la petición (el del portal, aunque se use sudo)."""
    return request.env.user.partner_id


class ShrimpAuthSignupRedirect(AuthSignupHome):
    """Intercepta el botón "Crear cuenta" del login: en vez del signup nativo de
    Odoo, lleva al formulario de registro propio (/register).
    Se conserva el flujo con token (usuarios invitados que fijan su contraseña)."""

    @http.route()
    def web_auth_signup(self, *args, **kw):
        if not kw.get("token") and request.httprequest.method == "GET":
            return request.redirect("/register")
        return super().web_auth_signup(*args, **kw)


class ShrimpRegistryController(http.Controller):

    @http.route("/register", type="http", auth="public", website=True, sitemap=True)
    def registro_form(self, **kw):
        return request.render("shrimp_user_registry.registry_form", {"values": {}})


    # Tipos que el formulario genérico puede dar de alta y roles que nacen
    # pendientes de aprobación: los decide la matriz de capacidades
    # (res.partner._shrimp_capability_matrix), que amplía cada módulo.
    # Los atributos se conservan una versión por compatibilidad.
    _BASE_USER_TYPES = {"semillero", "laboratorio", "camaronera"}
    _TYPES_REQUIRING_APPROVAL = {"empacadora", "maquilador"}

    # Nombre del campo trampa (honeypot): invisible para personas; un bot que
    # rellena todos los campos lo completa y su alta se descarta en silencio.
    HONEYPOT_FIELD = "website_hp"

    def _signup_types(self):
        return set(request.env["res.partner"].sudo()._shrimp_types_with("signup"))

    def _allowed_user_types(self):
        """Roles que se pueden registrar desde el sitio de esta petición."""
        return self._signup_types()

    def _types_requiring_approval(self):
        return set(request.env["res.partner"].sudo()._shrimp_types_with("requires_approval"))

    def _check_signup_guards(self, post):
        """Captcha (si está configurado) y límite de altas por IP.

        Devuelve un mensaje de error o None. El captcha usa el de Odoo
        (google_recaptcha / website_cf_turnstile): si el módulo no está o no
        tiene claves, la verificación no bloquea nada.
        """
        if not throttle("signup:%s" % client_ip(),
                        "shrimp_user_registry.signup_rate_limit",
                        "shrimp_user_registry.signup_rate_window", 5, 3600):
            return _("Demasiados intentos de registro desde tu conexión. "
                     "Espera un rato y vuelve a intentarlo.")
        try:
            request.env["ir.http"]._verify_request_recaptcha_token("shrimp_signup")
        except (ValidationError, UserError) as e:
            return e.args[0] if e.args else _("No se pudo verificar que no eres un robot.")
        return None

    @http.route("/register/submit", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def registro_submit(self, **post):
        env = request.env

        user_type = (post.get("shrimp_user_type") or "").strip()
        email = _normalize_email(post.get("email"))
        password = post.get("password") or ""
        name = _normalize_text(post.get("name") or email)
        vat_or_id = _normalize_vat(post.get("vat_or_id"))

        # Honeypot: se responde igual que un alta correcta para no enseñarle
        # al bot qué lo delató, pero no se crea nada.
        if (post.get(self.HONEYPOT_FIELD) or "").strip():
            _logger.info("Alta descartada por honeypot desde %s", client_ip())
            return request.redirect("/web/login")

        guard_error = self._check_signup_guards(post)
        if guard_error:
            return request.render(self._registro_form_template(user_type), {
                "error": guard_error,
                "values": {k: v for k, v in post.items() if "password" not in k},
            })

        try:
            # -----------------------------
            # 1. VALIDACIONES BÁSICAS
            # -----------------------------
            if not user_type:
                raise ValidationError(_("Debe seleccionar el tipo de usuario."))

            # Lista blanca por sitio: el tipo llega en el POST y es falsificable.
            if user_type not in self._allowed_user_types():
                raise ValidationError(_("Este tipo de usuario no se puede registrar desde aquí."))

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
            if user_type in self._types_requiring_approval():
                partner_vals["shrimp_account_state"] = "pending"

            # Perfil común: cada formulario conserva sus nombres de campo
            # (lab_*, farm_*) porque las secciones de todos los roles viajan
            # en el mismo POST, pero se guarda en los campos generales.
            if user_type == "laboratorio":
                partner_vals.update({
                    "shrimp_razon_social": _normalize_text(post.get("lab_razon_social")),
                    "lab_global_gap": _to_bool(post.get("lab_global_gap")),
                    "lab_social_ship_partner": _to_bool(post.get("lab_social_ship_partner")),
                    "shrimp_ubicacion": _normalize_text(post.get("lab_ubicacion")),
                })

            if user_type == "camaronera":
                partner_vals.update({
                    "shrimp_razon_social": _normalize_text(post.get("farm_razon_social")),
                    "shrimp_representante": _normalize_text(post.get("farm_representante")),
                    "shrimp_telefono": _normalize_text(post.get("farm_telefono")),
                    "shrimp_ubicacion": _normalize_text(post.get("farm_ubicacion")),
                    "shrimp_capacity_value": _to_float(post.get("farm_capacidad"), 0.0),
                    "shrimp_capacity_unit": "ton_year",
                    "farm_area_ha": _to_float(post.get("farm_area_ha"), 0.0),
                })

            # Hook de extension: otros modulos (p. ej. shrimp_verification)
            # aportan aqui los campos propios de su rol y sus validaciones.
            partner_vals.update(self._extra_partner_vals(user_type, post))

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
                self._save_certificate_lines(partner, user_type, post)
                attachment_model = env["ir.attachment"].sudo()

                # -----------------------------
                # 6. SEMILLERO
                # -----------------------------
                def _save_files(field_name, m2m_field):
                    lst = request.httprequest.files.getlist(field_name)
                    if not lst:
                        return

                    att_ids = []
                    for ff in lst[:20]:
                        c, real_mime, fname = read_upload(ff)
                        if not c:
                            continue

                        a = attachment_model.create({
                            "name": fname,
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

                # Hook post-registro: otros módulos crean lo suyo con la cuenta
                # ya existente (p. ej. el verificador da de alta a su equipo).
                self._post_registration(partner, user_type, post)

            return request.redirect("/web/login")

        except ValidationError as e:
            _logger.info("Validación fallida en /register/submit: %s", e)
            return request.render(self._registro_form_template(user_type), {
                "error": e.args[0] if e.args else _("No se pudo completar el registro."),
                "values": {k: v for k, v in post.items() if "password" not in k},
            })
        except Exception:
            _logger.exception("Error inesperado en /register/submit")
            return request.render(self._registro_form_template(user_type), {
                "error": _("Ocurrió un error inesperado al procesar el registro."),
                "values": {k: v for k, v in post.items() if "password" not in k},
            })
        

    def _save_certificate_lines(self, partner, user_type, post):
        """Guarda los certificados del formulario (cert_line_<n>_id/_file/
        _number/_issue_date/_expiry_date) para el rol `user_type`: solo los
        del catálogo que aplican a ese rol o a todos. Los usa el alta y el
        «Agregar perfil» de Mi cuenta."""
        env = request.env
        files = request.httprequest.files
        cert_line_model = env["shrimp.user.certificate.line"].sudo()
        attachment_model = env["ir.attachment"].sudo()

        indices = set()
        for k in post.keys():
            m = re.match(r"^cert_line_(\d+)_id$", k)
            if m:
                indices.add(m.group(1))

        creadas = cert_line_model.browse()
        for idx in sorted(indices, key=lambda x: int(x))[:30]:
            cert_id = post.get(f"cert_line_{idx}_id")
            f = files.get(f"cert_line_{idx}_file")
            if not cert_id or not f:
                continue

            # Validar que el certificado exista, esté activo y aplique al rol
            certificate = resolve_catalog_certificate(cert_id, [user_type, "all"])
            if not certificate:
                raise ValidationError(_("Uno de los certificados seleccionados no es válido para el tipo de usuario."))

            content, real_mime, fname = read_upload(f)
            if not content:
                continue

            att = attachment_model.create({
                "name": fname,
                "datas": base64.b64encode(content),
                "res_model": "res.partner",
                "res_id": partner.id,
                "mimetype": real_mime,
            })

            creadas |= cert_line_model.create({
                "partner_id": partner.id,
                "certificate_id": certificate.id,
                "certificate_number": _normalize_text(post.get(f"cert_line_{idx}_number")) or False,
                "issue_date": post.get(f"cert_line_{idx}_issue_date") or False,
                "expiry_date": post.get(f"cert_line_{idx}_expiry_date") or False,
                "file_attachment_id": att.id,
                "status": "pending",
            })
        return creadas

    # ------------------------------------------------------------------
    # Varios perfiles por cuenta
    # ------------------------------------------------------------------
    # El perfil activo ("Actuar como") se elige en la barra superior y se
    # guarda en la cuenta (res.partner.shrimp_user_type). Agregar un perfil
    # se hace desde «Mi cuenta»: nace pendiente si el rol requiere aprobación
    # (empacadora, maquilador) y lo aprueba la administración en
    # «Aprobaciones › Perfiles por aprobar».
    @staticmethod
    def _safe_next(url, default="/my"):
        """Solo rutas locales: un ?next= a otro dominio sería un redirect abierto."""
        url = (url or "").strip()
        if not url.startswith("/") or url.startswith("//") or "\\" in url \
                or "\r" in url or "\n" in url:
            return default
        return url[:500]

    @http.route("/my/profile/activate", type="http", auth="user", website=True,
                methods=["POST"], csrf=True, sitemap=False)
    def perfil_activar(self, role=None, next=None, **post):
        partner = current_partner()
        destino = self._safe_next(next)
        try:
            partner.sudo()._shrimp_set_active_role((role or "").strip())
        except (UserError, ValidationError) as e:
            sep = "&" if "?" in destino else "?"
            return request.redirect("%s%serror=%s" % (
                destino, sep, flash_message(e.args[0] if e.args else "")))
        return request.redirect(destino)

    def _profile_post_aliases(self, role, post):
        """El formulario de «Agregar perfil» manda el perfil común con nombres
        genéricos; los ganchos de cada rol (_extra_partner_vals) leen los
        nombres de SU formulario de alta (lab_*, farm_*, emp_*, pack_*)."""
        datos = dict(post)
        comunes = {
            "shrimp_razon_social": ("lab_razon_social", "farm_razon_social", "emp_razon_social",
                                    "pack_razon_social", "ver_razon_social"),
            "shrimp_representante": ("farm_representante", "emp_representante", "pack_representante",
                                     "ver_representante"),
            "shrimp_telefono": ("farm_telefono", "emp_telefono", "pack_telefono", "ver_telefono"),
            "shrimp_ubicacion": ("lab_ubicacion", "farm_ubicacion", "emp_planta_ubicacion",
                                 "pack_ubicacion", "ver_ubicacion"),
        }
        for generico, alias in comunes.items():
            valor = _normalize_text(post.get(generico))
            for a in alias:
                if valor and not datos.get(a):
                    datos[a] = valor
        return datos

    def _profile_vals(self, role, post):
        """Datos del perfil común y del rol que se guardan al agregarlo."""
        vals = {
            "shrimp_razon_social": _normalize_text(post.get("shrimp_razon_social")),
            "shrimp_representante": _normalize_text(post.get("shrimp_representante")),
            "shrimp_telefono": _normalize_text(post.get("shrimp_telefono")),
            "shrimp_ubicacion": _normalize_text(post.get("shrimp_ubicacion")),
        }
        if role == "camaronera" and post.get("farm_area_ha"):
            vals["farm_area_ha"] = _to_float(post.get("farm_area_ha"), 0.0)
        vals.update(self._extra_partner_vals(role, self._profile_post_aliases(role, post)))
        # El tipo y el estado de la cuenta no se tocan desde aquí: agregar un
        # perfil no cambia el perfil activo.
        for clave in ("shrimp_user_type", "shrimp_account_state"):
            vals.pop(clave, None)
        return vals

    @http.route("/my/profile/add", type="http", auth="user", website=True,
                methods=["POST"], csrf=True, sitemap=False)
    def perfil_agregar(self, role=None, next=None, **post):
        partner = current_partner()
        socio = partner.sudo()._shrimp_role_holder()
        destino = self._safe_next(next, "/my")
        sep = "&" if "?" in destino else "?"
        role = (role or "").strip()
        try:
            if socio != partner.sudo():
                raise ValidationError(_("Solo la cuenta de la empresa puede agregar perfiles."))
            # La misma lista blanca por sitio que el alta: lo que no se puede
            # registrar desde este sitio tampoco se agrega desde aquí.
            if role not in socio._shrimp_addable_roles():
                raise ValidationError(_("Ese perfil no se puede agregar a una cuenta."))
            if role not in self._allowed_user_types():
                raise ValidationError(_(
                    "El perfil «%s» se agrega desde su propia plataforma (la misma "
                    "en la que se registra): entra allí con tu cuenta y agrégalo "
                    "desde «Mi cuenta».") % socio._shrimp_type_label(role))
            post = dict(post, role=role)
            with request.env.cr.savepoint():
                linea = socio._shrimp_request_role(role, self._profile_vals(role, post))
                self._save_certificate_lines(socio, role, post)
        except (UserError, ValidationError) as e:
            return request.redirect("%s%serror=%s" % (
                destino, sep, flash_message(e.args[0] if e.args else "")))
        return request.redirect("%s%ssaved=%s" % (
            destino, sep, "perfil_pendiente" if linea.state == "pending" else "perfil"))

    def _extra_partner_vals(self, user_type, post):
        """Campos adicionales del partner segun el tipo de usuario.

        Punto de extension para modulos que agregan roles al registro. Puede
        lanzar ValidationError si el rol tiene requisitos propios.
        """
        return {}

    def _post_registration(self, partner, user_type, post):
        """Gancho tras crear el partner y su usuario (dentro de la misma
        transacción). Los módulos que amplían el registro crean aquí lo suyo.
        Por defecto no hace nada."""
        return

    def _registro_form_template(self, user_type):
        """Template al que se vuelve si el registro falla. Por defecto el
        formulario genérico; los módulos con formulario propio lo sobrescriben."""
        return "shrimp_user_registry.registry_form"

    def _certificate_roles(self):
        """Roles cuyos certificados se pueden pedir desde este sitio."""
        return self._allowed_user_types()

    @http.route("/register/certificates", type="jsonrpc", auth="public", website=True, csrf=False)
    def certificados_por_rol(self, role=None):
        """Catálogo de certificados para el formulario de alta.

        Devuelve el código uuid_ref ("ref"), nunca el id: el formulario lo
        envía de vuelta y el servidor solo resuelve por código.
        """
        domain = [("active", "=", True)]
        if role and role in self._certificate_roles():
            domain += [("role", "in", [role, "all"])]
        else:
            domain += [("role", "=", "all")]
        certs = request.env["shrimp.certificate"].sudo().search(domain, order="sequence, name")
        return [{"ref": c.uuid_ref, "name": c.name, "issuer": c.issuer} for c in certs]
