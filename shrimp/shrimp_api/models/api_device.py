"""Dispositivos de la app móvil y operaciones firmadas.

Contrato: docs/app/CONTRATO-FIRMA-DISPOSITIVO.md (versión 1).

La clave de API de la app (la que emite POST /auth/login) vive en el almacén
seguro del teléfono, pero una clave robada sirve desde cualquier sitio. Para
las operaciones que comprometen al socio (aceptar, dictaminar, comprar,
firmar actas…) la app registra además un DISPOSITIVO: una clave pública
EC P-256 cuya privada vive en el Android Keystore / Secure Enclave, no se
puede exportar y solo firma tras la huella (o la credencial del teléfono).
Cada operación sensible lleva una firma ES256 sobre una cadena canónica
(método, ruta, timestamp, Idempotency-Key, sha256 del cuerpo) y el servidor
la comprueba antes de ejecutar nada (controllers/main.py).

Tres modelos:

* ``shrimp.api.device``: el dispositivo (uno activo por clave de app).
* ``shrimp.api.signature.nonce``: antirreplay. El hash de cada firma aceptada
  se guarda 8 días (la ventana del timestamp es de 7 días + 10 min).
* ``shrimp.api.signed.operation``: auditoría de solo lectura de cada
  operación firmada aceptada. Se conserva (no la borra el cron).
"""

import base64
import binascii
import hashlib
import logging

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

SUPPORTED_ALGS = ("ES256",)
# Orden del grupo de P-256 (para normalizar s a «s bajo»: ver _api_signature_hash).
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
NONCE_RETENTION_DAYS = 8


def _b64decode_std(value):
    """Base64 estándar estricto (se toleran saltos de línea y espacios)."""
    txt = "".join((value or "").split())
    return base64.b64decode(txt, validate=True)


class ShrimpApiDevice(models.Model):
    _name = "shrimp.api.device"
    _description = "Dispositivo registrado de la app móvil"
    _inherit = "shrimp.uuid.mixin"
    _order = "active desc, id desc"

    name = fields.Char(string="Nombre", required=True, readonly=True)
    user_id = fields.Many2one("res.users", string="Usuario", required=True, readonly=True,
                              index=True, ondelete="cascade")
    partner_id = fields.Many2one(related="user_id.partner_id", string="Socio")
    api_key_id = fields.Many2one("shrimp.api.key", string="Clave de API", required=True,
                                 readonly=True, index=True, ondelete="cascade")
    platform = fields.Char(string="Plataforma", readonly=True)
    alg = fields.Selection([("ES256", "ES256 (ECDSA P-256 + SHA-256)")], string="Algoritmo",
                           required=True, default="ES256", readonly=True)
    public_key = fields.Text(string="Clave pública (DER base64)", required=True, readonly=True,
                             help="SubjectPublicKeyInfo DER en base64 estándar.")
    key_fingerprint = fields.Char(string="Huella de la clave", readonly=True,
                                  help="SHA-256 (hex) del DER de la clave pública.")
    active = fields.Boolean(default=True, readonly=True)
    revoked_at = fields.Datetime(string="Revocado el", readonly=True, copy=False)
    revoked_by_id = fields.Many2one("res.users", string="Revocado por", readonly=True, copy=False)
    last_used = fields.Datetime(string="Último uso", readonly=True)
    last_ip = fields.Char(string="Última IP", readonly=True)

    # Una clave de app tiene como máximo un dispositivo activo.
    _one_active_per_key = models.UniqueIndex(
        "(api_key_id) WHERE active IS TRUE",
        "Esta clave de API ya tiene un dispositivo activo.")

    # ------------------------------------------------------------------
    # Clave pública
    # ------------------------------------------------------------------
    @api.model
    def _api_normalize_public_key(self, value):
        """Valida una SubjectPublicKeyInfo DER en base64 y devuelve
        (base64_normalizado, huella_hex). Solo EC P-256."""
        try:
            der = _b64decode_std(value)
        except (binascii.Error, ValueError):
            raise ValidationError(_("La clave pública no es base64 estándar válido."))
        try:
            key = serialization.load_der_public_key(der)
        except (ValueError, TypeError, UnsupportedAlgorithm):
            raise ValidationError(_("La clave pública no es una SubjectPublicKeyInfo DER válida."))
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValidationError(_("La clave pública tiene que ser EC P-256 (secp256r1) para ES256."))
        der = key.public_bytes(serialization.Encoding.DER,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(der).decode(), hashlib.sha256(der).hexdigest()

    def _api_public_key(self):
        self.ensure_one()
        return serialization.load_der_public_key(_b64decode_std(self.public_key))

    def _api_verify(self, message, signature_b64):
        """Comprueba la firma ES256 (DER, base64 estándar) de ``message``.

        Devuelve el hash de la firma normalizada o None si no verifica.
        """
        self.ensure_one()
        try:
            signature = _b64decode_std(signature_b64)
        except (binascii.Error, ValueError):
            return None
        if not signature or len(signature) > 80:
            return None
        try:
            self._api_public_key().verify(signature, message, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError, TypeError):
            return None
        return self._api_signature_hash(signature)

    @api.model
    def _api_signature_hash(self, signature):
        """SHA-256 de la firma NORMALIZADA (r y s bajo, 32+32 bytes).

        ECDSA es maleable: si (r, s) verifica, (r, n−s) también, y un DER se
        puede reescribir sin cambiar (r, s). Con el hash de los bytes tal cual
        bastaría retocar la firma para saltarse el antirreplay; normalizada,
        todas las variantes de una misma firma dan el mismo hash.
        """
        r, s = decode_dss_signature(signature)
        s = min(s, P256_ORDER - s)
        return hashlib.sha256(r.to_bytes(32, "big") + s.to_bytes(32, "big")).hexdigest()

    # ------------------------------------------------------------------
    # Revocación
    # ------------------------------------------------------------------
    def _api_revoke(self, revoke_key=True):
        """Revoca los dispositivos y, si ``revoke_key``, también su clave de
        API (lo que pide el contrato al revocar un dispositivo; al registrar
        uno nuevo solo se revoca el anterior, no la clave)."""
        now = fields.Datetime.now()
        devices = self.sudo().with_context(active_test=False)
        for dev in devices.filtered("active"):
            dev.write({"active": False, "revoked_at": now, "revoked_by_id": self.env.user.id})
        if revoke_key:
            keys = devices.api_key_id.filtered(lambda k: not k.revoked_at)
            if keys:
                keys.action_revoke()
        return True

    def action_revoke(self):
        """Botón del backend: revoca el dispositivo y su clave."""
        return self._api_revoke(revoke_key=True)

    def write(self, vals):
        # Un dispositivo no se «reactiva»: la clave privada pudo quedar en un
        # teléfono perdido. Se registra otro.
        if vals.get("active") and any(not d.active for d in self):
            raise UserError(_("Un dispositivo revocado no se reactiva: la app tiene que "
                              "registrar uno nuevo."))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Último uso (después de confirmar; ver shrimp.api.key._api_touch_after_commit)
    # ------------------------------------------------------------------
    @api.model
    def _api_touch_after_commit(self, cr, device_id, ip):
        pendientes = cr.postcommit.data.setdefault("shrimp_api.touch_device", {})
        if not pendientes:
            registry = self.env.registry

            def _touch():
                for did, dip in list(pendientes.items()):
                    try:
                        with registry.cursor() as tcr:
                            tcr.execute("""
                                UPDATE shrimp_api_device
                                   SET last_used = (now() at time zone 'utc'), last_ip = %s
                                 WHERE id IN (SELECT id FROM shrimp_api_device
                                               WHERE id = %s
                                                 AND (last_used IS NULL OR last_used <
                                                      (now() at time zone 'utc') - interval '60 seconds')
                                               FOR UPDATE SKIP LOCKED)
                            """, ((dip or "")[:64], did))
                    except Exception:  # noqa: BLE001 - dato informativo
                        _logger.info("No se pudo marcar el último uso del dispositivo %s",
                                     did, exc_info=True)
                pendientes.clear()

            cr.postcommit.add(_touch)
        pendientes[device_id] = ip


class ShrimpApiSignatureNonce(models.Model):
    """Hash de cada firma aceptada, durante 8 días (antirreplay).

    Se inserta con INSERT … ON CONFLICT DO NOTHING en la misma transacción
    que la operación: si la operación se deshace, la firma no queda gastada.
    """

    _name = "shrimp.api.signature.nonce"
    _description = "Antirreplay de firmas de la app"
    _log_access = False
    _order = "id desc"

    signature_hash = fields.Char(required=True, readonly=True)
    device_id = fields.Integer(readonly=True, help="id de shrimp.api.device (sin clave foránea "
                                                   "para no bloquear la fila del dispositivo).")
    received_at = fields.Datetime(required=True, readonly=True, index=True,
                                  default=fields.Datetime.now)

    _signature_hash_unique = models.Constraint(
        "unique(signature_hash)", "Esa firma ya se usó.")

    @api.model
    def _api_consume(self, signature_hash, device_id):
        """True si la firma era nueva (y queda gastada); False si ya se usó."""
        self.env.cr.execute("""
            INSERT INTO shrimp_api_signature_nonce (signature_hash, device_id, received_at)
            VALUES (%s, %s, now() at time zone 'utc')
            ON CONFLICT (signature_hash) DO NOTHING
            RETURNING id
        """, (signature_hash, device_id))
        return bool(self.env.cr.fetchone())

    @api.model
    def _api_release(self, signature_hash):
        """Devuelve la firma (la operación falló con 5xx y se puede reintentar)."""
        self.env.cr.execute("DELETE FROM shrimp_api_signature_nonce WHERE signature_hash = %s",
                            (signature_hash,))

    @api.model
    def _cron_cleanup(self):
        self.env.cr.execute("""
            DELETE FROM shrimp_api_signature_nonce
             WHERE received_at < (now() at time zone 'utc') - interval '%s days'
        """ % int(NONCE_RETENTION_DAYS))
        return True


class ShrimpApiSignedOperation(models.Model):
    """Auditoría: cada operación firmada aceptada (solo lectura)."""

    _name = "shrimp.api.signed.operation"
    _description = "Operación firmada desde la app (auditoría)"
    _order = "id desc"
    _rec_name = "path"

    user_id = fields.Many2one("res.users", string="Usuario", required=True, readonly=True,
                              index=True, ondelete="restrict")
    partner_id = fields.Many2one(related="user_id.partner_id", string="Socio")
    device_id = fields.Many2one("shrimp.api.device", string="Dispositivo", readonly=True,
                                index=True, ondelete="set null")
    device_ref = fields.Char(string="Id del dispositivo", readonly=True,
                             help="uuid del dispositivo (se conserva aunque se borre).")
    method = fields.Char(string="Método", readonly=True)
    path = fields.Char(string="Ruta", readonly=True)
    idempotency_key = fields.Char(string="Idempotency-Key", readonly=True)
    signed_timestamp = fields.Integer(string="Timestamp firmado (Unix)", readonly=True)
    signed_at = fields.Datetime(string="Firmado el", readonly=True,
                                help="X-Signature-Timestamp convertido a fecha (UTC).")
    received_at = fields.Datetime(string="Recibido el", readonly=True, required=True,
                                  default=fields.Datetime.now)
    signature_hash = fields.Char(string="Hash de la firma", readonly=True)
    body_sha256 = fields.Char(string="SHA-256 del cuerpo", readonly=True)
    ip = fields.Char(string="IP", readonly=True)
    request_id = fields.Char(string="request_id", readonly=True)
    status_code = fields.Integer(string="Respuesta HTTP", readonly=True)
    # Modo supervisor (POST /supervisor/act-as): el socio cuya cuenta se vio
    # y la clave de solo lectura emitida.
    act_as_partner_id = fields.Many2one("res.partner", string="Socio visto (supervisor)",
                                        readonly=True, index="btree_not_null", ondelete="set null")
    act_as_key_id = fields.Many2one("shrimp.api.key", string="Clave de solo lectura emitida",
                                    readonly=True, ondelete="set null")

    _AUDIT_WRITABLE = {"status_code", "act_as_partner_id", "act_as_key_id"}

    # Solo el despachador (y el act-as del supervisor) escriben aquí, en sudo
    # y con este contexto.
    def write(self, vals):
        if not self.env.context.get("shrimp_api_audit") or set(vals) - self._AUDIT_WRITABLE:
            raise UserError(_("La auditoría de operaciones firmadas es de solo lectura."))
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get("shrimp_api_audit"):
            raise UserError(_("La auditoría de operaciones firmadas es de solo lectura."))
        return super().unlink()
