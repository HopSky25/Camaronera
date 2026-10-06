# -*- coding: utf-8 -*-
"""shrimp_verification: empresas verificadoras, técnicos, acreditaciones,
cuentas de acceso y compras de larva con verificación en campo.

También define ``compra_verificada()``, el motor que usa shrimp_packer para
el camarón adulto. El estado de la compra SIEMPRE se deriva del estado de la
verificación y de las posturas de las partes, como en el código:

  verificación recibida/asignada/en campo/informe completo -> compra
      'pending_verification' (reserva stock, no lo consume)
  aprobada (u obs.) y ronda de aceptación abierta -> 'pending_acceptance'
  aprobada y las DOS partes aceptaron -> 'confirmed' (lotes consumidos,
      movimiento, comisión) o 'done' (además, recibido: lote del comprador)
  aprobada pero una parte rechazó (trato caído) -> 'cancel'
  rechazada o cancelada -> 'cancel' (si el producto no pasó, el honorario lo
      paga el vendedor)
"""
from .comun import (MP, RNG, SV, Archivo, B64, CIUDADES, D, DOMINIO_LOGIN, DT, cobro_demo,
                    M2M, PASSWORD_DEMO, R, celular, geo, pdf_minimo, persona,
                    r2, ruc_sociedad, slug, telefono_fijo)
from .marketplace import (IMGS, SANIDAD_OK, TALLAS_COLA, TALLAS_ENTERO,
                          TARIFA_CENTAVOS, certs_producto, disponible,
                          escribir_resenas, evoluciones, lote_vals, prod_vals,
                          ref_p)

EMPRESAS_VIEJAS = ["pacifico", "certimar", "aquacontrol", "verimar", "guayas", "bioaudit"]
TECNICOS_VIEJOS = {"pacifico": 4, "certimar": 6, "aquacontrol": 5, "verimar": 6,
                   "guayas": 5, "bioaudit": 6}
EMPRESAS_NUEVAS = [
    ("Verificaciones Técnicas del Golfo S.A.", "Guayaquil"),
    ("Control Acuícola El Oro Cía. Ltda.", "Machala"),
    ("Inspectores Marinos de Manabí S.A.", "Manta"),
    ("Calidad y Campo Acuícola S.A.", "Durán"),
    ("Peritajes Camaroneros del Sur S.A.", "Santa Rosa"),
    ("Laboratorio de Verificación Península S.A.", "La Libertad"),
    ("Auditores de Cosecha Ecuador Cía. Ltda.", "Guayaquil"),
    ("Verificadora Estuario Cía. Ltda.", "Naranjal"),
    ("Técnicos de Muelle Asociados S.A.", "Posorja"),
    ("Inspección Acuícola Esmeraldas S.A.", "Atacames"),
]
# índices (0-based) de empresas nuevas SIN acreditación vigente
SIN_ACREDITAR = {7: "pending", 8: "expired"}
CRITERIOS_NUEVOS = ["Textura firme", "Sin sabor a tierra ni a fango", "Sin olor amoniacal",
                    "Dulzor característico", "Sin melanosis (manchas negras)",
                    "Caparazón sin flacidez", "Sin cabeza roja", "Branquias limpias",
                    "Músculo translúcido, sin opacidad", "Sin arena en el intestino"]
CARGOS_NUEVOS = ["Técnico de muelle", "Jefe de cuadrilla", "Biólogo de campo", "Auxiliar de muestreo"]
TRANSPORTISTAS = ["Transportes Frigoríficos del Litoral", "Trans Camarón Express",
                  "Logística Acuícola Andrade", "Frío Ruta Costa", "Transportes Hnos. Yagual"]
PLANTAS_TXT = ["Planta Durán km 6", "Planta Machala Puerto Bolívar", "Planta Posorja",
               "Planta Manta - Jaramijó", "Planta Taura km 22"]


def placa():
    letras = "ABCDEFGHJKLMNPRSTUVWXYZ"
    return f"{RNG.choice('GOMSE')}{RNG.choice(letras)}{RNG.choice(letras)}-{RNG.randint(1000, 9999)}"


def usuario_portal(arch, ux, partner_ref, login):
    arch.rec(ux, "res.users", {
        "partner_id": R(partner_ref), "login": login, "password": PASSWORD_DEMO,
        "company_id": R("base.main_company"), "company_ids": M2M(["base.main_company"]),
        "group_ids": M2M(["base.group_portal"]), "tz": "America/Guayaquil"})


# ============================================================================
# Motor de compra verificada (larva y adulto)
# ============================================================================
ESCENARIOS = [  # (clave, peso compras viejas, peso compras recientes)
    ("received", 0, 9), ("assigned", 0, 10), ("in_field", 0, 9), ("done", 0, 8),
    ("waiting", 0, 16), ("closed_confirmed", 6, 0), ("closed_done", 62, 0),
    ("broken", 9, 0), ("rejected", 14, 0), ("cancelled", 9, 0),
]
EN_CURSO = ("received", "assigned", "in_field", "done", "waiting")


def elegir_escenario(off):
    reciente = off > -8 or RNG.random() < 0.22
    return RNG.choices([e[0] for e in ESCENARIOS],
                       [e[2] if reciente else e[1] for e in ESCENARIOS])[0]


def calendario(esc, off):
    """(compra, inicio en campo, veredicto, entrega) en días relativos."""
    if esc == "received":
        off = -RNG.randint(0, 2)
        return off, None, None, off + 4
    if esc == "assigned":
        off = -RNG.randint(0, 3)
        return off, RNG.randint(1, 2), None, off + 5
    if esc in ("in_field", "done"):
        campo = -1
        off = campo - RNG.randint(1, 3)
        return off, campo, None, campo + 3
    if esc == "waiting":
        ver = -1
        campo = ver - RNG.randint(0, 1)
        off = campo - RNG.randint(1, 3)
        return off, campo, ver, ver + 3
    off = min(off, -9)
    campo = off + RNG.randint(1, 3)
    ver = campo + RNG.randint(0, 1)
    return off, campo, ver, ver + RNG.randint(1, 3)


def compra_verificada(M, arch, n, pr, buyer, qty, off, tipo, scope, prefijo, extra=None):
    """Escribe compra + movimiento/lote + despacho + verificación (+ líneas y
    conteos si es adulto) + posturas + reseña del verificador + comisión +
    reseñas comerciales. Devuelve el dict de la compra."""
    extra = extra or {}
    esc = extra.get("escenario") or elegir_escenario(off)
    emp = extra.get("empresa") or RNG.choice(M["ver_empresas"])
    tec = RNG.choice(M["ver_tecnicos"][emp])
    txx = f"{prefijo}tx_{n:04d}"
    vend, comp = ref_p(pr["sellerx"]), ref_p(buyer)
    off, t_campo, t_ver, entrega = calendario(esc, off)

    vstate = {"received": "received", "assigned": "assigned", "in_field": "in_field",
              "done": "done", "rejected": "rejected", "cancelled": "cancelled"}.get(esc)
    if vstate is None:
        vstate = RNG.choices(["approved", "approved_obs"], [50, 50] if esc == "waiting" else [70, 30])[0]
    cumple = vstate == "approved"
    acc_state, decisiones = "na", None
    if esc == "waiting":
        acc_state = "waiting"
        decisiones = RNG.choice([("pending", "pending"), ("accepted", "pending"), ("pending", "accepted")]
                                if cumple else
                                [("counter", "pending"), ("counter", "pending"), ("pending", "pending"),
                                 ("accepted", "pending")])
    elif esc in ("closed_confirmed", "closed_done"):
        acc_state, decisiones = "closed", ("accepted", "accepted")
    elif esc == "broken":
        acc_state = "broken"
        decisiones = (("rejected", "accepted") if (not cumple or RNG.random() < 0.5)
                      else ("accepted", "rejected"))
    tx_state = {"received": "pending_verification", "assigned": "pending_verification",
                "in_field": "pending_verification", "done": "pending_verification",
                "waiting": "pending_acceptance", "closed_confirmed": "confirmed",
                "closed_done": "done"}.get(esc, "cancel")

    tx = {"xid": txx, "prod": pr, "buyer": buyer, "qty": qty, "off": off, "estado": tx_state,
          "entrega": entrega, "precio": pr["price"], "tipo": tipo, "emp": emp, "escenario": esc,
          "t_campo": t_campo}
    if tx_state in ("confirmed", "done"):
        pr["consumido"] += qty
    precio = pr["price"]
    t_mov = t_ver + 1 if t_ver is not None else off

    arch.rec(txx, "shrimp.transaction", {
        "transaction_type": tipo, "product_id": R(pr["xid"]), "seller_partner_id": R(vend),
        "buyer_partner_id": R(comp), "location": pr["location"], "state": tx_state,
        "needs_verification": True, "transaction_qty": qty, "price_unit": precio,
        "amount_total": r2(qty * precio), "desired_qty": qty, "desired_date": D(entrega),
        "code": pr["name"]})
    if tx_state in ("confirmed", "done"):
        arch.rec(f"{prefijo}mv_{n:04d}", "shrimp.stock.move", {
            "product_id": R(pr["xid"]), "source_partner_id": R(vend), "dest_partner_id": R(comp),
            "qty": qty, "parent_move_id": R(pr["origin_move"]) if pr.get("origin_move") else None,
            "transaction_id": R(txx), "date": DT(min(t_mov, -1), 11, 0)})
    if tx_state == "done":
        tx["lot_comprador"] = f"{prefijo}lotc_{n:04d}"
        arch.rec(tx["lot_comprador"], "shrimp.stock.lot", {
            "product_id": R(pr["xid"]), "owner_id": R(comp),
            "origin_move_id": R(f"{prefijo}mv_{n:04d}"), "initial_qty": qty,
            "available_qty": qty, "uom_id": R(MP + pr["uom"]), "state": "available"})

    # ---------------- despacho: antes de la verificación (ella lo reutiliza)
    dsp = {"transaction_id": R(txx)}
    if esc != "received" and t_campo is not None:
        h_salida, viaje = RNG.randint(4, 9), RNG.randint(2, 6)
        cambios = RNG.choices([0, 1, 2], [70, 22, 8])[0]
        dsp.update({
            "harvest_date": D(t_campo),
            "eta": DT(t_campo, h_salida + viaje, RNG.choice([0, 30])),
            "eta_first": DT(t_campo, h_salida + viaje - (1 if cambios else 0), 0),
            "eta_changes": cambios, "carrier_name": RNG.choice(TRANSPORTISTAS),
            "vehicle_plate": placa(), "carrier_phone": celular(),
            "notes": RNG.choice(["Camión refrigerado con hielo en escamas 1:1.",
                                 "Tinas con agua de mar y oxígeno; salida tras el conteo.",
                                 "Se coordina con el técnico por WhatsApp a la salida.", None]),
        })
        if esc != "assigned":
            dsp["farm_departure"] = DT(t_campo, h_salida, RNG.choice([0, 15, 30]))
            if esc != "in_field" or RNG.random() < 0.5:
                retraso = RNG.choices([RNG.randint(-20, 25), RNG.randint(31, 150)], [75, 25])[0]
                minutos = (h_salida + viaje) * 60 + retraso
                dsp["actual_arrival"] = DT(t_campo, minutos // 60, minutos % 60)
                dsp["arrival_registered_by_id"] = R(tec)
    arch.rec(f"{prefijo}dsp_{n:04d}", "shrimp.dispatch", dsp)

    # ---------------- verificación
    ciudad = M["partners"].get(pr["sellerx"], {}).get("ciudad", "Naranjal")
    v = {
        "transaction_id": R(txx), "verifier_partner_id": R(emp),
        "technician_partner_id": R(tec) if esc != "received" else None,
        "state": vstate, "batch_code": pr["batch"],
        "pond_id": R(MP + pr["pond"]) if pr.get("pond") else None,
        "facility_id": R(MP + pr["facility"]) if pr.get("facility") else None,
        "fee": r2(RNG.uniform(120, 300) if scope == "larvae" else RNG.uniform(180, 460)),
        "currency_id": R("base.USD"),
        "assigned_date": DT(off, RNG.randint(8, 12), RNG.randint(0, 59)),
        "acceptance_state": acc_state,
        "buyer_notified": vstate in ("approved", "approved_obs", "rejected", "cancelled"),
    }
    if vstate in ("in_field", "done", "approved", "approved_obs", "rejected"):
        v["field_start_date"] = DT(t_campo, RNG.randint(6, 9), RNG.randint(0, 59))
        v["gps_latitude"], v["gps_longitude"] = geo(ciudad, 0.05)
        v["incident_notes"] = RNG.choice(["Sin novedades.", "Sin novedades.",
                                          "Retraso de 40 minutos por marea alta en el acceso.",
                                          "Lluvia durante el muestreo; se repitió una toma.",
                                          "Camino lastrado en mal estado; llegada en moto."])
    if vstate in ("approved", "approved_obs", "rejected", "cancelled"):
        v["verified_date"] = DT(t_ver if t_ver is not None else off, RNG.randint(12, 18), RNG.randint(0, 59))
    if vstate in ("approved", "approved_obs", "rejected"):
        v["margin_pct"] = 15.0
    if acc_state in ("waiting", "closed", "broken"):
        v["acceptance_deadline"] = DT(t_ver + 2, 18, 0)
        v["fee_payer_partner_id"] = R(comp)
    if vstate == "rejected":
        v["fee_payer_partner_id"] = R(vend)
    if esc == "broken":
        rechaza_comprador = decisiones[0] == "rejected"
        v["fee_payer_partner_id"] = R(comp if (cumple and rechaza_comprador) else vend)
    v["verdict_notes"] = {
        "approved": "Aprobado sin observaciones: el informe de campo cumple con lo declarado en la publicación.",
        "approved_obs": RNG.choice([
            "Se aprueba con observación: la cantidad verificada queda por debajo de lo vendido.",
            "Se aprueba con observación: talla predominante una por debajo de la publicada.",
            "Se aprueba con observación: merma superior a la habitual en la cosecha."]),
        "rejected": RNG.choice([
            "Se rechaza: no cumple lo publicado y el lote no es apto para la compra.",
            "Se rechaza el lote: los análisis salen fuera de los límites pactados."]),
        "cancelled": "Verificación cancelada a pedido del comprador antes de ir a campo.",
    }.get(vstate)

    lineas, conteos = [], []
    if vstate in ("done", "approved", "approved_obs", "rejected"):
        if scope == "larvae":
            informe_larva(v, pr, qty, vstate)
        else:
            lineas, conteos = informe_adulto(v, pr, qty, vstate, t_campo, extra)

    if vstate in ("done", "approved", "approved_obs", "rejected"):
        v["photo_ids"] = M2M(RNG.sample(IMGS, RNG.randint(2, 4)))
    vx = f"{prefijo}ver_{n:04d}"
    arch.rec(vx, "shrimp.verification", v)
    tx["verif"] = vx
    # Primer cobro de la plataforma: el honorario, emitido al INICIAR la
    # compra verificada (al comprador). Si el honorario lo terminó pagando el
    # vendedor (veredicto rechazado o trato caído por su culpa), el cobro al
    # comprador se acreditó y hay otro al vendedor. Cancelada: anulado.
    pagador = v.get("fee_payer_partner_id") or R(comp)
    cobro_fee = dict(charge_type="verification_fee", verification_id=R(vx), transaction_id=R(txx),
                     buyer_partner_id=R(comp), seller_partner_id=R(vend), product_id=R(pr["xid"]),
                     amount=v["fee"], invoice_qty=1.0, currency_id=R("base.USD"),
                     description="Verificación en campo (demostración)",
                     # Sin RNG: añadir llamadas al azar desplazaría toda la
                     # secuencia y cambiaría registros ya cargados en bases
                     # con la demo anterior (choques al actualizar).
                     date=DT(off, 9, 0))
    if vstate == "cancelled":
        arch.rec(f"{vx}_fee", "shrimp.charge", cobro_demo(
            payer_partner_id=R(comp), cancel_reason="Verificación cancelada", **dict(
                cobro_fee, state="cancelled")))
    elif pagador.x != R(comp).x:
        arch.rec(f"{vx}_fee", "shrimp.charge", cobro_demo(
            payer_partner_id=R(comp), cancel_reason="El honorario lo asume el vendedor", **dict(
                cobro_fee, state="cancelled")))
        arch.rec(f"{vx}_fee2", "shrimp.charge", cobro_demo(payer_partner_id=pagador, **cobro_fee))
    else:
        arch.rec(f"{vx}_fee", "shrimp.charge", cobro_demo(payer_partner_id=pagador, **cobro_fee))
    for k, (cls, sz, w) in enumerate(lineas, 1):
        arch.rec(f"{vx}_l{k}", "shrimp.verification.line", {
            "verification_id": R(vx), "quality_class": cls, "size_code": sz,
            "weight_lb": w, "sequence": k * 10})
    for k, (val, nota) in enumerate(conteos, 1):
        arch.rec(f"{vx}_c{k}", "shrimp.verification.count", {
            "verification_id": R(vx), "value": val, "note": nota, "sequence": k * 10})

    # ---------------- posturas de las partes
    if decisiones:
        for rol, partner, dec in (("buyer", comp, decisiones[0]), ("seller", vend, decisiones[1])):
            a = {"verification_id": R(vx), "role": rol, "partner_id": R(partner), "decision": dec,
                 "reason": {"accepted": "Conforme con el informe del verificador.",
                            "counter": "El lote no cumplió lo publicado; propongo ajustar el precio.",
                            "rejected": "No acepto el informe: el resultado no corresponde a lo pactado.",
                            "pending": None}[dec]}
            if dec != "pending":
                a["decided_at"] = DT(t_ver if esc == "waiting" else t_ver + RNG.randint(0, 1),
                                     RNG.randint(8, 20), RNG.randint(0, 59))
            if dec == "counter":
                a["counter_price"] = r2(precio * RNG.uniform(0.86, 0.95))
            if esc == "closed_done" and dec == "accepted" and RNG.random() < 0.12:
                a["auto"] = True
                a["reason"] = "Aceptada automáticamente al vencer el plazo sin respuesta."
            arch.rec(f"{vx}_acc_{rol}", "shrimp.verification.acceptance", a)

    # ---------------- reseña del comprador al verificador
    if vstate in ("approved", "approved_obs", "rejected") and esc != "waiting" and RNG.random() < 0.75:
        nota = max(1, min(5, (5 if vstate == "approved" else 4) - RNG.choice([0, 0, 0, 1, 2])))
        arch.rec(f"{vx}_rev", "shrimp.verifier.review", {
            "verification_id": R(vx), "reviewer_partner_id": R(comp), "rating": nota,
            "punctuality": max(1, min(5, nota + RNG.choice([-1, 0, 0, 1]))),
            "thoroughness": max(1, min(5, nota + RNG.choice([-1, 0, 0, 1]))),
            "communication": max(1, min(5, nota + RNG.choice([-1, 0, 1]))),
            "comment": RNG.choice([
                "Llegó a la hora de la cita y explicó cada medición.",
                "Informe claro y con fotos; se nota experiencia en muelle.",
                "Buen trabajo, aunque el informe tardó medio día más de lo previsto.",
                "Muy riguroso con el conteo; nos evitó una mala compra.",
                "Puntual y ordenado. Repetiremos con esta empresa.",
                "Cumplió, pero la comunicación por WhatsApp fue lenta."])})

    # ---------------- comisión y reseñas comerciales
    if tx_state in ("confirmed", "done"):
        rate = TARIFA_CENTAVOS[pr["uom"]]
        arch.rec(f"{prefijo}chg_{n:04d}", "shrimp.charge", cobro_demo(
            charge_type="commission",
            transaction_id=R(txx), seller_partner_id=R(vend), buyer_partner_id=R(comp),
            payer_partner_id=R(vend),
            product_id=R(pr["xid"]), qty=qty, uom_id=R(MP + pr["uom"]),
            rate_cents=rate, invoice_qty=qty, unit_amount=rate / 100.0,
            amount=r2(qty * rate / 100.0), currency_id=R("base.USD"),
            description="Comisión marketplace – %s" % pr["name"],
            date=DT(min(t_mov, -1), 18, 0)))
    if tx_state == "done":
        rv = [(tx, d) for d, p in (("to_seller", 0.8), ("to_buyer", 0.55)) if RNG.random() < p]
        escribir_resenas(arch, rv)
    return tx


def informe_larva(v, pr, qty, vstate):
    if vstate == "approved":
        fq, salud, surv = RNG.uniform(0.985, 1.03), RNG.choice(["excellent", "good", "good"]), pr["surv"] + RNG.uniform(-2, 2)
    elif vstate == "approved_obs":
        fq, salud, surv = RNG.uniform(0.9, 0.965), RNG.choice(["good", "acceptable"]), pr["surv"] - RNG.uniform(3, 8)
    elif vstate == "rejected":
        fq, salud, surv = RNG.uniform(0.7, 0.88), "rejected", pr["surv"] - RNG.uniform(20, 35)
    else:
        fq, salud, surv = RNG.uniform(0.97, 1.02), RNG.choice(["excellent", "good", "acceptable"]), pr["surv"] + RNG.uniform(-3, 1)
    v.update({
        "larvae_qty_verified": r2(qty * fq), "larvae_survival_rate": r2(max(20, surv)),
        "larvae_avg_size_mg": round(pr["size_mg"] * RNG.uniform(0.88, 1.08), 3),
        "larvae_health_status": salud,
        "larvae_health_notes": ("Necrosis en urópodos y alta mortalidad en el conteo volumétrico."
                                if salud == "rejected" else
                                "Larvas activas, fototropismo positivo, intestino lleno. Prueba de estrés superada."),
    })


def informe_adulto(v, pr, qty, vstate, t_campo, extra):
    """Los cinco análisis del camarón adulto, con números de planta creíbles."""
    pres = pr["presentation"]
    escalera = TALLAS_ENTERO if pres == "entero" else TALLAS_COLA
    idx = next((i for i, t in enumerate(escalera) if t[0] == pr["size"]), 2)
    enviado = float(qty)
    planta = r2(enviado * RNG.uniform(0.99, 1.012))
    if vstate == "approved_obs" and RNG.random() < 0.5:
        planta = r2(enviado * RNG.uniform(0.95, 0.975))       # faltó peso
    basura = r2(planta * RNG.uniform(0.008, 0.03))
    neto = planta - basura
    ppm = r2(RNG.uniform(110, 165) if (vstate == "rejected" and RNG.random() < 0.6) else RNG.uniform(25, 92))
    if vstate == "rejected" and ppm <= 100:
        sabor = "rejected"
    elif vstate == "approved_obs":
        sabor = RNG.choice(["good", "acceptable"])
    else:
        sabor = RNG.choice(["excellent", "good", "good", "acceptable"])
    clasificado = neto * (RNG.uniform(0.93, 0.985) if pres == "entero" else RNG.uniform(0.6, 0.68))
    dom = idx
    if vstate == "approved_obs" and RNG.random() < 0.5:
        dom = min(len(escalera) - 1, idx + 1)
    fa = RNG.uniform(0.55, 0.8) if vstate != "rejected" else RNG.uniform(0.3, 0.5)
    fb = RNG.uniform(0.12, min(0.3, 0.95 - fa))
    fc = max(0.02, 1 - fa - fb)
    ultimo = len(escalera) - 1
    reparto = {"a": [(dom, 0.8)] + ([(dom - 1, 0.2)] if dom > 0 else []),
               "b": [(dom, 0.6), (min(ultimo, dom + 1), 0.4)],
               "c": [(min(ultimo, dom + 1), 0.7), (min(ultimo, dom + 2), 0.3)]}
    lineas = []
    for cls, frac in (("a", fa), ("b", fb), ("c", fc)):
        pesos = {}
        for t, w in reparto[cls]:
            pesos[t] = pesos.get(t, 0) + w * RNG.uniform(0.8, 1.2)
        s = sum(pesos.values())
        for t in sorted(pesos):
            w = r2(clasificado * frac * pesos[t] / s)
            if w > 0:
                lineas.append((cls, escalera[t][1], w))
    piezas = escalera[dom][2]
    gram = (1000.0 / piezas) if pres == "entero" else (453.6 / piezas)
    unidad = "piezas/kg" if pres == "entero" else "piezas/lb"
    conteos = [(r2(piezas * RNG.uniform(0.93, 1.07)), f"Conteo {k} en muelle ({unidad})")
               for k in range(1, RNG.randint(2, 4) + 1)]
    v.update({
        "plant_name": extra.get("planta") or RNG.choice(PLANTAS_TXT),
        "harvest_date": D(t_campo), "process_date": D(t_campo + RNG.choice([0, 0, 1]) if t_campo < -1 else t_campo),
        "weight_sent_lb": r2(enviado), "weight_plant_lb": planta, "trash_lb": basura,
        "presentation": pres, "metabisulfite_ppm": ppm, "metabisulfite_limit_ppm": 100.0,
        "metabisulfite_notes": "Kit colorimétrico, tres tomas sobre el mismo lote.",
        "taste_result": sabor,
        "taste_notes": ("Se detecta sabor a fango; no apto." if sabor == "rejected"
                        else "Sabor característico, sin notas extrañas."),
        "grams_farm": r2(gram * RNG.uniform(0.97, 1.03)),
        "grams_plant_1": r2(gram * RNG.uniform(0.95, 1.05)),
        "grams_plant_2": r2(gram * RNG.uniform(0.95, 1.05)),
    })
    if sabor != "rejected":
        crit = RNG.sample(["taste_crit_olor", "taste_crit_color"] +
                          [f"demo_ms_crit_{i}" for i in range(1, 11)], 5)
        v["taste_criteria_ok_ids"] = M2M([SV + c for c in crit])
    return lineas, conteos


# ============================================================================
def construir(M):
    f8 = Archivo("shrimp_verification", "demo_08_masivo_verificadores.xml",
                 "Empresas verificadoras, técnicos, acreditaciones y cuentas de acceso\n"
                 "     (incluye verificador.demo@camaronera.test).")
    f9 = Archivo("shrimp_verification", "demo_09_masivo_verificaciones_larva.xml",
                 "Compras de postlarva laboratorio->camaronera con verificación en campo,\n"
                 "     en todas las etapas: despacho, informe, posturas, reseñas y cobros.")
    M["archivos"] += [f8, f9]

    f8.seccion("Catálogos: criterios de cata y cargos")
    for i, c in enumerate(CRITERIOS_NUEVOS, 1):
        f8.rec(f"demo_ms_crit_{i}", "shrimp.taste.criterion", {"name": c, "sequence": 20 + i})
    for i, c in enumerate(CARGOS_NUEVOS, 1):
        f8.rec(f"demo_ms_cargo_{i}", "shrimp.tech.role", {"name": c, "sequence": 60 + i})
    cargos = ["tech_role_campo", "tech_role_supervisor", "tech_role_inspector", "tech_role_lab",
              "tech_role_coord"] + [f"demo_ms_cargo_{i}" for i in range(1, 5)]

    # ---------------------------------------------------------------- empresas
    M["ver_empresas"] = [SV + f"demo_ver_{s}" for s in EMPRESAS_VIEJAS]
    M["ver_tecnicos"] = {SV + f"demo_ver_{s}": [SV + f"demo_vtec_{s}_{k}" for k in range(1, n + 1)]
                         for s, n in TECNICOS_VIEJOS.items()}
    f8.seccion("Bancos de las cuentas de los verificadores")
    bancos = {}
    for nb in ["Banco Pichincha", "Banco del Pacífico", "Banco Guayaquil",
               "Produbanco", "Banco Bolivariano", "Banco Internacional"]:
        bancos[nb] = f"demo_ms_bank_{slug(nb, 30)}"
        f8.rec(bancos[nb], "res.bank", {"name": nb, "country": R("base.ec")})

    f8.seccion("Empresas verificadoras nuevas")
    nuevas = []
    for i, (nombre, ciudad) in enumerate(EMPRESAS_NUEVAS):
        x = f"demo_ms_ver_{i + 1:02d}"
        prov, cod, st = CIUDADES[ciudad][0], CIUDADES[ciudad][1], CIUDADES[ciudad][2]
        ruc = ruc_sociedad(cod)
        dom = slug(nombre.split(" S.A")[0].split(" Cía")[0], 25).replace("_", "")
        tel = telefono_fijo(ciudad)
        cobertura = {"Guayas": "Guayas, Santa Elena, Los Ríos", "El Oro": "El Oro, Guayas",
                     "Manabí": "Manabí, Esmeraldas", "Santa Elena": "Santa Elena, Guayas",
                     "Esmeraldas": "Esmeraldas, Manabí"}[prov]
        socio = {
            "name": nombre, "is_company": True, "shrimp_user_type": "verificador",
            "vat_or_id": ruc, "email": f"operaciones@{dom}.test", "phone": tel,
            "street": f"Av. {RNG.choice(['Malecón', '25 de Junio', 'de las Américas', 'Rodríguez Bonín', 'Circunvalación'])} {RNG.randint(100, 2500)}",
            "city": ciudad, "state_id": R(st), "country_id": R("base.ec"),
            "shrimp_razon_social": nombre, "shrimp_representante": persona(con_titulo=True),
            "shrimp_telefono": tel, "shrimp_ubicacion": f"{ciudad}, {prov}", "ver_cobertura": cobertura,
            "ver_provincias": cobertura, "ver_radio_km": RNG.choice([80, 120, 150, 200, 250]),
            "ver_registro_num": f"SAE-VER-{RNG.randint(1000, 9999)}",
            "ver_entidad_acredita": "Servicio de Acreditación Ecuatoriano (SAE)",
            "ver_acred_vigencia": D(RNG.randint(120, 700) if i not in SIN_ACREDITAR else -20),
            # Datos de la cuenta: se sortean AQUÍ, en el mismo orden que antes
            # (cuando eran campos de la ficha), para no desplazar la secuencia
            # del azar; después se sacan del diccionario y van a su
            # res.partner.bank.
            "_banco": RNG.choice(["Banco Pichincha", "Banco del Pacífico", "Banco Guayaquil",
                                  "Produbanco", "Banco Bolivariano", "Banco Internacional"]),
            "_tipo": RNG.choice(["ahorros", "corriente"]),
            "_numero": f"{RNG.randint(10, 99)}{RNG.randint(10000000, 99999999)}",
            "ver_email_avisos": f"avisos@{dom}.test", "ver_whatsapp": celular(),
            "ver_horario": RNG.choice(["Lunes a sábado, 06:00 a 20:00", "Todos los días, 05:00 a 22:00",
                                       "Lunes a viernes 07:00-18:00; sábados bajo pedido"]),
            "ver_tiempo_respuesta": RNG.choice(["8 horas", "12 horas", "24 horas", "24 a 48 horas"]),
            "ver_equipo_propio": RNG.random() < 0.8,
            "ver_analisis_tipos": "Peso y merma, presentación (cuerpo/cola), metabisulfito, "
                                  "clasificación por talla, sabor; conteo volumétrico y supervivencia de larva",
            "ver_equipos": "Balanza 0,1 g, kit colorimétrico de metabisulfito, calibrador de tallas, "
                           "microscopio de campo, termómetro y oxímetro",
            "shrimp_capacity_value": float(RNG.randint(4, 16)), "shrimp_capacity_unit": "lots_day",
            "ver_ruc": ruc, "vat": ruc, "ver_razon_fiscal": nombre, "ver_dir_fiscal": f"{ciudad}, {prov}",
            "ver_fee_base": float(RNG.choice([180, 220, 250, 280, 300])),
            "ver_fee_por_lb": RNG.choice([0.005, 0.008, 0.01]),
        }
        banco, tipo, numero = socio.pop("_banco"), socio.pop("_tipo"), socio.pop("_numero")
        f8.rec(x, "res.partner", socio)
        # La cuenta para liquidarle sus honorarios: en el modelo estándar de
        # cuentas bancarias (antes, campos ver_bank_* en la ficha).
        f8.rec(f"{x}_cuenta", "res.partner.bank", {
            "partner_id": R(x), "bank_id": R(bancos[banco]), "acc_number": numero,
            "acc_holder_name": nombre, "shrimp_account_type": tipo, "shrimp_holder_id": ruc})
        nuevas.append((x, nombre, dom, ruc))

    f8.seccion("Técnicos de campo de las empresas nuevas (contactos hijos)")
    for x, nombre, dom, ruc in nuevas:
        M["ver_tecnicos"][SV + x] = []
        for k in range(1, RNG.randint(4, 7) + 1):
            t = f"{x}_tec_{k}"
            per = persona()
            partes = per.split()
            f8.rec(t, "res.partner", {
                "name": per, "parent_id": R(x), "type": "contact",
                "function": "Técnico de campo" if k <= 2 else RNG.choice(
                    ["Supervisor de campo", "Inspector de calidad", "Biólogo de campo"]),
                "shrimp_is_field_tech": True,
                "tech_role_id": R(RNG.choice(cargos[:2]) if k <= 2 else RNG.choice(cargos)),
                "email": f"{slug(partes[0])}.{slug(partes[1])}{k}@{dom}.test", "phone": celular()})
            M["ver_tecnicos"][SV + x].append(SV + t)

    f8.seccion("Acreditaciones con su PDF: vigentes, pendiente, vencida y rechazada")
    for i, (x, nombre, dom, ruc) in enumerate(nuevas):
        ax = f"{x}_acred_pdf"
        f8.rec(ax, "ir.attachment", {
            "name": f"Acreditacion_{slug(nombre, 30)}.pdf", "type": "binary",
            "mimetype": "application/pdf",
            "datas": B64(pdf_minimo(f"Acreditación de verificador - {nombre}", [
                f"RUC: {ruc}", "Organismo: Servicio de Acreditación Ecuatoriano (SAE)",
                "Alcance: verificación de camarón en campo y en planta"]))})
        estado, vence = "approved", RNG.randint(120, 720)
        if SIN_ACREDITAR.get(i) == "pending":
            estado = "pending"
        elif SIN_ACREDITAR.get(i) == "expired":
            vence = -RNG.randint(10, 60)
        f8.rec(f"{x}_acred", "shrimp.user.certificate.line", {
            "partner_id": R(x), "certificate_id": R("cert_acreditacion_verificador"),
            "certificate_number": f"ACRED-VER-{300 + i}", "issue_date": D(min(-30, vence - 730)),
            "expiry_date": D(vence), "file_attachment_id": R(ax), "status": estado})
        if SIN_ACREDITAR.get(i) == "expired":
            # pidió la renovación y se la rechazaron por documento ilegible
            f8.rec(f"{x}_acred_renov", "shrimp.user.certificate.line", {
                "partner_id": R(x), "certificate_id": R("cert_acreditacion_verificador"),
                "certificate_number": f"ACRED-VER-{400 + i}", "issue_date": D(-5),
                "expiry_date": D(725), "file_attachment_id": R(ax), "status": "rejected"})
        if i % 2 == 0:
            f8.rec(f"{x}_hab_lab", "shrimp.user.certificate.line", {
                "partner_id": R(x), "certificate_id": R("cert_habilitacion_laboratorio_analisis"),
                "certificate_number": f"HAB-LAB-{500 + i}", "issue_date": D(-RNG.randint(60, 300)),
                "expiry_date": D(RNG.randint(60, 400)), "file_attachment_id": R(ax), "status": "approved"})
        if i not in SIN_ACREDITAR:
            M["ver_empresas"].append(SV + x)

    # ---------------------------------------------------------------- cuentas
    f8.seccion("Cuentas de acceso: empresas y técnicos (portal) e internos")
    for k, s in enumerate(EMPRESAS_VIEJAS, 1):
        login = (f"verificador.demo@{DOMINIO_LOGIN}" if s == "pacifico"
                 else f"verificador.{k:02d}@{DOMINIO_LOGIN}")
        ux = f"demo_ms_user_ver_{s}"
        usuario_portal(f8, ux, f"demo_ver_{s}", login)
        M["usuarios"].append((login, ux, f"demo_ver_{s}", "verificador", "shrimp_verification"))
    for k, (x, *_r) in enumerate(nuevas, len(EMPRESAS_VIEJAS) + 1):
        login = f"verificador.{k:02d}@{DOMINIO_LOGIN}"
        ux = f"demo_ms_user_{x[8:]}"
        usuario_portal(f8, ux, x, login)
        M["usuarios"].append((login, ux, x, "verificador", "shrimp_verification"))
    nt = 0
    for tecs in M["ver_tecnicos"].values():
        for t in tecs:
            nt += 1
            login = f"tecnico.{nt:02d}@{DOMINIO_LOGIN}"
            ux = f"demo_ms_user_tec_{nt:03d}"
            usuario_portal(f8, ux, t.replace(SV, ""), login)
            M["usuarios"].append((login, ux, t.replace(SV, ""), "tecnico", "shrimp_verification"))
    for login, nombre, grupos in [
            ("coordinador.verificaciones", "Coordinación de verificaciones",
             ["base.group_user", "shrimp_marketplace.group_shrimp_user"]),
            ("contabilidad", "Contabilidad de la plataforma",
             ["base.group_user", "shrimp_marketplace.group_shrimp_user", "account.group_account_invoice"])]:
        pid = f"demo_ms_int_{slug(login)}_partner"
        f8.rec(pid, "res.partner", {"name": nombre, "email": f"{login}@{DOMINIO_LOGIN}"})
        ux = f"demo_ms_user_int_{slug(login)}"
        f8.rec(ux, "res.users", {"partner_id": R(pid), "login": f"{login}@{DOMINIO_LOGIN}",
                                 "password": PASSWORD_DEMO, "company_id": R("base.main_company"),
                                 "company_ids": M2M(["base.main_company"]),
                                 "group_ids": M2M(grupos), "tz": "America/Guayaquil"})
        M["usuarios"].append((f"{login}@{DOMINIO_LOGIN}", ux, pid, "interno", "shrimp_verification"))

    # ---------------------------------------------------------------- larva verificada
    P = M["partners"]
    productos = []
    for i in range(1, 46):
        lab = RNG.choice(M["lab"])
        p = P[lab]
        tq = RNG.choice([t for t in M["ponds"][lab] if t[2] in ("tank", "raceway")])
        pl = RNG.choice([10, 12, 12, 15])
        off = -RNG.randint(4, 330)
        productos.append({
            "xid": f"demo_mv_prod_{i:03d}", "kind": "lab", "sellerx": lab, "rol": "laboratorio",
            "species": "shrimp_species_vannamei", "stage": f"shrimp_stage_pl{pl}",
            "genetics": RNG.choice(["shrimp_genetics_spr", "shrimp_genetics_high_survival",
                                    "shrimp_genetics_tolerancia_wssv", "shrimp_genetics_fast_growth"]),
            "name": f"Postlarva PL{pl} certificada - Corrida V{i:03d} ({p['ciudad']})",
            "size_mg": r2(0.8 + pl * RNG.uniform(0.2, 0.3)), "surv": r2(RNG.uniform(72, 90)),
            "health": RNG.choice(SANIDAD_OK), "qty": RNG.randrange(12000, 40000, 100),
            "uom": "uom_millar", "price": r2(RNG.uniform(1.9, 3.3)),
            "location": f"{p['ciudad']}, {p['provincia']}", "state": "published", "off": off,
            "entrega": off + 3, "ventana": 20, "facility": tq[1], "pond": tq[0],
            "batch": f"LV-{lab[-2:]}-{i:03d}", "fotos": RNG.sample(IMGS, 2), "consumido": 0.0,
            "lot": f"demo_mv_lot_{i:03d}",
            "notas": "Corrida con PCR por lote; se vende con verificación de conteo en la siembra."})

    # Primero se deciden las compras (y su escenario) para saber el stock final.
    compras = []
    cams = [c for c in M["cam"] if not c.startswith("demo_ms_cam_grupo")]
    for pr in productos:
        pedido = 0
        for _ in range(RNG.randint(1, 3)):
            qty = RNG.randrange(800, 4500, 50)
            if pedido + qty > pr["qty"] * 0.7:
                break
            pedido += qty
            off = min(-1, pr["off"] + RNG.randint(1, 10))
            esc = elegir_escenario(off)
            compras.append((pr, RNG.choice(cams), qty, off, esc))
            if esc in ("closed_confirmed", "closed_done"):
                pr["consumido"] += qty
    f9.seccion("Corridas de postlarva que se venden con verificación (lote con disponible final)")
    for pr in productos:
        if disponible(pr) <= 0:
            pr["state"] = "sold"
        f9.rec(pr["xid"], "shrimp.product", prod_vals(pr), context="{'skip_initial_lot': True}")
        f9.rec(pr["lot"], "shrimp.stock.lot", lote_vals(pr))
    for pr in productos:
        certs_producto(M, f9, pr)
        evoluciones(M, f9, pr)
        pr["consumido"] = 0.0          # el motor lo vuelve a sumar
    f9.seccion("Compras verificadas: compra, movimiento, despacho, verificación y posturas")
    for n, (pr, cam, qty, off, esc) in enumerate(compras, 1):
        compra_verificada(M, f9, n, pr, cam, qty, off, "laboratorio_to_camaronera", "larvae",
                          "demo_mv_", {"escenario": esc})
