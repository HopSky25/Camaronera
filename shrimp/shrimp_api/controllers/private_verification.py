"""Verificación en campo, aceptación del informe, despacho y ranking de proveedores."""

import base64
import binascii

from odoo.tools.mimetypes import guess_mimetype

from . import serializers as S
from .framework import (ApiError, F, api_route, forbidden, invalid, iso_date, obj, paginate)

TAG_VER = "verificaciones"
TAG_DSP = "despacho"
READ = ("verifications:read", "verifications:write")

VER_SCHEMA = obj(id="uuid", reference="string", my_role="string", state="string", scope="string",
                 verification_mode="string", verification_label="string",
                 transaction="string", product="ref", buyer="ref", seller="ref", verifier="ref",
                 fee="money", acceptance_state="string", verified_at="datetime",
                 line_count="integer", count_count="integer", updated_at="datetime")
VER_STATES = ["received", "assigned", "in_field", "done", "approved", "approved_obs", "rejected", "cancelled",
              "declared_draft", "declared"]


def _role_domain(ctx, role):
    p = ctx.partner.id
    if role == "buyer":
        return [("buyer_partner_id", "=", p)]
    if role == "seller":
        return [("seller_partner_id", "=", p)]
    if role == "verifier":
        company = ctx.partner.shrimp_verifier_company()
        return [("verifier_partner_id", "=", company.id or 0)]
    if role == "technician":
        return [("technician_partner_id", "=", p)]
    return []


@api_route("GET", "/verifications", scope=READ, tags=[TAG_VER], paginated=True,
           summary="Verificaciones donde participo (comprador, vendedor, verificadora o técnico)",
           params={"role": F("enum", enum=["buyer", "seller", "verifier", "technician"]),
                   "state": F("enum", enum=VER_STATES),
                   "verification_mode": F("enum", enum=["platform", "declared"])},
           response=VER_SCHEMA)
def verifications_list(ctx):
    domain = _role_domain(ctx, ctx.enum_param("role", ["buyer", "seller", "verifier", "technician"]))
    state = ctx.enum_param("state", VER_STATES)
    if state:
        domain.append(("state", "=", state))
    mode = ctx.enum_param("verification_mode", ["platform", "declared"])
    if mode:
        domain.append(("verification_mode", "=", mode))
    return paginate(ctx, "shrimp.verification", domain, lambda v: S.verification(v, ctx.partner))


def _verification(ctx, id):
    return ctx.get_own("shrimp.verification", id, "La verificación")


@api_route("GET", "/verifications/{id}", scope=READ, tags=[TAG_VER],
           summary="Informe completo: pesos, clasificación (líneas), conteos, sabor, posturas",
           response=VER_SCHEMA)
def verification_get(ctx, id):
    return S.verification(_verification(ctx, id), ctx.partner, detail=True)


ACCEPT_SPEC = {
    "decision": F("enum", required=True, enum=["accept", "reject", "counter"]),
    "reason": F("text", description="Obligatorio al rechazar"),
    "counter_price": F("number", minimum=0, description="Precio unitario propuesto (solo comprador, "
                                                        "solo si el informe no cumple lo publicado)"),
}


@api_route("POST", "/verifications/{id}/acceptance", scope="verifications:write", tags=[TAG_VER],
           body=ACCEPT_SPEC, summary="Comprador o vendedor aceptan, rechazan o contraofertan el informe",
           response=VER_SCHEMA)
def verification_acceptance(ctx, id):
    v = _verification(ctx, id).sudo()
    role = S.verification_role(v, ctx.partner)
    if role not in ("buyer", "seller"):
        raise forbidden("Solo el comprador y el vendedor deciden sobre el informe.")
    vals = ctx.clean(ACCEPT_SPEC)
    postura = v.acceptance_ids.filtered(lambda a: a.role == role)[:1]
    if not postura:
        raise ApiError(409, "invalid-state", "Esta verificación no está esperando la aceptación de las partes.")
    reason = vals.get("reason") or None
    if vals["decision"] == "accept":
        postura.action_accept(reason=reason)
    elif vals["decision"] == "reject":
        postura.action_reject(reason=reason)
    else:
        if not vals.get("counter_price"):
            raise invalid("Falta counter_price para la contraoferta.")
        postura.action_counter(vals["counter_price"], reason=reason)
    return S.verification(v, ctx.partner, detail=True)


REVERT_SPEC = {"reason": F("text", description="Por qué se deshace (recomendado)")}


@api_route("POST", "/verifications/{id}/acceptance:revert", scope="verifications:write",
           tags=[TAG_VER], body=REVERT_SPEC,
           summary="Comprador o vendedor deshacen SU decisión sobre el informe",
           description="Devuelve la postura propia a como estaba antes de la última decisión "
                       "(normalmente «pending»; deshacer una contraoferta restaura la propuesta "
                       "anterior). Solo mientras la ronda siga abierta (acceptance_state "
                       "«waiting»): con las dos partes decididas o vencido el plazo la decisión ya "
                       "surtió efecto. Emite el webhook verification.acceptance_reverted.",
           response=VER_SCHEMA)
def verification_acceptance_revert(ctx, id):
    v = _verification(ctx, id).sudo()
    role = S.verification_role(v, ctx.partner)
    if role not in ("buyer", "seller"):
        raise forbidden("Solo el comprador y el vendedor deshacen su propia decisión.")
    vals = ctx.clean(REVERT_SPEC)
    postura = v.acceptance_ids.filtered(lambda a: a.role == role)[:1]
    if not postura:
        raise ApiError(409, "invalid-state", "Esta verificación no está esperando la aceptación de las partes.")
    postura.action_signoff_undo(reason=vals.get("reason") or None, actor=ctx.partner)
    return S.verification(v, ctx.partner, detail=True)


# --- lado de la verificadora ------------------------------------------------
def _as_verifier(ctx, id, editable=False):
    v = _verification(ctx, id).sudo()
    company = ctx.partner.shrimp_verifier_company()
    is_admin = bool(company) and ctx.partner.shrimp_is_verifier_admin() and v.verifier_partner_id == company
    is_tech = v.technician_partner_id == ctx.partner
    if not (is_admin or is_tech):
        raise forbidden("Esta operación es de la empresa verificadora o del técnico asignado.")
    if editable and v.is_final:
        raise ApiError(409, "invalid-state", "La verificación ya está cerrada.")
    return v, is_admin


@api_route("POST", "/verifications/{id}:assign", scope="verifications:write", tags=[TAG_VER],
           body={"technician": F("string", required=True, description="uuid del técnico (contacto de la empresa)")},
           summary="El administrador de la verificadora asigna el técnico de campo", response=VER_SCHEMA)
def verification_assign(ctx, id):
    v, is_admin = _as_verifier(ctx, id, editable=True)
    if not is_admin:
        raise forbidden("El técnico lo asigna el administrador de la empresa verificadora.")
    tech_ref = ctx.body.get("technician")
    if not isinstance(tech_ref, str):
        raise invalid("Falta technician (uuid).")
    company = ctx.partner.shrimp_verifier_company()
    techs = company.field_tech_ids.filtered("active") | company
    tech = techs.filtered(lambda t: t.uuid_ref == tech_ref)[:1]
    if not tech:
        raise invalid("Ese técnico no pertenece a tu empresa.")
    v.action_assign_technician(tech)
    return S.verification(v, ctx.partner, detail=True)


@api_route("POST", "/verifications/{id}:start", scope="verifications:write", tags=[TAG_VER],
           summary="Inicia el trabajo de campo (técnico asignado o administrador)", response=VER_SCHEMA)
def verification_start(ctx, id):
    v, _admin = _as_verifier(ctx, id, editable=True)
    v.action_start_field()
    return S.verification(v, ctx.partner, detail=True)


REPORT_SPEC = {
    "plant_name": F("string"),
    "harvest_date": F("date"),
    "process_date": F("date"),
    "weight_sent_lb": F("number", minimum=0),
    "weight_plant_lb": F("number", minimum=0),
    "trash_lb": F("number", minimum=0),
    "presentation": F("enum", enum=["entero", "cola"]),
    "metabisulfite_ppm": F("number", minimum=0),
    "metabisulfite_limit_ppm": F("number", minimum=0),
    "metabisulfite_notes": F("text"),
    "taste_result": F("enum", enum=["excellent", "good", "acceptable", "rejected"]),
    "taste_notes": F("text"),
    "taste_criteria_ok": F("refs", model="shrimp.taste.criterion", odoo="taste_criteria_ok_ids"),
    "grams_farm": F("number", minimum=0),
    "grams_plant_1": F("number", minimum=0),
    "grams_plant_2": F("number", minimum=0),
    "larvae_qty_verified": F("number", minimum=0),
    "larvae_survival_rate": F("number", minimum=0, maximum=100),
    "larvae_avg_size_mg": F("number", minimum=0),
    "larvae_health_status": F("enum", enum=["excellent", "good", "acceptable", "rejected"]),
    "larvae_health_notes": F("text"),
    "incident_notes": F("text"),
    "gps_latitude": F("number", minimum=-90, maximum=90, description="Solo se guarda; la API no lo devuelve"),
    "gps_longitude": F("number", minimum=-180, maximum=180),
    "lines": F("array", items={
        "quality_class": F("enum", required=True, enum=["a", "b", "c"]),
        "size_code": F("string", required=True, max_length=16),
        "weight_lb": F("number", required=True, minimum=0.01),
    }, description="Reemplaza la clasificación. Una fila por clase, como en el formulario."),
    "counts": F("array", items={"value": F("number", required=True, minimum=0.01),
                                "note": F("string")},
                description="Reemplaza los conteos (camarones por libra)."),
}


@api_route("PATCH", "/verifications/{id}/report", scope="verifications:write", tags=[TAG_VER],
           body=REPORT_SPEC, summary="Carga (parcial) del informe de campo desde la app del técnico",
           response=VER_SCHEMA)
def verification_report(ctx, id):
    v, _admin = _as_verifier(ctx, id, editable=True)
    vals = ctx.clean(REPORT_SPEC, partial=True)
    lines = vals.pop("lines", None)
    counts = vals.pop("counts", None)
    if "taste_criteria_ok_ids" in vals:
        vals["taste_criteria_ok_ids"] = [(6, 0, vals["taste_criteria_ok_ids"])]
    if vals:
        v.write(vals)
    if lines is not None:
        classes = [l["quality_class"] for l in lines]
        if len(classes) != len(set(classes)):
            raise invalid("Solo puede haber una fila por clase (A, B, C).")
        v.line_ids.unlink()
        Line = ctx.env["shrimp.verification.line"].sudo()
        for seq, line in enumerate(lines, start=1):
            Line.create(dict(line, verification_id=v.id, sequence=seq * 10))
    if counts is not None:
        v.count_ids.unlink()
        Count = ctx.env["shrimp.verification.count"].sudo()
        for seq, count in enumerate(counts, start=1):
            Count.create({"verification_id": v.id, "value": count["value"],
                          "note": count.get("note") or False, "sequence": seq * 10})
    return S.verification(v, ctx.partner, detail=True)


@api_route("POST", "/verifications/{id}:verdict", scope="verifications:write", tags=[TAG_VER],
           body={"verdict": F("enum", required=True, enum=["approve", "approve_obs", "reject"]),
                 "notes": F("text", description="Obligatorio con observaciones o rechazo")},
           summary="Emite el veredicto (técnico que fue a campo o administrador)", response=VER_SCHEMA)
def verification_verdict(ctx, id):
    v, _admin = _as_verifier(ctx, id, editable=True)
    if not v._puede_dictaminar(ctx.partner):
        raise forbidden("El veredicto lo firma el técnico que fue a campo o el administrador.")
    body = ctx.clean({"verdict": F("enum", required=True, enum=["approve", "approve_obs", "reject"]),
                      "notes": F("text")})
    state = {"approve": "approved", "approve_obs": "approved_obs", "reject": "rejected"}[body["verdict"]]
    if v.state == "in_field":
        v.action_mark_done()
    v._close(state, notes=body.get("notes") or None)
    if state == "rejected":
        v.transaction_id.action_cancel_for_verification()
    return S.verification(v, ctx.partner, detail=True)


# ---------------------------------------------------------------------------
# Compra verificada (con modo) y verificación DECLARADA por las partes
# ---------------------------------------------------------------------------
MODE_DOC = ("«platform»: verificadora acreditada de la plataforma (verifier obligatorio, con "
            "honorario). «declared»: verificación declarada por las partes (sin verificadora "
            "de la plataforma ni honorario): el comprador o el vendedor cargan el informe y lo "
            "presentan; la otra parte lo acepta/rechaza/contraoferta. No existe «no aplica».")
DECLARED_SPEC = {
    "declared_source": F("enum", enum=["external", "self"],
                         description="external: verificadora externa; self: verificación propia"),
    "external_verifier_name": F("string", max_length=200),
    "external_verifier_vat": F("string", max_length=20, description="RUC de la verificadora externa"),
}
BUY_VERIFIED_SPEC = dict({
    "product": F("ref", required=True, model="shrimp.product",
                 domain=[("state", "=", "published"), ("active", "=", True)],
                 description="Lote publicado"),
    "qty": F("number", required=True, minimum=0.000001),
    "verification_mode": F("enum", required=True, enum=["platform", "declared"], description=MODE_DOC),
    "verifier": F("ref", model="res.partner",
                  description="Verificador acreditado (solo y obligatorio en modo platform)"),
}, **DECLARED_SPEC)


@api_route("POST", "/verifications", scope="verifications:write", tags=[TAG_VER],
           body=BUY_VERIFIED_SPEC, status=201,
           summary="Compra un lote con verificación, eligiendo el modo (plataforma o declarada)",
           description="Graba la compra pendiente de verificación (reserva la cantidad, no consume "
                       "lotes). " + MODE_DOC + " Compra con el perfil de la petición.",
           response=VER_SCHEMA)
def verification_buy(ctx):
    vals = ctx.clean(BUY_VERIFIED_SPEC)
    product = ctx.env["shrimp.product"].sudo().browse(vals["product"]).with_context(
        shrimp_buyer_role=ctx.role)
    mode = vals["verification_mode"]
    verifier, fee, declared = None, 0.0, None
    if mode == "platform":
        if not vals.get("verifier"):
            raise invalid("En modo «platform» hay que indicar verifier.")
        verifier = ctx.env["res.partner"].sudo().browse(vals["verifier"])
        fee = ctx.env["shrimp.verification.fee"].sudo().compute(vals["qty"])
    else:
        if vals.get("verifier"):
            raise invalid("En modo «declared» no se elige verificadora de la plataforma.")
        declared = {k: vals.get(k) for k in DECLARED_SPEC if vals.get(k)}
    result = product.start_verified_purchase(
        ctx.partner, vals["qty"], verifier, fee=fee, mode=mode, declared_vals=declared)
    return S.verification(result["verification"], ctx.partner, detail=True)


DECLARED_REPORT_SPEC = dict(REPORT_SPEC, **DECLARED_SPEC)
DECLARED_REPORT_SPEC.update({
    "report_pdf": F("text", max_length=7_500_000,
                    description="Informe de la verificadora externa: PDF en base64 (máx. 5 MB)"),
    "report_pdf_filename": F("string", max_length=200),
    "notes": F("text", description="Observaciones / conclusión de quien verificó", odoo="verdict_notes"),
})


def _as_party_declared(ctx, id):
    v = _verification(ctx, id).sudo()
    if v.verification_mode != "declared":
        raise ApiError(409, "invalid-state", "Esta verificación la hace una verificadora de la plataforma.")
    if S.verification_role(v, ctx.partner) not in ("buyer", "seller"):
        raise forbidden("El informe declarado lo cargan el comprador o el vendedor.")
    return v


@api_route("PATCH", "/verifications/{id}/declared-report", scope="verifications:write",
           tags=[TAG_VER], body=DECLARED_REPORT_SPEC,
           summary="Comprador o vendedor cargan (por partes) el informe declarado",
           description="Mismos campos que el informe del técnico (pesos en planta y basura, "
                       "presentación, clases/tallas, metabisulfito, sabor, gramajes, conteos...) "
                       "más los datos opcionales de la verificadora externa y su informe en PDF. "
                       "Solo mientras el informe está en preparación (state «declared_draft»).",
           response=VER_SCHEMA)
def verification_declared_report(ctx, id):
    v = _as_party_declared(ctx, id)
    vals = ctx.clean(DECLARED_REPORT_SPEC, partial=True)
    lines = vals.pop("lines", None)
    counts = vals.pop("counts", None)
    pdf = vals.pop("report_pdf", None)
    pdf_name = vals.pop("report_pdf_filename", None)
    if "taste_criteria_ok_ids" in vals:
        vals["taste_criteria_ok_ids"] = [(6, 0, vals["taste_criteria_ok_ids"])]
    if pdf:
        try:
            raw = base64.b64decode(pdf, validate=True)
        except (binascii.Error, ValueError):
            raise invalid("report_pdf no es base64 válido.")
        if len(raw) > 5 * 1024 * 1024 or guess_mimetype(raw) != "application/pdf":
            raise invalid("report_pdf tiene que ser un PDF de 5 MB como máximo.")
        vals["declared_report_file"] = base64.b64encode(raw)
        vals["declared_report_filename"] = pdf_name or "informe_verificacion.pdf"
    v.action_declared_save(vals, ctx.partner)
    if lines is not None:
        classes = [l["quality_class"] for l in lines]
        if len(classes) != len(set(classes)):
            raise invalid("Solo puede haber una fila por clase (A, B, C).")
        v.line_ids.unlink()
        Line = ctx.env["shrimp.verification.line"].sudo()
        for seq, line in enumerate(lines, start=1):
            Line.create(dict(line, verification_id=v.id, sequence=seq * 10))
    if counts is not None:
        v.count_ids.unlink()
        Count = ctx.env["shrimp.verification.count"].sudo()
        for seq, count in enumerate(counts, start=1):
            Count.create({"verification_id": v.id, "value": count["value"],
                          "note": count.get("note") or False, "sequence": seq * 10})
    return S.verification(v, ctx.partner, detail=True)


@api_route("POST", "/verifications/{id}:declare", scope="verifications:write", tags=[TAG_VER],
           body={"notes": F("text", description="Observaciones de quien verificó (opcional)")},
           summary="Comprador o vendedor PRESENTAN el informe declarado a la otra parte",
           description="Quien lo presenta queda como declarante (su postura queda aceptada) y la "
                       "otra parte responde con POST /verifications/{id}/acceptance. Mientras la otra "
                       "parte no responda, el declarante puede retirarlo con "
                       "POST /verifications/{id}/acceptance:revert (vuelve a «declared_draft»). "
                       "Emite el webhook verification.declared_submitted.",
           response=VER_SCHEMA)
def verification_declare(ctx, id):
    v = _as_party_declared(ctx, id)
    body = ctx.clean({"notes": F("text")})
    v.action_declared_submit(ctx.partner, notes=body.get("notes") or None)
    return S.verification(v, ctx.partner, detail=True)


# ---------------------------------------------------------------------------
# Despacho
# ---------------------------------------------------------------------------
DSP_READ = ("dispatch:write", "transactions:read", "verifications:read")
DSP_SCHEMA = obj(id="uuid", my_role="string", state="string", transaction="string", product="ref",
                 harvest_date="date", farm_departure="datetime", eta="datetime",
                 actual_arrival="datetime", carrier_name="string", vehicle_plate="string",
                 delay_minutes="number", on_time="boolean", updated_at="datetime")


@api_route("GET", "/dispatches", scope=DSP_READ, tags=[TAG_DSP], paginated=True,
           summary="Despachos donde participo (vendedor, empacadora, verificadora, técnico)",
           params={"state": F("enum", enum=["sin_cita", "programado", "en_ruta", "llegado"])},
           response=DSP_SCHEMA)
def dispatches_list(ctx):
    domain = []
    state = ctx.enum_param("state", ["sin_cita", "programado", "en_ruta", "llegado"])
    if state:
        domain.append(("state", "=", state))
    return paginate(ctx, "shrimp.dispatch", domain, lambda d: S.dispatch(d, ctx.partner))


@api_route("GET", "/dispatches/{id}", scope=DSP_READ, tags=[TAG_DSP],
           summary="Detalle del despacho", response=DSP_SCHEMA)
def dispatch_get(ctx, id):
    return S.dispatch(ctx.get_own("shrimp.dispatch", id, "El despacho"), ctx.partner)


PLAN_SPEC = {
    "harvest_date": F("date"),
    "farm_departure": F("datetime"),
    "eta": F("datetime", description="La cita en planta (UTC). Cambiarla avisa a todos."),
    "carrier_name": F("string"),
    "vehicle_plate": F("string", max_length=16),
    "carrier_phone": F("string", max_length=32),
    "notes": F("text"),
}


@api_route("PATCH", "/dispatches/{id}", scope="dispatch:write", tags=[TAG_DSP], body=PLAN_SPEC,
           summary="El vendedor fija o mueve la cita (eta), la salida y el transporte",
           response=DSP_SCHEMA)
def dispatch_patch(ctx, id):
    d = ctx.get_own("shrimp.dispatch", id, "El despacho").sudo()
    if not d._es_vendedor(ctx.partner):
        raise forbidden("El despacho lo registra el vendedor.")
    vals = ctx.clean(PLAN_SPEC, partial=True)
    if not vals:
        raise invalid("No hay nada que actualizar.")
    if vals.get("vehicle_plate"):
        vals["vehicle_plate"] = vals["vehicle_plate"].upper()
    d.registrar_plan(ctx.partner, vals)
    return S.dispatch(d, ctx.partner)


@api_route("POST", "/dispatches/{id}/arrival", scope="dispatch:write", tags=[TAG_DSP],
           body={"actual_arrival": F("datetime", description="Por defecto, ahora")},
           summary="El técnico verificador (o su empresa) estampa la llegada real a planta",
           response=DSP_SCHEMA)
def dispatch_arrival(ctx, id):
    d = ctx.get_own("shrimp.dispatch", id, "El despacho").sudo()
    if not d._puede_estampar_llegada(ctx.partner):
        raise forbidden("La llegada real la registra el técnico verificador (ni el vendedor ni la empacadora).")
    vals = ctx.clean({"actual_arrival": F("datetime")})
    d.registrar_llegada(ctx.partner, vals.get("actual_arrival") or None)
    return S.dispatch(d, ctx.partner)


# ---------------------------------------------------------------------------
# Ranking de proveedores (empacadora)
# ---------------------------------------------------------------------------
RANK_FIELDS = ("lotes", "suficiente", "rendimiento", "rendimiento_n", "rendimiento_min",
               "rendimiento_max", "clase_a", "clase_a_n", "metabisulfito", "metabisulfito_n",
               "metabisulfito_fail", "sabor_rechazos", "merma", "merma_n", "basura", "basura_n",
               "incumplidos", "cumplimiento", "rechazados", "lb_verificadas", "puntaje")


@api_route("GET", "/suppliers/ranking", scope=READ, tags=[TAG_VER],
           summary="Ranking de mis proveedores por rendimiento verificado (empacadora)",
           params={"order": F("enum", enum=["puntaje", "rendimiento", "clase_a", "cumplimiento",
                                            "lotes", "metabisulfito"])},
           response=obj(rows={"type": "array"}, threshold="integer", suppliers="integer"))
def suppliers_ranking(ctx):
    ctx.require_type("empacadora")
    order = ctx.params.get("order") or "puntaje"
    data = ctx.env["shrimp.proveedor.ranking"].sudo().ranking(ctx.partner, orden=order)
    rows = []
    for fila in data["filas"]:
        row = {k: fila.get(k) for k in RANK_FIELDS}
        row["supplier"] = S.ref(fila["proveedor"])
        row["last_verified"] = iso_date(fila.get("ultima"))
        rows.append(row)
    return {"rows": rows, "order": data["orden"], "threshold": data["umbral"],
            "suppliers": data["proveedores"], "with_enough_sample": data["con_muestra"],
            "lots": data["lotes"], "in_progress": data["en_curso"], "weights": data["pesos"]}
