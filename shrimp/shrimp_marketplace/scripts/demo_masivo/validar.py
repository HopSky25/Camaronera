# -*- coding: utf-8 -*-
"""Validación estática de los datos (data + demo) de los cinco módulos shrimp_*.

    python3 /opt/odoo/custom_addons/shrimp/shrimp_marketplace/scripts/demo_masivo/validar.py

Comprueba, siguiendo el orden de carga real (módulo por módulo según sus
dependencias; dentro de cada módulo, 'data' y luego 'demo' en el orden del
manifiesto):
  1. Que cada XML se puede parsear.
  2. Que ningún xml id se crea dos veces (una segunda aparición solo vale como
     actualización de un registro ya existente: se informa aparte).
  3. Que todo ref="..." y todo ref('...') dentro de eval apunta a un id ya
     definido antes (del propio módulo o de una dependencia). Los ids de
     módulos estándar (base., account., ...) se dan por buenos.
  4. Cuenta registros por modelo en los archivos demo (todos, antiguos y nuevos).
"""
import ast
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ADDONS = Path(__file__).resolve().parents[3]
ORDEN = ["shrimp_user_registry", "shrimp_marketplace", "shrimp_verification",
         "shrimp_packer", "shrimp_copacking"]
PROPIOS = set(ORDEN)
RE_REF = re.compile(r"ref\(\s*['\"]([\w.]+)['\"]\s*\)")


def manifiesto(mod):
    return ast.literal_eval((ADDONS / mod / "__manifest__.py").read_text(encoding="utf-8"))


def main():
    definidos = {}           # id calificado -> (modulo, archivo)
    errores, avisos = [], []
    conteo_demo = {}
    actualizaciones = 0
    for mod in ORDEN:
        man = manifiesto(mod)
        for d in man.get("depends", []):
            if d.startswith("shrimp_") and ORDEN.index(d) > ORDEN.index(mod):
                errores.append(f"{mod} depende de {d}, que se carga después")
        visibles = {mod} | {d for d in man.get("depends", []) if d in PROPIOS}
        # dependencias transitivas
        cambio = True
        while cambio:
            cambio = False
            for d in list(visibles):
                for dd in manifiesto(d).get("depends", []):
                    if dd in PROPIOS and dd not in visibles:
                        visibles.add(dd)
                        cambio = True
        for tipo in ("data", "demo"):
            for rel in man.get(tipo, []):
                if not rel.endswith(".xml"):
                    continue
                ruta = ADDONS / mod / rel
                try:
                    raiz = ET.parse(ruta).getroot()
                except ET.ParseError as e:
                    errores.append(f"{mod}/{rel}: XML inválido: {e}")
                    continue
                for nodo in raiz.iter():
                    if nodo.tag in ("record", "template", "menuitem") and nodo.get("id"):
                        xid = nodo.get("id")
                        q = xid if "." in xid else f"{mod}.{xid}"
                        if q in definidos:
                            actualizaciones += 1
                        else:
                            if nodo.tag == "record" and "." in xid and xid.split(".")[0] in PROPIOS \
                                    and xid.split(".")[0] != mod:
                                errores.append(f"{mod}/{rel}: actualiza {xid}, que no existe todavía")
                            definidos[q] = (mod, rel)
                            if tipo == "demo" and nodo.tag == "record":
                                m = nodo.get("model")
                                conteo_demo[m] = conteo_demo.get(m, 0) + 1
                    refs = []
                    if nodo.get("ref"):
                        refs.append(nodo.get("ref"))
                    for atr in ("eval", "context", "groups"):
                        if nodo.get(atr):
                            refs += RE_REF.findall(nodo.get(atr))
                    if nodo.tag == "delete" and nodo.get("id"):
                        refs.append(nodo.get("id"))
                    for r in refs:
                        q = r if "." in r else f"{mod}.{r}"
                        modulo_ref = q.split(".")[0]
                        if modulo_ref not in PROPIOS or q.split(".", 1)[1].startswith(("model_", "field_")):
                            continue  # externos o generados por el ORM (ir.model)
                        if modulo_ref not in visibles:
                            errores.append(f"{mod}/{rel}: referencia a {q} de un módulo que no es dependencia")
                        elif q not in definidos:
                            errores.append(f"{mod}/{rel}: referencia {q} no definida antes")
    print(f"ids definidos: {len(definidos)}   actualizaciones de registros ya existentes: {actualizaciones}")
    print("\nRegistros por modelo en archivos DEMO (demo original + masiva):")
    for m, c in sorted(conteo_demo.items()):
        print(f"  {m:40s} {c:6d}")
    for a in avisos:
        print("AVISO:", a)
    if errores:
        print(f"\n{len(errores)} ERRORES:")
        for e in errores[:200]:
            print("  ", e)
        sys.exit(1)
    print("\nOK: todas las referencias resuelven en orden de carga y no hay ids duplicados.")


if __name__ == "__main__":
    main()
