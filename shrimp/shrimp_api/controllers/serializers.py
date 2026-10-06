"""Serializadores: la lista blanca de lo que sale por la API.

Cada función recibe un registro YA autorizado (encontrado con las reglas de
acceso del dueño de la clave, o con un dominio público fijo) y devuelve un
dict con los campos permitidos. Se lee en sudo solo lo que aparece aquí:
ningún campo sale por estar en el modelo.

Lo que no sale nunca, por diseño: ids enteros, cuentas bancarias, RUC/cédula
de personas, márgenes y comisiones de la plataforma (platform_amount,
verifier_amount, margin_pct, commission_cents, rate_cents,
platform_rate_per_lb), claves y secretos, credenciales SRI, GPS y archivos
con datos personales.
"""

from .framework import iso_date, iso_dt, money, ref, selection_label


def _sel(rec, field):
    return {"code": rec[field] or None, "label": selection_label(rec, field)}


# ---------------------------------------------------------------------------
# Catálogos
# ---------------------------------------------------------------------------
def species(r):
    r = r.sudo()
    return {"id": r.uuid_ref, "name": r.name, "scientific_name": r.scientific_name or None,
            "code": r.code or None}


def stage(r):
    r = r.sudo()
    return {"id": r.uuid_ref, "name": r.name, "code": r.code or None, "sequence": r.sequence}


def genetics_line(r):
    r = r.sudo()
    return {"id": r.uuid_ref, "name": r.name, "code": r.code or None, "brand": r.brand or None,
            "species": ref(r.species_id)}


def size_grade(r):
    r = r.sudo()
    return {"id": r.uuid_ref, "name": r.name, "presentation": r.presentation,
            "sequence": r.sequence}


def uom(r):
    # Sin commission_cents: es la tarifa de la plataforma.
    r = r.sudo()
    return {"id": r.uuid_ref, "name": r.name, "code": r.code}


def taste_criterion(r):
    r = r.sudo()
    out = {"id": r.uuid_ref if "uuid_ref" in r._fields else None, "name": r.name}
    if "description" in r._fields:
        out["description"] = r.description or None
    return out


def certificate_type(r):
    r = r.sudo()
    return {"id": r.uuid_ref, "name": r.name, "issuer": r.issuer, "code": r.code or None,
            "role": r.role, "type": r.certificate_type,
            "expires": bool(r.expires_required)}


def aguaje(r):
    r = r.sudo()
    return {"id": r.uuid_ref if "uuid_ref" in r._fields else None, "name": r.name,
            "year": r.year, "number": r.numero or None,
            "date_from": iso_date(r.date_from), "date_to": iso_date(r.date_to),
            "peak_from": iso_date(r.peak_from), "peak_to": iso_date(r.peak_to),
            "label": r.etiqueta if "etiqueta" in r._fields else r.name,
            "status": r.estado if "estado" in r._fields else None}


# ---------------------------------------------------------------------------
# Socios (perfil público)
# ---------------------------------------------------------------------------
def partner_public(p):
    p = p.sudo()
    out = {
        "id": p.uuid_ref,
        "name": p.name,
        "type": p.shrimp_user_type or None,
        "city": p.city or None,
        "province": p.state_id.name or None,
        "country": p.country_id.code or None,
        "ratings": {
            "as_seller": {"avg": round(p.shrimp_rating_avg or 0.0, 2),
                          "count": p.shrimp_rating_count or 0},
        },
    }
    t = p.shrimp_user_type
    if t == "verificador":
        out["ratings"]["as_verifier"] = {"avg": round(p.verifier_rating_avg or 0.0, 2),
                                         "count": p.verifier_rating_count or 0}
        out["verifier"] = {"accredited": bool(p.verifier_is_accredited),
                           "coverage": p.ver_cobertura or p.ver_provincias or None,
                           "base": p.shrimp_ubicacion or None}
    elif t == "empacadora":
        out["packer"] = {
            "plant": p.emp_planta_nombre or None,
            "plant_location": p.shrimp_ubicacion or None,
            "certifications": [c for c, ok in (("BAP", p.emp_cert_bap), ("ASC", p.emp_cert_asc),
                                                ("HACCP", p.emp_cert_haccp)) if ok]
                              + ([p.emp_cert_otras] if p.emp_cert_otras else []),
            "markets": [m for m, ok in (("asia", p.emp_mercado_asia), ("europe", p.emp_mercado_europa),
                                         ("north_america", p.emp_mercado_norteamerica),
                                         ("local", p.emp_mercado_local)) if ok],
            "accepts_harvest_reservations": bool(p.reserva_acepta),
        }
    elif t == "maquilador":
        out["copacker"] = {
            "plant_location": p.shrimp_ubicacion or None,
            "establishment_code": p.pack_codigo_establecimiento or None,
            "license_valid": bool(p.pack_habilitacion_vigente),
            "capacity_lb_week": (p.shrimp_capacity_value or None)
                                if p.shrimp_capacity_unit == "lb_week" else None,
            "presentations": p.pack_presentaciones or None,
            "min_lot_lb": p.pack_lote_minimo_lb or None,
        }
    return out


def me(user, key):
    p = user.partner_id.sudo()
    out = partner_public(p)
    # La nota como comprador no es pública: solo la ve el propio socio.
    out["ratings"]["as_buyer"] = {"avg": round(p.buyer_rating_avg or 0.0, 2),
                                  "count": p.buyer_rating_count or 0}
    out.update({
        "email": p.email or None,
        "phone": p.phone or None,
        "website": p.website or None,
        "street": p.street or None,
        "zip": p.zip or None,
        "is_field_technician": bool(p.shrimp_is_field_tech),
        "company": ref(p.parent_id) if p.parent_id else None,
        "preferences": {},
        "api_key": {
            "prefix": key.key_prefix,
            "name": key.name,
            "scopes": sorted(key._api_scope_codes()),
            "expires_at": iso_dt(key.expires_at),
        },
    })
    # Varios perfiles: "type" sigue siendo el perfil ACTIVO (compatibilidad);
    # "roles" lista todos con su estado de aprobación.
    titular = p._shrimp_role_holder()
    out["active_role"] = p._shrimp_effective_type() or None
    out["roles"] = [{"role": r.role, "label": p._shrimp_type_label(r.role), "state": r.state,
                     "active": r.role == titular.shrimp_user_type}
                    for r in titular.shrimp_role_ids]
    mios = set(p._shrimp_roles())
    if "empacadora" in mios:
        out["preferences"]["accepts_harvest_reservations"] = bool(p.reserva_acepta)
    if "camaronera" in mios:
        out["preferences"]["publish_verified_history"] = bool(p.farm_publicar_historial)
    if "maquilador" in mios:
        out["preferences"]["listed_in_directory"] = bool(p.pack_en_directorio)
    return out


# ---------------------------------------------------------------------------
# Productos, lotes, instalaciones
# ---------------------------------------------------------------------------
def _cert_lines(product, include_number=True):
    from odoo import fields as ofields
    today = ofields.Date.context_today(product)
    out = []
    for line in product.sudo().certificate_line_ids:
        if not line.active:
            continue
        out.append({
            "certificate": ref(line.certificate_id),
            "issuer": line.issuer or None,
            "number": (line.number or None) if include_number else None,
            "issue_date": iso_date(line.issue_date),
            "expiry_date": iso_date(line.expiry_date),
            "valid": not line.expiry_date or line.expiry_date >= today,
        })
    return out


def product_public(p):
    p = p.sudo()
    return {
        "id": p.uuid_ref,
        "name": p.name,
        "seller": ref(p.seller_partner_id),
        "seller_type": p.seller_role,
        "species": ref(p.species_id),
        "stage": ref(p.stage_id),
        "genetics_line": ref(p.genetics_line_id),
        "presentation": p.presentation or None,
        "size_grade": ref(p.size_grade_id),
        "avg_size_mg": p.avg_size_mg or None,
        "survival_rate": p.survival_rate or None,
        "price": money(p.price, p.env.company.currency_id),
        "uom": ref(p.uom_id),
        "available_qty": p.available_qty,
        "location": p.location or None,
        "expected_delivery_date": iso_date(p.expected_delivery_date),
        "available_from": iso_dt(p.available_from),
        "available_to": iso_dt(p.available_to),
        "published_at": iso_dt(p.published_date),
        "requires_verification": bool(p.requires_verification),
        "certificates": _cert_lines(p),
        "seller_rating": {"avg": round(p.seller_partner_id.shrimp_rating_avg or 0.0, 2),
                          "count": p.seller_partner_id.shrimp_rating_count or 0},
        "updated_at": iso_dt(p.write_date),
    }


def product_owner(p):
    p = p.sudo()
    out = product_public(p)
    out.update({
        "state": p.state,
        "active": p.active,
        "initial_qty": p.initial_qty,
        "reserved_qty": p.reserved_qty,
        "health_status": p.health_status or None,
        "origin_facility": ref(p.origin_facility_id),
        "origin_pond": ref(p.origin_pond_id),
        "batch_code": p.batch_code or None,
        "production_date": iso_date(p.production_date),
        "traceability_notes": p.traceability_notes or None,
        "created_at": iso_dt(p.create_date),
    })
    return out


def evolution(e):
    e = e.sudo()
    return {"id": e.uuid_ref, "date": iso_dt(e.date), "stage": ref(e.stage_id),
            "avg_size_mg": e.avg_size_mg, "survival_rate": e.survival_rate,
            "health_status": e.health_status or None, "available_qty": e.available_qty,
            "note": e.note or None}


def facility(f):
    f = f.sudo()
    return {"id": f.uuid_ref, "name": f.name, "code": f.code or None,
            "facility_type": f.facility_type, "address": f.address or None,
            "city": f.city or None, "province": f.province or None,
            "country": f.country_id.code or None, "active": f.active,
            "notes": f.notes or None, "pond_count": f.pond_count,
            "updated_at": iso_dt(f.write_date)}


def pond(p):
    p = p.sudo()
    return {"id": p.uuid_ref, "name": p.name, "code": p.code or None,
            "pond_type": p.pond_type, "facility": ref(p.facility_id),
            "capacity_mode": p.capacity_mode, "length_m": p.length_m, "width_m": p.width_m,
            "depth_m": p.depth_m, "manual_volume_m3": p.manual_volume_m3,
            "area_m2": p.area_m2, "volume_m3": p.volume_m3,
            "usable_volume_m3": p.usable_volume_m3, "max_stock_units": p.max_stock_units,
            "location": p.location or None, "active": p.active, "notes": p.notes or None,
            "updated_at": iso_dt(p.write_date)}


def lot(l):
    l = l.sudo()
    return {"id": l.uuid_ref, "product": ref(l.product_id), "owner": ref(l.owner_id),
            "initial_qty": l.initial_qty, "available_qty": l.available_qty,
            "uom": ref(l.uom_id), "state": l.state,
            "origin_move": l.origin_move_id.uuid_ref or None,
            # Perfil de la cuenta que tiene el lote y, si nació de una
            # transferencia interna entre perfiles, el lote del que salió.
            "held_role": (l._shrimp_held_role() or None) if "held_role" in l._fields else None,
            "parent_lot": (l.parent_lot_id.uuid_ref or None) if "parent_lot_id" in l._fields else None,
            "created_at": iso_dt(l.create_date), "updated_at": iso_dt(l.write_date)}


def move(m):
    m = m.sudo()
    return {"id": m.uuid_ref, "product": ref(m.product_id), "from": ref(m.source_partner_id),
            "to": ref(m.dest_partner_id), "qty": m.qty, "date": iso_dt(m.date),
            "parent_move": m.parent_move_id.uuid_ref or None,
            "transaction": ref(m.transaction_id),
            # Movimientos internos (siembra, producción, ajuste, empaque,
            # salida): no cambian el dueño; documentan la cantidad física.
            "type": m.move_type or "transfer",
            "direction": m.direction if (m.move_type or "transfer") != "transfer" else None,
            "lot": m.lot_id.uuid_ref or None,
            "reason": m.reason or None,
            # Transferencia interna entre perfiles de la misma cuenta.
            "from_role": (m.from_role or None) if "from_role" in m._fields else None,
            "to_role": (m.to_role or None) if "to_role" in m._fields else None,
            "export": m.export_id.uuid_ref or None if "export_id" in m._fields else None}


def export(e):
    e = e.sudo()
    cur = e.currency_id or e.env.company.currency_id
    return {"id": e.uuid_ref, "reference": e.name, "state": e.state,
            "date": iso_date(e.date),
            "destination_buyer": e.destination_buyer or None,
            "destination_country": e.destination_country_id.code or None,
            "destination_place": e.destination_place or None,
            "confidential": bool(e.confidential),
            "dae_number": e.dae_number or None, "invoice_number": e.invoice_number or None,
            "container": e.container or None, "boxes": e.boxes or None,
            "qty": e.qty, "uom": ref(e.uom_id),
            "unit_price": money(e.price_unit, cur) if e.price_unit else None,
            "total": money(e.amount_total, cur) if e.amount_total else None,
            "lines": [{"lot": l.lot_id.uuid_ref, "product": ref(l.product_id), "qty": l.qty,
                       "move": l.move_id.uuid_ref or None} for l in e.line_ids],
            "updated_at": iso_dt(e.write_date)}


def allocation(a):
    a = a.sudo()
    return {"id": a.uuid_ref, "lot": a.stock_lot_id.uuid_ref, "pond": ref(a.pond_id),
            "product": ref(a.product_id), "allocated_qty": a.allocated_qty,
            "allocation_date": iso_date(a.allocation_date), "state": a.state,
            "notes": a.notes or None}


# ---------------------------------------------------------------------------
# Compras / ventas
# ---------------------------------------------------------------------------
def transaction(t, partner):
    t = t.sudo()
    role = "buyer" if t.buyer_partner_id == partner else "seller"
    cur = t.env.company.currency_id
    v = t.verification_ids[:1] if "verification_ids" in t._fields else t.browse()
    return {
        "id": t.uuid_ref,
        "reference": t.name,
        "my_role": role,
        "state": t.state,
        "type": t.transaction_type,
        "product": ref(t.product_id),
        "result_product": ref(t.result_product_id),
        "seller": ref(t.seller_partner_id),
        "buyer": ref(t.buyer_partner_id),
        # Perfil con el que actuó cada parte (cuentas con varios perfiles).
        "seller_role": t.seller_role or None,
        "buyer_role": t.buyer_role or None,
        "qty": t.transaction_qty,
        "uom": ref(t.product_id.uom_id),
        "unit_price": money(t.price_unit, cur),
        "total": money(t.amount_total, cur),
        "delivery_date": iso_date(t.delivery_date),
        "location": t.location or None,
        "needs_verification": bool(getattr(t, "needs_verification", False)),
        "verification": v.uuid_ref if v else None,
        "verification_mode": (v.verification_mode or None) if v else None,
        "dispatch": t.dispatch_id.uuid_ref if "dispatch_id" in t._fields and t.dispatch_id else None,
        # Factura de la MERCADERÍA, emitida por el vendedor y registrada en la
        # compra (la plataforma no la emite). Solo la ven las partes.
        "seller_invoice": {
            "number": t.seller_invoice_number or None,
            "access_key": t.seller_invoice_access_key or None,
            "date": iso_date(t.seller_invoice_date),
            "has_file": bool(t.seller_invoice_attachment_id),
        } if t.has_seller_invoice else None,
        "created_at": iso_dt(t.create_date),
        "updated_at": iso_dt(t.write_date),
    }


def charge(c):
    # Sin rate_cents: es la tarifa de la plataforma.
    c = c.sudo()
    return {"id": c.uuid_ref, "reference": c.name, "type": c.charge_type,
            "state": c.state, "description": c.description or None,
            "origin": c.origin or None, "transaction": ref(c.transaction_id),
            "product": ref(c.product_id), "qty": c.qty, "uom": ref(c.uom_id),
            "amount": money(c.amount, c.currency_id), "date": iso_dt(c.date),
            "invoice_number": c.invoice_id.name if c.invoice_id else None,
            "invoice_state": c.invoice_state or None}


def check_request(c, partner):
    c = c.sudo()
    return {"id": c.uuid_ref, "reference": c.name,
            "my_role": "seller" if c.seller_partner_id == partner else "buyer",
            "state": c.state, "product": ref(c.product_id), "seller": ref(c.seller_partner_id),
            "buyer": ref(c.buyer_partner_id), "qty": c.qty, "uom": ref(c.uom_id),
            "check_fee": money(c.check_fee, c.currency_id),
            "transaction": ref(c.transaction_id), "note": c.note or None,
            "reviewed_at": iso_dt(c.reviewed_date), "created_at": iso_dt(c.create_date),
            "updated_at": iso_dt(c.write_date)}


# ---------------------------------------------------------------------------
# Verificación y despacho
# ---------------------------------------------------------------------------
def verification_role(v, partner):
    v = v.sudo()
    if partner == v.buyer_partner_id:
        return "buyer"
    if partner == v.seller_partner_id:
        return "seller"
    if partner == v.technician_partner_id:
        return "technician"
    if partner == v.verifier_partner_id:
        return "verifier"
    return None


def verification(v, partner, detail=False):
    v = v.sudo()
    out = {
        "id": v.uuid_ref,
        "reference": v.name,
        "my_role": verification_role(v, partner),
        "state": v.state,
        "scope": v.scope or None,
        "transaction": v.transaction_id.uuid_ref,
        "product": ref(v.product_id),
        "buyer": ref(v.buyer_partner_id),
        "seller": ref(v.seller_partner_id),
        "verifier": ref(v.verifier_partner_id),
        # Modo de verificación: «platform» (verificadora acreditada de la
        # plataforma) o «declared» (declarada por las partes).
        "verification_mode": v.verification_mode,
        "verification_label": v.verification_label(),
        "technician_assigned": bool(v.technician_partner_id),
        # El nombre del técnico solo lo ve su empresa (y él mismo).
        "technician": ref(v.technician_partner_id)
        if verification_role(v, partner) in ("verifier", "technician") else None,
        "dispatch": v.dispatch_id.uuid_ref or None,
        "dispatch_eta": iso_dt(v.dispatch_eta),
        "fee": money(v.fee, v.currency_id),
        "acceptance_state": v.acceptance_state,
        "acceptance_deadline": iso_dt(v.acceptance_deadline),
        "verified_at": iso_dt(v.verified_date),
        "line_count": len(v.line_ids),
        "count_count": len(v.count_ids),
        "updated_at": iso_dt(v.write_date),
    }
    if not detail:
        return out
    out.update({
        "batch_code": v.batch_code or None,
        "pond": ref(v.pond_id) if v.pond_id else ({"id": None, "name": v.pond_label} if v.pond_label else None),
        "facility": ref(v.facility_id),
        "plant_name": v.plant_name or None,
        "harvest_date": iso_date(v.harvest_date),
        "process_date": iso_date(v.process_date),
        "field_started_at": iso_dt(v.field_start_date),
        "weights": {"sent_lb": v.weight_sent_lb, "plant_lb": v.weight_plant_lb,
                    "trash_lb": v.trash_lb, "overweight_lb": v.overweight_lb,
                    "overweight_factor": v.overweight_factor, "net_lb": v.net_weight_lb},
        "presentation": v.presentation or None,
        "presentation_matches_product": bool(v.presentation_matches_product),
        "metabisulfite": {"ppm": v.metabisulfite_ppm, "limit_ppm": v.metabisulfite_limit_ppm,
                          "result": v.metabisulfite_result or None,
                          "notes": v.metabisulfite_notes or None},
        "classification": {
            "total_lb": v.total_processed_lb, "class_a_lb": v.class_a_lb,
            "class_b_lb": v.class_b_lb, "class_c_lb": v.class_c_lb,
            "yield_pct": v.yield_pct, "yield_class_a_pct": v.yield_class_a_pct,
            "yield_class_b_pct": v.yield_class_b_pct, "yield_class_c_pct": v.yield_class_c_pct,
            "lines": [{"id": l.uuid_ref, "quality_class": l.quality_class,
                       "size_code": l.size_code, "weight_lb": l.weight_lb,
                       "percent_of_total": round(l.percent_of_total, 2)} for l in v.line_ids],
        },
        "taste": {"result": v.taste_result or None, "notes": v.taste_notes or None,
                  "criteria_ok": [c.name for c in v.taste_criteria_ok_ids]},
        "grams": {"farm": v.grams_farm, "plant_1": v.grams_plant_1, "plant_2": v.grams_plant_2,
                  "variation": v.grams_variation},
        "counts": [{"id": c.uuid_ref, "value": c.value, "note": c.note or None} for c in v.count_ids],
        "larvae": {"qty_verified": v.larvae_qty_verified, "survival_rate": v.larvae_survival_rate,
                   "avg_size_mg": v.larvae_avg_size_mg,
                   "health_status": v.larvae_health_status or None,
                   "health_notes": v.larvae_health_notes or None,
                   "qty_diff_pct": v.larvae_qty_diff_pct,
                   "survival_diff_pp": v.larvae_survival_diff} if v.scope == "larvae" else None,
        "incident_notes": v.incident_notes or None,
        "verdict_notes": v.verdict_notes or None,
        "declared": {
            "source": v.declared_source or None,
            "external_verifier_name": v.external_verifier_name or None,
            "external_verifier_vat": v.external_verifier_vat or None,
            "has_report_pdf": bool(v.declared_report_file),
            "declarant": ref(v.declarant_partner_id),
            "declarant_role": v.declarant_role or None,
            "submitted_at": iso_dt(v.declared_submitted_date),
        } if v.verification_mode == "declared" else None,
        "photo_count": len(v.photo_ids),
        "acceptances": [{"role": a.role, "partner": ref(a.partner_id), "decision": a.decision,
                         "reason": a.reason or None, "decided_at": iso_dt(a.decided_at),
                         "automatic": bool(a.auto),
                         "counter_price": money(a.counter_price, a.currency_id) if a.decision == "counter" else None,
                         "can_revert": a.signoff_can_undo(actor=partner) if a.partner_id == partner else False}
                        for a in v.acceptance_ids],
        "warnings": v.report_warnings() if out["my_role"] in ("verifier", "technician") else [],
    })
    return out


def dispatch(d, partner):
    d = d.sudo()
    role = None
    if partner == d.seller_partner_id:
        role = "seller"
    elif partner == d.buyer_partner_id:
        role = "buyer"
    elif partner in (d.verifier_partner_id, d.technician_partner_id):
        role = "verifier"
    return {
        "id": d.uuid_ref, "my_role": role, "state": d.state,
        "transaction": d.transaction_id.uuid_ref, "product": ref(d.product_id),
        "seller": ref(d.seller_partner_id), "buyer": ref(d.buyer_partner_id),
        "verifier": ref(d.verifier_partner_id), "verification": d.verification_id.uuid_ref or None,
        "harvest_date": iso_date(d.harvest_date), "farm_departure": iso_dt(d.farm_departure),
        "eta": iso_dt(d.eta), "eta_first": iso_dt(d.eta_first), "eta_changes": d.eta_changes,
        "carrier_name": d.carrier_name or None, "vehicle_plate": d.vehicle_plate or None,
        "carrier_phone": d.carrier_phone or None, "notes": d.notes or None,
        "actual_arrival": iso_dt(d.actual_arrival),
        "delay_minutes": d.delay_minutes if d.actual_arrival and d.eta else None,
        "on_time": bool(d.on_time) if d.actual_arrival and d.eta else None,
        "updated_at": iso_dt(d.write_date),
    }


# ---------------------------------------------------------------------------
# Empacadora: listas de precios, reservas, avisos
# ---------------------------------------------------------------------------
def price_list(l, partner, detail=False):
    l = l.sudo()
    issuer = l.issuer_partner_id == partner
    out = {
        "id": l.uuid_ref, "name": l.name, "my_role": "issuer" if issuer else "recipient",
        "issuer": ref(l.issuer_partner_id), "state": l.state,
        "issue_date": iso_date(l.issue_date), "dispatch_from": iso_date(l.dispatch_from),
        "dispatch_to": iso_date(l.dispatch_to), "open_ended": bool(l.open_ended),
        "is_current": bool(l.is_current), "is_upcoming": bool(l.is_upcoming),
        "aguaje": ref(l.aguaje_id) if l.aguaje_id else None,
        "currency": l.currency_id.name,
        "payment": {"advance_pct": l.advance_pct, "advance_days": l.advance_days,
                    "balance_days": l.balance_days, "notes": l.payment_notes or None},
        "updated_at": iso_dt(l.write_date),
    }
    # Confidencial: solo el emisor ve a quién más se la mandó.
    if issuer:
        out["recipients"] = [ref(p) for p in l.recipient_ids]
        out["auto_publish"] = bool(l.auto_publish)
        out["auto_publish_date"] = iso_date(l.auto_publish_date)
    if detail:
        out["quality_conditions"] = l.quality_conditions or None
        out["lines"] = [{"size_grade": ref(x.size_grade_id), "presentation": x.presentation,
                         "channel": x.channel or None, "quality": x.quality, "uom": x.uom,
                         "price": money(x.price, x.currency_id)} for x in l.line_ids]
        out["bonuses"] = [{"name": b.name, "amount": money(b.amount, b.currency_id),
                           "note": b.note or None} for b in l.bonus_ids]
    return out


def forecast(f, partner, detail=False):
    f = f.sudo()
    farmer = f.farmer_partner_id == partner
    out = {
        "id": f.uuid_ref, "reference": f.name, "my_role": "farmer" if farmer else "packer",
        "farmer": ref(f.farmer_partner_id), "state": f.state,
        "pond": ref(f.pond_id), "facility": ref(f.facility_id),
        "expected_date": iso_date(f.expected_date), "expected_lb": f.expected_lb,
        "presentation": f.presentation, "size_grade": ref(f.size_grade_id),
        "tolerance_lb_pct": f.tolerance_lb_pct, "tolerance_size_steps": f.tolerance_size_steps,
        "date_tolerance_days": f.date_tolerance_days, "lb_min": f.lb_min, "lb_max": f.lb_max,
        "accepts_commitments": bool(f.admite_compromisos), "notes": f.notes or None,
        "actual": {"date": iso_date(f.actual_date), "lb": f.actual_lb or None,
                   "size_grade": ref(f.actual_size_grade_id)} if f.actual_date else None,
        "product": ref(f.product_id),
        "updated_at": iso_dt(f.write_date),
    }
    if farmer:
        out["recipients"] = [ref(p) for p in f.recipient_ids]
        out["open_call"] = bool(f.open_call)
    if detail:
        comms = f.commitment_ids if farmer else f.commitment_ids.filtered(
            lambda c: c.packer_partner_id == partner)
        out["commitments"] = [commitment(c, partner) for c in comms]
    return out


def commitment(c, partner):
    c = c.sudo()
    cur = c.currency_id
    out = {
        "id": c.uuid_ref, "forecast": c.forecast_id.uuid_ref,
        "my_role": "farmer" if c.farmer_partner_id == partner else "packer",
        "farmer": ref(c.farmer_partner_id), "packer": ref(c.packer_partner_id),
        "state": c.state, "committed_lb": c.committed_lb, "price_mode": c.price_mode,
        "price_per_lb": money(c.price_per_lb, cur) if c.price_mode == "fijo" else None,
        "step_delta_per_lb": money(c.step_delta_per_lb, cur),
        "price_floor_per_lb": money(c.price_floor_per_lb, cur) if c.price_mode == "lista" else None,
        "valid_until": iso_date(c.valid_until), "notes": c.notes or None,
        "accepted_at": iso_dt(c.accepted_at),
        "settlement": {"lb": c.settled_lb, "price_per_lb": money(c.settled_price_per_lb, cur),
                       "total": money(c.settled_total, cur), "size_steps": c.settled_steps,
                       "floor_applied": bool(c.floor_applied),
                       "deviation_notes": c.deviation_notes or None}
        if c.state in ("to_confirm", "honored", "released") else None,
        "break": {"side": c.break_side, "by": ref(c.broken_by_partner_id),
                  "at": iso_dt(c.broken_at), "reason": c.break_reason or None}
        if c.state == "broken" else None,
        "product": ref(c.product_id), "transaction": ref(c.transaction_id),
        "confirmations": [{"role": x.role, "round": x.ronda, "decision": x.decision,
                           "reason": x.reason or None, "decided_at": iso_dt(x.decided_at),
                           "can_revert": x.signoff_can_undo(actor=partner)
                           if x.partner_id == partner else False}
                          for x in c.confirmation_ids.filtered("active")],
        "updated_at": iso_dt(c.write_date),
    }
    return out


def lot_alert(a):
    a = a.sudo()
    return {"id": a.uuid_ref if "uuid_ref" in a._fields else None,
            "product": ref(a.product_id), "seller": ref(a.seller_partner_id),
            "price_list": ref(a.price_list_id), "quoted_price": a.price, "uom": a.uom,
            "qty": a.qty, "value": a.amount, "sent_at": iso_dt(a.sent_date)}


# ---------------------------------------------------------------------------
# Maquila
# ---------------------------------------------------------------------------
def copack_offer(o):
    o = o.sudo()
    return {"id": o.uuid_ref, "request": o.request_id.uuid_ref, "copacker": ref(o.copacker_partner_id),
            "client": ref(o.client_partner_id), "state": o.state,
            "rate_per_lb": money(o.rate_per_lb, o.currency_id), "capacity_lb": o.capacity_lb,
            "available_from": iso_date(o.available_from), "available_to": iso_date(o.available_to),
            "estimated_total": money(o.estimated_total, o.currency_id), "notes": o.notes or None,
            "updated_at": iso_dt(o.write_date)}


def copack_request(r, partner, detail=False):
    r = r.sudo()
    client = r.client_partner_id == partner
    out = {"id": r.uuid_ref, "reference": r.name, "my_role": "client" if client else "copacker",
           "client": ref(r.client_partner_id), "copacker": ref(r.copacker_partner_id),
           "is_open": bool(r.is_open), "state": r.state, "product": ref(r.product_id),
           "quantity_lb": r.quantity_lb, "presentation": r.presentation,
           "size_grade": ref(r.size_grade_id), "needed_from": iso_date(r.needed_from),
           "needed_to": iso_date(r.needed_to), "supplies_notes": r.supplies_notes or None,
           "notes": r.notes or None, "order": r.order_id.uuid_ref or None,
           "updated_at": iso_dt(r.write_date)}
    if detail:
        offers = r.offer_ids if client else r.offer_ids.filtered(
            lambda o: o.copacker_partner_id == partner)
        out["offers"] = [copack_offer(o) for o in offers]
    return out


def copack_order(o, partner, detail=False):
    # Sin platform_rate_per_lb / platform_amount: comisión de la plataforma.
    o = o.sudo()
    cur = o.currency_id
    out = {"id": o.uuid_ref, "reference": o.name,
           "my_role": "copacker" if o.copacker_partner_id == partner else "client",
           "state": o.state, "acceptance_state": o.acceptance_state,
           "self_packing": bool(o.self_packing),
           "client": ref(o.client_partner_id), "copacker": ref(o.copacker_partner_id),
           "request": o.request_id.uuid_ref or None, "product": ref(o.product_id),
           "agreed_qty_lb": o.agreed_qty_lb, "agreed_overrun_pct": o.agreed_overrun_pct,
           "rate_per_lb": money(o.rate_per_lb, cur),
           "received_lb": o.received_lb, "received_at": iso_dt(o.received_date),
           "packed_lb": o.packed_lb, "packed_at": iso_dt(o.packed_date), "boxes": o.boxes,
           "packed_presentation": o.packed_presentation or None,
           "packed_presentation_note": o.packed_presentation_note or None,
           "difference_lb": o.difference_lb, "difference_pct": o.difference_pct,
           "balanced": bool(o.cuadra), "tolerance_pct": o.tolerance_pct,
           "service_amount": money(o.service_amount, cur),
           "updated_at": iso_dt(o.write_date)}
    if detail:
        out.update({
            "supplies_notes": o.supplies_notes or None,
            "supplies_received": bool(o.supplies_received),
            "supplies_issue": o.supplies_issue or None,
            "signatures": [{"role": a.role, "partner": ref(a.partner_id), "round": a.ronda,
                            "decision": a.decision, "reason": a.reason or None,
                            "decided_at": iso_dt(a.decided_at),
                            "can_revert": a.signoff_can_undo(actor=partner)
                            if a.partner_id == partner else False}
                           for a in o.acceptance_ids.filtered("active")],
        })
    return out


def copack_tariff(t, partner, detail=False):
    t = t.sudo()
    issuer = t.copacker_partner_id == partner
    out = {"id": t.uuid_ref, "name": t.name, "my_role": "copacker" if issuer else "client",
           "copacker": ref(t.copacker_partner_id), "state": t.state,
           "valid_from": iso_date(t.valid_from), "valid_to": iso_date(t.valid_to),
           "open_ended": bool(t.open_ended), "is_current": bool(t.is_current),
           "min_lot_lb": t.min_lot_lb or None, "payment_notes": t.payment_notes or None,
           "conditions": t.conditions or None, "currency": t.currency_id.name,
           "updated_at": iso_dt(t.write_date)}
    if issuer:
        out["recipients"] = [ref(p) for p in t.recipient_ids]
    if detail:
        out["lines"] = [{"presentation": x.presentation, "pack_format": x.pack_format,
                         "from_lb": x.from_lb, "rate_per_lb": money(x.rate_per_lb, x.currency_id)}
                        for x in t.line_ids]
    return out


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def webhook_subscription(s):
    s = s.sudo()
    return {"id": s.uuid_ref, "name": s.name, "url": s.url,
            "events": s._event_list(), "active": s.active,
            "failure_count": s.failure_count, "last_success_at": iso_dt(s.last_success_at),
            "last_failure_at": iso_dt(s.last_failure_at), "last_error": s.last_error or None,
            "disabled_reason": s.disabled_reason or None,
            "created_at": iso_dt(s.create_date), "updated_at": iso_dt(s.write_date)}


def webhook_delivery(d):
    d = d.sudo()
    return {"id": d.uuid_ref, "event_type": d.event_type,
            "resource": {"type": d.resource_type, "uuid": d.resource_uuid},
            "occurred_at": iso_dt(d.occurred_at), "state": d.state, "attempts": d.attempts,
            "next_attempt_at": iso_dt(d.next_attempt_at), "last_status_code": d.last_status_code or None,
            "last_error": d.last_error or None, "delivered_at": iso_dt(d.delivered_at)}
