"""Puerta general del RPC para los usuarios del portal (defensa en profundidad).

En Odoo 19 /web/dataset/call_kw y /web/dataset/call_button son auth="user":
un usuario del portal llama cualquier método público de cualquier modelo
sobre los ids que quiera, sin ACL ni reglas de registro. Cada método
público de los módulos shrimp_* se protege solo (ver shrimp_user_registry/
authz.py); esta puerta es la segunda barrera para lo que se escape.

Regla, solo para usuarios «share» (portal y público): se niega
- cualquier método de un modelo «shrimp.*», y
- cualquier método «action_*» o «shrimp_*» de cualquier modelo,
salvo los pares (modelo, método) de RPC_ALLOWLIST.

La plataforma no usa el ORM desde el navegador para los usuarios del
portal (las pantallas del portal son formularios y rutas JSON propias), así
que la lista solo lleva lo que el núcleo de Odoo llama desde el portal
(/my/security: doble factor y llaves de acceso). Si una pantalla nueva
necesita un RPC del ORM, se agrega aquí su par (modelo, método).

Dónde se aplica:
- ir.http._dispatch (models/ir_http.py) para /web/dataset/call_kw*,
  /web/dataset/call_button* y /json/2/<modelo>/<método>;
- get_public_method de odoo.service.model (lo usan call_kw, /jsonrpc,
  /xmlrpc y /json/2): cubre también el RPC externo con usuario y
  contraseña, que no pasa por las rutas de /web.
"""

import logging
import sys

from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

# (modelo, método) que el portal del núcleo de Odoo llama por RPC y que la
# regla tocaría (empiezan por action_): seguridad de la cuenta en /my/security.
RPC_ALLOWLIST = frozenset({
    # auth_totp_portal: activar / desactivar el doble factor y cerrar las
    # sesiones de los dispositivos de confianza.
    ("res.users", "action_totp_enable_wizard"),
    ("res.users", "action_totp_disable"),
    ("res.users", "action_revoke_all_devices"),
    # auth_passkey_portal: crear y borrar llaves de acceso.
    ("res.users", "action_create_passkey"),
    ("auth.passkey.key", "action_delete_passkey"),
})

_DENIED_PREFIXES = ("action_", "shrimp_")
# Marca: la base tiene instalado shrimp_user_registry (el parche de
# get_public_method es global al proceso y sirve a todas las bases).
_MARKER_MODEL = "shrimp.account.member"


def shrimp_rpc_denied(env, model_name, method):
    """True si un usuario share no puede llamar `model_name.method` por RPC."""
    if not model_name or not method or env.su:
        return False
    if _MARKER_MODEL not in env.registry:
        return False
    user = env.user
    if not user or not user.share:
        return False
    if (model_name, method) in RPC_ALLOWLIST:
        return False
    return model_name.startswith("shrimp.") or method.startswith(_DENIED_PREFIXES)


def shrimp_rpc_check(env, model_name, method, origin="rpc"):
    if shrimp_rpc_denied(env, model_name, method):
        _logger.warning("shrimp: RPC %s.%s denegado al usuario de portal %s (uid %s, %s)",
                        model_name, method, env.user.login, env.uid, origin)
        raise AccessError(env._("No puedes llamar a %(m)s.%(f)s desde el portal.")
                          % {"m": model_name, "f": method})


def _install_get_public_method_patch():
    """Envuelve odoo.service.model.get_public_method (una sola vez)."""
    from odoo.service import model as service_model
    original = service_model.get_public_method
    if getattr(original, "_shrimp_rpc_gate", False):
        return

    def get_public_method(model, name):
        method = original(model, name)
        shrimp_rpc_check(model.env, model._name, name, origin="get_public_method")
        return method

    get_public_method._shrimp_rpc_gate = True
    get_public_method.__wrapped__ = original
    service_model.get_public_method = get_public_method
    # /json/2 importó la función por nombre.
    json2 = sys.modules.get("odoo.addons.rpc.controllers.json2")
    if json2 is not None and getattr(json2, "get_public_method", None) is original:
        json2.get_public_method = get_public_method


_install_get_public_method_patch()
