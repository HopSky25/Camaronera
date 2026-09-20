# -*- coding: utf-8 -*-
import logging
from odoo import fields, models, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def _process_saved_order(self, draft):
        # En cajas con punto de emisión SRI, toda venta debe generar factura
        # electrónica: se fuerza to_invoice y, si no hay cliente, se asigna el
        # consumidor final configurado. Se hace antes del super() porque es ahí
        # donde el POS decide facturar (to_invoice + invoice_journal_id).
        if not draft:
            for order in self:
                config = order.config_id
                if not config.ec_sri_point_id or order.state == 'cancel':
                    continue
                vals = {}
                if not order.to_invoice:
                    vals['to_invoice'] = True
                if not order.partner_id and config.ec_sri_final_consumer_id:
                    vals['partner_id'] = config.ec_sri_final_consumer_id.id
                if vals:
                    order.write(vals)
        return super()._process_saved_order(draft)

    def _generate_pos_order_invoice(self):
        invoice = super()._generate_pos_order_invoice()
        # sudo: el usuario del POS no necesita permisos contables SRI.
        self.sudo()._ec_sri_emit_documents()
        return invoice

    def _ec_sri_emit_documents(self):
        Document = self.env['ec.sri.document']
        for order in self:
            point = order.config_id.ec_sri_point_id
            move = order.account_move
            if not point or not move or move.move_type != 'out_invoice' or move.state != 'posted':
                continue
            if move.ec_sri_document_ids:
                continue  # idempotente: ya tiene comprobante
            if not move.partner_id:
                _logger.warning('SRI POS: la factura de %s no tiene cliente; no se emite.', order.name)
                continue
            try:
                document = Document.create({
                    'company_id': order.company_id.id,
                    'partner_id': move.partner_id.id,
                    'document_type': '01',
                    'point_id': point.id,
                    'date': move.invoice_date or fields.Date.context_today(order),
                    'move_id': move.id,
                    'mode': 'native',
                })
            except Exception as exc:  # noqa: BLE001 - no romper la venta
                _logger.error('SRI POS: no se pudo crear el comprobante de %s: %s', order.name, exc)
                continue
            # Firma en el momento y encola el envío inmediato (asíncrono). Si la
            # preparación falla (p. ej. consumidor final > USD 50 o certificado
            # ausente) el comprobante queda en borrador para revisión, sin
            # tumbar la venta ni la factura.
            try:
                with self.env.cr.savepoint():
                    document.action_queue()
            except (UserError, ValidationError, ValueError) as exc:
                document._event('pos_error', _('Preparación SRI pospuesta: %s') % exc)
