"""Claves de la API externa, sobre el modelo que ya existía en el marketplace.

Se extiende ``shrimp.api.key`` en vez de crear otro modelo para conservar las
claves que ya hubiera emitidas: el post_init_hook las convierte a hash y
borra el texto plano (ver hooks.py), así que las integraciones existentes
siguen funcionando con la misma clave.

POR QUÉ NO res.users.apikeys
----------------------------
Se valoró reutilizar las claves nativas de Odoo con un scope propio. Se
descartó por tres motivos concretos:

* una clave nativa tiene UN scope de texto, y aquí hacen falta varios scopes
  granulares, lista de IPs, orígenes CORS, caducidad por clave y revocación
  con traza;
* una clave nativa "global" (scope NULL) valida para cualquier scope, así que
  la clave RPC de un usuario abriría también esta API sin que nadie la
  hubiera pedido para eso;
* el formato ``trz_<prefijo>_<secreto>`` permite identificar la clave en logs
  y en escáneres de secretos sin exponerla.

Lo que sí se reutiliza es su maquinaria criptográfica: el mismo
``KEY_CRYPT_CONTEXT`` (PBKDF2-SHA512 de passlib), con comparación en tiempo
constante.
"""

import hashlib
import ipaddress
import logging
import re
import secrets
import threading
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.addons.base.models.res_users import KEY_CRYPT_CONTEXT

from .api_scope import SCOPE_CODES

_logger = logging.getLogger(__name__)

KEY_RE = re.compile(r"^trz_([0-9a-f]{8})_([A-Za-z0-9_\-]{20,})$")
MAX_PORTAL_DAYS = 365
# Modo supervisor de la app (contrato, Anexo B): la clave del supervisor y
# las de «actuar como» duran como mucho esto.
SUPERVISOR_KEY_HOURS = 8
SUPERVISOR_GROUP = "shrimp_api.group_shrimp_app_supervisor"

# Verificar PBKDF2 en cada petición cuesta unos milisegundos. Se recuerda, por
# proceso, qué clave en claro (por su sha256) ya se verificó contra QUÉ hash.
# Si el hash cambia (rotación) o la clave se revoca, la comprobación contra la
# base de cada petición lo detecta: la caché nunca decide por sí sola.
_VERIFIED = {}
_VERIFIED_LOCK = threading.Lock()
_VERIFIED_MAX = 2048


def _remember(digest, value):
    with _VERIFIED_LOCK:
        if len(_VERIFIED) >= _VERIFIED_MAX:
            _VERIFIED.clear()
        _VERIFIED[digest] = value


_DUMMY_HASH = []


def _dummy_verify():
    """Mismo coste que una verificación real (passlib de este servidor no trae
    CryptContext.dummy_verify)."""
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(KEY_CRYPT_CONTEXT.hash("trz_00000000_dummy"))
    KEY_CRYPT_CONTEXT.verify("trz_00000000_wrong", _DUMMY_HASH[0])


def _forget_key_ids(ids):
    with _VERIFIED_LOCK:
        for digest in [d for d, v in _VERIFIED.items() if v[0] in ids]:
            _VERIFIED.pop(digest, None)


class ShrimpApiKey(models.Model):
    _inherit = "shrimp.api.key"

    # La columna de texto plano se conserva vacía: borrarla del esquema
    # obligaría a tocar shrimp_marketplace. Ya no es obligatoria ni tiene
    # valor por defecto, y solo la ve el administrador (para poder comprobar
    # que está vacía).
    key = fields.Char(required=False, default=False, groups="base.group_system",
                      help="Columna heredada. Tras instalar shrimp_api queda vacía: "
                           "las claves se guardan solo como hash.")

    key_prefix = fields.Char(
        string="Prefijo", readonly=True, copy=False, index=True,
        help="Identifica la clave sin revelarla (trz_<prefijo>_…).")
    key_hash = fields.Char(readonly=True, copy=False, groups="base.group_system")
    is_legacy = fields.Boolean(
        string="Clave heredada", readonly=True,
        help="Emitida por la API anterior; se migró a hash al instalar.")
    scope_ids = fields.Many2many(
        "shrimp.api.scope", "shrimp_api_key_scope_rel", "key_id", "scope_id",
        string="Scopes")
    expires_at = fields.Datetime(string="Caduca el")
    revoked_at = fields.Datetime(string="Revocada el", readonly=True, copy=False)
    revoked_by_id = fields.Many2one("res.users", string="Revocada por", readonly=True, copy=False)
    ip_allowlist = fields.Text(
        string="IPs permitidas",
        help="Una IP o red CIDR por línea (o separadas por comas). Vacío = cualquiera.")
    allowed_origins = fields.Text(
        string="Orígenes CORS permitidos",
        help="Solo si se llama desde un navegador. Un origen por línea, p. ej. "
             "https://erp.miempresa.com. Vacío = sin CORS (servidor a servidor).")
    rate_read_per_min = fields.Integer(
        string="Lecturas/min", help="0 = valor por defecto del sistema.")
    rate_write_per_min = fields.Integer(
        string="Escrituras/min", help="0 = valor por defecto del sistema.")
    last_ip = fields.Char(string="Última IP", readonly=True)
    # La emite POST /auth/login. Con una clave de app, las rutas sensibles
    # exigen un dispositivo registrado y la firma de la operación (ver
    # models/api_device.py). Las claves creadas a mano en el portal no.
    is_app_key = fields.Boolean(
        string="Clave de la app", readonly=True, copy=False, index=True,
        help="Emitida por el inicio de sesión de la app móvil: las operaciones sensibles "
             "exigen un dispositivo registrado y una firma ES256.")
    device_ids = fields.One2many("shrimp.api.device", "api_key_id", string="Dispositivos",
                                 readonly=True, context={"active_test": False})
    # Modo supervisor (docs/app/CONTRATO-FIRMA-DISPOSITIVO.md, Anexo B).
    # Clave de supervisor: dueño = un usuario INTERNO con el grupo «Supervisor
    # de la app», sin scopes; solo sirve para el directorio de socios y para
    # «actuar como» (y para registrar su dispositivo, /me y cerrar sesión).
    is_supervisor_key = fields.Boolean(
        string="Clave de supervisor", readonly=True, copy=False, index=True,
        help="Emitida por el inicio de sesión de la app a un usuario interno con el grupo "
             "«Supervisor de la app». No lee datos de ningún socio: solo emite claves de "
             "solo lectura con «actuar como».")
    # Clave de «actuar como»: dueño = el usuario de portal del socio, solo
    # scopes *:read. Se revoca en cascada con la clave de supervisor que la
    # emitió.
    supervisor_user_id = fields.Many2one(
        "res.users", string="Emitida por el supervisor", readonly=True, copy=False, index=True,
        ondelete="cascade",
        help="Clave de solo lectura emitida con «actuar como» desde la app del supervisor.")
    supervisor_key_id = fields.Many2one(
        "shrimp.api.key", string="Clave de supervisor que la emitió", readonly=True, copy=False,
        index=True, ondelete="cascade")
    act_as_key_ids = fields.One2many(
        "shrimp.api.key", "supervisor_key_id", string="Claves de «actuar como» emitidas",
        readonly=True, context={"active_test": False})
    status = fields.Selection(
        [("active", "Activa"), ("expired", "Caducada"), ("revoked", "Revocada"),
         ("inactive", "Inactiva")],
        string="Situación", compute="_compute_status")
    display_key = fields.Char(string="Clave (enmascarada)", compute="_compute_status")

    _key_prefix_unique = models.Constraint(
        "unique(key_prefix)", "El prefijo de la clave debe ser único.")

    @api.depends("active", "revoked_at", "expires_at", "key_prefix")
    def _compute_status(self):
        now = fields.Datetime.now()
        for rec in self:
            if rec.revoked_at:
                rec.status = "revoked"
            elif not rec.active:
                rec.status = "inactive"
            elif rec.expires_at and rec.expires_at < now:
                rec.status = "expired"
            else:
                rec.status = "active"
            if rec.key_prefix and rec.key_prefix.startswith("legacy:"):
                rec.display_key = "%s… (heredada)" % rec.key_prefix[7:]
            else:
                rec.display_key = ("trz_%s_••••••••" % rec.key_prefix) if rec.key_prefix else False

    # ------------------------------------------------------------------
    # La API antigua: se desactivan sus puertas de texto plano
    # ------------------------------------------------------------------
    @api.model
    def _generate_key(self):
        return False

    @api.model
    def _authenticate(self, raw_key):
        # El controlador viejo ya no se carga, pero si alguien lo reactivara
        # no podría volver a comparar claves en claro.
        rec, _reason = self._api_authenticate(raw_key)
        return rec or False

    def action_regenerate_key(self):
        raise UserError(_(
            "Las claves ya no se regeneran en sitio: revoca esta y crea una "
            "nueva desde «Integraciones (API)». El secreto solo se muestra una vez."))

    def _touch(self):
        # Antes escribía en cada llamada (también en los GET) y bloqueaba la
        # fila de la clave en todas las peticiones concurrentes.
        return True

    @api.constrains("scope_ids", "user_id", "is_supervisor_key")
    def _check_api_owner(self):
        for rec in self:
            if rec.is_supervisor_key:
                # La única clave de un usuario interno: sin ningún scope de datos.
                if rec.scope_ids:
                    raise ValidationError(_("Una clave de supervisor no lleva scopes."))
                continue
            if rec.key_prefix and rec.user_id and not rec.user_id.share and rec.active \
                    and not rec.revoked_at:
                raise ValidationError(_(
                    "Las claves de la API externa son para usuarios del portal "
                    "(socios). Las integraciones internas usan la API JSON-2 de Odoo."))

    @api.constrains("supervisor_key_id", "scope_ids")
    def _check_act_as_read_only(self):
        # Red de seguridad: una clave de «actuar como» nunca escribe.
        for rec in self:
            if rec.supervisor_key_id and any(
                    not c.endswith(":read") for c in rec.sudo().scope_ids.mapped("code")):
                raise ValidationError(_("Una clave de «actuar como» solo puede tener scopes de lectura."))

    # ------------------------------------------------------------------
    # Emisión
    # ------------------------------------------------------------------
    @api.model
    def api_create_key(self, user, name, scope_codes, expires_at=None,
                       ip_allowlist=False, allowed_origins=False):
        """Crea una clave y devuelve (registro, clave_en_claro).

        La clave en claro NO se guarda en ningún sitio: quien la pide la ve
        una vez y, si la pierde, la revoca y pide otra.
        """
        user = user.sudo()
        if not user.share:
            raise ValidationError(_(
                "Solo los socios del portal pueden tener claves de la API externa."))
        name = (name or "").strip()
        if not name:
            raise ValidationError(_("Ponle un nombre a la clave (p. ej. «ERP planta»)."))
        codes = sorted(set(scope_codes or []))
        unknown = [c for c in codes if c not in SCOPE_CODES]
        if unknown:
            raise ValidationError(_("Scopes desconocidos: %s") % ", ".join(unknown))
        if not codes:
            raise ValidationError(_("Elige al menos un scope."))
        now = fields.Datetime.now()
        if not expires_at:
            expires_at = now + timedelta(days=90)
        if expires_at <= now:
            raise ValidationError(_("La caducidad tiene que estar en el futuro."))
        if expires_at > now + timedelta(days=MAX_PORTAL_DAYS + 1):
            raise ValidationError(_("Una clave dura como máximo %s días.") % MAX_PORTAL_DAYS)
        self._check_ip_list(ip_allowlist)
        self._check_origins(allowed_origins)

        prefix, raw, hashed = self._api_new_secret()
        scopes = self.env["shrimp.api.scope"].sudo().search([("code", "in", codes)])
        rec = self.sudo().create({
            "name": name,
            "user_id": user.id,
            "scope": "read",
            "key": False,
            "key_prefix": prefix,
            "key_hash": hashed,
            "scope_ids": [(6, 0, scopes.ids)],
            "expires_at": expires_at,
            "ip_allowlist": ip_allowlist or False,
            "allowed_origins": allowed_origins or False,
        })
        _logger.info("API key trz_%s creada para %s (scopes: %s)", prefix, user.login, ",".join(codes))
        return rec, raw

    @api.model
    def _api_new_secret(self):
        """(prefijo, clave_en_claro, hash) de una clave nueva."""
        for _attempt in range(5):
            prefix = secrets.token_hex(4)
            if not self.sudo().with_context(active_test=False).search_count(
                    [("key_prefix", "=", prefix)]):
                break
        raw = "trz_%s_%s" % (prefix, secrets.token_urlsafe(32))
        return prefix, raw, KEY_CRYPT_CONTEXT.hash(raw)

    # ------------------------------------------------------------------
    # Modo supervisor (contrato, Anexo B)
    # ------------------------------------------------------------------
    @api.model
    def _api_is_app_supervisor(self, user):
        """Usuario interno activo con el grupo «Supervisor de la app»."""
        user = user.sudo()
        return bool(user) and user.active and not user.share and user._has_group(SUPERVISOR_GROUP)

    @api.model
    def api_create_supervisor_key(self, user, name, expires_at=None):
        """Clave de supervisor (B.1): del usuario interno, de la app, sin
        scopes y de SUPERVISOR_KEY_HOURS horas como mucho. Devuelve
        (registro, clave_en_claro)."""
        user = user.sudo()
        if not self._api_is_app_supervisor(user):
            raise ValidationError(_("Solo un usuario interno con el grupo «Supervisor de la app» "
                                    "puede tener una clave de supervisor."))
        now = fields.Datetime.now()
        tope = now + timedelta(hours=SUPERVISOR_KEY_HOURS)
        expires_at = min(expires_at or tope, tope)
        if expires_at <= now:
            raise ValidationError(_("La caducidad tiene que estar en el futuro."))
        prefix, raw, hashed = self._api_new_secret()
        rec = self.sudo().create({
            "name": (name or "").strip() or "Supervisor",
            "user_id": user.id,
            "scope": "read",
            "key": False,
            "key_prefix": prefix,
            "key_hash": hashed,
            "scope_ids": [(5, 0, 0)],
            "expires_at": expires_at,
            "is_app_key": True,
            "is_supervisor_key": True,
        })
        _logger.info("API key de supervisor trz_%s creada para %s", prefix, user.login)
        return rec, raw

    def api_create_act_as_key(self, portal_user):
        """Clave de «actuar como» (B.3) emitida con ESTA clave de supervisor:
        del usuario de portal del socio, solo scopes *:read, de la app y con
        la caducidad de la clave de supervisor como tope (8 h como mucho).
        Devuelve (registro, clave_en_claro)."""
        self.ensure_one()
        sup_key = self.sudo()
        if not sup_key.is_supervisor_key or sup_key.status != "active":
            raise ValidationError(_("«Actuar como» solo se hace con una clave de supervisor vigente."))
        if not self._api_is_app_supervisor(sup_key.user_id):
            raise ValidationError(_("El dueño de la clave ya no es supervisor de la app."))
        portal_user = portal_user.sudo()
        if not portal_user.active or not portal_user.share:
            raise ValidationError(_("«Actuar como» solo sobre usuarios de portal activos."))
        now = fields.Datetime.now()
        expires_at = now + timedelta(hours=SUPERVISOR_KEY_HOURS)
        if sup_key.expires_at:
            expires_at = min(expires_at, sup_key.expires_at)
        # Nunca escritura: solo los scopes de lectura, aunque mañana se añada
        # uno nuevo de escritura.
        read_codes = [c for c in SCOPE_CODES if c.endswith(":read")]
        rec, raw = self.api_create_key(
            portal_user, "Supervisor – %s" % sup_key.user_id.name, read_codes, expires_at=expires_at)
        rec.sudo().write({"is_app_key": True, "supervisor_user_id": sup_key.user_id.id,
                          "supervisor_key_id": sup_key.id})
        _logger.info("API key trz_%s (actuar como %s, solo lectura) emitida por el supervisor %s "
                     "con trz_%s", rec.key_prefix, portal_user.login, sup_key.user_id.login,
                     sup_key.key_prefix)
        return rec, raw

    @api.model
    def _check_ip_list(self, text):
        for item in self._split(text):
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError:
                raise ValidationError(_("«%s» no es una IP ni una red CIDR válida.") % item)

    @api.model
    def _check_origins(self, text):
        for item in self._split(text):
            if not re.match(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$", item):
                raise ValidationError(_(
                    "«%s» no es un origen válido (esquema://host[:puerto], sin ruta).") % item)

    @api.model
    def _split(self, text):
        return [x.strip() for x in re.split(r"[\s,]+", text or "") if x.strip()]

    @api.constrains("ip_allowlist", "allowed_origins")
    def _check_lists(self):
        for rec in self:
            rec._check_ip_list(rec.ip_allowlist)
            rec._check_origins(rec.allowed_origins)

    # ------------------------------------------------------------------
    # Autenticación
    # ------------------------------------------------------------------
    @api.model
    def _api_authenticate(self, raw):
        """Devuelve (clave_sudo, None) o (None, motivo).

        motivo ∈ {missing, invalid, revoked, expired, user}.
        """
        raw = (raw or "").strip()
        if not raw:
            return None, "missing"
        if len(raw) > 256:
            return None, "invalid"
        m = KEY_RE.match(raw)
        prefix = m.group(1) if m else "legacy:" + raw[:8]
        rec = self.sudo().with_context(active_test=False).search(
            [("key_prefix", "=", prefix)], limit=1)
        if not rec or not rec.key_hash:
            # Mismo coste que una verificación real: no se distingue por tiempo
            # un prefijo inexistente de un secreto equivocado.
            _dummy_verify()
            return None, "invalid"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        if _VERIFIED.get(digest) != (rec.id, rec.key_hash):
            try:
                ok = KEY_CRYPT_CONTEXT.verify(raw, rec.key_hash)
            except (ValueError, TypeError):
                ok = False
            if not ok:
                return None, "invalid"
            _remember(digest, (rec.id, rec.key_hash))
        if rec.revoked_at or not rec.active:
            return None, "revoked"
        if rec.expires_at and rec.expires_at < fields.Datetime.now():
            return None, "expired"
        user = rec.user_id
        if rec.is_supervisor_key:
            # Interno, activo y todavía supervisor (quitarle el grupo corta
            # su sesión de la app en el acto).
            if not self._api_is_app_supervisor(user):
                return None, "user"
            return rec, None
        if not user or not user.active or not user.share:
            return None, "user"
        if rec.supervisor_key_id:
            # «Actuar como»: vale mientras valga la clave de supervisor que la
            # emitió (la revocación ya cae en cascada; esto cubre lo demás).
            sup = rec.supervisor_key_id
            if sup.revoked_at or not sup.active:
                return None, "revoked"
            if sup.expires_at and sup.expires_at < fields.Datetime.now():
                return None, "expired"
            if not self._api_is_app_supervisor(rec.supervisor_user_id):
                return None, "user"
        return rec, None

    def _api_scope_codes(self):
        self.ensure_one()
        return set(self.sudo().scope_ids.mapped("code"))

    def _api_ip_allowed(self, ip):
        self.ensure_one()
        nets = self._split(self.sudo().ip_allowlist)
        if not nets:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        for net in nets:
            try:
                if addr in ipaddress.ip_network(net, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _api_origin_allowed(self, origin):
        self.ensure_one()
        return bool(origin) and origin in self._split(self.sudo().allowed_origins)

    def _api_limits(self):
        self.ensure_one()
        param = self.env["ir.config_parameter"].sudo()

        def _num(k, d):
            try:
                return int(param.get_param(k) or d)
            except (TypeError, ValueError):
                return d
        read = self.rate_read_per_min or _num("shrimp_api.rate_read_per_min", 600)
        write = self.rate_write_per_min or _num("shrimp_api.rate_write_per_min", 60)
        return read, write

    # ------------------------------------------------------------------
    # Revocación
    # ------------------------------------------------------------------
    def action_revoke(self):
        for rec in self.sudo():
            if rec.revoked_at:
                continue
            rec.write({"revoked_at": fields.Datetime.now(),
                       "revoked_by_id": self.env.user.id, "active": False})
        _forget_key_ids(set(self.ids))
        # Sin clave, su dispositivo ya no sirve: se marca revocado también
        # para que el socio lo vea así en el portal.
        devices = self.env["shrimp.api.device"].sudo().search(
            [("api_key_id", "in", self.ids), ("active", "=", True)])
        if devices:
            devices._api_revoke(revoke_key=False)
        # Revocar una clave de supervisor revoca las de «actuar como» que emitió.
        act_as = self.sudo().with_context(active_test=False).search(
            [("supervisor_key_id", "in", self.ids), ("revoked_at", "=", False)])
        if act_as:
            act_as.action_revoke()
        return True

    def write(self, vals):
        res = super().write(vals)
        if {"active", "revoked_at", "key_hash", "expires_at"} & set(vals):
            _forget_key_ids(set(self.ids))
        return res

    def unlink(self):
        _forget_key_ids(set(self.ids))
        return super().unlink()

    # ------------------------------------------------------------------
    # Último uso: como mucho una escritura por minuto y por clave
    # ------------------------------------------------------------------
    @api.model
    def _api_touch_after_commit(self, cr, key_id, ip):
        """Programa la marca de último uso para DESPUÉS de confirmar `cr`.

        No puede ir dentro de la petición: la transacción de la petición
        (REPEATABLE READ) tomó su foto al autenticar la clave, y si un cursor
        aparte confirma un UPDATE de la fila de shrimp_api_key, el INSERT en
        shrimp_api_idempotency (FK a la clave, KEY SHARE sobre la fila)
        falla con «could not serialize access due to concurrent update».

        Si la petición se revierte (reintento por concurrencia), Odoo vacía
        los postcommit y el reintento la vuelve a programar. Un fallo aquí
        nunca rompe la respuesta: la petición ya está confirmada.
        """
        pendientes = cr.postcommit.data.setdefault("shrimp_api.touch", {})
        if not pendientes:
            registry = self.env.registry
            api_key = self

            def _touch():
                for kid, kip in list(pendientes.items()):
                    try:
                        with registry.cursor() as tcr:
                            api_key._api_touch_sql(tcr, kid, kip)
                    except Exception:  # noqa: BLE001 - dato informativo
                        _logger.info("No se pudo marcar el último uso de la clave %s",
                                     kid, exc_info=True)
                pendientes.clear()

            cr.postcommit.add(_touch)
        pendientes[key_id] = ip

    @api.model
    def _api_touch_sql(self, cr, key_id, ip):
        """Se ejecuta en un cursor propio, después de confirmar la petición
        (_api_touch_after_commit), con SKIP LOCKED: si otra petición está
        actualizando la fila, esta no espera — la marca de último uso no
        merece un bloqueo."""
        cr.execute("""
            UPDATE shrimp_api_key SET last_used = (now() at time zone 'utc'), last_ip = %s
             WHERE id IN (SELECT id FROM shrimp_api_key
                           WHERE id = %s
                             AND (last_used IS NULL
                                  OR last_used < (now() at time zone 'utc') - interval '60 seconds')
                           FOR UPDATE SKIP LOCKED)
        """, ((ip or "")[:64], key_id))
