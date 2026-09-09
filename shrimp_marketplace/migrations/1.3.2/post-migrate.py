# -*- coding: utf-8 -*-
"""Aplica a los lotes de demo los nombres y campos que ya están en el XML.

Los registros de demo llevan <data noupdate="1">: el xml_id se creó con esa
marca y cambiar el fichero NO los actualiza, ni con -u. Por eso hace falta
esta migración, o quien tome los cambios sigue viendo los nombres viejos.

Qué corrige (el detalle está en demo/demo_06_products.xml):

  1. Los 35 lotes de engorde se llamaban "Camarón Vannamei Entero 40/50" pero
     nunca tenían presentation ni size_grade_id: la talla vivía solo en el
     texto, y era arbitraria —solo 5 de los 35 cuadraban con el avg_size_mg
     del propio lote—. Ahora se deriva del peso.
  2. Los 40 juveniles pesan de 2 a 5 g y 28 tenían una talla comercial
     imposible (cola 26/30 para un animal de 3 g, que darían unas 230 piezas
     por libra). Con eso el sistema les ponía un "mejor precio hoy" que nadie
     va a pagar. Se les quita.
  3. seller_role decía "laboratorio" en los lotes de las camaroneras.

Los valores se leen DEL PROPIO XML del módulo, no se recalculan aquí: así la
base y el fichero no pueden divergir, y una instalación en limpio y una
actualización acaban igual.
"""
import os
import re

from odoo import api, SUPERUSER_ID

# presentation, size_grade_id y seller_role están en _LOCKED_AFTER_PURCHASE:
# el modelo prohíbe cambiarlos en un lote que ya tiene compras, y con razón
# —no se le cambia la talla a algo que alguien ya compró—. Estos lotes son
# datos de demo sembrados por XML sin pasar por esa regla, y lo que se hace es
# rellenar lo que nunca se puso, así que se escriben con SQL a propósito.
CAMPOS_BLOQUEADOS = ("presentation", "size_grade_id", "seller_role")


def _campo(cuerpo, nombre):
    m = re.search(r'<field name="%s"[^>]*>([^<]*)</field>' % nombre, cuerpo)
    if m:
        return m.group(1)
    m = re.search(r'<field name="%s"[^>]*ref="([^"]+)"' % nombre, cuerpo)
    return m.group(1) if m else None


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    ruta = os.path.join(os.path.dirname(__file__), "..", "..",
                        "demo", "demo_06_products.xml")
    ruta = os.path.normpath(ruta)
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as f:
        xml = f.read()

    tallas = {d.name: d.res_id for d in env["ir.model.data"].sudo().search(
        [("model", "=", "shrimp.size.grade")])}
    D = env["ir.model.data"].sudo()
    Prod = env["shrimp.product"].sudo()

    tocados = 0
    for xid, cuerpo in re.findall(
            r'<record id="([^"]+)" model="shrimp\.product">(.*?)</record>',
            xml, re.S):
        dato = D.search([("module", "=", "shrimp_marketplace"),
                         ("model", "=", "shrimp.product"),
                         ("name", "=", xid)], limit=1)
        if not dato:
            continue
        prod = Prod.browse(dato.res_id).exists()
        if not prod:
            continue

        vals = {}
        nombre = _campo(cuerpo, "name")
        if nombre and prod.name != nombre:
            vals["name"] = nombre
        rol = _campo(cuerpo, "seller_role")
        if rol and prod.seller_role != rol:
            vals["seller_role"] = rol
        pres = _campo(cuerpo, "presentation")
        if pres and prod.presentation != pres:
            vals["presentation"] = pres
        ref = _campo(cuerpo, "size_grade_id")
        if ref and tallas.get(ref) and prod.size_grade_id.id != tallas[ref]:
            vals["size_grade_id"] = tallas[ref]
        if not vals:
            continue

        if "name" in vals:
            prod.write({"name": vals.pop("name")})
        if vals:
            columnas = ", ".join("%s = %%s" % c for c in vals)
            cr.execute("UPDATE shrimp_product SET %s WHERE id = %%s" % columnas,
                       list(vals.values()) + [prod.id])
        tocados += 1

    # Los juveniles no llevan talla comercial: un animal de 2 a 5 g no es
    # mercadería de empacadora, se vende a otra finca para seguir engordando.
    # El XML ya no se la pone; a las bases existentes hay que quitársela.
    cr.execute("""
        UPDATE shrimp_product p
           SET presentation = NULL, size_grade_id = NULL
          FROM shrimp_stage s
         WHERE s.id = p.stage_id
           AND s.code = 'JUVENIL'
           AND (p.presentation IS NOT NULL OR p.size_grade_id IS NOT NULL)
    """)
    juveniles = cr.rowcount

    Prod.invalidate_model()
    print("shrimp_marketplace 1.3.2: %s lotes de demo realineados, "
          "%s juveniles sin talla comercial" % (tocados, juveniles))
