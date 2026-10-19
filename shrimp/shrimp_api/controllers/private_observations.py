"""«Estado real del lote» desde la API (shrimp_marketplace,
models/shrimp_product_observation.py). Rutas nuevas; las existentes no cambian
(POST /products/{id}/evolutions sigue cambiando la publicación).

  GET  /products/{id}/observations   observaciones y muestreos de un lote propio
  POST /products/{id}/observations   registra una observación: NO cambia la
                                     publicación ni el stock (la cantidad
                                     estimada es informativa)

Solo el dueño del lote con permiso «Vender» en el perfil del lote. Las fichas
públicas de producto traen «real_status» (la última observación).
"""
from . import serializers as S
from .framework import F, api_route, forbidden, obj
from .private_core import TAG_PROD

OBS_SPEC = {
    "date": F("datetime", description="Cuándo se observó (por defecto, ahora). No puede ser futura."),
    "stage": F("ref", required=True, model="shrimp.stage", odoo="stage_id",
               description="Etapa observada (del tipo de lote del perfil)"),
    "avg_size_mg": F("number", minimum=0, description="Talla / peso promedio en mg"),
    "survival_rate": F("number", minimum=0, maximum=100),
    "health_status": F("text"),
    "note": F("text"),
    "water_temp_c": F("number", minimum=0, maximum=40),
    "salinity_ppt": F("number", minimum=0, maximum=60),
    "dissolved_oxygen_mg_l": F("number", minimum=0, maximum=20),
    "ph": F("number", minimum=0, maximum=14),
    "estimated_qty": F("number", minimum=0,
                       description="Cantidad estimada en el lote (informativa, no cambia el stock)"),
}
OBS_SCHEMA = obj(id="uuid", date="datetime", origin="string", stage="ref", published_stage="ref",
                 avg_size_mg="number", survival_rate="number", health_status="string",
                 note="string", water={"type": "object"}, diverges_from_publication="boolean",
                 divergence={"type": ["object", "null"]}, estimated_qty="number",
                 editable="boolean", editable_until="datetime")


def _own_product(ctx, id, archived=False):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=archived)
    if not product.sudo()._shrimp_user_can_observe(ctx.user):
        raise forbidden("Solo el vendedor del lote (con permiso «Vender») registra observaciones.")
    return product


@api_route("GET", "/products/{id}/observations", scope=("products:read", "products:write"),
           tags=[TAG_PROD], summary="Estado real del lote: observaciones y muestreos de un producto propio",
           response={"type": "object", "properties": {"data": {"type": "array", "items": OBS_SCHEMA}}})
def observations_list(ctx, id):
    product = ctx.get_own("shrimp.product", id, "El producto", archived=True)
    obs = product.sudo().shrimp_observations()
    return {"data": [S.observation(e, owner=True) for e in obs],
            "meta": {"count": len(obs), "next_cursor": None}}


@api_route("POST", "/products/{id}/observations", scope="products:write", tags=[TAG_PROD],
           body=OBS_SPEC, status=201,
           summary="Registra una observación del lote (no cambia la publicación)",
           description="Si la etapa (o la talla, más allá del umbral de Ajustes) difiere de lo publicado, "
                       "se avisa al vendedor y a los compradores con compras o reservas en curso. "
                       "Actualizar la publicación es siempre explícito (PATCH del producto).",
           response=OBS_SCHEMA)
def observation_create(ctx, id):
    product = _own_product(ctx, id)
    vals = ctx.clean(OBS_SPEC)
    data = {
        "date": vals.get("date") or None,
        "stage": ctx.env["shrimp.stage"].sudo().browse(vals["stage_id"]),
        "avg_size_mg": vals.get("avg_size_mg") or 0.0,
        "survival_rate": vals.get("survival_rate") or 0.0,
        "health_status": vals.get("health_status") or "",
        "note": vals.get("note") or "",
        "water_temp_c": vals.get("water_temp_c") or 0.0,
        "salinity_ppt": vals.get("salinity_ppt") or 0.0,
        "dissolved_oxygen_mgl": vals.get("dissolved_oxygen_mg_l") or 0.0,
        "water_ph": vals.get("ph") or 0.0,
        "estimated_qty": vals.get("estimated_qty") or 0.0,
    }
    obs = product.with_env(ctx.env).shrimp_register_observation(data)
    return S.observation(obs, owner=True)
