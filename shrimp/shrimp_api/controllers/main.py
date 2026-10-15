"""El único punto de entrada de /api/v1 y la documentación OpenAPI."""

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import psycopg2

from odoo import http
from odoo.exceptions import AccessDenied, AccessError, MissingError, UserError, ValidationError
from odoo.http import request
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from ..models.api_key import KEY_RE
from ..models.res_config_settings import app_version_info
from .framework import (API_PREFIX, APP_PLATFORM_HEADER, APP_VERSION_HEADER, DEVICE_HEADER,
                        MONEY_SCHEMA, REF_SCHEMA, ROUTES,
                        SIGNATURE_HEADER, SIGNATURE_MAX_AGE, SIGNATURE_MAX_SKEW,
                        SIGNATURE_TS_HEADER, SIGNED_IF_DEVICE, SUPERVISOR_ONLY, ApiCtx, ApiError,
                        ApiResponse, PROBLEM_TITLES, api_route, canonical_string, object_schema,
                        parse_version, resolve)

_logger = logging.getLogger(__name__)

METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
MAX_BODY = 1024 * 1024
PUBLIC_HEADERS = "Accept, Content-Type, If-None-Match, X-App-Version, X-App-Platform"
PRIVATE_HEADERS = ("Authorization, X-API-Key, Content-Type, Accept, Idempotency-Key, "
                   "X-Shrimp-Role, X-Device-Id, X-Signature, X-Signature-Timestamp, "
                   "X-App-Version, X-App-Platform")
EXPOSE = "X-Request-Id, X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After, Idempotent-Replayed"

_AUTH_ERRORS = {
    "missing": (401, "missing-api-key", "Falta la clave de API. Envíala en «Authorization: "
                                        "Bearer <clave>» o en «X-API-Key»."),
    "invalid": (401, "invalid-api-key", "La clave de API no es válida."),
    "expired": (401, "expired-api-key", "La clave de API caducó. Pide una nueva en /my/api-keys."),
    "revoked": (401, "revoked-api-key", "La clave de API fue revocada."),
    "user": (403, "user-not-allowed", "El usuario dueño de la clave no puede usar la API externa "
                                      "(inactivo o interno: las integraciones internas usan JSON-2)."),
}

_RECORD_REPR = re.compile(r"\b[a-z_]+(?:\.[a-z_]+)+\([\d, ]*\)")


def _business_message(exc):
    msg = exc.args[0] if getattr(exc, "args", None) else str(exc)
    msg = str(msg)
    # Nunca un repr de recordset (lleva ids enteros) en un mensaje público.
    return _RECORD_REPR.sub("[registro]", msg)[:2000]


_SIGNATURE_ERRORS = {
    "device-not-registered": (403, "Esta operación exige un dispositivo registrado: registra este "
                                   "teléfono con POST /me/devices y firma la petición."),
    "signature-required": (403, "Esta operación exige firma: faltan las cabeceras X-Device-Id, "
                                "X-Signature-Timestamp y X-Signature."),
    "device-revoked": (403, "El dispositivo fue revocado. Vuelve a iniciar sesión en la app."),
    "invalid-signature": (403, "La firma de la petición no es válida."),
    "signature-expired": (403, "El timestamp de la firma está fuera de la ventana permitida "
                               "(hasta 7 días en el pasado y 10 minutos en el futuro)."),
    "signature-replayed": (409, "Esta firma ya se usó. Firma de nuevo la operación."),
}


def _signature_error(slug, detail=None):
    status, default = _SIGNATURE_ERRORS[slug]
    return ApiError(status, slug, detail or default)


class ShrimpApiController(http.Controller):

    # ------------------------------------------------------------------
    # Respuestas
    # ------------------------------------------------------------------
    def _json(self, data, status=200, headers=None, content_type="application/json; charset=utf-8"):
        body = json.dumps(data, ensure_ascii=False, default=str)
        hdrs = [("Content-Type", content_type)] + list(headers or [])
        return request.make_response(body, headers=hdrs, status=status)

    def _problem(self, err, rid, headers=None):
        base = request.httprequest.host_url.rstrip("/")
        payload = {
            "type": "%s%s/docs#problem-%s" % (base, API_PREFIX, err.slug),
            "title": err.title or PROBLEM_TITLES.get(err.status, "Error"),
            "status": err.status,
            "detail": err.detail or err.title,
            "request_id": rid,
        }
        if err.errors:
            payload["errors"] = err.errors
        for k, v in (err.extra or {}).items():
            payload.setdefault(k, v)
        return self._json(payload, status=err.status,
                          headers=list(headers or []) + list(err.headers),
                          content_type="application/problem+json; charset=utf-8")

    # ------------------------------------------------------------------
    # Despachador
    # ------------------------------------------------------------------
    @http.route([API_PREFIX, API_PREFIX + "/", API_PREFIX + "/<path:subpath>"], type="http",
                auth="none", csrf=False, save_session=False, methods=METHODS,
                readonly=False)
    def api_dispatch(self, subpath="", **kw):
        rid = uuid.uuid4().hex
        hr = request.httprequest
        method = hr.method.upper()
        path = "/" + (subpath or "").strip("/")
        origin = hr.headers.get("Origin")
        extra = [("X-Request-Id", rid)]
        started = time.monotonic()
        try:
            installed = request.env.registry._init_modules
            if method == "OPTIONS":
                return self._preflight(path, origin, installed, extra)
            route, pparams, allowed = resolve(path, method, installed)
            if not route:
                if allowed:
                    raise ApiError(405, "method-not-allowed",
                                   "Métodos permitidos: %s." % ", ".join(sorted(allowed)),
                                   headers=[("Allow", ", ".join(sorted(allowed | {"OPTIONS"})))])
                raise ApiError(404, "not-found", "No existe la ruta %s%s." % (API_PREFIX, path))
            if route.public:
                extra += [("Access-Control-Allow-Origin", "*"),
                          ("Access-Control-Expose-Headers", EXPOSE)]
                resp = self._serve_public(route, pparams, path, method, rid, extra)
            else:
                resp = self._serve_private(route, pparams, path, method, rid, extra, origin)
        except ApiError as err:
            resp = self._problem(err, rid, extra)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except Exception:  # noqa: BLE001
            _logger.exception("API %s %s%s falló (request_id=%s)", method, API_PREFIX, path, rid)
            resp = self._problem(ApiError(500, "internal-error",
                                          "Error interno. Indica este request_id al soporte."),
                                 rid, extra)
        _logger.debug("API %s %s -> %s (%.0f ms)", method, path, resp.status_code,
                      (time.monotonic() - started) * 1000)
        return resp

    # ------------------------------------------------------------------
    def _preflight(self, path, origin, installed, extra):
        is_public = path.startswith("/public/") or path in ("/openapi.json", "/docs", "/",
                                                             "/auth/login")
        headers = list(extra)
        if is_public:
            headers += [("Access-Control-Allow-Origin", "*"),
                        ("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS"),
                        ("Access-Control-Allow-Headers", PUBLIC_HEADERS),
                        ("Access-Control-Max-Age", "86400")]
        elif origin and self._origin_registered(origin):
            headers += [("Access-Control-Allow-Origin", origin),
                        ("Access-Control-Allow-Methods", "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"),
                        ("Access-Control-Allow-Headers", PRIVATE_HEADERS),
                        ("Access-Control-Max-Age", "600"),
                        ("Vary", "Origin")]
        return request.make_response("", headers=headers, status=204)

    def _origin_registered(self, origin):
        if not re.match(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$", origin or ""):
            return False
        keys = request.env["shrimp.api.key"].sudo().search(
            [("allowed_origins", "ilike", origin), ("revoked_at", "=", False)], limit=20)
        return any(k._api_origin_allowed(origin) for k in keys)

    # ------------------------------------------------------------------
    def _read_body(self, method, route=None):
        hr = request.httprequest
        if method not in ("POST", "PUT", "PATCH"):
            return {}, b""
        limit = (route.max_body if route is not None and route.max_body else None) or MAX_BODY
        if hr.content_length and hr.content_length > limit:
            raise ApiError(413, "payload-too-large", "El cuerpo supera %s MB."
                           % ("%g" % round(limit / (1024 * 1024), 1)))
        is_json = hr.mimetype == "application/json" or hr.mimetype.endswith("+json")
        # Se mira el tipo ANTES de leer: si llega un formulario, werkzeug ya se
        # comió el cuerpo al parsearlo y parecería vacío.
        if (hr.content_length or 0) > 0 and not is_json:
            raise ApiError(415, "unsupported-media-type",
                           "Envía el cuerpo como application/json.")
        raw = hr.get_data(cache=True) or b""
        if not raw.strip():
            return {}, raw
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "invalid-json", "El cuerpo no es JSON válido.")
        if not isinstance(body, dict):
            raise ApiError(400, "body-not-object",
                           "El cuerpo tiene que ser un objeto JSON ({...}), no una lista ni un valor suelto.")
        return body, raw

    def _params(self):
        return {k: v for k, v in request.httprequest.args.items()}

    def _run(self, route, ctx, pparams):
        """Ejecuta el handler en un savepoint: si falla, no queda nada a medias
        (Odoo confirmaría la transacción porque la respuesta de error no es una
        excepción)."""
        kept = None
        try:
            with request.env.cr.savepoint():
                try:
                    result = route.func(ctx, **pparams)
                except ApiError as err:
                    if not err.keep_changes:
                        raise
                    # El handler quiere conservar lo escrito (ver ApiError):
                    # se sale del savepoint sin excepción para que no se
                    # revierta y se relanza después.
                    kept = err
        except ApiError:
            raise
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except (ValidationError, UserError) as e:
            raise ApiError(422, "business-rule", _business_message(e))
        except (AccessError, AccessDenied):
            raise ApiError(403, "access-denied", "No tienes permiso para esta operación sobre este recurso.")
        except MissingError:
            raise ApiError(404, "not-found", "El recurso ya no existe.")
        except psycopg2.IntegrityError:
            raise ApiError(409, "conflict", "La operación choca con un dato existente "
                                            "(duplicado o en uso).")
        if kept is not None:
            raise kept
        if isinstance(result, ApiResponse):
            return result
        return ApiResponse(result, status=route.status)

    def _render(self, result, headers):
        if result.raw is not None:
            return request.make_response(result.raw, headers=[
                ("Content-Type", result.content_type or "application/octet-stream")]
                + list(headers) + list(result.headers), status=result.status)
        if result.status == 204:
            return request.make_response("", headers=list(headers) + list(result.headers), status=204)
        return self._json(result.data, status=result.status,
                          headers=list(headers) + list(result.headers))

    # ------------------------------------------------------------------
    def _serve_public(self, route, pparams, path, method, rid, extra):
        public_user = request.env.ref("base.public_user")
        request.update_env(user=public_user.id)
        limit = int(request.env["ir.config_parameter"].sudo().get_param(
            "shrimp_api.public_rate_per_min") or 0)
        if limit:
            ip = request.httprequest.remote_addr or "?"
            with request.env.registry.cursor() as cr:
                ok, hits, retry = request.env["shrimp.api.rate.bucket"]._hit(
                    cr, "ip:%s" % ip, "p", limit)
            if not ok:
                raise ApiError(429, "rate-limited", "Demasiadas peticiones desde esta IP.",
                               headers=[("Retry-After", str(retry))])
        # Las rutas públicas eran solo GET; el inicio de sesión de la app
        # (POST /auth/login) lleva cuerpo y su respuesta no se cachea.
        if route.path == "/auth/login":
            # Versión mínima de la app (contrato de mejoras, C1): el inicio de
            # sesión solo lo usa la app, así que también lo exige.
            self._check_app_version()
        body, _raw = self._read_body(method, route)
        ctx = ApiCtx(request.env, None, self._params(), body, method, path, rid,
                     request.httprequest.remote_addr, route)
        result = self._run(route, ctx, pparams)
        cache = "public, max-age=60" if method in ("GET", "HEAD") else "no-store"
        headers = list(extra) + [("Cache-Control", cache)]
        return self._render(result, headers)

    def _authenticate(self, route=None):
        hr = request.httprequest
        bearer = None
        auth = hr.headers.get("Authorization") or ""
        if auth:
            m = re.match(r"^\s*Bearer\s+(\S+)\s*$", auth, re.I)
            if not m:
                raise ApiError(401, "invalid-authorization",
                               "Cabecera Authorization mal formada: usa «Bearer <clave>».",
                               headers=[("WWW-Authenticate", 'Bearer realm="api"')])
            bearer = m.group(1)
        xkey = (hr.headers.get("X-API-Key") or "").strip() or None
        if bearer and xkey and bearer != xkey:
            raise ApiError(400, "ambiguous-credentials",
                           "Se recibieron dos claves distintas (Authorization y X-API-Key).")
        Key = request.env["shrimp.api.key"].sudo()
        key, reason = Key._api_authenticate(bearer or xkey)
        if not key:
            if reason == "revoked" and route is not None and route.signed:
                # La app que firma con un dispositivo revocado (lo que revoca
                # también su clave) recibe el error del contrato y no un 401
                # genérico. _api_authenticate solo dice «revoked» después de
                # comprobar el secreto, así que no se filtra nada.
                self._raise_if_device_revoked(bearer or xkey)
            status, slug, detail = _AUTH_ERRORS.get(reason, _AUTH_ERRORS["invalid"])
            raise ApiError(status, slug, detail,
                           headers=[("WWW-Authenticate", 'Bearer realm="api"')] if status == 401 else [])
        return key

    def _raise_if_device_revoked(self, raw_key):
        dev_ref = (request.httprequest.headers.get(DEVICE_HEADER) or "").strip()
        m = KEY_RE.match((raw_key or "").strip())
        if not dev_ref or len(dev_ref) > 64 or not m:
            return
        device = request.env["shrimp.api.device"].sudo().with_context(active_test=False).search(
            [("uuid_ref", "=", dev_ref), ("api_key_id.key_prefix", "=", m.group(1))], limit=1)
        if device and not device.active:
            raise _signature_error("device-revoked")

    # ------------------------------------------------------------------
    # Firma de operaciones (claves de la app). Contrato:
    # docs/app/CONTRATO-FIRMA-DISPOSITIVO.md
    # ------------------------------------------------------------------
    def _check_signature(self, key, method, route):
        """Comprueba dispositivo, ventana y firma. Devuelve un dict con lo que
        hace falta para gastar la firma y auditarla (o None si la ruta solo
        se firma cuando ya hay dispositivo y no lo hay); no escribe nada."""
        hr = request.httprequest
        Device = request.env["shrimp.api.device"].sudo().with_context(active_test=False)
        dev_ref = (hr.headers.get(DEVICE_HEADER) or "").strip()
        ts_raw = (hr.headers.get(SIGNATURE_TS_HEADER) or "").strip()
        sig_raw = (hr.headers.get(SIGNATURE_HEADER) or "").strip()
        has_device = bool(Device.search_count([("api_key_id", "=", key.id), ("active", "=", True)],
                                              limit=1))
        if route.signed == SIGNED_IF_DEVICE and not has_device:
            # Primer registro del dispositivo de esta clave: va sin firma
            # (todavía no hay con qué firmar). Cabeceras de firma, si vienen,
            # se ignoran.
            return None
        if not (dev_ref or ts_raw or sig_raw):
            if not has_device:
                raise _signature_error("device-not-registered")
            raise _signature_error("signature-required")
        missing = [h for h, v in ((DEVICE_HEADER, dev_ref), (SIGNATURE_TS_HEADER, ts_raw),
                                  (SIGNATURE_HEADER, sig_raw)) if not v]
        if missing:
            raise _signature_error("signature-required", "Faltan las cabeceras: %s." % ", ".join(missing))
        device = Device.browse()
        if len(dev_ref) <= 64:
            device = Device.search([("uuid_ref", "=", dev_ref), ("api_key_id", "=", key.id)], limit=1)
        if not device:
            raise _signature_error("device-not-registered",
                                   "El dispositivo %s no está registrado con esta clave." % DEVICE_HEADER)
        if not device.active:
            raise _signature_error("device-revoked")
        if not re.fullmatch(r"[0-9]{1,12}", ts_raw):
            raise _signature_error("invalid-signature",
                                   "%s tiene que ser un entero (segundos Unix)." % SIGNATURE_TS_HEADER)
        ts = int(ts_raw)
        now = time.time()
        if ts < now - SIGNATURE_MAX_AGE or ts > now + SIGNATURE_MAX_SKEW:
            raise _signature_error("signature-expired")
        idem_key = (hr.headers.get("Idempotency-Key") or "").strip()
        body = hr.get_data(cache=True) or b""
        message = canonical_string(method, hr.path, ts_raw, idem_key, body)
        sig_hash = device._api_verify(message, sig_raw)
        if not sig_hash:
            raise _signature_error("invalid-signature")
        return {"device": device, "timestamp": ts, "signature_hash": sig_hash,
                "idempotency_key": idem_key, "body_sha256": hashlib.sha256(body).hexdigest(),
                "path": hr.path, "audit": None}

    def _consume_signature(self, sig, ctx):
        """Gasta la firma (antirreplay) y deja la auditoría, en la transacción
        de la operación. 409 signature-replayed si ya se usó."""
        device = sig["device"]
        if not request.env["shrimp.api.signature.nonce"].sudo()._api_consume(
                sig["signature_hash"], device.id):
            raise _signature_error("signature-replayed")
        sig["audit"] = ctx.signed_operation = request.env["shrimp.api.signed.operation"].sudo().create({
            "user_id": ctx.user.id,
            "device_id": device.id,
            "device_ref": device.uuid_ref,
            "method": ctx.method,
            "path": sig["path"],
            "idempotency_key": sig["idempotency_key"] or False,
            "signed_timestamp": sig["timestamp"],
            "signed_at": datetime.fromtimestamp(sig["timestamp"], tz=timezone.utc).replace(tzinfo=None),
            "signature_hash": sig["signature_hash"],
            "body_sha256": sig["body_sha256"],
            "ip": (ctx.remote_addr or "")[:64] or False,
            "request_id": ctx.request_id,
        })
        request.env["shrimp.api.device"]._api_touch_after_commit(
            request.env.cr, device.id, ctx.remote_addr)

    def _finish_signature(self, sig, status_code):
        """Tras ejecutar: anota la respuesta en la auditoría o, si fue un 5xx
        (la operación no se hizo), devuelve la firma para poder reintentar."""
        if not sig or not sig.get("audit"):
            return
        audit = sig["audit"].with_context(shrimp_api_audit=True)
        if status_code is None or status_code >= 500:
            audit.unlink()
            request.env["shrimp.api.signature.nonce"].sudo()._api_release(sig["signature_hash"])
        else:
            audit.write({"status_code": status_code})

    def _run_signed(self, route, ctx, pparams, sig, headers):
        """Operación firmada sin Idempotency-Key (solo PATCH: los POST la
        exigen siempre). Sin respuesta guardada, repetir la firma es un 409."""
        self._consume_signature(sig, ctx)
        try:
            response = self._render(self._run(route, ctx, pparams), headers)
        except ApiError as err:
            response = self._problem(err, ctx.request_id, headers)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except Exception:
            self._finish_signature(sig, None)
            raise
        self._finish_signature(sig, response.status_code)
        return response

    def _serve_private(self, route, pparams, path, method, rid, extra, origin):
        hr = request.httprequest
        key = self._authenticate(route)
        ip = hr.remote_addr or ""
        if not key._api_ip_allowed(ip):
            raise ApiError(403, "ip-not-allowed", "Esta clave no admite peticiones desde tu IP.")
        if origin and key._api_origin_allowed(origin):
            extra += [("Access-Control-Allow-Origin", origin), ("Vary", "Origin"),
                      ("Access-Control-Expose-Headers", EXPOSE)]
        if key.is_app_key and route.path != "/auth/logout":
            # Solo las claves de la app (las de /auth/login, las de supervisor
            # y las de «actuar como»); las de integraciones no envían versión.
            # Cerrar sesión queda libre: una app vieja tiene que poder revocar
            # su clave aunque ya no se admita para nada más.
            self._check_app_version()
        self._check_supervisor(key, route, method)
        scopes = key._api_scope_codes()
        if route.scopes and not (set(route.scopes) & scopes):
            raise ApiError(403, "insufficient-scope",
                           "La clave no tiene el scope necesario: %s." % " o ".join(route.scopes))

        # Límite de peticiones en un cursor aparte que se confirma enseguida
        # (cuenta aunque la petición falle después).
        read_limit, write_limit = key._api_limits()
        kind, limit = ("w", write_limit) if method in WRITE_METHODS else ("r", read_limit)
        with request.env.registry.cursor() as cr:
            ok, hits, retry = request.env["shrimp.api.rate.bucket"]._hit(cr, "k:%s" % key.id, kind, limit)
        # El último uso NO va en ese cursor: actualiza la fila de
        # shrimp_api_key, y esta transacción ya tiene su foto (REPEATABLE
        # READ) desde que autenticó la clave. Si otro cursor confirma un
        # UPDATE de esa fila, el INSERT en shrimp_api_idempotency (FK a la
        # clave, que bloquea la fila con KEY SHARE) choca con «could not
        # serialize access due to concurrent update». Se hace después de
        # confirmar la petición.
        request.env["shrimp.api.key"]._api_touch_after_commit(request.env.cr, key.id, ip)
        extra += [("X-RateLimit-Limit", str(limit)),
                  ("X-RateLimit-Remaining", str(max(0, limit - hits)))]
        if not ok:
            raise ApiError(429, "rate-limited",
                           "Límite de %s peticiones de %s por minuto superado."
                           % (limit, "escritura" if kind == "w" else "lectura"),
                           headers=[("Retry-After", str(retry))])

        user = key.user_id
        context = dict(request.env.context, lang=user.lang or "es_EC", tz=user.tz or "America/Guayaquil",
                       shrimp_api_queue_mail=True, shrimp_api_request=rid)
        request.update_env(user=user.id, context=context)
        body, raw = self._read_body(method, route)
        # Operación sensible con clave de la app: dispositivo + firma, ANTES
        # de mirar la idempotencia (un reintento también tiene que venir
        # firmado por un dispositivo vigente) y sin escribir nada todavía.
        # La clave de «actuar como» (modo supervisor) no tiene dispositivo
        # propio: la autenticó el dispositivo del supervisor al emitirla, y
        # como solo lee nunca llega aquí con una ruta firmada (antes da
        # insufficient-scope en _check_supervisor o en los scopes).
        sig = self._check_signature(key, method, route) \
            if route.signed and key.is_app_key and not key.supervisor_key_id else None
        ctx = ApiCtx(request.env, key.with_env(request.env).sudo(), self._params(), body,
                     method, path, rid, ip, route)
        if sig:
            ctx.device = sig["device"].with_env(request.env).sudo()
        headers = list(extra) + [("Cache-Control", "no-store")]

        if method == "POST" or (sig and sig["idempotency_key"]):
            # Un PATCH firmado con Idempotency-Key también guarda su respuesta:
            # el reintento legítimo (misma clave, ruta y cuerpo) recibe la de
            # siempre en vez de chocar con el antirreplay.
            return self._idempotent(route, ctx, pparams, key, raw, headers, sig)
        if sig:
            return self._run_signed(route, ctx, pparams, sig, headers)
        return self._render(self._run(route, ctx, pparams), headers)

    def _check_app_version(self):
        """426 app-update-required si hay versión mínima y la app que llama
        es más vieja o no dice su versión (contrato de mejoras, C1)."""
        info = app_version_info(request.env)
        minimo = parse_version(info["min_version"] or "")
        if not minimo:
            return
        sent = (request.httprequest.headers.get(APP_VERSION_HEADER) or "").strip()
        version = parse_version(sent)
        if version and version >= minimo:
            return
        platform = (request.httprequest.headers.get(APP_PLATFORM_HEADER) or "").strip().lower()
        _logger.info("API: app %s (%s) por debajo de la mínima %s -> 426",
                     sent or "sin versión", platform or "?", info["min_version"])
        raise ApiError(
            426, "app-update-required",
            info["message"] or ("Esta versión de la app ya no se admite. Actualízala a la %s o "
                                "posterior para seguir." % info["min_version"]),
            extra={"min_version": info["min_version"], "download_url": info["download_url"]})

    def _check_supervisor(self, key, route, method):
        """Modo supervisor (contrato, Anexo B), antes que los scopes.

        * Clave de supervisor: solo las rutas marcadas supervisor=True o
          SUPERVISOR_ONLY; cualquier otra (todas las de datos) → 403
          supervisor-key.
        * Las rutas SUPERVISOR_ONLY no las admite ninguna otra clave (tampoco
          una de «actuar como»: no se encadena) → 403 supervisor-only.
        * Clave de «actuar como»: solo lectura. Sus scopes ya son *:read, pero
          hay rutas de escritura sin scope (registrar o revocar dispositivos):
          toda escritura salvo cerrar sesión → 403 insufficient-scope.
        """
        if key.is_supervisor_key:
            if not route.supervisor:
                raise ApiError(403, "supervisor-key",
                               "Una clave de supervisor no lee ni escribe datos de socios: usa "
                               "/supervisor/partners y /supervisor/act-as para ver la cuenta de "
                               "un socio (solo lectura).")
            return
        if route.supervisor == SUPERVISOR_ONLY:
            raise ApiError(403, "supervisor-only",
                           "Esta operación es solo para la clave de supervisor de la app.")
        if key.supervisor_key_id and method in WRITE_METHODS and route.path != "/auth/logout":
            raise ApiError(403, "insufficient-scope",
                           "Clave de solo lectura («actuar como» de un supervisor): no admite "
                           "operaciones de escritura.")

    def _idempotent(self, route, ctx, pparams, key, raw, headers, sig=None):
        hr = request.httprequest
        method = ctx.method
        idem_key = (hr.headers.get("Idempotency-Key") or "").strip()
        if not idem_key:
            raise ApiError(400, "idempotency-key-required",
                           "Los POST requieren la cabecera Idempotency-Key (p. ej. un UUID v4).")
        if len(idem_key) > 255:
            raise ApiError(400, "idempotency-key-too-long", "Idempotency-Key admite hasta 255 caracteres.")
        req_hash = hashlib.sha256(method.encode() + b" " + ctx.path.encode() + b"\n"
                                  + (raw or b"")).hexdigest()
        Idem = request.env["shrimp.api.idempotency"].sudo()
        existing = Idem.search([("api_key_id", "=", key.id), ("key", "=", idem_key)], limit=1)
        if existing:
            if existing.request_hash != req_hash:
                raise ApiError(422, "idempotency-key-reused",
                               "Esta Idempotency-Key ya se usó con otra petición (otra ruta o cuerpo).")
            if existing.state != "done":
                raise ApiError(409, "idempotency-in-progress",
                               "Hay una petición con esta Idempotency-Key en curso.")
            # Reintento idempotente legítimo: misma respuesta, aunque la firma
            # ya esté gastada (no se vuelve a ejecutar ni a auditar).
            replay = list(headers) + [("Idempotent-Replayed", "true"),
                                      ("Content-Type", existing.response_type or "application/json; charset=utf-8")]
            return request.make_response(existing.response_body or "", headers=replay,
                                         status=existing.status_code or 200)
        try:
            with request.env.cr.savepoint():
                record = Idem.create({"api_key_id": key.id, "key": idem_key, "method": method,
                                      "path": ctx.path, "request_hash": req_hash})
                record.flush_recordset()
        except psycopg2.IntegrityError:
            raise ApiError(409, "idempotency-in-progress",
                           "Hay una petición con esta Idempotency-Key en curso.")
        if sig:
            try:
                self._consume_signature(sig, ctx)
            except ApiError:
                # Firma repetida sin respuesta guardada (p. ej. pasadas las
                # 24 h de la idempotencia): no se deja la clave «en curso».
                record.unlink()
                raise
        try:
            result = self._run(route, ctx, pparams)
            response = self._render(result, headers)
        except ApiError as err:
            response = self._problem(err, ctx.request_id, headers)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except Exception:
            # Fallo inesperado: se libera la clave para que el cliente pueda
            # reintentar (si no, quedaría "en curso" 24 h).
            record.unlink()
            self._finish_signature(sig, None)
            raise
        self._finish_signature(sig, response.status_code)
        if response.status_code >= 500:
            record.unlink()
        else:
            record.write({"state": "done", "status_code": response.status_code,
                          "response_body": self._storable_body(route, response),
                          "response_type": response.headers.get("Content-Type")})
        return response


    def _storable_body(self, route, response):
        """Cuerpo que se guarda para la idempotencia: sin los secretos de la
        ruta (route.secret_fields), que no se guardan nunca en claro."""
        text = response.get_data(as_text=True)
        if not route.secret_fields or not text:
            return text
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict):
            for name in route.secret_fields:
                if name in data:
                    data[name] = None
        return json.dumps(data, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# OpenAPI 3.1 y página de documentación
# ---------------------------------------------------------------------------
def _problem_ref(desc):
    return {"description": desc, "content": {"application/problem+json": {
        "schema": {"$ref": "#/components/schemas/Problem"}}}}


def build_openapi(base_url, installed):
    paths = {}
    tags = set()
    for route in ROUTES:
        if route.module not in installed:
            continue
        op = {
            "operationId": "%s_%s" % (route.method.lower(),
                                      re.sub(r"[^a-zA-Z0-9]+", "_", route.path).strip("_")),
            "summary": route.summary or route.path,
            "tags": list(route.tags) or ["general"],
            "responses": {},
        }
        tags.update(op["tags"])
        if route.description:
            op["description"] = route.description
        if route.action_alias:
            op["description"] = (op.get("description", "") +
                                 "\n\nTambién se acepta como `%s`." % route.action_alias).strip()
        params = []
        for name in re.findall(r"\{(\w+)\}", route.path):
            params.append({"name": name, "in": "path", "required": True,
                           "schema": {"type": "string"},
                           "description": "uuid del recurso" if name == "id" else name})
        for name, spec in (route.params or {}).items():
            params.append({"name": name, "in": "query", "required": False,
                           "schema": spec.schema() if hasattr(spec, "schema") else spec,
                           "description": getattr(spec, "description", "")})
        if route.paginated:
            params += [
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1,
                                                            "maximum": 200, "default": 50}},
                {"name": "cursor", "in": "query", "schema": {"type": "string"},
                 "description": "Valor opaco de meta.next_cursor."},
                {"name": "updated_since", "in": "query",
                 "schema": {"type": "string", "format": "date-time"},
                 "description": "Solo registros modificados desde esta fecha (ISO-8601)."},
            ]
        if route.method == "POST" and not route.public:
            params.append({"name": "Idempotency-Key", "in": "header", "required": True,
                           "schema": {"type": "string", "maxLength": 255}})
        if route.signed and not route.public:
            if route.method != "POST":
                params.append({"name": "Idempotency-Key", "in": "header", "required": False,
                               "schema": {"type": "string", "maxLength": 255},
                               "description": "Recomendada con la firma: el reintento con la misma "
                                              "clave recibe la respuesta guardada."})
            params += [
                {"name": DEVICE_HEADER, "in": "header", "required": False,
                 "schema": {"type": "string", "format": "uuid"},
                 "description": "Obligatoria con una clave de la app: uuid del dispositivo registrado."},
                {"name": SIGNATURE_TS_HEADER, "in": "header", "required": False,
                 "schema": {"type": "integer"},
                 "description": "Obligatoria con una clave de la app: segundos Unix de la firma."},
                {"name": SIGNATURE_HEADER, "in": "header", "required": False,
                 "schema": {"type": "string", "format": "byte"},
                 "description": "Obligatoria con una clave de la app: firma ES256 (DER, base64)."},
            ]
            if route.signed == SIGNED_IF_DEVICE:
                op["x-signed"] = "if-device"
                op["description"] = (op.get("description", "") +
                                     "\n\n**Firmada si ya hay dispositivo:** con una clave de la app "
                                     "que ya tiene un dispositivo activo, la petición va firmada por ese "
                                     "dispositivo (README, «Dispositivos y firma de operaciones»).").strip()
            else:
                op["x-signed"] = True
                op["description"] = (op.get("description", "") +
                                     "\n\n**Operación firmada:** con una clave de la app móvil exige "
                                     "dispositivo registrado y firma ES256 (README, «Dispositivos y firma "
                                     "de operaciones»). Las demás claves no la necesitan.").strip()
        if route.supervisor == SUPERVISOR_ONLY:
            op["x-supervisor"] = "only"
            op["description"] = (op.get("description", "") +
                                 "\n\n**Solo con clave de supervisor** (README, «Modo supervisor»); "
                                 "cualquier otra clave recibe 403 supervisor-only.").strip()
        elif route.supervisor and not route.public:
            op["x-supervisor"] = True
        if params:
            op["parameters"] = params
        if route.body:
            op["requestBody"] = {"required": True, "content": {"application/json": {
                "schema": object_schema(route.body)}}}
        ok_schema = route.response or {"type": "object"}
        if route.paginated:
            ok_schema = {"type": "object", "properties": {
                "data": {"type": "array", "items": ok_schema},
                "meta": {"$ref": "#/components/schemas/PageMeta"}}}
        if route.binary:
            op["responses"][str(route.status)] = {"description": "Archivo", "content": {
                route.binary: {"schema": {"type": "string", "format": "binary"}}}}
        elif route.status == 204:
            op["responses"]["204"] = {"description": "Sin contenido"}
        else:
            op["responses"][str(route.status)] = {"description": "Correcto", "content": {
                "application/json": {"schema": ok_schema}}}
        if route.public:
            op["security"] = []
            op["responses"]["429"] = _problem_ref("Límite por IP superado (si está activo)")
        else:
            op["security"] = [{"bearerAuth": []}, {"apiKeyHeader": []}]
            if route.scopes:
                op["x-required-scopes"] = list(route.scopes)
                op["description"] = (op.get("description", "") + "\n\n**Scope:** `%s`"
                                     % "` o `".join(route.scopes)).strip()
            op["responses"].update({
                "401": _problem_ref("Clave ausente, inválida, caducada o revocada"),
                "403": _problem_ref("Sin scope, IP no permitida o sin derecho sobre el recurso"),
                "429": _problem_ref("Límite de peticiones superado (ver Retry-After)"),
            })
        op["responses"].update({
            "400": _problem_ref("Petición mal formada"),
            "404": _problem_ref("No existe o no tienes acceso"),
        })
        if not route.public or route.path == "/auth/login":
            op["responses"]["426"] = _problem_ref(
                "Clave de la app (o inicio de sesión) con X-App-Version por debajo de la mínima "
                "o sin ella: app-update-required, con min_version y download_url")
        if route.signed and not route.public:
            op["responses"]["403"] = _problem_ref(
                "Sin scope, IP no permitida, sin derecho sobre el recurso o, con clave de la app: "
                "device-not-registered, signature-required, device-revoked, invalid-signature, "
                "signature-expired")
        if route.method in WRITE_METHODS:
            op["responses"]["409"] = _problem_ref("Conflicto / Idempotency-Key en curso"
                                                  + (" / signature-replayed" if route.signed else ""))
            op["responses"]["422"] = _problem_ref("Datos no válidos o regla de negocio")
        oa_path = API_PREFIX + route.path
        paths.setdefault(oa_path, {})[route.method.lower()] = op
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "CamaronMarket — API externa del marketplace de camarón",
            "version": "1.0.0",
            "description": "API REST/JSON. Identificadores uuid, fechas ISO-8601 UTC, importes "
                           "con moneda, errores application/problem+json (RFC 9457). Guía "
                           "completa en el README del módulo shrimp_api.",
        },
        "servers": [{"url": base_url}],
        "tags": [{"name": t} for t in sorted(tags)],
        "paths": dict(sorted(paths.items())),
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer",
                               "description": "Authorization: Bearer trz_<prefijo>_<secreto>"},
                "apiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            },
            "schemas": {
                "Ref": REF_SCHEMA,
                "Money": MONEY_SCHEMA,
                "PageMeta": {"type": "object", "properties": {
                    "next_cursor": {"type": ["string", "null"]},
                    "count": {"type": "integer"}, "limit": {"type": "integer"},
                    "has_more": {"type": "boolean"}}},
                "Problem": {"type": "object", "required": ["type", "title", "status"], "properties": {
                    "type": {"type": "string", "format": "uri"},
                    "title": {"type": "string"}, "status": {"type": "integer"},
                    "detail": {"type": "string"}, "request_id": {"type": "string"},
                    "errors": {"type": "array", "items": {"type": "object", "properties": {
                        "field": {"type": "string"}, "message": {"type": "string"}}}}}},
            },
        },
    }


@api_route("GET", "/openapi.json", public=True, tags=["documentación"],
           summary="Documento OpenAPI 3.1 de esta API")
def openapi_json(ctx):
    base = request.httprequest.host_url.rstrip("/")
    return build_openapi(base, request.env.registry._init_modules)


DOCS_HTML = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>API externa — documentación</title>
<style>body{margin:0;font-family:system-ui,sans-serif}</style></head>
<body>
<redoc spec-url="%(spec)s" hide-download-button="false" required-props-first="true"></redoc>
<noscript><p>La documentación interactiva necesita JavaScript. El documento OpenAPI está en
<a href="%(spec)s">%(spec)s</a>.</p></noscript>
<script src="/shrimp_api/static/lib/redoc/redoc.standalone.js"></script>
</body></html>"""


@api_route("GET", "/docs", public=True, tags=["documentación"],
           summary="Documentación interactiva (Redoc)")
def docs_page(ctx):
    html = DOCS_HTML % {"spec": API_PREFIX + "/openapi.json"}
    return ApiResponse(raw=html.encode(), content_type="text/html; charset=utf-8")


@api_route("GET", "/", public=True, tags=["documentación"], summary="Índice de la API")
def api_index(ctx):
    return {"name": "Trazul API", "version": "v1",
            "docs": API_PREFIX + "/docs", "openapi": API_PREFIX + "/openapi.json"}
