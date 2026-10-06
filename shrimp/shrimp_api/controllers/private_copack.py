"""Servicio de empaque (maquila): solicitudes, ofertas, órdenes, acta y tarifas."""

from . import serializers as S
from .framework import ApiError, F, api_route, forbidden, invalid, obj, paginate, role_domain

TAG = "maquila (servicio de empaque)"
READ = ("copack:read", "copack:write")
# Dominio estático del esquema (OpenAPI); la regla de quién pide empaque es
# la capacidad "request_copack" de la matriz.
CLIENT_TYPES = ("camaronera", "empacadora")

REQ_SCHEMA = obj(id="uuid", reference="string", my_role="string", client="ref", copacker="ref",
                 state="string", quantity_lb="number", presentation="string", size_grade="ref",
                 needed_from="date", needed_to="date", offers={"type": "array"}, order="string",
                 updated_at="datetime")
OFFER_SCHEMA = obj(id="uuid", request="string", copacker="ref", client="ref", state="string",
                   rate_per_lb="money", capacity_lb="number", available_from="date",
                   available_to="date", estimated_total="money")
ORDER_SCHEMA = obj(id="uuid", reference="string", my_role="string", state="string",
                   acceptance_state="string", self_packing="boolean", client="ref", copacker="ref",
                   agreed_qty_lb="number",
                   rate_per_lb="money", received_lb="number", packed_lb="number", boxes="integer",
                   difference_lb="number", balanced="boolean", service_amount="money",
                   signatures={"type": "array"}, updated_at="datetime")
TARIFF_SCHEMA = obj(id="uuid", name="string", my_role="string", copacker="ref", state="string",
                    valid_from="date", valid_to="date", is_current="boolean",
                    lines={"type": "array"}, updated_at="datetime")


# ---------------------------------------------------------------------------
# Solicitudes
# ---------------------------------------------------------------------------
@api_route("GET", "/copack/requests", scope=READ, tags=[TAG], paginated=True,
           summary="Mis solicitudes (cliente) o la bandeja de solicitudes que puedo ofertar (maquilador)",
           params={"state": F("enum", enum=["draft", "published", "assigned", "done", "cancelled"])},
           response=REQ_SCHEMA)
def requests_list(ctx):
    domain = []
    state = ctx.enum_param("state", ["draft", "published", "assigned", "done", "cancelled"])
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.copack.request", domain,
                    lambda r: S.copack_request(r, ctx.partner, detail=True))


@api_route("GET", "/copack/requests/{id}", scope=READ, tags=[TAG],
           summary="Detalle de una solicitud (el cliente ve todas sus ofertas; el maquilador, la suya)",
           response=REQ_SCHEMA)
def request_get(ctx, id):
    return S.copack_request(ctx.get_own("shrimp.copack.request", id, "La solicitud"),
                            ctx.partner, detail=True)


REQ_SPEC = {
    "quantity_lb": F("number", required=True, minimum=0.01),
    "presentation": F("enum", required=True, enum=["entero", "cola", "valor_agregado"]),
    "size_grade": F("ref", model="shrimp.size.grade", odoo="size_grade_id"),
    "needed_from": F("date", required=True),
    "needed_to": F("date", required=True),
    "supplies_notes": F("text"),
    "notes": F("text"),
    "copacker": F("string", description="uuid de un maquilador del directorio o con quien ya trabajaste. "
                                        "Vacío = abierta a todos."),
    "product": F("ref", model="shrimp.product", own=True, odoo="product_id",
                 description="Lote propio a enlazar con la trazabilidad"),
}


def _visible_copackers_domain(ctx):
    """Mismo criterio que el portal: los del directorio y aquellos con los que
    ya hay una orden."""
    previous = ctx.env["shrimp.copack.order"].sudo().search(
        [("client_partner_id", "=", ctx.partner.id)]).mapped("copacker_partner_id").ids
    return role_domain("maquilador") + [
        ("id", "!=", ctx.partner.id),
        "|", ("pack_en_directorio", "=", True), ("id", "in", previous)]


@api_route("POST", "/copack/requests", scope="copack:write", tags=[TAG], body=REQ_SPEC, status=201,
           summary="Publica una solicitud de empaque (cliente: camaronera o empacadora)",
           response=REQ_SCHEMA)
def request_create(ctx):
    ctx.require_capability("request_copack", what="El empaque lo pide quien es dueño del camarón.")
    vals = ctx.clean(REQ_SPEC)
    copacker_ref = vals.pop("copacker", None)
    if copacker_ref:
        copacker = ctx.resolve("res.partner", copacker_ref, domain=_visible_copackers_domain(ctx))
        if not copacker:
            raise invalid("Ese maquilador no existe o no está a tu alcance.")
        vals["copacker_partner_id"] = copacker.id
    vals["client_partner_id"] = ctx.partner.id
    req = ctx.env["shrimp.copack.request"].sudo().create(vals)
    req.action_publish()
    return S.copack_request(req, ctx.partner, detail=True)


@api_route("POST", "/copack/requests/{id}:cancel", scope="copack:write", tags=[TAG],
           summary="El cliente cancela su solicitud (antes de empacar)", response=REQ_SCHEMA)
def request_cancel(ctx, id):
    req = ctx.get_own("shrimp.copack.request", id, "La solicitud").sudo()
    if req.client_partner_id != ctx.partner:
        raise forbidden("La solicitud la cancela el cliente.")
    if req.state not in ("draft", "published"):
        raise ApiError(409, "invalid-state", "Ya está adjudicada: se resuelve en la orden.")
    req.action_cancel()
    return S.copack_request(req, ctx.partner, detail=True)


OFFER_SPEC = {
    "rate_per_lb": F("number", required=True, minimum=0),
    "capacity_lb": F("number", required=True, minimum=0.01),
    "available_from": F("date", required=True),
    "available_to": F("date", required=True),
    "notes": F("text"),
}


@api_route("POST", "/copack/requests/{id}/offers", scope="copack:write", tags=[TAG], body=OFFER_SPEC,
           status=201, summary="El maquilador oferta (o rectifica su oferta) sobre una solicitud",
           response=OFFER_SCHEMA)
def offer_create(ctx, id):
    ctx.require_type("maquilador")
    req = ctx.get_own("shrimp.copack.request", id, "La solicitud").sudo()
    if req.copacker_partner_id and req.copacker_partner_id != ctx.partner:
        raise forbidden("Esta solicitud está dirigida a otro maquilador.")
    if req.state != "published":
        raise ApiError(409, "invalid-state", "Esta solicitud ya no admite ofertas.")
    vals = ctx.clean(OFFER_SPEC)
    Offer = ctx.env["shrimp.copack.offer"].sudo()
    mine = Offer.search([("request_id", "=", req.id), ("copacker_partner_id", "=", ctx.partner.id)], limit=1)
    if mine:
        mine.write(vals)
    else:
        mine = Offer.create(dict(vals, request_id=req.id, copacker_partner_id=ctx.partner.id))
    return S.copack_offer(mine)


@api_route("POST", "/copack/offers/{id}:withdraw", scope="copack:write", tags=[TAG],
           summary="El maquilador retira su oferta", response=OFFER_SCHEMA)
def offer_withdraw(ctx, id):
    offer = ctx.get_own("shrimp.copack.offer", id, "La oferta").sudo()
    if offer.copacker_partner_id != ctx.partner:
        raise forbidden("La oferta la retira el maquilador que la hizo.")
    offer.action_withdraw(actor=ctx.partner)
    return S.copack_offer(offer)


@api_route("POST", "/copack/offers/{id}:accept", scope="copack:write", tags=[TAG],
           summary="El cliente acepta una oferta: nace la orden de empaque", response=ORDER_SCHEMA)
def offer_accept(ctx, id):
    offer = ctx.get_own("shrimp.copack.offer", id, "La oferta").sudo()
    if offer.request_id.client_partner_id != ctx.partner:
        raise forbidden("La oferta la acepta quien pidió el servicio.")
    order = offer.action_accept(actor=ctx.partner)
    return S.copack_order(order, ctx.partner, detail=True)


# ---------------------------------------------------------------------------
# Órdenes
# ---------------------------------------------------------------------------
@api_route("GET", "/copack/orders", scope=READ, tags=[TAG], paginated=True,
           summary="Órdenes de empaque en las que soy parte",
           params={"state": F("enum", enum=["confirmed", "received", "packed", "signed", "closed", "cancelled"])},
           response=ORDER_SCHEMA)
def orders_list(ctx):
    domain = []
    state = ctx.enum_param("state", ["confirmed", "received", "packed", "signed", "closed", "cancelled"])
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.copack.order", domain, lambda o: S.copack_order(o, ctx.partner))


@api_route("GET", "/copack/orders/{id}", scope=READ, tags=[TAG],
           summary="Detalle de una orden con las firmas del acta", response=ORDER_SCHEMA)
def order_get(ctx, id):
    return S.copack_order(ctx.get_own("shrimp.copack.order", id, "La orden"), ctx.partner, detail=True)


SELF_PACK_SPEC = {
    "quantity_lb": F("number", required=True, minimum=0.01),
    "product": F("string", description="uuid de un lote PROPIO (lo que vendes como camaronera)"),
    "purchase": F("string", description="uuid de una compra tuya confirmada o recibida "
                                        "(lo que compraste como empacadora)"),
    "supplies_notes": F("text"),
    "tolerance_pct": F("number", minimum=0),
}


@api_route("POST", "/copack/orders", scope="copack:write", tags=[TAG], body=SELF_PACK_SPEC, status=201,
           summary="Registra un EMPAQUE PROPIO (self_packing): la cuenta empaca su camarón en su planta",
           description="Solo para cuentas con el perfil Maquilador APROBADO que además son dueñas del "
                       "camarón (camaronera o empacadora). Sin solicitud, ofertas, tarifa, comisión ni "
                       "acta de dos partes: luego :reception, :packing y :close (cierre interno). Un "
                       "empaque de terceros sigue naciendo de una oferta aceptada.",
           response=ORDER_SCHEMA)
def order_self_create(ctx):
    vals = ctx.clean(SELF_PACK_SPEC)
    partner = ctx.partner.sudo()
    if not partner._shrimp_self_pack_allowed():
        raise forbidden("Para empacar tu propio producto necesitas el perfil Maquilador (aprobado).")
    product, purchase = vals.pop("product", None), vals.pop("purchase", None)
    if bool(product) == bool(purchase):
        raise invalid("Indica el camarón a empacar: «product» (lote propio) o «purchase» (compra), uno solo.")
    origen = ("p:%s" % product) if product else ("t:%s" % purchase)
    order = ctx.env["shrimp.copack.order"].sudo().shrimp_create_self_packing(
        partner, origen, vals.pop("quantity_lb"), **vals)
    return S.copack_order(order, ctx.partner, detail=True)


def _order(ctx, id, copacker_only=False):
    order = ctx.get_own("shrimp.copack.order", id, "La orden").sudo()
    if copacker_only and order.copacker_partner_id != ctx.partner:
        raise forbidden("Esta acción es del maquilador.")
    return order


@api_route("POST", "/copack/orders/{id}:reception", scope="copack:write", tags=[TAG],
           body={"received_lb": F("number", required=True, minimum=0.01),
                 "supplies_received": F("boolean"), "supplies_issue": F("text")},
           summary="El maquilador registra la recepción (libras e insumos)", response=ORDER_SCHEMA)
def order_reception(ctx, id):
    order = _order(ctx, id, copacker_only=True)
    vals = ctx.clean({"received_lb": F("number", required=True, minimum=0.01),
                      "supplies_received": F("boolean"), "supplies_issue": F("text")})
    order.write(vals)
    order.action_register_reception()
    return S.copack_order(order, ctx.partner, detail=True)


PACKING_SPEC = {"packed_lb": F("number", required=True, minimum=0.01),
                "boxes": F("integer", minimum=0),
                "packed_presentation": F("string", description=(
                    "entero, cola o valor_agregado. Por compatibilidad se admite "
                    "texto libre: se guarda como detalle (packed_presentation_note).")),
                "packed_presentation_note": F("string", max_length=200)}


@api_route("POST", "/copack/orders/{id}:packing", scope="copack:write", tags=[TAG],
           body=PACKING_SPEC,
           summary="El maquilador registra lo empacado: se abre el acta a la firma", response=ORDER_SCHEMA)
def order_packing(ctx, id):
    order = _order(ctx, id, copacker_only=True)
    vals = ctx.clean(PACKING_SPEC)
    presentacion = vals.get("packed_presentation")
    validas = dict(order._fields["packed_presentation"].selection)
    if presentacion and presentacion not in validas:
        vals["packed_presentation_note"] = vals.get("packed_presentation_note") or presentacion
        vals["packed_presentation"] = False
    order.write(vals)
    order.action_register_packing()
    return S.copack_order(order, ctx.partner, detail=True)


@api_route("POST", "/copack/orders/{id}:sign", scope="copack:write", tags=[TAG],
           body={"decision": F("enum", required=True, enum=["accepted", "rejected"]),
                 "reason": F("text", description="Obligatorio si no se acepta")},
           summary="Cada parte firma (o rechaza) el acta de cuadre", response=ORDER_SCHEMA)
def order_sign(ctx, id):
    order = _order(ctx, id)
    vals = ctx.clean({"decision": F("enum", required=True, enum=["accepted", "rejected"]),
                      "reason": F("text")})
    role = "copacker" if order.copacker_partner_id == ctx.partner else "client"
    sign = order.acceptance_ids.filtered(lambda f: f.role == role)[:1]
    if not sign:
        raise ApiError(409, "invalid-state", "El acta no está abierta a la firma.")
    if vals["decision"] == "accepted":
        sign.action_accept(actor=ctx.partner)
    else:
        sign.action_reject(vals.get("reason") or "", actor=ctx.partner)
    return S.copack_order(order, ctx.partner, detail=True)


@api_route("POST", "/copack/orders/{id}:revert-signature", scope="copack:write", tags=[TAG],
           body={"reason": F("text", description="Por qué se deshace (recomendado)")},
           summary="Cada parte deshace SU firma del acta mientras el acta no esté cerrada",
           description="La firma propia vuelve a «pending». Se puede mientras la orden siga "
                       "empacada con el acta abierta o en disputa (no tras el cierre con dos "
                       "conformes ni tras reabrirla). Emite copack.signature_reverted.",
           response=ORDER_SCHEMA)
def order_revert_signature(ctx, id):
    order = _order(ctx, id)
    vals = ctx.clean({"reason": F("text")})
    role = "copacker" if order.copacker_partner_id == ctx.partner else "client"
    sign = order.acceptance_ids.filtered(lambda f: f.role == role)[:1]
    if not sign:
        raise ApiError(409, "invalid-state", "El acta no está abierta a la firma.")
    sign.action_signoff_undo(reason=vals.get("reason") or None, actor=ctx.partner)
    return S.copack_order(order, ctx.partner, detail=True)


@api_route("POST", "/copack/orders/{id}:reopen", scope="copack:write", tags=[TAG],
           body={"reason": F("text", required=True)},
           summary="Reabre un acta en disputa (cualquiera de las partes, con motivo)", response=ORDER_SCHEMA)
def order_reopen(ctx, id):
    order = _order(ctx, id)
    vals = ctx.clean({"reason": F("text", required=True)})
    order.action_reabrir_acta(vals["reason"], actor=ctx.partner)
    return S.copack_order(order, ctx.partner, detail=True)


@api_route("POST", "/copack/orders/{id}:close", scope="copack:write", tags=[TAG],
           summary="Cierra una orden con el acta firmada", response=ORDER_SCHEMA)
def order_close(ctx, id):
    order = _order(ctx, id)
    order.action_close()
    return S.copack_order(order, ctx.partner, detail=True)


@api_route("POST", "/copack/orders/{id}:cancel", scope="copack:write", tags=[TAG],
           summary="Cancela una orden aún no firmada (cualquiera de las partes)", response=ORDER_SCHEMA)
def order_cancel(ctx, id):
    order = _order(ctx, id)
    order.action_cancel()
    return S.copack_order(order, ctx.partner, detail=True)


# ---------------------------------------------------------------------------
# Tarifas
# ---------------------------------------------------------------------------
TARIFF_LINE = {
    "presentation": F("enum", required=True, enum=["entero", "cola", "valor_agregado"]),
    "pack_format": F("string", required=True),
    "from_lb": F("number", minimum=0),
    "rate_per_lb": F("number", required=True, minimum=0),
}
TARIFF_SPEC = {
    "name": F("string", required=True),
    "valid_from": F("date"),
    "valid_to": F("date"),
    "open_ended": F("boolean"),
    "min_lot_lb": F("number", minimum=0),
    "payment_notes": F("string"),
    "conditions": F("text"),
    "recipients": F("array", items={}, description="uuid de clientes con los que ya trabajas "
                                                   "(te dirigieron una solicitud o tienes una orden)"),
    "lines": F("array", items=TARIFF_LINE, description="Reemplaza todos los renglones"),
}
TARIFF_SPEC["recipients"] = F("refs", model="res.partner", odoo="recipient_ids",
                              domain=role_domain(*CLIENT_TYPES),
                              description=TARIFF_SPEC["recipients"].description)


@api_route("GET", "/copack/tariffs", scope=READ, tags=[TAG], paginated=True,
           summary="Tarifas que publico (maquilador) o que me dirigieron (cliente)", response=TARIFF_SCHEMA)
def tariffs_list(ctx):
    return paginate(ctx, "shrimp.copack.tariff", [],
                    lambda t: S.copack_tariff(t, ctx.partner, detail=True))


@api_route("GET", "/copack/tariffs/{id}", scope=READ, tags=[TAG],
           summary="Detalle de una tarifa", response=TARIFF_SCHEMA)
def tariff_get(ctx, id):
    return S.copack_tariff(ctx.get_own("shrimp.copack.tariff", id, "La tarifa"), ctx.partner, detail=True)


def _allowed_clients(ctx):
    """Mismo filtro que el portal: solo contrapartes reales del maquilador."""
    Req = ctx.env["shrimp.copack.request"].sudo()
    Order = ctx.env["shrimp.copack.order"].sudo()
    ids = set(Req.search([("copacker_partner_id", "=", ctx.partner.id)]).mapped("client_partner_id").ids)
    ids |= set(Order.search([("copacker_partner_id", "=", ctx.partner.id)]).mapped("client_partner_id").ids)
    return ids


def _tariff_vals(ctx, vals, tariff=None):
    if "recipient_ids" in vals:
        allowed = _allowed_clients(ctx) | (set(tariff.recipient_ids.ids) if tariff else set())
        bad = [i for i in vals["recipient_ids"] if i not in allowed]
        if bad:
            raise invalid("Solo puedes dirigir la tarifa a clientes con los que ya trabajas.")
        vals["recipient_ids"] = [(6, 0, vals["recipient_ids"])]
    if "lines" in vals:
        vals["line_ids"] = [(5, 0, 0)] + [(0, 0, l) for l in vals.pop("lines")]
    return vals


@api_route("POST", "/copack/tariffs", scope="copack:write", tags=[TAG], body=TARIFF_SPEC, status=201,
           summary="Crea una tarifa en borrador (maquilador)", response=TARIFF_SCHEMA)
def tariff_create(ctx):
    ctx.require_type("maquilador")
    vals = _tariff_vals(ctx, ctx.clean(TARIFF_SPEC))
    vals = {k: v for k, v in vals.items() if v is not None}
    vals.update({"copacker_partner_id": ctx.partner.id, "state": "draft"})
    if not vals.get("valid_from"):
        vals.pop("valid_from", None)
    tariff = ctx.env["shrimp.copack.tariff"].sudo().create(vals)
    return S.copack_tariff(tariff, ctx.partner, detail=True)


def _my_tariff(ctx, id):
    tariff = ctx.get_own("shrimp.copack.tariff", id, "La tarifa").sudo()
    if tariff.copacker_partner_id != ctx.partner:
        raise forbidden("La tarifa la modifica el maquilador que la publica.")
    return tariff


@api_route("PUT", "/copack/tariffs/{id}", scope="copack:write", tags=[TAG], body=TARIFF_SPEC,
           summary="Reemplaza la tarifa (cabecera, destinatarios y renglones)", response=TARIFF_SCHEMA)
def tariff_put(ctx, id):
    tariff = _my_tariff(ctx, id)
    vals = _tariff_vals(ctx, ctx.clean(TARIFF_SPEC), tariff)
    tariff.write(vals)
    return S.copack_tariff(tariff, ctx.partner, detail=True)


@api_route("POST", "/copack/tariffs/{id}:publish", scope="copack:write", tags=[TAG],
           summary="Publica la tarifa a sus destinatarios", response=TARIFF_SCHEMA)
def tariff_publish(ctx, id):
    tariff = _my_tariff(ctx, id)
    tariff.action_publish()
    return S.copack_tariff(tariff, ctx.partner, detail=True)


@api_route("POST", "/copack/tariffs/{id}:archive", scope="copack:write", tags=[TAG],
           summary="Archiva la tarifa", response=TARIFF_SCHEMA)
def tariff_archive(ctx, id):
    tariff = _my_tariff(ctx, id)
    tariff.action_archive_tariff()
    return S.copack_tariff(tariff, ctx.partner, detail=True)
