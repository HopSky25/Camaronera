"""Endpoints privados: perfil, instalaciones, piscinas, productos, lotes.

Patrón de todos los handlers privados:

1. Se busca el registro con ``ctx.env`` (usuario dueño de la clave, SIN sudo):
   si las ACL/ir.rule no le dejan verlo, es un 404 — no se distingue "no
   existe" de "no es tuyo" para no confirmar la existencia de datos ajenos.
2. Las escrituras van por el mismo camino que el portal: el método de negocio
   (o el create/write con los mismos campos), en sudo, con el dueño FORZADO
   al socio de la clave. Nunca se acepta un dueño o un id por parámetro.
"""

import base64
import binascii
import re

from odoo import fields
from odoo.exceptions import ValidationError

from odoo.addons.shrimp_marketplace.controllers.my_account_facilities import (
    MAX_DEPTH, MAX_METERS, MAX_VOLUME)
from odoo.addons.shrimp_marketplace.controllers.product_portal import (
    ShrimpProductPortalController as _Portal)
from odoo.addons.shrimp_user_registry.controllers.main import _read_validated_file

from . import serializers as S
from .framework import (ApiError, ApiResponse, F, api_route, forbidden, invalid, not_found, obj,
                        paginate)

TAG_ME = "perfil"
TAG_FAC = "instalaciones y piscinas"
TAG_PROD = "productos"
TAG_LOT = "lotes e inventario"

# Quién publica lo decide la matriz de capacidades ("sell_products"); esta
# tupla se conserva solo como referencia para la documentación.
SELLER_TYPES = ("semillero", "laboratorio", "camaronera")


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------
@api_route("GET", "/me", supervisor=True, tags=[TAG_ME], summary="Mi perfil y los datos de esta clave",
           description="Con una clave de supervisor (modo supervisor) devuelve {name, type: null, "
                       "supervisor: true, roles: []} y los datos de la clave. Con una clave de "
                       "«actuar como», el perfil del socio con read_only: true y viewed_by.",
           response=obj(id="uuid", name="string", type="string", email="string",
                        active_role="string", roles={"type": "array"},
                        supervisor="boolean", read_only="boolean",
                        api_key={"type": "object"}, account_state="string",
                        legal={"type": "object", "description":
                               "acceptance_required, pending_documents, accept_url"}))
def me_get(ctx):
    if ctx.key.is_supervisor_key:
        return S.supervisor_me(ctx.user, ctx.key)
    return S.me(ctx.user, ctx.key)


ME_SPEC = {
    "phone": F("string", max_length=64),
    "website": F("string", max_length=255),
    "street": F("string", max_length=255),
    "city": F("string", max_length=128),
    "zip": F("string", max_length=24),
    "preferences": F("object", description="accepts_harvest_reservations (empacadora), "
                                           "publish_verified_history (camaronera), "
                                           "listed_in_directory (maquilador)"),
}
PREFERENCES = {
    "empacadora": {"accepts_harvest_reservations": "reserva_acepta"},
    "camaronera": {"publish_verified_history": "farm_publicar_historial"},
    "maquilador": {"listed_in_directory": "pack_en_directorio"},
}


ACTIVE_ROLE_SPEC = {"role": F("string", required=True, max_length=32,
                              description="Perfil aprobado de la cuenta (semillero = Naupliero, laboratorio, "
                                          "camaronera, empacadora, maquilador, verificador)")}


@api_route("POST", "/me/active-role", signed=True, scope="profile:write", tags=[TAG_ME], body=ACTIVE_ROLE_SPEC,
           summary="Cambia el perfil activo de la cuenta («Actuar como»)",
           description="Solo a un perfil aprobado (y, para un subusuario, permitido por su "
                       "administrador). Fija el perfil de ESTA clave y es el mismo cambio que el "
                       "selector «Perfil» del portal para el usuario (el perfil activo es por "
                       "usuario, no de toda la cuenta). Para una sola petición con otro perfil "
                       "basta la cabecera X-Shrimp-Role (o ?acting_role=).")
def me_active_role(ctx):
    vals = ctx.clean(ACTIVE_ROLE_SPEC)
    if vals["role"] not in ctx.partner._shrimp_roles():
        raise forbidden("Tu cuenta no tiene aprobado el perfil «%s»." % vals["role"])
    if not ctx.partner._shrimp_user_allows(None, vals["role"]):
        raise forbidden("El administrador de tu cuenta no te dio acceso al perfil «%s»."
                        % vals["role"])
    ctx.partner.with_user(ctx.user).sudo()._shrimp_set_active_role(vals["role"])
    if ctx.key and "acting_role" in ctx.key._fields:
        ctx.key.sudo().write({"acting_role": vals["role"]})
    return S.me(ctx.user, ctx.key)


@api_route("PATCH", "/me", scope="profile:write", tags=[TAG_ME], body=ME_SPEC,
           summary="Actualiza datos de contacto y preferencias del propio socio")
def me_patch(ctx):
    vals = ctx.clean(ME_SPEC, partial=True)
    prefs = vals.pop("preferences", None) or {}
    # Las preferencias de cualquiera de los perfiles de la cuenta.
    allowed = {}
    for rol in ctx.partner._shrimp_roles():
        allowed.update(PREFERENCES.get(rol, {}))
    for name, value in prefs.items():
        if name not in allowed:
            raise invalid("Preferencia «%s» no aplica a tu tipo de socio." % name)
        if not isinstance(value, bool):
            raise invalid("La preferencia «%s» es true/false." % name)
        vals[allowed[name]] = value
    if not vals:
        raise invalid("No hay nada que actualizar.")
    # En sudo sobre el PROPIO socio (nunca uno que venga por parámetro): el
    # portal no tiene escritura sobre res.partner.
    ctx.partner.sudo().write(vals)
    return S.me(ctx.user, ctx.key)


# ---------------------------------------------------------------------------
# Instalaciones
# ---------------------------------------------------------------------------
FAC_SPEC = {
    "name": F("string", required=True, max_length=128),
    "code": F("string", max_length=32),
    "facility_type": F("enum", enum=["hatchery", "laboratory", "farm", "warehouse", "office", "other"]),
    "address": F("string"),
    "city": F("string", max_length=128),
    "province": F("string", max_length=128),
    "country": F("string", max_length=2, description="Código ISO-3166 alfa-2 (EC)"),
    "notes": F("text"),
}
FAC_SCHEMA = obj(id="uuid", name="string", code="string", facility_type="string",
                 city="string", province="string", active="boolean", updated_at="datetime")


def _unique_code(ctx, model, vals, current=None, what="una instalación"):
    """El código no se repite entre las instalaciones (o piscinas) del socio,
    archivadas incluidas: la regla del portal (my_account_facilities)."""
    code = vals.get("code")
    if not code:
        return
    dom = [("partner_id", "=", ctx.partner.id), ("code", "=", code)]
    if current:
        dom.append(("id", "!=", current.id))
    if ctx.env[model].sudo().with_context(active_test=False).search_count(dom):
        msg = "Ya tienes %s con el código «%s»." % (what, code)
        raise invalid(msg, errors=[{"field": "code", "message": msg}])


def _country(ctx, vals):
    if vals.get("country"):
        country = ctx.env["res.country"].sudo().search([("code", "=", vals["country"].upper())], limit=1)
        if not country:
            raise invalid("País desconocido: %s" % vals["country"])
        vals["country_id"] = country.id
    vals.pop("country", None)
    return vals


@api_route("GET", "/facilities", scope=("facilities:read", "facilities:write"), tags=[TAG_FAC],
           paginated=True, summary="Mis instalaciones",
           params={"include_archived": F("boolean")}, response=FAC_SCHEMA)
def facilities_list(ctx):
    domain = []
    if ctx.bool_param("include_archived"):
        domain = ["|", ("active", "=", True), ("active", "=", False)]
    return paginate(ctx, "shrimp.partner.facility", domain, S.facility)


@api_route("GET", "/facilities/{id}", scope=("facilities:read", "facilities:write"),
           tags=[TAG_FAC], summary="Detalle de una instalación", response=FAC_SCHEMA)
def facility_get(ctx, id):
    return S.facility(ctx.get_own("shrimp.partner.facility", id, "La instalación", archived=True))


@api_route("POST", "/facilities", scope="facilities:write", tags=[TAG_FAC], body=FAC_SPEC,
           status=201, summary="Crea una instalación", response=FAC_SCHEMA)
def facility_create(ctx):
    vals = _country(ctx, ctx.clean(FAC_SPEC))
    _unique_code(ctx, "shrimp.partner.facility", vals)
    vals["partner_id"] = ctx.partner.id
    fac = ctx.env["shrimp.partner.facility"].sudo().create(vals)
    return S.facility(fac)


def _facility_update(ctx, id):
    fac = ctx.get_own("shrimp.partner.facility", id, "La instalación", archived=True)
    vals = _country(ctx, ctx.clean(FAC_SPEC, partial=True))
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
    _unique_code(ctx, "shrimp.partner.facility", vals, current=fac)
    fac.sudo().write(vals)
    return S.facility(fac)


@api_route("PATCH", "/facilities/{id}", scope="facilities:write", tags=[TAG_FAC], body=FAC_SPEC,
           summary="Actualiza una instalación (parcial)", response=FAC_SCHEMA)
def facility_patch(ctx, id):
    return _facility_update(ctx, id)


@api_route("PUT", "/facilities/{id}", scope="facilities:write", tags=[TAG_FAC], body=FAC_SPEC,
           summary="Alias de PATCH (compatibilidad con la API anterior)", response=FAC_SCHEMA)
def facility_put(ctx, id):
    return _facility_update(ctx, id)


@api_route("DELETE", "/facilities/{id}", scope="facilities:write", tags=[TAG_FAC],
           summary="Archiva una instalación (no se borra: conserva la trazabilidad)",
           description="Sus piscinas o tanques se archivan con ella, como «Archivar» en Mi cuenta "
                       "del portal (siguen en el historial: siembras y origen de los lotes).",
           response=FAC_SCHEMA)
def facility_delete(ctx, id):
    fac = ctx.get_own("shrimp.partner.facility", id, "La instalación", archived=True)
    fac.sudo().pond_ids.write({"active": False})
    fac.sudo().write({"active": False})
    return S.facility(fac)


# ---------------------------------------------------------------------------
# Piscinas
# ---------------------------------------------------------------------------
POND_IMAGE_MAX_BODY = 8 * 1024 * 1024
POND_SPEC = {
    "name": F("string", required=True, max_length=128),
    "code": F("string", max_length=32),
    "pond_type": F("enum", enum=["earth", "geomembrane", "tank", "raceway", "other"]),
    "facility": F("ref", model="shrimp.partner.facility", own=True, odoo="facility_id"),
    "capacity_mode": F("enum", enum=["volume", "dimensions"]),
    "length_m": F("number", minimum=0, maximum=MAX_METERS),
    "width_m": F("number", minimum=0, maximum=MAX_METERS),
    "depth_m": F("number", minimum=0, maximum=MAX_DEPTH),
    "manual_volume_m3": F("number", minimum=0, maximum=MAX_VOLUME),
    "usable_volume_m3": F("number", minimum=0),
    "max_stock_units": F("number", minimum=0),
    "location": F("string"),
    "notes": F("text"),
    "image": F("text", max_length=POND_IMAGE_MAX_BODY,
               description="Foto de la piscina o tanque en base64 (JPG o PNG, 5 MB); null la quita"),
}
POND_SCHEMA = obj(id="uuid", name="string", code="string", pond_type="string", facility="ref",
                  area_m2="number", volume_m3="number", active="boolean", has_image="boolean",
                  updated_at="datetime")


def _pond_vals(ctx, partial, current=None):
    """Las reglas del portal: código no repetido entre las piscinas del
    socio y foto que sea una imagen real de hasta 5 MB."""
    vals = ctx.clean(POND_SPEC, partial=partial)
    _unique_code(ctx, "shrimp.partner.pond", vals, current=current, what="una piscina o tanque")
    if "image" in vals:
        if not vals["image"]:
            vals["image"] = False
        else:
            data = re.sub(r"\s+", "", vals["image"])
            if len(data) * 3 // 4 > 5 * 1024 * 1024 + 3:
                raise ApiError(413, "payload-too-large", "La foto supera 5 MB.")
            try:
                content = base64.b64decode(data, validate=True)
                _read_validated_file(content, allowed={"image/jpeg", "image/png"})
            except (binascii.Error, ValueError):
                raise invalid("El cuerpo de la petición no es válido.",
                              errors=[{"field": "image", "message": "No es base64 válido."}])
            except ValidationError:
                raise invalid("La foto tiene que ser una imagen JPG o PNG de hasta 5 MB.",
                              errors=[{"field": "image", "message": "No es una imagen JPG o PNG."}])
            vals["image"] = base64.b64encode(content)
    return vals


@api_route("GET", "/ponds", scope=("facilities:read", "facilities:write"), tags=[TAG_FAC],
           paginated=True, summary="Mis piscinas",
           params={"facility": F("string", description="uuid de instalación"),
                   "include_archived": F("boolean")}, response=POND_SCHEMA)
def ponds_list(ctx):
    domain = []
    if ctx.params.get("facility"):
        fac = ctx.get_own("shrimp.partner.facility", ctx.params["facility"], "La instalación",
                          archived=True)
        domain.append(("facility_id", "=", fac.id))
    if ctx.bool_param("include_archived"):
        domain += ["|", ("active", "=", True), ("active", "=", False)]
    return paginate(ctx, "shrimp.partner.pond", domain, S.pond)


@api_route("GET", "/ponds/{id}", scope=("facilities:read", "facilities:write"), tags=[TAG_FAC],
           summary="Detalle de una piscina", response=POND_SCHEMA)
def pond_get(ctx, id):
    return S.pond(ctx.get_own("shrimp.partner.pond", id, "La piscina", archived=True))


@api_route("POST", "/ponds", scope="facilities:write", tags=[TAG_FAC], body=POND_SPEC,
           status=201, max_body=POND_IMAGE_MAX_BODY, summary="Crea una piscina", response=POND_SCHEMA)
def pond_create(ctx):
    vals = _pond_vals(ctx, partial=False)
    vals["partner_id"] = ctx.partner.id
    pond = ctx.env["shrimp.partner.pond"].sudo().create(vals)
    return S.pond(pond)


def _pond_update(ctx, id):
    pond = ctx.get_own("shrimp.partner.pond", id, "La piscina", archived=True)
    vals = _pond_vals(ctx, partial=True, current=pond)
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
    pond.sudo().write(vals)
    return S.pond(pond)


@api_route("PATCH", "/ponds/{id}", scope="facilities:write", tags=[TAG_FAC], body=POND_SPEC,
           max_body=POND_IMAGE_MAX_BODY, summary="Actualiza una piscina (parcial)", response=POND_SCHEMA)
def pond_patch(ctx, id):
    return _pond_update(ctx, id)


@api_route("PUT", "/ponds/{id}", scope="facilities:write", tags=[TAG_FAC], body=POND_SPEC,
           max_body=POND_IMAGE_MAX_BODY, summary="Alias de PATCH (compatibilidad con la API anterior)", response=POND_SCHEMA)
def pond_put(ctx, id):
    return _pond_update(ctx, id)


@api_route("GET", "/ponds/{id}/image", scope=("facilities:read", "facilities:write"), tags=[TAG_FAC],
           binary="image/jpeg", summary="Foto de una piscina o tanque propio")
def pond_image(ctx, id):
    pond = ctx.get_own("shrimp.partner.pond", id, "La piscina", archived=True).sudo()
    if not pond.image:
        raise not_found("La foto de la piscina")
    content = base64.b64decode(pond.image)
    mime = "image/png" if content[:8].startswith(b"\x89PNG") else "image/jpeg"
    return ApiResponse(raw=content, content_type=mime, headers=[("X-Content-Type-Options", "nosniff")])


@api_route("DELETE", "/ponds/{id}", scope="facilities:write", tags=[TAG_FAC],
           summary="Archiva una piscina", response=POND_SCHEMA)
def pond_delete(ctx, id):
    pond = ctx.get_own("shrimp.partner.pond", id, "La piscina", archived=True)
    pond.sudo().write({"active": False})
    return S.pond(pond)


# ---------------------------------------------------------------------------
# Productos
# ---------------------------------------------------------------------------
PRODUCT_SPEC = {
    "name": F("string", required=True, max_length=200),
    "price": F("number", required=True, minimum=0),
    "initial_qty": F("number", required=True, minimum=0.000001),
    "uom": F("ref", model="shrimp.uom", odoo="uom_id", domain=[("active", "=", True)]),
    "species": F("ref", model="shrimp.species", odoo="species_id"),
    "stage": F("ref", model="shrimp.stage", odoo="stage_id"),
    "genetics_line": F("ref", model="shrimp.genetics.line", odoo="genetics_line_id"),
    "size_grade": F("ref", model="shrimp.size.grade", odoo="size_grade_id"),
    "presentation": F("enum", enum=["entero", "cola"]),
    "avg_size_mg": F("number", minimum=0),
    "survival_rate": F("number", minimum=0, maximum=100),
    "health_status": F("text"),
    "location": F("string"),
    "expected_delivery_date": F("date"),
    "available_from": F("datetime"),
    "available_to": F("datetime"),
    "origin_facility": F("ref", model="shrimp.partner.facility", own=True, odoo="origin_facility_id"),
    "origin_pond": F("ref", model="shrimp.partner.pond", own=True, odoo="origin_pond_id"),
    "batch_code": F("string", max_length=64),
    "production_date": F("date"),
    "traceability_notes": F("text"),
}
# Nombres de la API anterior (aceptaban el uuid en el campo *_id).
PRODUCT_LEGACY_ALIASES = {
    "uom_id": F("ref", model="shrimp.uom", odoo="uom_id"),
    "species_id": F("ref", model="shrimp.species", odoo="species_id"),
    "stage_id": F("ref", model="shrimp.stage", odoo="stage_id"),
    "genetics_line_id": F("ref", model="shrimp.genetics.line", odoo="genetics_line_id"),
}
PRODUCT_BODY = dict(PRODUCT_SPEC)
PRODUCT_INPUT = dict(PRODUCT_SPEC, **PRODUCT_LEGACY_ALIASES)
PRODUCT_SCHEMA = obj(id="uuid", name="string", state="string", active="boolean", price="money",
                     uom="ref", initial_qty="number", available_qty="number",
                     seller_type="string", species="ref", stage="ref", genetics_line="ref",
                     presentation="string", size_grade="ref", origin_facility="ref",
                     origin_pond="ref", batch_code="string", health_status="string",
                     real_status={"type": ["object", "null"],
                                  "description": "Última observación del vendedor (estado real "
                                                 "del lote); «stage» es la etapa publicada"},
                     updated_at="datetime")
UOM_BY_ROLE = {"semillero": "millar", "laboratorio": "millar", "camaronera": "libra"}


def _default_uom(ctx):
    code = UOM_BY_ROLE.get(ctx.role, "libra")
    Uom = ctx.env["shrimp.uom"].sudo()
    return (Uom.search([("code", "=", code), ("active", "=", True)], limit=1)
            or Uom.search([("active", "=", True)], limit=1))


def require_sell(ctx, product):
    """«Vender» en el perfil DEL LOTE (CR-12), como _get_owned_product del
    portal: la tabla de permisos solo mira que lo tenga en alguno."""
    user = ctx.user
    role = product.sudo()._shrimp_seller_role()
    if user._shrimp_restricted_member() and not user._shrimp_has_perm("sell", role=role):
        raise ApiError(403, "permission-denied", user._shrimp_perm_message("sell", role),
                       extra={"code": "permission_denied", "permission": "sell"})


def _notify_product(product, xmlid):
    template = product.env.ref(xmlid, raise_if_not_found=False)
    email = product.sudo().seller_partner_id.email
    if template and email:
        # En cola (contexto shrimp_api_queue_mail): nunca SMTP dentro de la petición.
        template.sudo().send_mail(product.id, force_send=False, email_values={"email_to": email})


@api_route("GET", "/products", scope=("products:read", "products:write"), tags=[TAG_PROD],
           paginated=True, summary="Mis productos (lotes que publico)",
           params={"state": F("enum", enum=["draft", "published", "sold", "cancel", "archived"],
                               description="archived = dados de baja que se pueden reactivar "
                                           "(como «Archivados» en Mis productos del portal)"),
                   "presentation": F("enum", enum=["entero", "cola"]),
                   "include_archived": F("boolean"),
                   "q": F("string")}, response=PRODUCT_SCHEMA)
def products_list(ctx):
    domain = [("seller_partner_id", "=", ctx.partner.id)]
    state = ctx.enum_param("state", ["draft", "published", "sold", "cancel", "archived"])
    if state == "archived":
        # Retirados (archivados): el eliminado (cancel) no se reactiva.
        domain += [("active", "=", False), ("state", "!=", "cancel")]
    elif state:
        domain.append(("state", "=", state))
    pres = ctx.enum_param("presentation", ["entero", "cola"])
    if pres:
        domain.append(("presentation", "=", pres))
    if ctx.bool_param("include_archived"):
        domain += ["|", ("active", "=", True), ("active", "=", False)]
    q = (ctx.params.get("q") or "").strip()[:80]
    if q:
        domain.append(("name", "ilike", q))
    return paginate(ctx, "shrimp.product", domain, S.product_owner)


@api_route("GET", "/products/{id}", scope=("products:read", "products:write"), tags=[TAG_PROD],
           summary="Detalle de un producto propio", response=PRODUCT_SCHEMA)
def product_get(ctx, id):
    return S.product_owner(ctx.get_own("shrimp.product", id, "El producto", archived=True))


@api_route("POST", "/products", scope="products:write", tags=[TAG_PROD], body=PRODUCT_BODY,
           status=201, summary="Crea un producto en borrador (el vendedor es siempre el dueño de la clave)",
           response=PRODUCT_SCHEMA)
def product_create(ctx):
    ctx.require_capability("sell_products", what="Solo nauplieros, laboratorios y camaroneras publican productos.")
    ctx.require_publishing_allowed()
    vals = clean_product(ctx, partial=False, role=ctx.role)
    vals.update({
        "seller_partner_id": ctx.partner.id,
        # El rol sale del perfil con el que actúa la petición (por defecto
        # el activo; X-Shrimp-Role / acting_role para otro perfil aprobado).
        "seller_role": ctx.role,
        "state": "draft",
        "active": True,
    })
    if not vals.get("uom_id"):
        vals["uom_id"] = _default_uom(ctx).id or False
    product = ctx.env["shrimp.product"].sudo().create(vals)
    _notify_product(product, "shrimp_marketplace.mail_template_shrimp_product_created")
    return S.product_owner(product)


def clean_product(ctx, partial, product=None, role=None):
    vals = ctx.clean(PRODUCT_INPUT, partial=partial)
    _check_portal_rules(ctx, vals, product, role or (product and product._shrimp_seller_role()))
    return vals


def _stage_role_problem(env, stage, role):
    """Texto si el perfil no puede publicar esa etapa (None si puede).

    La regla es la del formulario del portal (STAGE_TIPOS_POR_ROL): el
    naupliero y el laboratorio venden nauplio y larva; la camaronera,
    camarón (juvenil y engorde)."""
    if not stage or _Portal._stage_allowed_for_role(stage, role):
        return None
    permitidas = env["shrimp.stage"].sudo().search([("active", "=", True)]).filtered(
        lambda s: _Portal._stage_allowed_for_role(s, role))
    etiqueta = env["res.partner"]._shrimp_type_label(role) or role or ""
    return "La etapa «%s» no la puede publicar un perfil %s. Etapas permitidas: %s." % (
        stage.name, etiqueta.lower(), ", ".join(permitidas.mapped("name")) or "—")


def _check_portal_rules(ctx, vals, product, role):
    """Las validaciones del formulario del portal que el modelo no aplica.

    - La etapa tiene que poder publicarla el perfil del lote (como el portal,
      solo cuando se elige o se cambia: un lote antiguo conserva la suya).
    - Presentación y talla solo existen en camarón: el portal las descarta en
      nauplio y larva; aquí se rechazan para que el integrador se entere.
    - Instalación de origen: no una bodega ni una oficina, y sin instalación
      no hay piscina.
    El resto (talla en engorde, piscina de la instalación, campos bloqueados
    con compras) lo valida el modelo.
    """
    Product = ctx.env["shrimp.product"]
    stage = ctx.env["shrimp.stage"].sudo().browse(vals["stage_id"]) if vals.get("stage_id") \
        else (product.stage_id if product else ctx.env["shrimp.stage"])
    if vals.get("stage_id") and (not product or product.stage_id != stage):
        problema = _stage_role_problem(ctx.env, stage, role)
        if problema:
            raise invalid(problema, errors=[{"field": "stage", "message": problema}])
    if {"stage_id", "presentation", "size_grade_id"} & set(vals):
        presentation = vals["presentation"] if "presentation" in vals else (product and product.presentation)
        size = vals["size_grade_id"] if "size_grade_id" in vals else (product and product.size_grade_id.id)
        if stage and stage._shrimp_tipo() != "camaron" and (presentation or size):
            msg = "La presentación y la talla solo aplican al camarón, no a «%s»." % stage.name
            raise invalid(msg, errors=[{"field": "presentation" if presentation else "size_grade",
                                        "message": msg}])
    if vals.get("origin_facility_id"):
        fac = ctx.env["shrimp.partner.facility"].sudo().browse(vals["origin_facility_id"])
        if not Product._shrimp_facility_can_be_origin(fac):
            msg = "«%s» es una bodega u oficina: no produce lotes y no puede ser su origen." % fac.name
            raise invalid(msg, errors=[{"field": "origin_facility", "message": msg}])
    if vals.get("origin_pond_id"):
        fac_id = vals["origin_facility_id"] if "origin_facility_id" in vals \
            else (product and product.origin_facility_id.id)
        if not fac_id:
            msg = "Elige primero la instalación de origen de la piscina."
            raise invalid(msg, errors=[{"field": "origin_facility", "message": msg}])


def _portal_publish_problems(product):
    """Lo que el portal exige al guardar y el modelo no comprueba al publicar
    (un borrador creado por la API puede no tenerlo)."""
    if not product.stage_id:
        return ["Elige la etapa del lote antes de publicarlo."]
    if product.stage_id._shrimp_tipo() != "camaron" and (product.presentation or product.size_grade_id):
        return ["La presentación y la talla solo aplican al camarón, no a «%s»." % product.stage_id.name]
    return []


# Lo que fija cuánto paga el comprador y cuánto hay: en un lote ya publicado,
# con la clave de la app, se cambia firmado (POST /products/{id}:update-terms).
COMMERCIAL_FIELDS = {"price": "price", "initial_qty": "initial_qty", "uom_id": "uom"}


def _changed(product, vals, fields_map):
    return [api for odoo, api in fields_map.items()
            if odoo in vals and product._field_value_changed(product, odoo, vals[odoo])]


def _check_purchase_locks(product, vals):
    """Campos bloqueados por compras (las reglas del portal y del modelo),
    con el campo en errors[] para que la app lo marque."""
    if not product.has_purchases():
        return
    locked = {odoo: api for odoo, api in S.LOCKED_API_FIELDS.items()
              if odoo != "price" or product.shrimp_price_locked()}
    blocked = _changed(product, vals, locked)
    if blocked:
        reason = product.shrimp_purchase_lock_reason() or "ya tiene compras registradas"
        msg = "No puedes cambiar esto: el lote %s." % reason
        raise invalid(msg, errors=[{"field": f, "message": "Bloqueado: el lote %s." % reason}
                                   for f in blocked])


def _product_update(ctx, id, signed=False):
    product = ctx.get_own("shrimp.product", id, "El producto")
    require_sell(ctx, product)
    vals = clean_product(ctx, partial=True, product=product.sudo())
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
    _check_purchase_locks(product.sudo(), vals)
    if not signed and ctx.key.is_app_key and product.sudo().state != "draft":
        comerciales = _changed(product.sudo(), vals, COMMERCIAL_FIELDS)
        if comerciales:
            raise ApiError(
                403, "signature-required",
                "En un lote publicado, el precio, la cantidad y la unidad se cambian firmados: "
                "usa POST /products/{id}:update-terms con la firma del dispositivo.",
                extra={"fields": comerciales})
    # Si ya tiene compras, el modelo rechaza cambiar especie, precio, unidad...
    # con un mensaje claro (422). No se descartan en silencio como en el
    # formulario: un integrador tiene que enterarse de que no se aplicó.
    product.sudo().write(vals)
    return S.product_owner(product)


@api_route("PATCH", "/products/{id}", scope="products:write", tags=[TAG_PROD], body=PRODUCT_BODY,
           summary="Actualiza un producto propio (parcial)", response=PRODUCT_SCHEMA)
def product_patch(ctx, id):
    return _product_update(ctx, id)


@api_route("PUT", "/products/{id}", scope="products:write", tags=[TAG_PROD], body=PRODUCT_BODY,
           summary="Alias de PATCH (compatibilidad con la API anterior)", response=PRODUCT_SCHEMA)
def product_put(ctx, id):
    return _product_update(ctx, id)


@api_route("POST", "/products/{id}:update-terms", scope="products:write", tags=[TAG_PROD],
           body=PRODUCT_BODY, signed=True,
           summary="Cambia un lote propio con la firma del dispositivo (precio, cantidad o unidad "
                   "de un lote publicado)",
           description="El mismo cuerpo y las mismas reglas que PATCH /products/{id} (parcial). Con "
                       "la clave de la app va firmada (§5 bis): en un lote que no está en borrador "
                       "(publicado o vendido), cambiar price, initial_qty o uom por PATCH da 403 "
                       "signature-required y se hace aquí. Con compras siguen bloqueados los datos "
                       "críticos (422 con errors[].field); el precio, solo mientras haya compras "
                       "abiertas. Las claves de integración también pueden usarla (sin firma).",
           response=PRODUCT_SCHEMA)
def product_update_terms(ctx, id):
    return _product_update(ctx, id, signed=True)


@api_route("POST", "/products/{id}:publish", scope="products:write", tags=[TAG_PROD], signed=True,
           summary="Publica un producto en borrador (mismas reglas que el portal)",
           description="Con la clave de la app va firmada por el dispositivo (§5 bis). Exige "
                       "etapa, stock, y presentación y talla en el camarón de engorde. Publica "
                       "con shrimp.product.action_publish, como el botón «Publicar» del portal.",
           response=PRODUCT_SCHEMA)
def product_publish(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto").sudo()
    ctx.require_publishing_allowed()
    if product.state != "draft":
        raise ApiError(409, "invalid-state", "Solo se publica un producto en borrador.")
    if product.available_qty <= 0:
        raise invalid("El producto no tiene stock disponible.")
    # Mismo criterio que el portal: solo el camarón de engorde exige
    # presentación y talla comercial.
    if (product.stage_id.code or "").strip().upper() == "ENGORDE" and (
            not product.presentation or not product.size_grade_id):
        raise invalid("El camarón de engorde necesita presentación y talla para publicarse.")
    problemas = _portal_publish_problems(product)
    if problemas:
        raise invalid(" ".join(problemas))
    # El mismo método que el portal (suspensión, perfil rechazado, stock,
    # vendedor y permiso «Vender» del subusuario).
    product.action_publish()
    _notify_product(product, "shrimp_marketplace.mail_template_shrimp_product_published")
    return S.product_owner(product)


@api_route("POST", "/products/{id}:archive", scope="products:write", tags=[TAG_PROD],
           summary="Da de baja el producto (sale del marketplace; se puede reactivar)",
           response=PRODUCT_SCHEMA)
def product_archive(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=True)
    product.sudo().write({"active": False, "state": "draft"})
    return S.product_owner(product)


@api_route("POST", "/products/{id}:unarchive", scope="products:write", tags=[TAG_PROD],
           signed=True,
           summary="Reactiva un producto dado de baja (queda en borrador)",
           description="Con la clave de la app va firmada (§5 bis, desde 19.0.1.17.0): devuelve a "
                       "la oferta del vendedor un lote que retiró. Un producto eliminado no se "
                       "reactiva (409 invalid-state), igual que en el portal "
                       "(shrimp.product.shrimp_can_reactivate).",
           response=PRODUCT_SCHEMA)
def product_unarchive(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=True)
    require_sell(ctx, product)
    if product.sudo().state == "cancel":
        raise ApiError(409, "invalid-state", "Un producto eliminado no se reactiva.")
    if product.sudo().shrimp_can_reactivate():
        product.sudo().write({"active": True})
    return S.product_owner(product)


@api_route("DELETE", "/products/{id}", scope="products:write", tags=[TAG_PROD],
           summary="Borrado lógico (active=false, state=cancel). Se conserva para la trazabilidad",
           response=PRODUCT_SCHEMA)
def product_delete(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=True)
    product.sudo().write({"active": False, "state": "cancel"})
    return S.product_owner(product)


# --- evolución ---------------------------------------------------------------
EVO_SPEC = {
    "date": F("datetime"),
    "stage": F("ref", model="shrimp.stage", odoo="stage_id"),
    "avg_size_mg": F("number", minimum=0),
    "survival_rate": F("number", minimum=0, maximum=100),
    "health_status": F("text"),
    "note": F("text"),
}
EVO_SCHEMA = obj(id="uuid", date="datetime", stage="ref", avg_size_mg="number",
                 survival_rate="number", health_status="string", note="string")


@api_route("GET", "/products/{id}/evolutions", scope=("products:read", "products:write"),
           tags=[TAG_PROD], summary="Histórico de evolución de un producto propio",
           response={"type": "object", "properties": {"data": {"type": "array", "items": EVO_SCHEMA}}})
def evolutions_list(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=True)
    evos = product.sudo().evolution_ids.sorted(lambda e: (e.date, e.id))
    return {"data": [S.evolution(e) for e in evos], "meta": {"count": len(evos), "next_cursor": None}}


@api_route("POST", "/products/{id}/evolutions", scope="products:write", tags=[TAG_PROD],
           body=EVO_SPEC, status=201,
           summary="Registra una medición (estadío, tamaño, supervivencia, sanidad)",
           description="Actualiza esos datos en el producto y deja la medición en su histórico, "
                       "que es lo que sale en el certificado de trazabilidad.",
           response=EVO_SCHEMA)
def evolution_create(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto").sudo()
    vals = ctx.clean(EVO_SPEC)
    measured = {k: v for k, v in vals.items()
                if k in ("stage_id", "avg_size_mg", "survival_rate", "health_status")}
    if not measured:
        raise invalid("Indica al menos una medición (stage, avg_size_mg, survival_rate o health_status).")
    product.with_context(skip_evolution_snapshot=True).write(measured)
    evo = ctx.env["shrimp.product.evolution"].sudo().create({
        # Esta ruta cambia los datos publicados: es un cambio en la
        # publicación. Para observar sin tocarla: POST /products/{id}/observations.
        "origin": "publication",
        "product_id": product.id,
        "date": vals.get("date") or fields.Datetime.now(),
        "stage_id": product.stage_id.id or False,
        "avg_size_mg": product.avg_size_mg,
        "survival_rate": product.survival_rate,
        "health_status": product.health_status,
        "available_qty": product.available_qty,
        "note": vals.get("note") or False,
        "user_id": ctx.user.id,
    })
    return S.evolution(evo)


# ---------------------------------------------------------------------------
# Lotes de inventario
# ---------------------------------------------------------------------------
LOT_SCHEMA = obj(id="uuid", product="ref", owner="ref", initial_qty="number",
                 available_qty="number", uom="ref", state="string", updated_at="datetime")


@api_route("GET", "/lots", scope=("lots:read", "lots:write"), tags=[TAG_LOT], paginated=True,
           summary="Mi inventario por lote",
           params={"product": F("string", description="uuid de producto"),
                   "state": F("enum", enum=["available", "consumed"])},
           response=LOT_SCHEMA)
def lots_list(ctx):
    domain = [("owner_id", "=", ctx.partner.id)]
    if ctx.params.get("product"):
        product = ctx.resolve("shrimp.product", ctx.params["product"],
                              domain=[("stock_lot_ids.owner_id", "=", ctx.partner.id)])
        if not product:
            raise ApiError(400, "invalid-parameter", "«product» no corresponde a ningún lote tuyo.")
        domain.append(("product_id", "=", product.id))
    state = ctx.enum_param("state", ["available", "consumed"])
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.stock.lot", domain, S.lot)


@api_route("GET", "/lots/{id}", scope=("lots:read", "lots:write"), tags=[TAG_LOT],
           summary="Detalle de un lote propio", response=LOT_SCHEMA)
def lot_get(ctx, id):
    return S.lot(ctx.get_own("shrimp.stock.lot", id, "El lote"))


@api_route("GET", "/lots/{id}/moves", scope=("lots:read", "lots:write"), tags=[TAG_LOT],
           summary="Procedencia (cadena de movimientos hasta el origen) y salidas del lote",
           response=obj(provenance={"type": "array"}, outgoing={"type": "array"}))
def lot_moves(ctx, id):
    lot = ctx.get_own("shrimp.stock.lot", id, "El lote").sudo()
    chain, current, seen = [], lot.origin_move_id, set()
    while current and current.id not in seen:
        seen.add(current.id)
        chain.append(current)
        current = current.parent_move_id
    outgoing = ctx.env["shrimp.stock.move"].search([
        ("product_id", "=", lot.product_id.id), ("source_partner_id", "=", ctx.partner.id)],
        order="date asc, id asc", limit=500)
    return {"provenance": [S.move(m) for m in reversed(chain)],
            "outgoing": [S.move(m) for m in outgoing]}


ALLOC_SPEC = {
    "pond": F("ref", required=True, model="shrimp.partner.pond", own=True, odoo="pond_id"),
    "allocated_qty": F("number", required=True, minimum=0.000001),
    "allocation_date": F("date"),
    "notes": F("text"),
}
ALLOC_SCHEMA = obj(id="uuid", lot="uuid", pond="ref", allocated_qty="number",
                   allocation_date="date", state="string")


@api_route("GET", "/lots/{id}/allocations", scope=("lots:read", "lots:write"), tags=[TAG_LOT],
           summary="Siembras/asignaciones del lote a piscinas",
           response={"type": "object", "properties": {"data": {"type": "array", "items": ALLOC_SCHEMA}}})
def lot_allocations(ctx, id):
    lot = ctx.get_own("shrimp.stock.lot", id, "El lote")
    allocs = ctx.env["shrimp.lot.allocation"].search([("stock_lot_id", "=", lot.id)])
    return {"data": [S.allocation(a) for a in allocs], "meta": {"count": len(allocs), "next_cursor": None}}


@api_route("POST", "/lots/{id}/allocations", scope="lots:write", tags=[TAG_LOT], body=ALLOC_SPEC,
           status=201, summary="Asigna (siembra) parte del lote en una piscina propia",
           response=ALLOC_SCHEMA)
def lot_allocate(ctx, id):
    lot = ctx.get_own("shrimp.stock.lot", id, "El lote")
    vals = ctx.clean(ALLOC_SPEC)
    vals["stock_lot_id"] = lot.id
    if not vals.get("allocation_date"):
        vals.pop("allocation_date", None)
    alloc = ctx.env["shrimp.lot.allocation"].sudo().create(vals)
    return S.allocation(alloc)
