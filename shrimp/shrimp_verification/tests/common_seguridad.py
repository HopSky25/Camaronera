import base64
from datetime import date, timedelta

from odoo.addons.shrimp_marketplace.tests.common import (
    CLAVE, PDF_MIN, crear_socios, producto)


def montar_verificacion(env, sufijo="v"):
    """Cadena laboratorio→camaronera con verificación en campo.

    Devuelve (socios, verificación). Vendedor: s["lab"]; comprador: s["cam"].
    Se usa larva (y no camarón adulto) para no depender de shrimp_packer: el
    alcance "larvae" se dictamina con cuatro datos y no exige tallas ni
    metabisulfito.
    """
    s = crear_socios(env, sufijo)
    Socio = env["res.partner"]
    portal = env.ref("base.group_portal")

    def verificadora(nombre, ruc):
        emp = Socio.create({
            "name": nombre, "is_company": True, "shrimp_user_type": "verificador",
            "vat_or_id": ruc, "email": "%s@prueba.test" % ruc,
            "ver_bank_name": "Banco", "ver_bank_account_number": "2100548837",
            "ver_bank_holder_id": "0912345678"})
        att = env["ir.attachment"].create({
            "name": "acred.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        env["shrimp.user.certificate.line"].create({
            "partner_id": emp.id,
            "certificate_id": env.ref("shrimp_verification.cert_acreditacion_verificador").id,
            "file_attachment_id": att.id, "status": "approved", "certificate_number": ruc,
            "issue_date": date.today() - timedelta(days=30),
            "expiry_date": date.today() + timedelta(days=365)})
        return emp

    s["verif"] = verificadora("Verificadora %s" % sufijo, "09777%s001" % sufijo.rjust(5, "0")[-5:])
    s["verif2"] = verificadora("Otra verificadora %s" % sufijo, "09778%s001" % sufijo.rjust(5, "0")[-5:])
    s["tec"] = Socio.create({"name": "Técnico %s" % sufijo, "parent_id": s["verif"].id,
                             "shrimp_is_field_tech": True, "email": "tec.%s@prueba.test" % sufijo})
    s["tec_baja"] = Socio.create({"name": "Técnico de baja %s" % sufijo, "parent_id": s["verif"].id,
                                  "shrimp_is_field_tech": True, "active": False,
                                  "email": "tecb.%s@prueba.test" % sufijo})
    s["tec_ajeno"] = Socio.create({"name": "Técnico ajeno %s" % sufijo, "parent_id": s["verif2"].id,
                                   "shrimp_is_field_tech": True, "email": "teca.%s@prueba.test" % sufijo})
    for clave in ("verif", "tec"):
        s["u_" + clave] = env["res.users"].create({
            "name": s[clave].name, "login": "%s.%s" % (clave, sufijo), "partner_id": s[clave].id,
            "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})
    lote = producto(env, s["lab"], nombre="Larva verificada %s" % sufijo)
    res = lote.start_verified_purchase(s["cam"], 10.0, s["verif"], fee=50.0)
    return s, res["verification"]


def abrir_ronda(verificacion, cumple=True):
    """Deja la verificación aprobada y la ronda de aceptación abierta, sin
    pasar por la facturación (que exige plan contable)."""
    verificacion.write({
        "larvae_qty_verified": 10.0, "larvae_survival_rate": 90.0,
        "larvae_avg_size_mg": 5.0,
        "larvae_health_status": "good" if cumple else "rejected",
        "state": "approved",
    })
    verificacion._abrir_ronda_aceptacion()
    return verificacion
