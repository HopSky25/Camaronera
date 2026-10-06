# -*- coding: utf-8 -*-
"""Fichas de calidad de los lotes de larva y nauplio (shrimp.product.quality)
y certificados PCR por lote (Fase 1 de la propuesta de menús).

Se ejecuta DESPUÉS de todo lo demás y con su PROPIO generador aleatorio
(semilla distinta): no consume números de comun.RNG, así que el resto de la
demo sale idéntico byte a byte. Escribe un archivo nuevo,
shrimp_marketplace/demo/demo_16_masivo_calidad.xml.

Mezcla a propósito:
- fichas recientes con los cuatro PCR negativos (lo normal),
- fichas antiguas (más de 30 días: «Más de 30 días» en la ficha pública),
- fichas con PCR parcial o no realizado,
- algún PCR positivo en lotes descartados (cancelados),
- certificados PCR-WSSV / PCR-AHPND por lote, unos vigentes y otros ya
  vencidos (el filtro «PCR vigente» del catálogo solo cuenta los vigentes).
"""
import random

from .comun import B64, D, MP, R, UR, Archivo, pdf_minimo

SEMILLA_CALIDAD = 20261003

LABS_ANALISIS = [
    "Laboratorio de Diagnóstico Acuícola - ESPOL",
    "INP - Instituto Público de Investigación de Acuicultura y Pesca",
    "Laboratorio de Patología Acuícola del Pacífico",
    "Biolab Diagnóstico Molecular (Guayaquil)",
    "Laboratorio propio (PCR en tiempo real)",
]
OBS = [
    "Muestra de 150 animales por tanque. Branquias y hepatopáncreas sin lesiones.",
    "Prueba de estrés con choque osmótico a 0 ppt durante 30 min.",
    "Prueba de estrés con formol 100 ppm, 30 min. Buena actividad natatoria.",
    "Uniformidad medida por conteo de rostro y longitud total.",
    "Animales activos, intestino lleno, sin necrosis.",
]


def construir(M):
    rng = random.Random(SEMILLA_CALIDAD)
    P = M["partners"]
    productos = M.get("mkt_productos", {})
    nombres_tanque = {}
    for px, tanques in M.get("ponds", {}).items():
        for t in tanques:
            nombres_tanque[t[0]] = t[4]

    f = Archivo("shrimp_marketplace", "demo_16_masivo_calidad.xml",
                "Fichas de calidad de lotes de larva y nauplio y certificados PCR por lote")
    f.seccion("Informes de análisis (PDF)")

    candidatos = [pr for _x, pr in sorted(productos.items())
                  if pr["kind"] in ("sem", "lab", "res") and "." not in pr["sellerx"]]
    elegidos = [pr for pr in candidatos
                if (pr["state"] == "published" and rng.random() < 0.55)
                or (pr["state"] == "cancel" and rng.random() < 0.5)
                or (pr["state"] == "draft" and rng.random() < 0.3)]

    informes = {}
    for pr in elegidos:
        if pr["state"] == "draft" or rng.random() < 0.35:
            continue
        ax = f"{pr['xid']}_calidad_pdf"
        f.rec(ax, "ir.attachment", {
            "name": f"Informe_PCR_{pr['batch']}.pdf", "type": "binary",
            "mimetype": "application/pdf",
            "res_model": "shrimp.product.quality",
            "datas": B64(pdf_minimo(f"Informe de análisis - lote {pr['batch']}", [
                f"Lote: {pr['name']}",
                "PCR en tiempo real: WSSV, IHHNV, AHPND y EHP.",
                "Prueba de estrés y uniformidad en muestra representativa.",
                "Documento de demostración generado automáticamente."])),
        })
        informes[pr["xid"]] = ax

    f.seccion("Fichas de calidad")
    n = 0
    for pr in elegidos:
        cuantas = 1 if rng.random() < 0.6 else 2
        for k in range(1, cuantas + 1):
            n += 1
            if pr["state"] == "cancel":
                perfil = "positivo"
            else:
                perfil = rng.choices(["negativo", "parcial", "no_realizado"], [70, 15, 15])[0]
            if perfil == "negativo":
                pcr = {c: "negativo" for c in ("pcr_wssv", "pcr_ihhnv", "pcr_ahpnd", "pcr_ehp")}
            elif perfil == "parcial":
                pcr = {"pcr_wssv": "negativo", "pcr_ihhnv": "no_realizado",
                       "pcr_ahpnd": "negativo", "pcr_ehp": "no_realizado"}
            elif perfil == "positivo":
                pcr = {"pcr_wssv": "negativo", "pcr_ihhnv": "negativo",
                       "pcr_ahpnd": "positivo", "pcr_ehp": "no_realizado"}
            else:
                pcr = {c: "no_realizado" for c in ("pcr_wssv", "pcr_ihhnv", "pcr_ahpnd", "pcr_ehp")}
            # La primera ficha es la más reciente; la segunda, de un muestreo anterior.
            # Una de cada tres «recientes» queda con más de 30 días.
            if k == 1:
                off = -rng.randint(1, 20) if rng.random() < 0.67 else -rng.randint(35, 90)
            else:
                off = -rng.randint(40, 120)
            vals = {
                "product_id": R(pr["xid"]),
                "sample_date": D(off),
                "stage_id": R(MP + pr["stage"]),
                "stress_test_survival": round(min(99.0, pr["surv"] + rng.uniform(-6, 4)), 1),
                "uniformity": round(rng.uniform(72, 95), 1),
                "tank": nombres_tanque.get(pr.get("pond")) or f"T-{rng.randint(1, 12):02d}",
                "analysis_lab": rng.choice(LABS_ANALISIS),
                "notes": rng.choice(OBS),
            }
            vals.update(pcr)
            if pr["kind"] == "res" and pr.get("lot"):
                vals["origin_lot_id"] = R(pr["lot"])
            if k == 1 and pr["xid"] in informes:
                vals["attachment_id"] = R(informes[pr["xid"]])
            f.rec(f"{pr['xid']}_calidad_{k}", "shrimp.product.quality", vals)

    f.seccion("Certificados PCR por lote (vigentes y vencidos)")
    for pr in candidatos:
        if pr["state"] != "published" or rng.random() >= 0.35:
            continue
        adj = P.get(pr["sellerx"], {}).get("adjunto")
        if not adj:
            continue
        cert = rng.choice(["shrimp_certificate_pcr_wssv", "shrimp_certificate_pcr_ahpnd"])
        vencido = rng.random() < 0.3
        emision = -rng.randint(20, 120) if not vencido else -rng.randint(200, 300)
        vence = rng.randint(20, 160) if not vencido else -rng.randint(5, 60)
        f.rec(f"{pr['xid']}_pc_pcr", "shrimp.product.certificate.line", {
            "product_id": R(pr["xid"]), "certificate_id": R(UR + cert),
            "number": f"PCR-{pr['batch']}", "issue_date": D(emision), "expiry_date": D(vence),
            "attachment_id": R(UR + adj), "active": True, "status": "approved",
        }, context="{'shrimp_keep_cert_status': True}")

    M["archivos"].append(f)
