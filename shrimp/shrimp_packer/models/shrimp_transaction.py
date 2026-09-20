from odoo import api, models, _
from odoo.exceptions import ValidationError


class ShrimpTransaction(models.Model):
    _inherit = "shrimp.transaction"

    # La cadena valida quién puede comprar en cada eslabón: el semillero vende
    # al laboratorio, el laboratorio a la camaronera. La última pata quedaba
    # abierta —"Camaronera → Comprador", compraba cualquiera— porque no existía
    # el rol de empacadora. Ahora que existe, se cierra.
    #
    # Va como constrains propia y no editando la de shrimp_marketplace para no
    # chocar con el desarrollo que corre en paralelo sobre ese módulo.
    #
    # Se declara sobre estos tres campos a propósito: así solo se comprueba al
    # crear la compra o al cambiar las partes, y no en cada cambio de estado.
    # Las compras viejas —hechas cuando no había rol de empacadora— siguen
    # funcionando: se verifican, se aceptan y se cierran sin tropezar con esto.
    @api.constrains("transaction_type", "seller_partner_id", "buyer_partner_id")
    def _check_buyer_is_packer(self):
        for rec in self:
            if rec.transaction_type != "camaronera_to_buyer":
                continue
            if rec.seller_partner_id.shrimp_user_type != "camaronera":
                raise ValidationError(_(
                    "Quien vende camarón adulto debe ser una camaronera."))
            if rec.buyer_partner_id.shrimp_user_type != "empacadora":
                raise ValidationError(_(
                    "El camarón adulto solo lo puede comprar una empacadora. "
                    "«%(quien)s» está registrada como %(tipo)s."
                ) % {
                    "quien": rec.buyer_partner_id.name or "",
                    "tipo": dict(
                        rec.buyer_partner_id._fields["shrimp_user_type"]
                        ._description_selection(self.env)
                    ).get(rec.buyer_partner_id.shrimp_user_type, _("sin tipo")),
                })
