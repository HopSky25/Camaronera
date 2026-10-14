import base64
import logging

from werkzeug.exceptions import Forbidden, NotFound

from odoo import _, http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from odoo.addons.shrimp_user_registry.controllers.main import (
    binary_headers, current_partner, flash_message, throttle)
from odoo.addons.shrimp_verification.controllers.main import ShrimpVerificationPortal

_logger = logging.getLogger(__name__)

# Un .p12 de persona natural o jurídica pesa unos pocos KB; 200 KB sobra y
# evita que alguien suba un archivo enorme a la memoria del servidor.
MAX_P12 = 200 * 1024
HOSTS_LOCALES = ("localhost", "127.0.0.1", "::1", "[::1]")


def _https_ok():
    """La firma solo viaja cifrada: HTTPS, salvo desde la propia máquina
    (desarrollo) o si el administrador lo desactiva (no recomendado)."""
    icp = request.env["ir.config_parameter"].sudo()
    exigir = (icp.get_param("shrimp_firma.exigir_https") or "True").strip().lower()
    if exigir in ("0", "false", "no", "off"):
        return True
    if request.httprequest.scheme == "https":
        return True
    return (request.httprequest.host or "").split(":")[0] in HOSTS_LOCALES \
        or (request.httprequest.host or "").startswith("[::1]")


class ShrimpFirmaPortal(ShrimpVerificationPortal):

    # ------------------------------------------------------------------
    # Portal del verificador
    # ------------------------------------------------------------------
    def _verificacion_para_firmar(self, ref):
        """La verificación, si quien entra puede firmarla (403 si no)."""
        rec = self._my_verification(ref)
        if not rec._puede_dictaminar(current_partner()):
            raise Forbidden()
        return rec

    @http.route("/verifier/verifications/<ref>/informe.pdf", type="http", auth="user",
                website=True, sitemap=False)
    def firma_informe_preview(self, ref, **kw):
        """El informe en PDF, SIN firmar, para revisarlo antes de firmar."""
        rec = self._my_verification(ref)
        if rec.verification_mode != "platform":
            raise NotFound()
        pdf = rec._firma_render_pdf()
        return request.make_response(pdf, headers=binary_headers(
            pdf, "application/pdf", filename=rec.informe_nombre_archivo()))

    @http.route("/verifier/verifications/<ref>/firmar", type="http", auth="user",
                website=True, methods=["GET"], sitemap=False)
    def firma_formulario(self, ref, **kw):
        salto = self._redirigir_a_plataforma_verificadores(
            "/verifier/verifications/%s/firmar" % ref)
        if salto:
            return salto
        rec = self._verificacion_para_firmar(ref)
        if not rec.firma_aplica() or rec.firma_vigente():
            return request.redirect("/verifier/verifications/%s" % rec.uuid_ref)
        return request.render("shrimp_firma_electronica.firma_formulario", {
            "page_name": "verifier_firma",
            "v": rec,
            "https_ok": _https_ok(),
            # ir.http ya cambió «?error=flash» por el texto que el propio
            # servidor guardó en la sesión (_shrimp_sanear_mensajes).
            "error": kw.get("error"),
        })

    @http.route("/verifier/verifications/<ref>/firmar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True, sitemap=False)
    def firma_firmar(self, ref, **post):
        rec = self._verificacion_para_firmar(ref)
        volver = "/verifier/verifications/%s/firmar?error=" % rec.uuid_ref
        if not _https_ok():
            return request.redirect(volver + flash_message(_(
                "La firma electrónica solo se envía por una conexión segura (HTTPS).")))
        # Intentos limitados por usuario: la contraseña de la firma no se
        # puede adivinar a fuerza de probar desde este formulario.
        if not throttle("firma_p12:%s" % request.env.user.id,
                        "shrimp_firma.intentos_max", "shrimp_firma.intentos_ventana_s",
                        10, 900):
            return request.redirect(volver + flash_message(_(
                "Demasiados intentos de firma. Espera unos minutos y vuelve a intentarlo.")))
        if not post.get("acepto"):
            return request.redirect(volver + flash_message(_(
                "Confirma que la firma electrónica es tuya y que firmas el informe.")))

        archivo = post.get("certificado")
        contenido = b""
        if archivo and hasattr(archivo, "read"):
            contenido = archivo.read(MAX_P12 + 1)
        contrasena = post.get("contrasena") or ""
        # Que no queden copias en el diccionario de la petición.
        post.pop("contrasena", None)
        post.pop("certificado", None)
        if not contenido:
            return request.redirect(volver + flash_message(_(
                "Sube el archivo de tu firma electrónica (.p12 o .pfx).")))
        if len(contenido) > MAX_P12:
            contenido = contrasena = None
            return request.redirect(volver + flash_message(_(
                "Ese archivo es demasiado grande para ser una firma electrónica (.p12 o .pfx).")))
        try:
            with request.env.cr.savepoint():
                rec.action_firmar_informe(contenido, contrasena, current_partner(),
                                          user=request.env.user)
        except AccessError:
            raise Forbidden() from None
        except UserError as e:
            return request.redirect(volver + flash_message(e.args[0] if e.args else ""))
        finally:
            contenido = contrasena = archivo = None
        return request.redirect("/verifier/verifications/%s?saved=firmado" % rec.uuid_ref)

    # ------------------------------------------------------------------
    # Comprobación pública
    # ------------------------------------------------------------------
    def _firma_por_token(self, token):
        token = (token or "").strip()
        # Un token de verdad son 43 caracteres url-safe; lo demás ni se busca.
        if len(token) < 32 or len(token) > 128:
            raise NotFound()
        firma = request.env["shrimp.verification.firma"].sudo().search(
            [("token", "=", token)], limit=1)
        if not firma or firma.token != token:
            raise NotFound()
        return firma

    @http.route("/verificacion/firma/<string:token>", type="http", auth="public",
                website=True, sitemap=False)
    def firma_publica(self, token, **kw):
        firma = self._firma_por_token(token)
        return request.render("shrimp_firma_electronica.firma_comprobacion", {
            "firma": firma,
            "v": firma.verification_id,
        })

    @http.route("/verificacion/firma/<string:token>/pdf", type="http", auth="public",
                website=True, sitemap=False)
    def firma_publica_pdf(self, token, **kw):
        firma = self._firma_por_token(token)
        adjunto = firma.attachment_id.sudo()
        if not adjunto:
            raise NotFound()
        contenido = adjunto.raw or base64.b64decode(adjunto.datas or b"")
        return request.make_response(contenido, headers=binary_headers(
            contenido, "application/pdf",
            filename=firma.verification_id.informe_nombre_archivo(firmado=True),
            download=True, cache="private, max-age=600"))
