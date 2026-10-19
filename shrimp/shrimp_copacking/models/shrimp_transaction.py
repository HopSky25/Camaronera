from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ShrimpTransaction(models.Model):
    _inherit = "shrimp.transaction"

    # En maquila la propiedad nunca cambia de manos, así que el empaque no es
    # parte de la compraventa; pero sí de la TRAZABILIDAD de la compra cuyo
    # camarón se empacó. El vínculo es explícito (orden.transaction_id):
    # antes se deducía por producto y cada orden salía en todas las compras de
    # ese producto, también en las de otros compradores.
    copack_order_all_ids = fields.One2many(
        "shrimp.copack.order", "transaction_id", string="Órdenes de empaque de esta compra")
    copack_order_ids = fields.Many2many(
        "shrimp.copack.order", string="Empaques del lote",
        compute="_compute_copack_orders",
        help="Órdenes de empaque de esta compra que constan en la trazabilidad: "
             "empaque hecho y sin disputa abierta.")

    @api.depends("copack_order_all_ids.es_facturable", "copack_order_all_ids.packed_date",
                 "copack_order_all_ids.state", "copack_order_all_ids.self_packing")
    def _compute_copack_orders(self):
        Order = self.env["shrimp.copack.order"].sudo()
        for rec in self:
            tx = rec.sudo()
            # 1) Empaques de lo que compró el comprador (orden con esta compra).
            ordenes = tx.copack_order_all_ids
            if tx.id:
                # 2) Empaques que están en la cadena física (el lote vendido es
                #    un lote empacado: la camaronera empacó su cosecha y vendió
                #    el producto empacado) o que el comprador hizo con su lote.
                data = tx.get_full_traceability_data()
                ordenes |= data["moves"].mapped("copack_order_id")
                # 3) Ventas anteriores al lote empacado (sus movimientos no
                #    dicen de qué lote salieron): el vendedor empacó ESE
                #    producto antes de la venta.
                legado = tx.stock_move_ids and not tx.stock_move_ids.filtered("lot_id")
                if tx.create_date and legado:
                    ordenes |= Order.search([
                        ("client_partner_id", "=", tx.seller_partner_id.id),
                        ("product_id", "=", tx.product_id.id),
                        ("packed_date", "<=", tx.create_date),
                    ])
            # Solo trabajos con el empaque hecho Y sin disputa abierta: el
            # certificado no acredita un empaque que una parte no aceptó.
            # El empaque propio (sin acta ni cobro) consta una vez cerrado.
            rec.copack_order_ids = ordenes.filtered(
                lambda o: o._shrimp_consta_en_trazabilidad()).sorted(
                lambda o: o.packed_date or o.create_date)

    # ------------------------------------------------------------------
    # D-34: la compra de origen se cancela
    # ------------------------------------------------------------------
    def _shrimp_copack_cancel_blockers(self):
        """Órdenes de empaque de esta compra con el camarón ya en planta (o
        empacado): con ellas viva la compra no se cancela, porque el camarón
        físico está en la planta del maquilador y el empaque ya se hizo o está
        en marcha."""
        self.ensure_one()
        return self.sudo().copack_order_all_ids.filtered(
            lambda o: o.state in ("received", "packed", "signed", "closed"))

    def _shrimp_cancel_apply(self, *args, **kwargs):
        """Antes de cancelar la compra: bloquea si hay camarón en una planta de
        empaque; si no, cancela en cascada (con aviso) las solicitudes y las
        órdenes de empaque todavía sin recibir."""
        for tx in self:
            bloqueo = tx._shrimp_copack_cancel_blockers()
            if bloqueo:
                raise ValidationError(_(
                    "La compra %(c)s no se puede cancelar: su camarón ya está en la planta de "
                    "empaque (orden %(o)s). Primero hay que resolver la orden de empaque "
                    "(acta de devolución o cancelación aprobada por la plataforma).") % {
                        "c": tx.name or "", "o": ", ".join(bloqueo.mapped("name"))})
        res = super()._shrimp_cancel_apply(*args, **kwargs)
        for tx in self:
            tx.sudo()._shrimp_copack_cascade_cancel()
        return res

    def _shrimp_copack_cascade_cancel(self):
        self.ensure_one()
        motivo = _("Se canceló la compra de origen %s.") % (self.name or "")
        Order = self.env["shrimp.copack.order"].sudo()
        Request = self.env["shrimp.copack.request"].sudo()
        ordenes = Order.search([("transaction_id", "=", self.id), ("state", "=", "confirmed")])
        for orden in ordenes:
            orden._shrimp_do_cancel("system", motivo, reopen=False)
        solicitudes = Request.search([("transaction_id", "=", self.id),
                                      ("state", "in", ("draft", "published", "expired"))])
        for sol in solicitudes:
            sol.action_cancel(reason=motivo)
            sol._cpk_notify(
                sol.client_partner_id, _("Solicitud %s cancelada") % sol.name,
                _("Tu solicitud de empaque %(s)s se canceló porque %(m)s") % {
                    "s": sol.name, "m": motivo[:1].lower() + motivo[1:]})
            for oferta in sol.offer_ids.filtered(lambda o: o.closed_reason == "request_cancelled"):
                oferta._cpk_notify(
                    oferta.copacker_partner_id, _("Solicitud %s cancelada") % sol.name,
                    _("La solicitud %(s)s se canceló (%(m)s): tu oferta queda descartada.") % {
                        "s": sol.name, "m": motivo},
                    url=sol._cpk_portal_url(oferta.copacker_partner_id))
        return True

    def _shrimp_chain_parties(self, data=None):
        """Empaques sin movimiento de inventario (anteriores a que el empaque
        moviera el lote): la planta se añade después de su cliente."""
        seq = super()._shrimp_chain_parties(data)
        presentes = {p.id for p, _rol in seq}
        for order in self.sudo().copack_order_ids:
            maq = order.copacker_partner_id
            if not maq or maq.id in presentes:
                continue
            idx = [i for i, (p, _rol) in enumerate(seq) if p.id == order.client_partner_id.id]
            if idx:
                seq.insert(idx[-1] + 1, (maq, "maquilador"))
                presentes.add(maq.id)
        return seq

    def _shrimp_chain_service_party(self, move):
        """En la cadena, el empaque añade a la planta que lo hizo."""
        if move.move_type == "packing" and move.copack_order_id:
            return (move.copack_order_id.copacker_partner_id, "maquilador")
        return super()._shrimp_chain_service_party(move)

    def _shrimp_chain_node_detail(self, partner, rol, data):
        """Fila de la cadena del certificado: la planta que empacó (o el
        dueño, si fue empaque propio) dice cuánto empacó y en cuántas cajas."""
        det = super()._shrimp_chain_node_detail(partner, rol, data)
        ordenes = self.sudo().copack_order_ids.filtered(
            lambda o: o.copacker_partner_id == partner
            and (rol == "maquilador" or o.self_packing))
        if not ordenes:
            return det
        partes = []
        for o in ordenes:
            txt = _("Empacó %s") % self.shrimp_fmt_qty(o.packed_lb, "lb")
            if o.boxes:
                txt += " " + _("en %s cajas") % self.shrimp_fmt_num(o.boxes, 0)
            partes.append(txt)
        if rol == "maquilador":
            return {"parts": partes, "date": ordenes[0].packed_date,
                    "ref": ", ".join(ordenes.mapped("name"))}
        det["parts"] = (det.get("parts") or []) + [p[:1].lower() + p[1:] for p in partes]
        det["date"] = det.get("date") or ordenes[0].packed_date
        return det


class ShrimpStockMove(models.Model):
    _inherit = "shrimp.stock.move"

    copack_order_id = fields.Many2one(
        "shrimp.copack.order", string="Orden de empaque", index=True, ondelete="set null")
