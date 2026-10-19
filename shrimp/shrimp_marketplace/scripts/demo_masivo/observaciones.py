# -*- coding: utf-8 -*-
"""«Estado real del lote»: observaciones del vendedor con parámetros de agua.

Se ejecuta al final y con su PROPIO generador aleatorio (semilla distinta):
no consume números de comun.RNG, así que el resto de la demo sale idéntico.
Escribe shrimp_marketplace/demo/demo_18_masivo_observaciones.xml.

Para unos pocos lotes publicados (larva de laboratorio y camarón juvenil o de
engorde) deja 3-4 observaciones después de la publicación, con temperatura,
salinidad, oxígeno disuelto, pH y cantidad estimada. En dos de ellos la última
observación diverge de lo publicado (el lote avanzó de etapa, o la cantidad
estimada es menor que el stock): es lo que ve el vendedor como aviso y el
comprador en «Estado real del lote». Sin correos al cargar la demo
(shrimp_evolution_no_notify).
"""
import random

from .comun import DT, MP, R, Archivo, r2

SEMILLA_OBS = 20261014

ORDEN_LARVA = ["shrimp_stage_pl5", "shrimp_stage_pl8", "shrimp_stage_pl10", "shrimp_stage_pl12",
               "shrimp_stage_pl15", "shrimp_stage_pl20"]
NOTAS = [
    "Muestreo de 100 animales en el tanque. Buen nado y respuesta a la luz.",
    "Recambio de agua del 30 % por la mañana. Animales activos.",
    "Alimentación ajustada al consumo. Intestino lleno en la mayoría.",
    "Muestreo con atarraya en tres puntos de la piscina.",
    "Ligera baja de oxígeno en la madrugada; se encendieron aireadores.",
]
SANIDAD = [
    "Sin signos de enfermedad.",
    "Hepatopáncreas con buena coloración, sin necrosis.",
    "Branquias limpias, sin manchas.",
]


def construir(M):
    rng = random.Random(SEMILLA_OBS)
    P = M["partners"]
    productos = M.get("mkt_productos", {})
    f = Archivo("shrimp_marketplace", "demo_18_masivo_observaciones.xml",
                "Estado real del lote: observaciones del vendedor con parámetros de agua")
    M["archivos"].append(f)
    f.seccion("Observaciones del vendedor después de publicar")

    def elegir(kinds, n, filtro=lambda pr: True):
        cands = [pr for _x, pr in sorted(productos.items())
                 if pr["kind"] in kinds and pr["state"] == "published" and pr.get("active", True)
                 and "." not in pr["sellerx"] and filtro(pr)]
        return rng.sample(cands, min(n, len(cands)))

    # La larva divergente va primero y SIN ventas: así el vendedor ve el botón
    # «Actualizar publicación a la etapa observada» (con ventas la etapa no se
    # puede cambiar y se le explica).
    divergente_larva = elegir(("lab",), 1, lambda pr: not pr.get("consumido"))
    larvas = divergente_larva + [pr for pr in elegir(("lab",), 4) if pr not in divergente_larva][:3]
    camarones = elegir(("juv", "eng"), 3)
    for i, pr in enumerate(larvas + camarones):
        es_larva = pr["kind"] == "lab"
        user = P.get(pr["sellerx"], {}).get("user")
        n = rng.randint(3, 4)
        divergente = i in (0, len(larvas))   # una larva y un camarón
        etapa = pr["stage"]
        for k in range(1, n + 1):
            off = -2 * (n - k) - 1
            ultima = k == n
            if es_larva and divergente and ultima and etapa in ORDEN_LARVA:
                pos = min(len(ORDEN_LARVA) - 1, ORDEN_LARVA.index(etapa) + 1)
                etapa_obs = ORDEN_LARVA[pos]
            elif not es_larva and divergente and ultima and etapa == "shrimp_stage_juvenil":
                etapa_obs = "shrimp_stage_engorde"
            else:
                etapa_obs = etapa
            crece = 1.0 + 0.05 * k if es_larva else 1.0 + 0.08 * k
            qty = pr["qty"] * (rng.uniform(0.55, 0.75) if divergente and ultima else rng.uniform(0.9, 1.05))
            f.rec(f"{pr['xid']}_obs_{k}", "shrimp.product.evolution", {
                "product_id": R(pr["xid"]),
                "origin": "observation",
                "date": DT(off, rng.randint(7, 11), rng.choice([0, 15, 30, 45])),
                "stage_id": R(MP + etapa_obs),
                "avg_size_mg": r2(pr["size_mg"] * crece),
                "survival_rate": r2(max(30.0, min(99.0, pr["surv"] - rng.uniform(0, 2.5) * k))),
                "health_status": rng.choice(SANIDAD),
                "note": rng.choice(NOTAS),
                "water_temp_c": r2(rng.uniform(27.0, 30.5) if es_larva else rng.uniform(25.5, 29.5)),
                "salinity_ppt": r2(rng.uniform(28.0, 34.0) if es_larva else rng.uniform(8.0, 22.0)),
                "dissolved_oxygen_mgl": r2(rng.uniform(4.8, 6.8)),
                "water_ph": r2(rng.uniform(7.6, 8.3)),
                "estimated_qty": r2(qty),
                "user_id": R(user) if user else None,
            }, context="{'shrimp_evolution_no_notify': True}")
