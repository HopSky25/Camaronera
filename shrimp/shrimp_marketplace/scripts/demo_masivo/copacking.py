# -*- coding: utf-8 -*-
"""shrimp_copacking: maquiladores, sus tarifas dirigidas, y el ciclo completo
de un servicio de empaque: solicitud -> ofertas -> orden adjudicada ->
recepción -> empaque -> acta firmada por las dos partes -> cierre.

Coherencia que se respeta (la misma que impone el código):
* El lote que se manda a empacar es SIEMPRE del cliente (camaronera dueña del
  lote); una empacadora cliente no lleva lote.
* Solicitud adjudicada <-> una oferta aceptada, el resto descartadas o
  retiradas, y una orden. Solicitud cerrada <-> orden cerrada.
* Libras recibidas y empacadas sin pasar lo acordado + margen; empacadas <=
  recibidas. Acta: abierta, en disputa, firmada; las rondas reabiertas quedan
  archivadas (active=False), no se borran.
"""
from .comun import (CIUDADES, CIUDADES_PLANTA, D, DOMINIO_LOGIN, DT, M2M, MP,
                    RNG, SP, UR, Archivo, R, persona, r2, ruc_sociedad, slug,
                    telefono_fijo)
from .comun import cobro_demo
from .verificacion import usuario_portal

MAQUILADORES = [
    ("Maquila Acuícola del Pacífico S.A.", "Durán"), ("Servicios de Empaque Puerto Bolívar S.A.", "Puerto Bolívar"),
    ("Planta de Proceso Costa Sur Cía. Ltda.", "Machala"), ("Empaques Marinos Posorja S.A.", "Posorja"),
    ("Coempaque Manabí S.A.", "Jaramijó"), ("Procesos Acuícolas Taura S.A.", "Taura"),
    ("Maquila Frigorífica Chongón S.A.", "Chongón"), ("Servicios IQF Santa Elena S.A.", "Santa Elena"),
    ("Planta Maquiladora El Guabo Cía. Ltda.", "El Guabo"), ("Empacadora de Servicios Manta S.A.", "Manta"),
    ("Valor Agregado Ecuador S.A.", "Guayaquil"), ("Frío y Empaque Huaquillas Cía. Ltda.", "Huaquillas"),
]
FORMATOS = {
    "entero": ["Master 6 x 2 kg (bloque)", "Caja 20 kg granel", "Master 10 x 1 kg IQF"],
    "cola": ["Master 10 x 2 lb IQF", "Master 6 x 4 lb semi-IQF", "Bloque 5 lb"],
    "valor_agregado": ["Bandeja 400 g pelado desvenado", "Bolsa 1 kg PUD IQF", "Brocheta 12 x 250 g"],
}
TARIFA_BASE = {"entero": 0.11, "cola": 0.16, "valor_agregado": 0.42}
INSUMOS = ["Cajas master, fundas y etiquetas del cliente; metabisulfito lo pone la planta.",
           "El cliente lleva cartón, zunchos y etiquetas con su código de exportador.",
           "Empaque completo del cliente (bolsa IQF, master, etiqueta) para 1.200 cajas."]


def construir(M):
    P = M["partners"]
    f1 = Archivo("shrimp_copacking", "demo_01_maquiladores.xml",
                 "Maquiladores (plantas que empacan camarón ajeno) y sus cuentas de acceso.")
    f2 = Archivo("shrimp_copacking", "demo_02_tarifas.xml",
                 "Tarifas de servicio de empaque dirigidas a clientes concretos.")
    f3 = Archivo("shrimp_copacking", "demo_03_solicitudes_ordenes.xml",
                 "Solicitudes de empaque, ofertas, órdenes adjudicadas y actas firmadas.")
    M["archivos"] += [f1, f2, f3]

    # ------------------------------------------------------------ maquiladores
    maqs = []
    f1.seccion("Maquiladores")
    for i, (nombre, ciudad) in enumerate(MAQUILADORES, 1):
        x = f"demo_ms_maq_{i:02d}"
        prov, cod, st = CIUDADES[ciudad][0], CIUDADES[ciudad][1], CIUDADES[ciudad][2]
        dom = slug(nombre.split(" S.A")[0].split(" Cía")[0], 25).replace("_", "")
        tel = telefono_fijo(ciudad)
        desde = -RNG.randint(200, 900)
        vigente = i not in (11,)
        minimo = float(RNG.choice([2000, 5000, 8000, 10000, 15000]))
        f1.rec(x, "res.partner", {
            "name": nombre, "is_company": True, "shrimp_user_type": "maquilador",
            "vat_or_id": ruc_sociedad(cod), "email": f"servicios@{dom}.test", "phone": tel,
            "street": f"Km {RNG.randint(2, 20)} vía {RNG.choice(['Durán-Tambo', 'Data', 'Puerto Bolívar', 'a la Costa'])}",
            "city": ciudad, "state_id": R(st), "country_id": R("base.ec"),
            "shrimp_razon_social": nombre, "shrimp_representante": persona(con_titulo=True),
            "shrimp_telefono": tel, "shrimp_ubicacion": f"{ciudad}, {prov}",
            "pack_codigo_establecimiento": f"EC-{RNG.randint(100, 999)}-{RNG.choice('ABCDE')}",
            "pack_habilitacion_desde": D(desde),
            "pack_habilitacion_hasta": D(desde + 1095 if vigente else -RNG.randint(5, 40)),
            "shrimp_capacity_value": float(RNG.choice([150000, 250000, 400000, 600000])),
            "shrimp_capacity_unit": "lb_week",
            "pack_presentaciones": RNG.choice(["Entero y cola", "Cola IQF y valor agregado",
                                               "Entero, cola y valor agregado", "Entero en bloque"]),
            "pack_desde_entero": r2(TARIFA_BASE["entero"] * RNG.uniform(0.9, 1.15)),
            "pack_desde_cola": r2(TARIFA_BASE["cola"] * RNG.uniform(0.9, 1.15)),
            "pack_desde_valor_agregado": r2(TARIFA_BASE["valor_agregado"] * RNG.uniform(0.9, 1.2)),
            "pack_currency_id": R("base.USD"),
            "pack_tarifa_nota": "Precios referenciales; la tarifa firme depende del volumen.",
            "pack_lote_minimo_lb": minimo, "pack_en_directorio": i != 12,
            "shrimp_account_state": "approved",
        })
        maqs.append(x)
        P[x] = {"rol": "maquilador", "nombre": nombre, "ciudad": ciudad, "minimo": minimo}
    f1.seccion("Maquiladores recién registrados, pendientes de aprobación (sin operaciones)")
    for i, (nombre, ciudad, estado) in enumerate([
            ("Empaques del Litoral Norte S.A.", "Pedernales", "pending"),
            ("Maquila Frío Sur Cía. Ltda.", "Arenillas", "pending"),
            ("Servicios de Glaseo Express S.A.", "Guayaquil", "rejected")], 1):
        cod, st = CIUDADES[ciudad][1], CIUDADES[ciudad][2]
        dom = slug(nombre.split(" S.A")[0].split(" Cía")[0], 25).replace("_", "")
        f1.rec(f"demo_ms_maq_nuevo_{i}", "res.partner", {
            "name": nombre, "is_company": True, "shrimp_user_type": "maquilador",
            "vat_or_id": ruc_sociedad(cod), "email": f"servicios@{dom}.test",
            "phone": telefono_fijo(ciudad), "city": ciudad, "state_id": R(st), "country_id": R("base.ec"),
            "shrimp_razon_social": nombre, "shrimp_ubicacion": f"{ciudad}, {CIUDADES[ciudad][0]}",
            "pack_en_directorio": False, "shrimp_account_state": estado,
            "comment": "Pendiente de verificar la habilitación de la planta." if estado == "pending"
            else "Rechazado: la planta no consta en el listado de establecimientos habilitados."})
    f1.seccion("Cuentas de portal de los maquiladores")
    for k, x in enumerate(maqs, 1):
        login = f"maquilador.{k:02d}@{DOMINIO_LOGIN}"
        ux = f"demo_ms_user_{x.replace('demo_ms_', '')}"
        usuario_portal(f1, ux, x, login)
        M["usuarios"].append((login, ux, x, "maquilador", "shrimp_copacking"))

    # ------------------------------------------------------------ clientes
    camaroneras = [c for c in M["cam"]]
    empacadoras = [SP + e for e in [f"demo_ms_emp_{i:02d}" for i in range(1, 11)]]
    # lotes de engorde por camaronera (marketplace y packer)
    lotes = {}
    for x, p in M["mkt_productos"].items():
        if p["kind"] == "eng" and p["state"] in ("published", "sold"):
            lotes.setdefault(p["sellerx"], []).append((MP + x, p))
    for p in M["prod_pk"]:
        lotes.setdefault(p["sellerx"], []).append((SP + p["xid"], p))

    # ------------------------------------------------------------ tarifas
    f2.seccion("Tarifas: la vigente publicada, las anteriores archivadas y un borrador")
    nt = 0
    for x in maqs:
        dest = [UR + c for c in RNG.sample(camaroneras, RNG.randint(3, 7))] + RNG.sample(empacadoras, RNG.randint(1, 3))
        inicios = sorted(RNG.sample(range(-360, -20), 4)) + [0]
        for j, ini in enumerate(inicios):
            nt += 1
            tx = f"demo_ms_tar_{nt:03d}"
            ultima = j == len(inicios) - 1
            estado = "published" if j == len(inicios) - 2 else ("draft" if ultima else "archived")
            fin = inicios[j + 1] - 1 if j < len(inicios) - 2 else None
            f2.rec(tx, "shrimp.copack.tariff", {
                "name": f"Tarifa de empaque {'2026' if ini > -270 else '2025'} - {'v' + str(j + 1)}",
                "copacker_partner_id": R(x), "currency_id": R("base.USD"),
                "valid_from": D(ini if not ultima else 15), "open_ended": fin is None,
                "valid_to": D(fin) if fin is not None else None,
                "recipient_ids": M2M(dest), "min_lot_lb": P[x]["minimo"],
                "payment_notes": RNG.choice(["50 % al recibir, saldo a 15 días", "Contado contra acta firmada",
                                             "Crédito 30 días con garantía"]),
                "conditions": "Tolerancia de manipulación 0,5 %. Insumos por cuenta del cliente. "
                              "Cámara de mantenimiento a -20 °C hasta 5 días sin costo.",
                "state": estado})
            factor = 1 + 0.03 * j
            k = 0
            for pres in ("entero", "cola", "valor_agregado"):
                if pres == "valor_agregado" and RNG.random() < 0.5:
                    continue
                for fmt in RNG.sample(FORMATOS[pres], 2):
                    for desde, desc in ((0.0, 1.0), (20000.0, 0.93), (50000.0, 0.87)):
                        if desde == 50000.0 and RNG.random() < 0.5:
                            continue
                        k += 1
                        f2.rec(f"{tx}_r{k}", "shrimp.copack.tariff.line", {
                            "tariff_id": R(tx), "presentation": pres, "pack_format": fmt,
                            "from_lb": desde, "rate_per_lb": r2(TARIFA_BASE[pres] * factor * desc * RNG.uniform(0.95, 1.08))})

    # ------------------------------------------------------------ solicitudes
    f3.seccion("Solicitudes, ofertas, órdenes y actas")
    nreq = nof = nord = 0
    for i in range(1, 96):
        nreq += 1
        rx = f"demo_ms_sol_{nreq:03d}"
        compra_ref = stock_ref = None
        if RNG.random() < 0.7:
            cam = RNG.choice([c for c in camaroneras if lotes.get(c)])
            cliente = UR + cam
            lote_ref, lote = RNG.choice(lotes[cam])
            pres = lote["presentation"] if RNG.random() < 0.8 else "valor_agregado"
            talla = MP + lote["size"]
            qty = float(round(min(lote["qty"], RNG.uniform(8000, 45000)) / 10) * 10)
        else:
            cliente = RNG.choice(empacadoras)
            lote_ref, talla = None, None
            pres = RNG.choice(["entero", "cola", "valor_agregado"])
            qty = float(RNG.randrange(15000, 90000, 500))
            # Lo que una empacadora manda a empacar es camarón que COMPRÓ: se
            # enlaza la solicitud a esa compra (y a su lote de inventario), así
            # el empaque sale en la trazabilidad de esa compra y no en otras.
            # Sin RNG (elección determinista por número de solicitud): añadir
            # llamadas al azar desplazaría la secuencia y cambiaría registros
            # ya cargados en bases con la demo anterior.
            compras = [t for t in M.get("compras_adulto", [])
                       if t["buyer"] == cliente and t["estado"] in ("confirmed", "done")]
            if compras:
                compra = compras[nreq % len(compras)]
                compra_ref = SP + compra["xid"]
                lote_ref = SP + compra["prod"]["xid"]
                stock_ref = (SP + compra["lot_comprador"]) if compra.get("lot_comprador") else None
                # Presentación, talla y libras se dejan como estaban sorteadas
                # (no cambian los registros de bases con la demo anterior).
        estado = RNG.choices(["draft", "published", "assigned", "done", "cancelled"], [8, 16, 24, 40, 12])[0]
        off = {"draft": RNG.randint(2, 20), "published": RNG.randint(-3, 10)}.get(estado, -RNG.randint(10, 300))
        if estado == "assigned":
            off = -RNG.randint(1, 12)
        dirigida = RNG.random() < 0.35
        maq_dir = None
        if dirigida:
            ok = [m for m in maqs if P[m]["minimo"] <= qty]
            maq_dir = RNG.choice(ok) if ok else None
        f3.rec(rx, "shrimp.copack.request", {
            "client_partner_id": R(cliente), "product_id": R(lote_ref) if lote_ref else None,
            "transaction_id": R(compra_ref) if compra_ref else None,
            "stock_lot_id": R(stock_ref) if stock_ref else None,
            "copacker_partner_id": R(maq_dir) if maq_dir else None, "quantity_lb": qty,
            "size_grade_id": R(talla) if talla else None, "presentation": pres,
            "needed_from": D(off), "needed_to": D(off + RNG.randint(3, 10)),
            "supplies_notes": RNG.choice(INSUMOS),
            "notes": RNG.choice(["Camarón ya clasificado, sin cabeza ni basura.",
                                 "Urgente: la línea propia está en mantenimiento.", None]),
            "state": estado})
        if estado == "draft":
            continue
        # ofertas
        ofertantes = [maq_dir] if maq_dir else RNG.sample(maqs, RNG.randint(1, 4))
        if maq_dir and RNG.random() < 0.3:
            ofertantes += RNG.sample([m for m in maqs if m != maq_dir], 1)
        ganador = ofertantes[0] if estado in ("assigned", "done") or (estado == "cancelled" and RNG.random() < 0.5) else None
        oferta_ganadora = None
        for m in ofertantes:
            nof += 1
            ox = f"demo_ms_ofe_{nof:04d}"
            if m == ganador:
                st = "accepted"
            elif estado == "published":
                st = RNG.choices(["sent", "withdrawn"], [85, 15])[0]
            else:
                st = RNG.choices(["rejected", "withdrawn"], [80, 20])[0]
            cap = qty if RNG.random() < 0.75 else float(round(qty * RNG.uniform(0.6, 0.95) / 10) * 10)
            base = TARIFA_BASE[pres] * RNG.uniform(0.92, 1.12)
            f3.rec(ox, "shrimp.copack.offer", {
                "request_id": R(rx), "copacker_partner_id": R(m), "currency_id": R("base.USD"),
                "rate_per_lb": r2(base), "capacity_lb": cap,
                "available_from": D(off), "available_to": D(off + RNG.randint(4, 12)),
                "notes": RNG.choice(["Incluye glaseo al 10 %.", "Turno nocturno disponible.",
                                     "Cámara de mantenimiento incluida 3 días.", None]),
                "state": st})
            if st == "accepted":
                oferta_ganadora = (ox, m, base, cap)
        if not oferta_ganadora:
            continue
        # orden
        nord += 1
        ox, m, tarifa, cap = oferta_ganadora
        dx = f"demo_ms_oem_{nord:03d}"
        acordado = min(cap, qty)
        if estado == "done":
            ost = "closed"
        elif estado == "cancelled":
            ost = "cancelled"
        else:
            ost = RNG.choice(["confirmed", "received", "packed", "packed", "signed"])
        reabierta = ost in ("signed", "closed", "received") and RNG.random() < 0.15
        recibido = r2(acordado * RNG.uniform(0.9, 1.06)) if ost not in ("confirmed", "cancelled") else 0.0
        if ost == "cancelled" and RNG.random() < 0.5:
            recibido = r2(acordado * RNG.uniform(0.9, 1.0))
        cuadra = RNG.random() < 0.8
        empacado = 0.0
        if ost in ("packed", "signed", "closed") or (ost == "received" and reabierta):
            empacado = r2(recibido * (RNG.uniform(0.996, 1.0) if cuadra else RNG.uniform(0.975, 0.992)))
        acta = {"confirmed": "na", "received": "na", "cancelled": "na", "signed": "closed",
                "closed": "closed"}.get(ost)
        if ost == "packed":
            acta = "disputed" if (not cuadra or RNG.random() < 0.3) else "open"
        t_rec = min(-1, off + RNG.randint(0, 2))
        t_emp = min(-1, t_rec + RNG.randint(1, 3))
        v = {"request_id": R(rx), "offer_id": R(ox), "client_partner_id": R(cliente),
             "copacker_partner_id": R(m), "product_id": R(lote_ref) if lote_ref else None,
             "transaction_id": R(compra_ref) if compra_ref else None,
             "stock_lot_id": R(stock_ref) if stock_ref else None,
             "currency_id": R("base.USD"), "agreed_qty_lb": acordado,
             "agreed_overrun_pct": 10.0, "rate_per_lb": r2(tarifa),
             "supplies_notes": RNG.choice(INSUMOS),
             "supplies_received": ost != "confirmed" and RNG.random() < 0.85,
             "received_lb": recibido or None,
             "received_date": DT(t_rec, RNG.randint(6, 14), 0) if recibido else None,
             "packed_lb": empacado or None,
             "packed_date": DT(t_emp, RNG.randint(14, 22), 0) if ost in ("packed", "signed", "closed") else None,
             "boxes": int(empacado / RNG.choice([8.8, 13.2, 44.1])) if empacado and ost != "received" else None,
             # Presentación empacada: la lista única de presentaciones; el
             # formato concreto va en la nota.
             "packed_presentation": pres if empacado else None,
             "packed_presentation_note": {"entero": "Entero en bloque", "cola": "Cola IQF",
                                          "valor_agregado": "Pelado desvenado (PUD)"}[pres] if empacado else None,
             "tolerance_pct": 0.5, "platform_rate_per_lb": 0.01,
             "state": ost, "acceptance_state": acta}
        if v["supplies_received"] is False and ost != "confirmed":
            v["supplies_issue"] = "Faltaron 80 cajas master; la línea paró 2 horas hasta que llegaron."
        f3.rec(dx, "shrimp.copack.order", v)
        # Comisión de la plataforma por libra empacada, al maquilador, cuando
        # las dos partes firmaron el acta.
        if ost in ("signed", "closed") and empacado:
            f3.rec(f"{dx}_chg", "shrimp.charge", cobro_demo(
                charge_type="copack_platform", copack_order_id=R(dx), payer_partner_id=R(m),
                transaction_id=R(compra_ref) if compra_ref else None,
                product_id=R(lote_ref) if lote_ref else None,
                qty=empacado, invoice_qty=empacado, unit_amount=0.01,
                amount=r2(empacado * 0.01), currency_id=R("base.USD"),
                description="Comisión de empaque (demostración)",
                date=DT(t_emp, 20, 0)))
        f3.fn_write("shrimp.copack.request", ["shrimp_copacking." + rx], [("order_id", "=", False)],
                    {"order_id": R(dx)})
        # firmas del acta
        rondas = []
        if reabierta:
            rondas.append((1, ("accepted", "rejected"), False))
        ronda_vig = 2 if reabierta else 1
        if ost in ("signed", "closed"):
            rondas.append((ronda_vig, ("accepted", "accepted"), True))
        elif ost == "packed":
            if acta == "disputed":
                rondas.append((ronda_vig, RNG.choice([("rejected", "accepted"), ("rejected", "pending"), ("accepted", "rejected")]), True))
            else:
                rondas.append((ronda_vig, RNG.choice([("pending", "pending"), ("accepted", "pending"), ("pending", "accepted")]), True))
        for ronda, (dc, dm), vigente in rondas:
            for rol, partner, dec in (("client", cliente, dc), ("copacker", m, dm)):
                a = {"order_id": R(dx), "ronda": ronda, "active": vigente, "role": rol,
                     "partner_id": R(partner), "decision": dec}
                if dec != "pending":
                    a.update({"decided_at": DT(t_emp + (0 if vigente else -1), RNG.randint(9, 18), 0),
                              "signed_received_lb": recibido, "signed_packed_lb": empacado,
                              "signed_difference_lb": r2(recibido - empacado),
                              "signed_difference_pct": r2(100 * (recibido - empacado) / recibido) if recibido else 0.0,
                              "signed_tolerance_pct": 0.5})
                if dec == "rejected":
                    a["reason"] = RNG.choice(["Faltan libras: la diferencia supera la tolerancia pactada.",
                                              "Hay cajas con peso neto por debajo de lo etiquetado."])
                elif dec == "accepted":
                    a["reason"] = "Conforme con las libras recibidas y empacadas."
                if not vigente:
                    a.update({"archived_at": DT(t_emp, 19, 0),
                              "archive_reason": "Se reabrió el acta para rectificar las libras empacadas tras el reconteo."})
                f3.rec(f"{dx}_firma{ronda}_{rol}", "shrimp.copack.acceptance", a)
