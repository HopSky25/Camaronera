from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Con qué se emiten las facturas de SERVICIO de la plataforma. Vacíos, se
    # usa el diario de ventas por defecto y el punto de emisión atado a él.
    shrimp_service_journal_id = fields.Many2one(
        "account.journal", string="Diario de facturas de servicio",
        domain="[('type', '=', 'sale'), ('company_id', '=', id)]",
        help="Diario de ventas (con documentos del SRI) para las facturas de "
             "comisión, verificación y empaque que emite la plataforma.")
    shrimp_service_sri_point_id = fields.Many2one(
        "ec.sri.point", string="Punto de emisión de la plataforma",
        domain="[('company_id', '=', id)]",
        help="Punto de emisión SRI (001-00X) de las facturas de servicio. Si se "
             "deja vacío se usa el punto atado al diario.")
    shrimp_service_payment_id = fields.Many2one(
        "l10n_ec.sri.payment", string="Forma de pago SRI de los servicios",
        help="Forma de pago que se declara en la factura (p. ej. 20 - Otros con "
             "utilización del sistema financiero).")
    shrimp_sri_auto_emit = fields.Boolean(
        string="Enviar al SRI al emitir", default=True,
        help="Al contabilizar una factura de servicio se crea su comprobante "
             "electrónico y se encola para el SRI (el envío lo hace el cron del "
             "módulo SRI). Si falla, la factura queda contabilizada y el aviso "
             "en su historial.")
