from odoo import models


class ShrimpTransaction(models.Model):
    """La última pata de la cadena (camaronera → empacadora) ya no necesita
    una restricción propia: quién compra a quién lo decide la matriz de
    capacidades (res.partner._shrimp_capability_matrix), que este módulo
    amplía con «buy_from_camaronera = empacadora + camaronera», y la aplica la restricción
    única de shrimp_marketplace (shrimp.transaction._check_types)."""

    _inherit = "shrimp.transaction"
