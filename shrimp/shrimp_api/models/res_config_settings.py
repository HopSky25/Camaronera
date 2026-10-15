"""Ajustes › CamaronMarket › App móvil (docs/app/CONTRATO-MEJORAS-APP.md).

Versión mínima y última de la app, enlace de descarga, mensaje de
actualización (C1) y huellas del certificado de firma de Android para los
enlaces directos (C6). Todo son parámetros de sistema: vacíos por defecto,
y vacío = sin obligación / sin huellas.
"""

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..controllers.framework import parse_version

PARAM_MIN_VERSION = "shrimp_api.app_min_version"
PARAM_LATEST_VERSION = "shrimp_api.app_latest_version"
PARAM_DOWNLOAD_URL = "shrimp_api.app_download_url"
PARAM_UPDATE_MESSAGE = "shrimp_api.app_update_message"
PARAM_ANDROID_CERTS = "shrimp_api.android_cert_sha256"
PARAM_ANDROID_PACKAGE = "shrimp_api.android_package_name"
DEFAULT_ANDROID_PACKAGE = "com.camaronmarket.verificador_campo"

# AA:BB:… (32 pares hexadecimales), la forma en que la da keytool / Play Console.
_CERT_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){31}$")


def _param(env, key):
    return (env["ir.config_parameter"].sudo().get_param(key) or "").strip()


def app_version_info(env):
    """Lo que devuelve GET /public/app-version y lo que usa el 426.
    Cada valor vacío sale como None."""
    return {
        "min_version": _param(env, PARAM_MIN_VERSION) or None,
        "latest_version": _param(env, PARAM_LATEST_VERSION) or None,
        "download_url": _param(env, PARAM_DOWNLOAD_URL) or None,
        "message": _param(env, PARAM_UPDATE_MESSAGE) or None,
    }


def android_cert_fingerprints(env):
    """Huellas SHA-256 del parámetro (separadas por coma, espacios o saltos
    de línea), normalizadas a «AA:BB:…» en mayúsculas. Las mal formadas se
    descartan: una huella inválida en assetlinks.json no verifica nada."""
    raw = _param(env, PARAM_ANDROID_CERTS)
    out = []
    for item in re.split(r"[,\s;]+", raw):
        item = item.strip().upper()
        if not item:
            continue
        if ":" not in item and re.fullmatch(r"[0-9A-F]{64}", item):
            item = ":".join(item[i:i + 2] for i in range(0, 64, 2))
        if _CERT_RE.match(item) and item not in out:
            out.append(item)
    return out


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    shrimp_app_min_version = fields.Char(
        string="Versión mínima obligatoria", config_parameter=PARAM_MIN_VERSION,
        help="x.y.z. Con un valor, la app más vieja (o que no diga su versión) recibe 426 "
             "app-update-required y muestra la pantalla «Actualiza la app». Vacío = sin obligación.")
    shrimp_app_latest_version = fields.Char(
        string="Última versión publicada", config_parameter=PARAM_LATEST_VERSION,
        help="x.y.z. Si la app es más vieja que esta (pero no que la mínima) muestra un aviso "
             "discreto que se puede descartar.")
    shrimp_app_download_url = fields.Char(
        string="Enlace de descarga", config_parameter=PARAM_DOWNLOAD_URL,
        help="https://… a la tienda o al APK. Lo abre el botón «Actualizar».")
    shrimp_app_update_message = fields.Char(
        string="Mensaje de actualización", config_parameter=PARAM_UPDATE_MESSAGE,
        help="Texto que muestra la app al pedir que se actualice (opcional).")
    shrimp_android_cert_sha256 = fields.Char(
        string="Huellas SHA-256 del certificado de Android", config_parameter=PARAM_ANDROID_CERTS,
        help="Huellas SHA-256 del certificado con el que se firma la app (AA:BB:…), separadas por "
             "coma. Las publica /.well-known/assetlinks.json para que Android abra los enlaces "
             "directos en la app. Vacío = lista vacía.")

    @api.constrains("shrimp_app_min_version", "shrimp_app_latest_version",
                    "shrimp_app_download_url")
    def _check_shrimp_app_settings(self):
        for rec in self:
            for name in ("shrimp_app_min_version", "shrimp_app_latest_version"):
                value = (rec[name] or "").strip()
                if value and not parse_version(value):
                    raise ValidationError(_("«%s» no es una versión x.y.z.") % value)
            minimo = parse_version((rec.shrimp_app_min_version or "").strip())
            ultima = parse_version((rec.shrimp_app_latest_version or "").strip())
            if minimo and ultima and ultima < minimo:
                raise ValidationError(_("La última versión no puede ser menor que la mínima."))
            url = (rec.shrimp_app_download_url or "").strip()
            if url and not re.match(r"^https?://\S+$", url):
                raise ValidationError(_("El enlace de descarga tiene que empezar por https://."))
