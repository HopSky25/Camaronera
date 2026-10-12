import logging

from odoo import models

_logger = logging.getLogger(__name__)


class AccountChartTemplate(models.AbstractModel):
    _inherit = "account.chart.template"

    def _post_load_data(self, template_code, company, template_data):
        """Al cargar el plan «ec» en una compañía (nueva o recargada), se deja
        su configuración contable de CamaronMarket. Idempotente; si falla no
        impide cargar el plan."""
        super()._post_load_data(template_code, company, template_data)
        if template_code != "ec":
            return
        try:
            with self.env.cr.savepoint():
                self.env["shrimp.contabilidad.setup"]._setup_company(company)
        except Exception:  # noqa: BLE001
            _logger.exception("shrimp_contabilidad: no se pudo configurar %s tras cargar "
                              "el plan ec", company.name)
