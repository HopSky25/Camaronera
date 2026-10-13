"""Dispositivos de la app móvil: registro, lista y revocación.

Contrato: docs/app/CONTRATO-FIRMA-DISPOSITIVO.md (§2, versión 1.1). No exige
scope y solo lo admite una clave de la app (``is_app_key``). Una clave de app
tiene como máximo un dispositivo activo:

* el PRIMER registro de una clave va sin firma (todavía no hay con qué);
* SUSTITUIRLO exige la firma del dispositivo activo (``SIGNED_IF_DEVICE``):
  quien robe la clave de API no puede reemplazar el teléfono legítimo. Si el
  teléfono se perdió, el socio lo revoca en el portal (lo que revoca también
  la clave) y vuelve a iniciar sesión: la clave nueva registra sin firma.

Registrar o sustituir deja una nota interna en el historial del socio.
"""

import logging

from markupsafe import Markup

from odoo.exceptions import ValidationError

from .framework import (SIGNED_IF_DEVICE, ApiError, ApiResponse, F, api_route, invalid, iso_dt,
                        obj)

_logger = logging.getLogger(__name__)

TAG = "dispositivos (app móvil)"

DEVICE_SPEC = {
    "name": F("string", max_length=64, description="Nombre visible del teléfono",
              example="Samsung A54 de Juan"),
    "platform": F("string", max_length=32, description="android, ios…", example="android"),
    "public_key": F("string", required=True, max_length=2048,
                    description="SubjectPublicKeyInfo DER en base64 estándar (EC P-256)"),
    "alg": F("enum", enum=["ES256"], description="Por defecto ES256 (el único admitido)"),
}

DEVICE_SCHEMA = obj(id="uuid", name="string", platform="string", alg="string",
                    created_at="datetime", active="boolean", last_used_at="datetime",
                    revoked_at="datetime", current="boolean")


def device_json(rec, key=None):
    rec = rec.sudo()
    return {
        "id": rec.uuid_ref,
        "name": rec.name,
        "platform": rec.platform or None,
        "alg": rec.alg,
        "created_at": iso_dt(rec.create_date),
        "active": bool(rec.active),
        "last_used_at": iso_dt(rec.last_used),
        "revoked_at": iso_dt(rec.revoked_at),
        # El dispositivo de la clave con la que se llama.
        "current": bool(key and rec.api_key_id == key),
    }


@api_route("POST", "/me/devices", signed=SIGNED_IF_DEVICE, supervisor=True, tags=[TAG], body=DEVICE_SPEC, status=201,
           response=DEVICE_SCHEMA,
           summary="Registra (o sustituye) el teléfono que firma las operaciones sensibles",
           description="Solo con una clave de la app (la de POST /auth/login); con otra clave, 409 "
                       "not-an-app-key. La clave pública es una SubjectPublicKeyInfo DER EC P-256 "
                       "en base64 estándar; si no lo es, o alg no es ES256, 422. El primer registro "
                       "de una clave va sin firma. Si la clave ya tiene un dispositivo activo, "
                       "sustituirlo exige la firma de ESE dispositivo (cabeceras X-Device-Id, "
                       "X-Signature-Timestamp, X-Signature; sin ellas 403 signature-required) y "
                       "el anterior queda revocado (la clave sigue). No exige scope; sí "
                       "Idempotency-Key, como todo POST. Deja una nota en el historial del socio.")
def device_register(ctx):
    vals = ctx.clean(DEVICE_SPEC)
    key = ctx.key.sudo()
    if not key.is_app_key:
        raise ApiError(409, "not-an-app-key",
                       "Solo la clave de la app móvil (POST /auth/login) registra dispositivos. "
                       "Las claves de integración no firman operaciones.")
    Device = ctx.env["shrimp.api.device"].sudo()
    try:
        public_key, fingerprint = Device._api_normalize_public_key(vals["public_key"])
    except ValidationError as e:
        raise invalid("La clave pública no es válida.",
                      errors=[{"field": "public_key", "message": e.args[0]}])
    previos = Device.search([("api_key_id", "=", key.id), ("active", "=", True)])
    # El despachador ya exigió la firma del dispositivo activo (SIGNED_IF_DEVICE);
    # esto es la red por si alguien cambia esa marca de la ruta.
    if previos and (not ctx.device or ctx.device not in previos):
        raise ApiError(403, "signature-required",
                       "Esta clave ya tiene un dispositivo activo: sustituirlo exige la firma de ese "
                       "dispositivo (X-Device-Id, X-Signature-Timestamp y X-Signature).")
    if previos:
        previos._api_revoke(revoke_key=False)
        # El índice único (un activo por clave) se comprueba en el INSERT.
        previos.flush_recordset()
    dev = Device.create({
        "name": vals.get("name") or "Dispositivo",
        "user_id": ctx.user.id,
        "api_key_id": key.id,
        "platform": vals.get("platform") or False,
        "alg": vals.get("alg") or "ES256",
        "public_key": public_key,
        "key_fingerprint": fingerprint,
    })
    _logger.info("API: dispositivo %s registrado para %s con la clave trz_%s%s",
                 dev.uuid_ref, ctx.user.login, key.key_prefix,
                 " (revoca %s)" % ", ".join(previos.mapped("uuid_ref")) if previos else "")
    _note_device(ctx, dev, previos)
    return ApiResponse(device_json(dev, key), status=201)


def _note_device(ctx, dev, previos):
    """Nota interna en el historial del socio (no notifica a nadie)."""
    partner = ctx.user.partner_id.sudo()
    if previos:
        titulo = Markup("<p><b>App móvil: dispositivo sustituido</b></p>")
        sustituye = Markup("<li>Sustituye a: %s (firmado por ese dispositivo)</li>") % ", ".join(
            "«%s»" % (d.name or d.uuid_ref) for d in previos)
    else:
        titulo = Markup("<p><b>App móvil: dispositivo registrado</b></p>")
        sustituye = Markup("")
    body = titulo + Markup("<ul><li>Dispositivo: %s</li><li>Plataforma: %s</li><li>IP: %s</li>"
                           "<li>Clave de API: trz_%s_…</li>%s</ul>") % (
        dev.name, dev.platform or "—", ctx.remote_addr or "—", ctx.key.sudo().key_prefix, sustituye)
    partner.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_note")


@api_route("GET", "/me/devices", supervisor=True, tags=[TAG], response=obj(data={"type": "array", "items": DEVICE_SCHEMA}),
           summary="Dispositivos registrados con mis claves de la app (activos y revocados)")
def device_list(ctx):
    devices = ctx.env["shrimp.api.device"].with_context(active_test=False).search(
        [("user_id", "=", ctx.user.id)], order="active desc, id desc", limit=100)
    return {"data": [device_json(d, ctx.key) for d in devices]}


@api_route("POST", "/me/devices/{id}:revoke", supervisor=True, tags=[TAG], response=DEVICE_SCHEMA,
           summary="Revoca un dispositivo y su clave de API",
           description="El dispositivo deja de firmar y su clave de API queda revocada en el acto "
                       "(si es la clave con la que se llama, las siguientes peticiones reciben 401 "
                       "revoked-api-key, o 403 device-revoked en las rutas firmadas). Repetirla "
                       "sobre un dispositivo ya revocado responde lo mismo.")
def device_revoke(ctx, id):
    dev = ctx.get_own("shrimp.api.device", id, "El dispositivo", archived=True)
    dev.sudo()._api_revoke(revoke_key=True)
    _logger.info("API: dispositivo %s revocado por %s", dev.uuid_ref, ctx.user.login)
    return device_json(dev, ctx.key)
