"""Endpoints privados: perfil, instalaciones, piscinas, productos, lotes.

Patrón de todos los handlers privados:

1. Se busca el registro con ``ctx.env`` (usuario dueño de la clave, SIN sudo):
   si las ACL/ir.rule no le dejan verlo, es un 404 — no se distingue "no
   existe" de "no es tuyo" para no confirmar la existencia de datos ajenos.
2. Las escrituras van por el mismo camino que el portal: el método de negocio
   (o el create/write con los mismos campos), en sudo, con el dueño FORZADO
   al socio de la clave. Nunca se acepta un dueño o un id por parámetro.
"""

from odoo import fields

from . import serializers as S
from .framework import (ApiError, ApiResponse, F, api_route, forbidden, invalid, obj, paginate)

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
@api_route("GET", "/me", tags=[TAG_ME], summary="Mi perfil y los datos de esta clave",
           response=obj(id="uuid", name="string", type="string", email="string",
                        active_role="string", roles={"type": "array"},
                        api_key={"type": "object"}))
def me_get(ctx):
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
                              description="Perfil aprobado de la cuenta (semillero, laboratorio, "
                                          "camaronera, empacadora, maquilador, verificador)")}


@api_route("POST", "/me/active-role", scope="profile:write", tags=[TAG_ME], body=ACTIVE_ROLE_SPEC,
           summary="Cambia el perfil activo de la cuenta («Actuar como»)",
           description="Solo a un perfil aprobado. Es el mismo cambio que el selector «Perfil» "
                       "del portal y persiste en la cuenta. Para una sola petición con otro "
                       "perfil basta la cabecera X-Shrimp-Role (o ?acting_role=).")
def me_active_role(ctx):
    vals = ctx.clean(ACTIVE_ROLE_SPEC)
    if vals["role"] not in ctx.partner._shrimp_roles():
        raise forbidden("Tu cuenta no tiene aprobado el perfil «%s»." % vals["role"])
    ctx.partner.sudo()._shrimp_set_active_role(vals["role"])
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
    vals["partner_id"] = ctx.partner.id
    fac = ctx.env["shrimp.partner.facility"].sudo().create(vals)
    return S.facility(fac)


def _facility_update(ctx, id):
    fac = ctx.get_own("shrimp.partner.facility", id, "La instalación", archived=True)
    vals = _country(ctx, ctx.clean(FAC_SPEC, partial=True))
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
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
           response=FAC_SCHEMA)
def facility_delete(ctx, id):
    fac = ctx.get_own("shrimp.partner.facility", id, "La instalación", archived=True)
    fac.sudo().write({"active": False})
    return S.facility(fac)


# ---------------------------------------------------------------------------
# Piscinas
# ---------------------------------------------------------------------------
POND_SPEC = {
    "name": F("string", required=True, max_length=128),
    "code": F("string", max_length=32),
    "pond_type": F("enum", enum=["earth", "geomembrane", "tank", "raceway", "other"]),
    "facility": F("ref", model="shrimp.partner.facility", own=True, odoo="facility_id"),
    "capacity_mode": F("enum", enum=["volume", "dimensions"]),
    "length_m": F("number", minimum=0),
    "width_m": F("number", minimum=0),
    "depth_m": F("number", minimum=0),
    "manual_volume_m3": F("number", minimum=0),
    "usable_volume_m3": F("number", minimum=0),
    "max_stock_units": F("number", minimum=0),
    "location": F("string"),
    "notes": F("text"),
}
POND_SCHEMA = obj(id="uuid", name="string", code="string", pond_type="string", facility="ref",
                  area_m2="number", volume_m3="number", active="boolean", updated_at="datetime")


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
           status=201, summary="Crea una piscina", response=POND_SCHEMA)
def pond_create(ctx):
    vals = ctx.clean(POND_SPEC)
    vals["partner_id"] = ctx.partner.id
    pond = ctx.env["shrimp.partner.pond"].sudo().create(vals)
    return S.pond(pond)


def _pond_update(ctx, id):
    pond = ctx.get_own("shrimp.partner.pond", id, "La piscina", archived=True)
    vals = ctx.clean(POND_SPEC, partial=True)
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
    pond.sudo().write(vals)
    return S.pond(pond)


@api_route("PATCH", "/ponds/{id}", scope="facilities:write", tags=[TAG_FAC], body=POND_SPEC,
           summary="Actualiza una piscina (parcial)", response=POND_SCHEMA)
def pond_patch(ctx, id):
    return _pond_update(ctx, id)


@api_route("PUT", "/ponds/{id}", scope="facilities:write", tags=[TAG_FAC], body=POND_SPEC,
           summary="Alias de PATCH (compatibilidad con la API anterior)", response=POND_SCHEMA)
def pond_put(ctx, id):
    return _pond_update(ctx, id)


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
                     updated_at="datetime")
UOM_BY_ROLE = {"semillero": "millar", "laboratorio": "millar", "camaronera": "libra"}


def _default_uom(ctx):
    code = UOM_BY_ROLE.get(ctx.role, "libra")
    Uom = ctx.env["shrimp.uom"].sudo()
    return (Uom.search([("code", "=", code), ("active", "=", True)], limit=1)
            or Uom.search([("active", "=", True)], limit=1))


def _notify_product(product, xmlid):
    template = product.env.ref(xmlid, raise_if_not_found=False)
    email = product.sudo().seller_partner_id.email
    if template and email:
        # En cola (contexto shrimp_api_queue_mail): nunca SMTP dentro de la petición.
        template.sudo().send_mail(product.id, force_send=False, email_values={"email_to": email})


@api_route("GET", "/products", scope=("products:read", "products:write"), tags=[TAG_PROD],
           paginated=True, summary="Mis productos (lotes que publico)",
           params={"state": F("enum", enum=["draft", "published", "sold", "cancel"]),
                   "presentation": F("enum", enum=["entero", "cola"]),
                   "include_archived": F("boolean"),
                   "q": F("string")}, response=PRODUCT_SCHEMA)
def products_list(ctx):
    domain = [("seller_partner_id", "=", ctx.partner.id)]
    state = ctx.enum_param("state", ["draft", "published", "sold", "cancel"])
    if state:
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
    ctx.require_capability("sell_products", what="Solo semilleros, laboratorios y camaroneras publican productos.")
    vals = clean_product(ctx, partial=False)
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


def clean_product(ctx, partial):
    vals = ctx.clean(PRODUCT_INPUT, partial=partial)
    return vals


def _product_update(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto")
    vals = clean_product(ctx, partial=True)
    if not vals:
        raise invalid("No hay campos válidos para actualizar.")
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


@api_route("POST", "/products/{id}:publish", scope="products:write", tags=[TAG_PROD],
           summary="Publica un producto en borrador (mismas reglas que el portal)",
           response=PRODUCT_SCHEMA)
def product_publish(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto").sudo()
    if product.state != "draft":
        raise ApiError(409, "invalid-state", "Solo se publica un producto en borrador.")
    if product.available_qty <= 0:
        raise invalid("El producto no tiene stock disponible.")
    # Mismo criterio que el portal: solo el camarón de engorde exige
    # presentación y talla comercial.
    if (product.stage_id.code or "").strip().upper() == "ENGORDE" and (
            not product.presentation or not product.size_grade_id):
        raise invalid("El camarón de engorde necesita presentación y talla para publicarse.")
    product.write({"state": "published", "published_date": fields.Datetime.now()})
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
           summary="Reactiva un producto dado de baja (queda en borrador)", response=PRODUCT_SCHEMA)
def product_unarchive(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=True)
    if product.sudo().state == "cancel":
        raise ApiError(409, "invalid-state", "Un producto eliminado no se reactiva.")
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
