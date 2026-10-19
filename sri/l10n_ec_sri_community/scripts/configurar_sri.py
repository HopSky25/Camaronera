# -*- coding: utf-8 -*-
"""Puesta en marcha del SRI con los datos reales de la empresa.

Lo que se puede deducir ya lo hace solo el post_init_hook al instalar: los
códigos del catálogo en cada IVA, la habilitación de las compañías de Ecuador
en ambiente de PRUEBAS y el tipo de identificación de los contactos.

Lo que NO se puede deducir son datos de identidad fiscal, y por eso van aquí y
no en el hook: inventarlos sería peor que dejarlos en blanco, porque acabarían
dentro de un comprobante electrónico.

    RUC, dirección matriz y certificado .p12.

Uso:
    cd /home/ccarballo/odoo19
    # 1) rellenar DATOS abajo con la información real
    # 2) ejecutar:
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d odoo19 --no-http \
        < custom_addons/Camaronera/l10n_ec_sri_community/scripts/configurar_sri.py

Es idempotente: se puede correr las veces que haga falta.

EL CERTIFICADO NO VA AQUÍ. El módulo lo lee de variables de entorno del
servidor, nunca de la base de datos, que es lo correcto: un .p12 guardado en
una tabla se acaba filtrando en un respaldo. Hay que exportar, con el mismo
prefijo que se ponga en `secreto`:

    export EC_SRI_TRAZUL_P12_PATH=/ruta/segura/firma.p12
    export EC_SRI_TRAZUL_P12_PASSWORD='...'
"""

DATOS = {
    "compania": "TRAZUL S.A.S.",
    # RUC de 13 dígitos. Sin esto el módulo se niega a emitir.
    "ruc": "",
    # Dirección de la matriz tal como consta en el RUC.
    "direccion_matriz": "",
    # Prefijo de las variables de entorno del certificado. Debe empezar por
    # EC_SRI_ y llevar solo mayúsculas, números y guion bajo.
    "secreto": "EC_SRI_TRAZUL",
    "nombre_comercial": "Trazul",
    # 'regular', 'rimpe' o 'popular'
    "regimen": "regular",
    "obligado_contabilidad": True,
    # Número de contribuyente especial y resolución de agente de retención,
    # si aplican. En blanco si no.
    "contribuyente_especial": "",
    "agente_retencion": "",
}

# Un punto de emisión por tipo de documento que se vaya a usar.
# (establecimiento, punto, tipo de documento, nombre)
PUNTOS = [
    ("001", "001", "01", "Matriz - Facturas"),
    ("001", "001", "04", "Matriz - Notas de crédito"),
    ("001", "001", "05", "Matriz - Notas de débito"),
    ("001", "001", "07", "Matriz - Retenciones"),
]
# Dirección del establecimiento. Si se deja en blanco se usa la matriz.
DIRECCION_ESTABLECIMIENTO = ""


C = env["res.company"].sudo()                              # noqa: F821
P = env["ec.sri.point"].sudo()                             # noqa: F821

compania = C.search([("name", "=", DATOS["compania"])], limit=1)
if not compania:
    raise SystemExit("No existe la compañía %r." % DATOS["compania"])
if compania.country_id.code != "EC":
    raise SystemExit("La compañía %s no está en Ecuador." % compania.name)

vals = {
    "ec_sri_enabled": True,
    "ec_sri_trade_name": DATOS["nombre_comercial"] or False,
    "ec_sri_regime": DATOS["regimen"],
    "ec_sri_accounting": DATOS["obligado_contabilidad"],
    "ec_sri_special": DATOS["contribuyente_especial"] or False,
    "ec_sri_agent": DATOS["agente_retencion"] or False,
    "ec_sri_secret_name": DATOS["secreto"] or False,
}
if DATOS["ruc"]:
    vals["vat"] = DATOS["ruc"]
if DATOS["direccion_matriz"]:
    vals["ec_sri_address"] = DATOS["direccion_matriz"]
compania.write(vals)

print("Compañía: %s" % compania.name)
print("  RUC:               %s" % (compania.vat or "*** FALTA ***"))
print("  Dirección matriz:  %s" % (compania.ec_sri_address or "*** FALTA ***"))
print("  Ambiente:          %s" % dict(
    compania._fields["ec_sri_environment"].selection).get(compania.ec_sri_environment))
print("  Prefijo secreto:   %s" % (compania.sudo().ec_sri_secret_name or "*** FALTA ***"))

direccion = DIRECCION_ESTABLECIMIENTO or compania.ec_sri_address
tipos = dict(P._fields["document_type"].selection)
print("\nPuntos de emisión (ambiente de pruebas):")
for establecimiento, punto, tipo, nombre in PUNTOS:
    existente = P.search([
        ("company_id", "=", compania.id),
        ("establishment", "=", establecimiento),
        ("emission", "=", punto),
        ("document_type", "=", tipo),
        ("environment", "=", "1"),
    ], limit=1)
    if existente:
        print("  %s-%s %-22s ya existe (secuencial en %s)" % (
            establecimiento, punto, tipos.get(tipo), existente.next_number))
        continue
    if not direccion:
        print("  %s-%s %-22s NO SE CREA: falta la dirección del establecimiento"
              % (establecimiento, punto, tipos.get(tipo)))
        continue
    nuevo = P.create({
        "name": nombre,
        "company_id": compania.id,
        "establishment": establecimiento,
        "emission": punto,
        "address": direccion,
        "document_type": tipo,
        "environment": "1",
    })
    print("  %s-%s %-22s creado" % (establecimiento, punto, tipos.get(tipo)))

env.cr.commit()                                            # noqa: F821

faltan = []
if not compania.vat:
    faltan.append("el RUC de 13 dígitos")
if not compania.ec_sri_address:
    faltan.append("la dirección matriz")
if not P.search_count([("company_id", "=", compania.id)]):
    faltan.append("al menos un punto de emisión")
import os
prefijo = compania.sudo().ec_sri_secret_name or ""
if not (prefijo and os.environ.get(prefijo + "_P12_PATH")):
    faltan.append("el certificado .p12 en las variables de entorno")

if faltan:
    print("\nTodavía no se puede emitir. Falta: %s." % ", ".join(faltan))
    print("Rellena DATOS en este script y vuelve a ejecutarlo.")
else:
    print("\nListo para emitir en ambiente de PRUEBAS.")
    print("Para producción hay que superar la homologación del SRI y marcar")
    print("'Pruebas de homologación completadas' en la compañía.")
