"""Evento invoice.authorized.

El comprobante cambia de estado con ``_set()``, que llama a
``super(SriDocument, self).write`` y se salta cualquier override de write
puesto por encima. Por eso el gancho va en ``_set`` y no en ``write``.
"""

from odoo import models

from odoo.addons.shrimp_api.models.webhook import register_event


def _invoice_audience(doc, partner):
    receptor = doc.partner_id.commercial_partner_id
    return bool(receptor) and partner.commercial_partner_id == receptor


register_event("invoice.authorized", "Comprobante electrónico autorizado por el SRI", "invoice",
               _invoice_audience, check_access=False, ident=lambda doc: doc.access_key)


class EcSriDocument(models.Model):
    _inherit = "ec.sri.document"

    def _set(self, vals):
        before = {doc.id: doc.state for doc in self}
        res = super()._set(vals)
        if vals.get("state") == "authorized":
            authorized = self.filtered(lambda d: before.get(d.id) != "authorized")
            if authorized:
                self.env["shrimp.webhook.delivery"].sudo()._emit("invoice.authorized", authorized)
        return res
