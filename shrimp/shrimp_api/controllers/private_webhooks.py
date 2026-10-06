"""Gestión de suscripciones a webhooks (scope webhooks:manage)."""

from ..models.webhook import EVENTS
from . import serializers as S
from .framework import ApiResponse, F, api_route, clean, invalid, obj, paginate

TAG = "webhooks"
MAX_SUBSCRIPTIONS = 10
SUB_SCHEMA = obj(id="uuid", name="string", url="string", events={"type": "array", "items": {"type": "string"}},
                 active="boolean", failure_count="integer", last_success_at="datetime",
                 last_error="string")
SUB_SPEC = {
    "name": F("string", required=True, max_length=128),
    "url": F("string", required=True, max_length=2048, description="https://…"),
    "events": F("array", required=True, items={},
                description="Lista de tipos de evento, o [\"*\"] para todos los que te correspondan"),
    "active": F("boolean"),
}


def _events(events):
    if not isinstance(events, list) or not events or not all(isinstance(e, str) for e in events):
        raise invalid("«events» es una lista no vacía de códigos de evento (o [\"*\"]).")
    unknown = [e for e in events if e != "*" and (e not in EVENTS or e == "ping")]
    if unknown:
        raise invalid("Eventos desconocidos: %s" % ", ".join(unknown))
    return " ".join(sorted(set(events)))


def _clean(ctx, partial):
    body = dict(ctx.body)
    has_events = "events" in body
    events = body.pop("events", None)
    vals = clean(ctx, body, {k: v for k, v in SUB_SPEC.items() if k != "events"}, partial=partial)
    if has_events or not partial:
        vals["event_types"] = _events(events)
    return vals


@api_route("GET", "/webhooks/events", scope="webhooks:manage", tags=[TAG],
           summary="Tipos de evento disponibles",
           response=obj(data={"type": "array", "items": obj(type="string", label="string",
                                                            resource_type="string")}))
def webhook_events(ctx):
    installed = ctx.env.registry._init_modules
    data = [{"type": code, "label": spec.label, "resource_type": spec.resource_type}
            for code, spec in sorted(EVENTS.items())
            if code != "ping" and (code != "invoice.authorized" or "shrimp_api_sri" in installed)]
    return {"data": data, "meta": {"count": len(data), "next_cursor": None}}


@api_route("GET", "/webhooks", scope="webhooks:manage", tags=[TAG], paginated=True,
           summary="Mis suscripciones", response=SUB_SCHEMA)
def webhooks_list(ctx):
    return paginate(ctx, "shrimp.webhook.subscription",
                    ["|", ("active", "=", True), ("active", "=", False)], S.webhook_subscription)


@api_route("POST", "/webhooks", scope="webhooks:manage", tags=[TAG], body=SUB_SPEC, status=201,
           summary="Crea una suscripción. La respuesta trae el secreto de firma UNA sola vez",
           response=obj(id="uuid", name="string", url="string", secret="string"))
def webhook_create(ctx):
    Sub = ctx.env["shrimp.webhook.subscription"].sudo()
    if Sub.with_context(active_test=False).search_count([("partner_id", "=", ctx.partner.id)]) >= MAX_SUBSCRIPTIONS:
        raise invalid("Máximo %s suscripciones por socio." % MAX_SUBSCRIPTIONS)
    vals = _clean(ctx, partial=False)
    secret = Sub._new_secret()
    vals.update({"partner_id": ctx.partner.id, "user_id": ctx.user.id, "secret": secret})
    sub = Sub.create(vals)
    out = S.webhook_subscription(sub)
    out["secret"] = secret
    return ApiResponse(out, status=201)


def _mine(ctx, id):
    return ctx.get_own("shrimp.webhook.subscription", id, "La suscripción", archived=True).sudo()


@api_route("GET", "/webhooks/{id}", scope="webhooks:manage", tags=[TAG],
           summary="Detalle de una suscripción", response=SUB_SCHEMA)
def webhook_get(ctx, id):
    return S.webhook_subscription(_mine(ctx, id))


@api_route("PATCH", "/webhooks/{id}", scope="webhooks:manage", tags=[TAG], body=SUB_SPEC,
           summary="Cambia nombre, URL, eventos o la activa/desactiva", response=SUB_SCHEMA)
def webhook_patch(ctx, id):
    sub = _mine(ctx, id)
    vals = _clean(ctx, partial=True)
    if vals.get("active"):
        vals.update(failure_count=0, disabled_reason=False)
    sub.write(vals)
    return S.webhook_subscription(sub)


@api_route("DELETE", "/webhooks/{id}", scope="webhooks:manage", tags=[TAG], status=204,
           summary="Borra la suscripción y su historial de entregas")
def webhook_delete(ctx, id):
    _mine(ctx, id).unlink()
    return ApiResponse(None, status=204)


@api_route("POST", "/webhooks/{id}:ping", scope="webhooks:manage", tags=[TAG], status=202,
           summary="Encola un evento «ping» de prueba", response=obj(delivery="uuid", state="string"))
def webhook_ping(ctx, id):
    sub = _mine(ctx, id)
    delivery = sub.action_ping()
    return ApiResponse({"delivery": delivery.uuid_ref, "state": delivery.state}, status=202)


@api_route("POST", "/webhooks/{id}:rotate-secret", scope="webhooks:manage", tags=[TAG],
           summary="Genera un secreto de firma nuevo (el anterior deja de valer)",
           response=obj(secret="string"))
def webhook_rotate(ctx, id):
    return {"secret": _mine(ctx, id).action_rotate_secret()}


@api_route("GET", "/webhooks/{id}/deliveries", scope="webhooks:manage", tags=[TAG], paginated=True,
           summary="Historial de entregas de una suscripción",
           params={"state": F("enum", enum=["pending", "retrying", "success", "dead"])},
           response=obj(id="uuid", event_type="string", state="string", attempts="integer",
                        last_status_code="integer", delivered_at="datetime"))
def webhook_deliveries(ctx, id):
    sub = _mine(ctx, id)
    domain = [("subscription_id", "=", sub.id)]
    state = ctx.enum_param("state", ["pending", "retrying", "success", "dead"])
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.webhook.delivery", domain, S.webhook_delivery)
