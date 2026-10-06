"""Mover producto propio entre los perfiles de la misma cuenta (API).

* ``POST /lots/{id}:transfer-profile`` (lots:write): mueve todo o parte de un
  lote propio a otro perfil APROBADO de la cuenta. Transferencia interna: sin
  precio, comisión, factura ni verificación.
* ``POST /products/{id}:change-profile`` (products:write): el producto entero
  pasa a venderse con otro perfil (o, con ``qty``, solo esa cantidad).
* ``GET /profile-transfers`` y ``GET /profile-transfers/{id}``: historial.

Las reglas son las del portal (shrimp_marketplace/models/
shrimp_profile_transfer.py); un incumplimiento es un 422 con el motivo.
"""

from . import serializers as S
from .framework import F, api_route, iso_dt, not_found, obj, ref

TAG = "lotes e inventario"

TRANSFER_SPEC = {
    "to_role": F("string", required=True, max_length=32,
                 description="Perfil de destino (laboratorio, camaronera…), aprobado en tu cuenta."),
    "qty": F("number", minimum=0.000001,
             description="Cantidad a mover. Sin ella, todo lo disponible."),
    "reason": F("text", max_length=500),
}
CHANGE_SPEC = {
    "to_role": F("string", required=True, max_length=32),
    "qty": F("number", minimum=0.000001,
             description="Opcional: mover solo esta cantidad (crea un lote para el otro "
                         "perfil) en vez de cambiar el perfil del producto entero."),
    "reason": F("text", max_length=500),
}
TRANSFER_SCHEMA = obj(id="uuid", kind="string", date="datetime", from_role="string",
                      to_role="string", label="string", qty="number", uom="ref",
                      product="ref", target_product="ref", source_lot="uuid",
                      new_lots={"type": "array"}, moves={"type": "array"}, reason="string")


def transfer(t):
    t = t.sudo()
    return {
        "id": t.uuid_ref,
        "kind": t.kind,
        "date": iso_dt(t.date),
        "from_role": t.from_role,
        "to_role": t.to_role,
        "label": t.label(),
        "qty": t.qty,
        "uom": ref(t.uom_id),
        "product": ref(t.product_id),
        "target_product": ref(t.target_product_id),
        "source_lot": t.lot_id.uuid_ref or None,
        "new_lots": [S.lot(l) for l in t.new_lot_ids],
        "moves": [S.move(m) for m in t.move_ids],
        "reason": t.reason or None,
    }


@api_route("POST", "/lots/{id}:transfer-profile", scope="lots:write", tags=[TAG],
           body=TRANSFER_SPEC, status=201, response=TRANSFER_SCHEMA,
           summary="Mueve un lote propio (todo o parte) a otro perfil de tu cuenta")
def lot_transfer_profile(ctx, id):
    lot = ctx.get_own("shrimp.stock.lot", id, "El lote")
    vals = ctx.clean(TRANSFER_SPEC)
    t = lot.sudo().action_transfer_profile(
        vals["to_role"], qty=vals.get("qty"), reason=vals.get("reason"), actor=ctx.partner)
    return transfer(t)


@api_route("POST", "/products/{id}:change-profile", scope="products:write", tags=["productos"],
           body=CHANGE_SPEC, status=201, response=TRANSFER_SCHEMA,
           summary="Cambia el perfil con el que vendes un producto propio (o mueve una cantidad)")
def product_change_profile(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto")
    vals = ctx.clean(CHANGE_SPEC)
    if vals.get("qty"):
        t = product.sudo().action_transfer_qty_to_profile(
            vals["to_role"], vals["qty"], reason=vals.get("reason"), actor=ctx.partner)
    else:
        t = product.sudo().action_change_profile(
            vals["to_role"], reason=vals.get("reason"), actor=ctx.partner)
    return transfer(t)


@api_route("GET", "/profile-transfers", scope=("lots:read", "lots:write", "products:read",
                                               "products:write"),
           tags=[TAG], summary="Historial de movimientos entre mis perfiles",
           response={"type": "object", "properties": {"data": {"type": "array",
                                                               "items": TRANSFER_SCHEMA}}})
def transfers_list(ctx):
    recs = ctx.env["shrimp.profile.transfer"].sudo().search(
        [("partner_id", "=", ctx.partner.id)], limit=200)
    return {"data": [transfer(t) for t in recs],
            "meta": {"count": len(recs), "next_cursor": None}}


@api_route("GET", "/profile-transfers/{id}", scope=("lots:read", "lots:write", "products:read",
                                                    "products:write"),
           tags=[TAG], summary="Detalle de un movimiento entre mis perfiles",
           response=TRANSFER_SCHEMA)
def transfer_get(ctx, id):
    rec = ctx.resolve("shrimp.profile.transfer", id, domain=[("partner_id", "=", ctx.partner.id)])
    if not rec:
        raise not_found("El movimiento entre perfiles")
    return transfer(rec)
