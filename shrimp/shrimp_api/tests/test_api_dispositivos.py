"""Dispositivos de la app y operaciones firmadas.

Contrato: docs/app/CONTRATO-FIRMA-DISPOSITIVO.md. Se genera un par EC P-256
con cryptography (lo que en el teléfono hace el Keystore / Secure Enclave)
y se firma la cadena canónica igual que la app.
"""

import base64
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import ApiCase

PASSWORD = "Clave-api-123"
DAY = 24 * 3600


def new_keypair(curve=None):
    priv = ec.generate_private_key(curve or ec.SECP256R1())
    der = priv.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    return priv, base64.b64encode(der).decode()


def sign(priv, method, path, ts, idem, body):
    """La cadena del contrato (§3), firmada ES256 en DER y base64 estándar."""
    message = "\n".join([method, path, str(ts), idem or "",
                         hashlib.sha256(body).hexdigest()]).encode("utf-8")
    return base64.b64encode(priv.sign(message, ec.ECDSA(hashes.SHA256()))).decode()


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiDispositivos(ApiCase):

    def setUp(self):
        super().setUp()
        failures = getattr(self.env.registry, "_login_failures", None)
        if failures is not None:
            failures.clear()

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------
    def login(self, login="a.api", device="Pixel de prueba"):
        resp = self.api("POST", "/auth/login", body={"login": login, "password": PASSWORD,
                                                     "device": device})
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def device_body(self, pub_b64, name="Samsung A54 de Juan", **extra):
        body = {"name": name, "platform": "android", "public_key": pub_b64, "alg": "ES256"}
        body.update(extra)
        return body

    def register(self, raw, pub_b64, name="Samsung A54 de Juan", **extra):
        return self.api("POST", "/me/devices", raw=raw, body=self.device_body(pub_b64, name, **extra))

    def app_with_device(self, login="a.api"):
        raw = self.login(login)["api_key"]
        priv, pub = new_keypair()
        resp = self.register(raw, pub)
        self.assertEqual(resp.status_code, 201, resp.text)
        return raw, priv, resp.json()["id"]

    def signed(self, method, path, raw, device_id, priv, body=None, idem=None, ts=None,
               signature=None, headers=None):
        """Petición firmada. Devuelve (respuesta, cabeceras usadas, bytes del cuerpo)."""
        data = json.dumps(body).encode() if body is not None else b""
        if idem is None and method == "POST":
            idem = str(uuid.uuid4())
        ts = int(time.time()) if ts is None else ts
        full_path = "/api/v1" + path
        h = {"Content-Type": "application/json", "X-Device-Id": device_id,
             "X-Signature-Timestamp": str(ts),
             "X-Signature": signature or sign(priv, method, full_path, ts, idem, data)}
        if idem:
            h["Idempotency-Key"] = idem
        h.update(headers or {})
        return self.send(method, path, raw, h, data), h, data

    def send(self, method, path, raw, headers, data):
        h = dict(headers)
        h["Authorization"] = "Bearer " + raw
        return self.url_open("/api/v1" + path, data=data, headers=h, method=method,
                             allow_redirects=False)

    def audits(self, **domain):
        return self.env["shrimp.api.signed.operation"].sudo().search(
            [(k, "=", v) for k, v in domain.items()])

    # ------------------------------------------------------------------
    # Login, registro y operación firmada correcta
    # ------------------------------------------------------------------
    def test_login_marca_clave_de_app(self):
        data = self.login()
        self.assertIs(data["device_required"], True)
        key = self.env["shrimp.api.key"].sudo().search([("user_id", "=", self.u_a.id)],
                                                       order="id desc", limit=1)
        self.assertTrue(key.is_app_key)
        # Las claves creadas a mano no son de la app.
        self.assertFalse(self.key_a.is_app_key)

    def test_registro_y_operacion_firmada(self):
        raw = self.login()["api_key"]
        priv, pub = new_keypair()
        resp = self.register(raw, pub)
        self.assertEqual(resp.status_code, 201, resp.text)
        dev = resp.json()
        for k in ("id", "name", "platform", "alg", "created_at", "active"):
            self.assertIn(k, dev)
        self.assertEqual(dev["name"], "Samsung A54 de Juan")
        self.assertEqual(dev["platform"], "android")
        self.assertEqual(dev["alg"], "ES256")
        self.assertIs(dev["active"], True)
        self.assertTrue(dev["created_at"].endswith("Z"))
        device = self.env["shrimp.api.device"].sudo().search([("uuid_ref", "=", dev["id"])])
        self.assertEqual(device.user_id, self.u_a)
        self.assertTrue(device.api_key_id.is_app_key)
        self.assertEqual(device.public_key, pub)

        resp, h, _data = self.signed("POST", "/me/active-role", raw, dev["id"], priv,
                                     body={"role": "camaronera"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["active_role"], "camaronera")

        # Auditoría en la misma transacción, con todos los datos.
        audit = self.audits(device_id=device.id)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.user_id, self.u_a)
        self.assertEqual(audit.method, "POST")
        self.assertEqual(audit.path, "/api/v1/me/active-role")
        self.assertEqual(audit.signed_timestamp, int(h["X-Signature-Timestamp"]))
        self.assertEqual(audit.idempotency_key, h["Idempotency-Key"])
        self.assertEqual(audit.status_code, 200)
        self.assertTrue(audit.ip)
        self.assertRegex(audit.signature_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(audit.device_ref, dev["id"])
        nonce = self.env["shrimp.api.signature.nonce"].sudo().search(
            [("signature_hash", "=", audit.signature_hash)])
        self.assertEqual(len(nonce), 1)

        # La lista de mis dispositivos.
        lista = self.api("GET", "/me/devices", raw=raw)
        self.assertEqual(lista.status_code, 200, lista.text)
        ids = [d["id"] for d in lista.json()["data"]]
        self.assertIn(dev["id"], ids)
        self.assertTrue([d for d in lista.json()["data"] if d["id"] == dev["id"]][0]["current"])
        # Otro socio no lo ve ni lo puede revocar.
        self.assertNotIn(dev["id"], [d["id"] for d in self.api("GET", "/me/devices", raw=self.raw_b).json()["data"]])
        self.assertProblem(self.api("POST", "/me/devices/%s:revoke" % dev["id"], raw=self.raw_b), 404)

    def test_get_no_se_firma(self):
        raw, _priv, _dev = self.app_with_device()
        self.assertEqual(self.api("GET", "/me", raw=raw).status_code, 200)
        self.assertEqual(self.api("GET", "/verifications", raw=raw).status_code, 200)

    # ------------------------------------------------------------------
    # Errores de la sección 5
    # ------------------------------------------------------------------
    def test_sin_firma(self):
        raw, _priv, dev_id = self.app_with_device()
        self.assertProblem(self.api("POST", "/me/active-role", raw=raw, body={"role": "camaronera"}),
                           403, "signature-required")
        # Con alguna cabecera pero no todas.
        body = self.assertProblem(self.api("POST", "/me/active-role", raw=raw, body={"role": "camaronera"},
                                           headers={"X-Device-Id": dev_id}), 403, "signature-required")
        self.assertIn("X-Signature", body["detail"])
        self.assertFalse(self.audits(user_id=self.u_a.id))

    def test_firma_mala(self):
        raw, priv, dev_id = self.app_with_device()
        # Firmada con otra clave.
        otra, _pub = new_keypair()
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, otra, body={"role": "camaronera"})
        self.assertProblem(resp, 403, "invalid-signature")
        # Firma buena de OTRO cuerpo.
        ts = int(time.time())
        idem = str(uuid.uuid4())
        firma = sign(priv, "POST", "/api/v1/me/active-role", ts, idem, b'{"role": "empacadora"}')
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   idem=idem, ts=ts, signature=firma)
        self.assertProblem(resp, 403, "invalid-signature")
        # Firma buena de otra ruta (la alias /x/accion se firma tal como se envía).
        firma = sign(priv, "POST", "/api/v1/me/active-roles", ts, idem, b'{"role": "camaronera"}')
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   idem=idem, ts=ts, signature=firma)
        self.assertProblem(resp, 403, "invalid-signature")
        # Basura y timestamp no numérico.
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   signature="no-es-base64!!")
        self.assertProblem(resp, 403, "invalid-signature")
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   headers={"X-Signature-Timestamp": "ayer"})
        self.assertProblem(resp, 403, "invalid-signature")
        self.assertFalse(self.audits(user_id=self.u_a.id))

    def test_ventana_del_timestamp(self):
        raw, priv, dev_id = self.app_with_device()
        now = int(time.time())
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   ts=now - 8 * DAY)
        self.assertProblem(resp, 403, "signature-expired")
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   ts=now + 20 * 60)
        self.assertProblem(resp, 403, "signature-expired")
        # La cola sin internet: firmada hace 6 días, enviada hoy.
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   ts=now - 6 * DAY)
        self.assertEqual(resp.status_code, 200, resp.text)
        # Reloj del teléfono 5 minutos adelantado.
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"},
                                   ts=now + 5 * 60)
        self.assertEqual(resp.status_code, 200, resp.text)
        audit = self.audits(user_id=self.u_a.id).sorted("id")
        self.assertEqual(len(audit), 2)
        self.assertEqual(audit[0].signed_timestamp, now - 6 * DAY)
        self.assertEqual(audit[0].signed_at,
                         datetime.fromtimestamp(now - 6 * DAY, tz=timezone.utc).replace(tzinfo=None))

    def test_reintento_idempotente_y_replay(self):
        raw, priv, dev_id = self.app_with_device()
        resp1, h, data = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"})
        self.assertEqual(resp1.status_code, 200, resp1.text)
        # Reintento legítimo (misma firma, Idempotency-Key, ruta y cuerpo):
        # la misma respuesta, sin volver a ejecutar ni a auditar.
        resp2 = self.send("POST", "/me/active-role", raw, h, data)
        self.assertEqual(resp2.status_code, 200, resp2.text)
        self.assertEqual(resp2.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(resp2.json(), resp1.json())
        self.assertEqual(len(self.audits(user_id=self.u_a.id)), 1)

        # La misma firma con OTRA Idempotency-Key: la clave va dentro de la
        # cadena firmada, así que la firma ya no verifica.
        otra = dict(h, **{"Idempotency-Key": str(uuid.uuid4())})
        self.assertProblem(self.send("POST", "/me/active-role", raw, otra, data), 403, "invalid-signature")

        # Pasadas las 24 h de la idempotencia ya no hay respuesta guardada:
        # repetir la firma es un replay.
        key = self.env["shrimp.api.key"].sudo().search([("user_id", "=", self.u_a.id)], order="id desc", limit=1)
        self.env["shrimp.api.idempotency"].sudo().search(
            [("api_key_id", "=", key.id), ("key", "=", h["Idempotency-Key"])]).unlink()
        self.assertProblem(self.send("POST", "/me/active-role", raw, h, data), 409, "signature-replayed")
        # Y no deja la Idempotency-Key «en curso».
        self.assertFalse(self.env["shrimp.api.idempotency"].sudo().search(
            [("api_key_id", "=", key.id), ("key", "=", h["Idempotency-Key"])]))
        self.assertEqual(len(self.audits(user_id=self.u_a.id)), 1)

        # Firma maleada (r, n-s): mismo mensaje, otros bytes de firma. El
        # antirreplay la reconoce igual.
        from cryptography.hazmat.primitives.asymmetric.utils import (decode_dss_signature,
                                                                     encode_dss_signature)
        from odoo.addons.shrimp_api.models.api_device import P256_ORDER
        r, s = decode_dss_signature(base64.b64decode(h["X-Signature"]))
        maleada = base64.b64encode(encode_dss_signature(r, P256_ORDER - s)).decode()
        self.assertProblem(self.send("POST", "/me/active-role", raw, dict(h, **{"X-Signature": maleada}), data),
                           409, "signature-replayed")

    def test_patch_firmado(self):
        """PATCH /dispatches/{id} es sensible. Con un despacho que no existe
        el handler responde 404, pero la firma se comprueba antes."""
        raw, priv, dev_id = self.app_with_device()
        path = "/dispatches/%s" % uuid.uuid4()
        body = {"notes": "Sale a las 5"}
        self.assertProblem(self.api("PATCH", path, raw=raw, body=body), 403, "signature-required")
        # Sin Idempotency-Key (cadena con la línea vacía): se ejecuta y
        # repetir la misma firma es un replay.
        resp, h, data = self.signed("PATCH", path, raw, dev_id, priv, body=body)
        self.assertProblem(resp, 404, "not-found")
        self.assertNotIn("Idempotency-Key", h)
        self.assertProblem(self.send("PATCH", path, raw, h, data), 409, "signature-replayed")
        # Con Idempotency-Key: el reintento recibe la respuesta guardada.
        resp, h, data = self.signed("PATCH", path, raw, dev_id, priv, body=body, idem=str(uuid.uuid4()))
        self.assertProblem(resp, 404, "not-found")
        again = self.send("PATCH", path, raw, h, data)
        self.assertProblem(again, 404, "not-found")
        self.assertEqual(again.headers.get("Idempotent-Replayed"), "true")
        audits = self.audits(user_id=self.u_a.id)
        self.assertEqual(len(audits), 2)
        self.assertEqual(set(audits.mapped("status_code")), {404})
        self.assertEqual(set(audits.mapped("method")), {"PATCH"})
        # Una clave manual sigue sin firmar (y llega al handler: 404).
        self.assertProblem(self.api("PATCH", path, raw=self.raw_a, body=body), 404, "not-found")

    def test_dispositivo_revocado(self):
        raw, priv, dev_id = self.app_with_device()
        # Registrar otro (firmado por el actual) revoca el anterior, no la clave.
        priv2, pub2 = new_keypair()
        resp, _h, _d = self.signed("POST", "/me/devices", raw, dev_id, priv, body=self.device_body(pub2, "Tablet"))
        self.assertEqual(resp.status_code, 201, resp.text)
        dev2 = resp.json()["id"]
        old = self.env["shrimp.api.device"].sudo().with_context(active_test=False).search([("uuid_ref", "=", dev_id)])
        self.assertFalse(old.active)
        self.assertTrue(old.revoked_at)
        self.assertEqual(old.revoked_by_id, self.u_a)
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"})
        self.assertProblem(resp, 403, "device-revoked")
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev2, priv2, body={"role": "camaronera"})
        self.assertEqual(resp.status_code, 200, resp.text)
        estados = {d["id"]: d["active"] for d in self.api("GET", "/me/devices", raw=raw).json()["data"]}
        self.assertEqual(estados, {dev_id: False, dev2: True})

        # Revocar el dispositivo revoca también su clave.
        resp = self.api("POST", "/me/devices/%s:revoke" % dev2, raw=raw)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIs(resp.json()["active"], False)
        key = self.env["shrimp.api.key"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.u_a.id), ("is_app_key", "=", True)], order="id desc", limit=1)
        self.assertEqual(key.status, "revoked")
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev2, priv2, body={"role": "camaronera"})
        self.assertProblem(resp, 403, "device-revoked")
        self.assertProblem(self.api("GET", "/me", raw=raw), 401, "revoked-api-key")

    def test_logout_revoca_el_dispositivo(self):
        raw, _priv, dev_id = self.app_with_device()
        self.assertEqual(self.api("POST", "/auth/logout", raw=raw).status_code, 200)
        dev = self.env["shrimp.api.device"].sudo().with_context(active_test=False).search([("uuid_ref", "=", dev_id)])
        self.assertFalse(dev.active)

    def test_clave_de_app_sin_dispositivo(self):
        raw = self.login()["api_key"]
        self.assertProblem(self.api("POST", "/me/active-role", raw=raw, body={"role": "camaronera"}),
                           403, "device-not-registered")
        # Con cabeceras de un dispositivo que no es de esta clave.
        _raw_b, priv_b, dev_b = self.app_with_device("b.api")
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_b, priv_b, body={"role": "camaronera"})
        self.assertProblem(resp, 403, "device-not-registered")

    def test_clave_manual_sin_cambios(self):
        resp = self.api("POST", "/me/active-role", raw=self.raw_a, body={"role": "camaronera"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(self.audits(user_id=self.u_a.id))
        # Y no puede registrar dispositivos.
        _priv, pub = new_keypair()
        self.assertProblem(self.register(self.raw_a, pub), 409, "not-an-app-key")
        self.assertEqual(self.api("GET", "/me/devices", raw=self.raw_a).json(), {"data": []})

    def test_clave_publica_invalida(self):
        raw = self.login()["api_key"]
        body = self.assertProblem(self.register(raw, "esto no es base64"), 422, "validation-error")
        self.assertEqual(body["errors"][0]["field"], "public_key")
        self.assertProblem(self.register(raw, base64.b64encode(b"\x30\x03\x02\x01\x01").decode()), 422)
        # EC pero P-384.
        _p, pub384 = new_keypair(ec.SECP384R1())
        self.assertProblem(self.register(raw, pub384), 422, "validation-error")
        # RSA.
        rsa_pub = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.assertProblem(self.register(raw, base64.b64encode(rsa_pub).decode()), 422, "validation-error")
        # Algoritmo no soportado.
        _p, pub = new_keypair()
        body = self.assertProblem(self.register(raw, pub, alg="RS256"), 422, "validation-error")
        self.assertEqual(body["errors"][0]["field"], "alg")
        self.assertFalse(self.env["shrimp.api.device"].sudo().with_context(active_test=False).search(
            [("user_id", "=", self.u_a.id)]))

    # ------------------------------------------------------------------
    # Auditoría, retención y documentación
    # ------------------------------------------------------------------
    def test_auditoria_solo_lectura_y_retencion(self):
        raw, priv, dev_id = self.app_with_device()
        resp, _h, _d = self.signed("POST", "/me/active-role", raw, dev_id, priv, body={"role": "camaronera"})
        self.assertEqual(resp.status_code, 200, resp.text)
        audit = self.audits(user_id=self.u_a.id)
        self.assertEqual(len(audit), 1)
        with self.assertRaises(UserError):
            audit.write({"path": "/otra"})
        with self.assertRaises(UserError):
            audit.unlink()
        # El socio no lee la auditoría ni el antirreplay.
        with self.assertRaises(AccessError):
            self.env["shrimp.api.signed.operation"].with_user(self.u_a).search([])
        with self.assertRaises(AccessError):
            self.env["shrimp.api.signature.nonce"].with_user(self.u_a).search([])
        # Sí lee sus dispositivos, y solo los suyos.
        self.app_with_device("b.api")
        mine = self.env["shrimp.api.device"].with_user(self.u_a).with_context(active_test=False).search([])
        self.assertEqual(mine.mapped("user_id"), self.u_a)

        # El cron borra el antirreplay de más de 8 días; la auditoría queda.
        Nonce = self.env["shrimp.api.signature.nonce"].sudo()
        nonce = Nonce.search([("signature_hash", "=", audit.signature_hash)])
        self.assertTrue(nonce)
        self.env.cr.execute("UPDATE shrimp_api_signature_nonce SET received_at = received_at - interval '9 days' "
                            "WHERE id = %s", (nonce.id,))
        Nonce._cron_cleanup()
        self.env.invalidate_all()
        self.assertFalse(Nonce.search([("signature_hash", "=", audit.signature_hash)]))
        self.assertTrue(audit.exists())

    def test_openapi_marca_las_rutas_firmadas(self):
        spec = self.url_open("/api/v1/openapi.json").json()
        op = spec["paths"]["/api/v1/me/active-role"]["post"]
        self.assertTrue(op.get("x-signed"))
        self.assertIn("X-Signature", [p["name"] for p in op["parameters"]])
        self.assertTrue(spec["paths"]["/api/v1/verifications/{id}/report"]["patch"].get("x-signed"))
        self.assertEqual(spec["paths"]["/api/v1/me/devices"]["post"].get("x-signed"), "if-device")
        self.assertFalse(spec["paths"]["/api/v1/facilities"]["post"].get("x-signed"))

    def test_portal_lista_y_revoca(self):
        raw, _priv, dev_id = self.app_with_device()
        self.authenticate("a.api", PASSWORD)
        page = self.url_open("/my/api-keys")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Mis dispositivos", page.text)
        self.assertIn("Samsung A54 de Juan", page.text)
        self.assertIn("/my/api-keys/devices/%s/revoke" % dev_id, page.text)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        # Otro socio no puede revocarlo.
        _raw_b, _p, dev_b = self.app_with_device("b.api")
        resp = self.url_open("/my/api-keys/devices/%s/revoke" % dev_b, data={"csrf_token": csrf})
        self.assertEqual(resp.status_code, 404)
        resp = self.url_open("/my/api-keys/devices/%s/revoke" % dev_id, data={"csrf_token": csrf})
        self.assertIn(resp.status_code, (200, 302, 303))
        dev = self.env["shrimp.api.device"].sudo().with_context(active_test=False).search([("uuid_ref", "=", dev_id)])
        self.assertFalse(dev.active)
        self.assertProblem(self.api("GET", "/me", raw=raw), 401, "revoked-api-key")
        self.assertTrue(self.env["shrimp.api.device"].sudo().search([("uuid_ref", "=", dev_b)]).active)

    # ------------------------------------------------------------------
    # Contrato 1.1: sustituir el dispositivo exige la firma del actual
    # ------------------------------------------------------------------
    def notes(self, partner):
        return self.env["mail.message"].sudo().search(
            [("model", "=", "res.partner"), ("res_id", "=", partner.id),
             ("subtype_id", "=", self.env.ref("mail.mt_note").id),
             ("body", "ilike", "App móvil: dispositivo")], order="id asc")

    def test_primer_registro_deja_nota(self):
        antes = len(self.notes(self.farm_a))
        _raw, _priv, _dev = self.app_with_device()
        notas = self.notes(self.farm_a)
        self.assertEqual(len(notas), antes + 1)
        self.assertIn("dispositivo registrado", notas[-1].body)
        self.assertIn("Samsung A54 de Juan", notas[-1].body)
        self.assertIn("android", notas[-1].body)
        self.assertIn("127.0.0.1", notas[-1].body)

    def test_sustitucion_firmada_por_el_actual(self):
        raw, priv, dev_id = self.app_with_device()
        priv2, pub2 = new_keypair()
        resp, h, _d = self.signed("POST", "/me/devices", raw, dev_id, priv,
                                  body=self.device_body(pub2, "Pixel nuevo"))
        self.assertEqual(resp.status_code, 201, resp.text)
        nuevo = resp.json()
        self.assertIs(nuevo["active"], True)
        Device = self.env["shrimp.api.device"].sudo().with_context(active_test=False)
        self.assertFalse(Device.search([("uuid_ref", "=", dev_id)]).active)
        self.assertTrue(Device.search([("uuid_ref", "=", nuevo["id"])]).active)
        # La clave sigue y el nuevo firma.
        resp, _h2, _d2 = self.signed("POST", "/me/active-role", raw, nuevo["id"], priv2, body={"role": "camaronera"})
        self.assertEqual(resp.status_code, 200, resp.text)
        # La sustitución queda en la auditoría, firmada por el anterior.
        audit = self.audits(path="/api/v1/me/devices")
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.device_ref, dev_id)
        self.assertEqual(audit.status_code, 201)
        self.assertEqual(audit.idempotency_key, h["Idempotency-Key"])
        # Y en el historial del socio.
        nota = self.notes(self.farm_a)[-1]
        self.assertIn("dispositivo sustituido", nota.body)
        self.assertIn("Pixel nuevo", nota.body)
        self.assertIn("Samsung A54 de Juan", nota.body)

    def test_sustitucion_sin_firma_rechazada(self):
        raw, _priv, dev_id = self.app_with_device()
        _p2, pub2 = new_keypair()
        self.assertProblem(self.register(raw, pub2, name="Ladrón"), 403, "signature-required")
        Device = self.env["shrimp.api.device"].sudo().with_context(active_test=False)
        self.assertTrue(Device.search([("uuid_ref", "=", dev_id)]).active)
        self.assertFalse(Device.search([("name", "=", "Ladrón")]))

    def test_sustitucion_firmada_por_el_par_nuevo_rechazada(self):
        raw, _priv, dev_id = self.app_with_device()
        priv2, pub2 = new_keypair()
        body = self.device_body(pub2, "Ladrón")
        # Con el X-Device-Id del actual pero firmada con la clave nueva.
        resp, _h, _d = self.signed("POST", "/me/devices", raw, dev_id, priv2, body=body)
        self.assertProblem(resp, 403, "invalid-signature")
        # Con un X-Device-Id inventado.
        resp, _h, _d = self.signed("POST", "/me/devices", raw, str(uuid.uuid4()), priv2, body=body)
        self.assertProblem(resp, 403, "device-not-registered")
        Device = self.env["shrimp.api.device"].sudo().with_context(active_test=False)
        self.assertTrue(Device.search([("uuid_ref", "=", dev_id)]).active)
        self.assertFalse(Device.search([("name", "=", "Ladrón")]))
        self.assertFalse(self.audits(path="/api/v1/me/devices"))

    def test_telefono_perdido_revocar_y_volver_a_entrar(self):
        raw, _priv, dev_id = self.app_with_device()
        # El socio revoca el teléfono perdido desde el portal.
        self.authenticate("a.api", PASSWORD)
        page = self.url_open("/my/api-keys")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        resp = self.url_open("/my/api-keys/devices/%s/revoke" % dev_id, data={"csrf_token": csrf})
        self.assertIn(resp.status_code, (200, 302, 303))
        self.assertProblem(self.api("GET", "/me", raw=raw), 401, "revoked-api-key")
        # Vuelve a entrar: clave nueva, sin dispositivo; el primer registro va sin firma.
        raw2 = self.login()["api_key"]
        self.assertNotEqual(raw2, raw)
        _p, pub = new_keypair()
        resp = self.register(raw2, pub, name="Teléfono nuevo")
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertIn("dispositivo registrado", self.notes(self.farm_a)[-1].body)
