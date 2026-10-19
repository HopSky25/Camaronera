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
                 line_count="integer", count_count="integer", updated_at="datetime",
                 # Equipo de campo (solo lo ven la empresa y sus técnicos).
                 technician="ref",
                 support_technicians={"type": "array", "items": {"$ref": "#/components/schemas/Ref"}},
                 # Decisiones de la auditoría (19.0.1.23.0).
                 verifier_response="string", verifier_response_deadline="datetime",
                 rejection_reason="string", contest_state="string", contest_deadline="datetime",
                 second_opinion_of="string", counter_outcome="string",
                 declared_deadline="datetime", payout_state="string",
                 # Plazos, «no presentado», declinada y lo que puedo hacer (detalle).
                 deadline_assign="datetime", deadline_verdict="datetime",
                 deadline_overdue="string", not_presented_arrival="datetime",
                 verifier_decline_reason="string", superseded="boolean",
                 second_opinion="string", can_verdict="boolean", can_respond="boolean",
                 can_decline="boolean", can_request_second_opinion="boolean",
                 contest_hours="integer")
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
        # Responsable o de apoyo (equipo de campo).
        return ["|", ("technician_partner_id", "=", p), ("support_technician_ids", "in", [p])]
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


@api_route("POST", "/verifications/{id}/acceptance", signed=True, scope="verifications:write", tags=[TAG_VER],
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


@api_route("POST", "/verifications/{id}/acceptance:revert", signed=True, scope="verifications:write",
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
    # Técnico asignado: responsable o de apoyo (los dos cargan datos de campo;
    # el veredicto lo filtra _puede_dictaminar: solo responsable o empresa).
    is_tech = v.shrimp_is_assigned_tech(ctx.partner)
    if not (is_admin or is_tech):
        raise forbidden("Esta operación es de la empresa verificadora o del técnico asignado.")
    if editable and v.is_final:
        raise ApiError(409, "invalid-state", "La verificación ya está cerrada.")
    return v, is_admin


TECH_SCHEMA = obj(id="uuid", name="string", role="string", active="boolean")


@api_route("GET", "/me/technicians", scope=READ, tags=[TAG_VER], response={
               "type": "object", "properties": {"data": {"type": "array", "items": TECH_SCHEMA}}},
           summary="Técnicos de campo de mi empresa verificadora",
           description="Los contactos de la empresa marcados como técnico de campo y activos: son los "
                       "uuid que admite POST /verifications/{id}:assign. Lo puede pedir la empresa o "
                       "uno de sus técnicos. 403 si la cuenta no tiene aprobado el perfil verificador.")
def me_technicians(ctx):
    titular = ctx.partner._shrimp_role_holder()
    if not titular._shrimp_has_role("verificador"):
        raise forbidden("Tu cuenta no tiene aprobado el perfil verificador.")
    techs = titular.sudo().child_ids.filtered(lambda c: c.shrimp_is_field_tech and c.active)
    return {"data": [{
        "id": t.uuid_ref,
        "name": t.name,
        "role": t.tech_role_id.name or None,
        "active": bool(t.active),
    } for t in techs.sorted(lambda t: (t.name or "").lower())]}


@api_route("POST", "/verifications/{id}:assign", signed=True, scope="verifications:write", tags=[TAG_VER],
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


SUPPORT_SPEC = {"add": F("array", items={"technician": F("string", required=True)},
                         description="Técnicos de apoyo a sumar (uuid de técnicos activos de la empresa)"),
                "remove": F("array", items={"technician": F("string", required=True)},
                            description="Técnicos de apoyo a quitar")}


@api_route("POST", "/verifications/{id}:support-technicians", signed=True, scope="verifications:write",
           tags=[TAG_VER], body=SUPPORT_SPEC,
           summary="El administrador de la verificadora suma o quita técnicos de apoyo",
           description="Los técnicos de apoyo cargan datos de campo (informe, inicio en campo) "
                       "pero no firman el veredicto: lo firma el técnico responsable "
                       "(technician) o la empresa. Exige un responsable asignado.",
           response=VER_SCHEMA)
def verification_support_technicians(ctx, id):
    v, is_admin = _as_verifier(ctx, id, editable=True)
    if not is_admin:
        raise forbidden("El equipo lo arma el administrador de la empresa verificadora.")
    body = ctx.clean(SUPPORT_SPEC)
    company = ctx.partner.shrimp_verifier_company()
    techs = company.field_tech_ids.filtered("active")

    def resolve(items):
        refs = [i["technician"] for i in (items or [])]
        found = techs.filtered(lambda t: t.uuid_ref in refs)
        if len(found) != len(set(refs)):
            raise invalid("Algún técnico no es un técnico activo de tu empresa.")
        return found

    add, remove = resolve(body.get("add")), resolve(body.get("remove"))
    if remove:
        v.action_remove_support_technician(remove)
    if add:
        v.action_add_support_technician(add)
    return S.verification(v, ctx.partner, detail=True)


@api_route("POST", "/verifications/{id}:start", signed=True, scope="verifications:write", tags=[TAG_VER],
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


@api_route("PATCH", "/verifications/{id}/report", signed=True, scope="verifications:write", tags=[TAG_VER],
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


VERDICT_SPEC = {
    "verdict": F("enum", required=True, enum=["approve", "approve_obs", "reject"]),
    "notes": F("text", description="Obligatorio con observaciones o rechazo"),
    "reject_reason": F("enum", enum=["field", "not_presented"],
                       description="Solo con verdict=reject. «field» (por defecto): producto no "
                                   "conforme según el informe de campo; exige la verificación en "
                                   "campo o con el informe completo. «not_presented»: el producto "
                                   "no se presentó en la planta; se puede emitir sin trabajo de "
                                   "campo y exige arrival_at y al menos una foto (photos)."),
    "arrival_at": F("datetime", description="Hora de llegada del técnico a la planta "
                                            "(obligatoria con not_presented)"),
    "photos": F("array", items={"data": F("string", required=True, max_length=7 * 1024 * 1024,
                                          description="Imagen en base64 (JPEG/PNG, 5 MB máx.)"),
                                "name": F("string")},
                description="Fotos de evidencia (obligatorias con not_presented si la "
                            "verificación todavía no tiene fotos)"),
}


@api_route("POST", "/verifications/{id}:verdict", signed=True, scope="verifications:write", tags=[TAG_VER],
           # Las fotos de evidencia van en base64 en el cuerpo: el tope general
           # de 1 MB no alcanzaría ni para una.
           body=VERDICT_SPEC, max_body=8 * 1024 * 1024,
           summary="Emite el veredicto (técnico que fue a campo o administrador)",
           description="Aprobar, aprobar con observaciones o rechazar por el informe exigen el "
                       "trabajo de campo (state in_field o done). Sin campo solo se puede rechazar "
                       "con reject_reason=not_presented y evidencia (fotos + arrival_at): el "
                       "honorario lo paga el vendedor y suma un strike. Un rechazo por el informe "
                       "deja al vendedor un plazo para pedir una segunda opinión: la compra se "
                       "cancela cuando el rechazo queda firme.",
           response=VER_SCHEMA)
def verification_verdict(ctx, id):
    v, _admin = _as_verifier(ctx, id, editable=True)
    if not v._puede_dictaminar(ctx.partner):
        raise forbidden("El veredicto lo firma el técnico que fue a campo o el administrador.")
    body = ctx.clean(VERDICT_SPEC)
    state = {"approve": "approved", "approve_obs": "approved_obs", "reject": "rejected"}[body["verdict"]]
    if state == "rejected" and body.get("reject_reason") == "not_presented":
        photos = []
        for i, foto in enumerate(body.get("photos") or []):
            try:
                raw = base64.b64decode(foto["data"], validate=True)
            except (binascii.Error, ValueError):
                raise invalid("photos[%s].data no es base64 válido." % i)
            mime = guess_mimetype(raw)
            if len(raw) > 5 * 1024 * 1024 or mime not in ("image/jpeg", "image/png", "image/webp"):
                raise invalid("photos[%s] tiene que ser una imagen JPEG/PNG/WebP de 5 MB como máximo." % i)
            photos.append((foto.get("name") or "evidencia_%s" % (i + 1), raw, mime))
        if photos:
            att = ctx.env["ir.attachment"].sudo().create([{
                "name": nombre, "type": "binary", "datas": base64.b64encode(raw), "mimetype": mime,
                "res_model": "shrimp.verification", "res_id": v.id, "public": False,
            } for nombre, raw, mime in photos])
            v.write({"photo_ids": [(4, a.id) for a in att]})
        v.action_not_presented(body.get("arrival_at") or False, body.get("notes"))
        return S.verification(v, ctx.partner, detail=True)
    if v.state == "in_field":
        v.action_mark_done()
    v._close(state, notes=body.get("notes") or None,
             rejection_kind="field" if state == "rejected" else None)
    return S.verification(v, ctx.partner, detail=True)


@api_route("POST", "/verifications/{id}:respond", scope="verifications:write", tags=[TAG_VER],
           body={"decision": F("enum", required=True, enum=["accept", "decline"]),
                 "reason": F("text", description="Obligatorio al declinar")},
           summary="La verificadora acepta o declina la orden (administrador de la empresa)",
           description="Dentro del plazo de respuesta (24 h por defecto desde la asignación) y antes "
                       "de ir a campo. Declinada (o sin respuesta en el plazo), el comprador elige "
                       "otra verificadora o cancela sin costo. Asignar el técnico también acepta la "
                       "orden.",
           response=VER_SCHEMA)
def verification_respond(ctx, id):
    v, is_admin = _as_verifier(ctx, id, editable=True)
    if not is_admin:
        raise forbidden("La orden la acepta o la declina el administrador de la empresa verificadora.")
    body = ctx.clean({"decision": F("enum", required=True, enum=["accept", "decline"]),
                      "reason": F("text")})
    v._shrimp_check_response_admin(ctx.partner)
    if body["decision"] == "accept":
        v.action_verifier_accept()
    else:
        v.action_verifier_decline(body.get("reason"))
    return S.verification(v, ctx.partner, detail=True)


# ---------------------------------------------------------------------------
# Compra verificada (con modo) y verificación DECLARADA por las partes
# ---------------------------------------------------------------------------
MODE_DOC = ("«platform»: verificadora acreditada de la plataforma (verifier obligatorio, con "
            "honorario). «declared»: verificación declarada por las partes (sin verificadora "
            "de la plataforma ni honorario): el comprador o el vendedor cargan el informe y lo "
            "presentan; la otra parte lo acepta/rechaza/contraoferta. Si la matriz de verificación "
            "(vendedor → comprador) dice «No aplica» para la compra, se rechaza con 422.")
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


@api_route("POST", "/verifications", signed=True, scope="verifications:write", tags=[TAG_VER],
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
        # La tarifa publicada por la verificadora elegida (o la global si no tiene).
        fee = ctx.env["shrimp.verification.fee"].sudo().compute(vals["qty"], verifier)
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


@api_route("PATCH", "/dispatches/{id}", signed=True, scope="dispatch:write", tags=[TAG_DSP], body=PLAN_SPEC,
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


@api_route("POST", "/dispatches/{id}/arrival", signed=True, scope="dispatch:write", tags=[TAG_DSP],
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
