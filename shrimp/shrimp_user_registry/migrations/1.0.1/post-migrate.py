# -*- coding: utf-8 -*-
"""Garantiza que el catálogo de certificados del semillero exista y esté activo.

Contexto: el desplegable de "Añadir certificado" del portal filtra por
`role in (tipo_usuario, 'all')` (shrimp_user_registry/controllers/main.py:225
y :329). Hasta ahora `shrimp_certificate` no tenía NI UN registro con
role='semillero', así que a un laboratorio de larvas no se le ofrecía nada
suyo que acreditar y acababa colgándose papeles de camaronera.

Sobre el `noupdate="1"` de data/shrimp_certificate_data.xml: se comprobó en
odoo/tools/convert.py (_tag_record) que `noupdate` solo impide ACTUALIZAR un
xml_id que ya existe; un xml_id nuevo se crea igual en un `-u` porque
`forcecreate` vale True por defecto. Es decir, los 7 certificados nuevos
aparecen solos al actualizar el módulo.

Entonces, ¿para qué esta migración? Para lo que el XML sí tiene prohibido
hacer en una base ya creada: volver a poner `active = True`. Si alguien
archiva uno de estos certificados desde la interfaz —o una demo lo archivó—,
el fichero de datos no lo puede recuperar nunca más, y el semillero se queda
otra vez sin nada que elegir sin que salte ningún error. Aquí se detecta y se
corrige, y se avisa por consola de lo que se tocó.

Es idempotente: en una base sana no escribe nada.
"""
import os
import re

from odoo import api, SUPERUSER_ID

MODULO = "shrimp_user_registry"
FICHERO = ("data", "shrimp_certificate_data.xml")

# Enteros y booleanos del modelo: el XML los trae siempre como texto, y
# escribir "1" en un Integer o en un Boolean de Odoo no es equivalente a 1.
CAMPOS_ENTEROS = ("sequence", "duration_value")
CAMPOS_BOOLEANOS = ("expires_required", "active")
CAMPOS_TEXTO = ("name", "issuer", "role", "certificate_type", "code",
                "description", "duration_period")


def _campos_del_registro(cuerpo):
    """Extrae los <field name="x">valor</field> de un bloque <record>."""
    vals = {}
    for nombre, valor in re.findall(
            r'<field\s+name="([^"]+)"\s*>(.*?)</field>', cuerpo, re.S):
        vals[nombre] = valor.strip()
    # La variante <field name="x" eval="True"/> también se usa en el proyecto.
    for nombre, valor in re.findall(
            r'<field\s+name="([^"]+)"\s+eval="([^"]+)"\s*/>', cuerpo):
        vals[nombre] = valor.strip()
    return vals


def _normalizar(vals):
    limpio = {}
    for campo in CAMPOS_TEXTO:
        if vals.get(campo):
            limpio[campo] = vals[campo]
    for campo in CAMPOS_ENTEROS:
        if campo in vals:
            try:
                limpio[campo] = int(vals[campo])
            except ValueError:
                pass
    for campo in CAMPOS_BOOLEANOS:
        if campo in vals:
            limpio[campo] = vals[campo].strip().lower() in ("1", "true")
    return limpio


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    ruta = os.path.join(os.path.dirname(__file__), "..", "..", *FICHERO)
    ruta = os.path.normpath(ruta)
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as f:
        xml = f.read()

    Cert = env["shrimp.certificate"].sudo().with_context(active_test=False)
    Datos = env["ir.model.data"].sudo()

    creados, reactivados = [], []
    for xid, cuerpo in re.findall(
            r'<record\s+id="([^"]+)"\s+model="shrimp\.certificate">(.*?)</record>',
            xml, re.S):
        vals = _normalizar(_campos_del_registro(cuerpo))
        if vals.get("role") != "semillero":
            continue

        dato = Datos.search([("module", "=", MODULO),
                             ("model", "=", "shrimp.certificate"),
                             ("name", "=", xid)], limit=1)
        cert = Cert.browse(dato.res_id).exists() if dato else Cert.browse()

        if not cert:
            # El nombre es único en el modelo: si el certificado ya está en la
            # base pero sin xml_id (creado a mano), se adopta en vez de chocar
            # contra la restricción de unicidad.
            cert = Cert.search([("name", "=", vals.get("name"))], limit=1)
            if not cert:
                cert = Cert.create(vals)
                creados.append(vals.get("name"))
            if not dato:
                Datos.create({
                    "module": MODULO,
                    "model": "shrimp.certificate",
                    "name": xid,
                    "res_id": cert.id,
                    "noupdate": True,
                })
        elif not cert.active:
            cert.write({"active": True})
            reactivados.append(cert.name)

    print("shrimp_user_registry 1.0.1: certificados de semillero -> "
          "%s creados %s, %s reactivados %s"
          % (len(creados), creados, len(reactivados), reactivados))
