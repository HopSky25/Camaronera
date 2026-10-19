"""LOPDP (hueco #7): lo que el marketplace añade a la copia de los datos y a
la anonimización de una cuenta (ver shrimp_user_registry/models/
shrimp_data_request.py)."""
from odoo import _, models


def _d(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class ResPartnerLopdpMarketplace(models.Model):
    _inherit = "res.partner"

    def _shrimp_lopdp_export_extra(self, datos):
        datos = super()._shrimp_lopdp_export_extra(datos)
        ids = self._shrimp_lopdp_partner_ids()
        env = self.env
        Fac = env["shrimp.partner.facility"].sudo().with_context(active_test=False)
        datos["facilities"] = [{
            "name": f.name, "code": f.code, "type": f.facility_type, "address": f.address,
            "city": f.city, "province": f.province, "country": f.country_id.code or None,
            "latitude": f.latitude, "longitude": f.longitude, "notes": f.notes, "active": f.active}
            for f in Fac.search([("partner_id", "in", ids)])]
        Pond = env["shrimp.partner.pond"].sudo().with_context(active_test=False)
        datos["ponds"] = [{
            "name": p.name, "code": p.code, "facility": p.facility_id.name, "location": p.location,
            "type": p.pond_type, "notes": p.notes}
            for p in Pond.search([("partner_id", "in", ids)])]
        Prod = env["shrimp.product"].sudo().with_context(active_test=False)
        datos["products"] = [{
            "reference": p.uuid_ref, "name": p.name, "state": p.state, "active": p.active,
            "price": p.price, "initial_qty": p.initial_qty, "available_qty": p.available_qty,
            "uom": p.uom_id.display_name, "published_date": _d(p.published_date)}
            for p in Prod.search([("seller_partner_id", "in", ids)])]
        Tx = env["shrimp.transaction"].sudo().with_context(active_test=False)
        txs = Tx.search(["|", ("buyer_partner_id", "in", ids), ("seller_partner_id", "in", ids)])
        datos["transactions"] = [{
            "reference": t.name, "role": "buyer" if t.buyer_partner_id.id in ids else "seller",
            "counterparty": (t.seller_partner_id if t.buyer_partner_id.id in ids
                             else t.buyer_partner_id).name,
            "product": t.product_id.display_name,
            "quantity": t.transaction_qty,
            "state": t.state, "date": _d(t.create_date)}
            for t in txs]
        return datos

    def _shrimp_lopdp_anonymize_extra(self, dry_run=False):
        informe = super()._shrimp_lopdp_anonymize_extra(dry_run=dry_run)
        ids = self._shrimp_lopdp_partner_ids()
        Fac = self.env["shrimp.partner.facility"].sudo().with_context(active_test=False)
        Pond = self.env["shrimp.partner.pond"].sudo().with_context(active_test=False)
        facs = Fac.search([("partner_id", "in", ids)])
        ponds = Pond.search([("partner_id", "in", ids)])
        informe.append(_("Instalaciones sin dirección ni coordenadas GPS: %(f)s; piscinas sin "
                         "ubicación: %(p)s") % {"f": len(facs), "p": len(ponds)})
        if not dry_run:
            if facs:
                facs.write({"address": False, "latitude": 0.0, "longitude": 0.0, "notes": False})
            if ponds:
                ponds.write({"location": False, "notes": False})
        return informe
