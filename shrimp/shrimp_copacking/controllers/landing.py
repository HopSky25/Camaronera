"""Portada pública del sitio de empaque (co-packing).

La ruta "/" la sigue resolviendo ShrimpCopackerHome (website_home.py); aquí
solo vive lo que necesita la plantilla, para no mezclarlo con la lógica de
redirección.
"""
from odoo.http import request


def es_maquilador(user):
    """True si quien navega es la cuenta de una planta maquiladora."""
    return bool(user) and not user._is_public() and \
        user.partner_id.sudo().shrimp_user_type == "maquilador"


def _url_en_sitio(sitio, ruta):
    dominio = (sitio.domain or "").strip().rstrip("/") if sitio else ""
    if dominio and "://" not in dominio:
        dominio = "%s://%s" % (request.httprequest.scheme, dominio)
    return (dominio + ruta) if dominio else ruta


def valores_portada():
    """Valores de render de shrimp_copacking.copack_landing."""
    web = request.website
    W = request.env["website"].sudo()
    # El directorio de plantas vive en el marketplace (sitio principal). Si no
    # tiene dominio se deja relativo: en una instalación de un solo dominio la
    # ruta existe igual.
    principal = W._shrimp_main_site() if hasattr(W, "_shrimp_main_site") else W.browse()
    base = web.get_base_url().rstrip("/")
    return {
        "url_buscar_planta": _url_en_sitio(principal, "/marketplace/copacking"),
        "title": "Servicio de empaque para plantas maquiladoras | %s" % web.name,
        "website_meta_description": (
            "Recibe solicitudes de empaque de camaroneras y empacadoras, oferta "
            "con tu tarifa y cierra cada trabajo con un acta firmada por las dos "
            "partes que cuadra las libras de entrada y salida."),
        "shrimp_copack_og": {
            "og:type": "website",
            "og:site_name": web.name,
            "og:title": "Tu planta empaca. Las libras cuadran solas.",
            "og:description": "Solicitudes, ofertas, actas firmadas y liquidaciones "
                              "para plantas de empaque de camarón.",
            "og:url": base + "/",
            "og:image": base + "/shrimp_copacking/static/src/img/icon-app-512.png",
            "og:locale": "es_EC",
        },
    }
