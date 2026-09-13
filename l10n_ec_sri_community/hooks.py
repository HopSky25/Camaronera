# -*- coding: utf-8 -*-
"""Autoconfiguración al instalar.

El módulo se puede instalar y quedar mudo: los comprobantes se arman leyendo
los códigos del catálogo del SRI que hay que anotar en cada impuesto, y si esos
campos están vacíos el XML sale sin la sección de impuestos y el SRI lo
rechaza. Anotar a mano cada IVA de cada compañía es trabajo mecánico y fácil de
equivocar, así que se hace aquí.

Es deliberadamente conservador:

  - Solo toca impuestos de tipo porcentaje cuya tarifa coincide con una del
    catálogo del SRI. Un impuesto raro se deja en blanco para que alguien lo
    mire, que es mejor que asignarle un código inventado.
  - Solo rellena campos VACÍOS. Nunca pisa una configuración existente: quien
    ya ajustó su instalación no la pierde al actualizar.
  - Es idempotente: correrlo otra vez no cambia nada.

No inventa datos de identidad fiscal. El RUC, la dirección matriz y el
certificado .p12 son datos reales de la empresa y quedan a cargo del usuario;
sin ellos el módulo instala y se configura, pero se niega a emitir (lo valida
_ec_sri_company_data).
"""

import logging
import re

_logger = logging.getLogger(__name__)

# Catálogo del SRI, tabla 17 (codigoPorcentaje del IVA). La clave es la tarifa
# tal como la tiene Odoo en account.tax.amount.
#
# El 15 % es la tarifa vigente en Ecuador desde abril de 2024; se dejan las
# anteriores porque una base con histórico sigue teniendo facturas con 12 % y
# 14 %, y esas también hay que poder reconstruirlas.
IVA_POR_TARIFA = {
    0.0: ("0", "zero"),      # IVA 0 %
    5.0: ("8", "taxable"),   # IVA diferenciado 5 %
    12.0: ("2", "taxable"),  # histórico
    13.0: ("10", "taxable"),  # histórico
    14.0: ("3", "taxable"),  # histórico
    15.0: ("4", "taxable"),  # vigente
}

CODIGO_IVA = "2"  # tabla 16: 2 = IVA


def _companias_ec(env):
    """Las compañías de Ecuador.

    Se filtra en Python y no con un dominio porque en Odoo 19
    res.company.country_id no está almacenado —es un related al partner— y la
    búsqueda revienta con "Cannot convert to SQL because it is not stored".
    Las compañías son pocas, así que no hay coste.
    """
    return env["res.company"].sudo().search([]).filtered(
        lambda c: c.country_id.code == "EC")


def _configurar_impuestos(env):
    """Anota el código SRI en los IVA que se puedan identificar sin ambigüedad."""
    companias = _companias_ec(env)
    if not companias:
        return 0
    impuestos = env["account.tax"].sudo().search([
        ("company_id", "in", companias.ids),
        ("amount_type", "=", "percent"),
    ])
    tocados = 0
    for impuesto in impuestos:
        datos = IVA_POR_TARIFA.get(round(impuesto.amount, 2))
        if not datos:
            continue
        porcentaje, bolsa = datos
        vals = {}
        if not impuesto.ec_sri_tax_code:
            vals["ec_sri_tax_code"] = CODIGO_IVA
        if not impuesto.ec_sri_percentage_code:
            vals["ec_sri_percentage_code"] = porcentaje
        if not impuesto.ec_sri_ats_bucket:
            vals["ec_sri_ats_bucket"] = bolsa
        if vals:
            impuesto.write(vals)
            tocados += 1
    return tocados


def _habilitar_companias(env):
    """Deja listas las compañías de Ecuador, siempre en ambiente de PRUEBAS.

    Se habilita el módulo pero no la producción: ec_sri_environment queda en
    '1' (Pruebas) y ec_sri_production_ready en falso, así que ningún
    comprobante puede salir con validez tributaria hasta que alguien lo decida
    a conciencia y haya pasado la homologación.
    """
    companias = _companias_ec(env)
    tocadas = 0
    for compania in companias:
        if compania.ec_sri_enabled:
            continue
        compania.write({
            "ec_sri_enabled": True,
            "ec_sri_environment": "1",
            "ec_sri_production_ready": False,
        })
        tocadas += 1
    return tocadas


def _identificar_contactos(env):
    """Deduce el tipo de identificación del SRI a partir del número.

    En Ecuador la longitud lo dice sin ambigüedad: 13 dígitos es RUC y 10 es
    cédula. Sin este campo, facturar a un contacto se cae con "Configure
    Identificación SRI", y son datos que ya están en la ficha.

    Un pasaporte o una identificación del exterior no se pueden deducir del
    número, así que esos se dejan vacíos a propósito: es preferible que
    alguien los complete a etiquetarlos mal, porque el tipo viaja en el XML.
    """
    Partner = env["res.partner"].sudo()
    tocados = 0
    for partner in Partner.search([("ec_sri_identification_type", "=", False),
                                   ("vat", "!=", False)]):
        numero = (partner.vat or "").strip()
        if re.fullmatch(r"[0-9]{13}", numero):
            partner.ec_sri_identification_type = "04"   # RUC
        elif re.fullmatch(r"[0-9]{10}", numero):
            partner.ec_sri_identification_type = "05"   # Cédula
        else:
            continue
        tocados += 1
    return tocados


def post_init_hook(env):
    impuestos = _configurar_impuestos(env)
    companias = _habilitar_companias(env)
    contactos = _identificar_contactos(env)
    _logger.info(
        "SRI Community: %s impuesto(s) con código del catálogo, "
        "%s compañía(s) de Ecuador habilitadas en ambiente de PRUEBAS, "
        "%s contacto(s) con tipo de identificación. Falta el RUC, la "
        "dirección matriz, el punto de emisión y el certificado para emitir.",
        impuestos, companias, contactos)
