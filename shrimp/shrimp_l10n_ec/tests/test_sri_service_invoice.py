"""Factura electrónica del SERVICIO de la plataforma con el SRI activo.

Nada sale al SRI: la firma usa un certificado de prueba y el envío se simula.
Se comprueba que la factura de servicio queda VÁLIDA para el SRI (punto de
emisión, documento 01, identificación del cliente), que se encola, que sin
punto de emisión el cobro espera con el motivo (sin caer en otro diario) y que
la anulación sale como nota de crédito (04) sobre la factura autorizada.
"""
from unittest.mock import patch

from odoo.tests import tagged

from .common import SriPlatformCase


@tagged("post_install", "-at_install")
class TestSriServiceInvoice(SriPlatformCase):

    def test_identificacion_segun_el_numero(self):
        self.assertEqual(self.cliente.vat, "1790011674001")
        self.assertEqual(self.cliente.l10n_latam_identification_type_id,
                         self.env.ref("l10n_ec.ec_ruc"))
        P = self.env["res.partner"]
        self.assertEqual(P._shrimp_identification_type_for("0912345678"),
                         self.env.ref("l10n_ec.ec_dni"))
        self.assertIn(P._shrimp_identification_type_for("AB123456"),
                      self.env.ref("l10n_ec.ec_passport") | self.env.ref("l10n_latam_base.it_pass"))

    def test_factura_de_servicio_valida_para_el_sri(self):
        cobro = self._cobro()
        Invoice = self.registry["account.move"]
        with patch.object(Invoice, "_ec_sri_emit_keep", autospec=True,
                          return_value=(self.env["ec.sri.document"], False)) as emitir:
            cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        factura = cobro.invoice_id
        self.assertEqual(factura.state, "posted")
        self.assertEqual(factura.ec_sri_point_id, self.punto)
        self.assertEqual(factura.l10n_latam_document_type_id, self.factura)
        self.assertTrue(factura.name.endswith("001-003-000000001"), factura.name)
        self.assertEqual(emitir.call_count, 1)

    def test_reintento_tras_configurar_el_punto(self):
        self.company.shrimp_service_sri_point_id = False
        self.journal.ec_sri_point_id = False
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "pending")
        self.assertIn("punto de emisión", cobro.invoice_error)
        self.assertFalse(cobro.invoice_id)
        self.company.shrimp_service_sri_point_id = self.punto
        Invoice = self.registry["account.move"]
        with patch.object(Invoice, "_ec_sri_emit_keep", autospec=True,
                          return_value=(self.env["ec.sri.document"], False)):
            self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)

    def test_nota_de_credito_del_servicio(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self._process(cobro.invoice_id)
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        nota = cobro.refund_id
        self.assertEqual(nota.state, "posted")
        self.assertEqual(nota.l10n_latam_document_type_id, self.nota_credito)
        self.assertEqual(nota.ec_sri_reason, "Servicio no prestado")
