"""Compras y ventas: transacciones, trazabilidad y cobros (las solicitudes de chequeo
se retiraron: 410 Gone)."""

from odoo.http import request

from . import serializers as S
from .framework import ApiError, ApiResponse, F, api_route, forbidden, obj, paginate

TAG_TX = "compras y ventas"
TAG_CHK = "solicitudes de chequeo (retirado)"

TX_SCHEMA = obj(id="uuid", reference="string", my_role="string", state="string", type="string",
                product="ref", seller="ref", buyer="ref", qty="number", uom="ref",
                unit_price="money", total="money", delivery_date="date",
                verification="string", dispatch="string",
                seller_invoice={"type": ["object", "null"],
                                "description": "Factura de la mercadería registrada por el vendedor "
                                               "(número, clave de acceso, fecha, si hay archivo)"},
                reception={"type": ["object", "null"],
                           "description": "Entrega y recepción: si la compra pide confirmación de "
                                          "recepción, despacho, plazo (deadline, days_left), si se "
                                          "confirmó automáticamente e incidencia"},
                updated_at="datetime")
TX_STATES = ["draft", "pending_verification", "pending_acceptance", "confirmed", "done", "cancel"]


@api_route("GET", "/transactions", scope=("transactions:read", "transactions:write"), tags=[TAG_TX],
           paginated=True, summary="Mis compras y ventas",
           params={"role": F("enum", enum=["buyer", "seller"], description="Solo compras o solo ventas"),
                   "state": F("enum", enum=TX_STATES)},
           response=TX_SCHEMA)
def transactions_list(ctx):
    role = ctx.enum_param("role", ["buyer", "seller"])
    if role == "buyer":
        domain = [("buyer_partner_id", "=", ctx.partner.id)]
    elif role == "seller":
        domain = [("seller_partner_id", "=", ctx.partner.id)]
    else:
        domain = ["|", ("buyer_partner_id", "=", ctx.partner.id),
                  ("seller_partner_id", "=", ctx.partner.id)]
    state = ctx.enum_param("state", TX_STATES)
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.transaction", domain, lambda t: S.transaction(t, ctx.partner))


def _my_tx(ctx, id):
    return ctx.get_own("shrimp.transaction", id, "La transacción", domain=[
        "|", ("buyer_partner_id", "=", ctx.partner.id), ("seller_partner_id", "=", ctx.partner.id)])


@api_route("GET", "/transactions/{id}", scope=("transactions:read", "transactions:write"),
           tags=[TAG_TX], summary="Detalle de una compra o venta propia", response=TX_SCHEMA)
def transaction_get(ctx, id):
    return S.transaction(_my_tx(ctx, id), ctx.partner)


@api_route("GET", "/transactions/{id}/traceability", scope=("transactions:read", "transactions:write"),
           tags=[TAG_TX], summary="Trazabilidad completa para las partes de la compra",
           description="Lo público (cadena, certificados, veredicto...) más el detalle operativo: "
                       "movimientos, lotes, siembras en piscina y evolución. Sin precios.",
           response=obj(public={"type": "object"}, moves={"type": "array"}, lots={"type": "array"},
                        allocations={"type": "array"}, evolutions={"type": "array"},
                        own_lots={"type": "array"}, exports={"type": "array"},
                        public_url="string"))
def transaction_traceability(ctx, id):
    tx = _my_tx(ctx, id).sudo()
    data = tx.get_full_traceability_data()
    return {
        "public": tx._public_traceability_data(),
        "public_url": tx.traceability_url(),
        # D-29: si la página pública se muestra (public / cancelled) o no
        # (withdrawn / unavailable). public_url se conserva igual.
        "public_status": tx.shrimp_trace_public_status(),
        "moves": [S.move(m) for m in data["moves"]],
        "lots": [{"id": l.uuid_ref, "product": S.ref(l.product_id), "owner": S.ref(l.owner_id),
                  "initial_qty": l.initial_qty, "uom": S.ref(l.uom_id)} for l in data["lots"]],
        "allocations": [{"lot": a.stock_lot_id.uuid_ref, "pond": S.ref(a.pond_id),
                         "allocated_qty": a.allocated_qty,
                         "allocation_date": S.iso_date(a.allocation_date)} for a in data["allocations"]],
        "evolutions": [S.evolution(e) for e in data["evolutions"]],
        # Saldo solo de los lotes propios (el de los demás es stock ajeno).
        "own_lots": [S.lot(l) for l in data["own_lots"].filtered(lambda l: l.owner_id == ctx.partner)],
        "exports": [S.export(e) if e.partner_id == ctx.partner else dict(e.public_dict(), id=e.uuid_ref)
                    for e in data["exports"].filtered(lambda e: e.state == "registered")],
    }


@api_route("GET", "/transactions/{id}/traceability.pdf", scope=("transactions:read", "transactions:write"),
           tags=[TAG_TX], binary="application/pdf",
           summary="Certificado de trazabilidad en PDF (solo el comprador, como en el portal)")
def transaction_traceability_pdf(ctx, id):
    tx = _my_tx(ctx, id)
    if tx.sudo().buyer_partner_id != ctx.partner:
        raise forbidden("El certificado de trazabilidad lo descarga el comprador.")
    report = request.env["ir.actions.report"].sudo()._get_report_from_name(
        "shrimp_marketplace.report_shrimp_full_traceability")
    if not report:
        raise ApiError(404, "not-found", "El informe no está disponible.")
    pdf, _fmt = report.sudo()._render_qweb_pdf(report.report_name, res_ids=[tx.id])
    name = (tx.sudo().name or "trazabilidad").replace("/", "-")
    return ApiResponse(raw=pdf, content_type="application/pdf", headers=[
        ("Content-Disposition", 'attachment; filename="Trazabilidad-%s.pdf"' % name)])


@api_route("POST", "/transactions/{id}/trace-token:rotate", scope="transactions:write", tags=[TAG_TX],
           summary="Invalida el QR público actual y genera uno nuevo",
           response=obj(public_url="string"))
def transaction_rotate_token(ctx, id):
    tx = _my_tx(ctx, id)
    tx.sudo().action_rotate_trace_token()
    return {"public_url": tx.sudo().traceability_url()}


RECEIVE_BODY = {"note": F("text", description="Nota opcional de la recepción")}
ISSUE_BODY = {"note": F("text", required=True, description="Qué problema hubo con la entrega")}
DISPATCH_BODY = {"note": F("text", description="Nota opcional para el comprador")}


@api_route("POST", "/transactions/{id}:receive", scope="transactions:write", tags=[TAG_TX],
           summary="El comprador confirma la recepción (desde la fecha de entrega o el despacho)",
           description="Cuerpo opcional ({note}). Sube el lote al inventario del comprador y "
                       "deja la compra completada. Si la compra pide confirmación de recepción y "
                       "el comprador no la confirma en el plazo, la plataforma la confirma sola.",
           body=RECEIVE_BODY, response=TX_SCHEMA)
def transaction_receive(ctx, id):
    tx = _my_tx(ctx, id)
    if tx.sudo().buyer_partner_id != ctx.partner:
        raise forbidden("La recepción la confirma el comprador.")
    vals = ctx.clean(RECEIVE_BODY)
    tx.sudo().shrimp_confirm_reception(note=vals.get("note"), actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:report-issue", scope="transactions:write", tags=[TAG_TX],
           summary="El comprador reporta un problema con la entrega en vez de confirmarla",
           description="La compra queda con incidencia (reception.status = incidence): no se "
                       "confirma automáticamente y la plataforma la revisa.",
           body=ISSUE_BODY, response=TX_SCHEMA)
def transaction_report_issue(ctx, id):
    tx = _my_tx(ctx, id)
    if tx.sudo().buyer_partner_id != ctx.partner:
        raise forbidden("El problema lo reporta el comprador.")
    vals = ctx.clean(ISSUE_BODY)
    tx.sudo().shrimp_report_reception_issue(vals.get("note"), actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:dispatch", scope="transactions:write", tags=[TAG_TX],
           summary="El vendedor marca la compra como despachada",
           description="Desde el despacho el comprador puede confirmar la recepción y, si la "
                       "compra la pide, empieza a correr su plazo.",
           body=DISPATCH_BODY, response=TX_SCHEMA)
def transaction_dispatch(ctx, id):
    tx = _my_tx(ctx, id)
    if tx.sudo().seller_partner_id != ctx.partner:
        raise forbidden("El despacho lo marca el vendedor.")
    vals = ctx.clean(DISPATCH_BODY)
    tx.sudo().action_mark_dispatched(note=vals.get("note"), actor=ctx.partner)
    return S.transaction(tx, ctx.partner)


@api_route("POST", "/transactions/{id}:complete", scope="transactions:write", tags=[TAG_TX],
           summary="El comprador concluye una compra verificada y aceptada por las partes",
           response=TX_SCHEMA)
def transaction_complete(ctx, id):
    tx = _my_tx(ctx, id)
    if tx.sudo().buyer_partner_id != ctx.partner:
        raise forbidden("La compra la concluye el comprador.")
    tx.sudo().action_complete_after_verification()
    return S.transaction(tx, ctx.partner)


@api_route("GET", "/charges", scope=("transactions:read", "transactions:write"), tags=[TAG_TX],
           paginated=True, summary="Servicios que me factura la plataforma",
           description="Comisión por venta (al vendedor), honorario de verificación (a quien "
                       "lo paga) y comisión de empaque (al maquilador).",
           response=obj(id="uuid", reference="string", type="string", state="string",
                        transaction="ref", qty="number", amount="money", date="datetime",
                        invoice_number="string", invoice_state="string"))
def charges_list(ctx):
    return paginate(ctx, "shrimp.charge", [("payer_partner_id", "=", ctx.partner.id)], S.charge)


# ---------------------------------------------------------------------------
# Solicitudes de chequeo: RETIRADAS (decisión D-28)
# ---------------------------------------------------------------------------
# El flujo se retiró por completo: se compra directo o con verificación en
# campo. Las rutas siguen declaradas para que un integrador viejo reciba un
# 410 Gone con la explicación (y no un 404 que parezca un error de URL).
CHK_GONE_DETAIL = ("Las solicitudes de chequeo se retiraron de la plataforma: compra directo "
                   "(desde el marketplace) o con verificación en campo (POST /verifications). "
                   "Las solicitudes anteriores se conservan solo como histórico interno.")


def _check_requests_gone():
    raise ApiError(410, "check-requests-retired", CHK_GONE_DETAIL, title="Recurso retirado")


@api_route("GET", "/check-requests", scope=("transactions:read", "transactions:write"),
           tags=[TAG_CHK], summary="RETIRADO (410 Gone): solicitudes de chequeo",
           description=CHK_GONE_DETAIL)
def check_requests_list(ctx):
    _check_requests_gone()


@api_route("GET", "/check-requests/{id}", scope=("transactions:read", "transactions:write"),
           tags=[TAG_CHK], summary="RETIRADO (410 Gone): detalle de una solicitud de chequeo",
           description=CHK_GONE_DETAIL)
def check_request_get(ctx, id):
    _check_requests_gone()


@api_route("POST", "/check-requests/{id}:approve", scope=("transactions:write", "products:write"),
           tags=[TAG_CHK], summary="RETIRADO (410 Gone): aprobar una solicitud de chequeo",
           description=CHK_GONE_DETAIL)
def check_request_approve(ctx, id):
    _check_requests_gone()


@api_route("POST", "/check-requests/{id}:reject", scope=("transactions:write", "products:write"),
           tags=[TAG_CHK], summary="RETIRADO (410 Gone): rechazar una solicitud de chequeo",
           description=CHK_GONE_DETAIL)
def check_request_reject(ctx, id):
    _check_requests_gone()
