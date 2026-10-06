# -*- coding: utf-8 -*-
"""shrimp_packer: empacadoras, listas de precios semana a semana, compras de
camarón adulto verificadas, reservas anticipadas de cosecha y avisos de lotes.
Incluye también el ajuste de coherencia de la demo ORIGINAL (archivo 09).

Las cosechas salen de las siembras que generó el marketplace: piscina
sembrada con postlarva -> ~95-120 días de engorde -> cosecha (lote ENGORDE
con su piscina de origen) -> compra verificada por una empacadora. Si la
cosecha se reservó por anticipado, la empacadora que se comprometió es la que
compra, al precio liquidado del compromiso.
"""
import math
import xml.etree.ElementTree as ET

from datetime import timedelta

from .comun import (ADDONS, MP, REF, RNG, SP, SV, UR, Archivo, CIUDADES, E, SR, cobro_demo,
                    CIUDADES_PLANTA, D, DOMINIO_LOGIN, DT, E, M2M, R, celular,
                    persona, r2, ruc_sociedad, slug, telefono_fijo)
from .marketplace import (IMGS, LB_POR_KG, PRECIO_COLA_LB, PRECIO_ENTERO_KG,
                          SANIDAD_OK, TALLAS_COLA, TALLAS_ENTERO,
                          TARIFA_CENTAVOS, certs_producto, disponible,
                          evoluciones, lote_vals, prod_vals)
from .verificacion import EN_CURSO, compra_verificada, elegir_escenario, usuario_portal

VIEJAS_EMP = ["demo_pk_aquagold", "demo_pk_ecuamarisco_s_a", "demo_pk_empacadora_demo_trazul",
              "demo_pk_pesquera_del_golfo", "demo_pk_exportadora_manabi"]
VIEJAS_CAM = ["demo_pk_grupo_burgos", "demo_pk_camaronera_escalante_s_a",
              "demo_pk_fimasa_fincas_marinas_s_a", "demo_pk_camaronera_rolesa"]
NUEVAS_EMP = [
    ("Empacadora Golfo Azul S.A.", "Durán"), ("Mariscos del Estero S.A.", "Guayaquil"),
    ("Procesadora Puerto Bolívar Seafood S.A.", "Puerto Bolívar"),
    ("Exportadora Pacific Prime S.A.", "Posorja"), ("Frigorífico Manabita del Mar S.A.", "Jaramijó"),
    ("Empacadora Santa Elena Export S.A.", "Santa Elena"), ("Langostinos del Ecuador Export S.A.", "Taura"),
    ("Procesadora Acuícola Machala S.A.", "Machala"), ("Seafood Chongón Cía. Ltda.", "Chongón"),
    ("Empacadora Costa Verde Export S.A.", "Manta"),
]
# Tallas que cotizan las listas (packer añade 10/20 entero y U/12, U/15 cola)
ENTERO_LISTA = [(SP + "size_entero_10_20", "10/20")] + [(MP + x, n) for x, n, _p in TALLAS_ENTERO]
COLA_LISTA = [(SP + "size_cola_u12", "U/12"), (SP + "size_cola_u15", "U/15")] + \
    [(MP + x, n) for x, n, _p in TALLAS_COLA]
CONDICIONES = [
    "Sin sabores extraños, sin picados, sin hongos. Se rechaza color amarillo o mezclado.",
    "No se recibe camarón con branquias sucias ni cabeza roja. Metabisulfito bajo 100 ppm.",
    "Producto fresco, cosechado con hielo 1:1 y entregado en menos de 8 horas.",
    "Se descuenta la basura y la merma de pesca. Sin flacidez ni mudado en exceso.",
]
BONOS = [("SMALL", "Lote menor a 10.000 lb"), ("MEDIUM", "Lote de 10.000 a 30.000 lb"),
         ("LARGE", "Lote mayor a 30.000 lb"), ("LOCAL", "Entrega en planta con transporte propio"),
         ("ROJO", "Coloración roja intensa (cocido)"), ("BAP", "Finca con certificación BAP vigente")]


def factor_mercado(off, sesgo):
    """Precio relativo del día: estacional (+-6 %) con tendencia y ruido."""
    return (1 + 0.06 * math.sin(2 * math.pi * (off + 60) / 365.0) + 0.0001 * off + sesgo)


def precio_kg(talla, off, sesgo):
    return PRECIO_ENTERO_KG[talla] * factor_mercado(off, sesgo)


def precio_lb_cola(talla, off, sesgo):
    return PRECIO_COLA_LB[talla] * factor_mercado(off, sesgo)


def aguaje_desde(off):
    """El aguaje en curso o el próximo a partir del día ``off``: lo que una
    lista nueva (borrador o próxima) tiene que llevar, porque el aguaje es
    obligatorio al publicar y nunca puede haber pasado. Por orden del modelo
    (date_from), el primero que termina ese día o después."""
    return SR("shrimp.aguaje",
              "[('date_to', '>=', (DateTime.today() + relativedelta(days=%d)).strftime('%%Y-%%m-%%d'))]"
              % off)


def construir(M):
    P = M["partners"]
    f4 = Archivo("shrimp_packer", "demo_04_masivo_empacadoras.xml",
                 "Empacadoras, calendario de aguajes del año anterior, datos de empacadora\n"
                 "     en las camaroneras y cuentas de acceso.")
    f5 = Archivo("shrimp_packer", "demo_05_masivo_listas.xml",
                 "Listas de precios de compra de las empacadoras, una cada dos semanas en\n"
                 "     los últimos seis meses y una al mes antes: renglones, bonos y destinatarios.")
    f6 = Archivo("shrimp_packer", "demo_06_masivo_compras_adulto.xml",
                 "Cosechas de engorde y su venta verificada a empacadoras: despacho,\n"
                 "     informe de planta con clasificación y conteos, posturas, cobros y reseñas.")
    f7 = Archivo("shrimp_packer", "demo_07_masivo_reservas.xml",
                 "Reservas anticipadas de cosecha: declaraciones, compromisos de las\n"
                 "     empacadoras y confirmaciones cuando la cosecha salió fuera de banda.")
    f8 = Archivo("shrimp_packer", "demo_08_masivo_avisos.xml",
                 "Avisos de lotes nuevos enviados a las empacadoras.")
    M["archivos"] += [f4, f5, f6, f7, f8]

    # ================================================================ 04 socios
    f4.seccion("Aguajes del año anterior (los del año en curso los siembra data/)")
    f4.funcion("shrimp.aguaje", "sembrar_anio", "[(DateTime.today() - relativedelta(years=1)).year]")

    f4.seccion("Empacadoras nuevas")
    emps = []
    for i, (nombre, ciudad) in enumerate(NUEVAS_EMP, 1):
        x = f"demo_ms_emp_{i:02d}"
        prov, cod, st = CIUDADES[ciudad][0], CIUDADES[ciudad][1], CIUDADES[ciudad][2]
        dom = slug(nombre.split(" S.A")[0].split(" Cía")[0], 25).replace("_", "")
        tel = telefono_fijo(ciudad)
        f4.rec(x, "res.partner", {
            "name": nombre, "is_company": True, "shrimp_user_type": "empacadora",
            "vat_or_id": ruc_sociedad(cod), "email": f"compras@{dom}.test", "phone": tel,
            "street": f"Km {RNG.randint(3, 25)} vía {RNG.choice(['Durán-Tambo', 'a la Costa', 'Puerto Bolívar', 'Data', 'Manta-Montecristi'])}",
            "city": ciudad, "state_id": R(st), "country_id": R("base.ec"),
            "shrimp_razon_social": nombre, "shrimp_representante": persona(con_titulo=True),
            "emp_contacto_comercial": persona(), "shrimp_telefono": tel,
            "emp_codigo_exportador": f"EXP-{RNG.randint(1000, 9999)}",
            "emp_planta_nombre": f"Planta {ciudad}", "shrimp_ubicacion": f"{ciudad}, {prov}",
            "shrimp_capacity_value": float(RNG.choice([60000, 90000, 120000, 180000, 250000, 400000])),
            "shrimp_capacity_unit": "lb_day",
            "emp_cert_bap": RNG.random() < 0.7, "emp_cert_asc": RNG.random() < 0.45,
            "emp_cert_haccp": True,
            "emp_cert_otras": RNG.choice(["BRCGS AA", "IFS Food", "FDA registrada", "Halal", None]),
            "emp_aprobacion_sanitaria": f"ARCSA-PL-{RNG.randint(10000, 99999)}",
            "emp_mercado_asia": RNG.random() < 0.8, "emp_mercado_europa": RNG.random() < 0.55,
            "emp_mercado_norteamerica": RNG.random() < 0.5, "emp_mercado_local": RNG.random() < 0.3,
            "emp_aviso_frecuencia": RNG.choices(["diario", "dos_veces", "off"], [55, 35, 10])[0],
            "emp_aviso_min_cantidad": float(RNG.choice([0, 5000, 10000, 20000])),
            "emp_aviso_incluir_no_dirigidos": RNG.random() < 0.4,
            "emp_aviso_ultima_revision": DT(-1, 7, 0), "emp_aviso_ultimo_envio": DT(-1, 7, 2),
            "reserva_acepta": RNG.random() < 0.75, "shrimp_account_state": "approved",
        })
        emps.append(x)
        P[x] = {"rol": "empacadora", "nombre": nombre, "ciudad": ciudad, "provincia": prov}
    f4.seccion("Empacadoras recién registradas, todavía sin aprobar (sin operaciones)")
    for i, (nombre, ciudad, estado) in enumerate([
            ("Exportadora Nuevo Horizonte S.A.", "Guayaquil", "pending"),
            ("Mariscos Isla Verde S.A.", "Machala", "pending"),
            ("Procesadora Delta Export S.A.", "Durán", "rejected")], 1):
        cod, st = CIUDADES[ciudad][1], CIUDADES[ciudad][2]
        dom = slug(nombre.split(" S.A")[0], 25).replace("_", "")
        f4.rec(f"demo_ms_emp_nueva_{i}", "res.partner", {
            "name": nombre, "is_company": True, "shrimp_user_type": "empacadora",
            "vat_or_id": ruc_sociedad(cod), "email": f"compras@{dom}.test",
            "phone": telefono_fijo(ciudad), "city": ciudad, "state_id": R(st), "country_id": R("base.ec"),
            "shrimp_razon_social": nombre, "shrimp_representante": persona(con_titulo=True),
            "emp_planta_nombre": f"Planta {ciudad}", "shrimp_ubicacion": ciudad,
            "shrimp_account_state": estado,
            "comment": "Registro web pendiente de revisar la aprobación sanitaria." if estado == "pending"
            else "Rechazada: el código de exportador no corresponde a la razón social."})
    todas_emp = emps + VIEJAS_EMP

    f4.seccion("Datos que la empacadora mira de cada camaronera")
    for k, cx in enumerate(M["cam"]):
        f4.fn_write("res.partner", [UR + cx], [("shrimp_registro_acuicola", "=", False)], {
            "shrimp_registro_acuicola": f"GR-{1200 + k * 7}",
            "farm_tasa_descuento_anual": r2(RNG.choice([0, 0, 9.5, 11.0, 12.5, 14.0, 16.5])) or None,
            "farm_publicar_historial": RNG.random() < 0.5})

    f4.seccion("Cuentas de acceso de empacadoras y de las camaroneras de la demo de listas")
    for k, x in enumerate(VIEJAS_EMP + emps, 1):
        login = f"empacadora.{k:02d}@{DOMINIO_LOGIN}"
        ux = f"demo_ms_user_{x.replace('demo_', '')}"
        usuario_portal(f4, ux, x, login)
        M["usuarios"].append((login, ux, x, "empacadora", "shrimp_packer"))
    base_cam = 5 + len(M["cam"])
    for k, x in enumerate(VIEJAS_CAM, base_cam + 1):
        login = f"camaronera.{k:02d}@{DOMINIO_LOGIN}"
        ux = f"demo_ms_user_{x.replace('demo_', '')}"
        usuario_portal(f4, ux, x, login)
        M["usuarios"].append((login, ux, x, "camaronera", "shrimp_packer"))

    # ================================================================ 05 listas
    # Cada empacadora compra en una zona: le escribe a sus camaroneras.
    cams = M["cam"]
    destinatarios, sesgos, presentaciones = {}, {}, {}
    listas_vigentes = {}
    for x in emps:
        destinatarios[x] = RNG.sample(cams, RNG.randint(8, 16))
        sesgos[x] = {"base": RNG.uniform(-0.03, 0.03), "grande": RNG.uniform(-0.04, 0.04)}
        presentaciones[x] = RNG.choices([("entero", "cola"), ("entero",), ("cola",)], [60, 30, 10])[0]
    f5.seccion("Listas de precios (la última publicada; las anteriores archivadas)")
    nl = 0
    M["listas"] = {}
    for x in emps:
        fechas = list(range(-182, 1, 14))[::-1] + list(range(-196 - 28, -366, -28))
        fechas = sorted(set(fechas))
        upcoming = RNG.random() < 0.3
        for j, emi in enumerate(fechas):
            nl += 1
            lx = f"demo_ms_pl_{nl:04d}"
            ultima = j == len(fechas) - 1
            estado = "published" if ultima else "archived"
            desde = emi + RNG.choice([0, 1, 2])
            if ultima:
                desde = min(desde, 0)
            pago = RNG.choice([(50, 3, 12), (60, 2, 15), (100, 5, 0), (40, 5, 21), (70, 3, 10)])
            semana = (REF + timedelta(days=desde)).isocalendar()[1]
            v = {"name": f"Semana {semana} - despacho desde el {(REF + timedelta(days=desde)).strftime('%d/%m')}",
                 "issuer_partner_id": R(x), "issue_date": D(emi), "dispatch_from": D(desde),
                 # El aguaje al que rige la lista: el que contiene su fecha de
                 # despacho (si cae entre dos aguajes, ninguno).
                 "aguaje_id": SR("shrimp.aguaje", (
                     "[('date_from', '<=', (DateTime.today() + relativedelta(days=%d)).strftime('%%Y-%%m-%%d')), "
                     "('date_to', '>=', (DateTime.today() + relativedelta(days=%d)).strftime('%%Y-%%m-%%d'))]")
                     % (desde, desde)),
                 "open_ended": ultima, "dispatch_to": None if ultima else D(desde + 13),
                 "state": estado, "currency_id": R("base.USD"),
                 "quality_conditions": RNG.choice(CONDICIONES),
                 "advance_pct": float(pago[0]), "advance_days": pago[1],
                 "balance_days": pago[2] if pago[0] < 100 else 0,
                 "payment_notes": RNG.choice(["Saldo contra informe de verificación.",
                                              "Pago por transferencia al Banco del Pacífico.", None]),
                 "recipient_ids": M2M([UR + c for c in destinatarios[x]])}
            f5.rec(lx, "shrimp.price.list", v)
            M["listas"].setdefault(x, []).append((lx, emi, estado))
            renglones(f5, lx, presentaciones[x], emi, sesgos[x])
            if ultima:
                listas_vigentes[x] = lx
        # borradores: la de la próxima semana (con publicación automática) y una a medias.
        # Las listas nuevas llevan el aguaje en curso o el próximo: sin él la
        # publicación automática no podría publicarlas.
        nl += 1
        lx = f"demo_ms_pl_{nl:04d}"
        nuevas = M.setdefault("listas_nuevas", [])
        nuevas.append(lx)
        f5.rec(lx, "shrimp.price.list", {
            "name": "Lista próxima semana (borrador)", "issuer_partner_id": R(x),
            "issue_date": D(0), "dispatch_from": D(7), "aguaje_id": aguaje_desde(7),
            "open_ended": True, "state": "draft",
            "auto_publish": True, "auto_publish_date": D(RNG.randint(5, 7)),
            "currency_id": R("base.USD"), "quality_conditions": RNG.choice(CONDICIONES),
            "advance_pct": 50.0, "advance_days": 3, "balance_days": 12,
            "recipient_ids": M2M([UR + c for c in destinatarios[x]])})
        renglones(f5, lx, presentaciones[x], 7, sesgos[x])
        if upcoming:
            nl += 1
            lx = f"demo_ms_pl_{nl:04d}"
            nuevas.append(lx)
            desde_prox = RNG.randint(4, 9)
            f5.rec(lx, "shrimp.price.list", {
                "name": "Lista para el próximo aguaje", "issuer_partner_id": R(x),
                "issue_date": D(-1), "dispatch_from": D(desde_prox),
                "aguaje_id": aguaje_desde(desde_prox), "open_ended": True,
                "state": "published", "currency_id": R("base.USD"),
                "quality_conditions": RNG.choice(CONDICIONES), "advance_pct": 100.0,
                "advance_days": 2, "recipient_ids": M2M([UR + c for c in destinatarios[x][:5]])})
            renglones(f5, lx, presentaciones[x], 5, sesgos[x])
    M["listas_vigentes"] = listas_vigentes
    # El aguaje de cada lista, también en bases que ya tenían la demo (las
    # listas son noupdate): escritura idempotente al instalar y en cada -u.
    # Las nuevas (borrador y próximas) también: en una base que ya tenía la
    # demo nacieron sin aguaje y no podrían publicarse.
    refs = ", ".join(repr(SP + x) for x in sorted(
        set(lx for lst in M["listas"].values() for (lx, _e, _s) in lst)
        | set(M.get("listas_nuevas", []))))
    f5.escrituras.append(
        '        <function model="shrimp.price.list" name="_shrimp_link_aguaje" '
        'eval="[[r.id for r in [obj().env.ref(x, False) for x in [%s]] if r]]"/>\n' % refs)

    # ================================================================ 06 cosechas y compras
    siembras = [s for s in M["siembras"] if s["off"] + 95 <= -2 and not s["cam"].startswith("demo_ms_cam_grupo")]
    siembras_futuras = [s for s in M["siembras"] if s["off"] + 95 > 4]
    RNG.shuffle(siembras)
    siembras = siembras[:130]
    productos, compras = [], []
    reservas_pasadas = []
    for i, s in enumerate(siembras, 1):
        dias = RNG.randint(95, 125)
        h = min(-2, s["off"] + dias)
        pres = RNG.choices(["entero", "cola"], [72, 28])[0]
        tl = RNG.choice(TALLAS_ENTERO[1:6] if pres == "entero" else TALLAS_COLA[1:6])
        if pres == "entero":
            gramos = 1000.0 / tl[2]
        else:
            gramos = 453.6 / tl[2] / 0.65
        surv = RNG.uniform(0.5, 0.72)
        lb = s["qty"] * 1000 * surv * gramos / 453.6
        lb = max(5000, round(lb / 10) * 10)
        # quién compra: preferentemente una empacadora que le pasa lista
        candidatas = [e for e in emps if s["cam"] in destinatarios[e]] or emps
        emp = RNG.choice(candidatas)
        off_lista = h
        if pres == "entero":
            precio = precio_kg(tl[1], off_lista, sesgos[emp]["base"]) / LB_POR_KG
        else:
            precio = precio_lb_cola(tl[1], off_lista, sesgos[emp]["base"]) * 0.62
        cam = P[s["cam"]]
        pr = {"xid": f"demo_mpk_prod_{i:03d}", "kind": "eng", "sellerx": s["cam"], "rol": "camaronera",
              "species": "shrimp_species_vannamei", "stage": "shrimp_stage_engorde",
              "genetics": (s["prod"].get("genetics") if s["prod"].get("genetics") != "shrimp_genetics_monodon_classic"
                           else "shrimp_genetics_spr"),
              "name": f"Cosecha {s['nombre']} - {pres} {tl[1]} ({cam['nombre'].split(' S.A')[0].split(' Cía')[0]})",
              "size_mg": r2(gramos * 1000), "surv": r2(surv * 100), "health": RNG.choice(SANIDAD_OK),
              "qty": lb, "uom": "uom_libra", "presentation": pres, "size": tl[0],
              "price": r2(precio), "location": f"{cam['ciudad']}, {cam['provincia']}",
              "state": "published", "off": h, "entrega": h + 1, "ventana": 6,
              "facility": s["fac"], "pond": s["pond"], "batch": f"COS-{s['cam'][-2:]}-{i:03d}",
              "fotos": RNG.sample(IMGS, 3), "consumido": 0.0, "lot": f"demo_mpk_lot_{i:03d}",
              "notas": f"Piscina sembrada con {s['qty']:.0f} millares de {s['prod']['name']}.",
              "siembra": s, "comprador": emp}
        if (RNG.random() < 0.35 and emp in listas_vigentes and h > -14
                and s["cam"] in destinatarios[emp]):    # la lista tiene que ser visible para el vendedor
            pr["price_list"] = listas_vigentes[emp]
        productos.append(pr)
        # ¿se reservó por anticipado?
        if RNG.random() < 0.4:
            reservas_pasadas.append(pr)
            pr["reserva"] = True
    # compras
    n = 0
    for pr in productos:
        qty = round(pr["qty"] * RNG.uniform(0.85, 1.0) / 10) * 10
        esc = None
        if pr.get("reserva"):
            esc = RNG.choices(["closed_done", "closed_confirmed", "broken"], [80, 12, 8])[0]
        n += 1
        if pr["off"] > -10:
            esc = RNG.choice(EN_CURSO)      # recién cosechado: la verificación sigue abierta
        esc = esc or elegir_escenario(pr["off"])
        compras.append((n, pr, pr["comprador"], qty, pr["off"] - 2, esc))
        if esc in ("closed_confirmed", "closed_done"):
            pr["consumido"] += qty
        elif esc in ("broken", "rejected", "cancelled") and RNG.random() < 0.6:
            # el lote termina en otra planta
            otra = RNG.choice([e for e in emps if e != pr["comprador"]])
            n += 1
            q2 = round(pr["qty"] * RNG.uniform(0.8, 0.95) / 10) * 10
            compras.append((n, pr, otra, q2, min(-3, pr["off"] + RNG.randint(3, 6)), "closed_done"))
            pr["consumido"] += q2
    f6.seccion("Cosechas (lote ENGORDE con piscina de origen) y su lote de stock")
    for pr in productos:
        if disponible(pr) <= 0.001:
            pr["state"] = "sold"
        f6.rec(pr["xid"], "shrimp.product", prod_vals(pr), context="{'skip_initial_lot': True}")
        f6.rec(pr["lot"], "shrimp.stock.lot", lote_vals(pr))
    f6.seccion("Certificados y evolución del engorde")
    for pr in productos:
        certs_producto(M, f6, pr)
        evoluciones(M, f6, pr, 5, 9)
        pr["consumido"] = 0.0
    f6.seccion("Compras verificadas de camarón adulto")
    M["compras_adulto"] = []
    M["prod_pk"] = productos
    for n, pr, emp, qty, off, esc in compras:
        tx = compra_verificada(M, f6, n, pr, SP + emp, qty, off, "camaronera_to_buyer", "adult",
                               "demo_mpk_", {"escenario": esc, "planta": f"Planta {P[emp]['ciudad']}"})
        M["compras_adulto"].append(tx)
        pr.setdefault("compras", []).append(tx)

    # ================================================================ 07 reservas
    reservas(M, f7, emps, destinatarios, sesgos, reservas_pasadas, siembras_futuras, listas_vigentes)

    # ================================================================ 08 avisos
    f8.seccion("Avisos de lotes: uno por empacadora y lote, con el precio de su lista")
    lotes_aviso = [(MP + x if not x.startswith(MP) else x) for x in M["prod_eng_mkt"]]
    info = {MP + x: p for x, p in M["mkt_productos"].items()}
    for pr in productos:
        if pr["state"] == "published":
            lotes_aviso.append(pr["xid"])
            info[pr["xid"]] = pr
    na = 0
    for emp in emps:
        if emp not in listas_vigentes:
            continue
        propios = [l for l in lotes_aviso if info[l]["sellerx"] in destinatarios[emp]]
        otros = [l for l in lotes_aviso if l not in propios]
        elegidos = propios + RNG.sample(otros, min(len(otros), RNG.randint(4, 12)))
        for l in elegidos:
            pr = info[l]
            na += 1
            if pr["presentation"] == "entero":
                precio, uom = r2(precio_kg(next(n for (x, n, _p) in TALLAS_ENTERO if x == pr["size"]), 0, sesgos[emp]["base"])), "kg"
                qty = r2(disponible(pr) / LB_POR_KG)
            else:
                precio, uom = r2(precio_lb_cola(next(n for (x, n, _p) in TALLAS_COLA if x == pr["size"]), 0, sesgos[emp]["base"])), "lb"
                qty = r2(disponible(pr))
            f8.rec(f"demo_ms_aviso_{na:04d}", "shrimp.lot.alert", {
                "packer_partner_id": R(emp), "product_id": R(l),
                "seller_partner_id": R(UR + pr["sellerx"]), "price_list_id": R(listas_vigentes[emp]),
                "price": precio, "uom": uom, "qty": qty, "amount": r2(precio * qty),
                "sent_date": DT(max(-3, min(-1, pr["off"])), RNG.choice([7, 15]), RNG.randint(0, 5))})

    coherencia_demo_original(M)


def renglones(arch, lx, pres, off, sesgo):
    k = 0
    if "entero" in pres:
        for sx, n in ENTERO_LISTA:
            if n == "10/20" and RNG.random() < 0.5:
                continue
            ab = precio_kg(n, off, sesgo["base"] + (sesgo["grande"] if n in ("10/20", "20/30", "30/40") else 0))
            for q, p in (("ab", ab), ("c", ab - RNG.uniform(0.35, 0.6))):
                k += 1
                arch.rec(f"{lx}_r{k}", "shrimp.price.list.line", {
                    "price_list_id": R(lx), "size_grade_id": R(sx), "quality": q, "uom": "kg",
                    "price": r2(p)})
    if "cola" in pres:
        for sx, n in COLA_LISTA:
            a = precio_lb_cola(n, off, sesgo["base"])
            filas = [("directa", "a", a), ("directa", "b", a - RNG.uniform(0.25, 0.45))]
            if n in ("21/25", "26/30", "31/35", "36/40", "41/50"):
                filas.append(("sobrante", "a", a - RNG.uniform(0.15, 0.35)))
            for canal, q, p in filas:
                k += 1
                arch.rec(f"{lx}_r{k}", "shrimp.price.list.line", {
                    "price_list_id": R(lx), "size_grade_id": R(sx), "channel": canal, "quality": q,
                    "uom": "lb", "price": r2(p)})
    for j, (nombre, nota) in enumerate(RNG.sample(BONOS, RNG.randint(2, 4)), 1):
        arch.rec(f"{lx}_b{j}", "shrimp.price.list.bonus", {
            "price_list_id": R(lx), "name": nombre, "amount": r2(RNG.choice([0.03, 0.05, 0.08, 0.1, 0.15])),
            "note": nota, "sequence": j * 10})


# ============================================================================
def reservas(M, f7, emps, destinatarios, sesgos, pasadas, futuras, listas_vigentes):
    """Declaraciones de cosecha con sus compromisos.

    Restricción a sortear: un compromiso exige "vale hasta" >= hoy y <= fecha
    de la cosecha. Para las cosechas YA ocurridas, la declaración se crea en
    borrador con fecha futura, se le crean los compromisos y después se le
    escriben sus datos reales (fecha pasada, estado y cosecha). Es lo que
    permite el modelo: mientras está en borrador sus términos se pueden
    corregir. El "vale hasta" de esos compromisos queda en el día de carga.
    """
    P = M["partners"]
    nf = 0

    def cabecera(fx, cam, pond, fac, exp_off, lb, pres, talla, dests, estado, extra=None):
        v = {"farmer_partner_id": R(UR + cam), "pond_id": R(MP + pond), "facility_id": R(MP + fac),
             "expected_date": D(exp_off), "expected_lb": lb, "presentation": pres,
             "size_grade_id": R(MP + talla), "tolerance_lb_pct": float(RNG.choice([10, 15, 20, 20, 25])),
             "tolerance_size_steps": RNG.choice([1, 1, 2]), "date_tolerance_days": RNG.choice([5, 7, 7, 10]),
             "currency_id": R("base.USD"), "recipient_ids": M2M(dests),
             "open_call": RNG.random() < 0.25, "state": estado,
             "notes": RNG.choice(["Muestreo con atarraya: 22 g promedio, buena uniformidad.",
                                  "Cosecha con máximo aguaje; se puede adelantar dos días.",
                                  "Piscina con aireación; recambio diario del 10 %.", None])}
        v.update(extra or {})
        f7.rec(fx, "shrimp.harvest.forecast", v)
        return v

    def talla_de(pr):
        return pr["size"], pr["presentation"]

    f7.seccion("Cosechas ya ocurridas que se habían reservado (borrador futuro -> datos reales)")
    for pr in pasadas:
        nf += 1
        fx = f"demo_ms_res_{nf:03d}"
        s = pr["siembra"]
        comprador = pr["comprador"]
        otras = RNG.sample([e for e in emps if e != comprador], RNG.randint(0, 2))
        dests = [comprador] + otras
        exp_lb = round(pr["qty"] * RNG.uniform(0.92, 1.08) / 10) * 10
        talla, pres = talla_de(pr)
        cabecera(fx, s["cam"], s["pond"], s["fac"], RNG.randint(20, 40), exp_lb, pres, talla, dests, "draft")
        compras = pr.get("compras", [])
        tx_ok = next((t for t in compras if t["buyer"] == SP + comprador and t["estado"] in ("done", "confirmed")), None)
        tx_rota = next((t for t in compras if t["buyer"] == SP + comprador and t["estado"] == "cancel"), None)
        # Una compra todavía en verificación o esperando firmas solo pudo
        # iniciarse sobre un compromiso CUMPLIDO: queda enlazada a él. Antes
        # salía un compromiso "pendiente de confirmar" con una compra en curso.
        tx_curso = next((t for t in compras if t["buyer"] == SP + comprador
                         and t["estado"] in ("pending_verification", "pending_acceptance")), None)
        # destino del compromiso de la empacadora compradora
        if tx_ok:
            destino = RNG.choices(["honored", "honored_conf"], [60, 40])[0]
        elif tx_rota:
            destino = RNG.choice(["broken", "released"])
        else:
            destino = RNG.choice(["to_confirm", "honored_conf", "honored", "broken"])
            if tx_curso and destino in ("honored", "honored_conf"):
                # Cumplido y con la compra en curso: se enlaza la compra.
                f7.fn_write("shrimp.harvest.commitment", [SP + fx + "_c1"],
                            [("state", "=", "honored"), ("transaction_id", "=", False)],
                            {"transaction_id": R(tx_curso["xid"])})
            if tx_curso and destino == "to_confirm":
                # Una compra en curso solo pudo iniciarse sobre un compromiso
                # CUMPLIDO. Los registros se generan igual que antes (mismas
                # tiradas del azar, para no cambiar bases que ya tienen la
                # demo) y una escritura idempotente —que corre al instalar y
                # en cada -u— deja el compromiso cumplido, enlazado a su compra
                # y con las dos firmas aceptadas.
                if True:
                    f7.fn_write("shrimp.harvest.commitment", [SP + fx + "_c1"],
                                [("state", "=", "to_confirm")],
                                {"state": "honored", "transaction_id": R(tx_curso["xid"]),
                                 "deviation_notes": "Salió una talla por debajo de la declarada; "
                                                    "ambas partes la aceptaron."})
                    for rol in ("farmer", "packer"):
                        f7.fn_write("shrimp.harvest.confirmation", [SP + fx + "_c1_conf1_" + rol],
                                    [("decision", "=", "pending")],
                                    {"decision": "accepted", "decided_at": DT(pr["off"], 12, 0)},
                                    context="{'active_test': False}")
        # Vigencia de los compromisos: nunca «hoy» (días=0). La demo calcula la
        # fecha con DateTime.today() (UTC del servidor) y la restricción
        # _check_vigencia la compara con context_today (zona del usuario que
        # carga la demo). Con una zona al este de UTC, cerca de medianoche
        # «hoy UTC» ya es «ayer» para el usuario y la instalación fallaba con
        # «Un compromiso que ya venció no se puede enviar». Con un día de
        # margen vale a cualquier hora y en cualquier zona (máx. +14 h).
        # max() sobre la MISMA tirada: no cambia la secuencia del azar.
        modo = RNG.choice(["fijo", "lista"])
        lb_comp =round(min(exp_lb, pr["qty"]) * RNG.uniform(0.8, 1.0) / 10) * 10
        cx = f"{fx}_c1"
        c = {"forecast_id": R(fx), "packer_partner_id": R(comprador), "committed_lb": float(lb_comp),
             "price_mode": modo, "currency_id": R("base.USD"), "valid_until": D(max(1, RNG.randint(0, 10))),
             "notes": "Precio sujeto a informe de verificación en planta.",
             "accepted_at": DT(pr["off"] - RNG.randint(15, 30), 10, 0)}
        if modo == "fijo":
            c.update({"price_per_lb": r2(pr["price"] * RNG.uniform(0.98, 1.02)),
                      "step_delta_per_lb": r2(RNG.uniform(0.08, 0.2))})
        else:
            c.update({"price_floor_per_lb": r2(pr["price"] * RNG.uniform(0.85, 0.93)),
                      "price_list_id": R(listas_vigentes.get(comprador)) if listas_vigentes.get(comprador) else None})
        liquidadas = float(min(lb_comp, pr["qty"]))
        precio_liq = pr["price"]
        if destino in ("honored", "honored_conf"):
            c.update({"state": "honored", "settled_lb": liquidadas, "settled_price_per_lb": precio_liq,
                      "settled_steps": 0 if destino == "honored" else RNG.choice([1, -1]),
                      "floor_applied": modo == "lista" and RNG.random() < 0.2,
                      "product_id": R(pr["xid"]),
                      "transaction_id": R(tx_ok["xid"]) if tx_ok else None,
                      "deviation_notes": None if destino == "honored" else
                      "Salió una talla por debajo de la declarada; ambas partes la aceptaron."})
        elif destino == "to_confirm":
            c.update({"state": "to_confirm", "settled_lb": liquidadas, "settled_price_per_lb": precio_liq,
                      "settled_steps": RNG.choice([1, 2]), "product_id": R(pr["xid"]),
                      "deviation_notes": "La talla real quedó fuera de la banda declarada."})
        elif destino == "released":
            c.update({"state": "released", "settled_lb": liquidadas, "settled_price_per_lb": precio_liq,
                      "product_id": R(pr["xid"]),
                      "deviation_notes": "Cosecha 30 % por debajo del piso de la banda."})
        else:
            c.update({"state": "broken", "break_side": RNG.choice(["farmer", "packer"]),
                      "broken_at": DT(pr["off"], 16, 0),
                      "break_reason": "La planta no recibió el camarón en la fecha pactada por falta de cámara."})
            c["broken_by_partner_id"] = R(comprador if c["break_side"] == "packer" else UR + s["cam"])
        f7.rec(cx, "shrimp.harvest.commitment", c)
        for k, e in enumerate(otras, 2):
            f7.rec(f"{fx}_c{k}", "shrimp.harvest.commitment", {
                "forecast_id": R(fx), "packer_partner_id": R(e), "committed_lb": float(round(lb_comp * 0.7 / 10) * 10),
                "price_mode": "lista", "price_floor_per_lb": r2(pr["price"] * 0.88),
                "currency_id": R("base.USD"), "valid_until": D(max(1, RNG.randint(0, 10))),
                "state": RNG.choice(["rejected", "withdrawn", "lapsed"]),
                "notes": "Oferta de respaldo por si la principal no se concreta."})
        rondas = []
        if destino == "honored_conf":
            # 1.ª ronda: la empacadora no la aceptaba; se renegoció y firmaron la 2.ª
            rondas = [(1, "accepted", "rejected", False), (2, "accepted", "accepted", True)]
        elif destino == "released":
            rondas = [(1, "accepted", "rejected", True)]
        elif destino == "to_confirm":
            rondas = [(1, RNG.choice(["accepted", "pending"]), "pending", True)]
        for ronda, dec_f, dec_p, vigente in rondas:
            for rol, partner, dec in (("farmer", UR + s["cam"], dec_f), ("packer", comprador, dec_p)):
                f7.rec(f"{cx}_conf{ronda}_{rol}", "shrimp.harvest.confirmation", {
                    "commitment_id": R(cx), "ronda": ronda, "role": rol, "partner_id": R(partner),
                    "decision": dec, "active": vigente,
                    "archived_at": None if vigente else DT(pr["off"], 17, 0),
                    "archive_reason": None if vigente else "Se reabrió la confirmación con un precio ajustado por talla.",
                    "reason": {"accepted": None, "pending": None}.get(dec, "La talla salió dos escalones más chica; así no nos sirve para el pedido."),
                    "decided_at": None if dec == "pending" else DT(pr["off"] - (1 if ronda == 1 and not vigente else 0), RNG.randint(9, 16), 0),
                    "frozen_expected_lb": float(exp_lb), "frozen_actual_lb": float(pr["qty"]),
                    "frozen_lb_min": r2(exp_lb * 0.8), "frozen_lb_max": r2(exp_lb * 1.2),
                    "frozen_settled_lb": liquidadas, "frozen_price_per_lb": precio_liq,
                    "frozen_steps": 1, "frozen_days": RNG.randint(0, 4),
                    "frozen_reason": "La talla real quedó fuera de la banda declarada."})
        # ahora sí, la declaración con sus datos reales
        f7.fn_write("shrimp.harvest.forecast", [SP + fx], [("state", "=", "draft")], {
            "expected_date": D(pr["off"] + RNG.randint(-3, 3)), "state": "harvested",
            "actual_date": D(pr["off"]), "actual_lb": float(pr["qty"]),
            "actual_size_grade_id": R(MP + talla), "product_id": R(pr["xid"])})

    f7.seccion("Declaraciones vencidas y canceladas")
    for i in range(14):
        nf += 1
        fx = f"demo_ms_res_{nf:03d}"
        cam = RNG.choice([c for c in M["cam"] if M["ponds"][c]])
        pond = RNG.choice([p for p in M["ponds"][cam] if p[2] == "earth"] or M["ponds"][cam])
        pres = RNG.choice(["entero", "cola"])
        talla = RNG.choice(TALLAS_ENTERO if pres == "entero" else TALLAS_COLA)[0]
        dests = RNG.sample(emps, 2)
        cabecera(fx, cam, pond[0], pond[1], 25, float(RNG.randrange(15000, 50000, 500)), pres, talla, dests, "draft")
        cancelada = i % 2 == 0
        f7.rec(f"{fx}_c1", "shrimp.harvest.commitment", {
            "forecast_id": R(fx), "packer_partner_id": R(dests[0]), "committed_lb": 10000.0,
            "price_mode": "lista", "price_floor_per_lb": r2(RNG.uniform(1.9, 2.4)),
            "currency_id": R("base.USD"), "valid_until": D(max(1, RNG.randint(0, 10))), "state": "lapsed",
            "notes": "Compromiso que quedó sin efecto."})
        exp = -RNG.randint(20, 200)
        upd = {"expected_date": D(exp), "state": "cancelled" if cancelada else "expired"}
        if cancelada:
            upd.update({"cancel_reason": RNG.choice([
                "Mortalidad por mancha blanca: se cosechó de emergencia y se vendió en el mercado local.",
                "Se adelantó la cosecha por baja de oxígeno; no dio tiempo a reservar.",
                "La piscina se transfirió a otra finca del grupo."]),
                "cancelled_at": DT(exp - 5, 11, 0),
                "cancelled_by_uid": R(P[cam]["user"]) if P[cam].get("user") else None})
        f7.fn_write("shrimp.harvest.forecast", [SP + fx], [("state", "=", "draft")], upd)

    f7.seccion("Cosechas por venir: publicadas, comprometidas y borradores")
    RNG.shuffle(futuras)
    for s in futuras[:60]:
        nf += 1
        fx = f"demo_ms_res_{nf:03d}"
        if s["cam"].startswith("demo_ms_cam_grupo"):
            continue
        exp_off = s["off"] + RNG.randint(95, 120)
        if exp_off < 4:
            exp_off = RNG.randint(4, 60)
        pres = RNG.choices(["entero", "cola"], [72, 28])[0]
        talla = RNG.choice(TALLAS_ENTERO[1:6] if pres == "entero" else TALLAS_COLA[1:6])
        gram = 1000.0 / talla[2] if pres == "entero" else 453.6 / talla[2] / 0.65
        lb = max(5000.0, round(s["qty"] * 1000 * 0.62 * gram / 453.6 / 10) * 10)
        candidatas = [e for e in emps if s["cam"] in destinatarios[e]] or emps
        dests = RNG.sample(candidatas, min(len(candidatas), RNG.randint(1, 3)))
        estado = RNG.choices(["draft", "published", "committed"], [15, 45, 40])[0]
        if estado == "draft":
            dests = dests[:1]
        cabecera(fx, s["cam"], s["pond"], s["fac"], exp_off, lb, pres, talla[0], dests, estado)
        if estado == "draft":
            continue
        precio_ref = (precio_kg(talla[1], exp_off, 0) / LB_POR_KG if pres == "entero"
                      else precio_lb_cola(talla[1], exp_off, 0) * 0.62)
        aceptada = dests[0] if estado == "committed" else None
        for k, e in enumerate(dests, 1):
            fijo = exp_off <= 45 and RNG.random() < 0.6
            st = "sent"
            if estado == "committed":
                st = "accepted" if e == aceptada else "rejected"
            elif RNG.random() < 0.12:
                st = "withdrawn"
            c = {"forecast_id": R(fx), "packer_partner_id": R(e),
                 "committed_lb": float(round(lb * RNG.uniform(0.7, 1.0) / 10) * 10),
                 "price_mode": "fijo" if fijo else "lista", "currency_id": R("base.USD"),
                 "valid_until": D(max(1, exp_off - RNG.randint(2, 15)) if exp_off > 3 else exp_off),
                 "state": st, "notes": "Retiro con camión propio en la finca." if RNG.random() < 0.3 else None}
            if fijo:
                c.update({"price_per_lb": r2(precio_ref * RNG.uniform(0.96, 1.03)),
                          "step_delta_per_lb": r2(RNG.uniform(0.08, 0.2))})
            else:
                c["price_floor_per_lb"] = r2(precio_ref * RNG.uniform(0.84, 0.92))
            if st == "accepted":
                c["accepted_at"] = DT(-RNG.randint(1, 10), 10, 0)
            f7.rec(f"{fx}_c{k}", "shrimp.harvest.commitment", c)


# ============================================================================
def _parse(ruta):
    out = {}
    for rec in ET.parse(ruta).getroot().iter("record"):
        d = {"_model": rec.get("model")}
        for f in rec.findall("field"):
            d[f.get("name")] = f.get("ref") or (f.text or "").strip()
        out[rec.get("id")] = d
    return out


def coherencia_demo_original(M):
    """Ajusta la demo original a las reglas de negocio actuales.

    * camaronera_to_buyer solo lo compra una empacadora (shrimp_packer).
    * El estado de la compra sigue al de su verificación y a las posturas.
    * Toda compra confirmada/completada tiene movimiento y comisión, y su
      lote de origen descuenta lo vendido.
    Va en noupdate: se aplica al instalar. En una base que ya tenía la demo,
    un -u NO reescribe estos registros (Odoo no toca registros noupdate).
    """
    f = Archivo("shrimp_packer", "demo_09_masivo_coherencia_demo_original.xml",
                "Ajuste de coherencia de la demo ORIGINAL (marketplace y verificación)\n"
                "     a las reglas de negocio vigentes.")
    M["archivos"].append(f)
    txs = _parse(ADDONS / "shrimp_marketplace/demo/demo_10_transactions.xml")
    prods = _parse(ADDONS / "shrimp_marketplace/demo/demo_06_products.xml")
    vers = {}
    for nombre in ("demo_04_verifications_adult.xml", "demo_05_verifications_larvae.xml"):
        for k, v in _parse(ADDONS / "shrimp_verification/demo" / nombre).items():
            if v["_model"] == "shrimp.verification":
                vers[k] = v
    accs = {k: v for k, v in _parse(ADDONS / "shrimp_verification/demo/demo_06_acceptances.xml").items()}
    revs = {k: v for k, v in _parse(ADDONS / "shrimp_verification/demo/demo_07_verifier_reviews.xml").items()}

    consumo = {}
    NUEVA = [("needs_verification", "=", False)]          # aún sin ajustar
    SIN_PAGADOR = [("fee_payer_partner_id", "=", False)]
    f.seccion("Compras de camarón adulto: el comprador pasa a ser una empacadora")
    nuevo_comprador = {}
    adultas = [k for k, t in txs.items() if t.get("transaction_type") == "camaronera_to_buyer"]
    for i, k in enumerate(sorted(adultas, key=lambda x: int(x.split("_")[-1]))):
        emp = VIEJAS_EMP[i % len(VIEJAS_EMP)]
        nuevo_comprador[k] = emp
        f.fn_write("shrimp.transaction", [MP + k], [("buyer_partner_id.shrimp_user_type", "!=", "empacadora")],
                   {"buyer_partner_id": R(emp)})

    f.seccion("Estado de cada compra según su verificación y posturas")
    for vid, v in sorted(vers.items()):
        tk = v["transaction_id"].replace(MP, "")
        t = txs[tk]
        st = v["state"]
        comprador = nuevo_comprador.get(tk) or t["buyer_partner_id"]
        comprador_ref = comprador if comprador.startswith("demo_pk_") else MP + comprador
        mis_acc = {a["role"]: (ak, a) for ak, a in accs.items() if a.get("verification_id") == vid}
        if st in ("received", "assigned", "in_field", "done"):
            f.fn_write("shrimp.transaction", [MP + tk], NUEVA, {"state": "pending_verification", "needs_verification": True})
        elif st == "rejected":
            f.fn_write("shrimp.transaction", [MP + tk], NUEVA, {"state": "cancel", "needs_verification": True})
            f.fn_write("shrimp.verification", [SV + vid], SIN_PAGADOR, {"acceptance_state": "na",
                                                     "fee_payer_partner_id": R(MP + t["seller_partner_id"])})
            for rol, (ak, _a) in mis_acc.items():
                f.partes.append(f'        <delete model="shrimp.verification.acceptance" id="{SV}{ak}"/>\n')
        else:  # approved / approved_obs con la ronda cerrada: las dos partes aceptan
            for rol, (ak, a) in mis_acc.items():
                vals = {"decision": "accepted", "reason": "Conforme con el informe del verificador.",
                        "counter_price": 0.0}
                if rol == "buyer" and tk in nuevo_comprador:
                    vals["partner_id"] = R(comprador_ref)
                partner = vals.pop("partner_id", None)
                # la firma solo la escriben las acciones del modelo; aquí se
                # reconstruye la demo, con el mismo permiso que usan ellas
                f.fn_write("shrimp.verification.acceptance", [SV + ak], [("decision", "!=", "accepted")], vals,
                           context="{'_shrimp_firma_ok': True}")
                if partner:
                    f.fn_write("shrimp.verification.acceptance", [SV + ak],
                               [("partner_id.shrimp_user_type", "!=", "empacadora")], {"partner_id": partner})
            f.fn_write("shrimp.verification", [SV + vid], SIN_PAGADOR, {"acceptance_state": "closed",
                                                     "fee_payer_partner_id": R(comprador_ref)})
            f.fn_write("shrimp.transaction", [MP + tk], NUEVA, {"state": "done", "needs_verification": True})
            qty = float(t["transaction_qty"])
            prod = t["product_id"]
            consumo[prod] = consumo.get(prod, 0.0) + qty
            fecha = t.get("sold_date") or t.get("desired_date")
            f.rec(f"demo_ms_fix_mv_{tk}", "shrimp.stock.move", {
                "product_id": R(MP + prod), "source_partner_id": R(MP + t["seller_partner_id"]),
                "dest_partner_id": R(comprador_ref), "qty": qty, "transaction_id": R(MP + tk),
                "date": f"{fecha} 15:00:00"})
            uom = prods[prod].get("uom_id", "uom_millar")
            rate = TARIFA_CENTAVOS.get(uom, 1.0)
            f.rec(f"demo_ms_fix_chg_{tk}", "shrimp.charge", cobro_demo(
                charge_type="commission",
                transaction_id=R(MP + tk), seller_partner_id=R(MP + t["seller_partner_id"]),
                payer_partner_id=R(MP + t["seller_partner_id"]),
                buyer_partner_id=R(comprador_ref), product_id=R(MP + prod), qty=qty,
                uom_id=R(MP + uom), rate_cents=rate, invoice_qty=qty, unit_amount=rate / 100.0,
                amount=r2(qty * rate / 100.0),
                currency_id=R("base.USD"), date=f"{fecha} 18:00:00"))
        # la calificación al verificador la firma el comprador (ahora la empacadora)
        for rk, r in revs.items():
            if r.get("verification_id") == vid and tk in nuevo_comprador:
                f.fn_write("shrimp.verifier.review", [SV + rk],
                           [("reviewer_partner_id.shrimp_user_type", "!=", "empacadora")],
                           {"reviewer_partner_id": R(comprador_ref)})

    f.seccion("Lote de origen de cada producto: descuenta lo vendido")
    for prod, qty in sorted(consumo.items()):
        ini = float(prods[prod]["initial_qty"])
        disp = max(0.0, ini - qty)
        # solo si el lote sigue intacto (idempotente y no pisa consumos reales)
        expr = (f"[obj().search([('product_id', '=', obj().env.ref('{MP}{prod}', False).id), "
                f"('origin_move_id', '=', False), ('available_qty', '=', {ini})]).ids, "
                f"{{'available_qty': {disp}, 'state': '{'consumed' if disp <= 0 else 'available'}'}}]")
        f.escrituras.append(f'        <function model="shrimp.stock.lot" name="write" eval="{expr}"/>\n')
