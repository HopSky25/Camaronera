"""Inicio y cierre de sesión de la app móvil.

La app no abre una sesión web de Odoo: el usuario entra con su correo y su
contraseña del portal y a cambio recibe una clave de API normal (la misma
que crearía a mano en «Integraciones (API)»), que es lo que usa en adelante.
Así la app pasa por el mismo camino que cualquier integración: scopes,
caducidad, revocación y la página del portal donde el socio ve sus claves.

* La contraseña se comprueba con ``res.users._login`` (lo que hace
  /web/session/authenticate), sin guardar nada en la sesión.
* Solo usuarios de portal activos; un usuario interno recibe 403, igual que
  cuando intenta usar una clave.
* Mensajes genéricos: no se distingue «no existe» de «contraseña mala».
* Como máximo LOGIN_MAX_FAILURES intentos fallidos por (IP, login) en
  LOGIN_WINDOW segundos, contados en ``shrimp.rate.limit`` (la misma tabla
  que usa el registro web). El intento fallido se guarda aunque la respuesta
  sea un error (ApiError con keep_changes).
"""

import logging
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessDenied
from odoo.http import request

from ..models.api_scope import SCOPE_CODES
from . import serializers as S
from .framework import ApiError, F, api_route, invalid, iso_dt, obj
from .portal import MAX_ACTIVE_KEYS

_logger = logging.getLogger(__name__)

TAG_AUTH = "sesión (app móvil)"

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = 15 * 60          # segundos
LOGIN_KEY_DAYS = 365
APP_KEY_PREFIX = "App móvil – "

# Lo que la app necesita: todas las lecturas y las escrituras que hace un
# socio desde el móvil. Nunca webhooks:manage (eso es para servidores).
APP_WRITE_SCOPES = (
    "profile:write", "verifications:write", "dispatch:write", "harvest:write",
    "pricelists:write", "copack:write", "transactions:write", "products:write",
    "facilities:write", "lots:write", "exports:write",
)


def app_scopes():
    return sorted(set(c for c in SCOPE_CODES if c.endswith(":read")) | set(APP_WRITE_SCOPES))


LOGIN_SPEC = {
    "login": F("string", required=True, max_length=255,
               description="Correo (login) del usuario del portal", example="socio@finca.ec"),
    "password": F("string", required=True, max_length=1024, description="Contraseña del portal"),
    "device": F("string", max_length=64,
                description="Nombre del dispositivo; se usa para nombrar la clave "
                            "(«App móvil – <device>»)", example="Pixel 7 de Juan"),
}

LOGIN_RESPONSE = obj(api_key={"type": "string", "description": "Clave trz_… Solo se devuelve aquí: "
                                                               "guárdala en el almacén seguro del móvil."},
                     expires_at="datetime",
                     device_required={"type": "boolean",
                                      "description": "Siempre true: la clave es de la app y las "
                                                     "operaciones sensibles exigen registrar el "
                                                     "dispositivo (POST /me/devices) y firmarlas."},
                     me={"type": "object", "description": "Lo mismo que devuelve GET /me con esa clave."})


def _throttle_key(ip, login):
    return "api-login:%s:%s" % ((ip or "?")[:45], (login or "").strip().lower()[:150])


def _failures(ip, login):
    """Filas de intentos fallidos aún vigentes para (IP, login)."""
    Limit = request.env["shrimp.rate.limit"].sudo()
    desde = fields.Datetime.now() - timedelta(seconds=LOGIN_WINDOW)
    return Limit.search([("key", "=", _throttle_key(ip, login)), ("ts", ">=", desde)], order="ts asc")


def _reject(ip, login, why):
    """401 genérico; deja constancia del fallo (sobrevive al savepoint)."""
    _logger.warning("API login fallido para «%s» desde %s (%s)", login, ip or "?", why)
    request.env["shrimp.rate.limit"].sudo().create({"key": _throttle_key(ip, login)})
    raise ApiError(401, "invalid-credentials", "Correo o contraseña incorrectos.", keep_changes=True)


@api_route("POST", "/auth/login", public=True, tags=[TAG_AUTH], body=LOGIN_SPEC,
           response=LOGIN_RESPONSE,
           summary="Inicio de sesión de la app móvil: correo y contraseña → clave de API",
           description="Comprueba la contraseña del usuario del portal y emite una clave de API "
                       "de %s días con los scopes de la app (todas las lecturas y las escrituras "
                       "que hace un socio; nunca webhooks:manage). No hace falta Idempotency-Key. "
                       "Errores: 401 invalid-credentials (correo o contraseña incorrectos, sin "
                       "distinguir cuál), 403 user-not-allowed (usuario interno o inactivo), "
                       "403 account-not-approved (la cuenta no tiene ningún perfil), "
                       "403 mfa-not-supported (el usuario tiene verificación en dos pasos), "
                       "429 rate-limited (más de %s intentos fallidos en %s minutos desde la "
                       "misma IP para el mismo correo). La clave es «de la app» "
                       "(device_required: true): antes de una operación sensible hay que registrar "
                       "el dispositivo (POST /me/devices) y firmar la petición."
                       % (LOGIN_KEY_DAYS, LOGIN_MAX_FAILURES, LOGIN_WINDOW // 60))
def auth_login(ctx):
    vals = ctx.clean(LOGIN_SPEC)
    login = vals["login"]
    # La contraseña se toma tal cual (clean recorta espacios y una contraseña
    # puede llevarlos).
    password = ctx.body.get("password")
    if not vals.get("password") or not isinstance(password, str):
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": "password", "message": "Es obligatorio."}])
    device = vals.get("device") or "dispositivo"
    ip = ctx.remote_addr or ""

    fallos = _failures(ip, login)
    if len(fallos) >= LOGIN_MAX_FAILURES:
        retry = int((fallos[0].ts + timedelta(seconds=LOGIN_WINDOW) - fields.Datetime.now()).total_seconds())
        _logger.warning("API login bloqueado para «%s» desde %s: %s fallos en %s min",
                        login, ip or "?", len(fallos), LOGIN_WINDOW // 60)
        raise ApiError(429, "rate-limited",
                       "Demasiados intentos fallidos. Espera %s minutos antes de volver a intentarlo."
                       % max(1, -(-retry // 60)),
                       headers=[("Retry-After", str(max(1, retry)))])

    Users = request.env["res.users"]
    credential = {"login": login, "password": password, "type": "password"}
    try:
        # Igual que /web/session/authenticate (res.users.authenticate -> _login),
        # pero sin tocar la sesión: la app no la usa. "interactive" indica que
        # es una contraseña, no una clave RPC de res.users.apikeys.
        auth_info = Users._login(credential, {"interactive": True})
    except AccessDenied as e:
        _reject(ip, login, e.args[0] if e.args else "AccessDenied")
    user = Users.sudo().browse(auth_info["uid"])
    if not user.exists() or not user.active or not user.share:
        _logger.warning("API login rechazado para «%s» desde %s: usuario interno o inactivo", login, ip or "?")
        raise ApiError(403, "user-not-allowed",
                       "Este usuario no puede usar la API externa (inactivo o interno). "
                       "La app es para socios del portal.")
    if auth_info.get("mfa") != "skip" and user._mfa_url():
        raise ApiError(403, "mfa-not-supported",
                       "Tu usuario tiene verificación en dos pasos y la app todavía no la admite. "
                       "Crea una clave de API desde el portal (Mi cuenta → Integraciones).")
    partner = user.partner_id.sudo()
    titular = partner._shrimp_role_holder()
    if not titular.shrimp_user_type and not titular.shrimp_role_ids:
        raise ApiError(403, "account-not-approved",
                       "Tu cuenta no tiene ningún perfil (camaronera, laboratorio, empacadora…). "
                       "Completa el registro en el portal o pide a la administración que te asigne uno.")

    Key = request.env["shrimp.api.key"].sudo()
    activas = Key.search([("user_id", "=", user.id), ("key_prefix", "!=", False),
                          ("revoked_at", "=", False)], order="id asc")
    activas = activas.filtered(lambda k: k.status == "active")
    # El portal limita a MAX_ACTIVE_KEYS claves vivas. Si la app entra una y
    # otra vez (reinstalaciones, varios teléfonos) se revocan primero sus
    # propias claves más antiguas; las que el socio creó a mano no se tocan.
    while len(activas) >= MAX_ACTIVE_KEYS:
        vieja = activas.filtered(lambda k: k.is_app_key or (k.name or "").startswith(APP_KEY_PREFIX))[:1]
        if not vieja:
            raise ApiError(409, "too-many-keys",
                           "Tienes %s claves de API activas. Revoca alguna en el portal "
                           "(Mi cuenta → Integraciones) para poder entrar desde la app." % MAX_ACTIVE_KEYS)
        vieja.action_revoke()
        activas -= vieja

    expires_at = fields.Datetime.now() + timedelta(days=LOGIN_KEY_DAYS)
    key, secret = Key.api_create_key(user, APP_KEY_PREFIX + device, app_scopes(), expires_at=expires_at)
    # Lo que ve el socio en el portal como "último uso" de esta clave. Es
    # clave de la app: las rutas sensibles exigirán dispositivo y firma
    # (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md).
    key.write({"last_used": fields.Datetime.now(), "last_ip": ip[:64] or False,
               "is_app_key": True})
    # Un inicio correcto limpia el contador de fallos de este (IP, login),
    # también los ya fuera de la ventana (no tiene sentido conservarlos).
    request.env["shrimp.rate.limit"].sudo().search([("key", "=", _throttle_key(ip, login))]).unlink()
    _logger.info("API login correcto para «%s» desde %s: clave trz_%s (%s)",
                 login, ip or "?", key.key_prefix, key.name)
    return {
        "api_key": secret,
        "expires_at": iso_dt(key.expires_at),
        "device_required": True,
        "me": S.me(user, key),
    }


@api_route("POST", "/auth/logout", tags=[TAG_AUTH], response=obj(revoked="boolean"),
           summary="Cierra la sesión de la app: revoca la clave con la que se llama",
           description="Mismo efecto que «Revocar» en el portal: la clave deja de servir en el "
                       "acto. No exige scope. La respuesta es la misma si la clave ya estaba "
                       "revocada (una segunda llamada recibiría 401 revoked-api-key).")
def auth_logout(ctx):
    key = ctx.key.sudo()
    key.action_revoke()
    _logger.info("API logout: clave trz_%s revocada por %s desde %s",
                 key.key_prefix, ctx.user.login, ctx.remote_addr or "?")
    return {"revoked": True}
