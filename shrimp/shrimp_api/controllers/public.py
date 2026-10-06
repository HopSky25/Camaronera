"""Endpoints públicos (/api/v1/public/...).

Sin clave y con CORS abierto (solo GET). Son los ÚNICOS que usan sudo para
buscar, y siempre con un dominio fijo escrito aquí, nunca construido con lo
que manda el cliente: el cliente solo puede estrechar el filtro con valores
que se validan (uuid de catálogo, enumerados).
"""

from odoo import fields

from . import serializers as S
from .framework import (ApiError, F, api_route, not_found, obj, paginate)

TAG_CAT = "público: catálogos"
TAG_MKT = "público: marketplace"
TAG_DIR = "público: directorios"
TAG_TRZ = "público: trazabilidad"


def _envelope(records, serializer):
    data = [serializer(r) for r in records]
    return {"data": data, "meta": {"count": len(data), "next_cursor": None}}


CATALOG_ITEM = obj(id="uuid", name="string", code="string")


@api_route("GET", "/public/catalogs/species", public=True, tags=[TAG_CAT],
           summary="Especies", response=CATALOG_ITEM)
def cat_species(ctx):
    return _envelope(ctx.env["shrimp.species"].sudo().search([("active", "=", True)]), S.species)


@api_route("GET", "/public/catalogs/stages", public=True, tags=[TAG_CAT],
           summary="Estadíos", response=CATALOG_ITEM)
def cat_stages(ctx):
    return _envelope(ctx.env["shrimp.stage"].sudo().search([("active", "=", True)]), S.stage)


@api_route("GET", "/public/catalogs/genetics-lines", public=True, tags=[TAG_CAT],
           summary="Líneas genéticas", response=CATALOG_ITEM)
def cat_genetics(ctx):
    return _envelope(ctx.env["shrimp.genetics.line"].sudo().search([("active", "=", True)]),
                     S.genetics_line)


@api_route("GET", "/public/catalogs/size-grades", public=True, tags=[TAG_CAT],
           summary="Tallas por presentación",
           params={"presentation": F("enum", enum=["entero", "cola"])},
           response=obj(id="uuid", name="string", presentation="string"))
def cat_size_grades(ctx):
    domain = [("active", "=", True)]
    pres = ctx.enum_param("presentation", ["entero", "cola"])
    if pres:
        domain.append(("presentation", "=", pres))
    return _envelope(ctx.env["shrimp.size.grade"].sudo().search(domain), S.size_grade)


@api_route("GET", "/public/catalogs/uoms", public=True, tags=[TAG_CAT],
           summary="Unidades de medida (sin la tarifa de comisión)", response=CATALOG_ITEM)
def cat_uoms(ctx):
    return _envelope(ctx.env["shrimp.uom"].sudo().search([("active", "=", True)]), S.uom)


@api_route("GET", "/public/catalogs/taste-criteria", public=True, tags=[TAG_CAT],
           summary="Criterios de cata del verificador", response=obj(id="uuid", name="string"))
def cat_taste(ctx):
    return _envelope(ctx.env["shrimp.taste.criterion"].sudo().search([("active", "=", True)]),
                     S.taste_criterion)


@api_route("GET", "/public/catalogs/certificate-types", public=True, tags=[TAG_CAT],
           summary="Tipos de certificado reconocidos",
           response=obj(id="uuid", name="string", issuer="string", role="string"))
def cat_certificates(ctx):
    return _envelope(ctx.env["shrimp.certificate"].sudo().search([("active", "=", True)]),
                     S.certificate_type)


@api_route("GET", "/public/catalogs/aguajes", public=True, tags=[TAG_CAT],
           summary="Calendario de aguajes (mareas vivas) de un año, o los elegibles para "
                   "una lista de precios (upcoming=true: el en curso y los próximos)",
           params={"year": F("integer", description="Año (por defecto, el actual)"),
                   "upcoming": F("boolean", description="Solo el aguaje en curso y los próximos "
                                                        "(los que admite una lista de precios)")},
           response=obj(id="uuid", name="string", label="string", year="integer", number="integer",
                        date_from="date", date_to="date", peak_from="date", peak_to="date",
                        status="string"))
def cat_aguajes(ctx):
    Aguaje = ctx.env["shrimp.aguaje"].sudo()
    if ctx.bool_param("upcoming"):
        return _envelope(Aguaje.seleccionables(limite=60), S.aguaje)
    year = ctx.int_param("year", fields.Date.today().year, 2000, 2100)
    return _envelope(Aguaje.search([("year", "=", year)], order="date_from"), S.aguaje)


# ---------------------------------------------------------------------------
# Marketplace
# ---------------------------------------------------------------------------
PUBLIC_PRODUCT_DOMAIN = [("active", "=", True), ("state", "=", "published"),
                         ("available_qty", ">", 0)]

PRODUCT_PUBLIC_SCHEMA = obj(
    id="uuid", name="string", seller="ref", seller_type="string", species="ref", stage="ref",
    genetics_line="ref", presentation="string", size_grade="ref", price="money", uom="ref",
    available_qty="number", location="string", expected_delivery_date="date",
    published_at="datetime", requires_verification="boolean",
    certificates={"type": "array", "items": obj(certificate="ref", issuer="string",
                                                number="string", expiry_date="date",
                                                valid="boolean")},
    updated_at="datetime")


def _catalog_filter(ctx, name, model, field, domain):
    value = ctx.params.get(name)
    if value:
        rec = ctx.resolve(model, value)
        if not rec:
            raise ApiError(400, "invalid-parameter", "«%s» no corresponde a ningún %s." % (name, model))
        domain.append((field, "=", rec.id))


@api_route("GET", "/public/products", public=True, tags=[TAG_MKT], paginated=True,
           summary="Lotes publicados en el marketplace",
           params={"species": F("string", description="uuid de especie"),
                   "stage": F("string", description="uuid de estadío"),
                   "genetics_line": F("string", description="uuid de línea genética"),
                   "size_grade": F("string", description="uuid de talla"),
                   "seller": F("string", description="uuid del vendedor"),
                   "presentation": F("enum", enum=["entero", "cola"]),
                   "seller_type": F("enum", enum=["semillero", "laboratorio", "camaronera"]),
                   "q": F("string", description="Texto en el nombre")},
           response=PRODUCT_PUBLIC_SCHEMA)
def public_products(ctx):
    domain = list(PUBLIC_PRODUCT_DOMAIN)
    _catalog_filter(ctx, "species", "shrimp.species", "species_id", domain)
    _catalog_filter(ctx, "stage", "shrimp.stage", "stage_id", domain)
    _catalog_filter(ctx, "genetics_line", "shrimp.genetics.line", "genetics_line_id", domain)
    _catalog_filter(ctx, "size_grade", "shrimp.size.grade", "size_grade_id", domain)
    if ctx.params.get("seller"):
        vendedores = ctx.env["res.partner"].sudo()._shrimp_types_with("sell_products")
        seller = ctx.resolve("res.partner", ctx.params["seller"],
                             domain=ctx.env["res.partner"].sudo()._shrimp_role_domain(vendedores))
        if not seller:
            raise ApiError(400, "invalid-parameter", "«seller» no corresponde a ningún vendedor.")
        domain.append(("seller_partner_id", "=", seller.id))
    pres = ctx.enum_param("presentation", ["entero", "cola"])
    if pres:
        domain.append(("presentation", "=", pres))
    stype = ctx.enum_param("seller_type", ["semillero", "laboratorio", "camaronera"])
    if stype:
        domain.append(("seller_role", "=", stype))
    q = (ctx.params.get("q") or "").strip()[:80]
    if q:
        domain.append(("name", "ilike", q))
    return paginate(ctx, "shrimp.product", domain, S.product_public, sudo=True)


@api_route("GET", "/public/products/{id}", public=True, tags=[TAG_MKT],
           summary="Detalle público de un lote publicado (metadatos de certificados, sin archivos)",
           response=PRODUCT_PUBLIC_SCHEMA)
def public_product(ctx, id):
    product = ctx.resolve("shrimp.product", id, domain=PUBLIC_PRODUCT_DOMAIN)
    if not product:
        raise not_found("El lote")
    return S.product_public(product)


# ---------------------------------------------------------------------------
# Directorios
# ---------------------------------------------------------------------------
PARTNER_PUBLIC_SCHEMA = obj(id="uuid", name="string", type="string", city="string",
                            province="string", country="string", ratings={"type": "object"})


def _directory_domain(kind, env):
    base = [("active", "=", True), ("shrimp_is_field_tech", "=", False)]
    # Varios perfiles por cuenta: por perfil (activo o aprobado), no por el
    # perfil con el que la cuenta navega ahora.
    Partner = env["res.partner"].sudo()
    if kind == "packers":
        return base + Partner._shrimp_role_domain("empacadora")
    if kind == "copackers":
        return base + Partner._shrimp_role_domain("maquilador") + [("pack_en_directorio", "=", True)]
    if kind == "verifiers":
        return base + Partner._shrimp_role_domain("verificador") + [("verifier_is_accredited", "=", True)]
    if kind == "sellers":
        sellers = env["shrimp.product"].sudo()._read_group(
            PUBLIC_PRODUCT_DOMAIN, ["seller_partner_id"])
        ids = [s.id for (s,) in sellers]
        return base + Partner._shrimp_role_domain(Partner._shrimp_types_with("sell_products")) + [
            ("id", "in", ids)]
    raise ValueError(kind)


def _directory(kind, summary):
    @api_route("GET", "/public/directory/%s" % kind, public=True, tags=[TAG_DIR], paginated=True,
               summary=summary, response=PARTNER_PUBLIC_SCHEMA)
    def handler(ctx):
        return paginate(ctx, "res.partner", _directory_domain(kind, ctx.env),
                        S.partner_public, sudo=True)
    handler.__name__ = "directory_%s" % kind
    return handler


directory_packers = _directory("packers", "Empacadoras")
directory_copackers = _directory("copackers", "Maquiladores que aparecen en el directorio")
directory_verifiers = _directory("verifiers", "Verificadores acreditados")
directory_sellers = _directory("sellers", "Vendedores con lotes publicados")


@api_route("GET", "/public/partners/{id}", public=True, tags=[TAG_DIR],
           summary="Perfil público de un socio de los directorios", response=PARTNER_PUBLIC_SCHEMA)
def public_partner(ctx, id):
    for kind in ("packers", "copackers", "verifiers", "sellers"):
        partner = ctx.resolve("res.partner", id, domain=_directory_domain(kind, ctx.env))
        if partner:
            return S.partner_public(partner)
    raise not_found("El socio")


# ---------------------------------------------------------------------------
# Honorario de verificación
# ---------------------------------------------------------------------------
@api_route("GET", "/public/verification-fee/quote", public=True, tags=[TAG_MKT],
           summary="Cotiza el honorario de verificación en campo para un lote",
           params={"lb": F("number", required=True, description="Libras (o unidades) a verificar")},
           response=obj(qty="number", base="money", variable="money", total="money",
                        capped="boolean", minimum_applied="boolean"))
def fee_quote(ctx):
    qty = ctx.float_param("lb", None, lo=0)
    if qty is None:
        raise ApiError(400, "invalid-parameter", "Falta «lb».")
    d = ctx.env["shrimp.verification.fee"].sudo().desglose(qty)
    cur = ctx.env.company.currency_id
    from .framework import money
    return {"qty": qty, "base": money(d["base"], cur), "variable": money(d["variable"], cur),
            "total": money(d["total"], cur), "capped": bool(d["topado"]),
            "minimum_applied": bool(d["minimo_aplicado"])}


# ---------------------------------------------------------------------------
# Trazabilidad pública
# ---------------------------------------------------------------------------
@api_route("GET", "/public/traceability/{token}", public=True, tags=[TAG_TRZ],
           summary="Trazabilidad pública de una compra (la del QR del certificado)",
           description="Cadena de custodia (empresa, rol, provincia), especie, genética, fechas, "
                       "resumen de evolución, certificados vigentes, veredicto de verificación y "
                       "planta. NO incluye precios, comisiones, GPS, teléfonos, técnicos ni archivos.",
           response=obj(status="string", product={"type": "object"},
                        chain={"type": "array", "items": obj(company="string", role="string",
                                                             province="string", country="string")},
                        dates={"type": "object"}, evolution={"type": ["object", "null"]},
                        certificates={"type": "array"}, verification={"type": ["object", "null"]},
                        packing={"type": "array"}))
def public_traceability(ctx, token):
    if not token or len(token) < 16 or len(token) > 64:
        raise not_found("La trazabilidad")
    tx = ctx.env["shrimp.transaction"].sudo().search([("trace_token", "=", token)], limit=1)
    if not tx:
        raise not_found("La trazabilidad")
    return tx._public_traceability_data()
