# -*- coding: utf-8 -*-
"""shrimp_marketplace: instalaciones, piscinas, lotes y la cadena completa
semillero -> laboratorio -> camaronera, con stock coherente.

Cómo se mantiene la coherencia del stock (igual que lo haría el ORM):

* Los productos se crean con ``skip_initial_lot`` y su lote de origen se
  escribe aparte, con id propio, y con la cantidad disponible FINAL: la
  inicial menos todo lo que consumen las compras confirmadas o completadas.
* Cada compra confirmada o completada tiene su ``shrimp.stock.move`` (con
  ``parent_move_id`` = movimiento que originó el lote consumido, que es lo que
  encadena la trazabilidad).
* Al completarse nace el inventario del comprador: un lote con
  ``origin_move_id`` y, si el comprador es laboratorio, su producto propio
  (el "producto resultado" que el laboratorio larvicultiva y revende).
* Las compras en borrador solo piden lo que aún queda disponible; las
  solicitudes de chequeo activas reservan sin pasarse del disponible.

Convención de referencias: los datos maestros del marketplace y los socios
del registro se escriben SIEMPRE con el módulo delante (MP / UR), para que la
misma función sirva desde los módulos que dependen de este.
"""
from .comun import (MP, RNG, UR, Archivo, B64, D, DOMINIO_LOGIN, DT, E, M2M, cobro_demo,
                    PASSWORD_DEMO, R, geo, pdf_minimo, r2)

IMGS = [MP + f"demo_img_{i}" for i in range(1, 13)]
GEN_VANNAMEI = ["shrimp_genetics_spr", "shrimp_genetics_spr_plus",
                "shrimp_genetics_fast_growth", "shrimp_genetics_high_survival",
                "shrimp_genetics_balanced_performance", "shrimp_genetics_tolerancia_wssv",
                "shrimp_genetics_uniformidad"]
GEN_NOMBRE = {"shrimp_genetics_spr": "SPR", "shrimp_genetics_spr_plus": "SPR Plus",
              "shrimp_genetics_fast_growth": "Crecimiento rápido",
              "shrimp_genetics_high_survival": "Alta supervivencia",
              "shrimp_genetics_balanced_performance": "Desempeño balanceado",
              "shrimp_genetics_tolerancia_wssv": "Tolerante WSSV",
              "shrimp_genetics_uniformidad": "Alta uniformidad",
              "shrimp_genetics_monodon_classic": "Monodon clásica"}
# (xmlid, nombre, piezas medias). Entero: piezas por KILO. Cola: por LIBRA.
TALLAS_ENTERO = [("size_entero_2030", "20/30", 25), ("size_entero_3040", "30/40", 35),
                 ("size_entero_4050", "40/50", 45), ("size_entero_5060", "50/60", 55),
                 ("size_entero_6070", "60/70", 65), ("size_entero_7080", "70/80", 75),
                 ("size_entero_80100", "80/100", 90), ("size_entero_100120", "100/120", 110)]
TALLAS_COLA = [("size_cola_1620", "16/20", 18), ("size_cola_2125", "21/25", 23),
               ("size_cola_2630", "26/30", 28), ("size_cola_3135", "31/35", 33),
               ("size_cola_3640", "36/40", 38), ("size_cola_4150", "41/50", 45),
               ("size_cola_5160", "51/60", 55), ("size_cola_6170", "61/70", 65)]
# Precio de referencia en finca, $/kg entero y $/lb cola (2025-2026).
PRECIO_ENTERO_KG = {"10/20": 8.4, "20/30": 7.0, "30/32": 6.6, "30/40": 6.1, "40/50": 5.3,
                    "50/60": 4.8, "60/70": 4.4, "70/80": 4.0, "80/100": 3.6, "100/120": 3.2}
PRECIO_COLA_LB = {"U/12": 7.8, "U/15": 7.2, "16/20": 6.4, "21/25": 5.6, "26/30": 5.0,
                  "31/35": 4.5, "36/40": 4.1, "41/50": 3.7, "51/60": 3.3, "61/70": 3.0,
                  "71/90": 2.7, "91/110": 2.4}
LB_POR_KG = 2.2046226218
SANIDAD_OK = [
    "PCR negativo a WSSV, IHHNV y AHPND. Sin signos clínicos.",
    "Animales activos, buena respuesta al estrés por salinidad. PCR negativo.",
    "Hepatopáncreas lleno y coloración normal. Sin necrosis visible.",
    "Muestra libre de EHP y Vibrio por debajo del umbral. Lote apto.",
]
OBS_EVOL = ["Biometría de rutina.", "Muestreo con atarraya en tres puntos.",
            "Recambio de agua y control de oxígeno.", "Ajuste de ración por consumo.",
            "Control sanitario: sin novedades.", "Muestreo previo a la venta."]
COMENT_VEND = ["Larva fuerte, buena supervivencia en la siembra.",
               "Cumplieron fecha y cantidad. Repetiremos.",
               "Buen empaque y transporte con oxígeno, llegó en perfecto estado.",
               "Hubo un día de retraso pero avisaron con tiempo.",
               "PCR al día y conteo exacto en la recepción.",
               "La talla vino algo dispareja, el resto bien.",
               "Muy buena atención técnica después de la venta."]
COMENT_COMP = ["Pago puntual y buena coordinación de la recepción.",
               "Cliente serio, recibió a la hora pactada.",
               "Demoró el pago del saldo unos días.",
               "Excelente comunicación durante toda la compra."]
TARIFA_CENTAVOS = {"uom_millar": 1.0, "uom_libra": 0.5}


def ref_p(x):
    """Socio: los del registro llevan UR; los ya calificados se dejan."""
    return x if "." in x else UR + x


def prod_vals(p):
    """Valores de shrimp.product a partir del dict interno. Sirve a todos los
    módulos: todo lo que no es del propio archivo va calificado."""
    v = {
        "name": p["name"], "seller_partner_id": R(ref_p(p["sellerx"])),
        "seller_role": p["rol"], "species_id": R(MP + p["species"]),
        "stage_id": R(MP + p["stage"]),
        "genetics_line_id": R(MP + p["genetics"]) if p.get("genetics") else None,
        "avg_size_mg": p["size_mg"], "survival_rate": p["surv"],
        "health_status": p["health"], "initial_qty": p["qty"], "uom_id": R(MP + p["uom"]),
        "presentation": p.get("presentation"),
        "size_grade_id": R(MP + p["size"]) if p.get("size") else None,
        "price": p["price"], "location": p["location"], "state": p["state"],
        "active": p.get("active", True),
        "available_from": DT(p["off"], 7), "available_to": DT(p["off"] + p.get("ventana", 30), 18),
        "published_date": DT(p["off"], 9, 30) if p["state"] in ("published", "sold") else None,
        "expected_delivery_date": D(p["entrega"]),
        "origin_facility_id": R(MP + p["facility"]) if p.get("facility") else None,
        "origin_pond_id": R(MP + p["pond"]) if p.get("pond") else None,
        "batch_code": p["batch"], "production_date": D(p["off"]),
        "traceability_notes": p.get("notas"),
        "photo_attachment_ids": M2M(p["fotos"]),
        "price_list_id": R(p["price_list"]) if p.get("price_list") else None,
        # campo heredado (archivos sueltos): el expediente del vendedor
        "cert_attachment_ids": M2M([UR + p["adjunto"]]) if p.get("adjunto") else None,
    }
    return v


def disponible(pr):
    return pr["qty"] - pr["consumido"]


def lote_vals(pr):
    disp = r2(max(0.0, disponible(pr)))
    return {"product_id": R(pr["xid"]), "owner_id": R(ref_p(pr["sellerx"])),
            "initial_qty": pr["qty"], "available_qty": disp, "uom_id": R(MP + pr["uom"]),
            "state": "consumed" if disp <= 0 else "available"}


def certs_producto(M, arch, pr, fuente=None):
    """Certificados del producto: los aprobados del vendedor (o, si el producto
    nace de una compra, copia de los del producto de origen, como hace
    _copy_product_certificates)."""
    P = M["partners"]
    if fuente is not None:
        lineas = fuente.get("_certs", [])
    else:
        ok = [l for l in M["cert_lines"].get(pr["sellerx"], []) if l[2] == "approved"]
        lineas = []
        for (lx, cx, _st, ven) in RNG.sample(ok, min(len(ok), RNG.randint(1, 2))):
            emi = min(ven - 365, -10)
            num = f"{cx.split('_')[-1][:5].upper()}-{pr['batch']}"
            estado = RNG.choices(["approved", "pending", "rejected"], [85, 10, 5])[0]
            lineas.append((lx, cx, num, emi, max(ven, emi + 30), P[pr["sellerx"]]["adjunto"], estado))
    pr["_certs"] = lineas
    for k, (lx, cx, num, emi, ven, adj, estado) in enumerate(lineas, 1):
        # Revisión interna: casi todos aprobados (solo esos se publican);
        # algunos pendientes de revisar y alguno rechazado.
        arch.rec(f"{pr['xid']}_pc_{k}", "shrimp.product.certificate.line", {
            "product_id": R(pr["xid"]), "source_user_certificate_line_id": R(UR + lx),
            "certificate_id": R(UR + cx), "number": num, "issue_date": D(emi),
            "expiry_date": D(ven), "attachment_id": R(UR + adj), "active": True,
            "status": estado}, context="{'shrimp_keep_cert_status': True}")


def evoluciones(M, arch, pr, n_min=3, n_max=6):
    """Muestreos del lote entre su inicio y su salida a la venta."""
    P = M["partners"]
    if pr["off"] >= -1 and pr["kind"] != "res":
        return
    k_tot = RNG.randint(n_min, n_max)
    if pr["kind"] == "res":
        ini, fin = pr["off"], min(-1, pr["madura"])
    elif pr["kind"] == "eng":
        ini, fin = pr["off"] - RNG.randint(85, 110), pr["off"] - 1
    elif pr["kind"] == "juv":
        ini, fin = pr["off"] - RNG.randint(25, 40), pr["off"] - 1
    else:
        ini, fin = pr["off"] - RNG.randint(6, 20), pr["off"] - 1
    if fin <= ini:
        fin = ini + k_tot
    etapas = {"sem": ["shrimp_stage_nauplio"],
              "lab": ["shrimp_stage_zoea", "shrimp_stage_mysis", "shrimp_stage_pl5", pr["stage"]],
              "res": ["shrimp_stage_nauplio", "shrimp_stage_zoea", "shrimp_stage_mysis",
                      "shrimp_stage_pl5", "shrimp_stage_pl8", pr["stage"]],
              "juv": ["shrimp_stage_pl12", "shrimp_stage_pl20", "shrimp_stage_juvenil"],
              "eng": ["shrimp_stage_juvenil", "shrimp_stage_engorde"]}[pr["kind"]]
    surv = 99.0
    user = P.get(pr["sellerx"], {}).get("user")
    for k in range(1, k_tot + 1):
        frac = k / k_tot
        off = min(-1, int(ini + (fin - ini) * frac))
        caida = (pr["surv"] - 99.0) / k_tot
        surv = surv + caida * RNG.uniform(0.7, 1.3)
        st = etapas[min(len(etapas) - 1, int(frac * len(etapas) - 0.001))]
        crec = frac if pr["kind"] != "eng" else frac ** 1.3
        arch.rec(f"{pr['xid']}_ev_{k}", "shrimp.product.evolution", {
            "product_id": R(pr["xid"]),
            "date": DT(off, RNG.randint(7, 16), RNG.choice([0, 15, 30, 45])),
            "stage_id": R(MP + st),
            "avg_size_mg": r2(max(0.005, pr["size_mg"] * (0.15 + 0.85 * crec))),
            "survival_rate": r2(pr["surv"] if k == k_tot else max(surv, pr["surv"])),
            "available_qty": r2(pr["qty"] if k < k_tot else max(0, disponible(pr))),
            "health_status": RNG.choice(SANIDAD_OK), "note": RNG.choice(OBS_EVOL),
            "user_id": R(user) if user else None,
        })


def construir(M):
    P = M["partners"]
    f12 = Archivo("shrimp_marketplace", "demo_12_masivo_instalaciones.xml",
                  "Instalaciones y piscinas/tanques de los socios de la demo masiva.")
    f13 = Archivo("shrimp_marketplace", "demo_13_masivo_productos.xml",
                  "Lotes publicados por semilleros, laboratorios y camaroneras, con su\n"
                  "     lote de stock, certificados y evolución productiva.")
    f14 = Archivo("shrimp_marketplace", "demo_14_masivo_transacciones.xml",
                  "Compras semillero->laboratorio y laboratorio->camaronera de punta a\n"
                  "     punta: movimientos, lotes del comprador, siembras en piscina,\n"
                  "     solicitudes de chequeo, comisiones y reseñas de las dos partes.")
    f15 = Archivo("shrimp_marketplace", "demo_15_masivo_accesos.xml",
                  "Cuentas de portal de los socios de la demo original y claves de API.")
    M["archivos"] += [f12, f13, f14, f15]

    # ================================================================ instalaciones
    # M["ponds"][partner] = [(pond, facility, tipo, capacidad, nombre)]
    M["fac"], M["ponds"] = {}, {}
    for px in M["sem"] + M["lab"] + M["cam"]:
        p = P[px]
        rol, ciudad = p["rol"], p["ciudad"]
        corto = px.replace("demo_ms_", "")
        M["fac"][px], M["ponds"][px] = [], []
        if rol == "semillero":
            plan = [("hatchery", f"Maduración {ciudad}", 5), ("office", f"Oficina comercial {ciudad}", 0)]
        elif rol == "laboratorio":
            plan = [("laboratory", f"Planta de larvicultura {ciudad}", 7),
                    ("warehouse", f"Bodega de alimento y artemia {ciudad}", 0)]
        else:
            n_fincas = 2 if px.startswith("demo_ms_cam_grupo") else RNG.choice([1, 1, 2, 2, 3])
            corto_nom = p["nombre"].split(" S.A")[0].split(" Cía")[0]
            plan = [("farm", f"Finca {corto_nom} - Sector {s}", RNG.randint(4, 7))
                    for s in "ABC"[:n_fincas]]
            plan.append((RNG.choice(["office", "warehouse"]), f"Oficina y bodega {ciudad}", 0))
        for k, (tipo, nombre, piscinas) in enumerate(plan, 1):
            fx = f"demo_ms_fac_{corto}_{k}"
            lat, lon = geo(ciudad, 0.04)
            f12.rec(fx, "shrimp.partner.facility", {
                "partner_id": R(UR + px), "name": nombre,
                "code": f"{corto[:3].upper()}{corto[-2:]}-{k:02d}", "facility_type": tipo,
                "address": f"{RNG.choice(['Km', 'Recinto', 'Sector'])} {RNG.randint(1, 30)} - {ciudad}",
                "city": ciudad, "province": p["provincia"], "country_id": R("base.ec"),
                "latitude": lat, "longitude": lon, "active": True,
                "notes": {"hatchery": "Sala de maduración con fotoperiodo controlado y sala de desove.",
                          "laboratory": "Larvicultura en tanques de fibra con aireación y calefacción.",
                          "farm": "Piscinas de tierra abastecidas desde el estero; bombas de 24 pulgadas.",
                          }.get(tipo, "Bodega de insumos y oficina administrativa."),
            })
            M["fac"][px].append((fx, tipo))
            for j in range(1, piscinas + 1):
                pond = f"demo_ms_pond_{corto}_{k}_{j}"
                if tipo == "hatchery":
                    vals = {"name": f"Tanque de {'maduración' if j <= 3 else 'desove'} {j:02d}",
                            "pond_type": "tank", "capacity_mode": "volume",
                            "manual_volume_m3": r2(RNG.uniform(8, 24)),
                            "max_stock_units": RNG.randint(80, 260)}
                elif tipo == "laboratory":
                    if j <= 5:
                        vals = {"name": f"Tanque larvario {j:02d}", "pond_type": "tank",
                                "capacity_mode": "volume", "manual_volume_m3": r2(RNG.uniform(20, 40)),
                                "max_stock_units": RNG.randint(4000, 8000)}
                    else:
                        vals = {"name": f"Raceway {j - 5:02d}", "pond_type": "raceway",
                                "capacity_mode": "dimensions", "length_m": RNG.randint(18, 30),
                                "width_m": RNG.choice([2.0, 2.5, 3.0]), "depth_m": 1.1,
                                "max_stock_units": RNG.randint(1500, 3000)}
                else:
                    if j == 1 and RNG.random() < 0.5:
                        vals = {"name": f"Precriadero {k:02d}", "pond_type": "geomembrane",
                                "capacity_mode": "dimensions", "length_m": RNG.randint(40, 70),
                                "width_m": RNG.randint(20, 35), "depth_m": 1.2,
                                "max_stock_units": RNG.randint(1500, 3000)}
                    else:
                        lg, an = RNG.randint(160, 420), RNG.randint(70, 190)
                        vals = {"name": f"Piscina {j + (k - 1) * 10:02d}", "pond_type": "earth",
                                "capacity_mode": "dimensions", "length_m": lg, "width_m": an,
                                "depth_m": r2(RNG.uniform(0.9, 1.5)),
                                # 10-14 PL/m2, en millares
                                "max_stock_units": round(lg * an * RNG.uniform(10, 14) / 1000)}
                vals.update({
                    "partner_id": R(UR + px), "facility_id": R(fx), "code": f"P-{k}{j:02d}",
                    "location": f"{nombre} · módulo {chr(64 + (j - 1) // 3 + 1)}",
                    "active": not (tipo == "farm" and j == piscinas and RNG.random() < 0.08),
                })
                if vals["capacity_mode"] == "dimensions":
                    vals["usable_volume_m3"] = r2(vals["length_m"] * vals["width_m"] * vals["depth_m"] * 0.9)
                if RNG.random() < 0.25:
                    vals["notes"] = RNG.choice(["Fondo secado y encalado en el último ciclo.",
                                                "Compuerta de salida reparada.",
                                                "Aireadores de paleta instalados (4 x 2 HP).",
                                                "Monitoreo de oxígeno con sonda continua."])
                f12.rec(pond, "shrimp.partner.pond", vals)
                if vals["active"]:
                    M["ponds"][px].append((pond, fx, vals["pond_type"],
                                           vals.get("max_stock_units", 0), vals["name"]))

    # ================================================================ productos
    productos = {}
    orden = []

    def nuevo_prod(**kw):
        kw.setdefault("consumido", 0.0)
        kw.setdefault("lot", kw["xid"].replace("_prod_", "_lot_"))
        productos[kw["xid"]] = kw
        orden.append(kw["xid"])
        return kw

    # ---- semilleros: nauplios
    n = 0
    for px in M["sem"]:
        p = P[px]
        tanques = [t for t in M["ponds"][px] if t[2] == "tank"]
        for _k in range(RNG.randint(6, 8)):
            n += 1
            off = -RNG.randint(8, 350)
            stage = RNG.choices(["shrimp_stage_nauplio", "shrimp_stage_zoea", "shrimp_stage_mysis"], [80, 10, 10])[0]
            gen = RNG.choice(GEN_VANNAMEI)
            tq = RNG.choice(tanques)
            estado, activo = "published", True
            if RNG.random() < 0.07:
                estado, off = "draft", RNG.randint(3, 25)        # corrida programada
            elif RNG.random() < 0.06:
                estado, activo = "cancel", RNG.random() < 0.5    # PCR positivo, se descarta
            etiqueta = {"shrimp_stage_nauplio": "Nauplios", "shrimp_stage_zoea": "Zoea",
                        "shrimp_stage_mysis": "Mysis"}[stage]
            nuevo_prod(
                xid=f"demo_ms_prod_sem_{n:03d}", kind="sem", sellerx=px,
                rol="semillero", species="shrimp_species_vannamei", stage=stage, genetics=gen,
                name=f"{etiqueta} L. vannamei {GEN_NOMBRE[gen]} - Corrida {n:03d}",
                size_mg=r2(RNG.uniform(0.004, 0.02) if stage == "shrimp_stage_nauplio" else RNG.uniform(0.05, 0.3)),
                surv=r2(RNG.uniform(88, 97)), health=RNG.choice(SANIDAD_OK),
                qty=RNG.randrange(60000, 240000, 500), uom="uom_millar",
                price=r2(RNG.uniform(0.42, 0.78)), location=f"{p['ciudad']}, {p['provincia']}",
                state=estado, active=activo, off=off, entrega=off + RNG.randint(1, 3), ventana=12,
                facility=tq[1], pond=tq[0], batch=f"MAD-{px[-2:]}-{n:03d}",
                fotos=RNG.sample(IMGS, 2),
                notas="Reproductores SPF importados; desove y eclosión en sala propia." if RNG.random() < 0.5 else None)

    # ---- laboratorios: postlarva propia
    n = 0
    for px in M["lab"]:
        p = P[px]
        tanques = [t for t in M["ponds"][px] if t[2] in ("tank", "raceway")]
        for _k in range(RNG.randint(4, 6)):
            n += 1
            off = -RNG.randint(5, 340)
            stage = RNG.choices(["shrimp_stage_pl8", "shrimp_stage_pl10", "shrimp_stage_pl12",
                                 "shrimp_stage_pl15", "shrimp_stage_pl20"], [8, 30, 35, 17, 10])[0]
            pl = int(stage.split("pl")[-1])
            tq = RNG.choice(tanques)
            estado, activo = "published", True
            if RNG.random() < 0.08:
                estado, off = "draft", RNG.randint(4, 20)
            elif RNG.random() < 0.05:
                estado, activo = "cancel", False
            species = "shrimp_species_vannamei" if RNG.random() > 0.04 else "shrimp_species_monodon"
            gen = "shrimp_genetics_monodon_classic" if species.endswith("monodon") else RNG.choice(GEN_VANNAMEI)
            nuevo_prod(
                xid=f"demo_ms_prod_lab_{n:03d}", kind="lab", sellerx=px,
                rol="laboratorio", species=species, stage=stage, genetics=gen,
                name=f"Postlarva PL{pl} {'Vannamei' if 'vannamei' in species else 'Monodon'} - Corrida {px[-2:]}{n:03d}",
                size_mg=r2(0.8 + pl * RNG.uniform(0.18, 0.32)), surv=r2(RNG.uniform(70, 92)),
                health=RNG.choice(SANIDAD_OK), qty=RNG.randrange(8000, 45000, 100), uom="uom_millar",
                price=r2(RNG.uniform(1.6, 3.2) + (pl - 10) * 0.04),
                location=f"{p['ciudad']}, {p['provincia']}", state=estado, active=activo,
                off=off, entrega=off + RNG.randint(2, 6), ventana=15,
                facility=tq[1], pond=tq[0], batch=f"LAR-{px[-2:]}-{n:03d}", fotos=RNG.sample(IMGS, 2))

    # ---- camaroneras: juveniles de precría y camarón de engorde en oferta
    n = 0
    M["prod_eng_mkt"] = []
    for px in M["cam"]:
        p = P[px]
        piscinas = [t for t in M["ponds"][px] if t[2] in ("earth", "geomembrane")]
        if not piscinas:
            continue
        for _k in range(RNG.randint(2, 4)):
            n += 1
            pond = RNG.choice(piscinas)
            juvenil = pond[2] == "geomembrane" or RNG.random() < 0.2
            off = -RNG.randint(1, 45)
            estado = RNG.choices(["published", "draft", "cancel"], [82, 12, 6])[0]
            if estado == "draft":
                off = RNG.randint(5, 40)
            xid = f"demo_ms_prod_cam_{n:03d}"
            comun = dict(xid=xid, sellerx=px, rol="camaronera",
                         species="shrimp_species_vannamei", genetics=RNG.choice(GEN_VANNAMEI),
                         health=RNG.choice(SANIDAD_OK), location=f"{p['ciudad']}, {p['provincia']}",
                         state=estado, active=estado != "cancel", off=off,
                         facility=pond[1], pond=pond[0], fotos=RNG.sample(IMGS, 3))
            if juvenil:
                g = RNG.uniform(1.0, 3.5)
                nuevo_prod(kind="juv", stage="shrimp_stage_juvenil",
                           name=f"Juvenil precriado {g:.1f} g - {pond[4]} ({p['ciudad']})",
                           size_mg=r2(g * 1000), surv=r2(RNG.uniform(75, 90)),
                           qty=RNG.randrange(300, 2500, 10), uom="uom_millar",
                           price=r2(RNG.uniform(8, 15)), entrega=off + RNG.randint(3, 10),
                           batch=f"JUV-{px[-2:]}-{n:03d}", **comun)
            else:
                pres = RNG.choices(["entero", "cola"], [70, 30])[0]
                tl = RNG.choice(TALLAS_ENTERO if pres == "entero" else TALLAS_COLA)
                if pres == "entero":
                    gramos, precio = 1000.0 / tl[2], PRECIO_ENTERO_KG[tl[1]] / LB_POR_KG
                else:
                    gramos, precio = 453.6 / tl[2] / 0.65, PRECIO_COLA_LB[tl[1]] * 0.62
                lote = f"COS-{px[-2:]}-{n:03d}"
                nuevo_prod(kind="eng", stage="shrimp_stage_engorde",
                           name=f"Camarón {pres} {tl[1]} - {pond[4]} ({lote})",
                           size_mg=r2(gramos * 1000), surv=r2(RNG.uniform(55, 78)),
                           qty=RNG.randrange(6000, 48000, 50), uom="uom_libra",
                           presentation=pres, size=tl[0],
                           price=r2(precio * RNG.uniform(0.95, 1.06)),
                           entrega=off + RNG.randint(1, 6), ventana=8, batch=lote, **comun)
                if estado == "published":
                    M["prod_eng_mkt"].append(MP + xid)

    # ================================================================ transacciones
    txs, allocs, reviews, checks, res_prods = [], [], [], [], []
    lot_comprador = {}
    M["siembras"] = []
    ntx = [0]
    labs_compra = M["lab"] + [MP + x for x in ("demo_p_biomarino", "demo_p_genetica_se", "demo_p_aqualab_gye")]
    cams_compra = [c for c in M["cam"] if not c.startswith("demo_ms_cam_grupo")]

    def estado_tx(off):
        # Lo viejo está cerrado; lo reciente puede seguir en tránsito.
        if off < -20:
            return RNG.choices(["done", "cancel"], [88, 12])[0]
        return RNG.choices(["done", "confirmed", "cancel"], [40, 50, 10])[0]

    def registrar_tx(tipo, pr, buyer, qty, off, estado):
        ntx[0] += 1
        tx = {"xid": f"demo_ms_tx_{ntx[0]:04d}", "tipo": tipo, "prod": pr, "buyer": buyer,
              "qty": qty, "off": off, "estado": estado, "entrega": off + RNG.randint(1, 4),
              "precio": pr["price"]}
        if estado == "done" and tx["entrega"] > -1:
            if off <= -2:
                tx["entrega"] = -1
            else:
                tx["estado"] = estado = "confirmed"
        txs.append(tx)
        if estado in ("confirmed", "done"):
            pr["consumido"] += qty
            tx["move"] = {"xid": tx["xid"].replace("_tx_", "_mv_"), "parent": pr.get("origin_move")}
        if estado == "done":
            if RNG.random() < 0.85:
                reviews.append((tx, "to_seller"))
            if RNG.random() < 0.6:
                reviews.append((tx, "to_buyer"))
        return tx

    def recibir(tx):
        """El comprador confirma la recepción (action_receive)."""
        pr, buyer = tx["prod"], tx["buyer"]
        lx = tx["xid"].replace("_tx_", "_lotc_")
        if tx["tipo"] == "semillero_to_laboratorio" and "." not in buyer:
            # El laboratorio recibe nauplios: nace su producto propio y los larvicultiva.
            pl = RNG.choice([10, 12, 12, 15])
            bp = P[buyer]
            madura = tx["entrega"] + 18 + (pl - 10)
            tq = RNG.choice([t for t in M["ponds"][buyer] if t[2] in ("tank", "raceway")])
            vend = P[pr["sellerx"]]["nombre"].split(" ")
            rp = nuevo_prod(
                xid=tx["xid"].replace("_tx_", "_prod_res_"), kind="res", sellerx=buyer,
                rol="laboratorio", species="shrimp_species_vannamei",
                stage=f"shrimp_stage_pl{pl}" if madura < -2 else "shrimp_stage_zoea",
                genetics=pr["genetics"],
                name=f"Postlarva PL{pl} de {vend[0]} {vend[1]} - Corrida {tx['xid'][-4:]}",
                size_mg=r2(0.8 + pl * RNG.uniform(0.2, 0.3)), surv=r2(RNG.uniform(45, 70)),
                health=RNG.choice(SANIDAD_OK), qty=tx["qty"], uom="uom_millar",
                price=r2(RNG.uniform(1.7, 3.0)), location=f"{bp['ciudad']}, {bp['provincia']}",
                state="published" if madura < -2 else "draft", off=tx["entrega"],
                entrega=madura, ventana=25, facility=tq[1], pond=tq[0],
                batch=f"LAR-{buyer[-2:]}-{tx['xid'][-4:]}", fotos=pr["fotos"],
                notas=f"Nauplios comprados a {P[pr['sellerx']]['nombre']} (lote {pr['batch']}).",
                lot=lx, origin_move=tx["move"]["xid"], madura=madura, fuente=pr)
            tx["result"] = rp
            res_prods.append(rp)
            lot_comprador[tx["xid"]] = {"xid": lx, "prod": rp, "owner": buyer, "res": True}
            allocs.append({"lot": lx, "pond": tq[0], "off": tx["entrega"], "rp": rp,
                           "frac": RNG.uniform(0.3, 0.38),
                           "estado": "released" if madura < -2 else "allocated"})
            return
        lot_comprador[tx["xid"]] = {"xid": lx, "prod": pr, "owner": buyer, "qty": tx["qty"]}
        if tx["tipo"] == "laboratorio_to_camaronera" and "." not in buyer:
            piscinas = [t for t in M["ponds"][buyer] if t[2] in ("earth", "geomembrane")]
            k = min(RNG.choice([1, 1, 1, 2]), len(piscinas))
            restante = tx["qty"]
            for i, pz in enumerate(RNG.sample(piscinas, k)):
                parte = r2(restante if i == k - 1 else restante * RNG.uniform(0.45, 0.55))
                restante -= parte
                est = ("released" if tx["entrega"] < -110 else
                       RNG.choices(["allocated", "draft", "cancelled"], [85, 8, 7])[0])
                allocs.append({"lot": lx, "pond": pz[0], "off": tx["entrega"], "qty": parte,
                               "estado": est})
                if est in ("allocated", "released"):
                    M["siembras"].append({"cam": buyer, "pond": pz[0], "fac": pz[1], "nombre": pz[4],
                                          "off": tx["entrega"], "qty": parte, "estado": est,
                                          "prod": pr})

    # ---- semillero -> laboratorio
    for x in list(orden):
        pr = productos[x]
        if pr["kind"] != "sem" or pr["state"] != "published":
            continue
        for _ in range(RNG.randint(1, 3)):
            qty = RNG.randrange(8000, 60000, 500)
            if pr["consumido"] + qty > pr["qty"] * 0.9:
                break
            off = pr["off"] + RNG.randint(0, 6)
            if off >= 0:
                continue
            tx = registrar_tx("semillero_to_laboratorio", pr, RNG.choice(labs_compra), qty, off,
                              estado_tx(off))
            if tx["estado"] == "done":
                recibir(tx)

    # ---- algunas corridas se venden completas: el último laboratorio se lleva el saldo
    for x in list(orden):
        pr = productos[x]
        if (pr["kind"] == "sem" and pr["state"] == "published" and pr["off"] < -40
                and pr["consumido"] > 0 and RNG.random() < 0.3):
            off = pr["off"] + RNG.randint(7, 12)
            tx = registrar_tx("semillero_to_laboratorio", pr, RNG.choice(M["lab"]),
                              round(disponible(pr), 2), off, "done")
            recibir(tx)

    # ---- laboratorio -> camaronera (postlarva propia y la larvicultivada)
    for x in list(orden):
        pr = productos[x]
        if pr["kind"] not in ("lab", "res") or pr["state"] != "published":
            continue
        base = pr["madura"] if pr["kind"] == "res" else pr["off"]
        tope = 0.55 if pr["kind"] == "res" else 0.85
        for _ in range(RNG.randint(1, 4)):
            qty = RNG.randrange(600, 4200, 50)
            if pr["consumido"] + qty > pr["qty"] * tope:
                break
            off = base + RNG.randint(0, 8)
            if off >= 0:
                continue
            buyer = RNG.choice(cams_compra + [MP + "demo_p_rio_chone", MP + "demo_p_balao_grande"])
            tx = registrar_tx("laboratorio_to_camaronera", pr, buyer, qty, off, estado_tx(off))
            if tx["estado"] == "done":
                recibir(tx)

    # ---- solicitudes de chequeo (las aprobadas ejecutan su compra)
    candidatos = [productos[x] for x in orden if productos[x]["kind"] in ("sem", "lab", "res")
                  and productos[x]["state"] == "published"]
    for ncr, pr in enumerate(RNG.sample(candidatos, min(95, len(candidatos))), 1):
        es_sem = pr["kind"] == "sem"
        buyer = RNG.choice(M["lab"] if es_sem else cams_compra)
        estado = RNG.choices(["requested", "under_review", "approved", "rejected", "cancelled"],
                             [16, 14, 40, 15, 15])[0]
        libre = disponible(pr) - pr.get("reservado", 0)
        qty = RNG.randrange(5000, 30000, 500) if es_sem else RNG.randrange(500, 2500, 50)
        off = min(-1, (pr.get("madura") or pr["off"]) + RNG.randint(0, 10))
        cr = {"xid": f"demo_ms_chk_{ncr:03d}", "prod": pr, "buyer": buyer, "qty": qty,
              "estado": estado, "off": off}
        if estado in ("requested", "under_review"):
            cr["off"] = -RNG.randint(0, 6)
            if qty > libre * 0.5:
                cr["qty"] = qty = int(libre * 0.3 / 50) * 50
            if qty <= 0:
                cr["estado"], cr["qty"] = "cancelled", 500
            else:
                pr["reservado"] = pr.get("reservado", 0) + qty
        elif estado == "approved":
            if qty > libre * 0.6:
                cr["estado"] = "rejected"
            else:
                tipo = "semillero_to_laboratorio" if es_sem else "laboratorio_to_camaronera"
                tx = registrar_tx(tipo, pr, buyer, qty, off, "done" if off < -6 else "confirmed")
                cr["tx"] = tx
                if tx["estado"] == "done":
                    recibir(tx)
        checks.append(cr)

    # ---- compras en borrador (carrito sin confirmar) sobre lo que queda
    libres = [p for p in candidatos if disponible(p) - p.get("reservado", 0) > 3000]
    for pr in RNG.sample(libres, min(20, len(libres))):
        es_sem = pr["kind"] == "sem"
        libre = disponible(pr) - pr.get("reservado", 0)
        qty = min(int(libre * 0.4 / 50) * 50,
                  RNG.randrange(2000, 20000, 100) if es_sem else RNG.randrange(400, 2000, 50))
        tipo = "semillero_to_laboratorio" if es_sem else "laboratorio_to_camaronera"
        registrar_tx(tipo, pr, RNG.choice(M["lab"] if es_sem else cams_compra), qty,
                     -RNG.randint(0, 3), "draft")

    # ---- estado final de cada producto según su stock
    for pr in productos.values():
        if pr["state"] == "published" and disponible(pr) <= 0.0001:
            pr["state"] = "sold"

    # ================================================================ escritura 13
    f13.seccion("Comisión del marketplace por unidad de medida (centavos)")
    for u, c in (("uom_millar", TARIFA_CENTAVOS["uom_millar"]),
                 ("uom_libra", TARIFA_CENTAVOS["uom_libra"]), ("uom_unidad", 0.2)):
        # solo si nadie la configuró todavía
        f13.fn_write("shrimp.uom", [MP + u], [("commission_cents", "=", 0)], {"commission_cents": c})

    for pr in productos.values():
        if RNG.random() < 0.3:
            pr["adjunto"] = P[pr["sellerx"]].get("adjunto")
    origen = [x for x in orden if productos[x]["kind"] != "res"]
    f13.seccion("Productos de origen (semillero, laboratorio, camaronera)")
    for x in origen:
        f13.rec(x, "shrimp.product", prod_vals(productos[x]), context="{'skip_initial_lot': True}")
    f13.seccion("Lote de stock de origen de cada producto (disponible final)")
    for x in origen:
        f13.rec(productos[x]["lot"], "shrimp.stock.lot", lote_vals(productos[x]))
    f13.seccion("Certificados de cada producto (heredados del vendedor)")
    for x in origen:
        certs_producto(M, f13, productos[x])
    f13.seccion("Evolución productiva (muestreos)")
    for x in origen:
        evoluciones(M, f13, productos[x])

    # ================================================================ escritura 14
    f14.seccion("Compras: producto del laboratorio, transacción, movimiento y lote del comprador")
    for tx in txs:
        pr = tx["prod"]
        rp = tx.get("result")
        if rp:
            f14.rec(rp["xid"], "shrimp.product", prod_vals(rp), context="{'skip_initial_lot': True}")
        v = {"transaction_type": tx["tipo"], "product_id": R(pr["xid"]),
             "seller_partner_id": R(ref_p(pr["sellerx"])), "buyer_partner_id": R(ref_p(tx["buyer"])),
             "location": pr["location"], "state": tx["estado"], "transaction_qty": tx["qty"],
             "price_unit": tx["precio"], "amount_total": r2(tx["qty"] * tx["precio"]),
             "desired_qty": tx["qty"], "desired_date": D(tx["entrega"]),
             "result_product_id": R(rp["xid"]) if rp else None}
        if tx["tipo"] == "semillero_to_laboratorio":
            v.update({"sold_qty": tx["qty"], "sold_date": D(tx["off"]), "production_note": pr["name"]})
        else:
            v["code"] = pr["name"]
        if tx["estado"] == "done" and RNG.random() < 0.15:
            fx = tx["xid"] + "_factura"
            f14.rec(fx, "ir.attachment", {
                "name": f"Factura_{tx['xid'][-4:]}.pdf", "type": "binary", "mimetype": "application/pdf",
                "datas": B64(pdf_minimo("Factura de venta", [
                    f"Vendedor: {P[pr['sellerx']]['nombre']}", f"Producto: {pr['name']}",
                    f"Cantidad: {tx['qty']} millares - Precio unitario: {tx['precio']} USD",
                    f"Total: {r2(tx['qty'] * tx['precio'])} USD"]))})
            v["invoice_attachment_ids"] = M2M([fx])
        f14.rec(tx["xid"], "shrimp.transaction", v)
        mv = tx.get("move")
        if mv:
            f14.rec(mv["xid"], "shrimp.stock.move", {
                "product_id": R(pr["xid"]), "source_partner_id": R(ref_p(pr["sellerx"])),
                "dest_partner_id": R(ref_p(tx["buyer"])), "qty": tx["qty"],
                "parent_move_id": R(mv["parent"]) if mv["parent"] else None,
                "transaction_id": R(tx["xid"]),
                "date": DT(tx["off"], RNG.randint(8, 17), RNG.choice([0, 20, 40]))})
        lc = lot_comprador.get(tx["xid"])
        if lc and lc.get("res"):
            rp = lc["prod"]
            vals = lote_vals(rp)
            vals["origin_move_id"] = R(mv["xid"])
            f14.rec(lc["xid"], "shrimp.stock.lot", vals)
            certs_producto(M, f14, rp, fuente=rp["fuente"])
        elif lc:
            f14.rec(lc["xid"], "shrimp.stock.lot", {
                "product_id": R(pr["xid"]), "owner_id": R(ref_p(lc["owner"])),
                "origin_move_id": R(mv["xid"]), "initial_qty": lc["qty"],
                "available_qty": lc["qty"], "uom_id": R(MP + pr["uom"]), "state": "available"})

    f14.seccion("Siembras: asignación de lotes a piscinas y tanques")
    for na, a in enumerate(allocs, 1):
        if "qty" not in a:
            a["qty"] = r2(min(max(0.0, disponible(a["rp"])), a["rp"]["qty"] * a["frac"]))
            if a["qty"] <= 0:
                continue
        f14.rec(f"demo_ms_alloc_{na:04d}", "shrimp.lot.allocation", {
            "stock_lot_id": R(a["lot"]), "pond_id": R(a["pond"]), "allocated_qty": a["qty"],
            "allocation_date": D(a["off"]), "state": a["estado"],
            "notes": {"allocated": "Siembra directa con aclimatación de 4 horas.",
                      "released": "Ciclo cerrado: asignación liberada al cosechar.",
                      "draft": "Siembra programada; pendiente de preparar la piscina.",
                      "cancelled": "Se canceló la siembra por bloom de algas en la piscina."}[a["estado"]]})

    f14.seccion("Solicitudes de chequeo")
    for cr in checks:
        pr = cr["prod"]
        v = {"product_id": R(pr["xid"]), "seller_partner_id": R(ref_p(pr["sellerx"])),
             "buyer_partner_id": R(UR + cr["buyer"]), "qty": cr["qty"], "state": cr["estado"],
             "check_fee": RNG.choice([40.0, 60.0, 75.0, 90.0, 120.0]), "currency_id": R("base.USD"),
             "note": {"requested": "Pido conteo y PCR antes de confirmar la compra.",
                      "under_review": "Técnico asignado; muestreo en el tanque de cosecha.",
                      "approved": "Conteo y sanidad conformes. Se ejecuta la compra.",
                      "rejected": "El conteo salió 18 % por debajo de lo publicado.",
                      "cancelled": "El comprador desistió antes del muestreo."}[cr["estado"]]}
        if cr["estado"] in ("approved", "rejected"):
            v["reviewed_by"] = R(UR + "demo_ms_user_int_aprobador_certificados")
            v["reviewed_date"] = DT(cr["off"], RNG.randint(9, 17), 0)
        if cr.get("tx"):
            v["transaction_id"] = R(cr["tx"]["xid"])
            v["source_lot_id"] = R(pr["lot"])
            if cr["tx"].get("result"):
                v["result_product_id"] = R(cr["tx"]["result"]["xid"])
        f14.rec(cr["xid"], "shrimp.check.request", v)

    f14.seccion("Comisiones del marketplace (una por compra confirmada)")
    for tx in txs:
        if tx["estado"] not in ("confirmed", "done"):
            continue
        pr = tx["prod"]
        rate = TARIFA_CENTAVOS[pr["uom"]]
        f14.rec(tx["xid"].replace("_tx_", "_chg_"), "shrimp.charge", cobro_demo(
            charge_type="commission",
            transaction_id=R(tx["xid"]), seller_partner_id=R(ref_p(pr["sellerx"])),
            payer_partner_id=R(ref_p(pr["sellerx"])),
            buyer_partner_id=R(ref_p(tx["buyer"])), product_id=R(pr["xid"]),
            qty=tx["qty"], uom_id=R(MP + pr["uom"]), rate_cents=rate,
            invoice_qty=tx["qty"], unit_amount=rate / 100.0,
            amount=r2(tx["qty"] * rate / 100.0), currency_id=R("base.USD"),
            description="Comisión marketplace – %s" % pr["name"],
            date=DT(tx["off"], 18, 0)))

    f14.seccion("Reseñas de las dos partes")
    escribir_resenas(f14, reviews)

    f14.seccion("Evolución de la larvicultura en los productos del laboratorio")
    for rp in res_prods:
        evoluciones(M, f14, rp, 5, 8)

    # ================================================================ escritura 15
    f15.seccion("Cuentas de portal de los socios de la demo original")
    viejos = [("semillero", ["demo_p_semillas_pacifico", "demo_p_larvatec", "demo_p_aquasemilla",
                             "demo_p_costa_azul", "demo_p_biolarva_sur"]),
              ("laboratorio", ["demo_p_biomarino", "demo_p_genetica_se", "demo_p_aqualab_gye",
                               "demo_p_marino_pacifico", "demo_p_nutrilarva"]),
              ("camaronera", ["demo_p_rio_chone", "demo_p_ecuacamaron", "demo_p_balao_grande",
                              "demo_p_el_manglar", "demo_p_santa_rosa_cam"])]
    for rol, lista in viejos:
        for k, px in enumerate(lista, 1):
            login = f"{rol}.{k:02d}@{DOMINIO_LOGIN}"
            ux = f"demo_ms_user_{px.replace('demo_p_', '')}"
            f15.rec(ux, "res.users", {
                "partner_id": R(px), "login": login, "password": PASSWORD_DEMO,
                "company_id": R("base.main_company"), "company_ids": M2M(["base.main_company"]),
                "group_ids": M2M(["base.group_portal"]), "tz": "America/Guayaquil"})
            M["usuarios"].append((login, ux, px, rol, "shrimp_marketplace"))

    f15.seccion("Los usuarios internos del registro operan el back office del marketplace")
    for ux in ("demo_ms_user_int_aprobador_certificados", "demo_ms_user_int_soporte_socios"):
        f15.fn_write("res.users", [UR + ux], [], {"group_ids": E("[(4, ref('shrimp_marketplace.group_shrimp_user'))]")})

    f15.seccion("Certificados de producto de la demo original: revisados (solo lo aprobado se publica)")
    import xml.etree.ElementTree as ET
    from .comun import ADDONS
    viejos_pc = [r.get("id") for r in ET.parse(
        ADDONS / "shrimp_marketplace/demo/demo_07_product_certificates.xml").getroot().iter("record")]
    reparto = {"approved": [], "rejected": []}
    for x in viejos_pc:
        d = RNG.choices(["approved", "rejected", None], [92, 3, 5])[0]
        if d:
            reparto[d].append(MP + x)
    for d, ids in reparto.items():
        for i in range(0, len(ids), 60):
            f15.fn_write("shrimp.product.certificate.line", ids[i:i + 60], [("status", "=", "pending")],
                         {"status": d})

    f15.seccion("Claves de API (integraciones con ERP, BI y apps de campo)")
    portal = [f"{mod}.{u}" for (_l, u, _p, rol, mod) in M["usuarios"] if rol != "interno"]
    etiquetas = ["Integración ERP", "Tablero BI", "App de campo", "Sincronización contable",
                 "Inventario de bodega", "Portal del grupo"]
    nk = 0
    for uref in RNG.sample(portal, 52):
        nk += 1
        usado = RNG.random() < 0.8
        f15.rec(f"demo_ms_apikey_{nk:03d}", "shrimp.api.key", {
            "name": RNG.choice(etiquetas), "user_id": R(uref),
            "scope": RNG.choices(["read", "write"], [55, 45])[0],
            "active": RNG.random() > 0.1,
            "last_used": DT(-RNG.randint(1, 90), RNG.randint(6, 20), RNG.randint(0, 59)) if usado else None,
            "call_count": RNG.randint(20, 18000) if usado else 0})
    for k, (nombre, uref) in enumerate([
            ("Integración contable central", "base.user_admin"),
            ("Migración de catálogo", "base.user_admin"),
            ("Auditoría de certificados", UR + "demo_ms_user_int_aprobador_certificados"),
            ("Soporte: consulta de lotes", UR + "demo_ms_user_int_soporte_socios")], 1):
        nk += 1
        f15.rec(f"demo_ms_apikey_{nk:03d}", "shrimp.api.key", {
            "name": nombre, "user_id": R(uref), "scope": "admin" if k <= 3 else "read",
            "active": True, "last_used": DT(-RNG.randint(1, 20), 10, 0),
            "call_count": RNG.randint(100, 50000)})

    M["mkt_productos"] = productos


def escribir_resenas(arch, reviews, prefijo_tx=""):
    for tx, dirc in reviews:
        pr = tx["prod"]
        vendedor, comprador = R(ref_p(pr["sellerx"])), R(ref_p(tx["buyer"]))
        if dirc == "to_seller":
            v = {"direction": "to_seller", "seller_partner_id": vendedor,
                 "reviewer_partner_id": comprador,
                 "rating": RNG.choices([5, 4, 3, 2, 1], [48, 32, 12, 5, 3])[0],
                 "comment": RNG.choice(COMENT_VEND)}
        else:
            v = {"direction": "to_buyer", "seller_partner_id": comprador,
                 "reviewer_partner_id": vendedor, "rating": RNG.choices([5, 4, 3], [60, 30, 10])[0],
                 "comment": RNG.choice(COMENT_COMP)}
        v["transaction_id"] = R(prefijo_tx + tx["xid"])
        arch.rec(f"{tx['xid']}_rv_{dirc}", "shrimp.review", v)
