"""Retraso de entrega desde la API (shrimp_marketplace,
models/shrimp_transaction_delay.py). Rutas nuevas; las existentes no cambian.

  POST /transactions/{id}:notify-delay      vendedor: avisa el retraso
  POST /transactions/{id}:delay-accept      quien debe responder acepta
                                            (comprador: la fecha nueva;
                                             vendedor: la contrapropuesta)
  POST /transactions/{id}:delay-counter     comprador: propone otra fecha (una vez)
  POST /transactions/{id}:delay-reject      vendedor: rechaza la contrapropuesta
  POST /transactions/{id}:cancel            comprador: cancela sin costo (retraso, no
                                            entrega, verificación vencida);
                                            vendedor: cancela la venta antes del
                                            despacho (con strike, «reason» obligatorio)
  POST /transactions/{id}:cancel-request    cualquiera de las partes: pide cancelar
                                            de mutuo acuerdo
  POST /transactions/{id}:cancel-request-accept / -reject / -withdraw
  POST /transactions/{id}:switch-verifier   comprador: otra verificadora cuando la
                                            actual no cumplió su plazo (en una segunda
                                            opinión, el vendedor)
  GET  /transactions/{id}/verifier-options  qué se puede hacer con la verificadora
                                            vencida y las verificadoras elegibles

:cancel y :switch-verifier van firmadas con la clave de la app.

El detalle de la compra trae «delay» y «cancellation» (campos nuevos: los
clientes que no los conocen los ignoran).
"""
from odoo.http import request

from . import serializers as S
from .framework import ApiError, F, api_route, forbidden
from .private_trade import TAG_TX, TX_SCHEMA, _my_tx

REASONS = ["aguaje", "production", "sanitary", "transport", "other"]

NOTIFY_BODY = {
    "new_date": F("date", required=True, description="Nueva fecha de entrega (AAAA-MM-DD)"),
    "reason": F("enum", required=True, enum=REASONS,
                description="aguaje (aguaje / clima), production, sanitary, transport u other"),
    "note": F("text", description="Nota opcional para el comprador"),
}
COUNTER_BODY = {
    "date": F("date", required=True, description="Fecha que propone el comprador"),
    "note": F("text", description="Nota opcional para el vendedor"),
}
NOTE_BODY = {"note": F("text", description="Nota opcional")}
CANCEL_BODY = {
    "reason": F("text", description="Motivo (obligatorio si cancela el vendedor)"),
    "note": F("text", description="Nota opcional (compatibilidad: comprador)"),
}
REQUEST_BODY = {"reason": F("text", required=True, description="Por qué se pide cancelar")}
SWITCH_BODY = {"verifier": F("string", required=True,
                             description="id (uuid) de la nueva verificadora acreditada")}


def _side(ctx, tx):
    tx = tx.sudo()
    if tx.seller_partner_id == ctx.partner:
        return "seller"
    if tx.buyer_partner_id == ctx.partner:
        return "buyer"
    raise forbidden("No participas en esta compra.")


@api_route("POST", "/transactions/{id}:notify-delay", scope="transactions:write", tags=[TAG_TX],
           summary="El vendedor avisa un retraso de entrega",
           description="Solo compras con confirmación de recepción, confirmadas y sin despachar; "
                       "desde la confirmación hasta los días de gracia después de la fecha acordada. "
                       "Máximo de avisos y tope de días configurables (por defecto 2 avisos y 15 días "
                       "sobre la fecha original). El comprador tiene un plazo (48 h) para aceptar o "
                       "contraproponer; si no responde, la fecha nueva se acepta tácitamente.",
           body=NOTIFY_BODY, response=TX_SCHEMA)
def transaction_notify_delay(ctx, id):
    tx = _my_tx(ctx, id)
    if _side(ctx, tx) != "seller":
        raise forbidden("El retraso lo avisa el vendedor.")
    vals = ctx.clean(NOTIFY_BODY)
    tx.sudo().action_notify_delay(vals["new_date"], vals["reason"], note=vals.get("note"),
                                  actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:delay-accept", scope="transactions:write", tags=[TAG_TX],
           summary="Aceptar la fecha propuesta (comprador) o la contrapropuesta (vendedor)",
           body=NOTE_BODY, response=TX_SCHEMA)
def transaction_delay_accept(ctx, id):
    tx = _my_tx(ctx, id)
    ctx.clean(NOTE_BODY)
    if _side(ctx, tx) == "buyer":
        tx.sudo().action_delay_buyer_accept(actor=ctx.partner)
    else:
        tx.sudo().action_delay_seller_accept(actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:delay-counter", scope="transactions:write", tags=[TAG_TX],
           summary="El comprador propone otra fecha (una sola vez)",
           description="Dentro del tope. El vendedor tiene un plazo (48 h) para aceptarla o "
                       "rechazarla; si la rechaza o no responde, el comprador puede cancelar sin costo.",
           body=COUNTER_BODY, response=TX_SCHEMA)
def transaction_delay_counter(ctx, id):
    tx = _my_tx(ctx, id)
    if _side(ctx, tx) != "buyer":
        raise forbidden("La contrapropuesta la hace el comprador.")
    vals = ctx.clean(COUNTER_BODY)
    tx.sudo().action_delay_buyer_counter(vals["date"], note=vals.get("note"), actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:delay-reject", scope="transactions:write", tags=[TAG_TX],
           summary="El vendedor rechaza la contrapropuesta del comprador",
           body=NOTE_BODY, response=TX_SCHEMA)
def transaction_delay_reject(ctx, id):
    tx = _my_tx(ctx, id)
    if _side(ctx, tx) != "seller":
        raise forbidden("La contrapropuesta la responde el vendedor.")
    ctx.clean(NOTE_BODY)
    tx.sudo().action_delay_seller_reject(actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:cancel", signed=True, scope="transactions:write", tags=[TAG_TX],
           summary="Cancelar la compra (comprador sin costo, o vendedor antes del despacho)",
           description="Comprador: solo si cancellation.buyer_can_cancel (retraso: el vendedor "
                       "rechazó o no respondió la contrapropuesta, el retraso supera el tope o pasó "
                       "la gracia sin despacho ni aviso; «No entregada»: venció el plazo de "
                       "recepción sin despacho; verificación vencida). Vendedor: solo si "
                       "cancellation.seller_can_cancel (antes del despacho o de que la "
                       "verificadora vaya a campo), con «reason» obligatorio y strike. Devuelve el "
                       "stock al vendedor (o libera la reserva) y anula los cobros de la "
                       "plataforma (comisión, honorario de una verificación no realizada).",
           body=CANCEL_BODY, response=TX_SCHEMA)
def transaction_cancel(ctx, id):
    tx = _my_tx(ctx, id)
    lado = _side(ctx, tx)
    vals = ctx.clean(CANCEL_BODY)
    motivo = vals.get("reason") or vals.get("note")
    tx.sudo().shrimp_cancel(lado, reason=motivo, actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:cancel-request", scope="transactions:write", tags=[TAG_TX],
           summary="Pedir cancelar la compra de mutuo acuerdo",
           description="La otra parte tiene un plazo (48 h, configurable) para aceptar "
                       "(:cancel-request-accept) o rechazar; si no responde, la solicitud caduca. "
                       "Sin strike.",
           body=REQUEST_BODY, response=TX_SCHEMA)
def transaction_cancel_request(ctx, id):
    tx = _my_tx(ctx, id)
    _side(ctx, tx)
    vals = ctx.clean(REQUEST_BODY)
    tx.sudo().action_cancel_request(vals["reason"], actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:cancel-request-accept", scope="transactions:write",
           tags=[TAG_TX], summary="Aceptar la solicitud de cancelación de la otra parte",
           body=NOTE_BODY, response=TX_SCHEMA)
def transaction_cancel_request_accept(ctx, id):
    tx = _my_tx(ctx, id)
    _side(ctx, tx)
    vals = ctx.clean(NOTE_BODY)
    tx.sudo().action_cancel_request_respond(True, note=vals.get("note"), actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:cancel-request-reject", scope="transactions:write",
           tags=[TAG_TX], summary="Rechazar la solicitud de cancelación de la otra parte",
           body=NOTE_BODY, response=TX_SCHEMA)
def transaction_cancel_request_reject(ctx, id):
    tx = _my_tx(ctx, id)
    _side(ctx, tx)
    vals = ctx.clean(NOTE_BODY)
    tx.sudo().action_cancel_request_respond(False, note=vals.get("note"), actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:cancel-request-withdraw", scope="transactions:write",
           tags=[TAG_TX], summary="Retirar mi solicitud de cancelación", response=TX_SCHEMA)
def transaction_cancel_request_withdraw(ctx, id):
    tx = _my_tx(ctx, id)
    _side(ctx, tx)
    tx.sudo().action_cancel_request_withdraw(actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:switch-verifier", signed=True, scope="transactions:write",
           tags=[TAG_TX],
           summary="El comprador (o el vendedor, en una segunda opinión) cambia de verificadora "
                   "cuando la actual no cumplió su plazo o declinó la orden",
           description="Solo si cancellation.can_switch_verifier. La misma orden pasa a la nueva "
                       "verificadora acreditada (vuelve a «recibida», plazos desde ahora, mismo "
                       "honorario).",
           body=SWITCH_BODY, response=TX_SCHEMA)
def transaction_switch_verifier(ctx, id):
    tx = _my_tx(ctx, id)
    if _side(ctx, tx) != S.switch_verifier_side(tx):
        raise forbidden("La verificadora la cambia el comprador (en una segunda opinión, el "
                        "vendedor, que la pidió).")
    vals = ctx.clean(SWITCH_BODY)
    if not hasattr(tx.sudo(), "action_switch_verifier"):
        raise ApiError(409, "invalid-state", "Esta compra no tiene verificación.")
    nueva = request.env["res.partner"].sudo().resolve_ref(vals["verifier"])
    if not nueva:
        raise ApiError(404, "not-found", "La verificadora no existe.")
    tx.sudo().action_switch_verifier(nueva, actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


VERIFIER_OPTIONS_SCHEMA = {"type": "object", "properties": {
    "verification": {"type": "string"}, "verifier": {"$ref": "#/components/schemas/Ref"},
    "overdue": {"type": ["string", "null"]}, "decline_reason": {"type": ["string", "null"]},
    "chooser": {"type": "string"}, "can_switch": {"type": "boolean"},
    "can_cancel": {"type": "boolean"}, "auto_cancel_at": {"type": ["string", "null"]},
    "current_fee": {"type": "object"},
    "options": {"type": "array", "items": {"type": "object"}}}}


@api_route("GET", "/transactions/{id}/verifier-options", scope=("transactions:read", "transactions:write"),
           tags=[TAG_TX],
           summary="La verificadora no cumplió (o declinó): cambiar de verificadora o cancelar",
           description="Lo mismo que «Plazos de la verificadora» en la trazabilidad de la compra. "
                       "overdue: assign (sin técnico a tiempo), verdict (sin veredicto a tiempo), "
                       "declined (declinó la orden) o response (no aceptó ni declinó a tiempo). "
                       "chooser: quien cambia de verificadora (el comprador; en una segunda "
                       "opinión, el vendedor). can_switch y can_cancel (el comprador cancela sin "
                       "costo) son para quien consulta. options (solo si can_switch): verificadoras "
                       "acreditadas elegibles con el honorario que se cobraría (se recalcula con su "
                       "tarifa). auto_cancel_at: si nadie hace nada, la compra se cancela sola.",
           response=VERIFIER_OPTIONS_SCHEMA)
def transaction_verifier_options(ctx, id):
    tx = _my_tx(ctx, id).sudo()
    lado = _side(ctx, tx)
    ver = tx.verification_ids[:1] if "verification_ids" in tx._fields else None
    if not ver or ver.verification_mode != "platform":
        raise ApiError(409, "invalid-state", "Esta compra no tiene una verificadora de la plataforma.")
    abierta = tx.state not in ("done", "cancel")
    vencida = abierta and ver._shrimp_deadline_applies() and ver.deadline_overdue or False
    chooser = S.switch_verifier_side(tx)
    can_switch = bool(vencida and lado == chooser)
    can_cancel = bool(vencida and lado == "buyer" and tx.shrimp_buyer_cancel_cause())
    options = []
    if can_switch:
        # Ni la actual ni una de las partes (lo rechaza _shrimp_reassign_verifier).
        excluir = [tx.seller_partner_id.commercial_partner_id.id,
                   tx.buyer_partner_id.commercial_partner_id.id]
        elegibles = ctx.env["res.partner"].sudo().with_context(
            shrimp_exclude_partner_ids=excluir).available_verifiers().filtered(
            lambda p: p != ver.verifier_partner_id)
        options = [{"verifier": S.ref(p), "fee": S.money(ver.shrimp_fee_for(p), ver.currency_id)}
                   for p in elegibles.sorted(lambda p: (p.name or "").lower())]
    auto = ver.shrimp_auto_cancel_at() if vencida else False
    return {
        "verification": ver.uuid_ref,
        "verifier": S.ref(ver.verifier_partner_id),
        "overdue": vencida or None,
        "decline_reason": ver.verifier_decline_reason or None,
        "chooser": chooser,
        "can_switch": can_switch,
        "can_cancel": can_cancel,
        "auto_cancel_at": S.iso_dt(auto),
        "current_fee": S.money(ver.fee, ver.currency_id),
        "options": options,
    }
