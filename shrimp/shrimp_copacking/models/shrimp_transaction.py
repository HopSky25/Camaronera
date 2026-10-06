from odoo import _, api, fields, models


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
