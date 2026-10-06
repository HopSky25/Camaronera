"""Montaje común de los tests de seguridad del marketplace.

Se crea todo aquí (sin depender de los datos de demo) para que la batería
corra en una base limpia. Lo reutilizan los tests de shrimp_verification y
shrimp_packer.
"""
import base64
import re
from datetime import date, timedelta

from odoo.tests.common import TransactionCase

# PNG de 1x1 válido (magic bytes reales).
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
PDF_MIN = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

CLAVE = "Clave-de-prueba-123"


def crear_socios(env, sufijo=""):
    """Partners y usuarios de portal de toda la cadena. Devuelve un dict."""
    Socio = env["res.partner"]
    portal = env.ref("base.group_portal")
    s = {}

    def u(socio, login):
        return env["res.users"].create({
            "name": socio.name, "login": login + sufijo, "partner_id": socio.id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])],
        })

    s["sem"] = Socio.create({
        "name": "Semillero sec%s" % sufijo, "is_company": True,
        "email": "sem.sec%s@prueba.test" % sufijo, "vat_or_id": "0911100001%s" % (sufijo or "001"),
        "shrimp_user_type": "semillero"})
    s["lab"] = Socio.create({
        "name": "Laboratorio sec%s" % sufijo, "is_company": True,
        "email": "lab.sec%s@prueba.test" % sufijo, "vat_or_id": "0911100002%s" % (sufijo or "001"),
        "shrimp_user_type": "laboratorio",
        "lab_razon_social": "Lab S.A.", "lab_ubicacion": "Durán"})
    s["lab2"] = Socio.create({
        "name": "Laboratorio ajeno sec%s" % sufijo, "is_company": True,
        "email": "lab2.sec%s@prueba.test" % sufijo, "vat_or_id": "0911100003%s" % (sufijo or "001"),
        "shrimp_user_type": "laboratorio",
        "lab_razon_social": "Lab2 S.A.", "lab_ubicacion": "Durán"})
    s["cam"] = Socio.create({
        "name": "Camaronera sec%s" % sufijo, "is_company": True,
        "email": "cam.sec%s@prueba.test" % sufijo, "vat_or_id": "0911100004%s" % (sufijo or "001"),
        "shrimp_user_type": "camaronera",
        "farm_razon_social": "Cam S.A.", "farm_representante": "Rep",
        "farm_telefono": "04-1234567", "farm_ubicacion": "Guayas"})
    for clave in ("sem", "lab", "lab2", "cam"):
        s["u_" + clave] = u(s[clave], clave + ".sec")
    return s


def producto(env, vendedor, nombre="Lote sec", etapa="shrimp_marketplace.shrimp_stage_pl12",
             publicado=True, **extra):
    vals = {
        "name": nombre,
        "seller_partner_id": vendedor.id,
        "seller_role": vendedor.shrimp_user_type,
        "stage_id": env.ref(etapa).id,
        "initial_qty": 100.0,
        "price": 10.0,
        "uom_id": env.ref("shrimp_marketplace.uom_millar").id,
        "expected_delivery_date": date.today() + timedelta(days=10),
        "state": "published" if publicado else "draft",
        "published_date": False,
    }
    vals.update(extra)
    return env["shrimp.product"].create(vals)


def csrf_de(texto):
    m = re.search(r'csrf_token["\']?\s*[:=]\s*["\']([0-9a-fA-Fo]+)["\']', texto) or \
        re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


class ShrimpSecurityCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env)
        cls.larva = producto(cls.env, cls.s["sem"])
