"""Modo supervisor de la app (contrato docs/app/CONTRATO-FIRMA-DISPOSITIVO.md,
Anexo B).

Un usuario INTERNO con el grupo «Supervisor de la app» entra a la app con su
correo y contraseña (POST /auth/login) y recibe una clave de SUPERVISOR: 8 h,
de la app (registra su dispositivo como cualquiera), sin ningún scope de
datos. Con ella solo puede:

* ``GET /supervisor/partners``: directorio de socios aprobados con usuario de
  portal activo (B.2);
* ``POST /supervisor/act-as``: emitir, FIRMADO por el dispositivo de esa
  clave de supervisor, una clave de SOLO LECTURA del usuario de portal de un
  socio (B.3), con la que la app ve la cuenta del socio con las mismas
  reglas de acceso del portal.

La clave de «actuar como» pertenece al usuario de portal del socio, lleva
``supervisor_user_id`` y ``supervisor_key_id``, solo scopes ``*:read``,
caduca a las 8 h (como mucho cuando caduque la de supervisor) y no tiene
dispositivo propio. Revocar la clave de supervisor (o cerrar su sesión)
revoca en cascada todas las que emitió (shrimp.api.key.action_revoke).

Auditoría: cada act-as es una operación firmada, así que queda en
``shrimp.api.signed.operation`` (usuario = el supervisor, dispositivo, ruta,
firma, IP…) con dos campos propios: ``act_as_partner_id`` (el socio visto) y
``act_as_key_id`` (la clave emitida). Además, una nota en el historial del
socio.
"""

import logging

from markupsafe import Markup

from odoo import fields

from . import serializers as S
from .framework import (SUPERVISOR_ONLY, ApiError, ApiResponse, F, api_route, iso_dt, obj,
                        paginate)

_logger = logging.getLogger(__name__)

TAG = "modo supervisor (app móvil)"
TZ = "America/Guayaquil"

PARTNER_SCHEMA = obj(id="uuid", name="string", type="string", roles={"type": "array",
                                                                      "items": {"type": "string"}},
                     city="string", province="string")

DIRECTORY_PARAMS = {
    "q": F("string", description="Texto a buscar en el nombre, el correo o el RUC/cédula"),
    "type": F("string", description="Perfil aprobado del socio (semillero, laboratorio, camaronera, "
                                    "empacadora, maquilador, verificador…)"),
}

ACT_AS_SPEC = {
    "partner": F("string", required=True, max_length=64, description="uuid del socio",
                 example="6f1c2a8e-…"),
}

ACT_AS_RESPONSE = obj(
    api_key={"type": "string", "description": "Clave trz_… de SOLO LECTURA del usuario de portal "
                                              "del socio. Solo se devuelve aquí (un reintento con "
                                              "la misma Idempotency-Key la recibe a null)."},
    expires_at="datetime",
    partner=PARTNER_SCHEMA,
    me={"type": "object", "description": "Lo mismo que GET /me con esa clave."},
    read_only={"type": "boolean", "description": "Siempre true."},
)


# ---------------------------------------------------------------------------
# Dominios
# ---------------------------------------------------------------------------
def approved_domain():
    """Socios con algún perfil aprobado (o un perfil activo aprobado)."""
    return ["|", ("shrimp_role_ids", "any", [("state", "=", "approved")]),
            "&", ("shrimp_user_type", "!=", False), ("shrimp_account_state", "=", "approved")]


def portal_user_domain():
    """Con un usuario de portal activo y ningún usuario interno activo."""
    return [("user_ids", "any", [("share", "=", True), ("active", "=", True)]),
            ("user_ids", "not any", [("share", "=", False), ("active", "=", True)])]


def role_approved_domain(role):
    return ["|", "&", ("shrimp_user_type", "=", role), ("shrimp_account_state", "=", "approved"),
            ("shrimp_role_ids", "any", [("role", "=", role), ("state", "=", "approved")])]


# ---------------------------------------------------------------------------
# B.2 Directorio
# ---------------------------------------------------------------------------
@api_route("GET", "/supervisor/partners", supervisor=SUPERVISOR_ONLY, tags=[TAG], paginated=True,
           params=DIRECTORY_PARAMS, response=PARTNER_SCHEMA,
           summary="Directorio de socios para «actuar como» (solo clave de supervisor)",
           description="Socios con algún perfil aprobado y un usuario de portal activo (nunca "
                       "cuentas con usuario interno). `q` busca en el nombre, el correo y el "
                       "RUC/cédula; `type` filtra por perfil aprobado. Paginado por cursor como el "
                       "resto de la API (orden por fecha de modificación). `type` es el perfil "
                       "activo y `roles` todos los aprobados.")
def supervisor_partners(ctx):
    Partner = ctx.env["res.partner"].sudo()
    domain = approved_domain() + portal_user_domain()
    q = (ctx.params.get("q") or "").strip()
    if len(q) > 128:
        raise ApiError(400, "invalid-parameter", "«q» admite hasta 128 caracteres.")
    if q:
        domain += ["|", "|", ("name", "ilike", q), ("email", "ilike", q), ("vat_or_id", "ilike", q)]
    role = (ctx.params.get("type") or "").strip()
    if role:
        tipos = [v for v, _l in Partner._fields["shrimp_user_type"].selection]
        if role not in tipos:
            raise ApiError(400, "invalid-parameter", "«type» admite: %s." % ", ".join(tipos))
        domain += role_approved_domain(role)
    return paginate(ctx, "res.partner", domain, S.supervisor_partner, sudo=True)


# ---------------------------------------------------------------------------
# B.3 Actuar como (solo lectura)
# ---------------------------------------------------------------------------
@api_route("POST", "/supervisor/act-as", signed=True, supervisor=SUPERVISOR_ONLY,
           secret_fields=("api_key",), tags=[TAG], body=ACT_AS_SPEC, status=201,
           response=ACT_AS_RESPONSE,
           summary="Actuar como un socio, en solo lectura (solo clave de supervisor, firmada)",
           description="Emite una clave de SOLO LECTURA (todos los scopes *:read y ninguno de "
                       "escritura) del usuario de portal del socio: el primero activo por id. Dura "
                       "8 h, y como mucho lo que le quede a la clave de supervisor. No tiene "
                       "dispositivo propio ni registra uno: cualquier escritura con ella da 403 "
                       "insufficient-scope (salvo POST /auth/logout, que la revoca). Revocar la "
                       "clave de supervisor revoca las que emitió. Firmada por el dispositivo de "
                       "la clave de supervisor; exige Idempotency-Key. Deja una nota en el "
                       "historial del socio y queda en la auditoría de operaciones firmadas. "
                       "Errores: 404 not-found (el socio no existe), 403 act-as-internal-user (la "
                       "cuenta tiene un usuario interno), 422 partner-not-approved (sin perfil "
                       "aprobado), 422 partner-without-portal-user (sin usuario de portal activo).")
def supervisor_act_as(ctx):
    vals = ctx.clean(ACT_AS_SPEC)
    sup_key = ctx.key.sudo()
    # El despachador ya verificó la firma con el dispositivo de ESTA clave;
    # esto es la red por si alguien cambia la marca de la ruta.
    if not ctx.device or ctx.device.api_key_id != sup_key:
        raise ApiError(403, "signature-required",
                       "«Actuar como» exige la firma del dispositivo de la clave de supervisor.")
    Partner = ctx.env["res.partner"].sudo()
    ref = vals.get("partner") or ""
    partner = Partner.search([("uuid_ref", "=", ref)], limit=1) if ref else Partner
    if not partner:
        raise ApiError(404, "not-found", "El socio no existe.")
    users = partner.with_context(active_test=False).user_ids.sorted("id")
    if users.filtered(lambda u: u.active and not u.share):
        raise ApiError(403, "act-as-internal-user",
                       "Esa cuenta tiene un usuario interno: «actuar como» es solo para socios del "
                       "portal.")
    if not Partner.search_count([("id", "=", partner.id)] + approved_domain(), limit=1):
        raise ApiError(422, "partner-not-approved",
                       "El socio no tiene ningún perfil aprobado.")
    portal_user = users.filtered(lambda u: u.active and u.share)[:1]
    if not portal_user:
        raise ApiError(422, "partner-without-portal-user",
                       "El socio no tiene un usuario de portal activo: no hay cuenta que ver.")

    Key = ctx.env["shrimp.api.key"].sudo()
    # Una por (clave de supervisor, socio): la anterior deja de servir.
    previas = Key.search([("supervisor_key_id", "=", sup_key.id), ("user_id", "=", portal_user.id),
                          ("revoked_at", "=", False)])
    if previas:
        previas.action_revoke()
    key, secret = sup_key.api_create_act_as_key(portal_user)
    key.write({"last_used": fields.Datetime.now(), "last_ip": (ctx.remote_addr or "")[:64] or False})

    if ctx.signed_operation:
        ctx.signed_operation.with_context(shrimp_api_audit=True).write(
            {"act_as_partner_id": partner.id, "act_as_key_id": key.id})
    _note_act_as(ctx, partner, key)
    _logger.info("API: el supervisor %s actúa como %s (%s) con trz_%s, solo lectura, desde %s",
                 ctx.user.login, partner.name, portal_user.login, key.key_prefix,
                 ctx.remote_addr or "?")
    return ApiResponse({
        "api_key": secret,
        "expires_at": iso_dt(key.expires_at),
        "partner": S.supervisor_partner(partner),
        "me": S.me(portal_user, key),
        "read_only": True,
    }, status=201)


def _note_act_as(ctx, partner, key):
    """Nota interna en el historial del socio (no notifica a nadie)."""
    ahora = fields.Datetime.context_timestamp(partner.with_context(tz=partner.tz or TZ),
                                              fields.Datetime.now())
    body = Markup("<p>El supervisor <b>%s</b> vio tu cuenta desde la app (solo lectura) el %s.</p>"
                  "<ul><li>Clave de solo lectura: trz_%s_… (caduca el %s UTC)</li>"
                  "<li>Dispositivo: %s</li><li>IP: %s</li></ul>") % (
        ctx.user.name, ahora.strftime("%d/%m/%Y a las %H:%M"), key.key_prefix,
        fields.Datetime.to_string(key.expires_at), ctx.device.name if ctx.device else "—",
        ctx.remote_addr or "—")
    partner.sudo().message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note")
