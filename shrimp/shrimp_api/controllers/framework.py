"""Mini-framework de la API externa.

Todo /api/v1 entra por UNA sola ruta de Odoo (controllers/main.py) y aquí se
resuelve contra un registro propio de rutas. Se hace así por tres motivos:

1. El registro es la fuente del documento OpenAPI: una ruta que no se declara
   aquí no existe ni en el servidor ni en la documentación, así que no pueden
   desincronizarse.
2. Autenticación, scopes, límites de peticiones, idempotencia, CORS y el
   formato de error (RFC 9457) se aplican en un solo sitio y no en cada
   controlador. La API anterior repetía todo eso a mano en cada ruta, y por
   eso cada ruta lo hacía un poco distinto.
3. Werkzeug no deja usar ``/x/{uuid}:accion`` con sus convertidores; con un
   router propio se aceptan las dos formas (``:accion`` y ``/accion``).

Los módulos que amplían la API (p. ej. shrimp_api_sri) declaran rutas con el
mismo decorador; el despachador solo las sirve si su módulo está instalado en
la base que atiende la petición.
"""

import base64
import binascii
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from odoo import fields
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
PROBLEM_TITLES = {
    400: "Petición incorrecta",
    401: "No autenticado",
    403: "Prohibido",
    404: "No encontrado",
    405: "Método no permitido",
    406: "No aceptable",
    409: "Conflicto",
    413: "Cuerpo demasiado grande",
    415: "Tipo de contenido no soportado",
    422: "Datos no válidos",
    429: "Demasiadas peticiones",
    500: "Error interno",
}


class ApiError(Exception):
    """Error que se convierte tal cual en un application/problem+json."""

    def __init__(self, status, slug, detail=None, title=None, errors=None, headers=None):
        super().__init__(detail or slug)
        self.status = status
        self.slug = slug
        self.title = title or PROBLEM_TITLES.get(status, "Error")
        self.detail = detail
        self.errors = errors
        self.headers = headers or []


def not_found(what="El recurso"):
    return ApiError(404, "not-found", "%s no existe o no tienes acceso." % what)


def forbidden(detail):
    return ApiError(403, "forbidden", detail)


def invalid(detail, errors=None):
    return ApiError(422, "validation-error", detail, errors=errors)


# ---------------------------------------------------------------------------
# Respuestas
# ---------------------------------------------------------------------------
class ApiResponse:
    """Lo que puede devolver un handler además de un dict/list (=200)."""

    def __init__(self, data=None, status=200, headers=None, raw=None, content_type=None):
        self.data = data
        self.status = status
        self.headers = headers or []
        self.raw = raw                      # bytes para PDF/XML
        self.content_type = content_type


# ---------------------------------------------------------------------------
# Registro de rutas
# ---------------------------------------------------------------------------
ROUTES = []


def _compile(path):
    out, pos = "", 0
    for m in re.finditer(r"\{(\w+)\}", path):
        out += re.escape(path[pos:m.start()]) + "(?P<%s>[^/:]+)" % m.group(1)
        pos = m.end()
    out += re.escape(path[pos:])
    return re.compile("^" + out + "$")


class Route:
    def __init__(self, method, path, func, *, scopes=(), public=False, summary="",
                 description="", tags=(), params=None, body=None, response=None,
                 status=200, module=None, paginated=False, binary=None):
        self.method = method.upper()
        self.path = path
        self.func = func
        self.scopes = tuple(scopes)
        self.public = public
        self.summary = summary
        self.description = description
        self.tags = tuple(tags)
        self.params = params or {}
        self.body = body
        self.response = response
        self.status = status
        self.module = module
        self.paginated = paginated
        self.binary = binary
        self.patterns = [_compile(path)]
        # Las acciones se publican como "/x/{id}:accion" (estilo Google AIP)
        # y se aceptan también como "/x/{id}/accion" para clientes o proxies
        # que se atraganten con los dos puntos.
        m = re.search(r"\}:([\w-]+)$", path)
        self.action_alias = None
        if m:
            self.action_alias = path[:m.start() + 1] + "/" + m.group(1)
            self.patterns.append(_compile(self.action_alias))

    def match(self, path):
        for pattern in self.patterns:
            m = pattern.match(path)
            if m:
                return m.groupdict()
        return None


def api_route(method, path, *, scope=None, public=False, summary="", description="",
              tags=(), params=None, body=None, response=None, status=200,
              paginated=False, binary=None):
    """Declara una operación de /api/v1.

    ``scope`` puede ser un código o una tupla de códigos (basta con tener uno).
    ``body`` es un dict {campo: F(...)} que sirve a la vez para validar y para
    documentar.
    """
    scopes = (scope,) if isinstance(scope, str) else tuple(scope or ())

    def deco(func):
        module = func.__module__.split(".")[2] if func.__module__.startswith("odoo.addons.") \
            else func.__module__
        route = Route(method, path, func, scopes=scopes, public=public, summary=summary,
                      description=description or (func.__doc__ or "").strip(), tags=tags,
                      params=params, body=body, response=response, status=status,
                      module=module, paginated=paginated, binary=binary)
        # Con --dev=reload un módulo puede importarse dos veces: se reemplaza
        # la ruta en vez de duplicarla.
        for i, existing in enumerate(ROUTES):
            if existing.method == route.method and existing.path == route.path:
                ROUTES[i] = route
                break
        else:
            ROUTES.append(route)
        return func
    return deco


def resolve(path, method, installed_modules):
    """(route, path_params, allowed_methods) para un path relativo a /api/v1."""
    allowed = set()
    for route in ROUTES:
        if route.module not in installed_modules:
            continue
        params = route.match(path)
        if params is None:
            continue
        allowed.add(route.method)
        if route.method == method or (method == "HEAD" and route.method == "GET"):
            return route, params, allowed
    return None, None, allowed


# ---------------------------------------------------------------------------
# Conversión de valores
# ---------------------------------------------------------------------------
def iso_dt(value):
    """Datetime de Odoo (UTC ingenuo) -> ISO-8601 con Z."""
    if not value:
        return None
    if isinstance(value, str):
        value = fields.Datetime.to_datetime(value)
    return value.replace(tzinfo=timezone.utc, microsecond=0).isoformat().replace("+00:00", "Z")


def iso_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        return value[:10]
    return value.isoformat()


def money(amount, currency):
    if amount is None or amount is False:
        return None
    currency = currency.sudo() if currency else currency
    digits = currency.decimal_places if currency else 2
    return {"amount": round(float(amount or 0.0), digits),
            "currency": currency.name if currency else None}


def ref(record, name_field=None):
    """Relación -> {"id": uuid, "name": nombre}. Lee solo esos dos datos (sudo),
    igual que Odoo muestra el nombre de un many2one aunque no se pueda abrir."""
    if not record:
        return None
    rec = record.sudo()[:1]
    uid = rec.uuid_ref if "uuid_ref" in rec._fields else None
    if name_field:
        name = rec[name_field]
    elif rec._name == "res.partner":
        name = rec.name
    else:
        name = rec.display_name
    return {"id": uid, "name": name or None}


def selection_label(record, field_name):
    value = record[field_name]
    if not value:
        return None
    desc = dict(record._fields[field_name]._description_selection(record.env))
    return desc.get(value, value)


def parse_datetime(value, field="valor"):
    """ISO-8601 (con Z u offset, o sin zona = UTC) -> datetime UTC ingenuo."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ApiError(422, "validation-error", "%s: se esperaba una fecha-hora ISO-8601." % field)
    txt = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        try:
            d = date.fromisoformat(txt)
            dt = datetime(d.year, d.month, d.day)
        except ValueError:
            raise ApiError(422, "validation-error",
                           "%s: «%s» no es una fecha-hora ISO-8601." % (field, value))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(microsecond=0)


def parse_date(value, field="valor"):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ApiError(422, "validation-error", "%s: se esperaba una fecha AAAA-MM-DD." % field)
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise ApiError(422, "validation-error",
                       "%s: «%s» no es una fecha AAAA-MM-DD." % (field, value))


# ---------------------------------------------------------------------------
# Validación del cuerpo
# ---------------------------------------------------------------------------
class F:
    """Especificación de un campo de entrada.

    type: string | text | number | integer | boolean | date | datetime |
          enum | ref | refs | array | object
    """

    def __init__(self, type, required=False, odoo=None, enum=None, max_length=None,
                 model=None, own=False, domain=None, description="", minimum=None,
                 maximum=None, items=None, nullable=True, example=None):
        self.type = type
        self.required = required
        self.odoo = odoo
        self.enum = enum
        self.max_length = max_length or (255 if type == "string" else 20000 if type == "text" else None)
        self.model = model
        self.own = own
        self.domain = domain or []
        self.description = description
        self.minimum = minimum
        self.maximum = maximum
        self.items = items
        self.nullable = nullable
        self.example = example

    # --- OpenAPI -----------------------------------------------------------
    def schema(self):
        t = self.type
        if t in ("string", "text"):
            s = {"type": "string"}
            if self.max_length:
                s["maxLength"] = self.max_length
        elif t == "number":
            s = {"type": "number"}
        elif t == "integer":
            s = {"type": "integer"}
        elif t == "boolean":
            s = {"type": "boolean"}
        elif t == "date":
            s = {"type": "string", "format": "date"}
        elif t == "datetime":
            s = {"type": "string", "format": "date-time"}
        elif t == "enum":
            s = {"type": "string", "enum": list(self.enum)}
        elif t == "ref":
            s = {"type": "string", "format": "uuid",
                 "description": "uuid de %s" % self.model}
        elif t == "refs":
            s = {"type": "array", "items": {"type": "string", "format": "uuid"}}
        elif t == "array":
            s = {"type": "array", "items": object_schema(self.items or {})}
        else:
            s = {"type": "object"}
        if self.minimum is not None:
            s["minimum"] = self.minimum
        if self.maximum is not None:
            s["maximum"] = self.maximum
        if self.description:
            s["description"] = (s.get("description", "") + " " + self.description).strip()
        if self.example is not None:
            s["examples"] = [self.example]
        if self.nullable and not self.required and t not in ("refs", "array"):
            s = {"anyOf": [s, {"type": "null"}]}
        return s


def object_schema(spec):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {name: f.schema() for name, f in spec.items()},
        "required": [name for name, f in spec.items() if f.required],
    }


def _coerce(ctx, name, f, value, errors):
    if value is None:
        if f.required or not f.nullable:
            errors.append({"field": name, "message": "No puede ser nulo."})
            return None
        return False if f.type not in ("refs", "array") else []
    t = f.type
    try:
        if t in ("string", "text"):
            if not isinstance(value, str):
                raise ValueError("se esperaba texto")
            value = value.strip()
            if f.max_length and len(value) > f.max_length:
                raise ValueError("supera %s caracteres" % f.max_length)
            return value or False
        if t == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("se esperaba un número")
            value = float(value)
        elif t == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("se esperaba un entero")
        elif t == "boolean":
            if not isinstance(value, bool):
                raise ValueError("se esperaba true/false")
            return value
        elif t == "date":
            return parse_date(value, name)
        elif t == "datetime":
            return parse_datetime(value, name)
        elif t == "enum":
            if value not in f.enum:
                raise ValueError("valores permitidos: %s" % ", ".join(f.enum))
            return value
        elif t == "ref":
            if not isinstance(value, str):
                raise ValueError("se esperaba un uuid (texto)")
            rec = ctx.resolve(f.model, value, own=f.own, domain=f.domain)
            if not rec:
                raise ValueError("no existe o no tienes acceso")
            return rec.id
        elif t == "refs":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError("se esperaba una lista de uuid")
            ids = []
            for v in value:
                rec = ctx.resolve(f.model, v, own=f.own, domain=f.domain)
                if not rec:
                    raise ValueError("«%s» no existe o no es válido aquí" % v)
                ids.append(rec.id)
            return ids
        elif t == "array":
            if not isinstance(value, list):
                raise ValueError("se esperaba una lista")
            out = []
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    raise ValueError("el elemento %s no es un objeto" % i)
                sub_errors = []
                out.append(clean(ctx, item, f.items or {}, errors=sub_errors, prefix="%s[%s]." % (name, i)))
                errors.extend(sub_errors)
            return out
        elif t == "object":
            if not isinstance(value, dict):
                raise ValueError("se esperaba un objeto")
            return value
        if f.minimum is not None and value < f.minimum:
            raise ValueError("debe ser ≥ %s" % f.minimum)
        if f.maximum is not None and value > f.maximum:
            raise ValueError("debe ser ≤ %s" % f.maximum)
        return value
    except ApiError as e:
        errors.append({"field": name, "message": e.detail})
    except ValueError as e:
        errors.append({"field": name, "message": str(e)})
    return None


def clean(ctx, body, spec, partial=False, errors=None, prefix=""):
    """Valida ``body`` contra ``spec`` y devuelve {nombre_odoo: valor}.

    Campos desconocidos -> 422 (no se ignoran en silencio: un error de nombre
    del integrador tiene que verse, no perderse). Si ``errors`` viene, se
    acumulan ahí en vez de lanzar.
    """
    own_errors = errors is None
    errors = [] if errors is None else errors
    unknown = [k for k in body if k not in spec]
    for k in unknown:
        errors.append({"field": prefix + k, "message": "Campo desconocido o no editable."})
    vals = {}
    for name, f in spec.items():
        if name not in body:
            if f.required and not partial:
                errors.append({"field": prefix + name, "message": "Es obligatorio."})
            continue
        value = _coerce(ctx, prefix + name, f, body[name], errors)
        vals[f.odoo or name] = value
    if own_errors and errors:
        raise invalid("El cuerpo de la petición no es válido.", errors=errors)
    return vals


# ---------------------------------------------------------------------------
# Paginación por cursor opaco sobre (write_date, id)
# ---------------------------------------------------------------------------
def encode_cursor(write_date, rec_id):
    payload = json.dumps({"w": write_date.strftime("%Y-%m-%dT%H:%M:%S"), "i": rec_id},
                         separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor):
    try:
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad).decode())
        return datetime.strptime(data["w"], "%Y-%m-%dT%H:%M:%S"), int(data["i"])
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
        raise ApiError(400, "invalid-cursor", "El cursor no es válido. Úsalo tal como lo "
                       "devolvió la API en meta.next_cursor.")


def paginate(ctx, model, domain, serializer, sudo=False):
    """Lista paginada con las reglas de acceso del dueño de la clave.

    Orden estable por (segundo de write_date, id): Odoo compara las fechas por
    segundos completos, así que el cursor también. Sin esto dos registros
    escritos en el mismo segundo podían saltarse o repetirse entre páginas.
    """
    limit = ctx.int_param("limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    domain = list(domain)
    since = ctx.params.get("updated_since")
    if since:
        domain.append(("write_date", ">=", parse_datetime(since, "updated_since")))
    cursor = ctx.params.get("cursor")
    if cursor:
        w, i = decode_cursor(cursor)
        nxt = w + timedelta(seconds=1)
        domain += ["|", ("write_date", ">=", nxt),
                   "&", "&", ("write_date", ">=", w), ("write_date", "<", nxt), ("id", ">", i)]
    # sudo solo en los listados públicos, que llevan un dominio fijo.
    Model = ctx.env[model].sudo() if sudo else ctx.env[model]
    query = Model._search(domain, limit=limit + 1)
    alias = query.table
    query.order = SQL("date_trunc('second', %s) ASC, %s ASC",
                      SQL.identifier(alias, "write_date"), SQL.identifier(alias, "id"))
    ids = list(query.get_result_ids())
    has_more = len(ids) > limit
    ids = ids[:limit]
    records = Model.browse(ids)
    data = [serializer(rec) for rec in records]
    next_cursor = None
    if has_more and records:
        last = records[-1].sudo()
        next_cursor = encode_cursor(last.write_date, last.id)
    return {"data": data, "meta": {"next_cursor": next_cursor, "count": len(data),
                                   "limit": limit, "has_more": has_more}}


# ---------------------------------------------------------------------------
# Esquemas comunes para OpenAPI
# ---------------------------------------------------------------------------
REF_SCHEMA = {"anyOf": [{"type": "object", "properties": {
    "id": {"type": ["string", "null"], "format": "uuid"},
    "name": {"type": ["string", "null"]}}, "required": ["id", "name"]}, {"type": "null"}]}
MONEY_SCHEMA = {"anyOf": [{"type": "object", "properties": {
    "amount": {"type": "number"}, "currency": {"type": ["string", "null"]}}}, {"type": "null"}]}


def obj(**props):
    """Atajo para documentar respuestas: obj(name="string", price=MONEY_SCHEMA...)."""
    out = {}
    for k, v in props.items():
        if isinstance(v, str):
            if v == "uuid":
                out[k] = {"type": "string", "format": "uuid"}
            elif v == "datetime":
                out[k] = {"type": ["string", "null"], "format": "date-time"}
            elif v == "date":
                out[k] = {"type": ["string", "null"], "format": "date"}
            elif v == "ref":
                out[k] = {"$ref": "#/components/schemas/Ref"}
            elif v == "money":
                out[k] = {"$ref": "#/components/schemas/Money"}
            else:
                out[k] = {"type": [v, "null"]}
        else:
            out[k] = v
    return {"type": "object", "properties": out}


def role_domain(*roles):
    """Dominio de res.partner: cuentas que pueden actuar como alguno de
    `roles` (perfil activo o perfil aprobado). Versión estática de
    res.partner._shrimp_role_domain para las especificaciones de campos."""
    roles = list(roles)
    return ["|", ("shrimp_user_type", "in", roles),
            ("shrimp_role_ids", "any", [("role", "in", roles), ("state", "=", "approved")])]


# Perfil con el que actúa la petición ("Actuar como"). No se llama `role`
# porque varios listados ya usan ?role= con otro sentido (buyer/seller,
# issuer/recipient...).
ACTING_ROLE_HEADER = "X-Shrimp-Role"
ACTING_ROLE_PARAM = "acting_role"


# ---------------------------------------------------------------------------
# Contexto de una petición
# ---------------------------------------------------------------------------
class ApiCtx:
    """Lo que recibe cada handler.

    ``env`` es el entorno del USUARIO dueño de la clave, sin sudo: las
    búsquedas que hace un handler con ``ctx.env`` pasan por sus ACL y sus
    ir.rule. Para escribir, el handler llama al método de negocio (en sudo y
    con el ``actor`` explícito, igual que el portal) solo después de haber
    encontrado el registro con ``ctx.env``.
    """

    def __init__(self, env, key=None, params=None, body=None, method="GET", path="/",
                 request_id=None, remote_addr=None, route=None):
        self.env = env
        self.key = key
        self.user = env.user
        self.partner = env.user.partner_id.sudo() if key else env["res.partner"].sudo()
        self.params = params or {}
        self.body = body if body is not None else {}
        self.method = method
        self.path = path
        self.request_id = request_id
        self.remote_addr = remote_addr
        self.route = route
        # Perfil pedido para esta petición: cabecera X-Shrimp-Role, parámetro
        # ?acting_role= o clave "acting_role" del cuerpo (se retira del cuerpo
        # para que la validación estricta de campos no la rechace).
        self._acting_role_raw = None
        if isinstance(self.body, dict) and ACTING_ROLE_PARAM in self.body:
            self._acting_role_raw = self.body.pop(ACTING_ROLE_PARAM)
        if self.params.get(ACTING_ROLE_PARAM):
            self._acting_role_raw = self.params.get(ACTING_ROLE_PARAM)
        try:
            from odoo.http import request as _req
            cabecera = _req.httprequest.headers.get(ACTING_ROLE_HEADER) if _req else None
        except Exception:  # noqa: BLE001 - sin petición HTTP (tests unitarios)
            cabecera = None
        if cabecera:
            self._acting_role_raw = cabecera
        self._role = None

    @property
    def role(self):
        """Perfil con el que actúa esta petición: el pedido (si la cuenta lo
        tiene aprobado) o, por defecto, el perfil activo de la cuenta."""
        if self._role is None:
            activo = self.partner._shrimp_effective_type() if self.partner else False
            pedido = (str(self._acting_role_raw).strip() if self._acting_role_raw else "")
            if pedido and pedido != activo:
                if not self.partner or not self.partner._shrimp_has_role(pedido):
                    raise forbidden("Tu cuenta no tiene aprobado el perfil «%s»." % pedido)
                self._role = pedido
            else:
                self._role = activo or False
        return self._role

    # --- parámetros --------------------------------------------------------
    def int_param(self, name, default=None, lo=None, hi=None):
        raw = self.params.get(name)
        if raw in (None, ""):
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid-parameter", "«%s» debe ser un entero." % name)
        if lo is not None and value < lo:
            raise ApiError(400, "invalid-parameter", "«%s» debe ser ≥ %s." % (name, lo))
        if hi is not None and value > hi:
            raise ApiError(400, "invalid-parameter", "«%s» debe ser ≤ %s." % (name, hi))
        return value

    def float_param(self, name, default=None, lo=None):
        raw = self.params.get(name)
        if raw in (None, ""):
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid-parameter", "«%s» debe ser un número." % name)
        if lo is not None and value < lo:
            raise ApiError(400, "invalid-parameter", "«%s» debe ser ≥ %s." % (name, lo))
        return value

    def bool_param(self, name, default=False):
        raw = self.params.get(name)
        if raw in (None, ""):
            return default
        if str(raw).lower() in ("1", "true", "yes", "si", "sí"):
            return True
        if str(raw).lower() in ("0", "false", "no"):
            return False
        raise ApiError(400, "invalid-parameter", "«%s» debe ser true o false." % name)

    def enum_param(self, name, allowed, default=None):
        raw = self.params.get(name)
        if raw in (None, ""):
            return default
        if raw not in allowed:
            raise ApiError(400, "invalid-parameter", "«%s» admite: %s." % (name, ", ".join(allowed)))
        return raw

    # --- registros ---------------------------------------------------------
    def resolve(self, model, uuid_ref, own=False, domain=None, archived=False):
        """uuid -> registro. ``own`` busca con las reglas del usuario; si no,
        es un catálogo y se busca en sudo con el dominio dado."""
        if not uuid_ref or not isinstance(uuid_ref, str) or len(uuid_ref) > 64:
            return self.env[model].browse()
        Model = self.env[model] if own else self.env[model].sudo()
        if archived:
            Model = Model.with_context(active_test=False)
        return Model.search([("uuid_ref", "=", uuid_ref)] + list(domain or []), limit=1)

    def get_own(self, model, uuid_ref, what="El recurso", domain=None, archived=False):
        rec = self.resolve(model, uuid_ref, own=True, domain=domain, archived=archived)
        if not rec:
            raise not_found(what)
        return rec

    def require_type(self, *types, what=None):
        """El perfil con el que actúa la petición (ctx.role) tiene que ser
        uno de `types`."""
        if self.role not in types:
            raise forbidden(what or "Esta operación es para socios de tipo: %s." % ", ".join(types))

    def require_capability(self, capability, what=None):
        """Como require_type, pero con la matriz de capacidades de la
        plataforma (res.partner._shrimp_can): la misma regla que el portal,
        aplicada al perfil con el que actúa la petición."""
        if not self.partner.sudo()._shrimp_can(capability, role=self.role):
            raise forbidden(what or "Tu tipo de socio no puede hacer esta operación.")

    def clean(self, spec, partial=False):
        return clean(self, self.body, spec, partial=partial)
