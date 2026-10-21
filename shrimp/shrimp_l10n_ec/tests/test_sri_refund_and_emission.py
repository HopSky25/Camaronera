"""Rama feat-sri: nota de crédito conciliada con su factura, comprobante que se
conserva si la firma falla y correo al cliente al autorizarse. SRI simulado."""
from unittest.mock import patch

from odoo.tests import tagged

from .common import SriPlatformCase


@tagged("post_install", "-at_install")
class TestSriRefundAndEmission(SriPlatformCase):

    def _cobro_autorizado(self, importe=20.0):
        cobro = self._cobro(importe)
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self._process(cobro.invoice_id)
        return cobro

    def _pagar(self, factura, monto=None):
        vals = {"amount": monto} if monto else {}
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=factura.ids).create(vals)._create_payments()

    def _receivable(self, move):
        return move.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")

    # -- Conciliación de la nota de crédito ----------------------------------
    def test_nota_de_credito_concilia_factura_sin_pagar(self):
        cobro = self._cobro_autorizado()
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        factura, nota = cobro.invoice_id, cobro.refund_id
        self.assertTrue(self._receivable(factura).reconciled)
        self.assertTrue(self._receivable(nota).reconciled)
        self.assertEqual(factura.payment_state, "reversed")
        self.assertFalse(nota.activity_ids)

    def test_nota_de_credito_factura_pagada_en_parte(self):
        cobro = self._cobro_autorizado()
        factura = cobro.invoice_id
        self._pagar(factura, monto=5.0)
        cobro.action_cancel_charge("Servicio no prestado")
        nota = cobro.refund_id
        self.assertTrue(factura.currency_id.is_zero(factura.amount_residual))
        self.assertAlmostEqual(nota.amount_residual, 5.0)
        self.assertEqual(nota.activity_ids.summary, "Devolver al cliente")
        self.assertEqual(nota.activity_ids.user_id, self.responsable)

    def test_nota_de_credito_factura_pagada(self):
        cobro = self._cobro_autorizado()
        factura = cobro.invoice_id
        self._pagar(factura)
        cobro.action_cancel_charge("Servicio no prestado")
        nota = cobro.refund_id
        self.assertEqual(nota.state, "posted")
        self.assertFalse(self._receivable(nota).reconciled, "La nota queda abierta: saldo a favor")
        self.assertAlmostEqual(nota.amount_residual, nota.amount_total)
        self.assertEqual(nota.activity_ids.summary, "Devolver al cliente")

    # -- La firma falla: el comprobante se conserva ---------------------------
    def test_fallo_de_firma_conserva_el_comprobante(self):
        cobro = self._cobro()
        with patch.object(type(self.company), "_ec_sri_certificate",
                          return_value=(b"roto", "roto")):
            cobro._try_invoice()
        factura = cobro.invoice_id
        self.assertEqual(factura.state, "posted")
        self.assertEqual(cobro.state, "sri_rejected")
        doc = self._doc(factura, "01")
        self.assertTrue(doc, "El comprobante no se deshace con el fallo")
        self.assertEqual(doc.state, "draft")
        clave = doc.access_key
        self.assertTrue(clave)
        self.assertIn(doc.last_message[:40], cobro.invoice_error)
        # Reintento con el certificado bueno: misma clave, sin duplicar.
        cobro.action_retry_invoice()
        self.assertEqual(doc.state, "queued")
        self.assertEqual(doc.access_key, clave)
        self.assertEqual(len(factura.ec_sri_document_ids), 1)
        self._process(factura)
        self.assertEqual(cobro.state, "invoiced")

    # -- Correo al cliente -------------------------------------------------------
    def test_factura_autorizada_se_envia_al_cliente(self):
        cobro = self._cobro_autorizado()
        doc = self._doc(cobro.invoice_id, "01")
        self.assertTrue(doc.customer_mail_sent)
        mail = self.env["mail.mail"].sudo().search([
            ("model", "=", "ec.sri.document"), ("res_id", "=", doc.id)])
        self.assertEqual(len(mail), 1)
        self.assertIn(self.cliente, mail.recipient_ids)
        self.assertEqual(len(mail.attachment_ids), 2, "RIDE (PDF) y XML autorizado")
