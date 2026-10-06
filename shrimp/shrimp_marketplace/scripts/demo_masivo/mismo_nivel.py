# -*- coding: utf-8 -*-
"""Compras al MISMO nivel de la cadena.

- shrimp_marketplace (demo_17_masivo_mismo_nivel.xml): un semillero le
  compra nauplios a otro semillero y un laboratorio le compra postlarva a
  otro laboratorio (una recibida, que el comprador revende como producto
  propio, y otra todavía en tránsito).
- shrimp_packer (demo_10_masivo_mismo_nivel.xml): camaronera → camaronera.
  * Camarón ADULTO con verificación: la camaronera A le vende parte de su
    cosecha a la camaronera B; B lo recibe como producto propio y le revende
    una parte a una empacadora (cadena A → B → empacadora enlazada por el
    movimiento de la compra) y el saldo queda publicado.
  * Juveniles de precría con verificación: la camaronera B2 los recibe como
    lote del producto de origen y los siembra (la siembra consume el lote).
  * Otra compra de adulto entre camaroneras con la verificación todavía
    esperando la aceptación de las partes.

Se ejecuta el ÚLTIMO y con su PROPIA semilla: guarda y restaura el estado de
comun.RNG, así que el resto de la demo sale idéntico byte a byte.
"""
from .comun import MP, RNG, SP, Archivo, D, DT, R, cobro_demo, r2
from .marketplace import (IMGS, SANIDAD_OK, TARIFA_CENTAVOS, certs_producto,
                          disponible, evoluciones, lote_vals, prod_vals, ref_p)
from .verificacion import compra_verificada

SEMILLA_MISMO_NIVEL = 20261003
PREFIJO = "demo_mn_"


def _nombre_corto(P, x):
    return P[x]["nombre"].split(" S.A")[0].split(" Cía")[0]


def _pond(M, px, tipos):
    opciones = [t for t in M["ponds"].get(px, []) if t[2] in tipos]
    return opciones[0] if opciones else None


def construir(M):
    estado = RNG.getstate()
    RNG.seed(SEMILLA_MISMO_NIVEL)
    try:
        _marketplace(M)
        _packer(M)
    finally:
        RNG.setstate(estado)


# ============================================================================
# semillero -> semillero y laboratorio -> laboratorio (compra directa)
# ============================================================================
def _marketplace(M):
    P = M["partners"]
    f = Archivo("shrimp_marketplace", "demo_17_masivo_mismo_nivel.xml",
                "Compras al mismo nivel: semillero -> semillero y laboratorio -> laboratorio\n"
                "     (el comprador revende lo recibido como producto propio).")
    M["archivos"].append(f)

    def producto(xid, kind, px, rol, stage, nombre, qty, precio, off, estado="published",
                 consumido=0.0, **extra):
        p = P[px]
        tq = _pond(M, px, ("tank", "raceway"))
        pr = dict(xid=xid, kind=kind, sellerx=px, rol=rol, species="shrimp_species_vannamei",
                  stage=stage, genetics="shrimp_genetics_spr", name=nombre,
                  size_mg=0.01 if rol == "semillero" else 3.4, surv=r2(RNG.uniform(85, 95)),
                  health=RNG.choice(SANIDAD_OK), qty=qty, uom="uom_millar", price=precio,
                  location=f"{p['ciudad']}, {p['provincia']}", state=estado, off=off,
                  entrega=off + 2, ventana=20, facility=tq[1] if tq else None,
                  pond=tq[0] if tq else None, batch=xid.upper().replace("DEMO_MN_PROD_", "MN-"),
                  fotos=RNG.sample(IMGS, 2), consumido=consumido, lot=xid.replace("_prod_", "_lot_"))
        pr.update(extra)
        return pr

    ntx = [0]

    def compra(pr, buyer, qty, off, estado, resultado=None):
        """Compra directa del mismo nivel: transacción, movimiento, lote o
        producto del comprador y comisión. Devuelve el xid del movimiento."""
        ntx[0] += 1
        n = ntx[0]
        txx, mvx = f"{PREFIJO}tx_{n:04d}", f"{PREFIJO}mv_{n:04d}"
        tipo = {"semillero": "semillero_to_semillero",
                "laboratorio": "laboratorio_to_laboratorio"}[pr["rol"]]
        entrega = off + 3
        v = {"transaction_type": tipo, "product_id": R(pr["xid"]),
             "seller_partner_id": R(ref_p(pr["sellerx"])), "buyer_partner_id": R(ref_p(buyer)),
             "seller_role": pr["rol"], "buyer_role": pr["rol"],
             "location": pr["location"], "state": estado, "transaction_qty": qty,
             "price_unit": pr["price"], "amount_total": r2(qty * pr["price"]),
             "desired_qty": qty, "desired_date": D(entrega), "code": pr["name"],
             "result_product_id": R(resultado["xid"]) if resultado else None,
             "received_date": DT(entrega, 10, 0) if estado == "done" else None}
        if pr["rol"] == "semillero":
            v.update({"sold_qty": qty, "sold_date": D(off), "production_note": pr["name"]})
        f.rec(txx, "shrimp.transaction", v)
        pr["consumido"] += qty
        f.rec(mvx, "shrimp.stock.move", {
            "product_id": R(pr["xid"]), "source_partner_id": R(ref_p(pr["sellerx"])),
            "dest_partner_id": R(ref_p(buyer)), "qty": qty, "transaction_id": R(txx),
            "date": DT(off, 11, 0)})
        rate = TARIFA_CENTAVOS[pr["uom"]]
        f.rec(f"{PREFIJO}chg_{n:04d}", "shrimp.charge", cobro_demo(
            charge_type="commission", transaction_id=R(txx),
            seller_partner_id=R(ref_p(pr["sellerx"])), payer_partner_id=R(ref_p(pr["sellerx"])),
            buyer_partner_id=R(ref_p(buyer)), product_id=R(pr["xid"]), qty=qty,
            uom_id=R(MP + pr["uom"]), rate_cents=rate, invoice_qty=qty,
            unit_amount=rate / 100.0, amount=r2(qty * rate / 100.0),
            currency_id=R("base.USD"), description="Comisión marketplace – %s" % pr["name"],
            date=DT(off, 18, 0)))
        return mvx, entrega

    # ---- semillero -> semillero: nauplios para cubrir un faltante de desove
    sa, sb = M["sem"][1], M["sem"][2]
    f.seccion("Semillero -> semillero: nauplios para cubrir un faltante de desove")
    pa = producto(f"{PREFIJO}prod_sem_a", "sem", sa, "semillero", "shrimp_stage_nauplio",
                  f"Nauplios SPR - excedente de desove ({_nombre_corto(P, sa)})", 180000.0, 0.55, -30)
    f.rec(pa["xid"], "shrimp.product", prod_vals(pa), context="{'skip_initial_lot': True}")
    certs_producto(M, f, pa)
    off = -28
    pb = producto(f"{PREFIJO}prod_sem_b", "res", sb, "semillero", "shrimp_stage_nauplio",
                  f"Nauplios SPR de {_nombre_corto(P, sa)} - reventa", 60000.0, 0.62, off + 3,
                  madura=off + 3, notas=f"Nauplios comprados a {P[sa]['nombre']} (lote {pa['batch']}).")
    f.rec(pb["xid"], "shrimp.product", prod_vals(pb), context="{'skip_initial_lot': True}")
    mv, _e = compra(pa, sb, 60000.0, off, "done", resultado=pb)
    pb["consumido"] = 15000.0           # el semillero B ya revendió una parte fuera de la demo
    lv = lote_vals(pb)
    lv["origin_move_id"] = R(mv)
    f.rec(pb["lot"], "shrimp.stock.lot", lv)
    certs_producto(M, f, pb, fuente=pa)

    # ---- laboratorio -> laboratorio: postlarva para completar un pedido
    la, lb = M["lab"][1], M["lab"][2]
    f.seccion("Laboratorio -> laboratorio: postlarva para completar un pedido")
    qa = producto(f"{PREFIJO}prod_lab_a", "lab", la, "laboratorio", "shrimp_stage_pl12",
                  f"Postlarva PL12 SPR - corrida para terceros ({_nombre_corto(P, la)})",
                  30000.0, 2.4, -20)
    f.rec(qa["xid"], "shrimp.product", prod_vals(qa), context="{'skip_initial_lot': True}")
    certs_producto(M, f, qa)
    off = -18
    qb = producto(f"{PREFIJO}prod_lab_b", "res", lb, "laboratorio", "shrimp_stage_pl12",
                  f"Postlarva PL12 de {_nombre_corto(P, la)} - reventa", 10000.0, 2.7, off + 3,
                  madura=off + 3, notas=f"Postlarva comprada a {P[la]['nombre']} (lote {qa['batch']}).")
    f.rec(qb["xid"], "shrimp.product", prod_vals(qb), context="{'skip_initial_lot': True}")
    mv, _e = compra(qa, lb, 10000.0, off, "done", resultado=qb)
    lv = lote_vals(qb)
    lv["origin_move_id"] = R(mv)
    f.rec(qb["lot"], "shrimp.stock.lot", lv)
    certs_producto(M, f, qb, fuente=qa)
    # Otra compra del mismo laboratorio, todavía en tránsito (sin lote del comprador).
    compra(qa, lb, 5000.0, -2, "confirmed")

    f.seccion("Lotes de origen (disponible final)")
    for pr in (pa, qa):
        f.rec(pr["lot"], "shrimp.stock.lot", lote_vals(pr))


# ============================================================================
# camaronera -> camaronera (con verificación, como todo lote de camaronera)
# ============================================================================
def _packer(M):
    P = M["partners"]
    f = Archivo("shrimp_packer", "demo_10_masivo_mismo_nivel.xml",
                "Compras entre camaroneras: camarón adulto que la compradora revende a una\n"
                "     empacadora (cadena A -> B -> empacadora) y juveniles que la compradora siembra.")
    M["archivos"].append(f)
    cams = [c for c in M["cam"] if not c.startswith("demo_ms_cam_grupo")
            and c not in (M["cam"][7], M["cam"][9])]         # sin las que también son empacadoras
    ca, cb, cj_a, cj_b, cw_a, cw_b = cams[1], cams[2], cams[3], cams[4], cams[5], cams[6]
    emp = SP + sorted(M["listas"])[0]
    planta_emp = f"Planta {P[sorted(M['listas'])[0]]['ciudad']}"

    def adulto(xid, px, qty, off, precio, pond, estado="published", **extra):
        p = P[px]
        pr = dict(xid=xid, kind="eng", sellerx=px, rol="camaronera",
                  species="shrimp_species_vannamei", stage="shrimp_stage_engorde",
                  genetics="shrimp_genetics_spr", name=extra.pop("name"),
                  size_mg=r2(1000.0 / 35 * 1000), surv=r2(RNG.uniform(60, 72)),
                  health=RNG.choice(SANIDAD_OK), qty=qty, uom="uom_libra",
                  presentation="entero", size="size_entero_3040", price=precio,
                  location=f"{p['ciudad']}, {p['provincia']}", state=estado, off=off,
                  entrega=off + 2, ventana=8, facility=pond[1] if pond else None,
                  pond=pond[0] if pond else None, fotos=RNG.sample(IMGS, 3), consumido=0.0,
                  lot=xid.replace("_prod_", "_lot_"),
                  batch=xid.upper().replace("DEMO_MN_PROD_", "MN-"))
        pr.update(extra)
        return pr

    # ---------------------------------------------------------------- A -> B -> empacadora
    f.seccion("Camaronera A le vende parte de su cosecha (adulto) a la camaronera B, con verificación")
    pa = adulto(f"{PREFIJO}prod_cam_a", ca, 20000.0, -40, 2.75, _pond(M, ca, ("earth",)),
                name=f"Cosecha entero 30/40 - venta parcial ({_nombre_corto(P, ca)})")
    f.rec(pa["xid"], "shrimp.product", prod_vals(pa), context="{'skip_initial_lot': True}")
    certs_producto(M, f, pa)
    evoluciones(M, f, pa, 4, 6)

    # La compra A -> B se escribe aparte para poder enlazar el producto que
    # recibe B (result_product_id) y quitar el lote "del producto de origen"
    # que compra_verificada daría al comprador: el adulto entre camaroneras
    # nace como producto PROPIO de B (lo revende), como hace action_receive.
    tmp = Archivo("shrimp_packer", "_tmp", "")
    tx1 = compra_verificada(M, tmp, 1, pa, ref_p(cb), 8000.0, -38, "camaronera_to_camaronera",
                            "adult", PREFIJO, {"escenario": "closed_done",
                                               "planta": f"Recepción en finca {_nombre_corto(P, cb)}"})
    pb = adulto(f"{PREFIJO}prod_cam_b", cb, 8000.0, tx1["entrega"], 2.95, None,
                name=f"Camarón entero 30/40 de {_nombre_corto(P, ca)} - reventa",
                notas=f"Comprado a {P[ca]['nombre']} (lote {pa['batch']}); verificado en la compra.",
                origin_move=f"{PREFIJO}mv_0001")
    pb["batch"] = pa["batch"]
    f.rec(pb["xid"], "shrimp.product", prod_vals(pb), context="{'skip_initial_lot': True}")
    lotc = f"{PREFIJO}lotc_0001"
    for xid, parte in zip(tmp.ids, tmp.partes):
        if xid == lotc:
            continue
        if xid == tx1["xid"]:
            parte = parte.replace(
                "        </record>",
                f'            <field name="result_product_id" ref="{pb["xid"]}"/>\n'
                f'            <field name="seller_role">camaronera</field>\n'
                f'            <field name="buyer_role">camaronera</field>\n'
                "        </record>")
        f.partes.append(parte)
        f.ids.append(xid)
    for m, c in tmp.conteo.items():
        f.conteo[m] = f.conteo.get(m, 0) + c
    f.conteo["shrimp.stock.lot"] -= 1
    certs_producto(M, f, pb, fuente=pa)

    f.seccion("La camaronera B revende a una empacadora parte del camarón que compró")
    compra_verificada(M, f, 2, pb, emp, 6000.0, tx1["entrega"] + 2, "camaronera_to_buyer",
                      "adult", PREFIJO, {"escenario": "closed_done", "planta": planta_emp})

    # ---------------------------------------------------------------- juveniles
    f.seccion("Juveniles de precría entre camaroneras: la compradora los siembra")
    pond_j = _pond(M, cj_a, ("geomembrane",)) or _pond(M, cj_a, ("earth",))
    pj = dict(xid=f"{PREFIJO}prod_cam_juv", kind="juv", sellerx=cj_a, rol="camaronera",
              species="shrimp_species_vannamei", stage="shrimp_stage_juvenil",
              genetics="shrimp_genetics_spr",
              name=f"Juvenil precriado 2,5 g para transferencia ({_nombre_corto(P, cj_a)})",
              size_mg=2500.0, surv=r2(RNG.uniform(80, 90)), health=RNG.choice(SANIDAD_OK),
              qty=1800.0, uom="uom_millar", price=11.5,
              location=f"{P[cj_a]['ciudad']}, {P[cj_a]['provincia']}", state="published",
              off=-25, entrega=-23, ventana=10, facility=pond_j[1], pond=pond_j[0],
              fotos=RNG.sample(IMGS, 3), consumido=0.0, lot=f"{PREFIJO}lot_cam_juv",
              batch="MN-JUV-01")
    f.rec(pj["xid"], "shrimp.product", prod_vals(pj), context="{'skip_initial_lot': True}")
    certs_producto(M, f, pj)
    txj = compra_verificada(M, f, 3, pj, ref_p(cj_b), 1200.0, -24, "camaronera_to_camaronera",
                            "larvae", PREFIJO, {"escenario": "closed_done"})
    destino = _pond(M, cj_b, ("earth",))
    f.rec(f"{PREFIJO}alloc_0001", "shrimp.lot.allocation", {
        "stock_lot_id": R(txj["lot_comprador"]), "pond_id": R(MP + destino[0]),
        "allocated_qty": 1100.0, "allocation_date": D(txj["entrega"]), "state": "allocated",
        "notes": f"Transferencia de juveniles comprados a {P[cj_a]['nombre']}."})

    # ---------------------------------------------------------------- en curso
    f.seccion("Adulto entre camaroneras con la verificación esperando la aceptación de las partes")
    pw = adulto(f"{PREFIJO}prod_cam_w", cw_a, 15000.0, -6, 2.8, _pond(M, cw_a, ("earth",)),
                name=f"Cosecha entero 30/40 ({_nombre_corto(P, cw_a)})")
    f.rec(pw["xid"], "shrimp.product", prod_vals(pw), context="{'skip_initial_lot': True}")
    certs_producto(M, f, pw)
    compra_verificada(M, f, 4, pw, ref_p(cw_b), 5000.0, -5, "camaronera_to_camaronera",
                      "adult", PREFIJO, {"escenario": "waiting",
                                         "planta": f"Recepción en finca {_nombre_corto(P, cw_b)}"})

    f.seccion("Lotes de stock (disponible final)")
    lv = lote_vals(pb)
    lv["origin_move_id"] = R(f"{PREFIJO}mv_0001")
    f.rec(pb["lot"], "shrimp.stock.lot", lv)
    for pr in (pa, pj, pw):
        f.rec(pr["lot"], "shrimp.stock.lot", lote_vals(pr))
    assert disponible(pa) > 0 and disponible(pb) > 0
