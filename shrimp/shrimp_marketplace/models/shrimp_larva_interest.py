# -*- coding: utf-8 -*-
"""«Avísame»: interés registrado desde una búsqueda de larva sin resultados.

Los avisos automáticos de lotes nuevos (shrimp.lot.alert) existen hoy solo
para la empacadora. Hasta que se generalicen a larva y nauplio (F5 de la
propuesta), el «Avísame» del catálogo guarda aquí lo que el comprador buscaba
—tipo, estadío, genética, supervivencia mínima, PCR— para que el equipo
comercial (y luego el aviso automático) lo atienda. Una fila por cuenta y
búsqueda: repetir el clic no duplica.
"""
from odoo import fields, models


class ShrimpLarvaInterest(models.Model):
    _name = "shrimp.larva.interest"
    _description = "Interés en larva o nauplio (Avísame)"
    _order = "create_date desc, id desc"

    partner_id = fields.Many2one(
        "res.partner", string="Cuenta", required=True, index=True, ondelete="cascade")
    tipo = fields.Selection(
        [("nauplio", "Nauplio"), ("larva", "Larva (PL)"), ("camaron", "Camarón")],
        string="Tipo")
    stage_id = fields.Many2one("shrimp.stage", string="Estadío", ondelete="set null")
    genetics_line_id = fields.Many2one(
        "shrimp.genetics.line", string="Línea genética", ondelete="set null")
    survival_min = fields.Integer(string="Supervivencia mínima (%)")
    pcr_required = fields.Boolean(string="Con PCR vigente")
    spf_required = fields.Boolean(string="SPF")
    search_url = fields.Char(string="Búsqueda")
    active = fields.Boolean(default=True)
    notified = fields.Boolean(string="Atendido", default=False)

    _shrimp_larva_interest_uniq = models.Constraint(
        "unique(partner_id, search_url)",
        "Ya registraste interés en esta búsqueda.",
    )
