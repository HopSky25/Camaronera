"""El estado del comprobante SRI vuelve al cobro de la plataforma.

ec.sri.document cambia de estado con ``_set()`` (se salta los overrides de
write), así que el gancho va ahí, igual que el webhook de shrimp_api_sri.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class EcSriDocument(models.Model):
    _inherit = "ec.sri.document"

    def _set(self, vals):
        before = {doc.id: doc.state for doc in self}
        res = super()._set(vals)
        if "state" in vals:
            changed = self.filtered(
                lambda d: before.get(d.id) != d.state and d.move_id
                and d.document_type in ("01", "04"))
            if changed:
                try:
                    with self.env.cr.savepoint():
                        self.env["shrimp.charge"].sudo()._shrimp_on_sri_state(changed)
                except Exception:  # noqa: BLE001 - nunca frena el proceso del SRI
                    _logger.exception("Cola SRI: no se pudo reflejar en el cobro el estado "
                                      "de %s", changed.ids)
        return res
