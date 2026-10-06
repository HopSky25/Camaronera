# -*- coding: utf-8 -*-
"""Escenario de trazabilidad de punta a punta (TRAZA-DEMO) — v2.

Cambios respecto a v1 (adaptado a las correcciones de trazabilidad):
- el laboratorio DECLARA su producción real: 90.000 millares de nauplio ->
  64.800 de PL12 (72 %), con movimiento «production";
- por eso A compra 48.000 y B 16.000 millares (no caben 60.000 + 20.000);
- la siembra CONSUME el lote de larva (movimiento «sowing");
- el peso verificado en planta ajusta el lote del comprador (ajuste + basura);
- el empaque crea el lote empacado y consume la merma;
- E1, E2 y E3 registran su SALIDA / EXPORTACIÓN (shrimp.export) en vez de un
  mensaje en el chatter;
- si la base no tiene los usuarios de demo, crea sus propios actores.

Uso (Odoo shell, el código se lee por stdin):
    odoo-bin shell -c <conf> -d <db> --no-http < escenario.py

Cadena:
  Semillero (nauplio, 100.000 millares)
    -> Laboratorio compra 90.000 millares y produce larva PL12
       -> Camaronera A compra 60.000 millares, siembra 4.000 en la piscina A-01
          -> cosecha 94.000 lb
             -> Empacadora E1 compra 40.000 lb (verificada)   -> exporta (fuera de plataforma)
             -> co-packing de A (maquilador 1): 54.000 lb recibidas / 53.760 lb empacadas
                -> Empacadora E3 (exportadora) compra 53.760 lb empacadas (verificada)
       -> Camaronera B compra 20.000 millares (ADAPTACIÓN: la plataforma no deja
          que una camaronera le compre a otra), siembra 2.000 en B-01
          -> cosecha 48.000 lb -> Empacadora E2 compra 48.000 lb (verificada)
             -> co-packing de E2 (maquilador 2) -> exporta (fuera de plataforma)

Idempotente: si ya existe el producto marcador no crea nada, solo imprime el
resumen. Todas las escrituras de negocio pasan por los mismos métodos que
llaman los controladores del portal, con el usuario de cada rol (sudo + actor
explícito, igual que el controlador). Las fechas se reubican al final de cada
paso (SQL directo, sin efectos de negocio) para repartir el recorrido en el
tiempo, porque los métodos sellan "ahora".
"""
import base64
import json
from datetime import date, datetime, timedelta

from odoo import fields

MARK = "TRAZA-DEMO"
P_SEM = MARK + " Nauplio Vannamei Lote 100K"
P_LAB = MARK + " Larva PL12 Vannamei (Lab, de Nauplio 100K)"
P_ADULT_A = MARK + " Camarón entero 40/50 · Camaronera A · Piscina A-01"
P_ADULT_B = MARK + " Camarón entero 50/60 · Camaronera B · Piscina B-01"
PDF_MIN = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
TZ_OFFSET = timedelta(hours=5)       # Ecuador continental: UTC-5

SU = env(su=True)
OUT = []


def log(msg):
    print(msg)
    OUT.append(msg)


def d(s):
    return fields.Date.to_date(s)


def dtl(s):
    """'YYYY-MM-DD HH:MM' hora local de Ecuador -> datetime UTC naive."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M") + TZ_OFFSET


def sqlset(rec, **vals):
    """Reubica fechas sin pasar por la lógica de negocio (solo backdating)."""
    if not rec:
        return
    env.flush_all()
    cols = ", ".join('"%s"=%%s' % k for k in vals)
    env.cr.execute('UPDATE "%s" SET %s WHERE id IN %%s' % (rec._table, cols),
                   list(vals.values()) + [tuple(rec.ids)])
    env.invalidate_all()


# ---------------------------------------------------------------------------
# Actores
# ---------------------------------------------------------------------------
Users = SU["res.users"].with_context(active_test=False)
Partner = SU["res.partner"]


def user_by_login(login):
    return Users.search([("login", "=", login)], limit=1)


def pick(candidates, ok=lambda u: True, label=""):
    for login in candidates:
        u = user_by_login(login)
        if u and u.active and ok(u):
            return u
    return False


def as_user(user):
    """Entorno del usuario de portal en sudo: lo mismo que hace el controlador."""
    return env(user=user.id, su=True)


def ensure_user(login, vals, extra_partner=None):
    """Busca el usuario; si no existe (base sin demo) lo crea mínimo."""
    u = user_by_login(login)
    if u:
        return u
    portal = SU.ref("base.group_portal")
    pvals = dict(vals, is_company=True, email=login)
    p = Partner.create(pvals)
    if extra_partner:
        extra_partner(p)
    u = Users.create({"name": p.name, "login": login, "partner_id": p.id,
                      "password": "demo123", "group_ids": [(6, 0, [portal.id])]})
    log("  (creado usuario faltante %s)" % login)
    return u


def operational(u):
    return u.partner_id.shrimp_is_operational()


def accredited(u):
    return bool(u.partner_id.verifier_is_accredited)


def seq(prefix, a, b):
    return ["%s.%02d@camaronera.test" % (prefix, i) for i in range(a, b + 1)]


def _approve(p):
    p.sudo().write({"shrimp_account_state": "approved"})
    return p


def crear_actores_traza():
    """Base sin demo: crea todos los actores TRAZA (idempotente)."""
    A = {}
    portal = SU.ref("base.group_portal")

    def usuario(login, vals):
        u = user_by_login(login)
        if u:
            return u
        p = Partner.create(dict(vals, is_company=True, email=login))
        _approve(p)
        return Users.create({"name": p.name, "login": login, "partner_id": p.id,
                             "password": "demo123", "group_ids": [(6, 0, [portal.id])]})

    A["sem"] = usuario("semillero.traza@camaronera.test", {
        "name": "Semillas del Pacífico TRAZA", "shrimp_user_type": "semillero", "vat_or_id": "0991000001001"})
    A["lab"] = usuario("laboratorio.traza@camaronera.test", {
        "name": "Laboratorio Biomarino TRAZA", "shrimp_user_type": "laboratorio", "vat_or_id": "0991000002001",
        "lab_razon_social": "Laboratorio Biomarino TRAZA S.A.", "lab_ubicacion": "Santa Elena"})
    for k, nombre, ruc, ubic in (("camA", "Camaronera Río Chone TRAZA", "0991000003001", "Manabí"),
                                 ("camB", "Ecuacamarón Guayas TRAZA", "0991000004001", "Guayas")):
        A[k] = usuario("%s.traza@camaronera.test" % k.lower(), {
            "name": nombre, "shrimp_user_type": "camaronera", "vat_or_id": ruc,
            "farm_razon_social": nombre + " S.A.", "farm_representante": "Rep",
            "farm_telefono": "04-1234567", "farm_ubicacion": ubic})
    for k, nombre, ruc, planta in (("e1", "AQUAGOLD TRAZA", "0991000005001", "Planta Durán"),
                                   ("e2", "ECUAMARISCO TRAZA", "0991000006001", "Planta Machala"),
                                   ("e3", "Exportadora Manabí TRAZA", "0991000007001", "Planta Manta")):
        A[k] = usuario("%s.traza@camaronera.test" % k, {
            "name": nombre, "shrimp_user_type": "empacadora", "vat_or_id": ruc,
            "emp_razon_social": nombre + " S.A.", "emp_capacidad_lb_dia": 120000,
            "emp_planta_nombre": planta, "emp_aprobacion_sanitaria": "MAP-%s" % ruc[-6:-3]})
    acred = SU.ref("shrimp_verification.cert_acreditacion_verificador")
    for i, (nombre, ruc) in enumerate((("Inspecciones Acuícolas TRAZA", "0991000008001"),
                                       ("Certimar TRAZA", "0991000009001")), start=1):
        v = usuario("ver%s.traza@camaronera.test" % i, {
            "name": nombre, "shrimp_user_type": "verificador", "vat_or_id": ruc,
            "ver_bank_name": "Banco", "ver_bank_account_number": "21005488%s" % i,
            "ver_bank_holder_id": "091234567%s" % i})
        if not v.partner_id.verifier_is_accredited:
            att = adjunto("acred-%s.pdf" % ruc, "res.partner", v.partner_id.id)
            SU["shrimp.user.certificate.line"].create({
                "partner_id": v.partner_id.id, "certificate_id": acred.id, "file_attachment_id": att.id,
                "status": "approved", "certificate_number": "ACR-%s" % ruc,
                "issue_date": d("2026-01-01"), "expiry_date": d("2027-12-31")})
        A["ver%s" % i] = v
        tecs = []
        for j, tnombre in enumerate(("Jorge Villamar" if i == 1 else "Byron Espinoza",
                                     "Karina Salazar" if i == 1 else "Rosa Mendoza")):
            login = "tec%s%s.traza@camaronera.test" % (i, j)
            u = user_by_login(login)
            if not u:
                t = Partner.create({"name": tnombre, "parent_id": v.partner_id.id,
                                    "shrimp_is_field_tech": True, "email": login})
                u = Users.create({"name": tnombre, "login": login, "partner_id": t.id,
                                  "password": "demo123", "group_ids": [(6, 0, [portal.id])]})
            tecs.append(u)
        A["tec%s" % i] = tecs[0]
        if i == 1:
            A["tec1b"] = tecs[1]
    for k, nombre, ruc in (("maq1", "Maquila Acuícola del Pacífico TRAZA", "0991000010001"),
                           ("maq2", "Servicios de Empaque Puerto Bolívar TRAZA", "0991000011001")):
        A[k] = usuario("%s.traza@camaronera.test" % k, {
            "name": nombre, "shrimp_user_type": "maquilador", "vat_or_id": ruc,
            "pack_razon_social": nombre + " S.A.", "pack_ubicacion": "Guayaquil",
            "pack_codigo_establecimiento": "MAQ-%s" % ruc[-6:-3], "pack_capacidad_lb_semana": 400000,
            "pack_lote_minimo_lb": 5000, "pack_en_directorio": True})
    return A


def actores():
    if not user_by_login("semillero.01@camaronera.test"):
        log("  (base sin usuarios de demo: se crean actores TRAZA propios)")
        return crear_actores_traza()
    A = {}
    A["sem"] = pick(seq("semillero", 1, 20)) or ensure_user(
        "semillero.traza@camaronera.test",
        {"name": "Semillero TRAZA", "shrimp_user_type": "semillero", "vat_or_id": "0991000001001"})
    A["lab"] = pick(seq("laboratorio", 1, 20)) or ensure_user(
        "laboratorio.traza@camaronera.test",
        {"name": "Laboratorio TRAZA", "shrimp_user_type": "laboratorio", "vat_or_id": "0991000002001",
         "lab_razon_social": "Laboratorio TRAZA S.A.", "lab_ubicacion": "Santa Elena"})
    cams = [u for u in (user_by_login(l) for l in seq("camaronera", 1, 42)) if u and u.active]
    if len(cams) < 2:
        for i, l in enumerate(("camaronera.trazaa@camaronera.test", "camaronera.trazab@camaronera.test")):
            cams.append(ensure_user(l, {"name": "Camaronera TRAZA %s" % "AB"[i],
                                        "shrimp_user_type": "camaronera",
                                        "vat_or_id": "099100000%d001" % (3 + i),
                                        "farm_razon_social": "Camaronera TRAZA %s" % "AB"[i],
                                        "farm_representante": "Rep", "farm_telefono": "04-1234567",
                                        "farm_ubicacion": "Guayas"}))
    A["camA"], A["camB"] = cams[0], cams[1]
    emps = [u for u in (user_by_login(l) for l in seq("empacadora", 1, 15))
            if u and u.active and operational(u)]
    # E3 (comprador del producto empacado de A): preferimos una "Exportadora".
    e3 = next((u for u in emps if "xportadora" in (u.partner_id.name or "")), None)
    resto = [u for u in emps if u != e3]
    if len(resto) < 2 or not e3:
        raise Exception("Se necesitan 3 empacadoras aprobadas (shrimp_account_state=approved).")
    A["e1"], A["e2"], A["e3"] = resto[0], resto[1], e3
    ver = [u for u in (user_by_login(l) for l in (["verificador.demo@camaronera.test"]
                                                    + seq("verificador", 2, 16)))
           if u and u.active and accredited(u)]

    def tecnico_de(ver_user, exclude=()):
        comp = ver_user.partner_id
        for t in comp.field_tech_ids.filtered("active"):
            tu = Users.search([("partner_id", "=", t.id)], limit=1)
            if tu and tu.active and tu not in exclude:
                return tu
        return False

    con_tec = [(v, tecnico_de(v)) for v in ver]
    con_tec = [(v, t) for v, t in con_tec if t]
    if len(con_tec) < 2:
        raise Exception("Se necesitan 2 verificadoras acreditadas con técnicos activos.")
    A["ver1"], A["tec1"] = con_tec[0]
    A["ver2"], A["tec2"] = con_tec[1]
    A["tec1b"] = tecnico_de(A["ver1"], exclude=(A["tec1"],)) or A["tec1"]
    maqs = [u for u in (user_by_login(l) for l in seq("maquilador", 1, 12))
            if u and u.active and operational(u)]
    if len(maqs) < 2:
        raise Exception("Se necesitan 2 maquiladores aprobados.")
    A["maq1"], A["maq2"] = maqs[0], maqs[1]
    return A


# ---------------------------------------------------------------------------
# Resumen (también se usa en la segunda ejecución)
# ---------------------------------------------------------------------------
def fmt(q):
    return "{:,.2f}".format(q or 0.0).replace(",", "X").replace(".", ",").replace("X", ".")


def resumen():
    Prod = SU["shrimp.product"].with_context(active_test=False)
    prods = Prod.search([("name", "like", MARK)], order="id")
    txs = SU["shrimp.transaction"].search([("product_id", "in", prods.ids)], order="id")
    log("\n================ RESUMEN TRAZA-DEMO ================")
    log("Base: %s   fecha: %s" % (env.cr.dbname, fields.Date.today()))
    log("\n-- Productos")
    for p in prods:
        lots = p.stock_lot_ids
        log("  [%s] %s | vendedor: %s (%s) | inicial %s %s | disponible %s | estado %s | uuid %s"
            % (p.id, p.name, p.seller_partner_id.name, p.seller_partner_id.shrimp_user_type,
               fmt(p.initial_qty), p.uom_id.name, fmt(p.available_qty), p.state, p.uuid_ref))
        if p.origin_pond_id:
            log("       piscina de origen: %s (%s)" % (p.origin_pond_id.display_name,
                                                     p.origin_facility_id.name or ""))
    log("\n-- Transacciones")
    for t in txs:
        v = t.verification_ids[:1]
        log("  %s | %s | %s -> %s | %s %s | %s | estado %s | entrega %s | creada %s"
            % (t.name, t.transaction_type, t.seller_partner_id.name, t.buyer_partner_id.name,
               fmt(t.transaction_qty), t.product_id.uom_id.name, t.product_id.name, t.state,
               t.delivery_date, t.create_date))
        log("       uuid %s  token público %s  factura vendedor %s"
            % (t.uuid_ref, t.trace_token if "trace_token" in t._fields else "-",
               t.seller_invoice_number or "-"))
        if t.result_product_id:
            log("       producto resultado del comprador: %s" % t.result_product_id.name)
        if v:
            log("       verificación %s | %s / técnico %s | estado %s | aceptación %s | rend %.2f%% A/B/C %.1f/%.1f/%.1f"
                % (v.name, v.verifier_partner_id.name, v.technician_partner_id.name, v.state,
                   v.acceptance_state, v.yield_pct, v.yield_class_a_pct, v.yield_class_b_pct,
                   v.yield_class_c_pct))
            if t.dispatch_id:
                dd = t.dispatch_id
                log("       despacho: pesca %s salida %s cita %s llegada %s (%s, %s)"
                    % (dd.harvest_date, dd.farm_departure, dd.eta, dd.actual_arrival,
                       dd.carrier_name, dd.vehicle_plate))
        for m in t.stock_move_ids:
            log("       move %s: %s %s -> %s  padre=%s" % (m.id, fmt(m.qty), m.source_partner_id.name,
                                                       m.dest_partner_id.name, m.parent_move_id.id or "-"))
        charges = t.charge_ids
        for c in charges:
            log("       cobro %s %s %s pagador %s factura %s (%s)" % (
                c.name, c.charge_type, fmt(c.amount), c.payer_partner_id.name,
                c.invoice_id.name or "-", c.state))
    log("\n-- Asignaciones a piscina")
    allocs = SU["shrimp.lot.allocation"].search([("product_id", "in", prods.ids)], order="id")
    for a in allocs:
        log("  %s millares de '%s' (lote %s de %s) -> %s el %s" % (
            fmt(a.allocated_qty), a.product_id.name, a.stock_lot_id.id, a.partner_id.name,
            a.pond_id.display_name, a.allocation_date))
    log("\n-- Órdenes de empaque")
    orders = SU["shrimp.copack.order"].search([("product_id", "in", prods.ids)], order="id")
    for o in orders:
        firmas = ", ".join("%s:%s" % (f.role, f.decision) for f in o.acceptance_ids)
        log("  %s (solicitud %s) | cliente %s | planta %s | acordado %s | recibido %s | empacado %s | cajas %s | dif %s lb (%.2f%%) | estado %s | acta %s [%s] | compra %s | lote %s | lote empacado %s"
            % (o.name, o.request_id.name, o.client_partner_id.name, o.copacker_partner_id.name,
               fmt(o.agreed_qty_lb), fmt(o.received_lb), fmt(o.packed_lb), o.boxes,
               fmt(o.difference_lb), o.difference_pct, o.state, o.acceptance_state, firmas,
               o.transaction_id.name or "-", o.stock_lot_id.id or "-", o.packed_lot_id.id or "-"))
        for c in o.charge_ids:
            log("       cobro %s %s %s pagador %s factura %s (%s)" % (
                c.name, c.charge_type, fmt(c.amount), c.payer_partner_id.name,
                c.invoice_id.name or "-", c.state))
    log("\n-- Movimientos internos (siembra, producción, ajustes, empaque, salida)")
    internos = SU["shrimp.stock.move"].search([("product_id", "in", prods.ids), ("move_type", "!=", "transfer")],
                                              order="date, id")
    for m in internos:
        log("  %s %-11s %-3s %12s | %-35s | %s" % (m.date, m.move_type, m.direction, fmt(m.qty),
                                                  m.source_partner_id.name, m.reason or ""))
    log("\n-- Salidas / exportaciones")
    for e in SU["shrimp.export"].search([("line_ids.product_id", "in", prods.ids)], order="id"):
        log("  %s | %s | %s | %s | DAE %s | cont %s | %s" % (
            e.name, e.partner_id.name, e.date, e.destination_country_id.name, e.dae_number,
            e.container, e.qty_label()))
    log("\n-- Existencias finales por titular (lotes con saldo)")
    lots = SU["shrimp.stock.lot"].search([("product_id", "in", prods.ids)], order="owner_id, id")
    for l in lots:
        log("  %-45s | %-55s | inicial %12s | disponible %12s %s | %s | origen move %s"
            % (l.owner_id.name, l.product_id.name, fmt(l.initial_qty), fmt(l.available_qty),
               l.uom_id.name or "", l.state, l.origin_move_id.id or "-"))
    log("====================================================\n")


# ---------------------------------------------------------------------------
# Escenario
# ---------------------------------------------------------------------------
def adjunto(nombre, model=False, res_id=False):
    return SU["ir.attachment"].create({"name": nombre, "datas": base64.b64encode(PDF_MIN),
                                       "mimetype": "application/pdf",
                                       "res_model": model, "res_id": res_id})


def certificado(user, product, xmlid, numero, emision, vence):
    """El vendedor sube el certificado (entra pendiente) y la plataforma lo aprueba."""
    E = as_user(user)
    att = adjunto("%s.pdf" % numero, "shrimp.product", product.id)
    line = E["shrimp.product.certificate.line"].create({
        "product_id": product.id, "certificate_id": SU.ref(xmlid).id,
        "number": numero, "issue_date": emision, "expiry_date": vence,
        "attachment_id": att.id})
    SU["shrimp.product.certificate.line"].browse(line.id).action_approve()
    return line


def instalacion(partner, nombre, code, ftype, city, province):
    Fac = SU["shrimp.partner.facility"]
    f = Fac.search([("partner_id", "=", partner.id), ("code", "=", code)], limit=1)
    return f or Fac.create({"partner_id": partner.id, "name": nombre, "code": code,
                            "facility_type": ftype, "city": city, "province": province})


def piscina(partner, facility, nombre, code, ha):
    Pond = SU["shrimp.partner.pond"]
    p = Pond.search([("partner_id", "=", partner.id), ("code", "=", code)], limit=1)
    return p or Pond.create({"partner_id": partner.id, "facility_id": facility.id,
                             "name": nombre, "code": code, "pond_type": "earth",
                             "capacity_mode": "dimensions", "length_m": 500.0,
                             "width_m": ha * 10000.0 / 500.0, "depth_m": 1.2,
                             "location": facility.city})


def compra_directa(buyer_user, product, qty, fecha):
    """POST /marketplace/buy/<ref>/confirm"""
    E = as_user(buyer_user)
    prod = E["shrimp.product"].browse(product.id)
    with env.cr.savepoint():
        res = prod.execute_purchase_flow(buyer_user.partner_id, qty)
    tx = res["transaction"]
    dt_ = dtl(fecha)
    sqlset(tx, create_date=dt_)
    sqlset(tx.stock_move_ids, date=dt_, create_date=dt_)
    return tx


def recibir(buyer_user, tx, fecha):
    """POST /marketplace/purchases/<ref>/receive"""
    E = as_user(buyer_user)
    t = E["shrimp.transaction"].browse(tx.id)
    t.action_receive()
    lots = SU["shrimp.stock.lot"].search([("origin_move_id", "in", tx.stock_move_ids.ids)])
    sqlset(lots, create_date=dtl(fecha))
    sqlset(tx, received_date=dtl(fecha))
    if tx.result_product_id:
        sqlset(tx.result_product_id, create_date=dtl(fecha))
    return lots


def factura_vendedor(seller_user, tx, numero, fecha):
    """POST /marketplace/sales/<ref>/invoice"""
    E = as_user(seller_user)
    t = E["shrimp.transaction"].browse(tx.id)
    att = adjunto("Factura-%s.pdf" % numero, "shrimp.transaction", tx.id)
    t.action_register_seller_invoice({"number": numero, "date": d(fecha),
                                      "attachment_id": att.id},
                                     actor=seller_user.partner_id)
    sqlset(tx, seller_invoice_registered_at=dtl(fecha + " 17:00"))


def compra_verificada(A, buyer_user, seller_user, ver_user, tec_user, product, qty, plan, informe):
    """Compra de camarón adulto con verificación en campo, completa:
    POST /marketplace/buy/<ref>/verify -> asignar técnico -> despacho del
    vendedor -> inicio en campo -> llegada a planta -> informe -> veredicto ->
    firmas de comprador y vendedor -> (compra confirmada) -> recepción."""
    buyer, seller = buyer_user.partner_id, seller_user.partner_id
    verifier, tech = ver_user.partner_id, tec_user.partner_id
    # 1) Comprador inicia la compra con verificación (controlador buy_with_verification)
    Eb = as_user(buyer_user)
    prod = Eb["shrimp.product"].browse(product.id)
    fee = Eb["shrimp.verification.fee"].compute(qty)
    with env.cr.savepoint():
        res = prod.start_verified_purchase(buyer, qty, verifier, fee=fee)
    tx, v = res["transaction"], res["verification"]
    sqlset(tx, create_date=dtl(plan["compra"]))
    sqlset(v, create_date=dtl(plan["compra"]), assigned_date=dtl(plan["compra"]))
    # 2) Admin de la verificadora asigna el técnico
    Ev = as_user(ver_user)
    Ev["shrimp.verification"].browse(v.id).action_assign_technician(tech)
    # 3) Vendedor registra el despacho (cita en planta)
    Es = as_user(seller_user)
    disp = Es["shrimp.transaction"].browse(tx.id)._ensure_dispatch()
    disp.registrar_plan(seller, {
        "harvest_date": d(plan["pesca"]),
        "farm_departure": dtl(plan["salida"]),
        "eta": dtl(plan["cita"]),
        "carrier_name": plan["transporte"], "vehicle_plate": plan["placa"],
        "carrier_phone": "0991234567",
        "notes": plan.get("notas") or False,
    })
    # 4) Técnico inicia el trabajo de campo y estampa la llegada real
    Et = as_user(tec_user)
    vt = Et["shrimp.verification"].browse(v.id)
    vt.action_start_field()
    sqlset(v, field_start_date=dtl(plan["llegada"]))
    Et["shrimp.dispatch"].browse(disp.id).registrar_llegada(tech, dtl(plan["llegada"]))
    # 5) Informe de campo (PATCH /verifications/{id}/report / formulario guardar)
    lines = informe.pop("lines")
    counts = informe.pop("counts")
    vt.write(dict(informe, harvest_date=d(plan["pesca"]), process_date=d(plan["proceso"])))
    Line = Et["shrimp.verification.line"]
    for i, (cls, size, lb) in enumerate(lines, start=1):
        Line.create({"verification_id": v.id, "quality_class": cls, "size_code": size,
                     "weight_lb": lb, "sequence": i * 10})
    Count = Et["shrimp.verification.count"]
    for i, c in enumerate(counts, start=1):
        Count.create({"verification_id": v.id, "value": c, "sequence": i * 10})
    # 6) Veredicto del técnico (controlador verification_verdict)
    with env.cr.savepoint():
        if vt.state == "in_field":
            vt.action_mark_done()
        vt._close("approved", notes=plan["veredicto"])
    sqlset(v, verified_date=dtl(plan["veredicto_dt"]))
    # 7) Firmas de comprador y vendedor (POST /marketplace/verifications/<ref>/accept)
    for user, role in ((buyer_user, "buyer"), (seller_user, "seller")):
        Eu = as_user(user)
        post = Eu["shrimp.verification"].browse(v.id).acceptance_ids.filtered(
            lambda a: a.role == role)
        post.sudo().action_accept(reason="Conforme con el informe %s." % v.name,
                                  actor=user.partner_id)
        sqlset(post, decided_at=dtl(plan["firmas"]))
    tx.invalidate_recordset()
    if tx.state != "confirmed":
        raise Exception("La compra %s no quedó confirmada (estado %s)" % (tx.name, tx.state))
    sqlset(tx.stock_move_ids, date=dtl(plan["firmas"]), create_date=dtl(plan["firmas"]))
    # 8) El comprador recibe (sube su inventario) y el lote se ajusta al peso
    #    verificado en planta (ajuste + basura): se fechan en la recepción.
    lots = recibir(buyer_user, tx, plan["recepcion"])
    sqlset(SU["shrimp.stock.move"].search([("lot_id", "in", lots.ids), ("move_type", "!=", "transfer")]),
           date=dtl(plan["recepcion"]), create_date=dtl(plan["recepcion"]))
    return tx, v


def informe_adulto(enviado, planta, basura, lineas, ppm, sabor, gramos):
    return {
        "plant_name": False, "weight_sent_lb": enviado, "weight_plant_lb": planta,
        "trash_lb": basura, "presentation": "entero",
        "metabisulfite_ppm": ppm, "metabisulfite_limit_ppm": 100.0,
        "metabisulfite_notes": "Muestra compuesta de 3 gavetas.",
        "taste_result": sabor, "taste_notes": "Sin sabor a tierra ni a choclo.",
        "grams_farm": gramos[0], "grams_plant_1": gramos[1], "grams_plant_2": gramos[2],
        "incident_notes": False, "lines": lineas, "counts": [48.0, 49.5, 47.8],
    }


def copack(cliente_user, maq_user, origen, libras, plan, salida_texto):
    """Solicitud dirigida -> oferta -> adjudicación -> recepción -> empaque ->
    acta firmada por los dos -> cierre."""
    cli, maq = cliente_user.partner_id, maq_user.partner_id
    Ec = as_user(cliente_user)
    Req = Ec["shrimp.copack.request"]
    vals = {
        "client_partner_id": cli.id, "quantity_lb": libras, "presentation": "entero",
        "needed_from": d(plan["desde"]), "needed_to": d(plan["hasta"]),
        "copacker_partner_id": maq.id,
        "supplies_notes": "Cajas master 20 lb, fundas, etiquetas y metabisulfito: los pone el cliente.",
        "notes": plan["notas"],
    }
    if plan.get("size_grade"):
        vals["size_grade_id"] = plan["size_grade"].id
    with env.cr.savepoint():
        vals.update(Req._shrimp_resolver_origen(cli, origen))
        sol = Req.create(vals)
        sol.action_publish()
    sqlset(sol, create_date=dtl(plan["solicitud"]))
    # Oferta del maquilador (POST /copacker/requests/<ref>/offer)
    Em = as_user(maq_user)
    oferta = Em["shrimp.copack.offer"].create({
        "request_id": sol.id, "copacker_partner_id": maq.id,
        "rate_per_lb": plan["tarifa"], "capacity_lb": libras,
        "available_from": d(plan["desde"]), "available_to": d(plan["hasta"]),
        "notes": "Túnel IQF y cámara a -20 °C. Master de 20 lb.",
    })
    sqlset(oferta, create_date=dtl(plan["oferta"]))
    # El cliente acepta (POST /marketplace/copacking/offers/<ref>/accept)
    orden = Ec["shrimp.copack.offer"].browse(oferta.id).action_accept(actor=cli)
    sqlset(orden, create_date=dtl(plan["oferta"]))
    # Recepción y empaque (maquilador)
    o = Em["shrimp.copack.order"].browse(orden.id)
    o.write({"received_lb": plan["recibidas"], "supplies_received": True})
    o.action_register_reception()
    o.write({"packed_lb": plan["empacadas"], "boxes": plan["cajas"],
             "packed_presentation": "entero",
             "packed_presentation_note": "Entero congelado IQF, master 20 lb (10 x 2 lb)"})
    o.action_register_packing()
    sqlset(orden, received_date=dtl(plan["recepcion"]), packed_date=dtl(plan["empaque"]))
    # Acta: firman las dos partes (POST /marketplace/copacking/orders/<ref>/sign)
    for user in (maq_user, cliente_user):
        Eu = as_user(user)
        firma = Eu["shrimp.copack.order"].browse(orden.id).acceptance_ids.filtered(
            lambda f: f.partner_id == user.partner_id and f.active and f.decision == "pending")
        firma.action_accept(actor=user.partner_id)
        sqlset(firma, decided_at=dtl(plan["acta"]))
    # El acta firmada mueve el inventario: se fecha en el empaque.
    orden.invalidate_recordset()
    sqlset(orden.packing_move_ids, date=dtl(plan["empaque"]), create_date=dtl(plan["empaque"]))
    sqlset(orden.packed_lot_id, create_date=dtl(plan["empaque"]))
    # Cierre (POST /marketplace/copacking/orders/<ref>/close)
    Ec["shrimp.copack.order"].browse(orden.id).action_close()
    if salida_texto:
        Ec["shrimp.copack.order"].browse(orden.id).message_post(body=salida_texto)
    orden.invalidate_recordset()
    return sol, orden


def escenario():
    A = actores()
    sem, lab, camA, camB = A["sem"], A["lab"], A["camA"], A["camB"]
    e1, e2, e3 = A["e1"], A["e2"], A["e3"]
    log("Actores:")
    for k in ("sem", "lab", "camA", "camB", "e1", "e2", "e3", "ver1", "tec1", "tec1b",
              "ver2", "tec2", "maq1", "maq2"):
        u = A[k]
        log("  %-5s %-40s %s" % (k, u.login, u.partner_id.commercial_partner_id.name))

    vannamei = SU.ref("shrimp_marketplace.shrimp_species_vannamei")
    genetica = SU.ref("shrimp_marketplace.shrimp_genetics_spr")
    millar = SU.ref("shrimp_marketplace.uom_millar")
    libra = SU.ref("shrimp_marketplace.uom_libra")
    st = lambda x: SU.ref("shrimp_marketplace.shrimp_stage_%s" % x)

    # --- Instalaciones y piscinas -------------------------------------------------
    f_sem = instalacion(sem.partner_id, "Maduración TRAZA", "TRZ-SEM", "hatchery", "Mar Bravo", "Santa Elena")
    f_lab = instalacion(lab.partner_id, "Larvicultura TRAZA", "TRZ-LAB", "laboratory", "San Pablo", "Santa Elena")
    f_A = instalacion(camA.partner_id, "Finca TRAZA A", "TRZ-FA", "farm", "Chone", "Manabí")
    f_B = instalacion(camB.partner_id, "Finca TRAZA B", "TRZ-FB", "farm", "Balao", "Guayas")
    pA1 = piscina(camA.partner_id, f_A, "Piscina A-01", "TRZ-A01", 8.0)
    pA2 = piscina(camA.partner_id, f_A, "Piscinas A-02..A-12", "TRZ-A02", 110.0)
    pB1 = piscina(camB.partner_id, f_B, "Piscina B-01", "TRZ-B01", 4.0)
    pB2 = piscina(camB.partner_id, f_B, "Piscinas B-02..B-06", "TRZ-B02", 36.0)

    # --- 1. Semillero publica 100.000 millares de nauplio ---------------------------
    Es = as_user(sem)
    p_sem = Es["shrimp.product"].create({
        "name": P_SEM, "seller_partner_id": sem.partner_id.id, "seller_role": "semillero",
        "species_id": vannamei.id, "stage_id": st("nauplio").id, "genetics_line_id": genetica.id,
        "avg_size_mg": 0.01, "survival_rate": 95.0, "health_status": "PCR negativo WSSV/IHHNV/AHPND.",
        "initial_qty": 100000.0, "uom_id": millar.id, "price": 0.45, "location": "Mar Bravo, Santa Elena",
        "batch_code": "NAU-2603-01", "production_date": d("2026-03-01"),
        "origin_facility_id": f_sem.id,
        "expected_delivery_date": d("2026-03-05"), "state": "draft", "active": True,
        "traceability_notes": "Desove de reproductores SPR, sala de maduración 2.",
    })
    certificado(sem, p_sem, "shrimp_user_registry.shrimp_certificate_agrocalidad",
                "AGRO-NAU-2026-0331", d("2026-02-20"), d("2027-02-20"))
    Es["shrimp.product"].browse(p_sem.id).action_publish()
    sqlset(p_sem, create_date=dtl("2026-03-02 08:00"), published_date=dtl("2026-03-02 09:00"))
    sqlset(p_sem.stock_lot_ids, create_date=dtl("2026-03-02 08:00"))
    sqlset(p_sem.evolution_ids, date=dtl("2026-03-02 09:00"))
    log("1. Semillero publicó %s: 100.000 millares" % p_sem.name)

    # --- 2. Laboratorio compra 90.000 millares ---------------------------------------
    tx1 = compra_directa(lab, p_sem, 90000.0, "2026-03-04 10:00")
    factura_vendedor(sem, tx1, "001-001-000000311", "2026-03-04")
    recibir(lab, tx1, "2026-03-05 07:30")
    p_lab = tx1.result_product_id
    log("2. %s: laboratorio compró 90.000 millares -> producto resultado %s" % (tx1.name, p_lab.id))

    # Larvicultura: el laboratorio actualiza su lote (edición del producto) hasta PL12.
    El = as_user(lab)
    pl = El["shrimp.product"].browse(p_lab.id)
    pl.write({"name": P_LAB, "batch_code": "LAB-PL12-2603-07", "origin_facility_id": f_lab.id,
              "location": "San Pablo, Santa Elena", "price": 2.10,
              "production_date": d("2026-03-23"), "expected_delivery_date": d("2026-03-25"),
              "traceability_notes": "Larvicultura de los nauplios %s (%s)." % (tx1.name, p_sem.batch_code)})
    etapas = [("zoea", 0.05, 88.0, "2026-03-07 08:00"), ("mysis", 0.3, 82.0, "2026-03-10 08:00"),
              ("pl5", 0.9, 76.0, "2026-03-15 08:00"), ("pl12", 2.6, 72.0, "2026-03-22 08:00")]
    for code, mg, surv, when in etapas:
        antes = set(pl.evolution_ids.ids)
        pl.write({"stage_id": st(code).id, "avg_size_mg": mg, "survival_rate": surv,
                  "health_status": "Sin signos clínicos; PCR negativo (%s)." % code.upper()})
        nuevas = pl.evolution_ids.filtered(lambda e: e.id not in antes)
        sqlset(nuevas, date=dtl(when))
    # Producción real: de 90.000 millares de nauplio salen 64.800 de PL12 (72 %).
    prod_moves = pl.action_declare_production(
        64800.0, reason="Larvicultura LAB-PL12-2603-07: supervivencia 72 % de nauplio a PL12 "
                        "(90.000 -> 64.800 millares)", actor=lab.partner_id, date=dtl("2026-03-22 18:00"))
    pl.action_publish()
    sqlset(p_lab, published_date=dtl("2026-03-23 09:00"))
    log("   Laboratorio declaró la producción real (%s) y publicó %s (PL12): %s millares"
        % (", ".join("%s %s" % (m.move_type, fmt(m.qty)) for m in prod_moves), P_LAB,
           fmt(p_lab.available_qty)))

    # --- 3. Camaronera A compra 60.000 millares; (adaptación) B compra 20.000 -------
    tx2 = compra_directa(camA, p_lab, 48000.0, "2026-03-24 09:00")
    factura_vendedor(lab, tx2, "001-001-000001204", "2026-03-24")
    lotA = recibir(camA, tx2, "2026-03-25 06:00")
    tx3 = compra_directa(camB, p_lab, 16000.0, "2026-03-24 15:00")
    factura_vendedor(lab, tx3, "001-001-000001205", "2026-03-24")
    lotB = recibir(camB, tx3, "2026-03-25 08:00")
    log("3. %s: A compró 48.000 millares; %s: B compró 16.000 millares" % (tx2.name, tx3.name))

    # Siembra (POST /marketplace/my-facilities/allocate)
    Alloc = as_user(camA)["shrimp.lot.allocation"]
    al1 = Alloc.create({"stock_lot_id": lotA.id, "pond_id": pA1.id, "allocated_qty": 4000.0,
                        "allocation_date": d("2026-03-25"),
                        "notes": "Siembra directa PL12, 50 PL/m2 (%s)." % tx2.name})
    al2 = Alloc.create({"stock_lot_id": lotA.id, "pond_id": pA2.id, "allocated_qty": 44000.0,
                        "allocation_date": d("2026-03-25"),
                        "notes": "Resto del lote en precriaderos/piscinas A-02..A-12."})
    AllocB = as_user(camB)["shrimp.lot.allocation"]
    bl1 = AllocB.create({"stock_lot_id": lotB.id, "pond_id": pB1.id, "allocated_qty": 2000.0,
                         "allocation_date": d("2026-03-25"),
                         "notes": "Siembra directa PL12, 50 PL/m2 (%s)." % tx3.name})
    bl2 = AllocB.create({"stock_lot_id": lotB.id, "pond_id": pB2.id, "allocated_qty": 14000.0,
                         "allocation_date": d("2026-03-25"),
                         "notes": "Resto del lote en B-02..B-06."})
    log("   Siembras: lote de A %s, lote de B %s (consumidos por la siembra)"
        % (fmt(lotA.available_qty), fmt(lotB.available_qty)))

    # --- 4. Cosecha de A-01: producto adulto 94.000 lb --------------------------------
    Ea = as_user(camA)
    p_A = Ea["shrimp.product"].create({
        "name": P_ADULT_A, "seller_partner_id": camA.partner_id.id, "seller_role": "camaronera",
        "species_id": vannamei.id, "stage_id": st("engorde").id, "genetics_line_id": genetica.id,
        "avg_size_mg": 20500.0, "survival_rate": 52.0,
        "health_status": "Engorde 117 días. Sin mortalidades anómalas.",
        "initial_qty": 94000.0, "uom_id": libra.id, "price": 2.35,
        "presentation": "entero", "size_grade_id": SU.ref("shrimp_marketplace.size_entero_4050").id,
        "location": "Chone, Manabí", "batch_code": "A01-2607",
        "origin_facility_id": f_A.id, "origin_pond_id": pA1.id,
        "production_date": d("2026-07-20"), "expected_delivery_date": d("2026-07-21"),
        "traceability_notes": "Siembra 25/03/2026: 4.000 millares PL12 del lote %s (compra %s). "
                              "Cosecha 20/07/2026." % (P_LAB, tx2.name),
        "state": "draft", "active": True,
    })
    certificado(camA, p_A, "shrimp_user_registry.shrimp_certificate_bap",
                "BAP-FARM-A-2026", d("2026-01-15"), d("2027-01-15"))
    Ea["shrimp.product"].browse(p_A.id).action_publish()
    sqlset(p_A, create_date=dtl("2026-07-14 10:00"), published_date=dtl("2026-07-14 10:30"))
    sqlset(p_A.stock_lot_ids, create_date=dtl("2026-07-14 10:00"))
    sqlset(p_A.evolution_ids, date=dtl("2026-07-14 10:30"))
    log("4. A publicó %s: 94.000 lb (piscina A-01)" % P_ADULT_A)

    # --- 5. E1 compra 40.000 lb con verificación ------------------------------------
    tx4, v4 = compra_verificada(A, e1, camA, A["ver1"], A["tec1"], p_A, 40000.0, {
        "compra": "2026-07-15 09:00", "pesca": "2026-07-20", "salida": "2026-07-20 22:00",
        "cita": "2026-07-21 04:00", "llegada": "2026-07-21 04:20", "proceso": "2026-07-21",
        "transporte": "Transportes Chone Frío", "placa": "MBA-1234",
        "veredicto": "Aprobado: peso, talla 40/50, metabisulfito y sabor conformes.",
        "veredicto_dt": "2026-07-21 11:00", "firmas": "2026-07-21 13:00",
        "recepcion": "2026-07-21 14:00",
    }, informe_adulto(40000.0, 39640.0, 120.0,
                      [("a", "40/50", 31200.0), ("b", "50/60", 5400.0), ("c", "40/50", 1300.0)],
                      45.0, "good", (20.5, 20.2, 20.6)))
    factura_vendedor(camA, tx4, "001-001-000000876", "2026-07-21")
    log("5. %s: E1 compró 40.000 lb (verificación %s)" % (tx4.name, v4.name))

    # --- 6. A manda el resto (54.000 lb) a co-packing -------------------------------
    solA, ordA = copack(camA, A["maq1"], "p:%s" % p_A.uuid_ref, 54000.0, {
        "solicitud": "2026-07-16 08:00", "oferta": "2026-07-17 10:00",
        "desde": "2026-07-21", "hasta": "2026-07-24", "tarifa": 0.18,
        "recibidas": 54000.0, "empacadas": 53760.0, "cajas": 2688,
        "recepcion": "2026-07-21 06:30", "empaque": "2026-07-22 18:00", "acta": "2026-07-23 10:00",
        "size_grade": SU.ref("shrimp_marketplace.size_entero_4050"),
        "notas": "Resto de la cosecha A-01 (%s). Se empaca para venderlo empacado." % p_A.batch_code,
    }, False)
    log("6. %s / %s: co-packing de A, %s lb recibidas -> %s lb empacadas"
        % (solA.name, ordA.name, fmt(ordA.received_lb), fmt(ordA.packed_lb)))

    # --- 7. B: cosecha B-01 y venta a E2 ------------------------------------------------
    Eb = as_user(camB)
    p_B = Eb["shrimp.product"].create({
        "name": P_ADULT_B, "seller_partner_id": camB.partner_id.id, "seller_role": "camaronera",
        "species_id": vannamei.id, "stage_id": st("engorde").id, "genetics_line_id": genetica.id,
        "avg_size_mg": 17800.0, "survival_rate": 56.0,
        "health_status": "Engorde 124 días.", "initial_qty": 48000.0, "uom_id": libra.id,
        "price": 2.15, "presentation": "entero",
        "size_grade_id": SU.ref("shrimp_marketplace.size_entero_5060").id,
        "location": "Balao, Guayas", "batch_code": "B01-2607",
        "origin_facility_id": f_B.id, "origin_pond_id": pB1.id,
        "production_date": d("2026-07-28"), "expected_delivery_date": d("2026-07-29"),
        "traceability_notes": "Siembra 25/03/2026: 2.000 millares PL12 del lote %s (compra %s). "
                              "Cosecha 28/07/2026." % (P_LAB, tx3.name),
        "state": "draft", "active": True,
    })
    Eb["shrimp.product"].browse(p_B.id).action_publish()
    sqlset(p_B, create_date=dtl("2026-07-22 09:00"), published_date=dtl("2026-07-22 09:30"))
    sqlset(p_B.stock_lot_ids, create_date=dtl("2026-07-22 09:00"))
    sqlset(p_B.evolution_ids, date=dtl("2026-07-22 09:30"))
    tx5, v5 = compra_verificada(A, e2, camB, A["ver2"], A["tec2"], p_B, 48000.0, {
        "compra": "2026-07-23 11:00", "pesca": "2026-07-28", "salida": "2026-07-28 23:00",
        "cita": "2026-07-29 05:00", "llegada": "2026-07-29 05:10", "proceso": "2026-07-29",
        "transporte": "Frío Balao Express", "placa": "GSA-5521",
        "veredicto": "Aprobado: talla 50/60 dominante, metabisulfito y sabor conformes.",
        "veredicto_dt": "2026-07-29 12:00", "firmas": "2026-07-29 15:00",
        "recepcion": "2026-07-29 16:00",
    }, informe_adulto(48000.0, 47900.0, 150.0,
                      [("a", "50/60", 40100.0), ("b", "60/70", 4700.0), ("c", "50/60", 1000.0)],
                      52.0, "good", (17.8, 17.5, 17.9)))
    factura_vendedor(camB, tx5, "001-001-000000412", "2026-07-29")
    log("7. %s: E2 compró 48.000 lb a B (verificación %s)" % (tx5.name, v5.name))

    # --- 8. A vende su producto empacado a E3 (exportadora) ------------------------
    tx6, v6 = compra_verificada(A, e3, camA, A["ver1"], A["tec1b"], p_A, 53760.0, {
        "compra": "2026-07-23 12:00", "pesca": "2026-07-20", "salida": "2026-07-24 07:00",
        "cita": "2026-07-24 11:00", "llegada": "2026-07-24 11:15", "proceso": "2026-07-22",
        "transporte": "Contenedor reefer desde %s" % A["maq1"].partner_id.name[:40],
        "placa": "GBC-8890",
        "notas": "Producto ya empacado en %s (orden %s): sale de la cámara del maquilador."
                 % (A["maq1"].partner_id.name, ordA.name),
        "veredicto": "Aprobado: producto empacado (orden %s), %s cajas master 20 lb verificadas."
                     % (ordA.name, ordA.boxes),
        "veredicto_dt": "2026-07-24 15:00", "firmas": "2026-07-24 17:00",
        "recepcion": "2026-07-24 18:00",
    }, informe_adulto(53760.0, 53760.0, 0.0,
                      [("a", "40/50", 45900.0), ("b", "50/60", 6600.0), ("c", "40/50", 1260.0)],
                      40.0, "excellent", (20.5, 20.4, 20.5)))
    factura_vendedor(camA, tx6, "001-001-000000901", "2026-07-24")
    log("8. %s: A vendió a E3 %s lb empacadas (verificación %s)" % (tx6.name, fmt(53760.0), v6.name))

    # --- 9. E2 usa co-packing con su compra y luego vende -----------------------------
    solE2, ordE2 = copack(e2, A["maq2"], "t:%s" % tx5.uuid_ref, 47750.0, {
        "solicitud": "2026-07-25 08:00", "oferta": "2026-07-26 09:00",
        "desde": "2026-07-29", "hasta": "2026-08-02", "tarifa": 0.16,
        "recibidas": 47750.0, "empacadas": 47600.0, "cajas": 2380,
        "recepcion": "2026-07-29 17:00", "empaque": "2026-07-31 19:00", "acta": "2026-08-01 10:00",
        "size_grade": SU.ref("shrimp_marketplace.size_entero_5060"),
        "notas": "Camarón comprado en %s (verificación %s), neto de basura." % (tx5.name, v5.name),
    }, False)
    log("9. %s / %s: co-packing de E2 %s lb -> %s lb; venta registrada fuera de la plataforma"
        % (solE2.name, ordE2.name, fmt(ordE2.received_lb), fmt(ordE2.packed_lb)))

    # --- 10. Salidas / exportaciones (último eslabón) -------------------------------
    def salida(user, tx, lote, qty, vals):
        """POST /marketplace/exports/register"""
        Ex = as_user(user)["shrimp.export"]
        att = adjunto("DAE-%s.pdf" % vals["dae_number"], "res.partner", user.partner_id.id)
        vals = dict(vals, attachment_ids=[(6, 0, [att.id])])
        exp = Ex.shrimp_register(user.partner_id, vals, [(lote, qty)])
        return exp

    def lote_de(tx, owner):
        return SU["shrimp.stock.lot"].search([("origin_move_id", "in", tx.stock_move_ids.ids),
                                              ("owner_id", "=", owner.id)], limit=1)

    lote_e1 = lote_de(tx4, e1.partner_id)
    exp1 = salida(e1, tx4, lote_e1, lote_e1.available_qty, {
        "date": d("2026-07-28"), "destination_buyer": "Importador de Valencia (España)",
        "destination_country_id": SU.ref("base.es").id, "destination_place": "Valencia",
        "dae_number": "028-2026-40-00010877", "invoice_number": "001-003-000010877",
        "container": "TGHU5566778", "boxes": 1976, "price_unit": 3.95, "confidential": True})
    packed_e2 = ordE2.packed_lot_id
    exp2 = salida(e2, tx5, packed_e2, packed_e2.available_qty, {
        "date": d("2026-08-05"), "destination_buyer": "Importador de Shanghái",
        "destination_country_id": SU.ref("base.cn").id, "destination_place": "Shanghái",
        "dae_number": "028-2026-40-00004512", "invoice_number": "001-002-000004512",
        "container": "MSKU7712345", "boxes": 2380, "price_unit": 3.80, "confidential": False})
    lote_e3 = lote_de(tx6, e3.partner_id)
    exp3 = salida(e3, tx6, lote_e3, lote_e3.available_qty, {
        "date": d("2026-07-30"), "destination_buyer": "Importador de Miami (EE. UU.)",
        "destination_country_id": SU.ref("base.us").id, "destination_place": "Miami",
        "dae_number": "028-2026-40-00022001", "invoice_number": "001-001-000022001",
        "container": "SEGU4410022", "boxes": 2688, "price_unit": 4.10, "confidential": True})
    for exp in (exp1, exp2, exp3):
        noon = SU["shrimp.transaction"].shrimp_noon_utc(exp.date, exp.partner_id)
        sqlset(exp.move_ids, date=noon, create_date=noon)
    log("10. Salidas: %s %s (E1 -> España), %s %s (E2 -> China), %s %s (E3 -> EE. UU.)"
        % (exp1.name, exp1.qty_label(), exp2.name, exp2.qty_label(), exp3.name, exp3.qty_label()))
    return True


# ---------------------------------------------------------------------------
existe = SU["shrimp.product"].with_context(active_test=False).search(
    [("name", "=", P_SEM)], limit=1)
if existe:
    log("El escenario TRAZA-DEMO ya existe (producto %s): no se crea nada." % existe.id)
else:
    log("Creando el escenario TRAZA-DEMO en %s ..." % env.cr.dbname)
    escenario()
    env.cr.commit()
    log("COMMIT OK")
resumen()
