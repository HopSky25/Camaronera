"""Autorización de los métodos públicos de los modelos shrimp_*.

Por qué existe: en Odoo 19 /web/dataset/call_kw y /web/dataset/call_button
son auth="user" (también para usuarios de portal) y llaman a cualquier método
PÚBLICO del modelo (sin «_» delante ni @api.private) sobre los ids que
mande el cliente, sin comprobar ACL ni reglas de registro. Un método que
trabaja en sudo() y deja la autorización al controlador del portal queda
abierto a cualquier usuario del portal que lo llame por RPC.

Regla: cada método público que cambia algo en sudo() se protege SOLO, sin
contar con el controlador ni con la puerta general de ir.http
(models/shrimp_rpc_gate.py, que es defensa en profundidad):

- lo que hace la plataforma (back-office) exige `shrimp_require_internal` o
  `shrimp_require_platform_admin`;
- lo que hace una de las partes comprueba que quien llama ES esa parte, con
  el socio en cuyo nombre actúa (`shrimp_actor`), nunca con un `actor` que
  llegue como argumento si quien llama no es de confianza.

«De confianza» = el entorno en sudo (el controlador, la API REST o el cron
ya autorizaron y trabajan en sudo) o un usuario interno. Los controladores
del portal y la API llaman sobre registros en sudo(): para ellos no cambia
nada.
"""

from odoo.exceptions import AccessError


def shrimp_is_trusted(env):
    """True si quien llama ya está autorizado: sudo o usuario interno."""
    if env.su:
        return True
    user = env.user
    return bool(user) and not user.share


def shrimp_require_internal(env, message=None):
    """Solo la plataforma (usuarios internos) o código en sudo."""
    if not shrimp_is_trusted(env):
        raise AccessError(message or env._("Esta acción la hace solo la plataforma."))
    return True


def shrimp_require_platform_admin(env, message=None):
    """Solo los administradores de la plataforma (base.group_system) o
    código en sudo: lo mismo que piden los asistentes del back-office."""
    if not (env.su or env.user.has_group("base.group_system")):
        raise AccessError(message or env._("Esta acción la hace solo un administrador de la "
                                       "plataforma."))
    return True


def shrimp_actor(env, actor=None):
    """Socio en cuyo nombre actúa quien llama.

    El `actor` que pasa el controlador o la API (current_partner(),
    ctx.partner) solo se respeta si quien llama es de confianza; desde un
    RPC del portal se usa siempre el del propio usuario (el mismo que
    devolvería current_partner()). Devuelve un registro de res.partner
    (vacío para el usuario público)."""
    if actor and shrimp_is_trusted(env):
        return actor
    user = env.user
    if not user or user._is_public():
        return env["res.partner"]
    return user.sudo()._shrimp_actor_partner().with_env(env)


def shrimp_strip_context(records, prefixes):
    """Quita del contexto las banderas internas (claves que empiezan por
    `prefixes`) que un RPC del portal podría mandar para saltarse
    comprobaciones. Si quien llama es de confianza, devuelve los registros
    tal cual."""
    if shrimp_is_trusted(records.env):
        return records
    prefixes = tuple(prefixes)
    ctx = {k: v for k, v in records.env.context.items() if not k.startswith(prefixes)}
    return records.with_context(ctx)
