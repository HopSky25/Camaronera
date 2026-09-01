from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare


class ShrimpProduct(models.Model):
    _name = "shrimp.product"
    _inherit = "shrimp.product"

    requires_verification = fields.Boolean(
        string="Requiere verificación en campo",
        compute="_compute_requires_verification",
        store=True,
        help="Obligatoria en camarón adulto (vendedor camaronera). En larvas la "
             "verificación existe igual, pero solo si el comprador la solicita.",
    )

    verification_scope = fields.Selection(
        [("adult", "Camarón adulto"), ("larvae", "Larva")],
        string="Alcance de la verificación",
        compute="_compute_requires_verification",
        store=True,
        help="Determina qué analiza el verificador: al camarón adulto se le hacen "
             "los cinco análisis; a la larva se le verifica cantidad, supervivencia, "
             "tamaño y estado sanitario.",
    )

    pending_verification_qty = fields.Float(
        string="Cantidad en verificación",
        compute="_compute_available_qty",
        store=True,
        help="Cantidad comprometida por compras pendientes de verificación.",
    )

    @api.depends("seller_partner_id.shrimp_user_type")
    def _compute_requires_verification(self):
        for rec in self:
            # Obligatoria solo en camarón adulto. En larvas se puede pedir, pero
            # no se impone: el informe ademas es distinto (a un nauplio no se le
            # mide metabisulfito, sabor ni talla comercial).
            es_adulto = rec.seller_partner_id.shrimp_user_type == "camaronera"
            rec.requires_verification = es_adulto
            rec.verification_scope = "adult" if es_adulto else "larvae"

    @api.depends(
        "stock_lot_ids.available_qty", "stock_lot_ids.state", "stock_lot_ids.owner_id",
        "seller_partner_id", "check_request_ids.qty", "check_request_ids.state",
        "transaction_ids.state", "transaction_ids.transaction_qty",
    )
    def _compute_available_qty(self):
        """Amplía el cálculo base descontando las compras pendientes de verificación.

        Sin esto el mismo lote podría venderse dos veces mientras el verificador
        está en campo, porque los lotes no se consumen hasta el veredicto.
        """
        super()._compute_available_qty()
        for rec in self:
            pending = sum(rec.transaction_ids.filtered(
                lambda t: t.state == "pending_verification").mapped("transaction_qty"))
            rec.pending_verification_qty = pending
            rec.reserved_qty = (rec.reserved_qty or 0.0) + pending
            rec.available_qty = max(0.0, (rec.available_qty or 0.0) - pending)

    # ------------------------------------------------------------------
    # Compra sujeta a verificación
    # ------------------------------------------------------------------
    def start_verified_purchase(self, buyer_partner, qty, verifier_partner, fee=0.0):
        """Graba la compra y la deja pendiente de verificación en campo.

        A diferencia de execute_purchase_flow, aquí NO se consumen lotes: la
        transacción queda en 'pending_verification' reservando la cantidad, y
        los lotes solo se mueven cuando el verificador aprueba y el comprador
        concluye la compra.
        """
        self.ensure_one()

        if not self.active:
            raise ValidationError(_("El producto no está activo."))
        if self.state != "published":
            raise ValidationError(_("Solo se pueden comprar productos publicados."))
        if buyer_partner.id == self.seller_partner_id.id:
            raise ValidationError(_("No puedes comprar tu propio producto."))
        if qty <= 0:
            raise ValidationError(_("La cantidad debe ser mayor a 0."))
        if float_compare(qty, self.available_qty, precision_digits=6) == 1:
            raise ValidationError(_("La cantidad solicitada supera el stock disponible."))

        if not verifier_partner or verifier_partner.shrimp_user_type != "verificador":
            raise ValidationError(_("Debes seleccionar un verificador acreditado."))
        # Se revalida en el servidor: el formulario ya solo ofrece acreditados,
        # pero el POST es falsificable.
        if not verifier_partner.verifier_is_accredited:
            raise ValidationError(_(
                "El verificador «%s» no tiene una acreditación aprobada y vigente."
            ) % verifier_partner.name)
        if verifier_partner.id in (buyer_partner.id, self.seller_partner_id.id):
            raise ValidationError(
                _("El verificador no puede ser el comprador ni el vendedor."))

        tx_type = self._get_tx_type_by_partners(self.seller_partner_id, buyer_partner)

        tx = self.env["shrimp.transaction"].create({
            "transaction_type": tx_type,
            "product_id": self.id,
            "seller_partner_id": self.seller_partner_id.id,
            "buyer_partner_id": buyer_partner.id,
            "location": self.location,
            "state": "pending_verification",
            "needs_verification": True,
            "transaction_qty": qty,
            "price_unit": self.price,
            "amount_total": qty * self.price,
            "desired_qty": qty,
            "desired_date": self.expected_delivery_date or fields.Date.context_today(self),
            "code": self.name,
        })

        verification = self.env["shrimp.verification"].create({
            "transaction_id": tx.id,
            "verifier_partner_id": verifier_partner.id,
            "batch_code": self.batch_code or False,
            "pond_id": self.origin_pond_id.id or False,
            "facility_id": self.origin_facility_id.id or False,
            "presentation": self.presentation or False,
            "grams_farm": self.avg_size_mg / 1000.0 if self.avg_size_mg else 0.0,
            "fee": fee or 0.0,
        })

        # Refresca el disponible para que la reserva se vea de inmediato.
        self._compute_available_qty()

        return {"transaction": tx, "verification": verification}
