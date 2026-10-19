import logging
import re
from urllib.parse import quote

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import Map, RequestRedirect, Rule

import odoo.modules.module
from odoo import models
from odoo.http import request

_logger = logging.getLogger(__name__)

# Prefijos de las pantallas propias: solo aquí se sanean los mensajes de la
# URL, para no alterar páginas del núcleo de Odoo (/web/login, /my...).
# Rutas en inglés (19.0.1.4.0) + las viejas en español, que siguen llegando
# (marcadores, correos) hasta que el redirector las manda a las nuevas.
# Bajo /my solo se sanean las pantallas propias (no /my, /my/account... del
# núcleo): /my/dashboard y /my/profile.
_PREFIJOS = (
    "/marketplace", "/register", "/verifier", "/copacker", "/packer",
    "/my/dashboard", "/my/profile",
    # Protección de datos (19.0.1.5.0)
    "/privacy", "/legal/terms", "/my/data", "/my/privacy",
    # Documentos legales versionados y usuarios de la cuenta (19.0.1.6.0)
    "/legal/", "/my/legal", "/my/team",
    # Comprobación pública de la firma electrónica del informe
    # (shrimp_firma_electronica)
    "/signature/",
    # rutas viejas (español)
    "/verificador", "/maquilador", "/empacadora", "/registro", "/mi-panel",
    "/mi-cuenta", "/privacidad", "/terminos", "/my/datos", "/verificacion/",
)

# Rutas viejas (español) -> nuevas (inglés) de ESTE módulo. Cada módulo añade
# las suyas extendiendo IrHttp._shrimp_legacy_routes() (así una ruta vieja
# nunca redirige a un módulo que no está instalado). Fuente única:
# /home/ccristhian/CamaronMarket/rutas/tools/mapping.py
_LEGACY_ROUTES = [
    ("/mi-cuenta/perfil/activar", "/my/profile/activate"),
    ("/mi-cuenta/perfil/agregar", "/my/profile/add"),
    ("/registro", "/register"),
    ("/registro/certificados", "/register/certificates"),
    ("/registro/submit", "/register/submit"),
    # Protección de datos (19.0.1.5.0). /legal/terms y no /terms: esa la usa
    # el módulo account del núcleo.
    ("/privacidad", "/privacy"),
    ("/terminos", "/legal/terms"),
    ("/my/datos", "/my/data"),
    ("/my/datos/solicitar", "/my/data/request"),
]
_LEGACY_MAPS = {}
_CODIGO = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    # ------------------------------------------------------------------
    # Compatibilidad: rutas viejas en español -> rutas nuevas en inglés
    # ------------------------------------------------------------------
    @classmethod
    def _shrimp_legacy_routes(cls):
        """Pares (ruta vieja, ruta nueva) con la sintaxis de @http.route.

        Cada módulo shrimp los extiende: ``super()._shrimp_legacy_routes() +
        [...]``. Los <param> de la vieja y la nueva se llaman igual.
        """
        return list(_LEGACY_ROUTES)

    @classmethod
    def _shrimp_legacy_map(cls):
        """Map de werkzeug con las rutas viejas (endpoint = ruta nueva).

        Se cachea por contenido de la tabla (no por registro): durante una
        instalación los módulos se cargan de a uno y la tabla va creciendo.
        """
        pares = tuple(cls._shrimp_legacy_routes())
        mapa = _LEGACY_MAPS.get(pares)
        if mapa is None:
            mapa = Map([Rule(vieja, endpoint=nueva, strict_slashes=False)
                        for vieja, nueva in pares])
            _LEGACY_MAPS.clear()
            _LEGACY_MAPS[pares] = mapa
        return mapa

    @classmethod
    def _shrimp_legacy_target(cls, path):
        """Ruta nueva para `path` si es una ruta vieja; None si no lo es.

        Conserva los parámetros de ruta (<ref>...); no mira el método HTTP
        (lo decide la ruta nueva).
        """
        if not path:
            return None
        try:
            nueva, args = cls._shrimp_legacy_map().bind("").match(path)
        except (NotFound, MethodNotAllowed, RequestRedirect):
            return None
        for clave, valor in args.items():
            nueva = nueva.replace("<%s>" % clave, quote(str(valor), safe=""))
        return nueva

    @classmethod
    def _shrimp_legacy_rewrite_url(cls, url):
        """Reescribe una URL local guardada (menú, plantilla): si su ruta es
        vieja devuelve la nueva con la misma query/fragmento; si no, la misma
        URL. Idempotente (una ruta nueva nunca coincide con una vieja)."""
        if not url or not url.startswith("/"):
            return url
        corte = min([i for i in (url.find("?"), url.find("#")) if i >= 0] or [len(url)])
        ruta, resto = url[:corte], url[corte:]
        nueva = cls._shrimp_legacy_target(ruta.rstrip("/") or "/")
        return nueva + resto if nueva else url

    @classmethod
    def _serve_fallback(cls):
        """Antes del 404: una URL vieja (español) se redirige a la nueva.

        Solo corre cuando NINGUNA ruta coincide, así que no tapa rutas reales
        ni hace «existir» rutas viejas para la barra (_shrimp_nav_route_exists).
        GET/HEAD -> 301; el resto -> 308 (conserva método y cuerpo). Se
        conserva la query string tal cual llegó (no request.params, que
        incluye el cuerpo del POST). El prefijo de idioma (/en/...) lo vuelve
        a poner request.redirect.
        """
        resp = super()._serve_fallback()
        if resp:
            return resp
        hreq = request.httprequest
        nueva = cls._shrimp_legacy_target(hreq.path)
        if not nueva:
            return None
        if hreq.query_string:
            nueva += "?" + hreq.query_string.decode("utf-8", "replace")
        codigo = 301 if hreq.method in ("GET", "HEAD") else 308
        _logger.info("ruta legacy %s %s -> %s (%s)", hreq.method, hreq.path, nueva, codigo)
        return request.redirect(nueva, code=codigo, local=True)

    @classmethod
    def _shrimp_sanear_mensajes(cls):
        """Ninguna pantalla propia pinta texto libre venido de la URL.

        - `message`/`error_message`/`msg`: solo códigos conocidos o "flash"
          (texto que el propio servidor guardó en la sesión del usuario).
        - `error`/`mensaje`: se conservan los códigos cortos (p. ej. "qty",
          "cancelada", "1") que las plantillas comparan; un texto libre se
          sustituye por el mensaje genérico.
        """
        from odoo.addons.shrimp_user_registry.controllers.main import (
            MESSAGE_TEXTS, pop_message)
        params = request.params
        for clave in ("message", "error_message", "msg"):
            if clave in params:
                valor = params.get(clave)
                texto = pop_message(valor) if isinstance(valor, str) else None
                if texto:
                    params[clave] = texto
                else:
                    params.pop(clave, None)
        for clave in ("error", "mensaje"):
            valor = params.get(clave)
            if not isinstance(valor, str) or not valor:
                continue
            if valor == "flash":
                params[clave] = pop_message("flash")
            elif not _CODIGO.match(valor):
                params[clave] = MESSAGE_TEXTS["generic"]

    @classmethod
    def _get_error_html(cls, env, code, values):
        from odoo.addons.shrimp_user_registry.controllers.main import (
            AccountPendingApproval, ShrimpPermissionDenied)
        if isinstance(values.get("exception"), AccountPendingApproval):
            try:
                return 403, env["ir.ui.view"]._render_template(
                    "shrimp_user_registry.account_pending", values)
            except Exception:  # noqa: BLE001 - cae a la página 403 estándar
                pass
        if isinstance(values.get("exception"), ShrimpPermissionDenied):
            try:
                return 403, env["ir.ui.view"]._render_template(
                    "shrimp_user_registry.permission_denied", values)
            except Exception:  # noqa: BLE001 - cae a la página 403 estándar
                pass
        return super()._get_error_html(env, code, values)

    @classmethod
    def _shrimp_permission_gate(cls, endpoint):
        """Permisos de los subusuarios (CR-12): cada ruta del portal que
        escribe exige el permiso de la matriz central
        (shrimp_permission_matrix.SHRIMP_ROUTE_PERMISSIONS). Sin él, 403 con
        una página que dice qué permiso falta. El administrador, los dueños
        de cuentas sin miembros y los usuarios internos no tienen límite. La
        API REST (auth="none") tiene su propia tabla."""
        from odoo.addons.shrimp_user_registry.controllers.main import ShrimpPermissionDenied
        from .shrimp_permission_matrix import WRITE_METHODS, shrimp_route_rule
        routing = getattr(endpoint, "routing", None) or {}
        if routing.get("auth") != "user":
            return
        user = request.env.user
        if not user or user._is_public() or not user.share:
            return
        if not user._shrimp_restricted_member():
            return
        method = request.httprequest.method
        regla = shrimp_route_rule(routing.get("routes"), method)
        if not regla:
            if method in WRITE_METHODS:
                _logger.warning("shrimp: ruta %s %s sin permiso en la matriz de subusuarios",
                                method, routing.get("routes"))
            return
        perm, scope = regla
        if user._shrimp_has_perm(perm, scope=scope):
            return
        _logger.info("shrimp: %s %s denegado al subusuario %s (falta %s)",
                     method, request.httprequest.path, user.login, perm)
        raise ShrimpPermissionDenied(user._shrimp_perm_message(perm))

    @classmethod
    def _shrimp_privacy_gate(cls, endpoint):
        """Redirección a /my/legal/accept si el usuario portal tiene versiones
        MAYORES sin aceptar de los documentos que le aplican (generales y
        anexos de los perfiles de su empresa). Páginas web GET y, desde
        19.0.1.12.0, también los POST de formularios web (no JSON, API,
        estáticos, logout ni las propias páginas legales); nunca a
        internos. Las versiones MENORES no bloquean: salen como aviso.

        Durante los tests de Odoo está apagado salvo que el parámetro
        shrimp_user_registry.privacy_gate_in_tests valga 1: los usuarios que
        crean los tests no tienen aceptación registrada y cada GET de su
        portal terminaría en la pantalla de aceptación.
        """
        from odoo.addons.shrimp_user_registry.controllers.main import (
            LEGAL_EXEMPT_PREFIXES as PRIVACY_EXEMPT_PREFIXES, legal_gate_needed as privacy_consent_pending)
        hreq = request.httprequest
        es_lectura = hreq.method in ("GET", "HEAD")
        if getattr(request.dispatcher, "routing_type", "http") != "http":
            return None
        routing = getattr(endpoint, "routing", None) or {}
        if not routing.get("website") or routing.get("auth") not in ("user", "public"):
            return None
        ruta = hreq.path or "/"
        # Con o sin prefijo de idioma (/en/privacy): se mira también la ruta
        # sin el primer tramo.
        tramos = ruta.split("/", 2)
        sin_idioma = "/" + tramos[2] if len(tramos) > 2 else ruta
        if ruta.startswith(PRIVACY_EXEMPT_PREFIXES) or sin_idioma.startswith(PRIVACY_EXEMPT_PREFIXES):
            return None
        if odoo.modules.module.current_test and request.env["ir.config_parameter"].sudo().get_param(
                "shrimp_user_registry.privacy_gate_in_tests") != "1":
            return None
        if not privacy_consent_pending(request.env.user):
            return None
        if not es_lectura:
            # Hueco #6: un POST directo (formulario viejo, script) tampoco
            # opera sin aceptar las versiones MAYORES. No se ejecuta la
            # acción: se manda a aceptar y luego a la página de origen.
            origen = hreq.referrer or ""
            host = hreq.host_url
            destino = origen[len(host) - 1:] if origen.startswith(host) else "/my"
            _logger.info("shrimp: POST %s bloqueado (documentos legales MAYORES pendientes, uid %s)",
                         ruta, request.env.uid)
            return request.redirect("/my/legal/accept?redirect=%s" % quote(destino or "/my", safe=""),
                                    code=303, local=True)
        destino = ruta + ("?" + hreq.query_string.decode("utf-8", "replace") if hreq.query_string else "")
        return request.redirect("/my/legal/accept?redirect=%s" % quote(destino, safe=""), local=True)

    @classmethod
    def _dispatch(cls, endpoint):
        try:
            ruta = request.httprequest.path or ""
            if (request.params and ruta.startswith(_PREFIJOS)
                    and getattr(request.dispatcher, "routing_type", "http") == "http"):
                cls._shrimp_sanear_mensajes()
        except Exception:  # noqa: BLE001 - el saneo nunca debe tumbar la petición
            pass
        try:
            gate = cls._shrimp_privacy_gate(endpoint)
        except Exception:  # noqa: BLE001 - la aceptación nunca debe tumbar la petición
            _logger.exception("shrimp: error al comprobar la aceptación de la política")
            gate = None
        if gate is not None:
            return gate
        # Permisos del subusuario (CR-12): fuera del try, el 403 tiene que llegar.
        cls._shrimp_permission_gate(endpoint)
        return super()._dispatch(endpoint)
