# -*- coding: utf-8 -*-
"""Historial de compras verificadas de AQUAGOLD (shrimp_packer,
demo_12_masivo_aquagold.xml).

AQUAGOLD (empacadora.01@camaronera.test) es la empacadora con la que se
enseña la plataforma, pero en la demo masiva no compraba con verificación:
el ranking de proveedores (/marketplace/suppliers), el tablero de la portada
y el resumen de «Mi panel» le salían vacíos. Aquí recibe un historial de
cuatro proveedores (camaroneras de la demo masiva), escrito con el MISMO
motor que el resto de compras de adulto (verificacion.compra_verificada):
cosecha de ENGORDE con presentación y talla, lote de stock descontado,
despacho, verificación con líneas por talla y conteos, posturas de las dos
partes, honorario, comisión, lote del comprador y reseñas.

Perfiles a propósito, para que el ranking diga algo:
  1. 5 lotes, rendimiento y clase A altos, todos aprobados -> líder.
  2. 4 lotes, algo por debajo, uno aprobado con observaciones.
  3. 3 lotes, el más flojo de los que tienen muestra suficiente.
  4. 2 lotes: «muestra corta», fuera del podio aunque rinda bien.

Fechas relativas al día de carga (últimos ~5 meses). Se ejecuta el ÚLTIMO y
con su PROPIA semilla: guarda y restaura comun.RNG, así que el resto de la
demo sale idéntico byte a byte.
"""
from .comun import RNG, SP, Archivo, r2
from .marketplace import IMGS, SANIDAD_OK, TALLAS_ENTERO, lote_vals, prod_vals
from .verificacion import compra_verificada

SEMILLA_AQUAGOLD = 20261011
PREFIJO = "demo_ag_"
COMPRADOR = SP + "demo_pk_aquagold"

# (lotes, rango clasificado/neto, rango clase A, veredictos, tallas)
PERFILES = [
    (5, (0.975, 0.985), (0.76, 0.84), ["approved"] * 5,
     ["size_entero_3040", "size_entero_3040", "size_entero_2030", "size_entero_3040",
      "size_entero_4050"]),
    (4, (0.955, 0.97), (0.64, 0.74), ["approved", "approved_obs", "approved", "approved"],
     ["size_entero_3040", "size_entero_4050", "size_entero_3040", "size_entero_4050"]),
    (3, (0.935, 0.955), (0.56, 0.66), ["approved", "approved", "approved_obs"],
     ["size_entero_4050", "size_entero_5060", "size_entero_4050"]),
    (2, (0.97, 0.985), (0.72, 0.8), ["approved", "approved"],
     ["size_entero_3040", "size_entero_2030"]),
]


def construir(M):
    estado = RNG.getstate()
    RNG.seed(SEMILLA_AQUAGOLD)
    try:
        _aquagold(M)
    finally:
        RNG.setstate(estado)


def _nombre_corto(P, x):
    return P[x]["nombre"].split(" S.A")[0].split(" Cía")[0]


def _aquagold(M):
    P = M["partners"]
    f = Archivo("shrimp_packer", "demo_12_masivo_aquagold.xml",
                "Historial de compras verificadas de AQUAGOLD con cuatro proveedores: alimenta el\n"
                "     ranking de proveedores, el tablero de la portada y el resumen de Mi panel.")
    M["archivos"].append(f)

    # Camaroneras con piscina de tierra que no sean las del grupo (esas
    # tienen su propia historia en la demo de reservas).
    cams = [c for c in M["cam"] if not c.startswith("demo_ms_cam_grupo")
            and any(t[2] == "earth" for t in M["ponds"].get(c, []))]
    proveedores = RNG.sample(cams, len(PERFILES))
    tallas = {x: (n, piezas) for x, n, piezas in TALLAS_ENTERO}

    # 1) Planificación: cosechas y la compra de cada una (la compra se cierra
    #    y recibe, así que consume lo comprado del lote del vendedor).
    plan = []
    n = 0
    for k, (cam, (lotes, r_cls, r_a, veredictos, tallas_lote)) in enumerate(
            zip(proveedores, PERFILES), 1):
        ponds = [t for t in M["ponds"][cam] if t[2] == "earth"]
        p = P[cam]
        for j in range(lotes):
            n += 1
            talla_x = tallas_lote[j]
            talla_n, piezas = tallas[talla_x]
            # Una compra cada ~3-4 semanas por proveedor, escalonadas entre
            # proveedores; la más reciente hace ~10 días.
            off = -(12 + j * 26 + k * 5 + RNG.randint(0, 4))
            pond = ponds[j % len(ponds)]
            qty_lote = float(RNG.choice([14000, 16000, 18000, 22000, 26000]))
            qty = round(qty_lote * RNG.uniform(0.85, 0.97) / 10) * 10
            pr = dict(
                xid=f"{PREFIJO}prod_{n:02d}", kind="eng", sellerx=cam, rol="camaronera",
                species="shrimp_species_vannamei", stage="shrimp_stage_engorde",
                genetics="shrimp_genetics_spr",
                name=f"Cosecha entero {talla_n} - {_nombre_corto(P, cam)} (AQUAGOLD)",
                size_mg=r2(1000.0 / piezas * 1000), surv=r2(RNG.uniform(58, 72)),
                health=RNG.choice(SANIDAD_OK), qty=qty_lote, uom="uom_libra",
                presentation="entero", size=talla_x, price=r2(RNG.uniform(2.45, 3.05)),
                location=f"{p['ciudad']}, {p['provincia']}", state="published",
                off=off - 1, entrega=off + 1, ventana=8,
                facility=pond[1], pond=pond[0], fotos=RNG.sample(IMGS, 3),
                consumido=qty, lot=f"{PREFIJO}lot_{n:02d}", batch=f"AG-{k}{j + 1:02d}",
                notas="Cosecha vendida a AQUAGOLD con verificación en campo.")
            plan.append((n, pr, qty, off, {
                "escenario": "closed_done", "vstate": veredictos[j],
                "rango_clasificado": r_cls, "rango_clase_a": r_a,
                "planta": "Planta AQUAGOLD Durán"}))

    # 2) Cosechas con su lote ya descontado (vendido entero si no queda nada).
    f.seccion("Cosechas de engorde de los proveedores de AQUAGOLD y su lote de stock")
    for _n, pr, _qty, _off, _extra in plan:
        if pr["qty"] - pr["consumido"] <= 0.001:
            pr["state"] = "sold"
        f.rec(pr["xid"], "shrimp.product", prod_vals(pr), context="{'skip_initial_lot': True}")
        f.rec(pr["lot"], "shrimp.stock.lot", lote_vals(pr))
        pr["consumido"] = 0.0     # compra_verificada lo vuelve a sumar

    # 3) Las compras verificadas, con el mismo motor que el resto de la demo.
    f.seccion("Compras verificadas de AQUAGOLD (cerradas y recibidas)")
    for n, pr, qty, off, extra in plan:
        compra_verificada(M, f, n, pr, COMPRADOR, qty, off, "camaronera_to_buyer", "adult",
                          PREFIJO, extra)
