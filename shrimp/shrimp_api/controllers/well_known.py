"""Enlaces directos de la app (docs/app/CONTRATO-MEJORAS-APP.md, C6).

GET /.well-known/assetlinks.json: Android comprueba aquí que la app
``com.camaronmarket.verificador_campo`` firmada con estas huellas puede abrir
los enlaces https del dominio. Las huellas salen del parámetro
``shrimp_api.android_cert_sha256`` (Ajustes › CamaronMarket › App móvil).

Odoo 19 ya sirve esta ruta desde ``auth_passkey`` (auto_install) con la app
oficial de Odoo (com.odoo.mobile, para las passkeys). Se hereda su
controlador —si no, cuál de las dos rutas gana depende del orden de carga— y
se AÑADE la entrada de CamaronMarket delante, sin quitar la de Odoo: así no
se rompe el inicio de sesión con passkey de la app de Odoo. Sin huellas, la
de CamaronMarket no aparece (si auth_passkey no está instalado, eso es `[]`).
"""

from odoo import http
from odoo.http import request

from odoo.addons.auth_passkey.controllers.main import WebauthnController
from odoo.addons.auth_passkey.mobile_utils import _WEB_WELL_KNOW_ANDROID

from ..models.res_config_settings import (DEFAULT_ANDROID_PACKAGE, PARAM_ANDROID_PACKAGE,
                                          android_cert_fingerprints)


def assetlinks(env):
    """Entradas de CamaronMarket (lista vacía si no hay huellas)."""
    certs = android_cert_fingerprints(env)
    if not certs:
        return []
    package = (env["ir.config_parameter"].sudo().get_param(PARAM_ANDROID_PACKAGE) or "").strip() \
        or DEFAULT_ANDROID_PACKAGE
    return [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": package,
                   "sha256_cert_fingerprints": certs},
    }]


class ShrimpApiWellKnown(WebauthnController):

    @http.route()
    def web_well_known_android(self):
        ours = assetlinks(request.env)
        package = ours[0]["target"]["package_name"] if ours else None
        odoo_entries = [e for e in _WEB_WELL_KNOW_ANDROID
                        if e.get("target", {}).get("package_name") != package]
        return request.make_json_response(ours + odoo_entries, headers={
            "Content-Type": "application/json",
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        })
