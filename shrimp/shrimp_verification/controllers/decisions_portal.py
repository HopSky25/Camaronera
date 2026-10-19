"""Portal de las decisiones de la auditoría de procesos (19.0.1.24.0).

  POST /verifier/verifications/<ref>/respond        D-21 la verificadora acepta
                                                     o declina la orden
  POST /verifier/verifications/<ref>/not-presented  D-16 rechazo por «Producto
                                                     no presentado» (fotos +
                                                     hora de llegada)
  POST /marketplace/sales/<tx>/second-opinion        D-25 el vendedor impugna el
                                                     rechazo (segunda opinión)

La elección del comprador tras el rechazo de su contraoferta (D-22) usa las
rutas de siempre de la ronda: /accept (comprar al precio original) y /reject
(cancelar sin costo).
"""
from datetime import datetime

import pytz

from odoo import http, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools import ustr
from werkzeug.exceptions import Forbidden

from odoo.addons.shrimp_marketplace.controllers.transaction_portal import (
    ShrimpTransactionPortalController as _TxPortal)
from odoo.addons.shrimp_user_registry.controllers.main import current_partner, flash_message

from .main import ShrimpVerificationPortal


def _msg(e):
    return e.args[0] if e.args else ustr(e)


class ShrimpVerificationDecisionsPortal(ShrimpVerificationPortal):

    # ---- D-21 -----------------------------------------------------------
    @http.route("/verifier/verifications/<ref>/respond", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_respond(self, ref, **post):
        rec = self._my_verification(ref, editable=True)
        decision = post.get("decision")
        try:
            with request.env.cr.savepoint():
                rec._shrimp_check_response_admin(self._partner())
                if decision == "accept":
                    rec.action_verifier_accept()
                elif decision == "decline":
                    rec.action_verifier_decline(post.get("reason"))
                else:
                    raise ValidationError(_("Respuesta no válida."))
        except (UserError, ValidationError, AccessError) as e:
            return request.redirect("/verifier/verifications/%s/assign-technician?error=1&message=%s"
                                    % (rec.uuid_ref, flash_message(_msg(e))))
        if decision == "decline":
            return request.redirect("/verifier/inbox?saved=declined")
        return request.redirect("/verifier/verifications/%s/assign-technician?saved=accepted"
                                % rec.uuid_ref)

    # ---- D-16 -----------------------------------------------------------
    @staticmethod
    def _local_to_utc(valor):
        """«2026-10-17T08:30» (hora local de la plataforma) → UTC ingenuo."""
        if not valor:
            return False
        try:
            local = datetime.strptime(valor.strip()[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ValidationError(_("La hora de llegada no es válida."))
        tz = request.env["shrimp.transaction"].with_context(shrimp_public_tz=True)._shrimp_tz()
        return tz.localize(local).astimezone(pytz.utc).replace(tzinfo=None)

    @http.route("/verifier/verifications/<ref>/not-presented", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def verifier_not_presented(self, ref, **post):
        rec = self._my_verification(ref, editable=True)
        if not rec._puede_dictaminar(self._partner()):
            raise Forbidden()
        try:
            with request.env.cr.savepoint():
                llegada = self._local_to_utc(post.get("arrival"))
                self._save_photos(rec)
                rec.action_not_presented(llegada, post.get("notes"))
        except (UserError, ValidationError, AccessError) as e:
            return request.redirect("/verifier/verifications/%s?error=1&message=%s#no-presentado"
                                    % (rec.uuid_ref, flash_message(_msg(e))))
        return request.redirect("/verifier/inbox?saved=verdict")

    # ---- D-25 -----------------------------------------------------------
    @http.route("/marketplace/sales/<tx_ref>/second-opinion", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_second_opinion(self, tx_ref, **post):
        partner = current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx or not partner or tx.seller_partner_id.id != partner.id:
            raise Forbidden()
        volver = _TxPortal._reception_back(
            post, "/marketplace/purchases/%s/traceability#segunda-opinion" % tx.uuid_ref)
        nueva = request.env["res.partner"].sudo().resolve_ref(post.get("verifier_ref") or "")
        try:
            with request.env.cr.savepoint():
                if not nueva:
                    raise ValidationError(_("Elige la verificadora de la segunda opinión."))
                tx.action_request_second_opinion(nueva, actor=partner)
        except (ValidationError, AccessError, UserError) as e:
            return request.redirect(_TxPortal._with_params(volver, error="second_opinion",
                                                           message=flash_message(_msg(e))))
        return request.redirect(_TxPortal._with_params(volver, saved="second_opinion"))
