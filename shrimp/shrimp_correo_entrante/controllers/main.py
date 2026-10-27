import hmac
import logging

from odoo import SUPERUSER_ID, http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Un correo con adjuntos grandes (fotos, PDF) entra holgado; más que esto no es
# una respuesta comercial y no se procesa.
TAMANO_MAXIMO = 25 * 1024 * 1024


class CorreoEntrante(http.Controller):

    @http.route("/correo/entrante", type="http", auth="none", methods=["POST"],
                csrf=False, save_session=False, readonly=False)
    def correo_entrante(self, **kw):
        env = request.env(user=SUPERUSER_ID)
        esperado = env["ir.config_parameter"].sudo().get_param("shrimp_correo.token") or ""
        recibido = request.httprequest.headers.get("X-Correo-Token", "")
        if not esperado or not hmac.compare_digest(esperado.encode(), recibido.encode()):
            return request.make_response("no autorizado", status=403)
        largo = request.httprequest.content_length or 0
        if largo > TAMANO_MAXIMO:
            return request.make_response("demasiado grande", status=413)
        crudo = request.httprequest.get_data(cache=False)
        if not crudo:
            return request.make_response("vacío", status=400)
        try:
            env["mail.thread"].with_context(mail_create_nolog=True).message_process(None, crudo)
        except ValueError as e:
            # Sin ruta: no responde a nada enviado desde Odoo. Ya está en Gmail.
            _logger.info("Correo entrante sin ruta en Odoo, ignorado: %s", str(e)[:200])
            return request.make_response("ignorado", status=200)
        except Exception:
            _logger.exception("Error al procesar un correo entrante")
            return request.make_response("error", status=500)
        return request.make_response("procesado", status=200)
