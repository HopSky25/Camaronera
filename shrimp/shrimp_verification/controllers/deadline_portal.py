"""Portal: verificación vencida (models/shrimp_verification_deadline.py).

  POST /marketplace/purchases/<tx>/verifier/assign   el comprador cambia a otra
                                                      verificadora acreditada

Cancelar sin costo va por POST /marketplace/purchases/<tx>/cancel (servicio
único de cancelación, shrimp_marketplace/controllers/delay_portal.py).
"""
from odoo import http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.tools import ustr
from werkzeug.exceptions import Forbidden

from odoo.addons.shrimp_marketplace.controllers.transaction_portal import (
    ShrimpTransactionPortalController as _TxPortal)
from odoo.addons.shrimp_user_registry.controllers.main import current_partner, flash_message


class ShrimpVerificationDeadlinePortal(http.Controller):

    @http.route("/marketplace/purchases/<tx_ref>/verifier/assign", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_switch_verifier(self, tx_ref, **post):
        partner = current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx or not partner or tx.buyer_partner_id.id != partner.id:
            raise Forbidden()
        volver = _TxPortal._reception_back(
            post, "/marketplace/purchases/%s/traceability#verificacion-plazo" % tx.uuid_ref)
        nueva = request.env["res.partner"].sudo().resolve_ref(post.get("verifier_ref") or "")
        try:
            with request.env.cr.savepoint():
                if not nueva:
                    raise ValidationError("Elige la nueva verificadora.")
                tx.action_switch_verifier(nueva, actor=partner)
        except (ValidationError, AccessError) as e:
            msg = e.args[0] if e.args else ustr(e)
            return request.redirect(_TxPortal._with_params(volver, error="verdeadline",
                                                           message=flash_message(msg)))
        return request.redirect(_TxPortal._with_params(volver, saved="verifier_switched"))
