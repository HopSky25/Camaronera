from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare

from .shrimp_transaction import VERIFICATION_HOLD_STATES


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
        help="Cantidad comprometida por compras verificadas que todavía no se "
             "cerraron: pendientes de veredicto o esperando la firma de las partes.",
    )

    @api.depends("seller_role")
    def _compute_requires_verification(self):
        for rec in self:
            # Obligatoria solo en camarón adulto. En larvas se puede pedir, pero
            # no se impone: el informe ademas es distinto (a un nauplio no se le
            # mide metabisulfito, sabor ni talla comercial).
            # Varios perfiles: lo decide el ROL del lote, no el perfil con el
            # que navega su vendedor (antes, cambiar de perfil activo habría
            # recalculado todos sus lotes).
            es_adulto = rec.seller_role == "camaronera"
            rec.requires_verification = es_adulto
            rec.verification_scope = "adult" if es_adulto else "larvae"

    @api.depends(
        "stock_lot_ids.available_qty", "stock_lot_ids.state", "stock_lot_ids.owner_id",
        "seller_partner_id", "check_request_ids.qty", "check_request_ids.state",
        "transaction_ids.state", "transaction_ids.transaction_qty",
    )

    def _compute_available_qty(self):
        """Amplía el cálculo base descontando las compras verificadas abiertas.

        Sin esto el mismo lote podría venderse dos veces mientras el verificador
        está en campo, porque los lotes no se consumen hasta que las partes
        firman. La reserva cubre los DOS estados de espera: tras un veredicto
        favorable la compra pasa a "esperando aceptación" y sigue sin consumir
        lotes; si solo se contaba "pendiente de verificación", en ese tramo el
        lote volvía a aparecer como disponible y se podía vender otra vez.
        """
        super()._compute_available_qty()
        for rec in self:
            pending = sum(rec.transaction_ids.filtered(
                lambda t: t.state in VERIFICATION_HOLD_STATES).mapped("transaction_qty"))
            rec.pending_verification_qty = pending
            rec.reserved_qty = (rec.reserved_qty or 0.0) + pending
            rec.available_qty = max(0.0, (rec.available_qty or 0.0) - pending)

    def _shrimp_requires_verification(self):
        self.ensure_one()
        return bool(self.requires_verification)

    # ------------------------------------------------------------------
    # Compra sujeta a verificación
    # ------------------------------------------------------------------
    VERIFICATION_MODES = ("platform", "declared")

    def start_verified_purchase(self, buyer_partner, qty, verifier_partner=None, fee=0.0,
                                mode="platform", declared_vals=None):
        """Graba la compra y la deja pendiente de verificación en campo.

        A diferencia de execute_purchase_flow, aquí NO se consumen lotes: la
        transacción queda en 'pending_verification' reservando la cantidad, y
        los lotes solo se mueven cuando el verificador aprueba y el comprador
        concluye la compra.

        `mode` (lo elige el comprador; no hay «no aplica»):
          * "platform": verificadora acreditada de la plataforma
            (`verifier_partner`), con su honorario `fee`.
          * "declared": verificación declarada por las partes. Sin verificadora
            ni honorario; el informe lo cargan comprador o vendedor y la otra
            parte lo confirma. `declared_vals` admite los datos opcionales de
            la verificadora externa (declared_source, external_verifier_name,
            external_verifier_vat).
        """
        self.ensure_one()
        if mode not in self.VERIFICATION_MODES:
            raise ValidationError(_(
                "Elige cómo se verifica la compra: con la verificadora de la "
                "plataforma o con una verificación declarada por las partes."))

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

        # El mismo filtro de rol que la compra directa (semillero→laboratorio,
        # laboratorio→camaronera, camaronera→empacadora). Antes este camino
        # solo miraba que el comprador no fuera el vendedor: cualquier rol
        # podía comprar cualquier lote si elegía "con verificación".
        motivo = self.motivo_no_comprable(buyer_partner)
        if motivo:
            raise ValidationError(motivo)

        if mode == "declared":
            # Sin verificadora de la plataforma: los controles de conflicto de
            # interés del verificador (misma entidad, acreditación) no
            # aplican, y se saltan SOLO en este modo. La garantía aquí es que
            # la otra parte tiene que confirmar el informe declarado.
            if verifier_partner:
                raise ValidationError(_(
                    "En la verificación declarada por las partes no se elige "
                    "verificadora de la plataforma."))
            fee = 0.0
        else:
            if not verifier_partner or not verifier_partner._shrimp_has_role("verificador"):
                raise ValidationError(_("Debes seleccionar un verificador acreditado."))
            # Conflicto de interés: una cuenta puede ser verificadora y además
            # comprar o vender, pero nunca verificar una compra en la que ella
            # (o su empresa) es parte.
            if verifier_partner.shrimp_same_entity(buyer_partner, self.seller_partner_id):
                raise ValidationError(_(
                    "El verificador no puede ser el comprador ni el vendedor (ni su "
                    "misma empresa): elige un verificador independiente."))
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

        # El mismo precio que la compra directa: antes este camino usaba
        # self.price y la directa price_for_partner(); si alguna vez vuelve un
        # precio por cliente, los dos caminos cobrarían distinto.
        unit_price = self.price_for_partner(buyer_partner)

        tx = self.env["shrimp.transaction"].create({
            "transaction_type": tx_type,
            "product_id": self.id,
            "seller_partner_id": self.seller_partner_id.id,
            "buyer_partner_id": buyer_partner.id,
            "location": self.location,
            "state": "pending_verification",
            "needs_verification": True,
            "transaction_qty": qty,
            "price_unit": unit_price,
            "amount_total": qty * unit_price,
            "desired_qty": qty,
            "desired_date": self.expected_delivery_date or fields.Date.context_today(self),
            "code": self.name,
            # semillero→laboratorio exige cantidad vendida (_check_qty_by_type);
            # sin esto la compra verificada de larva de semillero nunca se
            # podía grabar.
            "sold_qty": qty if tx_type == "semillero_to_laboratorio" else 0.0,
        })

        ver_vals = {
            "transaction_id": tx.id,
            "verification_mode": mode,
            "verifier_partner_id": verifier_partner.id if mode == "platform" else False,
            "batch_code": self.batch_code or False,
            "pond_id": self.origin_pond_id.id or False,
            "facility_id": self.origin_facility_id.id or False,
            "presentation": self.presentation or False,
            "grams_farm": self.avg_size_mg / 1000.0 if self.avg_size_mg else 0.0,
            "fee": fee or 0.0,
        }
        if mode == "declared":
            ver_vals["state"] = "declared_draft"
            for campo in ("declared_source", "external_verifier_name", "external_verifier_vat"):
                if (declared_vals or {}).get(campo):
                    ver_vals[campo] = declared_vals[campo]
            if ver_vals.get("declared_source") not in (None, "external", "self"):
                raise ValidationError(_("Origen de la verificación declarada no válido."))
        verification = self.env["shrimp.verification"].create(ver_vals)

        # Refresca el disponible para que la reserva se vea de inmediato.
        self._compute_available_qty()

        # Primer cobro de la plataforma: el honorario de la verificación, al
        # comprador que la contrata, en el momento de iniciar la compra.
        verification._register_fee_charge()

        return {"transaction": tx, "verification": verification}
