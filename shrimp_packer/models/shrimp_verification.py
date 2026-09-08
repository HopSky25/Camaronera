from odoo import api, fields, models, _


class ShrimpVerification(models.Model):
    _name = "shrimp.verification"
    _inherit = "shrimp.verification"

    # ------------------------------------------------------------------
    # La contraoferta, anclada a la lista de precios del comprador
    # ------------------------------------------------------------------
    # Cuando el producto no cumple lo publicado, el comprador puede proponer
    # otro precio. Hoy escribe un número a mano y el vendedor lo toma o lo
    # deja: es un regateo.
    #
    # Pero el comprador es una empacadora, y la empacadora publica lo que paga
    # por cada talla. Si el verificador midió que el lote es 31/35 y no 26/30,
    # el precio de la 31/35 en la lista de ESA empacadora es un número que ella
    # misma firmó y que el vendedor ya recibió. Proponer ese precio convierte
    # la renegociación en aritmética sobre un documento aceptado.
    def precio_sugerido_por_lista(self):
        """El precio de la talla realmente verificada, según la lista vigente
        del comprador. Devuelve {} si no se puede fundamentar."""
        self.ensure_one()
        tx = self.transaction_id
        comprador = self.buyer_partner_id
        producto = tx.product_id
        if not comprador or comprador.shrimp_user_type != "empacadora":
            return {}
        if self.scope != "adult" or not self.line_ids or not producto.presentation:
            return {}

        # La talla que domina el lote verificado: es el mismo criterio que usa
        # cumple_lo_publicado para decidir si el producto cumplió.
        dominante = max(self.line_ids, key=lambda l: l.weight_lb or 0.0)
        codigo = (dominante.size_code or "").strip()
        if not codigo:
            return {}

        talla = self.env["shrimp.size.grade"].sudo().search([
            ("name", "=", codigo),
            ("presentation", "=", producto.presentation),
        ], limit=1)
        if not talla:
            # El verificador escribe el código tal como llega del parte de
            # planta y el catálogo no siempre lo cubre. Sin talla en catálogo
            # no hay renglón que citar, y es mejor no sugerir nada que sugerir
            # un precio de otra talla.
            return {"sin_talla": codigo}

        L = self.env["shrimp.price.list"].sudo()
        lista = L.visibles_para(self.seller_partner_id).filtered(
            lambda l: l.issuer_partner_id == comprador)[:1]
        if not lista:
            return {}

        lineas = lista.line_ids.filtered(
            lambda l: l.size_grade_id == talla
            and l.presentation == producto.presentation
            and (producto.presentation != "cola" or l.channel == "directa"))
        if not lineas:
            return {"sin_renglon": talla.name, "lista": lista}

        # Con varias calidades para la misma talla se toma la que el verificador
        # midió, no la más baja. El renglón tiene que corresponder a lo que
        # realmente se inspeccionó: si el lote salió clase A, citar el precio de
        # la B sería usar el informe para pagar menos, y eso destruye justo lo
        # que hace útil a la verificación.
        clase = (dominante.quality_class or "a").lower()
        preferencia = {"a": ["a", "ab"], "b": ["b", "ab"], "c": ["c", "ab"]}.get(
            clase, ["ab", "a"])
        linea = next(
            (l for cal in preferencia for l in lineas if l.quality == cal),
            max(lineas, key=lambda l: l.price))
        precio_lista = linea.price
        qty = tx.transaction_qty or 0.0

        # La lista puede cotizar en otra unidad que la del lote.
        unidad_lote = producto._clave_uom() if hasattr(producto, "_clave_uom") else False
        precio_en_unidad_del_lote = precio_lista
        if unidad_lote and unidad_lote != linea.uom:
            from odoo.addons.shrimp_packer.models.shrimp_product import LB_POR_KG
            precio_en_unidad_del_lote = (
                precio_lista / LB_POR_KG if linea.uom == "kg" else precio_lista * LB_POR_KG)

        return {
            "talla_publicada": producto.size_grade_id.name or "",
            "talla_verificada": talla.name,
            "misma_talla": talla == producto.size_grade_id,
            "precio": precio_en_unidad_del_lote,
            "precio_lista": precio_lista,
            "uom_lista": linea.uom,
            "uom_lote": unidad_lote,
            "calidad": linea.quality,
            "clase_verificada": clase,
            "lista": lista,
            "empacadora": comprador,
            "total": precio_en_unidad_del_lote * qty,
            "precio_pactado": tx.price_unit or 0.0,
            # Solo tiene sentido proponerlo si está por debajo de lo pactado:
            # la contraoferta es para ajustar a la baja.
            "aplicable": bool(tx.price_unit) and precio_en_unidad_del_lote < tx.price_unit,
        }
