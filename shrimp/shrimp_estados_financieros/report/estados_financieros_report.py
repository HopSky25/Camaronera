from odoo import api, models


class ReportEstadoFinanciero(models.AbstractModel):
    """Valores del PDF: la misma estructura que usa el Excel."""

    _name = "report.shrimp_estados_financieros.report_ef"
    _description = "Estados financieros (PDF vertical)"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["shrimp.estados.financieros.wizard"].browse(docids)
        return {
            "doc_ids": docids,
            "doc_model": docs._name,
            "docs": docs,
            "reportes": [doc._datos_informe() for doc in docs],
        }


class ReportEstadoFinancieroHorizontal(models.AbstractModel):
    _name = "report.shrimp_estados_financieros.report_ef_horizontal"
    _inherit = "report.shrimp_estados_financieros.report_ef"
    _description = "Estados financieros (PDF horizontal)"
