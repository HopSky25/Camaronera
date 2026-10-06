# -*- coding: utf-8 -*-
"""Genera la demo masiva de los cinco módulos shrimp_*.

Uso (desde cualquier carpeta):

    python3 /opt/odoo/custom_addons/shrimp/shrimp_marketplace/scripts/demo_masivo/generar.py

Escribe los XML en la carpeta demo/ de cada módulo y muestra cuántos registros
salen por modelo. Es determinista: misma semilla, mismo XML.

Los módulos se generan en el orden de dependencias porque cada uno se apoya en
lo que generó el anterior (socios, piscinas, productos...). Ningún XML
referencia registros de un módulo que no sea él mismo o una dependencia.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "demo_masivo"  # noqa: A001

from demo_masivo import (calidad, copacking, declaradas, empacadora,  # noqa: E402
                         marketplace, mismo_nivel, multiperfil, registro, verificacion)


def main():
    M = {"archivos": [], "partners": {}, "usuarios": []}
    registro.construir(M)
    marketplace.construir(M)
    verificacion.construir(M)
    empacadora.construir(M)
    copacking.construir(M)
    # El último y sin azar: no cambia nada de lo anterior.
    multiperfil.construir(M)
    # Fichas de calidad de larva/nauplio: con su propio generador aleatorio,
    # tampoco cambia nada de lo anterior.
    calidad.construir(M)
    # Compras al mismo nivel (camaronera -> camaronera...): semilla propia,
    # tampoco cambia nada de lo anterior.
    mismo_nivel.construir(M)
    # Verificaciones declaradas por las partes: semilla propia, tampoco
    # cambia nada de lo anterior.
    declaradas.construir(M)

    total = {}
    for f in M["archivos"]:
        ruta = f.escribir()
        print(f"{ruta.relative_to(ruta.parents[2])}: "
              + ", ".join(f"{m}={c}" for m, c in sorted(f.conteo.items())))
        for m, c in f.conteo.items():
            total[m] = total.get(m, 0) + c
    print("\nTOTAL por modelo (registros nuevos de la demo masiva):")
    for m, c in sorted(total.items()):
        print(f"  {m:40s} {c:6d}")
    print("\nCuentas de acceso (contraseña: demo123):")
    for login, ux, px, rol, mod in M["usuarios"]:
        print(f"  {rol:12s} {login:45s} {mod}.{ux}")
    print("\nCuentas con varios perfiles:")
    for nombre, perfiles, usuario in M.get("multiperfil", []):
        print(f"  {perfiles:40s} {nombre}" + (f"  (usuario {usuario})" if usuario else ""))


if __name__ == "__main__":
    main()
