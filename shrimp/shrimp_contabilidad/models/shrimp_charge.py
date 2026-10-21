"""Los cobros de la plataforma eligen el producto (y por tanto la cuenta).

Dos ajustes, sin cambiar el flujo de facturación de shrimp_marketplace:

1. La comisión de una venta de LARVA o NAUPLIO se factura con el producto
   «Comisión marketplace – larva y nauplio» (cuenta 41020105, línea analítica
   «Larva y nauplio») en vez de la comisión general del marketplace
   (41020101). El tipo de producto lo da shrimp.product._shrimp_tipo(), el
   mismo criterio que usan las pestañas del catálogo.
2. Si un producto de servicio se crea después de instalar este módulo (p. ej.
   al instalar shrimp_verification), se le pone la cuenta de su línea, el IVA
   15 % de servicios y su distribución analítica la primera vez que se usa.

Además, antes de la factura de proveedor de una verificación, la deuda con
la verificadora se lleva a 21030101 «Honorarios por pagar a verificadoras».
"""
import logging

from odoo import api, models

from . import catalogo as C

_logger = logging.getLogger(__name__)


class ShrimpCharge(models.Model):
    _inherit = "shrimp.charge"

    def _shrimp_cont_es_larva(self, charge_type):
        if charge_type != "commission" or len(self) != 1 or not self.product_id:
            return False
        try:
            return self.product_id.sudo()._shrimp_tipo() in ("nauplio", "larva")
        except Exception:  # noqa: BLE001 - ante la duda, comisión general
            return False

    @api.model
    def _get_service_product(self, charge_type, company=None):
        company = company or (self.company_id if len(self) == 1 else None) \
            or self._shrimp_billing_company()
        Setup = self.env["shrimp.contabilidad.setup"]
        if self._shrimp_cont_es_larva(charge_type):
            product = Setup._ensure_own_income_product("larva")
            linea = "larva"
        else:
            product = super()._get_service_product(charge_type, company)
            linea = C.COBRO_A_LINEA.get(charge_type)
        if linea and not self.env.context.get("shrimp_cont_no_lazy"):
            Setup._configure_lazy(product, linea, company)
        return product

    @api.model
    def _create_vendor_bill(self, partner, product, label, amount, ref, company=None):
        # La misma compañía en la que shrimp_marketplace crea la factura.
        company = company or self._shrimp_billing_company()
        try:
            with self.env.cr.savepoint():
                self.env["shrimp.contabilidad.setup"]._payable_verificadora(
                    partner, product, company)
        except Exception:  # noqa: BLE001 - nunca debe impedir la liquidación
            _logger.exception("shrimp_contabilidad: no se pudo fijar la cuenta por pagar "
                              "de la verificadora %s", partner.display_name)
        return super()._create_vendor_bill(partner, product, label, amount, ref,
                                           company=company)
