"""Fotos, certificados y cantidad de un lote, para todos los canales.

Vivían en el controlador del portal (`product_portal.py`), atados a los
archivos del formulario. La app los necesita por la API con las mismas
reglas, así que el núcleo está aquí y el portal y `shrimp_api` lo llaman:

* Fotos: hasta 20; la primera es la portada. Las fotos se leen en el orden de
  ir.attachment (`id desc`), y para respetar un orden pedido sin cambiar ese
  criterio se recrean las que no quedan con un id mayor que la siguiente
  (`_shrimp_apply_photo_order`).
* Certificados del lote: ligar uno vigente de «mis certificados» del
  vendedor (aprobado, con archivo y sin vencer) o subir uno propio del lote
  (entra pendiente de revisión).
* Cantidad: cambiar `initial_qty` de un lote SIN compras mueve también su
  lote de stock base. Antes el número cambiaba y el disponible no (el portal
  ni lo guardaba y la API dejaba los dos desalineados).
"""

from odoo import _, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare

MAX_PHOTOS = 20


class ShrimpProductMedia(models.Model):
    _inherit = "shrimp.product"

    # ------------------------------------------------------------------
    # Fotos
    # ------------------------------------------------------------------
    def _shrimp_apply_photo_order(self, orden, quitar=None):
        """Deja las fotos del lote en el orden pedido.

        `orden`: lista de ("e", adjunto ya guardado) o ("n", (contenido,
        mimetype, nombre)) para una foto nueva; la primera es la portada.
        `quitar`: adjuntos que salen. Las nuevas que no quepan en el tope se
        descartan (las guardadas se conservan). La foto sustituida o quitada
        se borra solo si ningún otro producto la usa (el lote republicado por
        un comprador comparte las fotos). Devuelve True si cambió algo.
        """
        self.ensure_one()
        quitar = quitar or self.env["ir.attachment"].browse()
        quedan = self.photo_attachment_ids.sudo() - quitar
        recortado, total = [], 0
        for kind, item in orden:
            if kind == "n" and total >= MAX_PHOTOS:
                continue
            recortado.append((kind, item))
            total += 1
        orden = recortado
        if not quitar and all(k == "e" for k, _i in orden) and \
                [i.id for _k, i in orden] == quedan.ids:
            return False  # nada cambia

        Att = self.env["ir.attachment"].sudo()
        final, tope, sustituidas = [], 0, Att.browse()
        for kind, item in reversed(orden):
            if kind == "e" and item.id > tope:
                final.append(item.id)
                tope = item.id
                continue
            if kind == "e":
                datos = {"name": item.name, "raw": item.raw, "mimetype": item.mimetype}
                sustituidas |= item
            else:
                contenido, mime, nombre = item
                datos = {"name": nombre, "raw": contenido, "mimetype": mime}
            att = Att.create({**datos, "type": "binary", "res_model": "shrimp.product",
                              "res_id": self.id, "public": False})
            att.generate_access_token()
            final.append(att.id)
            tope = att.id
        final.reverse()
        self.sudo().write({"photo_attachment_ids": [(6, 0, final)]})
        self._shrimp_unlink_unused_photos(quitar | sustituidas)
        return True

    def _shrimp_unlink_unused_photos(self, atts):
        """Borra fotos que ya no usa ningún producto."""
        if not atts:
            return
        otros = self.env["shrimp.product"].sudo().with_context(
            active_test=False).search([("photo_attachment_ids", "in", atts.ids)])
        (atts - otros.mapped("photo_attachment_ids")).sudo().unlink()

    def shrimp_photos(self):
        """Fotos en su orden (la primera es la portada), con token."""
        self.ensure_one()
        atts = self.sudo().photo_attachment_ids
        sin_token = atts.filtered(lambda a: not a.access_token)
        if sin_token:
            sin_token.generate_access_token()
        return atts

    def shrimp_photo_by_token(self, token):
        self.ensure_one()
        token = str(token or "")
        return self.shrimp_photos().filtered(lambda a: token and a.access_token == token)[:1]

    def shrimp_add_photo(self, content, mimetype, filename, cover=False):
        """Agrega una foto al final (o como portada). ValidationError si ya
        tiene el máximo. Devuelve el adjunto nuevo."""
        self.ensure_one()
        actuales = self.shrimp_photos()
        if len(actuales) >= MAX_PHOTOS:
            raise ValidationError(_("El lote ya tiene %s fotos, el máximo. Quita una para agregar otra.")
                                  % MAX_PHOTOS)
        nueva = ("n", (content, mimetype, filename))
        existentes = [("e", a) for a in actuales]
        self._shrimp_apply_photo_order([nueva] + existentes if cover else existentes + [nueva])
        fotos = self.shrimp_photos()
        return fotos[:1] if cover else fotos[-1:]

    def shrimp_remove_photo(self, att):
        self.ensure_one()
        quedan = self.shrimp_photos() - att
        self._shrimp_apply_photo_order([("e", a) for a in quedan], quitar=att)

    def shrimp_reorder_photos(self, ordenadas):
        """`ordenadas`: adjuntos del lote en el orden nuevo; las que no
        vengan van al final, en su orden actual."""
        self.ensure_one()
        actuales = self.shrimp_photos()
        orden = [a for a in ordenadas if a in actuales]
        orden += [a for a in actuales if a not in orden]
        self._shrimp_apply_photo_order([("e", a) for a in orden])

    # ------------------------------------------------------------------
    # Certificados
    # ------------------------------------------------------------------
    def shrimp_valid_account_certificates(self, partner=None):
        """«Mis certificados» del vendedor que se pueden ligar al lote:
        aprobados, con archivo y sin vencer."""
        partner = partner or self.seller_partner_id
        today = fields.Date.context_today(self)
        return self.env["shrimp.user.certificate.line"].sudo().search([
            ("partner_id", "=", partner.id),
            ("status", "=", "approved"),
            ("file_attachment_id", "!=", False),
            "|", ("expiry_date", "=", False), ("expiry_date", ">=", today),
        ], order="id desc")

    def shrimp_link_account_certificate(self, user_line, partner=None):
        """Liga un certificado de la cuenta del vendedor al lote (hereda su
        aprobación). ValidationError si no se puede; si ya estaba ligado,
        devuelve la línea existente."""
        self.ensure_one()
        partner = partner or self.seller_partner_id
        user_line = user_line.sudo()
        today = fields.Date.context_today(self)
        if not user_line or user_line.partner_id != partner:
            raise ValidationError(_("Ese certificado no es de tu cuenta."))
        if user_line.status != "approved":
            raise ValidationError(_("El certificado «%s» todavía no está aprobado.")
                                  % user_line.certificate_id.name)
        if user_line.expiry_date and user_line.expiry_date < today:
            raise ValidationError(_("El certificado «%s» está vencido.") % user_line.certificate_id.name)
        if not user_line.file_attachment_id:
            raise ValidationError(_("El certificado «%s» no tiene archivo.") % user_line.certificate_id.name)
        Line = self.env["shrimp.product.certificate.line"].sudo()
        existente = Line.search([("product_id", "=", self.id),
                                 ("source_user_certificate_line_id", "=", user_line.id)], limit=1)
        if existente:
            return existente
        line = Line.create({
            "product_id": self.id,
            "source_user_certificate_line_id": user_line.id,
            "certificate_id": user_line.certificate_id.id,
            "number": user_line.certificate_number or False,
            "issue_date": user_line.issue_date or False,
            "expiry_date": user_line.expiry_date or False,
            "attachment_id": user_line.file_attachment_id.id,
        })
        self.sudo().write({"cert_attachment_ids": [(4, user_line.file_attachment_id.id)]})
        return line

    def _shrimp_cert_attachment(self, content, mimetype, filename, catalogo):
        att = self.env["ir.attachment"].sudo().create({
            "name": "cert_%s_%s" % (catalogo.id, filename),
            "type": "binary",
            "raw": content,
            "mimetype": mimetype,
            "res_model": "shrimp.product",
            "res_id": self.id,
            "public": False,
        })
        att.generate_access_token()
        return att

    def shrimp_add_own_certificate(self, catalogo, attachment, number=False, issue_date=False,
                                   expiry_date=False):
        """Certificado propio del lote con su archivo (`attachment`, ya
        validado). Entra pendiente de revisión."""
        self.ensure_one()
        line = self.env["shrimp.product.certificate.line"].sudo().create({
            "product_id": self.id,
            "certificate_id": catalogo.id,
            "number": number or False,
            "issue_date": issue_date or False,
            "expiry_date": expiry_date or False,
            "attachment_id": attachment.id,
        })
        self.sudo().write({"cert_attachment_ids": [(4, attachment.id)]})
        return line

    def shrimp_remove_certificate_lines(self, lines):
        """Quita líneas de certificado DEL LOTE (y sus archivos del m2m)."""
        self.ensure_one()
        lines = lines.sudo().filtered(lambda l: l.product_id == self)
        if not lines:
            return
        att_ids = lines.mapped("attachment_id").ids
        if att_ids:
            self.sudo().write({"cert_attachment_ids": [(3, aid) for aid in att_ids]})
        lines.unlink()

    # ------------------------------------------------------------------
    # Cantidad
    # ------------------------------------------------------------------
    def write(self, vals):
        cambia_qty = self.browse()
        if "initial_qty" in vals and not self.env.context.get("shrimp_skip_lot_sync"):
            cambia_qty = self.filtered(
                lambda r: r.id and self._field_value_changed(r, "initial_qty", vals["initial_qty"]))
        res = super().write(vals)
        for rec in cambia_qty:
            rec._shrimp_sync_initial_lot()
        return res

    def _shrimp_sync_initial_lot(self):
        """El lote de stock base sigue a la cantidad del producto mientras no
        se haya movido. Con compras ya no se llega aquí (la cantidad está
        bloqueada); con siembras o producción declarada, la cantidad se
        corrige declarando la producción real."""
        self.ensure_one()
        lots = self.env["shrimp.stock.lot"].sudo().search([
            ("product_id", "=", self.id), ("owner_id", "=", self.seller_partner_id.id),
            ("origin_move_id", "=", False)])
        if len(lots) != 1:
            return
        lot = lots
        intacto = not lot.move_ids and float_compare(
            lot.available_qty, lot.initial_qty, precision_digits=6) == 0
        if not intacto:
            raise ValidationError(_(
                "La cantidad de «%s» ya tuvo movimientos (siembras, producción declarada o "
                "ajustes): corrígela declarando la producción real desde Mis lotes.") % self.name)
        lot.write({"initial_qty": self.initial_qty, "available_qty": self.initial_qty,
                   "state": "available"})
        self._compute_available_qty()
