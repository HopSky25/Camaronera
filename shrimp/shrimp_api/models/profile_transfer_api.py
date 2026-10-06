"""Transferencias internas entre perfiles de la misma cuenta, en la API:

* evento de webhook ``lot.profile_transferred`` (solo para la propia cuenta);
* bloque ``profile_transfers`` en la trazabilidad pública (sin precios: fecha,
  empresa, perfil de origen y de destino, cantidad).
"""

from odoo import _, api, models

from .webhook import register_event

register_event("lot.profile_transferred",
               "Producto movido entre perfiles de tu cuenta (transferencia interna)",
               "profile_transfer", lambda rec, p: rec.partner_id == p, check_access=False)


class ShrimpProfileTransferApi(models.Model):
    _inherit = "shrimp.profile.transfer"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env["shrimp.webhook.delivery"].sudo()._emit("lot.profile_transferred", records)
        return records


class ShrimpTransactionProfileTrace(models.Model):
    _inherit = "shrimp.transaction"

    def _public_traceability_data(self):
        data = super()._public_traceability_data()
        tx = self.sudo().with_context(shrimp_public_tz=True)
        label = self.env["res.partner"]._shrimp_type_label
        partner_tz = tx.buyer_partner_id
        pasos = []
        for item in tx.shrimp_profile_transfers():
            fecha = tx.shrimp_local_date(item["date"], partner_tz)
            pasos.append({
                "date": fecha.isoformat() if fecha else None,
                "company": item["company"].commercial_partner_id.name or item["company"].name,
                "from_role": item["from_role"],
                "from_role_label": label(item["from_role"]),
                "to_role": item["to_role"],
                "to_role_label": label(item["to_role"]),
                "label": item["label"],
                "same_company": True,
                "qty": round(item["qty"] or 0.0, 2),
                "uom": item["uom"].name if item["uom"] else None,
            })
        data["profile_transfers"] = pasos
        return data
