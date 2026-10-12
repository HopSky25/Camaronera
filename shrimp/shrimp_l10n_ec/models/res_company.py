from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Con qué se emiten las facturas de SERVICIO de la plataforma. El número
    # fiscal (001-003-000000007) lo da el diario (establecimiento y punto de
    # l10n_ec); el punto de emisión SRI tiene que tener esos mismos tres y tres
    # dígitos. Vacíos los dos, se usa el diario de ventas por defecto y el
    # punto que coincide con él.
    shrimp_service_journal_id = fields.Many2one(
        "account.journal", string="Diario de facturas de servicio",
        domain="[('type', '=', 'sale'), ('company_id', '=', id), "
               "('l10n_latam_use_documents', '=', True)]",
        help="Diario de ventas (con documentos del SRI) para las facturas de "
             "comisión, verificación y empaque que emite la plataforma. Su "
             "establecimiento y punto de emisión deciden la numeración.")
    shrimp_service_sri_point_id = fields.Many2one(
        "ec.sri.point", string="Punto de emisión de la plataforma",
        domain="[('company_id', '=', id), ('document_type', '=', '01')]",
        help="Punto de emisión SRI de Factura (01) de las facturas de servicio, "
             "en el mismo ambiente que la compañía. Si no se elige diario, se "
             "usa el diario de ventas que numera con su establecimiento y punto. "
             "Vacío: el punto que coincide con el diario. Para las notas de "
             "crédito hace falta además el punto de Nota de crédito (04) con el "
             "mismo establecimiento y punto.")
    shrimp_service_payment_id = fields.Many2one(
        "l10n_ec.sri.payment", string="Forma de pago SRI de los servicios",
        help="Forma de pago que se declara en la factura (p. ej. 20 - Otros con "
             "utilización del sistema financiero).")
    shrimp_sri_auto_emit = fields.Boolean(
        string="Enviar al SRI al emitir", default=True,
        help="Al contabilizar una factura o nota de crédito de servicio se crea "
             "su comprobante electrónico en borrador; un proceso automático lo "
             "firma y lo pone en cola, y el cron del módulo SRI lo envía. Si la "
             "firma falla (p. ej. sin certificado), el motivo queda en el "
             "historial del comprobante y se reintenta.")
