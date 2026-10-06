# -*- coding: utf-8 -*-
"""Verificaciones DECLARADAS por las partes (shrimp_packer,
demo_11_masivo_verificacion_declarada.xml).

En vez de una verificadora de la plataforma, el comprador eligió una
verificación declarada: la hizo una verificadora externa (por convenio o no
registrada en la plataforma) o las propias partes. Una de ellas carga y
presenta el informe; la otra lo confirma en la ronda de aceptación. Sin
honorario de verificación (la comisión de la venta sí se cobra).

Escenarios (cada uno con su propia cosecha de camaronera, para que el stock
cuadre sin tocar los lotes que ya escribió la demo):
  1. Declarada por la EMPACADORA (verificadora externa con RUC y PDF);
     la camaronera acepta -> compra cerrada y recibida.
  2. Declarada por la CAMARONERA (verificación propia); la empacadora
     acepta -> compra confirmada.
  3. Declarada por la camaronera (externa sin RUC); el informe muestra
     faltante de peso, la empacadora contraoferta y la camaronera acepta
     el precio nuevo -> compra cerrada al precio ajustado.
  4. Declarada por la camaronera; la empacadora la RECHAZA -> trato caído,
     compra cancelada (sin honorario que repartir).
  5. Declarada por la empacadora y esperando la respuesta de la camaronera.
  6. Informe declarado todavía EN PREPARACIÓN (borrador a medias).

Se ejecuta el ÚLTIMO y con su PROPIA semilla: guarda y restaura el estado de
comun.RNG, así que el resto de la demo sale idéntico byte a byte.
"""
from .comun import B64, MP, RNG, SP, Archivo, D, DT, M2M, R, cobro_demo, pdf_minimo, r2
from .marketplace import IMGS, SANIDAD_OK, TARIFA_CENTAVOS, lote_vals, prod_vals, ref_p

SEMILLA_DECLARADAS = 20261004
PREFIJO = "demo_dcl_"

EXTERNAS = [
    ("Laboratorio de Control de Calidad Pacífico Sur S.A.", "0992456781001"),
    ("Inspecciones Acuícolas del Litoral (convenio con la planta)", None),
]


def construir(M):
    estado = RNG.getstate()
    RNG.seed(SEMILLA_DECLARADAS)
    try:
        _packer(M)
    finally:
        RNG.setstate(estado)


def _nombre_corto(P, x):
    return P[x]["nombre"].split(" S.A")[0].split(" Cía")[0]


def _pond(M, px):
    opciones = [t for t in M["ponds"].get(px, []) if t[2] in ("earth",)]
    return opciones[0] if opciones else None


def _informe(qty, faltante=False):
    """Números de planta creíbles para un entero 30/40."""
    enviado = float(qty)
    planta = r2(enviado * (RNG.uniform(0.93, 0.95) if faltante else RNG.uniform(0.995, 1.01)))
    basura = r2(planta * RNG.uniform(0.008, 0.02))
    neto = planta - basura
    clasificado = neto * RNG.uniform(0.93, 0.97)
    fa, fb = RNG.uniform(0.6, 0.75), RNG.uniform(0.15, 0.25)
    fc = max(0.02, 1 - fa - fb)
    lineas = [("a", "30/40", r2(clasificado * fa)), ("b", "40/50", r2(clasificado * fb)),
              ("c", "50/60", r2(clasificado * fc))]
    vals = {
        "weight_sent_lb": r2(enviado), "weight_plant_lb": planta, "trash_lb": basura,
        "presentation": "entero", "metabisulfite_ppm": r2(RNG.uniform(35, 85)),
        "metabisulfite_limit_ppm": 100.0,
        "metabisulfite_notes": "Kit colorimétrico, dos tomas sobre el mismo lote.",
        "taste_result": RNG.choice(["excellent", "good", "good"]),
        "taste_notes": "Sabor característico, sin notas a tierra.",
        "grams_farm": r2(RNG.uniform(27.5, 29.5)),
        "grams_plant_1": r2(RNG.uniform(27, 29)), "grams_plant_2": r2(RNG.uniform(27, 29)),
        "taste_criteria_ok_ids": M2M([f"shrimp_verification.{c}" for c in
                                      ("taste_crit_olor", "taste_crit_color")]),
    }
    conteos = [(r2(35 * RNG.uniform(0.95, 1.05)), f"Conteo {k} en planta (piezas/kg)") for k in (1, 2)]
    return vals, lineas, conteos


def _packer(M):
    P = M["partners"]
    f = Archivo("shrimp_packer", "demo_11_masivo_verificacion_declarada.xml",
                "Compras de camarón adulto con verificación DECLARADA por las partes (verificadora\n"
                "     externa o propia): aceptadas, una rechazada, una esperando y una en preparación.")
    M["archivos"].append(f)
    cams = [c for c in M["cam"] if not c.startswith("demo_ms_cam_grupo")
            and c not in (M["cam"][7], M["cam"][9])]
    emps = sorted(M["listas"])
    escenarios = [
        # (n, declara, origen, decision comprador, decision vendedor, estado tx, faltante)
        (1, "buyer", 0, "accepted", "accepted", "done", False),
        (2, "seller", "self", "accepted", "accepted", "confirmed", False),
        (3, "seller", 1, "counter", "accepted", "done", True),
        (4, "seller", 0, "rejected", "accepted", "cancel", False),
        (5, "buyer", 1, "accepted", "pending", "pending_acceptance", False),
        (6, None, 0, None, None, "pending_verification", False),
    ]
    for n, declara, origen, dec_b, dec_s, tx_state, faltante in escenarios:
        cam = cams[(7 + n) % len(cams)]
        emp_key = emps[n % len(emps)]
        emp = SP + emp_key
        pond = _pond(M, cam)
        off = {1: -30, 2: -22, 3: -16, 4: -12, 5: -3, 6: -1}[n]
        qty_lote = float(RNG.choice([12000, 15000, 18000, 20000]))
        qty = round(qty_lote * RNG.uniform(0.6, 0.9) / 10) * 10
        precio = r2(RNG.uniform(2.55, 2.95))
        p = P[cam]
        pr = dict(xid=f"{PREFIJO}prod_{n:02d}", kind="eng", sellerx=cam, rol="camaronera",
                  species="shrimp_species_vannamei", stage="shrimp_stage_engorde",
                  genetics="shrimp_genetics_spr",
                  name=f"Cosecha entero 30/40 - verificación declarada ({_nombre_corto(P, cam)})",
                  size_mg=r2(1000.0 / 35 * 1000), surv=r2(RNG.uniform(60, 72)),
                  health=RNG.choice(SANIDAD_OK), qty=qty_lote, uom="uom_libra",
                  presentation="entero", size="size_entero_3040", price=precio,
                  location=f"{p['ciudad']}, {p['provincia']}", state="published", off=off - 1,
                  entrega=off + 1, ventana=10, facility=pond[1] if pond else None,
                  pond=pond[0] if pond else None, fotos=RNG.sample(IMGS, 3),
                  consumido=qty if tx_state in ("done", "confirmed") else 0.0,
                  lot=f"{PREFIJO}lot_{n:02d}", batch=f"DCL-{n:03d}",
                  notas="Lote vendido con verificación declarada por las partes.")
        f.seccion(f"Escenario {n}: cosecha y compra con verificación declarada")
        f.rec(pr["xid"], "shrimp.product", prod_vals(pr), context="{'skip_initial_lot': True}")
        f.rec(pr["lot"], "shrimp.stock.lot", lote_vals(pr))

        vend, comp = ref_p(cam), emp
        txx = f"{PREFIJO}tx_{n:02d}"
        precio_final = precio
        contra = None
        if dec_b == "counter":
            contra = r2(precio * RNG.uniform(0.9, 0.94))
            precio_final = contra
        f.rec(txx, "shrimp.transaction", {
            "transaction_type": "camaronera_to_buyer", "product_id": R(pr["xid"]),
            "seller_partner_id": R(vend), "buyer_partner_id": R(comp),
            "seller_role": "camaronera", "buyer_role": "empacadora",
            "location": pr["location"], "state": tx_state, "needs_verification": True,
            "transaction_qty": qty, "price_unit": precio_final,
            "amount_total": r2(qty * precio_final), "desired_qty": qty,
            "desired_date": D(off + 2), "code": pr["name"]})
        if tx_state in ("done", "confirmed"):
            mvx = f"{PREFIJO}mv_{n:02d}"
            f.rec(mvx, "shrimp.stock.move", {
                "product_id": R(pr["xid"]), "source_partner_id": R(vend), "dest_partner_id": R(comp),
                "qty": qty, "transaction_id": R(txx), "date": DT(off + 2, 11, 0)})
            if tx_state == "done":
                f.rec(f"{PREFIJO}lotc_{n:02d}", "shrimp.stock.lot", {
                    "product_id": R(pr["xid"]), "owner_id": R(comp), "origin_move_id": R(mvx),
                    "initial_qty": qty, "available_qty": qty, "uom_id": R(MP + "uom_libra"),
                    "state": "available"})
            rate = TARIFA_CENTAVOS["uom_libra"]
            f.rec(f"{PREFIJO}chg_{n:02d}", "shrimp.charge", cobro_demo(
                charge_type="commission", transaction_id=R(txx), seller_partner_id=R(vend),
                buyer_partner_id=R(comp), payer_partner_id=R(vend), product_id=R(pr["xid"]),
                qty=qty, uom_id=R(MP + "uom_libra"), rate_cents=rate, invoice_qty=qty,
                unit_amount=rate / 100.0, amount=r2(qty * rate / 100.0),
                currency_id=R("base.USD"), description="Comisión marketplace – %s" % pr["name"],
                date=DT(off + 2, 18, 0)))

        # ---------------- informe declarado
        informe, lineas, conteos = _informe(qty, faltante)
        externa = EXTERNAS[origen] if isinstance(origen, int) else None
        v = {
            "transaction_id": R(txx), "verification_mode": "declared",
            "state": "declared_draft" if declara is None else "declared",
            "batch_code": pr["batch"],
            "pond_id": R(MP + pr["pond"]) if pr.get("pond") else None,
            "facility_id": R(MP + pr["facility"]) if pr.get("facility") else None,
            "currency_id": R("base.USD"), "fee": 0.0,
            "assigned_date": DT(off, 9, 0),
            "declared_source": "external" if externa else "self",
            "external_verifier_name": externa[0] if externa else None,
            "external_verifier_vat": externa[1] if externa else None,
            "plant_name": f"Planta {P[emp_key]['ciudad']}",
            "harvest_date": D(off), "process_date": D(off),
        }
        if declara is None:
            # Borrador a medias: solo los pesos, cargados por la empacadora.
            v.update({k: informe[k] for k in ("weight_sent_lb", "weight_plant_lb", "trash_lb",
                                               "presentation")})
            v["declared_last_editor_id"] = R(comp)
            v["acceptance_state"] = "na"
        else:
            v.update(informe)
            declarante = comp if declara == "buyer" else vend
            v.update({
                "declarant_partner_id": R(declarante), "declarant_role": declara,
                "declared_last_editor_id": R(declarante),
                "declared_submitted_date": DT(off + 1, 16, 0),
                "verified_date": DT(off + 1, 16, 0),
                "verdict_notes": ("Faltante de peso en planta respecto de lo vendido."
                                  if faltante else "Lote conforme con lo publicado."),
                "photo_ids": M2M(RNG.sample(IMGS, 2)),
                "acceptance_state": {"done": "closed", "confirmed": "closed", "cancel": "broken",
                                     "pending_acceptance": "waiting"}[tx_state],
                "acceptance_deadline": DT(off + 3, 18, 0),
                "buyer_notified": True,
            })
            if externa:
                v["declared_report_file"] = B64(pdf_minimo(
                    f"Informe de verificacion - lote {pr['batch']}", [
                        f"Verificadora: {externa[0]}" + (f" (RUC {externa[1]})" if externa[1] else ""),
                        f"Peso en planta: {informe['weight_plant_lb']} lb",
                        "Clasificacion A/B/C, metabisulfito y cata en planta.",
                        "Documento de demostracion generado automaticamente."]))
                v["declared_report_filename"] = f"Informe_verificacion_{pr['batch']}.pdf"
        vx = f"{PREFIJO}ver_{n:02d}"
        f.rec(vx, "shrimp.verification", v)
        if declara is not None:
            for k, (cls, sz, w) in enumerate(lineas, 1):
                f.rec(f"{vx}_l{k}", "shrimp.verification.line", {
                    "verification_id": R(vx), "quality_class": cls, "size_code": sz,
                    "weight_lb": w, "sequence": k * 10})
            for k, (val, nota) in enumerate(conteos, 1):
                f.rec(f"{vx}_c{k}", "shrimp.verification.count", {
                    "verification_id": R(vx), "value": val, "note": nota, "sequence": k * 10})

        # ---------------- posturas: el declarante suscribe; la otra parte decide
        if declara is None:
            continue
        razones = {
            "accepted": "Conforme con el informe declarado.",
            "counter": "El informe muestra faltante de peso; propongo ajustar el precio.",
            "rejected": "No acepto el informe declarado: no coincide con lo pactado.",
            "pending": None,
        }
        for rol, partner, dec in (("buyer", comp, dec_b), ("seller", vend, dec_s)):
            a = {"verification_id": R(vx), "role": rol, "partner_id": R(partner), "decision": dec,
                 "reason": ("Presentó el informe declarado." if rol == declara else razones[dec])}
            if dec != "pending":
                a["decided_at"] = DT(off + (1 if rol == declara else 2), 17, 0)
            if dec == "counter":
                a["counter_price"] = contra
            f.rec(f"{vx}_acc_{rol}", "shrimp.verification.acceptance", a)
