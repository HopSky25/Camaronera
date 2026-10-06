"""Salidas / exportaciones: el último eslabón de la trazabilidad.

La empacadora (o quien tenga producto empacado) registra la exportación o
venta fuera de la plataforma: fecha, destino, DAE, factura, contenedor y de
qué lote(s) sale. Se descuenta del lote con un movimiento «export» y aparece
como paso final en la trazabilidad. Webhook: ``export.registered``.
"""

from . import serializers as S
from .framework import F, ApiError, api_route, obj, paginate

TAG = "salidas / exportaciones"
READ = ("exports:read", "exports:write")

EXPORT_SCHEMA = obj(id="uuid", reference="string", state="string", date="date",
                    destination_buyer="string", destination_country="string",
                    destination_place="string", confidential="boolean", dae_number="string",
                    invoice_number="string", container="string", boxes="integer",
                    qty="number", uom="ref", unit_price="money", total="money",
                    lines={"type": "array"}, updated_at="datetime")

LINE_SPEC = {
    "lot": F("string", required=True, description="uuid de un lote propio con saldo"),
    "qty": F("number", required=True, minimum=0.000001),
}

EXPORT_SPEC = {
    "date": F("date", required=True),
    "destination_buyer": F("string", max_length=200),
    "destination_country": F("string", max_length=2, description="Código ISO 3166-1 alfa-2 (ES, US, CN...)"),
    "destination_place": F("string", max_length=200),
    "confidential": F("boolean", description="Si es verdadero (por defecto), la trazabilidad pública "
                                             "no publica el comprador de destino"),
    "dae_number": F("string", max_length=60),
    "invoice_number": F("string", max_length=60),
    "container": F("string", max_length=60),
    "boxes": F("integer", minimum=0),
    "unit_price": F("number", minimum=0),
    "notes": F("text"),
    "lines": F("array", required=True, items=LINE_SPEC,
               description="De qué lote(s) sale el producto y cuánto de cada uno"),
}


@api_route("GET", "/exports", scope=READ, tags=[TAG], paginated=True,
           summary="Mis salidas / exportaciones",
           params={"state": F("enum", enum=["draft", "registered", "cancelled"])},
           response=EXPORT_SCHEMA)
def exports_list(ctx):
    domain = [("partner_id", "=", ctx.partner.id)]
    state = ctx.enum_param("state", ["draft", "registered", "cancelled"])
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.export", domain, S.export)


@api_route("GET", "/exports/{id}", scope=READ, tags=[TAG],
           summary="Detalle de una salida propia", response=EXPORT_SCHEMA)
def export_get(ctx, id):
    return S.export(ctx.get_own("shrimp.export", id, "La salida"))


@api_route("POST", "/exports", scope="exports:write", tags=[TAG], body=EXPORT_SPEC, status=201,
           summary="Registra una salida / exportación (descuenta los lotes y cierra la trazabilidad)",
           response=EXPORT_SCHEMA)
def export_create(ctx):
    vals = ctx.clean(EXPORT_SPEC)
    lines = []
    for i, item in enumerate(vals.get("lines") or []):
        lot = ctx.resolve("shrimp.stock.lot", item.get("lot"), own=True)
        if not lot:
            raise ApiError(422, "validation-error", "lines[%s].lot no es un lote tuyo." % i)
        lines.append((lot.sudo(), item.get("qty")))
    if not lines:
        raise ApiError(422, "validation-error", "«lines» tiene que traer al menos un lote.")
    datos = {
        "date": vals.get("date"),
        "destination_buyer": vals.get("destination_buyer") or False,
        "destination_place": vals.get("destination_place") or False,
        "dae_number": vals.get("dae_number") or False,
        "invoice_number": vals.get("invoice_number") or False,
        "container": vals.get("container") or False,
        "boxes": vals.get("boxes") or 0,
        "price_unit": vals.get("unit_price") or 0.0,
        "notes": vals.get("notes") or False,
        "confidential": True if vals.get("confidential") is None else bool(vals.get("confidential")),
    }
    code = (vals.get("destination_country") or "").strip().upper()
    if code:
        country = ctx.env["res.country"].sudo().search([("code", "=", code)], limit=1)
        if not country:
            raise ApiError(422, "validation-error", "«destination_country» no es un código de país válido.")
        datos["destination_country_id"] = country.id
    exp = ctx.env["shrimp.export"].sudo().shrimp_register(ctx.partner, datos, lines)
    return S.export(exp)


@api_route("POST", "/exports/{id}:cancel", scope="exports:write", tags=[TAG],
           summary="Anula una salida propia (la cantidad vuelve a los lotes)",
           body={"reason": F("text")}, response=EXPORT_SCHEMA)
def export_cancel(ctx, id):
    exp = ctx.get_own("shrimp.export", id, "La salida")
    reason = (ctx.body or {}).get("reason") or "Anulada por API"
    exp.sudo().action_cancel(reason=reason, actor=ctx.partner)
    return S.export(exp)
