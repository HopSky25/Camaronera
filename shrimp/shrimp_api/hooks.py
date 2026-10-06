"""Instalación: tokens de trazabilidad y migración de las claves heredadas."""

import logging

from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT

_logger = logging.getLogger(__name__)

# Qué scopes nuevos equivalen a cada nivel de la API anterior. "admin"
# desaparece: era actuar en nombre de cualquier vendedor, y eso es una
# integración interna que va por JSON-2 con un usuario interno.
LEGACY_SCOPES = {
    "read": ["catalog:read", "products:read", "facilities:read"],
    "write": ["catalog:read", "products:read", "products:write",
              "facilities:read", "facilities:write"],
    "admin": ["catalog:read", "products:read", "products:write",
              "facilities:read", "facilities:write"],
}


def _fill_trace_tokens(env):
    Tx = env["shrimp.transaction"].sudo().with_context(active_test=False)
    missing = Tx.search([("trace_token", "=", False)])
    for tx in missing:
        tx.trace_token = Tx._new_trace_token()
    if missing:
        _logger.info("shrimp_api: %s transacciones con token de trazabilidad nuevo", len(missing))


def migrate_legacy_keys(env):
    """Convierte las claves en texto plano a hash y vacía la columna.

    Las integraciones existentes siguen funcionando con la misma cadena: el
    prefijo de búsqueda de una clave heredada son sus 8 primeros caracteres.
    Las claves de usuarios INTERNOS se desactivan: con un usuario interno no
    aplican las reglas del portal y la clave vería los datos de todos.
    """
    cr = env.cr
    cr.execute("SELECT id, key, scope, user_id FROM shrimp_api_key WHERE key IS NOT NULL AND key <> ''")
    rows = cr.fetchall()
    Key = env["shrimp.api.key"].sudo().with_context(active_test=False)
    Scope = env["shrimp.api.scope"].sudo()
    seen = set()
    for key_id, raw, scope, user_id in rows:
        rec = Key.browse(key_id)
        prefix = "legacy:" + raw[:8]
        user = env["res.users"].sudo().browse(user_id)
        vals = {
            "key_prefix": prefix,
            "key_hash": KEY_CRYPT_CONTEXT.hash(raw),
            "is_legacy": True,
            "scope_ids": [(6, 0, Scope.search([("code", "in", LEGACY_SCOPES.get(scope, LEGACY_SCOPES["read"]))]).ids)],
        }
        if prefix in seen or Key.search_count([("key_prefix", "=", prefix), ("id", "!=", key_id)]):
            vals.update(key_prefix="legacy-dup:%s" % key_id, active=False)
            _logger.warning("shrimp_api: clave heredada %s desactivada (prefijo repetido)", key_id)
        elif not user.share:
            vals["active"] = False
            _logger.warning("shrimp_api: clave heredada %s de un usuario interno desactivada; "
                            "usar la API JSON-2 de Odoo", key_id)
        seen.add(prefix)
        # write en SQL de la columna de texto plano, después de guardar el hash
        rec.write(vals)
    cr.execute("UPDATE shrimp_api_key SET key = NULL WHERE key IS NOT NULL")
    if rows:
        _logger.info("shrimp_api: %s claves heredadas migradas a hash", len(rows))


def post_init_hook(env):
    _fill_trace_tokens(env)
    migrate_legacy_keys(env)
