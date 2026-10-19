"""Fotos y certificados de un lote propio (gestión de lotes desde la app).

Lo mismo que el formulario de editar producto del portal
(shrimp_marketplace/controllers/product_portal.py: _save_photos,
_save_product_certificates, _remove_product_certificates), con el núcleo en
el modelo (shrimp_marketplace/models/shrimp_product_media.py):

* Fotos: hasta 20, JPG o PNG reales de hasta 5 MB; la primera es la portada.
  Llegan en base64 en el cuerpo JSON (por eso ``max_body``, como las fotos de
  campo de POST /verifications/{id}/photos).
* Certificados: ligar uno vigente de «mis certificados» del vendedor (hereda
  su aprobación) o subir uno propio del lote con su PDF o imagen (entra
  pendiente de revisión, como en el portal), y quitarlos.

No se firman: no cambian lo que se vende ni a qué precio (el portal tampoco
pide nada más que la sesión). Sí exigen «Vender» en el perfil del lote.
"""

import base64
import binascii
import re

from odoo.exceptions import ValidationError
from odoo.tools.image import image_process

from odoo.addons.shrimp_marketplace.models.shrimp_product_media import MAX_PHOTOS
from odoo.addons.shrimp_user_registry.controllers.main import _read_validated_file

from . import serializers as S
from .app import _image_type
from .framework import ApiError, ApiResponse, F, api_route, invalid, not_found, obj
from .private_core import TAG_PROD, require_sell

MAX_FILE_BYTES = 5 * 1024 * 1024
# 5 MB en base64 son ~6,7 MB, más el resto del JSON.
MEDIA_MAX_BODY = 8 * 1024 * 1024
THUMB_SIZE = (480, 480)

PHOTO_SPEC = {
    "filename": F("string", required=True, max_length=200, example="lote_frente.jpg"),
    "mime": F("enum", enum=["image/jpeg", "image/png"],
              description="Informativo: el tipo real se mira en la cabecera del archivo"),
    "data": F("text", required=True, max_length=MEDIA_MAX_BODY,
              description="La imagen en base64 estándar (JPG o PNG, máx. 5 MB decodificada)"),
    "cover": F("boolean", description="true = va como portada (primera); si no, al final"),
}
PHOTO_SCHEMA = obj(id="string", filename="string", mime="string", size="integer",
                   created_at="datetime")
PHOTOS_RESPONSE = {"type": "object", "properties": {
    "data": {"type": "array", "items": PHOTO_SCHEMA},
    "photo": PHOTO_SCHEMA,
    "meta": obj(count="integer", max="integer")}}

CERT_SPEC = {
    "account_certificate": F("string", max_length=64,
                             description="uuid de un certificado de «mis certificados» (aprobado, "
                                         "con archivo y vigente): se liga al lote"),
    "certificate": F("ref", model="shrimp.certificate", domain=[("active", "=", True)],
                     description="Certificado propio del lote: tipo del catálogo "
                                 "(/public/catalogs/certificate-types) de su perfil o «all»"),
    "number": F("string", max_length=64),
    "issue_date": F("date"),
    "expiry_date": F("date"),
    "filename": F("string", max_length=200, example="pcr_wssv.pdf"),
    "data": F("text", max_length=MEDIA_MAX_BODY,
              description="El archivo en base64 estándar: PDF, JPG o PNG de hasta 5 MB"),
}
CERT_LINE_SCHEMA = obj(id="uuid", certificate="ref", issuer="string", number="string",
                       issue_date="date", expiry_date="date", valid="boolean", status="string",
                       source="string", account_certificate="uuid", mime="string")
CERTS_RESPONSE = {"type": "object", "properties": {
    "data": {"type": "array", "items": CERT_LINE_SCHEMA},
    "available": {"type": "array", "items": obj(
        id="uuid", certificate="ref", issuer="string", number="string", issue_date="date",
        expiry_date="date", status="string")},
    "catalog": {"type": "array", "items": obj(id="uuid", name="string", issuer="string",
                                              code="string", role="string", expires="boolean")}}}


def _product(ctx, id, write=False):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=not write)
    if write:
        require_sell(ctx, product)
    return product.sudo()


def _decode(vals, field="data"):
    """base64 -> bytes con los topes de la plataforma (413 si pasa de 5 MB)."""
    data = re.sub(r"\s+", "", vals.get(field) or "")
    if not data:
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": field, "message": "Es obligatorio."}])
    if len(data) * 3 // 4 > MAX_FILE_BYTES + 3:
        raise ApiError(413, "payload-too-large", "El archivo supera 5 MB.")
    try:
        content = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": field, "message": "No es base64 válido."}])
    if len(content) > MAX_FILE_BYTES:
        raise ApiError(413, "payload-too-large", "El archivo supera 5 MB.")
    return content


def _filename(name, default):
    return re.sub(r'[\\/"\r\n<>]', "_", name or "")[:200] or default


def _photos(product, photo=None):
    fotos = product.shrimp_photos()
    out = {"data": [S.product_photo(a) for a in fotos],
           "meta": {"count": len(fotos), "max": MAX_PHOTOS}}
    if photo is not None:
        out["photo"] = S.product_photo(photo)
    return out


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------
@api_route("GET", "/products/{id}/photos", scope=("products:read", "products:write"),
           tags=[TAG_PROD], response=PHOTOS_RESPONSE,
           summary="Fotos de un lote propio, en orden (la primera es la portada)",
           description="Solo los datos; cada archivo se baja con GET /products/{id}/photos/{photo}.")
def product_photos(ctx, id):
    return _photos(_product(ctx, id))


@api_route("GET", "/products/{id}/photos/{photo}", scope=("products:read", "products:write"),
           tags=[TAG_PROD], binary="image/jpeg",
           params={"thumb": F("boolean", description="true = miniatura de 480 px como máximo")},
           summary="Archivo de una foto de un lote propio (JPG o PNG)")
def product_photo_file(ctx, id, photo):
    product = _product(ctx, id)
    att = product.shrimp_photo_by_token(photo)
    if not att:
        raise not_found("La foto")
    content = att.raw or b""
    mime = att.mimetype or "application/octet-stream"
    if ctx.bool_param("thumb") and content:
        try:
            content = image_process(content, size=THUMB_SIZE)
        except (ValueError, ValidationError, OSError):
            pass  # se entrega tal cual
    return ApiResponse(raw=content, content_type=mime, headers=[
        ("X-Content-Type-Options", "nosniff"),
        ("Content-Disposition", 'inline; filename="%s"' % _filename(att.name, "foto"))])


@api_route("POST", "/products/{id}/photos", scope="products:write", tags=[TAG_PROD],
           body=PHOTO_SPEC, status=201, max_body=MEDIA_MAX_BODY, response=PHOTOS_RESPONSE,
           summary="Sube una foto al lote (al final o como portada)",
           description="Exige Idempotency-Key; no se firma. JPG o PNG reales (se mira la cabecera "
                       "del archivo, no el mime declarado: si no es imagen, 422), máx. 5 MB "
                       "decodificada (413 payload-too-large) y hasta %s fotos por lote (422). Se "
                       "guarda igual que la foto del portal. Devuelve las fotos en su orden y la "
                       "nueva en «photo»." % MAX_PHOTOS)
def product_photo_upload(ctx, id):
    product = _product(ctx, id, write=True)
    vals = ctx.clean(PHOTO_SPEC)
    content = _decode(vals)
    mimetype = _image_type(content)
    if not mimetype:
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": "data", "message": "No es una imagen JPG o PNG."}])
    if len(product.shrimp_photos()) >= MAX_PHOTOS:
        raise invalid("El lote ya tiene %s fotos, el máximo. Quita una para agregar otra." % MAX_PHOTOS,
                      errors=[{"field": "data", "message": "Máximo %s fotos." % MAX_PHOTOS}])
    att = product.shrimp_add_photo(content, mimetype, _filename(vals.get("filename"), "foto"),
                                   cover=bool(vals.get("cover")))
    return _photos(product, att)


@api_route("POST", "/products/{id}/photos:reorder", scope="products:write", tags=[TAG_PROD],
           body={"order": F("array", required=True, items={},
                            description="Ids (tokens) de las fotos en el orden nuevo; la primera es "
                                        "la portada. Las que no vengan quedan al final.")},
           response=PHOTOS_RESPONSE,
           summary="Ordena las fotos del lote (la primera es la portada)",
           description="Exige Idempotency-Key; no se firma. Un id que no es una foto del lote da 422.")
def product_photos_reorder(ctx, id):
    product = _product(ctx, id, write=True)
    body = dict(ctx.body or {})
    order = body.pop("order", None)
    if body:
        raise invalid("El cuerpo de la petición no es válido.",
                      errors=[{"field": k, "message": "Campo desconocido o no editable."} for k in body])
    if not isinstance(order, list) or not all(isinstance(t, str) for t in order):
        raise invalid("«order» es la lista de ids de las fotos.",
                      errors=[{"field": "order", "message": "Se esperaba una lista de ids."}])
    por_token = {a.access_token: a for a in product.shrimp_photos()}
    ajenas = [t for t in order if t not in por_token]
    if ajenas:
        raise invalid("«%s» no es una foto de este lote." % ajenas[0],
                      errors=[{"field": "order", "message": "Hay ids que no son fotos del lote."}])
    product.shrimp_reorder_photos([por_token[t] for t in dict.fromkeys(order)])
    return _photos(product)


@api_route("DELETE", "/products/{id}/photos/{photo}", scope="products:write", tags=[TAG_PROD],
           response=PHOTOS_RESPONSE, summary="Quita una foto del lote",
           description="No se firma. El archivo se borra si ningún otro lote lo usa (el lote que "
                       "republica un comprador comparte las fotos). Si era la portada, la siguiente "
                       "pasa a serlo.")
def product_photo_delete(ctx, id, photo):
    product = _product(ctx, id, write=True)
    att = product.shrimp_photo_by_token(photo)
    if not att:
        raise not_found("La foto")
    product.shrimp_remove_photo(att)
    return _photos(product)


# ---------------------------------------------------------------------------
# Certificados
# ---------------------------------------------------------------------------
def _catalog(product):
    role = product._shrimp_seller_role()
    return product.env["shrimp.certificate"].sudo().search(
        [("active", "=", True), ("role", "in", [role, "all"])], order="name asc")


def _certificates(product, line=None):
    ligadas = set(product.certificate_line_ids.mapped("source_user_certificate_line_id").ids)
    disponibles = product.shrimp_valid_account_certificates().filtered(lambda l: l.id not in ligadas)
    out = {"data": [S.product_certificate_line(l) for l in product.certificate_line_ids],
           "available": [S.account_certificate(l) for l in disponibles],
           "catalog": [S.certificate_type(c) for c in _catalog(product)]}
    if line is not None:
        out["line"] = S.product_certificate_line(line)
    return out


@api_route("GET", "/products/{id}/certificates", scope=("products:read", "products:write"),
           tags=[TAG_PROD], response=CERTS_RESPONSE,
           summary="Certificados de un lote propio, los de «mis certificados» que se pueden ligar "
                   "y el catálogo para subir uno propio",
           description="«data»: los del lote con su estado de revisión (pending, approved, "
                       "rejected) y su origen (account = de «mis certificados», product = propio "
                       "del lote). «available»: los vigentes de la cuenta (aprobados, con archivo y "
                       "sin vencer) que aún no están ligados. «catalog»: tipos de certificado del "
                       "perfil del lote.")
def product_certificates(ctx, id):
    return _certificates(_product(ctx, id))


@api_route("POST", "/products/{id}/certificates", scope="products:write", tags=[TAG_PROD],
           body=CERT_SPEC, status=201, max_body=MEDIA_MAX_BODY, response=CERTS_RESPONSE,
           summary="Liga un certificado de «mis certificados» al lote o sube uno propio",
           description="Exige Idempotency-Key; no se firma. Con «account_certificate»: lo liga "
                       "(hereda su aprobación; si ya estaba, no se duplica). Con «certificate» + "
                       "«data» + «filename» (y opcionales «number», «issue_date», «expiry_date»): "
                       "certificado propio del lote, PDF, JPG o PNG de hasta 5 MB, que entra "
                       "pendiente de revisión como en el portal. Devuelve los certificados del lote "
                       "y el nuevo en «line».")
def product_certificate_add(ctx, id):
    product = _product(ctx, id, write=True)
    vals = ctx.clean(CERT_SPEC)
    if vals.get("account_certificate"):
        otros = [k for k in ("certificate", "data", "filename", "number", "issue_date", "expiry_date")
                 if vals.get(k)]
        if otros:
            raise invalid("Usa «account_certificate» solo, o «certificate» con su archivo.",
                          errors=[{"field": k, "message": "No va con account_certificate."}
                                  for k in otros])
        user_line = ctx.env["shrimp.user.certificate.line"].sudo().search(
            [("uuid_ref", "=", vals["account_certificate"]),
             ("partner_id", "=", product.seller_partner_id.id)], limit=1)
        if not user_line:
            raise invalid("Ese certificado no es de tu cuenta.",
                          errors=[{"field": "account_certificate", "message": "No existe en tu cuenta."}])
        try:
            line = product.shrimp_link_account_certificate(user_line)
        except ValidationError as e:
            msg = e.args[0] if e.args else "No se puede ligar."
            raise invalid(msg, errors=[{"field": "account_certificate", "message": msg}])
        return _certificates(product, line)

    errors = []
    for k in ("certificate", "data", "filename"):
        if not vals.get(k):
            errors.append({"field": k, "message": "Es obligatorio."})
    if errors:
        raise invalid("Indica el certificado y su archivo (o «account_certificate»).", errors=errors)
    catalogo = ctx.env["shrimp.certificate"].sudo().browse(vals["certificate"])
    if catalogo not in _catalog(product):
        msg = "«%s» no es un certificado del perfil de este lote." % catalogo.name
        raise invalid(msg, errors=[{"field": "certificate", "message": msg}])
    issue, expiry = _check_cert_dates(catalogo, vals)
    number = vals.get("number") or False
    if product.certificate_line_ids.filtered(
            lambda l: l.certificate_id == catalogo and (l.number or False) == number):
        msg = "Este lote ya tiene «%s» con ese número." % catalogo.name
        raise invalid(msg, errors=[{"field": "number", "message": msg}])
    content, mimetype = _cert_file(vals)
    att = product._shrimp_cert_attachment(content, mimetype, _filename(vals["filename"], "certificado"),
                                          catalogo)
    line = product.shrimp_add_own_certificate(catalogo, att, number=number, issue_date=issue or False,
                                              expiry_date=expiry or False)
    return _certificates(product, line)


def _check_cert_dates(catalogo, vals, current=None):
    """Vencimiento obligatorio si el tipo lo exige y no antes de la emisión
    (las restricciones del modelo, con el campo para la app)."""
    issue = vals["issue_date"] if "issue_date" in vals else (current and current["issue_date"])
    expiry = vals["expiry_date"] if "expiry_date" in vals else (current and current["expiry_date"])
    if catalogo.expires_required and not expiry:
        msg = "El certificado «%s» requiere fecha de expiración." % catalogo.name
        raise invalid(msg, errors=[{"field": "expiry_date", "message": msg}])
    if issue and expiry and expiry < issue:
        msg = "La fecha de expiración no puede ser menor que la fecha de emisión."
        raise invalid(msg, errors=[{"field": "expiry_date", "message": msg}])
    return issue or False, expiry or False


def _cert_file(vals):
    """(contenido, mimetype) del archivo de un certificado: PDF, JPG o PNG
    reales de hasta 5 MB."""
    content = _decode(vals)
    try:
        return content, _read_validated_file(content)
    except ValidationError as e:
        msg = e.args[0] if e.args else "Archivo no válido."
        raise invalid(msg, errors=[{"field": "data", "message": msg}])


CERT_EDIT_SPEC = {k: CERT_SPEC[k] for k in ("number", "issue_date", "expiry_date", "filename", "data")}


@api_route("PATCH", "/products/{id}/certificates/{line}", scope="products:write", tags=[TAG_PROD],
           body=CERT_EDIT_SPEC, max_body=MEDIA_MAX_BODY, response=CERTS_RESPONSE,
           summary="Corrige número y fechas (o el archivo) de un certificado propio del lote",
           description="Parcial y sin firma, como «Editar» en el formulario del portal. Solo los "
                       "propios del lote (source = product): los que vienen de «mis certificados» se "
                       "corrigen en /me/certificates (422). Un cambio lo devuelve a revisión "
                       "(pending). El emisor es el del tipo de certificado del catálogo y no se edita. "
                       "Para cambiar el archivo, «filename» + «data» (PDF, JPG o PNG, 5 MB).")
def product_certificate_edit(ctx, id, line):
    product = _product(ctx, id, write=True)
    rec = product.certificate_line_ids.filtered(lambda l: l.uuid_ref == line)
    if not rec:
        raise not_found("El certificado")
    if rec.source_user_certificate_line_id:
        msg = "Este certificado viene de tus certificados de la cuenta: corrígelo allí."
        raise invalid(msg, errors=[{"field": "line", "message": msg}])
    vals = ctx.clean(CERT_EDIT_SPEC, partial=True)
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
    issue, expiry = _check_cert_dates(rec.certificate_id, vals, current=rec)
    number = vals["number"] if "number" in vals else rec.number
    if (number or False) != (rec.number or False) and product.certificate_line_ids.filtered(
            lambda l: l != rec and l.certificate_id == rec.certificate_id and (l.number or False) == (number or False)):
        msg = "Este lote ya tiene «%s» con ese número." % rec.certificate_id.name
        raise invalid(msg, errors=[{"field": "number", "message": msg}])
    att = None
    if vals.get("data") or vals.get("filename"):
        if not vals.get("filename"):
            raise invalid("Indica el nombre del archivo.", errors=[{"field": "filename", "message": "Es obligatorio."}])
        content, mimetype = _cert_file(vals)
        att = product._shrimp_cert_attachment(content, mimetype, _filename(vals["filename"], "certificado"),
                                              rec.certificate_id)
    product.shrimp_edit_own_certificate(rec, number=number or False, issue_date=issue, expiry_date=expiry,
                                        attachment=att)
    return _certificates(product, rec)


@api_route("DELETE", "/products/{id}/certificates/{line}", scope="products:write", tags=[TAG_PROD],
           response=CERTS_RESPONSE, summary="Quita un certificado del lote",
           description="No se firma. Solo quita la línea del lote: el certificado de «mis "
                       "certificados» sigue en la cuenta.")
def product_certificate_delete(ctx, id, line):
    product = _product(ctx, id, write=True)
    rec = product.certificate_line_ids.filtered(lambda l: l.uuid_ref == line)
    if not rec:
        raise not_found("El certificado")
    product.shrimp_remove_certificate_lines(rec)
    return _certificates(product)


# ---------------------------------------------------------------------------
# «Mis certificados» de la cuenta
# ---------------------------------------------------------------------------
ACCOUNT_CERT_SCHEMA = obj(id="uuid", certificate="ref", issuer="string", number="string",
                          issue_date="date", expiry_date="date", status="string",
                          valid="boolean", mime="string", filename="string", products="integer")
ACCOUNT_CERTS_RESPONSE = {"type": "object", "properties": {
    "data": {"type": "array", "items": ACCOUNT_CERT_SCHEMA},
    "line": ACCOUNT_CERT_SCHEMA,
    "catalog": CERTS_RESPONSE["properties"]["catalog"]}}
ACCOUNT_CERT_SPEC = {
    "certificate": F("ref", model="shrimp.certificate", domain=[("active", "=", True)],
                     description="Tipo de certificado del catálogo de tus perfiles"),
    "number": F("string", max_length=64),
    "issue_date": F("date"),
    "expiry_date": F("date"),
    "filename": F("string", max_length=200, example="bap.pdf"),
    "data": F("text", max_length=MEDIA_MAX_BODY,
              description="El archivo en base64 estándar: PDF, JPG o PNG de hasta 5 MB"),
}
ACCOUNT_CERT_RENEW_SPEC = {k: ACCOUNT_CERT_SPEC[k]
                           for k in ("number", "issue_date", "expiry_date", "filename", "data")}


def _account_certificates(ctx, line=None):
    partner = ctx.partner
    out = {"data": [S.account_certificate_owner(l) for l in partner.shrimp_account_certificates()],
           "catalog": [S.certificate_type(c) for c in partner.shrimp_certificate_catalog()]}
    if line is not None:
        out["line"] = S.account_certificate_owner(line)
    return out


def _own_account_line(ctx, id):
    line = ctx.env["shrimp.user.certificate.line"].sudo().search(
        [("uuid_ref", "=", id), ("partner_id", "=", ctx.partner.id)], limit=1) if id else None
    if not line:
        raise not_found("El certificado")
    return line


@api_route("GET", "/me/certificates", tags=["perfil"], response=ACCOUNT_CERTS_RESPONSE,
           summary="Mis certificados de la cuenta (con su revisión) y el catálogo para subir uno",
           description="Como «Certificados» de Mi cuenta en el portal. «data»: los de la cuenta "
                       "con su estado de revisión (pending, approved, rejected) y si están vigentes; "
                       "«catalog»: tipos de certificado de todos tus perfiles (aprobados o pendientes) "
                       "y los de «todos».")
def account_certificates(ctx):
    return _account_certificates(ctx)


@api_route("POST", "/me/certificates", scope="profile:write", tags=["perfil"],
           body=ACCOUNT_CERT_SPEC, status=201, max_body=MEDIA_MAX_BODY,
           response=ACCOUNT_CERTS_RESPONSE,
           summary="Sube un certificado a la cuenta (queda pendiente de revisión)",
           description="Exige Idempotency-Key; no se firma (la plataforma lo revisa antes de que "
                       "sirva). «certificate», «filename» y «data» (PDF, JPG o PNG reales de hasta "
                       "5 MB) obligatorios; «number», «issue_date» y «expiry_date» opcionales (el "
                       "vencimiento es obligatorio si el tipo lo exige). Permiso «Administrar la "
                       "cuenta», como en el portal.")
def account_certificate_add(ctx):
    vals = ctx.clean(ACCOUNT_CERT_SPEC)
    errors = [{"field": k, "message": "Es obligatorio."}
              for k in ("certificate", "filename", "data") if not vals.get(k)]
    if errors:
        raise invalid("Indica el certificado y su archivo.", errors=errors)
    partner = ctx.partner
    catalogo = ctx.env["shrimp.certificate"].sudo().browse(vals["certificate"])
    if catalogo not in partner.shrimp_certificate_catalog():
        msg = "«%s» no es un certificado de tus perfiles." % catalogo.name
        raise invalid(msg, errors=[{"field": "certificate", "message": msg}])
    issue, expiry = _check_cert_dates(catalogo, vals)
    number = vals.get("number") or False
    if partner.shrimp_account_certificates().filtered(
            lambda l: l.certificate_id == catalogo and (l.certificate_number or False) == number):
        msg = "Ya tienes «%s» con ese número." % catalogo.name
        raise invalid(msg, errors=[{"field": "number", "message": msg}])
    content, mimetype = _cert_file(vals)
    att = partner._shrimp_account_cert_attachment(content, mimetype,
                                                  _filename(vals["filename"], "certificado"), catalogo)
    line = partner.shrimp_add_account_certificate(catalogo, att, number=number, issue_date=issue,
                                                  expiry_date=expiry)
    return _account_certificates(ctx, line)


@api_route("PATCH", "/me/certificates/{id}", scope="profile:write", tags=["perfil"],
           body=ACCOUNT_CERT_RENEW_SPEC, max_body=MEDIA_MAX_BODY, response=ACCOUNT_CERTS_RESPONSE,
           summary="Renueva o corrige un certificado de la cuenta (vuelve a revisión)",
           description="Como «Renovar» en Mi cuenta: número y fechas (parcial) y, si viene, el "
                       "archivo nuevo («filename» + «data»). Siempre vuelve a pending. No se firma.")
def account_certificate_renew(ctx, id):
    line = _own_account_line(ctx, id)
    vals = ctx.clean(ACCOUNT_CERT_RENEW_SPEC, partial=True)
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
    issue, expiry = _check_cert_dates(line.certificate_id, vals, current=line)
    number = vals["number"] if "number" in vals else line.certificate_number
    if (number or False) != (line.certificate_number or False) and ctx.partner.shrimp_account_certificates().filtered(
            lambda l: l != line and l.certificate_id == line.certificate_id
            and (l.certificate_number or False) == (number or False)):
        msg = "Ya tienes «%s» con ese número." % line.certificate_id.name
        raise invalid(msg, errors=[{"field": "number", "message": msg}])
    att = None
    if vals.get("data") or vals.get("filename"):
        if not vals.get("filename"):
            raise invalid("Indica el nombre del archivo.", errors=[{"field": "filename", "message": "Es obligatorio."}])
        content, mimetype = _cert_file(vals)
        att = ctx.partner._shrimp_account_cert_attachment(
            content, mimetype, _filename(vals["filename"], "certificado"), line.certificate_id)
    ctx.partner.shrimp_renew_account_certificate(line, number=number or False, issue_date=issue,
                                                 expiry_date=expiry, attachment=att)
    return _account_certificates(ctx, line)


@api_route("DELETE", "/me/certificates/{id}", scope="profile:write", tags=["perfil"],
           response=ACCOUNT_CERTS_RESPONSE, summary="Quita un certificado de la cuenta",
           description="Como «Eliminar» en Mi cuenta. Los lotes que ya lo tenían ligado conservan "
                       "su copia. No se firma.")
def account_certificate_delete(ctx, id):
    _own_account_line(ctx, id).unlink()
    return _account_certificates(ctx)
