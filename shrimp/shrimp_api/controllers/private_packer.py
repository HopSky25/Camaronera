"""Empacadora y camaronera: listas de precios, reserva anticipada y avisos de lotes."""

from . import serializers as S
from .framework import ApiError, F, api_route, forbidden, invalid, obj, paginate, role_domain

TAG_PL = "listas de precios"
TAG_HV = "reserva anticipada de cosecha"

# ---------------------------------------------------------------------------
# Listas de precios
# ---------------------------------------------------------------------------
PL_READ = ("pricelists:read", "pricelists:write")
PL_SCHEMA = obj(id="uuid", name="string", my_role="string", issuer="ref", state="string",
                issue_date="date", dispatch_from="date", dispatch_to="date", open_ended="boolean",
                is_current="boolean", is_upcoming="boolean", aguaje="ref", currency="string",
                lines={"type": "array"}, bonuses={"type": "array"}, updated_at="datetime")
PL_LINE = {
    "size_grade": F("ref", required=True, model="shrimp.size.grade", odoo="size_grade_id"),
    "channel": F("enum", enum=["directa", "sobrante"], description="Solo cola (por defecto directa)"),
    "quality": F("enum", enum=["ab", "a", "b", "c"], description="Entero: ab/c. Cola: a/b"),
    "uom": F("enum", enum=["kg", "lb"], description="Por defecto: entero kg, cola lb"),
    "price": F("number", required=True, minimum=0),
}
PL_BONUS = {
    "name": F("string", required=True),
    "amount": F("number", required=True),
    "note": F("string"),
}
PL_SPEC = {
    "name": F("string", required=True, max_length=128),
    "issue_date": F("date"),
    "dispatch_from": F("date"),
    "dispatch_to": F("date"),
    "open_ended": F("boolean"),
    "auto_publish": F("boolean"),
    "auto_publish_date": F("date"),
    "quality_conditions": F("text"),
    "advance_pct": F("number", minimum=0, maximum=100),
    "advance_days": F("integer", minimum=0),
    "balance_days": F("integer", minimum=0),
    "payment_notes": F("string"),
    "recipients": F("refs", model="res.partner", odoo="recipient_ids",
                    domain=role_domain("camaronera") + [("active", "=", True)],
                    description="uuid de camaroneras"),
    "lines": F("array", items=PL_LINE, description="Reemplaza todos los renglones"),
    "bonuses": F("array", items=PL_BONUS, description="Reemplaza todas las bonificaciones"),
    # El aguaje se elige del calendario de la plataforma (GET
    # /public/catalogs/aguajes?upcoming=true): el que está en curso o uno
    # próximo, nunca uno pasado. Obligatorio al crear y al publicar.
    "aguaje": F("ref", model="shrimp.aguaje", odoo="aguaje_id",
                description="uuid del aguaje (en curso o próximo) al que rige la lista. "
                            "Si no indicas despacho, se toma de las fechas del aguaje."),
}
# Al crear, el aguaje es obligatorio. En PUT puede omitirse: la lista conserva
# el que tiene (las listas históricas siguen valiendo con el suyo).
PL_SPEC_CREATE = dict(PL_SPEC, aguaje=F(
    "ref", required=True, model="shrimp.aguaje", odoo="aguaje_id",
    description="uuid del aguaje (en curso o próximo) al que rige la lista. Obligatorio. "
                "Si no indicas despacho, se toma de las fechas del aguaje."))


def _pl_vals(ctx, vals):
    Line = ctx.env["shrimp.price.list.line"]
    if "recipient_ids" in vals:
        vals["recipient_ids"] = [(6, 0, vals["recipient_ids"])]
    if "lines" in vals:
        cmds = [(5, 0, 0)]
        for line in vals.pop("lines"):
            grade = ctx.env["shrimp.size.grade"].sudo().browse(line["size_grade_id"])
            pres = grade.presentation
            permit = Line._CALIDADES_POR_PRESENTACION.get(pres, ())
            line.setdefault("uom", Line._UOM_POR_PRESENTACION.get(pres, "lb"))
            if not line.get("quality") and permit:
                line["quality"] = permit[0]
            if pres == "cola" and not line.get("channel"):
                line["channel"] = "directa"
            cmds.append((0, 0, line))
        vals["line_ids"] = cmds
    if "bonuses" in vals:
        vals["bonus_ids"] = [(5, 0, 0)] + [(0, 0, b) for b in vals.pop("bonuses")]
    # El aguaje ya no se deduce de la fecha de despacho: se elige del
    # calendario. Que no haya pasado lo valida el modelo (422 business-rule
    # con «El aguaje seleccionado ya pasó; elige el aguaje actual o uno
    # próximo.»).
    return vals


@api_route("GET", "/price-lists", scope=PL_READ, tags=[TAG_PL], paginated=True,
           summary="Listas que emito o que me dirigieron",
           params={"role": F("enum", enum=["issuer", "recipient"]),
                   "current": F("boolean", description="Solo las vigentes hoy"),
                   "state": F("enum", enum=["draft", "published", "archived"])},
           response=PL_SCHEMA)
def price_lists(ctx):
    role = ctx.enum_param("role", ["issuer", "recipient"])
    domain = []
    if role == "issuer":
        domain.append(("issuer_partner_id", "=", ctx.partner.id))
    elif role == "recipient":
        domain.append(("issuer_partner_id", "!=", ctx.partner.id))
    if ctx.bool_param("current"):
        domain.append(("is_current", "=", True))
    state = ctx.enum_param("state", ["draft", "published", "archived"])
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.price.list", domain,
                    lambda l: S.price_list(l, ctx.partner, detail=True))


@api_route("GET", "/price-lists/{id}", scope=PL_READ, tags=[TAG_PL],
           summary="Detalle de una lista (matriz de precios y bonificaciones)", response=PL_SCHEMA)
def price_list_get(ctx, id):
    return S.price_list(ctx.get_own("shrimp.price.list", id, "La lista"), ctx.partner, detail=True)


@api_route("POST", "/price-lists", scope="pricelists:write", tags=[TAG_PL], body=PL_SPEC_CREATE,
           status=201,
           summary="Crea una lista en borrador para un aguaje en curso o próximo "
                   "(la emisora es siempre la empacadora de la clave)",
           response=PL_SCHEMA)
def price_list_create(ctx):
    ctx.require_type("empacadora", what="Las listas de precios las publica una empacadora.")
    vals = _pl_vals(ctx, ctx.clean(PL_SPEC_CREATE))
    vals.update({"issuer_partner_id": ctx.partner.id, "state": "draft"})
    lst = ctx.env["shrimp.price.list"].sudo().create(vals)
    return S.price_list(lst, ctx.partner, detail=True)


def _my_list(ctx, id):
    lst = ctx.get_own("shrimp.price.list", id, "La lista").sudo()
    if lst.issuer_partner_id != ctx.partner:
        raise forbidden("Solo la empacadora emisora modifica la lista.")
    return lst


@api_route("PUT", "/price-lists/{id}", scope="pricelists:write", tags=[TAG_PL], body=PL_SPEC,
           summary="Reemplaza la cabecera y, si vienen, los renglones y bonificaciones",
           response=PL_SCHEMA)
def price_list_put(ctx, id):
    lst = _my_list(ctx, id)
    vals = _pl_vals(ctx, ctx.clean(PL_SPEC))
    lst.write(vals)
    return S.price_list(lst, ctx.partner, detail=True)


@api_route("POST", "/price-lists/{id}:publish", scope="pricelists:write", tags=[TAG_PL],
           summary="Publica la lista (exige un aguaje en curso o próximo; archiva la que "
                   "compite con ella y avisa a los destinatarios)",
           response=PL_SCHEMA)
def price_list_publish(ctx, id):
    lst = _my_list(ctx, id)
    lst.action_publish()
    return S.price_list(lst, ctx.partner, detail=True)


@api_route("POST", "/price-lists/{id}:archive", scope="pricelists:write", tags=[TAG_PL],
           summary="Archiva la lista", response=PL_SCHEMA)
def price_list_archive(ctx, id):
    lst = _my_list(ctx, id)
    lst.action_archive_list()
    return S.price_list(lst, ctx.partner, detail=True)


@api_route("GET", "/lot-alerts", scope=PL_READ, tags=[TAG_PL], paginated=True,
           summary="Avisos de lotes publicados que mi lista de precios cotiza (empacadora)",
           response=obj(product="ref", seller="ref", price_list="ref", quoted_price="number",
                        uom="string", qty="number", value="number", sent_at="datetime"))
def lot_alerts(ctx):
    ctx.require_type("empacadora")
    return paginate(ctx, "shrimp.lot.alert", [("packer_partner_id", "=", ctx.partner.id)], S.lot_alert)


# ---------------------------------------------------------------------------
# Reserva anticipada
# ---------------------------------------------------------------------------
HV_READ = ("harvest:read", "harvest:write")
FC_SCHEMA = obj(id="uuid", reference="string", my_role="string", farmer="ref", state="string",
                pond="ref", expected_date="date", expected_lb="number", presentation="string",
                size_grade="ref", lb_min="number", lb_max="number",
                accepts_commitments="boolean", commitments={"type": "array"}, updated_at="datetime")
CM_SCHEMA = obj(id="uuid", forecast="string", my_role="string", farmer="ref", packer="ref",
                state="string", committed_lb="number", price_mode="string", price_per_lb="money",
                price_floor_per_lb="money", valid_until="date", settlement={"type": ["object", "null"]},
                updated_at="datetime")
FC_STATES = ["draft", "published", "committed", "harvested", "cancelled", "expired"]
CM_STATES = ["sent", "accepted", "rejected", "withdrawn", "to_confirm", "honored", "released",
             "broken", "lapsed"]
FC_SPEC = {
    "expected_date": F("date", required=True),
    "expected_lb": F("number", required=True, minimum=0.01),
    "presentation": F("enum", required=True, enum=["entero", "cola"]),
    "size_grade": F("ref", required=True, model="shrimp.size.grade", odoo="size_grade_id"),
    "pond": F("ref", required=True, model="shrimp.partner.pond", own=True, odoo="pond_id"),
    "facility": F("ref", model="shrimp.partner.facility", own=True, odoo="facility_id"),
    "tolerance_lb_pct": F("number", minimum=0, maximum=100),
    "tolerance_size_steps": F("integer", minimum=0),
    "date_tolerance_days": F("integer", minimum=0),
    "notes": F("text"),
    "open_call": F("boolean", description="Dirigirla a todas las empacadoras que aceptan reservas"),
    "recipients": F("refs", model="res.partner", odoo="recipient_ids",
                    domain=role_domain("empacadora") + [("active", "=", True)]),
    "publish": F("boolean", description="Publicar al crear (por defecto true, como el portal)"),
}


@api_route("GET", "/harvest/forecasts", scope=HV_READ, tags=[TAG_HV], paginated=True,
           summary="Cosechas que declaré (camaronera) o que me dirigieron (empacadora)",
           params={"state": F("enum", enum=FC_STATES),
                   "open": F("boolean", description="Solo las que aún admiten compromisos")},
           response=FC_SCHEMA)
def forecasts_list(ctx):
    domain = []
    state = ctx.enum_param("state", FC_STATES)
    if state:
        domain.append(("state", "=", state))
    if ctx.bool_param("open"):
        domain.append(("admite_compromisos", "=", True))
    return paginate(ctx, "shrimp.harvest.forecast", domain,
                    lambda f: S.forecast(f, ctx.partner, detail=True))


@api_route("GET", "/harvest/forecasts/{id}", scope=HV_READ, tags=[TAG_HV],
           summary="Detalle de una cosecha declarada (con los compromisos que me tocan)",
           response=FC_SCHEMA)
def forecast_get(ctx, id):
    return S.forecast(ctx.get_own("shrimp.harvest.forecast", id, "La cosecha declarada"),
                      ctx.partner, detail=True)


@api_route("POST", "/harvest/forecasts", scope="harvest:write", tags=[TAG_HV], body=FC_SPEC,
           status=201, summary="Declara una cosecha futura (camaronera)", response=FC_SCHEMA)
def forecast_create(ctx):
    ctx.require_type("camaronera", what="Una cosecha la declara la camaronera que la va a cosechar.")
    vals = ctx.clean(FC_SPEC)
    publish = vals.pop("publish", True) is not False
    if "recipient_ids" in vals:
        vals["recipient_ids"] = [(6, 0, vals["recipient_ids"])]
    vals = {k: v for k, v in vals.items() if v is not None}
    vals["farmer_partner_id"] = ctx.partner.id
    fc = ctx.env["shrimp.harvest.forecast"].sudo().create(vals)
    if publish:
        fc.action_publish(actor=ctx.partner)
    return S.forecast(fc, ctx.partner, detail=True)


def _my_forecast(ctx, id):
    fc = ctx.get_own("shrimp.harvest.forecast", id, "La cosecha declarada").sudo()
    if fc.farmer_partner_id != ctx.partner:
        raise forbidden("Esta acción es de la camaronera que declaró la cosecha.")
    return fc


@api_route("POST", "/harvest/forecasts/{id}:publish", scope="harvest:write", tags=[TAG_HV],
           summary="Publica una declaración en borrador", response=FC_SCHEMA)
def forecast_publish(ctx, id):
    fc = _my_forecast(ctx, id)
    fc.action_publish(actor=ctx.partner)
    return S.forecast(fc, ctx.partner, detail=True)


@api_route("POST", "/harvest/forecasts/{id}:cancel", scope="harvest:write", tags=[TAG_HV],
           body={"reason": F("text", description="Obligatorio si ya está publicada")},
           summary="Retira la declaración (con compromisos aceptados, cuenta como incumplimiento)",
           response=FC_SCHEMA)
def forecast_cancel(ctx, id):
    fc = _my_forecast(ctx, id)
    vals = ctx.clean({"reason": F("text")})
    fc.action_cancel(motivo=vals.get("reason") or None, actor=ctx.partner)
    return S.forecast(fc, ctx.partner, detail=True)


@api_route("POST", "/harvest/forecasts/{id}:harvest", scope="harvest:write", tags=[TAG_HV],
           body={"actual_lb": F("number", required=True, minimum=0.01),
                 "actual_size_grade": F("ref", required=True, model="shrimp.size.grade"),
                 "actual_date": F("date")},
           summary="Registra la cosecha real: nace el lote y se liquida o se pide confirmación",
           response=FC_SCHEMA)
def forecast_harvest(ctx, id):
    fc = _my_forecast(ctx, id)
    vals = ctx.clean({"actual_lb": F("number", required=True, minimum=0.01),
                      "actual_size_grade": F("ref", required=True, model="shrimp.size.grade"),
                      "actual_date": F("date")})
    fc.action_registrar_cosecha(vals["actual_lb"], vals["actual_size_grade"],
                                actual_date=vals.get("actual_date") or None, actor=ctx.partner)
    return S.forecast(fc, ctx.partner, detail=True)


CM_SPEC = {
    "committed_lb": F("number", required=True, minimum=0.01),
    "price_mode": F("enum", required=True, enum=["fijo", "lista"]),
    "price_per_lb": F("number", minimum=0),
    "step_delta_per_lb": F("number", minimum=0),
    "price_floor_per_lb": F("number", minimum=0),
    "valid_until": F("date", required=True),
    "notes": F("text"),
}


@api_route("POST", "/harvest/forecasts/{id}/commitments", scope="harvest:write", tags=[TAG_HV],
           body=CM_SPEC, status=201,
           summary="La empacadora se compromete (o corrige su compromiso) sobre una cosecha que le dirigieron",
           response=CM_SCHEMA)
def commitment_create(ctx, id):
    ctx.require_type("empacadora")
    fc = ctx.get_own("shrimp.harvest.forecast", id, "La cosecha declarada").sudo()
    if not fc.visible_para(ctx.partner):
        raise forbidden("Esta cosecha no se te dirigió.")
    vals = ctx.clean(CM_SPEC)
    vals = {k: (v if v is not None else False) for k, v in vals.items()}
    mine = fc.commitment_ids.filtered(lambda c: c.packer_partner_id == ctx.partner)[:1]
    if mine:
        if mine.state not in ("sent", "withdrawn"):
            raise ApiError(409, "invalid-state", "Tu compromiso sobre esta cosecha ya no se puede cambiar.")
        mine.write(vals)
    else:
        vals.update({"forecast_id": fc.id, "packer_partner_id": ctx.partner.id})
        mine = ctx.env["shrimp.harvest.commitment"].sudo().create(vals)
    return S.commitment(mine, ctx.partner)


@api_route("GET", "/harvest/commitments", scope=HV_READ, tags=[TAG_HV], paginated=True,
           summary="Compromisos en los que soy parte",
           params={"state": F("enum", enum=CM_STATES)}, response=CM_SCHEMA)
def commitments_list(ctx):
    domain = []
    state = ctx.enum_param("state", CM_STATES)
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.harvest.commitment", domain, lambda c: S.commitment(c, ctx.partner))


@api_route("GET", "/harvest/commitments/{id}", scope=HV_READ, tags=[TAG_HV],
           summary="Detalle de un compromiso", response=CM_SCHEMA)
def commitment_get(ctx, id):
    return S.commitment(ctx.get_own("shrimp.harvest.commitment", id, "El compromiso"), ctx.partner)


def _commitment_action(ctx, id, method, role, **kwargs):
    cm = ctx.get_own("shrimp.harvest.commitment", id, "El compromiso").sudo()
    if role == "farmer" and cm.farmer_partner_id != ctx.partner:
        raise forbidden("Esta acción es de la camaronera.")
    if role == "packer" and cm.packer_partner_id != ctx.partner:
        raise forbidden("Esta acción es de la empacadora comprometida.")
    getattr(cm, method)(actor=ctx.partner, **kwargs)
    return S.commitment(cm, ctx.partner)


@api_route("POST", "/harvest/commitments/{id}:accept", scope="harvest:write", tags=[TAG_HV],
           summary="La camaronera acepta el compromiso (los demás se descartan)", response=CM_SCHEMA)
def commitment_accept(ctx, id):
    return _commitment_action(ctx, id, "action_accept", "farmer")


@api_route("POST", "/harvest/commitments/{id}:reject", scope="harvest:write", tags=[TAG_HV],
           body={"reason": F("text")}, summary="La camaronera descarta el compromiso", response=CM_SCHEMA)
def commitment_reject(ctx, id):
    reason = ctx.clean({"reason": F("text")}).get("reason") or None
    return _commitment_action(ctx, id, "action_reject", "farmer", motivo=reason)


@api_route("POST", "/harvest/commitments/{id}:withdraw", scope="harvest:write", tags=[TAG_HV],
           summary="La empacadora retira un compromiso aún no aceptado", response=CM_SCHEMA)
def commitment_withdraw(ctx, id):
    return _commitment_action(ctx, id, "action_withdraw", "packer")


@api_route("POST", "/harvest/commitments/{id}:desist", scope="harvest:write", tags=[TAG_HV],
           body={"reason": F("text", required=True)},
           summary="Cualquiera de las partes se echa atrás de un compromiso aceptado (queda como incumplido)",
           response=CM_SCHEMA)
def commitment_desist(ctx, id):
    reason = ctx.clean({"reason": F("text", required=True)}).get("reason")
    return _commitment_action(ctx, id, "action_desistir", None, motivo=reason)


BUY_SPEC = {"verifier": F("ref", model="res.partner",
                          domain=role_domain("verificador"),
                          description="Verificador acreditado (obligatorio en modo platform)"),
            "verification_mode": F("enum", enum=["platform", "declared"],
                                   description="platform: verificadora de la plataforma (con "
                                               "verifier); declared: verificación declarada por "
                                               "las partes. Por defecto platform si hay verifier.")}


@api_route("POST", "/harvest/commitments/{id}:buy", scope="harvest:write", tags=[TAG_HV],
           body=BUY_SPEC,
           summary="La empacadora compra el lote nacido de un compromiso cumplido",
           description="El camarón adulto se compra con verificación en campo: la compra "
                       "queda pendiente de verificación (y de la firma de las partes) y el "
                       "honorario se factura al iniciarla. Hay que indicar el verificador.",
           response=CM_SCHEMA)
def commitment_buy(ctx, id):
    vals = ctx.clean(BUY_SPEC)
    verifier = ctx.env["res.partner"].sudo().browse(vals["verifier"]) if vals.get("verifier") else None
    return _commitment_action(ctx, id, "action_comprar", "packer", verifier=verifier,
                              mode=vals.get("verification_mode") or None)


@api_route("POST", "/harvest/commitments/{id}/confirmation", scope="harvest:write", tags=[TAG_HV],
           body={"decision": F("enum", required=True, enum=["accepted", "rejected"]),
                 "reason": F("text", description="Obligatorio si no se acepta")},
           summary="Firma de la cosecha que salió fuera de la banda (cada parte la suya)",
           response=CM_SCHEMA)
def commitment_confirmation(ctx, id):
    cm = ctx.get_own("shrimp.harvest.commitment", id, "El compromiso").sudo()
    vals = ctx.clean({"decision": F("enum", required=True, enum=["accepted", "rejected"]),
                      "reason": F("text")})
    mine = cm.confirmation_ids.filtered(lambda c: c.active and c.partner_id == ctx.partner)[:1]
    if not mine:
        raise ApiError(409, "invalid-state", "No hay ninguna confirmación pendiente a tu nombre.")
    if vals["decision"] == "accepted":
        mine.action_accept(actor=ctx.partner)
    else:
        mine.action_reject(motivo=vals.get("reason") or None, actor=ctx.partner)
    return S.commitment(cm, ctx.partner)


@api_route("POST", "/harvest/commitments/{id}/confirmation:revert", scope="harvest:write",
           tags=[TAG_HV], body={"reason": F("text", description="Por qué se deshace (recomendado)")},
           summary="Cada parte deshace SU firma de la cosecha fuera de banda",
           description="Con el compromiso en «to_confirm», siempre. Un rechazo que ya liberó el "
                       "compromiso se puede deshacer solo dentro de la ventana de gracia "
                       "(shrimp.signoff_undo_minutes, 15 min) y si el lote sigue en borrador y "
                       "sin compra. Emite harvest.confirmation_reverted.",
           response=CM_SCHEMA)
def commitment_confirmation_revert(ctx, id):
    cm = ctx.get_own("shrimp.harvest.commitment", id, "El compromiso").sudo()
    vals = ctx.clean({"reason": F("text")})
    mine = cm.confirmation_ids.filtered(lambda c: c.active and c.partner_id == ctx.partner)[:1]
    if not mine:
        raise ApiError(409, "invalid-state", "No hay ninguna confirmación a tu nombre.")
    mine.action_signoff_undo(reason=vals.get("reason") or None, actor=ctx.partner)
    return S.commitment(cm, ctx.partner)
