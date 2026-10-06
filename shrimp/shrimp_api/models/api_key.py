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

    @api.constrains("scope_ids", "user_id")
    def _check_api_owner(self):
        for rec in self:
            if rec.key_prefix and rec.user_id and not rec.user_id.share and rec.active \
                    and not rec.revoked_at:
                raise ValidationError(_(
                    "Las claves de la API externa son para usuarios del portal "
                    "(socios). Las integraciones internas usan la API JSON-2 de Odoo."))

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

        for _attempt in range(5):
            prefix = secrets.token_hex(4)
            if not self.sudo().with_context(active_test=False).search_count(
                    [("key_prefix", "=", prefix)]):
                break
        raw = "trz_%s_%s" % (prefix, secrets.token_urlsafe(32))
        scopes = self.env["shrimp.api.scope"].sudo().search([("code", "in", codes)])
        rec = self.sudo().create({
            "name": name,
            "user_id": user.id,
            "scope": "read",
            "key": False,
            "key_prefix": prefix,
            "key_hash": KEY_CRYPT_CONTEXT.hash(raw),
            "scope_ids": [(6, 0, scopes.ids)],
            "expires_at": expires_at,
            "ip_allowlist": ip_allowlist or False,
            "allowed_origins": allowed_origins or False,
        })
        _logger.info("API key trz_%s creada para %s (scopes: %s)", prefix, user.login, ",".join(codes))
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
        if not user or not user.active or not user.share:
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
    def _api_touch_sql(self, cr, key_id, ip):
        """Se ejecuta en un cursor aparte (el del límite de peticiones), con
        SKIP LOCKED: si otra petición está actualizando la fila, esta no
        espera — la marca de último uso no merece un bloqueo."""
        cr.execute("""
            UPDATE shrimp_api_key SET last_used = (now() at time zone 'utc'), last_ip = %s
             WHERE id IN (SELECT id FROM shrimp_api_key
                           WHERE id = %s
                             AND (last_used IS NULL
                                  OR last_used < (now() at time zone 'utc') - interval '60 seconds')
                           FOR UPDATE SKIP LOCKED)
        """, ((ip or "")[:64], key_id))
