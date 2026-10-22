# shrimp_user_registry/controllers/main.py
import base64
import json
from markupsafe import Markup
import re
from odoo import http, _, fields
from odoo.http import request
from odoo.exceptions import ValidationError, UserError
from odoo.tools.mimetypes import guess_mimetype
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from werkzeug.exceptions import Forbidden, NotFound
from urllib.parse import quote, urlencode as url_encode
import odoo.modules.module
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


class ShrimpPermissionDenied(Forbidden):
    """El usuario es de la cuenta pero su administrador no le dio el permiso
    que exige la acción (CR-12). ir.http la convierte en una página 403 que
    dice qué permiso falta."""
    description = "Tu usuario no tiene permiso para esta acción."


def require_permission(perm, role=None, scope="active"):
    """403 si el usuario de la petición no tiene `perm` (ver
    res.users._shrimp_has_perm). `role`: el perfil del registro sobre el que
    se actúa (por defecto, el perfil activo del usuario)."""
    user = request.env.user
    if not user._shrimp_has_perm(perm, role=role, scope=scope):
        raise ShrimpPermissionDenied(user._shrimp_perm_message(perm, role))
    return True


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
    """Socio en cuyo nombre actúa el usuario de la petición.

    El dueño de la cuenta (y un técnico de campo) actúa con su propio
    contacto, como siempre; un SUBUSUARIO actúa en nombre de su empresa
    (res.users._shrimp_actor_partner)."""
    user = request.env.user
    if not user or user._is_public():
        return user.partner_id
    return user.sudo()._shrimp_actor_partner().with_env(request.env)


def is_account_admin():
    """True si el usuario de la petición administra su cuenta de empresa."""
    user = request.env.user
    return bool(user) and not user._is_public() and user.sudo()._shrimp_is_account_admin()


def validate_ec_identification(vat, person_type=None):
    """Valida una cédula (10) o un RUC (13) de Ecuador con su dígito
    verificador (python-stdnum, la misma librería que usa l10n_ec).

    - Persona jurídica: solo RUC de sociedad (tercer dígito 9, privada) o de
      entidad pública (tercer dígito 6). Una cédula o un RUC de persona
      natural se rechazan.
    - Persona natural: cédula, o RUC de persona natural (cédula + 001...).
      Un RUC de sociedad se rechaza.
    - Sin tipo (formularios viejos): cualquiera de los anteriores.
    Devuelve el número normalizado o lanza ValidationError."""
    from stdnum.ec import ci, ruc
    from stdnum.exceptions import ValidationError as StdnumError
    numero = _normalize_vat(vat)
    if not numero:
        raise ValidationError(_("Escribe la cédula o el RUC."))
    if not numero.isdigit():
        raise ValidationError(_("La cédula o el RUC solo llevan números."))

    def _ruc_sociedad(n):
        if len(n) != 13 or n[2] not in ("6", "9"):
            return False
        try:
            if n[2] == "9":
                ruc._validate_juridical(n)
            else:
                ruc._validate_public(n)
        except StdnumError:
            return False
        return ruc.is_valid(n)

    def _ruc_natural(n):
        # Cédula (tercer dígito 0-5, o 6 en cédulas antiguas) + establecimiento.
        if len(n) != 13 or n[2] > "6":
            return False
        try:
            ruc._validate_natural(n)
        except StdnumError:
            return False
        return True

    if person_type == "juridica":
        if len(numero) == 10:
            raise ValidationError(_(
                "Una persona jurídica se identifica con el RUC de la sociedad "
                "(13 dígitos), no con una cédula."))
        if _ruc_sociedad(numero):
            return numero
        if _ruc_natural(numero):
            raise ValidationError(_(
                "Ese RUC es de una persona natural. Si la cuenta es tuya, elige "
                "«Persona natural»."))
        raise ValidationError(_(
            "El RUC no es válido: 13 dígitos, tercer dígito 9 (sociedad "
            "privada) o 6 (pública) y su dígito verificador."))
    if person_type == "natural":
        if len(numero) == 10 and ci.is_valid(numero):
            return numero
        if _ruc_natural(numero):
            return numero
        if _ruc_sociedad(numero):
            raise ValidationError(_(
                "Ese RUC es de una sociedad. Elige «Persona jurídica (empresa)»."))
        raise ValidationError(_(
            "La cédula o el RUC de persona natural no son válidos: revisa los "
            "dígitos (cédula: 10; RUC: 13, termina en 001)."))
    if len(numero) == 10 and ci.is_valid(numero):
        return numero
    if len(numero) == 13 and ruc.is_valid(numero):
        return numero
    raise ValidationError(_("La cédula o el RUC no son válidos: revisa los dígitos."))


def legal_profile_label(role):
    from odoo.addons.shrimp_user_registry.models.shrimp_legal import profile_label
    return profile_label(request.env, role)


def legal_versions_from_post(post, documents):
    """Versiones aceptadas en un formulario.

    Cada documento se acepta con la casilla legal_accept_<código> cuyo valor
    es el CÓDIGO DE VERSIÓN que se le mostró. Si mientras tanto se publicó
    otra versión, la casilla ya no vale (hay que leer la nueva).
    Devuelve (versiones aceptadas, [documentos que faltan], [desactualizados]).
    """
    Version = request.env["shrimp.legal.document.version"].sudo()
    aceptadas, faltan, viejas = Version.browse(), [], []
    for doc in documents:
        vigente = doc.current_version_id
        if not vigente:
            continue
        valor = (post.get("legal_accept_%s" % doc.code) or "").strip()
        if not valor:
            faltan.append(doc)
        elif valor != vigente.name:
            viejas.append(doc)
        else:
            aceptadas |= vigente
    return aceptadas, faltan, viejas


def legal_error_message(faltan, viejas):
    if viejas:
        return _("Se publicó una versión nueva de: %s. Léela y vuelve a aceptarla.") % ", ".join(
            d.display_label() for d in viejas)
    return _("Para continuar tienes que leer y aceptar: %s.") % ", ".join(
        d.display_label() for d in faltan)


def request_user_agent():
    try:
        return (request.httprequest.headers.get("User-Agent") or "")[:400]
    except Exception:  # noqa: BLE001
        return ""


def site_allows_role(role):
    """True si el perfil `role` se usa en el sitio de la petición.

    Cada plataforma trabaja con sus perfiles (website._shrimp_role_allowed_here,
    shrimp_marketplace): la verificadora solo en CamaronMarket Verificadores,
    el maquilador solo en CamaronMarket Empaque y los demás en el marketplace.
    Sin sitio (o sin shrimp_marketplace instalado) no se restringe nada."""
    web = getattr(request, "website", None)
    if web and hasattr(web, "_shrimp_role_allowed_here"):
        return web._shrimp_role_allowed_here(role)
    return True


def offsite_role_message(role):
    """Mensaje para un perfil que se usa en otro sitio."""
    etiqueta = request.env["res.partner"].sudo()._shrimp_type_label(role)
    web = getattr(request, "website", None)
    sitio = ""
    if web and hasattr(web, "_shrimp_platform_site"):
        destino = web.sudo()._shrimp_platform_site(web._shrimp_role_platform(role))
        sitio = destino.name if destino else ""
    if sitio:
        return _("El perfil «%(p)s» se usa en su propio sitio, %(s)s: entra allí con tu cuenta.") % {
            "p": etiqueta, "s": sitio}
    return _("El perfil «%s» se usa en su propio sitio: entra allí con tu cuenta.") % etiqueta


class ShrimpAuthSignupRedirect(AuthSignupHome):
    """Intercepta el botón "Crear cuenta" del login: en vez del signup nativo de
    Odoo, lleva al formulario de registro propio (/register).
    Se conserva el flujo con token (usuarios invitados que fijan su contraseña)."""

    @http.route()
    def web_auth_signup(self, *args, **kw):
        if not kw.get("token") and request.httprequest.method == "GET":
            return request.redirect("/register")
        return super().web_auth_signup(*args, **kw)


# Obsoleto (19.0.1.6.0): la versión vigente de cada documento legal la decide
# ahora shrimp.legal.document.version (backoffice › Documentos legales). Se
# conserva el nombre por compatibilidad con integraciones que lo importen.
POLITICA_PRIVACIDAD_VERSION = "2026-10"


class ShrimpRegistryController(http.Controller):

    @http.route("/register", type="http", auth="public", website=True, sitemap=True)
    def registro_form(self, **kw):
        preseleccion = [r for r in (kw.get("profile") or "").split(",") if r]
        return self._render_signup("shrimp_user_registry.registry_form", {}, preselect=preseleccion)

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

    # ------------------------------------------------------------------
    # Asistente de alta (19.0.1.6.0)
    # ------------------------------------------------------------------
    # Descripción corta de cada perfil para el paso 3 del asistente.
    _PROFILE_HINTS = {
        "semillero": "Produce y vende nauplios y reproductores a los laboratorios.",
        "laboratorio": "Produce larva (postlarva) y la vende a camaroneras.",
        "camaronera": "Cría camarón en piscinas: compra larva y vende su cosecha.",
        "empacadora": "Compra camarón adulto, lo procesa y lo exporta.",
        "maquilador": "Planta que empaca camarón de terceros (servicio).",
        "verificador": "Verifica lotes en campo como tercero acreditado (opera con su acreditación aprobada).",
    }
    _PROFILE_ICONS = {
        "semillero": "fa-leaf", "laboratorio": "fa-flask", "camaronera": "fa-tint",
        "empacadora": "fa-industry", "maquilador": "fa-cubes", "verificador": "fa-check-square-o",
    }

    def _ordered_types(self, tipos):
        orden = [v for v, _l in request.env["res.partner"]._fields["shrimp_user_type"].selection]
        return [t for t in orden if t in set(tipos)]

    def _signup_profiles(self):
        """Perfiles que ofrece el asistente en este sitio (en orden)."""
        aprobacion = self._types_requiring_approval()
        return [{
            "code": t, "label": legal_profile_label(t),
            "hint": self._PROFILE_HINTS.get(t, ""),
            "icon": self._PROFILE_ICONS.get(t, "fa-user"),
            "requires_approval": t in aprobacion,
        } for t in self._ordered_types(self._allowed_user_types())]

    def _signup_context(self, values, preselect=None):
        perfiles = self._signup_profiles()
        codigos = [p["code"] for p in perfiles]
        elegidos = self._posted_types(values) if values else []
        if not elegidos and preselect:
            elegidos = [r for r in preselect if r in codigos]
        if not elegidos and len(codigos) == 1:
            elegidos = codigos[:]
        Doc = request.env["shrimp.legal.document"].sudo()
        return {
            # Catálogo de certificados por perfil para la sección única de
            # certificados del asistente (19.0.1.9.0).
            "signup_cert_catalog_json": json.dumps(self._signup_cert_catalog(codigos)),
            # Perfiles para el «Perfil al que aplica» del modal (tabla única, 19.0.1.10.1).
            "signup_cert_profiles_json": json.dumps(
                [{"code": p["code"], "label": p["label"]} for p in perfiles]),
            "signup_profiles": perfiles,
            "signup_profile_codes": codigos,
            # Enlaces al alta de los perfiles que se registran en otro sitio
            # (verificadora, planta de maquila): [{text, site, url}].
            "signup_other_sites": self._signup_other_sites(),
            "selected_profiles": elegidos,
            "legal_roles": codigos,
            "legal_documents": Doc._shrimp_signup_documents(codigos),
            "ec_states": request.env["res.country.state"].sudo().search(
                [("country_id.code", "=", "EC")], order="name"),
        }

    def _signup_other_sites(self):
        """Avisos del paso «Perfil» con el enlace al alta de los perfiles que
        se registran en su propio sitio. Cada módulo agrega el suyo."""
        return []

    def _signup_cert_catalog(self, codigos):
        """{perfil: [{ref, name, issuer, code, required, expires}]} con los
        certificados del catálogo que aplican a cada perfil (los suyos y los
        de «Todos»). `required`: «Obligatorio al registrarse» del propio
        perfil (lo mismo que exige _check_required_certificates)."""
        Cert = request.env["shrimp.certificate"].sudo()
        certs = Cert.search([("active", "=", True), ("role", "in", list(codigos) + ["all"])],
                            order="sequence, name")
        catalogo = {}
        for codigo in codigos:
            catalogo[codigo] = [{
                "ref": c.uuid_ref, "name": c.name, "issuer": c.issuer or "", "code": c.code or "",
                "required": bool(c.required_at_signup) and c.role == codigo,
                "expires": bool(c.expires_required),
            } for c in certs if c.role in (codigo, "all")]
        return catalogo

    def _signup_done_redirect(self, partner, tipos):
        """A dónde va el navegador tras un alta correcta (gancho por módulo)."""
        return "/web/login"

    def _render_signup(self, template, values, error=None, preselect=None):
        ctx = {"values": values or {}}
        if error:
            ctx["error"] = error
        ctx.update(self._signup_context(values or {}, preselect=preselect))
        return request.render(template, ctx)

    @staticmethod
    def _posted_types(post):
        """Perfiles elegidos: varios (shrimp_user_types) o uno (shrimp_user_type,
        el de los formularios especializados y las integraciones viejas)."""
        tipos = []
        try:
            tipos = request.httprequest.form.getlist("shrimp_user_types")
        except Exception:  # noqa: BLE001
            tipos = []
        if not tipos and isinstance(post.get("shrimp_user_types"), (list, tuple)):
            tipos = list(post.get("shrimp_user_types"))
        elif not tipos and isinstance(post.get("shrimp_user_types"), str):
            tipos = [t for t in post["shrimp_user_types"].split(",")]
        if not tipos and post.get("shrimp_user_type"):
            tipos = [post.get("shrimp_user_type")]
        vistos = []
        for t in tipos:
            t = (t or "").strip()
            if t and t not in vistos:
                vistos.append(t)
        return vistos

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

    # Formularios propios de cada perfil que mandan la razón social o el
    # representante con su prefijo (el del verificador, integraciones viejas).
    _RAZON_KEYS = ("shrimp_razon_social", "ver_razon_social", "farm_razon_social",
                   "lab_razon_social", "emp_razon_social", "pack_razon_social")
    _REPRESENTANTE_KEYS = ("shrimp_representante", "ver_representante", "farm_representante",
                           "emp_representante", "pack_representante")

    @staticmethod
    def _first_text(post, keys):
        for k in keys:
            valor = _normalize_text(post.get(k))
            if valor:
                return valor
        return ""

    def _company_vals(self, post):
        """Paso 2 del asistente: datos del titular de la cuenta, según el tipo
        de persona (19.0.1.8.0).

        Persona natural: cédula o RUC de persona natural, nombres y apellidos
        (por defecto, el nombre del paso 1) y nombre comercial opcional; no
        tiene representante legal (se representa a sí misma).
        Persona jurídica: RUC de sociedad, razón social, nombre comercial y
        representante legal obligatorios; cédula del representante opcional.

        Lo que no aplica al tipo elegido se ignora (sin JS el navegador manda
        todos los campos). `name` del socio = nombre legal (razón social o
        nombres y apellidos); `vat` sale de vat_or_id al crear."""
        tipo_persona = (post.get("shrimp_person_type") or "").strip()
        if tipo_persona not in ("natural", "juridica"):
            raise ValidationError(_("Indica si la cuenta es de una persona natural o jurídica."))
        juridica = tipo_persona == "juridica"
        vat = validate_ec_identification(post.get("vat_or_id"), tipo_persona)
        contacto = _normalize_text(post.get("shrimp_contact_name"))
        if not contacto:
            raise ValidationError(_("Escribe tu nombre completo (paso 1)."))
        celular = _normalize_text(post.get("phone"))
        if not celular or len(re.sub(r"\D", "", celular)) < 9:
            raise ValidationError(_("Escribe un número de celular válido."))

        # Nombre comercial: el campo nuevo o, en formularios viejos, `name`.
        comercial = _normalize_text(post.get("shrimp_nombre_comercial")) \
            if "shrimp_nombre_comercial" in post else _normalize_text(post.get("name"))
        representante = cedula_rep = False
        if juridica:
            nombre_legal = self._first_text(post, self._RAZON_KEYS)
            if not nombre_legal:
                raise ValidationError(_("La razón social es obligatoria para una persona jurídica."))
            if not comercial:
                raise ValidationError(_("El nombre comercial es obligatorio para una persona jurídica."))
            representante = self._first_text(post, self._REPRESENTANTE_KEYS)
            if not representante:
                raise ValidationError(_("El representante legal es obligatorio para una persona jurídica."))
            cedula_rep = _normalize_vat(post.get("shrimp_representante_cedula"))
            if cedula_rep:
                from stdnum.ec import ci
                if len(cedula_rep) != 10 or not ci.is_valid(cedula_rep):
                    raise ValidationError(_("La cédula del representante legal no es válida."))
        else:
            # Se representa a sí misma: registro_submit pone su nombre como
            # representante (el perfil común y la regla de la camaronera lo
            # piden lleno) salvo que el formulario de su perfil traiga otro
            # responsable (el «responsable técnico» del verificador).
            nombre_legal = _normalize_text(post.get("shrimp_nombres_apellidos")) or contacto

        estado = request.env["res.country.state"].sudo().browse(to_int(post.get("state_id")))
        if not estado.exists() or estado.country_id.code != "EC":
            raise ValidationError(_("Elige la provincia."))
        canton = _normalize_text(post.get("city"))
        direccion = _normalize_text(post.get("street"))
        if not canton:
            raise ValidationError(_("Escribe el cantón."))
        if not direccion:
            raise ValidationError(_("Escribe la dirección."))
        vals = {
            "shrimp_person_type": tipo_persona,
            "is_company": juridica,
            "name": nombre_legal,
            "shrimp_razon_social": nombre_legal,
            "shrimp_nombre_comercial": comercial or False,
            "vat_or_id": vat,
            "shrimp_contact_name": contacto,
            "phone": celular,
            "state_id": estado.id,
            "country_id": estado.country_id.id,
            "city": canton,
            "street": direccion,
            "shrimp_actividad": _normalize_text(post.get("shrimp_actividad")) or False,
        }
        if juridica:
            vals.update({"shrimp_representante": representante,
                         "shrimp_representante_cedula": cedula_rep or False})
        for campo in ("shrimp_telefono", "shrimp_ubicacion"):
            valor = _normalize_text(post.get(campo))
            if valor:
                vals[campo] = valor
        return vals

    def _profile_specific_vals(self, user_type, post):
        """Campos propios de los perfiles del módulo base (laboratorio,
        camaronera, naupliero) en el asistente."""
        vals = {}
        if user_type == "laboratorio":
            vals.update({
                "shrimp_razon_social": _normalize_text(post.get("lab_razon_social")) or False,
                # GlobalG.A.P. / Social Ship Partner ya no se piden al
                # registrarse (son certificados: paso 4, catálogo). Los
                # booleanos guardados se conservan; el alta no los escribe.
                "shrimp_ubicacion": _normalize_text(post.get("lab_ubicacion")) or False,
            })
        if user_type in ("laboratorio", "semillero"):
            clave = "lab_permiso_funcionamiento" if user_type == "laboratorio" else "sem_permiso_funcionamiento"
            permiso = _normalize_text(post.get(clave) or post.get("shrimp_permiso_funcionamiento"))
            if permiso:
                vals["shrimp_permiso_funcionamiento"] = permiso
        if user_type == "camaronera":
            vals.update({
                "shrimp_razon_social": _normalize_text(post.get("farm_razon_social")) or False,
                "shrimp_representante": _normalize_text(post.get("farm_representante")) or False,
                "shrimp_telefono": _normalize_text(post.get("farm_telefono")) or False,
                "shrimp_ubicacion": _normalize_text(post.get("farm_ubicacion")) or False,
                "shrimp_registro_acuicola": _normalize_text(post.get("shrimp_registro_acuicola")) or False,
                "shrimp_permiso_ambiental": _normalize_text(post.get("shrimp_permiso_ambiental")) or False,
            })
            # Hectáreas, n.º de piscinas y capacidad ya NO se piden en el
            # asistente (19.0.1.10.1): el área y las piscinas salen de las
            # instalaciones registradas. Si llegan (integraciones viejas) se
            # guardan; si no, no se escribe nada (ni ceros).
            area = _to_float(post.get("farm_area_ha"), 0.0)
            if area > 0:
                vals["farm_area_ha"] = area
            piscinas = int(_to_float(post.get("shrimp_num_piscinas"), 0.0))
            if piscinas > 0:
                vals["shrimp_num_piscinas"] = piscinas
            capacidad = _to_float(post.get("farm_capacidad"), 0.0)
            if capacidad > 0:
                vals.update({"shrimp_capacity_value": capacidad, "shrimp_capacity_unit": "ton_year"})
        return vals

    def _check_required_certificates(self, tipos, post):
        """Certificados que el catálogo marca como obligatorios al registrarse
        (shrimp.certificate.required_at_signup) para cada perfil elegido."""
        Cert = request.env["shrimp.certificate"].sudo()
        requeridos = Cert.search([("active", "=", True), ("required_at_signup", "=", True),
                                  ("role", "in", list(tipos))])
        if not requeridos:
            return
        files = request.httprequest.files
        subidos = set()
        for k in post:
            m = re.match(r"^cert_line_(\d+)_id$", k)
            if m and files.get("cert_line_%s_file" % m.group(1)):
                subidos.add((post.get(k) or "").strip())
        faltan = [c for c in requeridos if c.uuid_ref not in subidos]
        if faltan:
            raise ValidationError(_("Adjunta los documentos obligatorios: %s.") % ", ".join(
                faltan.mapped("name") if hasattr(faltan, "mapped") else [c.name for c in faltan]))

    @http.route("/register/submit", type="http", auth="public", website=True, methods=["POST"], csrf=True)
    def registro_submit(self, **post):
        env = request.env
        tipos = self._posted_types(post)
        user_type = tipos[0] if tipos else ""
        email = _normalize_email(post.get("email"))
        password = post.get("password") or ""
        valores = {k: v for k, v in post.items() if "password" not in k}
        if tipos:
            valores["shrimp_user_types"] = tipos

        # Honeypot: se responde igual que un alta correcta para no enseñarle
        # al bot qué lo delató, pero no se crea nada.
        if (post.get(self.HONEYPOT_FIELD) or "").strip():
            _logger.info("Alta descartada por honeypot desde %s", client_ip())
            return request.redirect("/web/login")

        guard_error = self._check_signup_guards(post)
        if guard_error:
            return self._render_signup(self._registro_form_template(user_type), valores, guard_error)

        try:
            # -----------------------------
            # 1. VALIDACIONES BÁSICAS
            # -----------------------------
            if not tipos:
                raise ValidationError(_("Elige al menos un perfil."))
            # Lista blanca por sitio: el tipo llega en el POST y es falsificable.
            permitidos = self._allowed_user_types()
            for t in tipos:
                if not site_allows_role(t):
                    raise ValidationError(offsite_role_message(t))
                if t not in permitidos:
                    raise ValidationError(_("Este tipo de usuario no se puede registrar desde aquí."))
            tipos = self._ordered_types(tipos)
            if not email:
                raise ValidationError(_("El correo es obligatorio."))
            if not EMAIL_RE.match(email):
                raise ValidationError(_("El correo no tiene un formato válido."))
            if not password:
                raise ValidationError(_("La contraseña es obligatoria."))
            if len(password) < 8:
                raise ValidationError(_("La contraseña debe tener al menos 8 caracteres."))
            if "password_confirm" in post and post.get("password_confirm") != password:
                raise ValidationError(_("Las contraseñas no coinciden."))

            empresa = self._company_vals(post)
            natural = empresa["shrimp_person_type"] == "natural"
            # Nombre legal: razón social o nombres y apellidos.
            name = empresa["name"]
            vat_or_id = _normalize_vat(empresa["vat_or_id"])

            # Documentos legales: generales + anexo de cada perfil elegido.
            Doc = env["shrimp.legal.document"].sudo()
            documentos = Doc.browse([d.id for d, _v in Doc._shrimp_signup_documents(tipos)])
            versiones, faltan, viejas = legal_versions_from_post(post, documentos)
            if faltan or viejas:
                raise ValidationError(legal_error_message(faltan, viejas))

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
            # Dos personas naturales pueden llamarse igual (las separa la
            # cédula); el nombre solo se compara entre sociedades.
            if not natural and partner_model.search([
                    ("x_name_normalized", "=", _normalize_name(name)),
                    ("shrimp_person_type", "!=", "natural")], limit=1):
                raise ValidationError(generic_dup_msg)

            # -----------------------------
            # 3. ARMAR VALORES DE PARTNER
            # -----------------------------
            # Perfil activo: el primero que no requiere aprobación (la cuenta
            # puede operar ya con él); si todos la requieren, el primero.
            aprobacion = self._types_requiring_approval()
            activo = next((t for t in tipos if t not in aprobacion), tipos[0])
            otros = [t for t in tipos if t != activo]

            # Los ganchos de cada rol leen los nombres de SU formulario
            # (lab_*, farm_*, emp_*, pack_*, ver_*): se rellenan desde los
            # genéricos del asistente.
            datos = dict(post)
            datos["shrimp_razon_social"] = empresa["shrimp_razon_social"]
            if natural:
                # Sin representante legal: lo que llegue (sin JS se mandan
                # todos los campos) no se usa.
                datos.pop("shrimp_representante", None)
                datos.pop("shrimp_representante_cedula", None)
            else:
                datos["shrimp_representante"] = empresa["shrimp_representante"]
            if not _normalize_text(datos.get("shrimp_telefono")):
                datos["shrimp_telefono"] = empresa["phone"]
            if not _normalize_text(datos.get("shrimp_ubicacion")):
                estado = env["res.country.state"].sudo().browse(empresa["state_id"])
                datos["shrimp_ubicacion"] = ", ".join(
                    x for x in (empresa["street"], empresa["city"], estado.name) if x)
            datos = self._profile_post_aliases(None, datos)
            self._check_required_certificates(tipos, post)

            partner_vals = {}
            # El activo al final: sus datos (capacidad, unidad) mandan.
            for t in otros + [activo]:
                partner_vals.update({k: v for k, v in self._profile_specific_vals(t, datos).items()
                                     if v or k not in partner_vals})
                # Hook de extension: otros modulos (p. ej. shrimp_verification)
                # aportan aqui los campos propios de su rol y sus validaciones.
                extra = self._extra_partner_vals(t, datos)
                partner_vals.update({k: v for k, v in extra.items() if v or k not in partner_vals})
            partner_vals.update({k: v for k, v in empresa.items() if v not in (None, "")})
            # Lo común escrito en el asistente manda sobre los alias.
            for campo in ("shrimp_razon_social", "shrimp_representante", "shrimp_telefono"):
                if datos.get(campo) and not partner_vals.get(campo):
                    partner_vals[campo] = _normalize_text(datos[campo])
            if not partner_vals.get("shrimp_telefono"):
                partner_vals["shrimp_telefono"] = empresa["phone"]
            if not partner_vals.get("shrimp_ubicacion"):
                estado = env["res.country.state"].sudo().browse(empresa["state_id"])
                partner_vals["shrimp_ubicacion"] = ", ".join(
                    x for x in (empresa["street"], empresa["city"], estado.name) if x)
            if natural and not partner_vals.get("shrimp_representante"):
                # La persona natural se representa a sí misma.
                partner_vals["shrimp_representante"] = name

            partner_vals.update({
                "name": name,
                "email": email,
                "shrimp_user_type": activo,
                "vat_or_id": vat_or_id,
            })
            partner_vals["shrimp_account_state"] = "pending" if activo in aprobacion else "approved"

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
                # Quien crea la cuenta es su ADMINISTRADOR.
                env["shrimp.account.member"].sudo()._shrimp_ensure(user, partner, is_admin=True)

                # Los demás perfiles elegidos: pendientes si requieren aprobación.
                for t in otros:
                    partner._shrimp_request_role(t)

                # -----------------------------
                # 5. CERTIFICADOS
                # -----------------------------
                # Una sola pasada: cada certificado se guarda una vez y vale
                # para cualquiera de los perfiles elegidos (antes se recorría
                # por perfil: un certificado de «Todos» se duplicaba y el de un
                # perfil hacía fallar la pasada de otro).
                self._save_certificate_lines(partner, user_type, post, roles=tipos)
                attachment_model = env["ir.attachment"].sudo()

                # -----------------------------
                # 6. NAUPLIERO (fotos)
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

                if "semillero" in tipos:
                    _save_files("sem_photo_files", "sem_photo_attachment_ids")
                    _save_files("sem_facility_files", "sem_facility_photo_attachment_ids")

                # Hook post-registro: otros módulos crean lo suyo con la cuenta
                # ya existente (p. ej. el verificador da de alta a su equipo).
                for t in tipos:
                    self._post_registration(partner, t, post)

                # Consentimiento y contrato (LOPDP): una aceptación por
                # documento, con su comprobante PDF.
                env["shrimp.legal.acceptance"].sudo()._shrimp_record(
                    user, versiones, "signup", ip=client_ip(), user_agent=request_user_agent(),
                    company=partner)

            return request.redirect(self._signup_done_redirect(partner, tipos), local=False)

        except ValidationError as e:
            _logger.info("Validación fallida en /register/submit: %s", e)
            return self._render_signup(self._registro_form_template(user_type), valores,
                                       e.args[0] if e.args else _("No se pudo completar el registro."))
        except Exception:
            _logger.exception("Error inesperado en /register/submit")
            return self._render_signup(self._registro_form_template(user_type), valores,
                                       _("Ocurrió un error inesperado al procesar el registro."))


    def _save_certificate_lines(self, partner, user_type, post, roles=None):
        """Guarda los certificados del formulario (cert_line_<n>_id/_file/
        _number/_issue_date/_expiry_date) para el rol `user_type` (o los
        `roles` elegidos en el alta): solo los del catálogo que aplican a esos
        roles o a todos. Nacen pendientes de revisión. Los usan el alta y el
        «Agregar perfil» de Mi cuenta."""
        roles = list(roles or [user_type])
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
            certificate = resolve_catalog_certificate(cert_id, roles + ["all"])
            if not certificate:
                raise ValidationError(_("Uno de los certificados seleccionados no es válido para el tipo de usuario."))
            # Tabla única de certificados (19.0.1.10.1): cada fila dice a qué
            # perfil aplica (cert_line_<n>_role). Ese perfil tiene que ser uno
            # de los elegidos y el certificado, de ese perfil o de «Todos».
            # Sin _role (formularios viejos, «Agregar perfil»): como antes.
            perfil = (post.get(f"cert_line_{idx}_role") or "").strip()
            if perfil:
                if perfil not in roles:
                    raise ValidationError(_(
                        "El certificado «%s» se asignó a un perfil que no elegiste.") % certificate.name)
                if certificate.role not in (perfil, "all"):
                    raise ValidationError(_(
                        "El certificado «%(c)s» no corresponde al perfil «%(p)s».") % {
                            "c": certificate.name,
                            "p": request.env["res.partner"].sudo()._shrimp_type_label(perfil)})

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
        role = (role or "").strip()
        # Cada sitio activa solo sus perfiles (la verificadora y el
        # maquilador se usan en su propio sitio).
        if role and not site_allows_role(role):
            sep = "&" if "?" in destino else "?"
            return request.redirect("%s%serror=%s" % (
                destino, sep, flash_message(offsite_role_message(role))))
        # Antes de usar un perfil, la empresa tiene que haber aceptado su
        # anexo (si nunca lo aceptó o hay una versión MAYOR nueva).
        pendiente = legal_annex_pending(partner.sudo()._shrimp_role_holder(), role)
        if pendiente and legal_enforced():
            return request.redirect("/my/legal/accept?%s" % url_encode({
                "activate": role, "redirect": destino}))
        try:
            partner.sudo()._shrimp_set_active_role(role)
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
            if socio != partner.sudo() or not is_account_admin():
                raise ValidationError(_("Solo el administrador de la cuenta puede agregar perfiles."))
            # La misma lista blanca por sitio que el alta: lo que no se puede
            # registrar desde este sitio tampoco se agrega desde aquí.
            if role not in socio._shrimp_addable_roles():
                raise ValidationError(_("Ese perfil no se puede agregar a una cuenta."))
            if not site_allows_role(role):
                raise ValidationError(offsite_role_message(role))
            if role not in self._allowed_user_types():
                raise ValidationError(_(
                    "El perfil «%s» se agrega desde su propia plataforma (la misma "
                    "en la que se registra): entra allí con tu cuenta y agrégalo "
                    "desde «Mi cuenta».") % socio._shrimp_type_label(role))
            post = dict(post, role=role)
            # El anexo del perfil se acepta al agregarlo (lo acepta el
            # administrador en nombre de la empresa).
            anexo = request.env["shrimp.legal.document"].sudo()._shrimp_annex(role)
            versiones, faltan, viejas = legal_versions_from_post(post, anexo)
            if faltan or viejas:
                raise ValidationError(legal_error_message(faltan, viejas))
            with request.env.cr.savepoint():
                linea = socio._shrimp_request_role(role, self._profile_vals(role, post))
                self._save_certificate_lines(socio, role, post)
                if versiones:
                    request.env["shrimp.legal.acceptance"].sudo()._shrimp_record(
                        request.env.user, versiones, "profile", ip=client_ip(),
                        user_agent=request_user_agent(), company=socio)
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
        return [{"ref": c.uuid_ref, "name": c.name, "issuer": c.issuer,
                 "required": bool(c.required_at_signup) and c.role == role} for c in certs]



# Rutas que no pasan por la aceptación de los documentos legales (ir.http):
# las páginas legales, la propia aceptación, los derechos del titular, la
# salida y lo técnico (API, JSON, estáticos).
LEGAL_EXEMPT_PREFIXES = (
    "/privacy", "/legal/", "/cookie-policy", "/my/privacy/accept", "/my/legal/", "/my/data",
    "/web/session/logout", "/web/login", "/web/", "/api/", "/website/", "/websocket",
    "/longpolling",
)
# Nombre anterior (compatibilidad).
PRIVACY_EXEMPT_PREFIXES = LEGAL_EXEMPT_PREFIXES

_LEGAL_CACHE = "_shrimp_legal_pending"
_DISMISS_KEY = "shrimp_legal_dismissed"


def legal_enforced():
    """La puerta de aceptación (y la exigencia del anexo al activar un
    perfil) está apagada durante los tests de Odoo salvo que el parámetro
    shrimp_user_registry.privacy_gate_in_tests valga 1: los usuarios que
    crean los tests no tienen aceptaciones registradas."""
    if odoo.modules.module.current_test:
        return request.env["ir.config_parameter"].sudo().get_param(
            "shrimp_user_registry.privacy_gate_in_tests") == "1"
    return True


def legal_pending(user=None):
    """Documentos pendientes del usuario de la petición (memo por petición)."""
    user = user or request.env.user
    memo = getattr(request, _LEGAL_CACHE, None)
    if memo is not None and memo[0] == user.id:
        return memo[1]
    res = request.env["shrimp.legal.document"].sudo()._shrimp_pending(user)
    try:
        setattr(request, _LEGAL_CACHE, (user.id, res))
    except Exception:  # noqa: BLE001
        pass
    return res


def legal_gate_needed(user):
    """True si `user` (portal) tiene documentos MAYORES sin aceptar."""
    if not user or user._is_public() or user._is_internal() or user._is_superuser():
        return False
    pend = legal_pending(user)
    return bool(pend["major"] or pend["company_major"])


def privacy_consent_pending(user):
    """Compatibilidad: True si el usuario tiene que pasar por la pantalla de
    aceptación (versiones mayores pendientes)."""
    return legal_gate_needed(user)


def legal_annex_pending(company, role):
    """True si la empresa no puede usar `role` sin aceptar antes su anexo:
    nunca lo aceptó o hay una versión MAYOR posterior."""
    if not role or not company:
        return False
    anexo = request.env["shrimp.legal.document"].sudo()._shrimp_annex(role)
    if not anexo or not anexo.current_version_id:
        return False
    return anexo._shrimp_status(request.env.user, company)["status"] in ("major", "none")


class ShrimpDatosPersonales(http.Controller):
    """Documentos legales públicos, aceptación y derechos del titular (LOPDP)."""

    # ------------------------------------------------------------------
    # Páginas públicas
    # ------------------------------------------------------------------
    _SLUGS = {"privacy": "privacy", "terms": "terms", "cookies": "cookies", "contract": "contract"}

    def _doc(self, doc_type, role=None):
        Doc = request.env["shrimp.legal.document"].sudo()
        if doc_type == "annex":
            doc = Doc._shrimp_annex(role)
        else:
            doc = Doc.search([("doc_type", "=", doc_type), ("active", "=", True)], limit=1)
        if not doc:
            raise NotFound()
        return doc

    def _render_doc(self, doc, version=None):
        version = version or doc.current_version_id
        if not version:
            raise NotFound()
        return request.render("shrimp_user_registry.pagina_documento_legal", {
            "doc": doc, "version": version, "is_current": version == doc.current_version_id,
            "dpo_email": self._dpo_email(),
            "annexes": request.env["shrimp.legal.document"].sudo().search(
                [("doc_type", "=", "annex"), ("active", "=", True)]) if doc.doc_type == "contract" else [],
        })

    def _render_history(self, doc):
        versiones = doc.version_ids.filtered(lambda v: v.state in ("published", "superseded")).sorted(
            lambda v: (v.published_date, v.id), reverse=True)
        return request.render("shrimp_user_registry.pagina_historial_legal", {
            "doc": doc, "versions": versiones})

    def _version_or_404(self, doc, version):
        v = doc.version_ids.filtered(lambda x: x.name == version and x.state in ("published", "superseded"))[:1]
        if not v:
            raise NotFound()
        return v

    @http.route("/privacy", type="http", auth="public", website=True, sitemap=True)
    def privacidad(self, **kw):
        return self._render_doc(self._doc("privacy"))

    # /legal/terms y no /terms: el módulo account del núcleo ya usa /terms.
    @http.route("/legal/terms", type="http", auth="public", website=True, sitemap=True)
    def terminos(self, **kw):
        return self._render_doc(self._doc("terms"))

    @http.route("/legal/cookies", type="http", auth="public", website=True, sitemap=True)
    def cookies(self, **kw):
        return self._render_doc(self._doc("cookies"))

    @http.route("/legal/contract", type="http", auth="public", website=True, sitemap=True)
    def contrato(self, **kw):
        return self._render_doc(self._doc("contract"))

    @http.route("/legal/contract/<string:role>", type="http", auth="public", website=True, sitemap=False)
    def anexo(self, role, **kw):
        return self._render_doc(self._doc("annex", role))

    @http.route(["/privacy/history", "/legal/<string:slug>/history",
                 "/legal/contract/history"], type="http", auth="public", website=True, sitemap=False)
    def historial(self, slug="contract", **kw):
        if request.httprequest.path.rstrip("/").endswith("/privacy/history"):
            slug = "privacy"
        if slug not in self._SLUGS:
            raise NotFound()
        return self._render_history(self._doc(slug))

    @http.route(["/privacy/history/<string:version>", "/legal/<string:slug>/history/<string:version>",
                 "/legal/contract/history/<string:version>"],
                type="http", auth="public", website=True, sitemap=False)
    def version_antigua(self, version, slug="contract", **kw):
        if request.httprequest.path.startswith("/privacy/") or "/privacy/history/" in request.httprequest.path:
            slug = "privacy"
        if slug not in self._SLUGS:
            raise NotFound()
        doc = self._doc(slug)
        return self._render_doc(doc, self._version_or_404(doc, version))

    @http.route("/legal/contract/<string:role>/history", type="http", auth="public", website=True,
                sitemap=False)
    def historial_anexo(self, role, **kw):
        return self._render_history(self._doc("annex", role))

    @http.route("/legal/contract/<string:role>/history/<string:version>", type="http", auth="public",
                website=True, sitemap=False)
    def version_antigua_anexo(self, role, version, **kw):
        doc = self._doc("annex", role)
        return self._render_doc(doc, self._version_or_404(doc, version))

    # La política de cookies nativa de Odoo (/cookie-policy, la enlaza la
    # barra de cookies): en los sitios de la plataforma se lleva a la nuestra.
    @http.route("/cookie-policy", type="http", auth="public", website=True, sitemap=False)
    def cookie_policy(self, **kw):
        website = getattr(request, "website", None)
        if website is None or "shrimp_platform" not in website._fields or website.sudo().shrimp_platform:
            return request.redirect("/legal/cookies", code=301)
        pagina = request.env["ir.http"]._serve_page()
        if not pagina:
            raise NotFound()
        return pagina

    # ------------------------------------------------------------------
    # Mis datos (derechos del titular) e historial de aceptaciones
    # ------------------------------------------------------------------
    def _my_acceptances(self):
        user = request.env.user
        Acc = request.env["shrimp.legal.acceptance"].sudo()
        empresa = user.sudo()._shrimp_account_company()
        dominio = [("user_id", "=", user.id)]
        if user.sudo()._shrimp_is_account_admin():
            # El administrador ve todo lo de su empresa (también lo que
            # aceptaron sus subusuarios).
            dominio = ["|", ("user_id", "=", user.id), ("commercial_partner_id", "=", empresa.id)]
        else:
            dominio = ["|", ("user_id", "=", user.id), "&",
                       ("commercial_partner_id", "=", empresa.id), ("document_id.scope", "=", "company")]
        return Acc.search(dominio, order="accepted_at desc, id desc", limit=200)

    def _my_data_requests(self):
        return request.env["shrimp.data.request"].sudo().search(
            [("user_id", "=", request.env.user.id)], order="id desc", limit=50)

    @http.route("/my/data", type="http", auth="user", website=True)
    def mis_datos(self, **kw):
        socio = request.env.user.partner_id.sudo()
        user = request.env.user.sudo()
        return request.render("shrimp_user_registry.pagina_mis_datos", {
            "socio": socio, "dpo_email": self._dpo_email(), "enviado": kw.get("ok") == "1",
            "limitado": kw.get("ok") == "limit",
            "acceptances": self._my_acceptances(),
            "pending": legal_pending(),
            "data_requests": self._my_data_requests(),
            "is_account_admin": user._shrimp_is_account_admin(),
            "company": user._shrimp_account_company(),
        })

    @http.route("/my/data/export", type="http", auth="user", website=True, methods=["POST"],
                csrf=True, sitemap=False)
    def exportar_datos(self, scope=None, **kw):
        """Portabilidad: descarga inmediata (ZIP con JSON y CSV) de los datos
        del titular. Un subusuario descarga sus propios datos; el
        administrador puede pedir también los de la cuenta (empresa).
        Queda registrada como solicitud de portabilidad atendida."""
        user = request.env.user
        if not throttle("data_export:%s" % user.id,
                        "shrimp_user_registry.data_export_rate_limit",
                        "shrimp_user_registry.data_export_rate_window", 5, 3600):
            return request.redirect("/my/data?ok=limit")
        usuario = user.sudo()
        alcance = "company" if (scope == "company" and usuario._shrimp_is_account_admin()) else "own"
        empresa = usuario._shrimp_account_company() or usuario.partner_id
        objetivo = empresa if alcance == "company" else usuario.partner_id
        contenido, nombre = objetivo.sudo()._shrimp_lopdp_export_zip(user=usuario, scope=alcance)
        Req = request.env["shrimp.data.request"].sudo()
        req = Req._shrimp_from_portal(usuario, "portabilidad", _("Descarga directa desde Mis datos."),
                                      origin="self_export")
        req.write({"subject_scope": alcance, "state": "done", "response_date": fields.Datetime.now(),
                   "response": Markup("<p>%s</p>") % _("Copia descargada por el titular (%s).") % nombre})
        req.message_post(body=_("El titular descargó la copia de sus datos (%s).") % nombre,
                         subtype_xmlid="mail.mt_note")
        return request.make_response(contenido, headers=binary_headers(
            contenido, "application/zip", filename=nombre, download=True))

    @http.route("/my/legal/receipt/<string:ref>", type="http", auth="user", website=True,
                sitemap=False)
    def comprobante(self, ref, **kw):
        acc = request.env["shrimp.legal.acceptance"].sudo().resolve_ref(ref)
        # Solo los de su propia empresa (y, si no administra, los suyos o los
        # de la empresa): cualquier otro código da 404, no 403.
        if not acc or acc not in self._my_acceptances():
            raise NotFound()
        datos = acc.receipt_content()
        if not datos:
            raise NotFound()
        contenido, mime, nombre = datos
        return request.make_response(contenido, headers=binary_headers(
            contenido, mime, filename=nombre, download=kw.get("download") == "1"))

    @http.route("/my/data/request", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def solicitar(self, tipo=None, detalle=None, **kw):
        """Registra la solicitud (acceso, rectificación, eliminación, portabilidad
        u oposición) como un registro shrimp.data.request con plazo legal,
        la anota en el historial de la cuenta y avisa al responsable (correo
        en cola al DPO si está configurado; si no, actividad a los
        administradores con el aviso de que falta).

        Lo que escribe el usuario se guarda como texto y se escapa al pintarlo
        (Markup % ...). Hay un límite de solicitudes por usuario.
        """
        tipos = {"acceso", "rectificacion", "eliminacion", "portabilidad", "oposicion"}
        if tipo not in tipos:
            return request.redirect("/my/data")
        user = request.env.user
        if not throttle("data_request:%s" % user.id,
                        "shrimp_user_registry.data_request_rate_limit",
                        "shrimp_user_registry.data_request_rate_window", 5, 3600):
            return request.redirect("/my/data?ok=limit")
        request.env["shrimp.data.request"].sudo()._shrimp_from_portal(user, tipo, detalle)
        return request.redirect("/my/data?ok=1")

    # ------------------------------------------------------------------
    # Aceptación de versiones nuevas (pantalla de acceso, aviso, perfil)
    # ------------------------------------------------------------------
    def _accept_items(self, activate=None):
        """(a aceptar [status], obligatorios [doc], bloqueados por la empresa [status])."""
        user = request.env.user
        pend = legal_pending(user)
        items = list(pend["major"]) + list(pend["minor"])
        obligatorios = [st["document"] for st in pend["major"]]
        if activate:
            empresa = user.sudo()._shrimp_account_company()
            anexo = request.env["shrimp.legal.document"].sudo()._shrimp_annex(activate)
            if anexo and anexo.current_version_id and user.sudo()._shrimp_is_account_admin():
                st = anexo._shrimp_status(user, empresa)
                if st["status"] != "ok":
                    if anexo not in [i["document"] for i in items]:
                        items.insert(0, st)
                    if anexo not in obligatorios:
                        obligatorios.append(anexo)
        return items, obligatorios, pend["company_major"]

    @http.route("/my/legal/accept", type="http", auth="user", website=True,
                methods=["GET", "POST"], sitemap=False)
    def aceptar_documentos(self, redirect=None, activate=None, source=None, **post):
        destino = ShrimpRegistryController._safe_next(redirect, "/my")
        if destino.startswith(("/my/legal/accept", "/my/privacy/accept")):
            destino = "/my"
        activate = (activate or "").strip() or None
        if activate and not site_allows_role(activate):
            # El anexo de un perfil de otro sitio se acepta en ese sitio.
            activate = None
        items, obligatorios, bloqueo = self._accept_items(activate)
        ctx = {"items": items, "required_ids": [d.id for d in obligatorios],
               "company_blocked": bloqueo, "redirect": destino, "activate": activate,
               "source": source or "", "is_admin": is_account_admin()}
        if request.httprequest.method == "POST":
            docs = request.env["shrimp.legal.document"].sudo().browse([i["document"].id for i in items])
            versiones, faltan, viejas = legal_versions_from_post(post, docs)
            faltan_oblig = [d for d in faltan if d in obligatorios]
            if faltan_oblig or viejas:
                ctx["error"] = legal_error_message(faltan_oblig, viejas)
                return request.render("shrimp_user_registry.pagina_aceptar_documentos", ctx)
            if versiones:
                canal = "profile" if activate else ("banner" if source == "banner" else "login_gate")
                request.env["shrimp.legal.acceptance"].sudo()._shrimp_record(
                    request.env.user, versiones, canal, ip=client_ip(),
                    user_agent=request_user_agent())
                try:
                    delattr(request, _LEGAL_CACHE)
                except AttributeError:
                    pass
            if activate:
                try:
                    current_partner().sudo()._shrimp_set_active_role(activate)
                except (UserError, ValidationError) as e:
                    sep = "&" if "?" in destino else "?"
                    return request.redirect("%s%serror=%s" % (
                        destino, sep, flash_message(e.args[0] if e.args else "")))
            return request.redirect(destino)
        if not items and not bloqueo:
            if activate:
                # Nada pendiente: se activa directamente (por POST desde la barra).
                return request.redirect(destino)
            return request.redirect(destino)
        return request.render("shrimp_user_registry.pagina_aceptar_documentos", ctx)

    # Ruta anterior (19.0.1.5.0): queda como alias.
    @http.route("/my/privacy/accept", type="http", auth="user", website=True,
                methods=["GET", "POST"], sitemap=False, csrf=False)
    def aceptar_privacidad(self, redirect=None, **kw):
        destino = ShrimpRegistryController._safe_next(redirect, "/my")
        return request.redirect("/my/legal/accept?%s" % url_encode({"redirect": destino}), code=303)

    @http.route("/my/legal/dismiss", type="http", auth="user", website=True, methods=["POST"],
                csrf=True, sitemap=False)
    def descartar_aviso(self, redirect=None, **kw):
        """«Recordármelo luego»: oculta el aviso de cambios MENORES en esta sesión."""
        pend = legal_pending()
        request.session[_DISMISS_KEY] = sorted(st["version"].id for st in pend["minor"])
        return request.redirect(ShrimpRegistryController._safe_next(redirect, "/my"))

    @staticmethod
    def _dpo_email():
        """Correo del responsable de datos (Ajustes › CamaronMarket) o False:
        nunca una dirección inventada."""
        return (request.env["ir.config_parameter"].sudo().get_param("shrimp.dpo_email") or "").strip() or False


class ShrimpTeamController(http.Controller):
    """Usuarios de la cuenta de empresa (/my/team): solo el administrador."""

    PERMS = ("view", "buy", "sell", "approve", "billing")

    def _admin_context(self):
        user = request.env.user.sudo()
        empresa = user._shrimp_account_company()
        if not empresa or not empresa.shrimp_user_type:
            raise Forbidden()
        miembro = user._shrimp_member()
        if not miembro and user.partner_id == empresa:
            # Cuenta anterior al sistema de subusuarios: su dueño es el admin.
            miembro = request.env["shrimp.account.member"].sudo()._shrimp_ensure(
                user, empresa, is_admin=True)
        if not miembro or not miembro.is_admin:
            raise Forbidden()
        return empresa, miembro

    def _member_of(self, empresa, ref):
        m = request.env["shrimp.account.member"].sudo().with_context(
            active_test=False).resolve_ref(ref)
        if not m or m.company_id != empresa:
            raise NotFound()
        return m

    def _perms_from_post(self, empresa, post, prefix="perm"):
        perms = {}
        for role in empresa._shrimp_roles(include_pending=True):
            elegidos = {p for p in self.PERMS if post.get("%s_%s_%s" % (prefix, role, p))}
            if elegidos:
                perms[role] = elegidos | {"view"}
        return perms

    @staticmethod
    def _manage_from_post(post, prefix="perm"):
        """Permiso de cuenta «Gestionar cuenta» (CR-12)."""
        return bool(post.get("%s_manage_account" % prefix))

    def _redirect(self, **params):
        return request.redirect("/my/team" + ("?" + url_encode(params) if params else ""))

    @http.route("/my/team", type="http", auth="user", website=True, sitemap=False)
    def team(self, **kw):
        empresa, yo = self._admin_context()
        miembros = request.env["shrimp.account.member"].sudo().with_context(
            active_test=False).search([("company_id", "=", empresa.id)])
        Acc = request.env["shrimp.legal.acceptance"].sudo()
        actividad = {m.id: Acc.search_count([("user_id", "=", m.user_id.id)]) for m in miembros}
        return request.render("shrimp_user_registry.pagina_mi_equipo", {
            "empresa": empresa, "yo": yo, "miembros": miembros,
            "roles": [(r, legal_profile_label(r)) for r in empresa._shrimp_roles(include_pending=True)],
            "perm_labels": [("view", "Ver"), ("buy", "Comprar"), ("sell", "Vender"),
                            ("approve", "Aprobar"), ("billing", "Facturación")],
            # Lo que tendrá el administrador actual si transfiere la
            # administración (D-37): por defecto, todo lo delegable.
            "prev_perms": yo._shrimp_full_permissions(),
            "actividad": actividad, "saved": kw.get("saved"),
            "error": MESSAGE_TEXTS.get(kw.get("error"), kw.get("error")) if kw.get("error") else None,
        })

    @http.route("/my/team/invite", type="http", auth="user", website=True, methods=["POST"],
                csrf=True, sitemap=False)
    def team_invite(self, **post):
        empresa, yo = self._admin_context()
        if not (throttle("team:%s" % empresa.id, "shrimp_user_registry.team_rate_limit",
                         "shrimp_user_registry.team_rate_window", 20, 3600)):
            return self._redirect(error="rate_limited")
        nombre = _normalize_text(post.get("name"))
        correo = _normalize_email(post.get("email"))
        if not nombre:
            return self._redirect(error="name_required")
        if not correo or not EMAIL_RE.match(correo):
            return self._redirect(error="email_invalid")
        Users = request.env["res.users"].sudo().with_context(active_test=False)
        if Users.search_count([("login", "=", correo)]):
            return self._redirect(error="email_taken")
        perms = self._perms_from_post(empresa, post)
        try:
            with request.env.cr.savepoint():
                contacto = request.env["res.partner"].sudo().create({
                    "name": nombre, "email": correo, "parent_id": empresa.id, "type": "contact",
                    "phone": _normalize_text(post.get("phone")) or False,
                })
                campo = "group_ids" if "group_ids" in Users._fields else "groups_id"
                # auth_signup envía la invitación (enlace con token para que
                # el invitado fije su contraseña).
                usuario = Users.create({
                    "name": nombre, "login": correo, "email": correo, "partner_id": contacto.id,
                    campo: [(6, 0, [request.env.ref("base.group_portal").id])],
                })
                miembro = request.env["shrimp.account.member"].sudo().create({
                    "company_id": empresa.id, "user_id": usuario.id, "is_admin": False,
                    "invited_by_id": request.env.user.id, "invite_date": fields.Datetime.now(),
                })
                miembro._set_permissions(perms, manage_account=self._manage_from_post(post))
                empresa.message_post(body=_("%(a)s invitó a %(n)s (%(c)s) como usuario de la cuenta.") % {
                    "a": request.env.user.name, "n": nombre, "c": correo})
        except (UserError, ValidationError) as e:
            return self._redirect(error=flash_message(e.args[0] if e.args else ""))
        return self._redirect(saved="invited")

    @http.route("/my/team/<string:ref>/permissions", type="http", auth="user", website=True,
                methods=["POST"], csrf=True, sitemap=False)
    def team_permissions(self, ref, **post):
        empresa, yo = self._admin_context()
        m = self._member_of(empresa, ref)
        if m.is_admin:
            return self._redirect(error="generic")
        m._set_permissions(self._perms_from_post(empresa, post),
                           manage_account=self._manage_from_post(post))
        return self._redirect(saved="permissions")

    @http.route("/my/team/<string:ref>/toggle", type="http", auth="user", website=True,
                methods=["POST"], csrf=True, sitemap=False)
    def team_toggle(self, ref, **post):
        empresa, yo = self._admin_context()
        m = self._member_of(empresa, ref)
        if m == yo or m.is_admin:
            return self._redirect(error="generic")
        m._shrimp_set_active(not m.active)
        return self._redirect(saved="state")

    @http.route("/my/team/<string:ref>/make-admin", type="http", auth="user", website=True,
                methods=["POST"], csrf=True, sitemap=False)
    def team_make_admin(self, ref, **post):
        empresa, yo = self._admin_context()
        m = self._member_of(empresa, ref)
        if m == yo or not m.active or not (post.get("confirm") or "").strip():
            return self._redirect(error="generic")
        # D-37: con qué permisos se queda el administrador saliente. El
        # formulario los manda marcados por defecto (todo lo delegable); si
        # llega sin el bloque (formulario viejo, POST a mano) se aplica ese
        # mismo valor por defecto.
        if post.get("prev_custom"):
            previos = self._perms_from_post(empresa, post, prefix="prev")
            gestionar = self._manage_from_post(post, prefix="prev")
        else:
            previos, gestionar = None, None
        try:
            m._shrimp_make_admin(previous_permissions=previos, previous_manage_account=gestionar)
        except (UserError, ValidationError) as e:
            return self._redirect(error=flash_message(e.args[0] if e.args else ""))
        return request.redirect("/my")

    @http.route("/my/team/<string:ref>/resend", type="http", auth="user", website=True,
                methods=["POST"], csrf=True, sitemap=False)
    def team_resend(self, ref, **post):
        empresa, yo = self._admin_context()
        m = self._member_of(empresa, ref)
        if not m.active or m == yo:
            return self._redirect(error="generic")
        try:
            m.user_id.sudo().with_context(create_user=True).action_reset_password()
        except Exception as e:  # noqa: BLE001 - sin servidor de correo, etc.
            _logger.warning("No se pudo reenviar la invitación a %s: %s", m.login, e)
            return self._redirect(error="generic")
        return self._redirect(saved="resent")
