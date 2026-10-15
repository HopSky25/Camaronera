"""Mejoras de la app móvil (docs/app/CONTRATO-MEJORAS-APP.md).

* C1 GET /public/app-version — versión mínima/última y enlace de descarga
  (el 426 app-update-required lo aplica el despachador, controllers/main.py).
* C2 POST /public/app-errors — reporte de errores de la app, sin terceros.
* C3 GET /me/notifications, POST /me/notifications/{id}:read y
  POST /me/notifications:read-all — bandeja de avisos.
* C4 GET|POST /verifications/{id}/photos — fotos de campo (la subida va firmada).
* C5 POST /purchases — compra directa (firmada).
"""

import base64
import binascii
import json
import logging
import re

from odoo.http import request

from ..models.res_config_settings import app_version_info
from . import serializers as S
from .framework import (ApiError, ApiResponse, F, api_route, forbidden, invalid, iso_dt, not_found,
                        obj)
from .private_trade import TX_SCHEMA
from .private_verification import READ as VER_READ, _as_verifier, _verification

_logger = logging.getLogger(__name__)

TAG_APP = "app móvil"
TAG_NOTIF = "avisos (app móvil)"
TAG_VER = "verificaciones"
TAG_TX = "compras y ventas"

# ---------------------------------------------------------------------------
# C1. Versión mínima obligatoria
# ---------------------------------------------------------------------------
PLATFORMS = ["android", "ios"]


@api_route("GET", "/public/app-version", public=True, tags=[TAG_APP],
           params={"platform": F("enum", enum=PLATFORMS, description="android | ios")},
           summary="Versión mínima y última de la app móvil (y enlace de descarga)",
           description="La app la consulta al abrir y al volver del segundo plano (como mucho una "
                       "vez por hora). Si su versión es menor que min_version, muestra la pantalla "
                       "bloqueante «Actualiza la app» con el enlace; si solo es menor que "
                       "latest_version, un aviso descartable. Valores vacíos = null (sin "
                       "obligación). Con min_version fijada, toda petición con una clave de la app "
                       "(y /auth/login) con X-App-Version menor o sin ella recibe 426 "
                       "app-update-required (con min_version y download_url en el cuerpo).",
           response=obj(min_version="string", latest_version="string", download_url="string",
                        message="string"))
def app_version(ctx):
    ctx.enum_param("platform", PLATFORMS)
    return app_version_info(ctx.env)


# ---------------------------------------------------------------------------
# C2. Reporte de errores de la app
# ---------------------------------------------------------------------------
APP_ERRORS_PER_HOUR = 30
APP_ERROR_SPEC = {
    "app_version": F("string", max_length=32, example="1.2.0"),
    "platform": F("enum", enum=PLATFORMS),
    "os_version": F("string", max_length=64, example="Android 14"),
    "device_model": F("string", max_length=128, example="SM-A546E"),
    "screen": F("string", max_length=128, description="Pantalla de la app donde ocurrió"),
    "error": F("text", required=True, max_length=4000),
    "stack": F("text", max_length=20000),
    "occurred_at": F("datetime"),
}


@api_route("POST", "/public/app-errors", public=True, tags=[TAG_APP], body=APP_ERROR_SPEC, status=204,
           summary="La app reporta un error (sin datos personales)",
           description="Sin clave y sin Idempotency-Key. Límite: %s reportes por hora por IP "
                       "(429 rate-limited con Retry-After). Campos extra → 422. La app no envía "
                       "usuario, correo, clave ni contenido de formularios. Los reportes se agrupan "
                       "por huella (sha256 del error + primera línea útil de la pila) en "
                       "Integraciones › Errores de la app. Respuesta 204." % APP_ERRORS_PER_HOUR)
def app_errors(ctx):
    limit = APP_ERRORS_PER_HOUR
    try:
        limit = int(ctx.env["ir.config_parameter"].sudo().get_param(
            "shrimp_api.app_errors_per_hour") or APP_ERRORS_PER_HOUR)
    except (TypeError, ValueError):
        pass
    if limit > 0:
        ip = (ctx.remote_addr or "?")[:45]
        Bucket = request.env["shrimp.api.rate.bucket"]
        # Cursor aparte y confirmado enseguida: cuenta aunque el reporte sea inválido.
        with request.env.registry.cursor() as cr:
            ok, _hits, retry = Bucket._hit(cr, "apperr:%s" % ip, Bucket.HOURLY_KIND, limit,
                                           period="hour")
        if not ok:
            raise ApiError(429, "rate-limited",
                           "Demasiados reportes de error desde esta IP (máximo %s por hora)." % limit,
                           headers=[("Retry-After", str(retry))])
    vals = ctx.clean(APP_ERROR_SPEC)
    if not vals.get("error"):
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": "error", "message": "Es obligatorio."}])
    ctx.env["shrimp.app.error"].sudo()._record(vals)
    return ApiResponse(status=204)


# ---------------------------------------------------------------------------
# C3. Bandeja de avisos
# ---------------------------------------------------------------------------
NOTIF_SCHEMA = obj(id="uuid", kind="string", title="string", body="string",
                   target=obj(type="string", id="string"), link="string",
                   created_at="datetime", read="boolean")
NOTIF_KINDS = ("verification_assigned, report_ready, verification_rejected, acceptance_due, "
               "purchase_received, purchase_confirmed, dispatch_scheduled, dispatch_eta_changed, "
               "truck_arrived, copack_request_published, copack_offer_accepted")


def _encode_notif_cursor(rec_id):
    payload = json.dumps({"n": rec_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_notif_cursor(cursor):
    try:
        pad = "=" * (-len(cursor) % 4)
        return int(json.loads(base64.urlsafe_b64decode(cursor + pad).decode())["n"])
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise ApiError(400, "invalid-cursor", "El cursor no es válido. Úsalo tal como lo devolvió "
                                              "la API en meta.next_cursor.")


def _notif_domain(ctx):
    domain = [("partner_id", "=", ctx.partner.id)]
    # X-Shrimp-Role (o ?acting_role=): solo los avisos de ese perfil y los
    # que no tienen perfil. Sin la cabecera, todos.
    if ctx._acting_role_raw:
        domain += ["|", ("role", "=", False), ("role", "=", ctx.role)]
    return domain


@api_route("GET", "/me/notifications", supervisor=True, tags=[TAG_NOTIF],
           params={"unread": F("boolean", description="Solo los no leídos"),
                   "limit": F("integer", description="1–200 (50 por defecto)"),
                   "cursor": F("string", description="meta.next_cursor de la página anterior")},
           response={"type": "object", "properties": {
               "data": {"type": "array", "items": NOTIF_SCHEMA},
               "meta": obj(next_cursor="string", count="integer", limit="integer",
                           has_more="boolean", unread_count="integer")}},
           summary="Mis avisos (bandeja de la app), del más nuevo al más viejo",
           description="Sin scope especial. Respeta X-Shrimp-Role: con la cabecera, solo los "
                       "avisos de ese perfil (y los que no tienen perfil). meta.unread_count es el "
                       "contador de no leídos (con el mismo filtro de perfil). La clave de "
                       "supervisor recibe la lista vacía; la de «actuar como» ve los del socio. "
                       "Tipos (kind): " + NOTIF_KINDS + ".")
def notifications_list(ctx):
    limit = ctx.int_param("limit", 50, 1, 200)
    unread = ctx.bool_param("unread")
    cursor = ctx.params.get("cursor")
    before = _decode_notif_cursor(cursor) if cursor else None
    if ctx.key.is_supervisor_key:
        return {"data": [], "meta": {"next_cursor": None, "count": 0, "limit": limit,
                                     "has_more": False, "unread_count": 0}}
    Notif = ctx.env["shrimp.app.notification"]
    domain = _notif_domain(ctx)
    unread_count = Notif.search_count(domain + [("is_read", "=", False)])
    if unread:
        domain.append(("is_read", "=", False))
    if before:
        domain.append(("id", "<", before))
    records = Notif.search(domain, order="id desc", limit=limit + 1)
    has_more = len(records) > limit
    records = records[:limit]
    return {"data": [n._api_dict() for n in records],
            "meta": {"next_cursor": _encode_notif_cursor(records[-1].id) if has_more else None,
                     "count": len(records), "limit": limit, "has_more": has_more,
                     "unread_count": unread_count}}


@api_route("POST", "/me/notifications/{id}:read", tags=[TAG_NOTIF], response=NOTIF_SCHEMA,
           summary="Marca un aviso como leído",
           description="Exige Idempotency-Key; no se firma y no exige scope. La clave de «actuar "
                       "como» de un supervisor no puede marcar (403 insufficient-scope).")
def notification_read(ctx, id):
    notif = ctx.resolve("shrimp.app.notification", id, own=True,
                        domain=[("partner_id", "=", ctx.partner.id)])
    if not notif:
        raise not_found("El aviso")
    notif.sudo().action_mark_read()
    return notif.sudo()._api_dict()


@api_route("POST", "/me/notifications:read-all", tags=[TAG_NOTIF],
           response=obj(updated="integer", unread_count="integer"),
           summary="Marca como leídos todos mis avisos",
           description="Exige Idempotency-Key; no se firma y no exige scope. Con X-Shrimp-Role, "
                       "solo los de ese perfil (y los que no tienen perfil). La clave de «actuar "
                       "como» no puede marcar (403 insufficient-scope).")
def notifications_read_all(ctx):
    domain = _notif_domain(ctx)
    pending = ctx.env["shrimp.app.notification"].search(domain + [("is_read", "=", False)])
    updated = pending.sudo().action_mark_read()
    return {"updated": updated, "unread_count": 0}


# ---------------------------------------------------------------------------
# C4. Fotos de campo
# ---------------------------------------------------------------------------
MAX_PHOTO_BYTES = 5 * 1024 * 1024
# 5 MB en base64 son ~6,7 MB, más el resto del JSON.
PHOTO_MAX_BODY = 8 * 1024 * 1024
PHOTO_STATES = ("in_field", "done")
PHOTO_SPEC = {
    "filename": F("string", required=True, max_length=200, example="pesaje_1.jpg"),
    "mime": F("enum", required=True, enum=["image/jpeg", "image/png"]),
    "data": F("text", required=True, max_length=PHOTO_MAX_BODY,
              description="La imagen en base64 estándar (máx. 5 MB decodificada)"),
    "taken_at": F("datetime", required=True),
    "latitude": F("number", minimum=-90, maximum=90,
                  description="Solo se guarda (visible para administradores); la API no lo devuelve"),
    "longitude": F("number", minimum=-180, maximum=180),
    "note": F("text", max_length=1000),
}
PHOTO_SCHEMA = obj(id="string", filename="string", mime="string", size="integer",
                   taken_at="datetime", note="string", created_at="datetime")


def _image_type(content):
    """Tipo REAL por la cabecera mágica; nunca el que dice el cliente."""
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return None


def _photo(att):
    att = att.sudo()
    if not att.access_token:
        att.generate_access_token()
    return {"id": att.access_token, "filename": att.name, "mime": att.mimetype or None,
            "size": att.file_size, "taken_at": iso_dt(att.shrimp_taken_at),
            "note": att.shrimp_note or None, "created_at": iso_dt(att.create_date)}


@api_route("GET", "/verifications/{id}/photos", scope=VER_READ, tags=[TAG_VER],
           response={"type": "object", "properties": {"data": {"type": "array", "items": PHOTO_SCHEMA}}},
           summary="Fotos de campo de una verificación (solo metadatos, sin el archivo)",
           description="Las que subió el técnico (desde el portal o la app). El id es el token de la "
                       "foto en el portal. Sin ubicación GPS. Lo pide quien ve la verificación.")
def verification_photos(ctx, id):
    v = _verification(ctx, id).sudo()
    return {"data": [_photo(a) for a in v.photo_ids.sorted("id")]}


@api_route("POST", "/verifications/{id}/photos", signed=True, scope="verifications:write",
           tags=[TAG_VER], body=PHOTO_SPEC, status=201, max_body=PHOTO_MAX_BODY,
           response=obj(id="string", filename="string", taken_at="datetime", size="integer"),
           summary="Sube una foto de campo (técnico asignado o administrador de la verificadora)",
           description="Firmada con una clave de la app, con Idempotency-Key. Solo con la "
                       "verificación en campo (in_field) o con el informe completo sin cerrar "
                       "(done); si no, 409 invalid-state. JPEG o PNG reales (se mira la cabecera "
                       "del archivo, no el mime declarado: si no es imagen, 422), máx. 5 MB "
                       "decodificada (413 payload-too-large). Se guarda igual que la foto del "
                       "portal (adjunto privado de la verificación).")
def verification_photo_upload(ctx, id):
    v, _admin = _as_verifier(ctx, id, editable=True)
    if v.state not in PHOTO_STATES:
        raise ApiError(409, "invalid-state", "Las fotos se suben con la verificación en campo o con "
                                             "el informe completo y sin cerrar.")
    vals = ctx.clean(PHOTO_SPEC)
    data = re.sub(r"\s+", "", vals["data"] or "")
    if len(data) * 3 // 4 > MAX_PHOTO_BYTES + 3:
        raise ApiError(413, "payload-too-large", "La foto supera 5 MB.")
    try:
        content = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": "data", "message": "No es base64 válido."}])
    if len(content) > MAX_PHOTO_BYTES:
        raise ApiError(413, "payload-too-large", "La foto supera 5 MB.")
    mimetype = _image_type(content)
    if not mimetype:
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": "data", "message": "No es una imagen JPEG o PNG."}])
    name = re.sub(r'[\\/"\r\n<>]', "_", vals["filename"] or "")[:200] or "foto"
    note = vals.get("note") or False
    att = ctx.env["ir.attachment"].sudo().create({
        # Los mismos campos que llena el portal (_save_photos)...
        "name": name,
        "type": "binary",
        "datas": base64.b64encode(content),
        "mimetype": mimetype,
        "res_model": "shrimp.verification",
        "res_id": v.id,
        "public": False,
        # ...y lo que solo manda la app.
        "description": note,
        "shrimp_taken_at": vals.get("taken_at") or False,
        "shrimp_note": note and note[:1000],
        "shrimp_latitude": vals.get("latitude") or 0.0,
        "shrimp_longitude": vals.get("longitude") or 0.0,
        "shrimp_uploaded_by_id": ctx.partner.id,
    })
    att.generate_access_token()
    v.write({"photo_ids": [(4, att.id)]})
    return {"id": att.access_token, "filename": att.name, "taken_at": iso_dt(att.shrimp_taken_at),
            "size": len(content)}


# ---------------------------------------------------------------------------
# C5. Compra directa
# ---------------------------------------------------------------------------
PURCHASE_SPEC = {
    "product": F("ref", required=True, model="shrimp.product",
                 domain=[("state", "=", "published"), ("active", "=", True)],
                 description="uuid del lote publicado"),
    "qty": F("number", required=True, minimum=0.000001,
             description="Cantidad en la unidad del lote (millares, libras…)"),
    "notes": F("text", max_length=2000, description="Nota para el vendedor (queda en la compra)"),
}


@api_route("POST", "/purchases", signed=True, scope="transactions:write", tags=[TAG_TX],
           body=PURCHASE_SPEC, status=201, response=TX_SCHEMA,
           summary="Compra directa de un lote sin verificación obligatoria (larva, nauplio…)",
           description="La misma lógica que «Comprar» en el portal (/marketplace/buy/<lote>/confirm, "
                       "shrimp.product.execute_purchase_flow): stock, perfil que puede comprar, "
                       "unidad y precio para el comprador. Compra con el perfil de la petición "
                       "(X-Shrimp-Role). Firmada con una clave de la app, con Idempotency-Key. "
                       "Errores: 422 verification-required (el lote exige verificación: usa POST "
                       "/verifications), 403 forbidden (tu perfil no puede comprar este lote; el "
                       "detail dice por qué), 422 business-rule (sin stock suficiente…). Respuesta "
                       "201: la compra, igual que GET /transactions/{id}.")
def purchase_create(ctx):
    vals = ctx.clean(PURCHASE_SPEC)
    role = ctx.role
    product = ctx.env["shrimp.product"].sudo().browse(vals["product"]).with_context(
        shrimp_buyer_role=role)
    if product._shrimp_requires_verification():
        raise ApiError(422, "verification-required",
                       "Este lote exige verificación en campo antes de comprarlo: cómpralo con "
                       "POST /verifications.")
    motivo = product.motivo_no_comprable(ctx.partner, role=role)
    if motivo:
        raise forbidden(motivo)
    result = product.execute_purchase_flow(ctx.partner, vals["qty"], buyer_role=role)
    tx = result["transaction"]
    if vals.get("notes"):
        tx.sudo().message_post(body=vals["notes"], author_id=ctx.partner.id,
                               message_type="comment", subtype_xmlid="mail.mt_note")
    return S.transaction(tx, ctx.partner)
