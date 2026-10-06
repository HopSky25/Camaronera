# -*- coding: utf-8 -*-
"""Ficha de calidad del lote de larva o nauplio (F3 de la propuesta).

Una ficha por muestreo: fecha, PCR de las cuatro enfermedades que pide el
comprador (WSSV, IHHNV, AHPND, EHP), prueba de estrés, uniformidad, estadío,
tanque, nauplio de origen y el laboratorio que analizó, con el informe en PDF.
El dueño del lote la crea y edita desde la ficha del producto; el comprador la
ve en la ficha pública y en el certificado de trazabilidad.

Desde el portal todo pasa por el controlador (sudo tras comprobar que el lote
es del usuario), como el resto de la plataforma.
"""
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

PCR_RESULTS = [
    ("negativo", "Negativo"),
    ("positivo", "Positivo"),
    ("no_realizado", "No realizado"),
]
PCR_FIELDS = [
    ("pcr_wssv", "WSSV"),
    ("pcr_ihhnv", "IHHNV"),
    ("pcr_ahpnd", "AHPND"),
    ("pcr_ehp", "EHP"),
]
# Días que se considera «reciente» un muestreo para la ficha pública.
QUALITY_RECENT_DAYS = 30


class ShrimpProductQuality(models.Model):
    _name = "shrimp.product.quality"
    _inherit = "shrimp.uuid.mixin"
    _description = "Ficha de calidad del lote"
    _order = "sample_date desc, id desc"
    _rec_name = "display_title"

    product_id = fields.Many2one(
        "shrimp.product", string="Lote", required=True, index=True, ondelete="cascade")
    seller_partner_id = fields.Many2one(
        related="product_id.seller_partner_id", string="Vendedor", store=True, index=True)

    sample_date = fields.Date(
        string="Fecha de muestreo", required=True, default=fields.Date.context_today)

    pcr_wssv = fields.Selection(PCR_RESULTS, string="PCR WSSV", default="no_realizado", required=True)
    pcr_ihhnv = fields.Selection(PCR_RESULTS, string="PCR IHHNV", default="no_realizado", required=True)
    pcr_ahpnd = fields.Selection(PCR_RESULTS, string="PCR AHPND", default="no_realizado", required=True)
    pcr_ehp = fields.Selection(PCR_RESULTS, string="PCR EHP", default="no_realizado", required=True)

    stress_test_survival = fields.Float(
        string="Prueba de estrés (% supervivencia)", digits=(5, 2),
        help="Supervivencia tras el choque osmótico o de formol.")
    uniformity = fields.Float(
        string="Uniformidad (%)", digits=(5, 2),
        help="Porcentaje de animales dentro de la talla dominante.")
    stage_id = fields.Many2one("shrimp.stage", string="Estadío", ondelete="restrict")
    tank = fields.Char(string="Tanque")
    origin_lot_id = fields.Many2one(
        "shrimp.stock.lot", string="Nauplio de origen", ondelete="set null",
        help="Lote de nauplio comprado del que sale esta corrida (laboratorios).")
    analysis_lab = fields.Char(string="Laboratorio que analizó")
    attachment_id = fields.Many2one(
        "ir.attachment", string="Informe (PDF)", ondelete="set null", copy=False)
    notes = fields.Text(string="Observaciones")
    evolution_id = fields.Many2one(
        "shrimp.product.evolution", string="Muestreo registrado", ondelete="set null",
        copy=False, readonly=True)

    pcr_status = fields.Selection(
        [("negativo", "PCR negativo"), ("positivo", "PCR positivo"),
         ("parcial", "PCR parcial"), ("no_realizado", "Sin PCR")],
        string="Resumen PCR", compute="_compute_pcr_status", store=True)
    display_title = fields.Char(compute="_compute_display_title")

    @api.depends("pcr_wssv", "pcr_ihhnv", "pcr_ahpnd", "pcr_ehp")
    def _compute_pcr_status(self):
        for rec in self:
            valores = [rec[f] for f, _l in PCR_FIELDS]
            if "positivo" in valores:
                rec.pcr_status = "positivo"
            elif all(v == "negativo" for v in valores):
                rec.pcr_status = "negativo"
            elif any(v == "negativo" for v in valores):
                rec.pcr_status = "parcial"
            else:
                rec.pcr_status = "no_realizado"

    @api.depends("product_id", "sample_date")
    def _compute_display_title(self):
        for rec in self:
            rec.display_title = "%s · %s" % (
                rec.product_id.name or _("Lote"),
                fields.Date.to_string(rec.sample_date) if rec.sample_date else "")

    # ------------------------------------------------------------------
    # Validaciones
    # ------------------------------------------------------------------
    @api.constrains("stress_test_survival", "uniformity")
    def _check_percentages(self):
        for rec in self:
            for fname in ("stress_test_survival", "uniformity"):
                valor = rec[fname] or 0.0
                if valor < 0.0 or valor > 100.0:
                    raise ValidationError(_("«%s» debe estar entre 0 y 100 %%.")
                                          % rec._fields[fname].string)

    @api.constrains("sample_date")
    def _check_sample_date(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            if rec.sample_date and rec.sample_date > hoy:
                raise ValidationError(_("La fecha de muestreo no puede estar en el futuro."))

    @api.constrains("product_id")
    def _check_product_tipo(self):
        for rec in self:
            if not rec.product_id._shrimp_accepts_quality():
                raise ValidationError(_(
                    "La ficha de calidad es para lotes de nauplio o larva, no para camarón adulto."))

    @api.constrains("origin_lot_id", "product_id")
    def _check_origin_lot(self):
        for rec in self:
            lote = rec.origin_lot_id
            if lote and lote.owner_id != rec.product_id.seller_partner_id:
                raise ValidationError(_(
                    "El nauplio de origen tiene que ser un lote del propio vendedor."))

    # ------------------------------------------------------------------
    # Ayudas para plantillas
    # ------------------------------------------------------------------
    def _pcr_rows(self):
        """[(etiqueta, código, texto)] de los cuatro PCR."""
        self.ensure_one()
        textos = dict(PCR_RESULTS)
        return [(label, self[f], textos.get(self[f], "")) for f, label in PCR_FIELDS]

    def _is_recent(self):
        self.ensure_one()
        if not self.sample_date:
            return False
        return (fields.Date.context_today(self) - self.sample_date).days <= QUALITY_RECENT_DAYS

    @api.model
    def _origin_lot_options(self, partner):
        """Lotes comprados del vendedor que pueden ser el nauplio de origen."""
        return self.env["shrimp.stock.lot"].sudo().search([
            ("owner_id", "=", partner.id), ("origin_move_id", "!=", False),
        ], order="id desc", limit=80)

    def action_register_sample(self):
        """«Registrar muestreo»: deja una fila en la evolución del lote (la
        misma tabla que usan la ficha pública y la trazabilidad) con el
        estadío y la supervivencia de la prueba de estrés."""
        Evolution = self.env["shrimp.product.evolution"].sudo()
        for rec in self:
            nota = _("Muestreo del %(f)s. PCR: %(p)s.%(u)s") % {
                "f": fields.Date.to_string(rec.sample_date),
                "p": dict(rec._fields["pcr_status"].selection).get(rec.pcr_status, ""),
                "u": (_(" Uniformidad %.0f %%.") % rec.uniformity) if rec.uniformity else "",
            }
            vals = {
                "product_id": rec.product_id.id,
                "date": fields.Datetime.now(),
                "stage_id": (rec.stage_id or rec.product_id.stage_id).id or False,
                "avg_size_mg": rec.product_id.avg_size_mg,
                "survival_rate": rec.stress_test_survival or rec.product_id.survival_rate,
                "available_qty": rec.product_id.available_qty,
                "health_status": rec.notes or False,
                "note": nota,
            }
            if rec.evolution_id:
                rec.evolution_id.write(vals)
            else:
                rec.evolution_id = Evolution.create(vals)
        return True

    @api.model
    def _shrimp_trace_quality(self, trace, tx=None):
        """Fichas de calidad de los lotes de la cadena de una compra, para el
        certificado de trazabilidad. No toca get_full_traceability_data: toma
        los productos que ya devuelve (o el de la compra si no vinieran)."""
        productos = self.env["shrimp.product"]
        if isinstance(trace, dict):
            productos = trace.get("products") or productos
            if not productos and trace.get("lots"):
                productos = trace["lots"].mapped("product_id")
        if tx is not None:
            productos |= tx.product_id
        if not productos:
            return self.browse()
        return self.sudo().search([("product_id", "in", productos.ids)],
                                  order="product_id, sample_date desc, id desc")
