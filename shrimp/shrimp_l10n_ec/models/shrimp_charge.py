from odoo import _, models


class ShrimpCharge(models.Model):
    _inherit = "shrimp.charge"

    def _shrimp_sri_enabled(self):
        self.ensure_one()
        return bool(self.company_id.ec_sri_enabled)

    def _shrimp_sri_point(self, journal):
        company = self.company_id
        return company.shrimp_service_sri_point_id or journal.ec_sri_point_id

    def _prepare_service_invoice(self, invoice):
        """Deja la factura de servicio lista para el SRI antes de contabilizar.

        Diario y punto de emisión de la plataforma, tipo de documento 01
        (Factura) y forma de pago. El cliente necesita su tipo de
        identificación (RUC/cédula/pasaporte): se sincroniza desde su número.
        """
        invoice = super()._prepare_service_invoice(invoice)
        if not self._shrimp_sri_enabled():
            return invoice
        company = self.company_id
        self.payer_partner_id._shrimp_sync_identification_type()
        vals = {}
        journal = company.shrimp_service_journal_id
        if journal and invoice.journal_id != journal:
            vals["journal_id"] = journal.id
        journal = journal or invoice.journal_id
        point = self._shrimp_sri_point(journal)
        if point:
            vals["ec_sri_point_id"] = point.id
            vals["ec_sri_establishment_id"] = point.establishment_id.id
        if company.shrimp_service_payment_id:
            vals["l10n_ec_sri_payment_id"] = company.shrimp_service_payment_id.id
        if vals:
            invoice.write(vals)
        factura = self.env.ref("l10n_ec.ec_dt_01", raise_if_not_found=False)
        if factura and invoice.l10n_latam_use_documents \
                and invoice.l10n_latam_document_type_id != factura \
                and factura in invoice.l10n_latam_available_document_type_ids:
            invoice.l10n_latam_document_type_id = factura
        return invoice

    def _after_service_invoice_posted(self, invoice):
        """Encola el comprobante electrónico para el SRI (si está activado).

        Solo encola: la firma y el envío los hace el cron del módulo SRI. Si
        falla (sin certificado, por ejemplo), la factura sigue contabilizada y
        el error queda en el historial del cobro.
        """
        res = super()._after_service_invoice_posted(invoice)
        if self._shrimp_sri_enabled() and self.company_id.shrimp_sri_auto_emit \
                and invoice.l10n_latam_use_documents:
            invoice.action_ec_sri_emit()
        return res

    def _prepare_refund_vals(self, reason):
        """La nota de crédito del SRI es el documento 04 (no la 01 de la
        factura que anula) y lleva el motivo."""
        vals = super()._prepare_refund_vals(reason)
        if self._shrimp_sri_enabled() and self.invoice_id.l10n_latam_use_documents:
            nota = self.env.ref("l10n_ec.ec_dt_04", raise_if_not_found=False)
            if nota:
                vals["l10n_latam_document_type_id"] = nota.id
            vals["ec_sri_reason"] = (reason or _("Anulación del servicio"))[:300]
        return vals

    def _prepare_service_refund(self, refund, reason):
        """Nota de crédito del SRI (04): exige el motivo y el punto de emisión."""
        refund = super()._prepare_service_refund(refund, reason)
        if not self._shrimp_sri_enabled():
            return refund
        vals = {"ec_sri_reason": (reason or _("Anulación del servicio"))[:300]}
        point = self._shrimp_sri_point(refund.journal_id) or self.invoice_id.ec_sri_point_id
        if point:
            vals["ec_sri_point_id"] = point.id
            vals["ec_sri_establishment_id"] = point.establishment_id.id
        refund.write(vals)
        return refund
