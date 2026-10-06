import logging
import re
from urllib.parse import quote

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import Map, RequestRedirect, Rule

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
    # rutas viejas (español)
    "/verificador", "/maquilador", "/empacadora", "/registro", "/mi-panel",
    "/mi-cuenta",
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
        from odoo.addons.shrimp_user_registry.controllers.main import AccountPendingApproval
        if isinstance(values.get("exception"), AccountPendingApproval):
            try:
                return 403, env["ir.ui.view"]._render_template(
                    "shrimp_user_registry.account_pending", values)
            except Exception:  # noqa: BLE001 - cae a la página 403 estándar
                pass
        return super()._get_error_html(env, code, values)

    @classmethod
    def _dispatch(cls, endpoint):
        try:
            ruta = request.httprequest.path or ""
            if (request.params and ruta.startswith(_PREFIJOS)
                    and getattr(request.dispatcher, "routing_type", "http") == "http"):
                cls._shrimp_sanear_mensajes()
        except Exception:  # noqa: BLE001 - el saneo nunca debe tumbar la petición
            pass
        return super()._dispatch(endpoint)
