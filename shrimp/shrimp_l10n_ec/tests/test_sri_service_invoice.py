"""Factura electrónica del SERVICIO de la plataforma con el SRI activo.

No se firma ni se envía nada al SRI: el envío se simula (mock). Lo que se
comprueba es que la factura de servicio queda VÁLIDA para contabilizarse con
el SRI activado (punto de emisión, documento 01, identificación del cliente),
que se encola para el SRI, que un fallo se reintenta y que la anulación sale
como nota de crédito (04).
"""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSriServiceInvoice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ec = cls.env.ref("base.ec")
        cls.company = cls.env["res.company"].create({
            "name": "PLATAFORMA SRI TEST", "country_id": ec.id, "vat": "1790016919001",
            "currency_id": cls.env.ref("base.USD").id})
        cls.env.user.company_ids |= cls.company
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.env["account.chart.template"].try_loading("ec", company=cls.company, install_demo=False)
        cls.company = cls.env["res.company"].browse(cls.company.id)
        cls.company.write({"ec_sri_enabled": True, "tax_calculation_rounding_method": "round_per_line"})
        cls.journal = cls.env["account.journal"].search([
            ("company_id", "=", cls.company.id), ("type", "=", "sale"),
            ("l10n_latam_use_documents", "=", True)], limit=1)
        cls.factura = cls.env.ref("l10n_ec.ec_dt_01")
        cls.nota_credito = cls.env.ref("l10n_ec.ec_dt_04")
        cls.establecimiento = cls.env["ec.sri.establishment"].create({
            "name": "Matriz", "code": "001", "company_id": cls.company.id, "address": "Guayaquil"})
        cls.punto = cls.env["ec.sri.point"].create({
            "name": "Plataforma", "establishment_id": cls.establecimiento.id, "emission": "003",
            "line_ids": [(0, 0, {"l10n_latam_document_type_id": cls.factura.id}),
                         (0, 0, {"l10n_latam_document_type_id": cls.nota_credito.id})]})
        cls.company.write({"shrimp_service_journal_id": cls.journal.id,
                           "shrimp_service_sri_point_id": cls.punto.id})
        cls.cliente = cls.env["res.partner"].create({
            "name": "Laboratorio SRI", "is_company": True, "email": "lab.sri@prueba.test",
            "vat_or_id": "1790011674001", "shrimp_user_type": "laboratorio",
            "lab_razon_social": "Laboratorio SRI S.A.", "lab_ubicacion": "Quito"})

    def _cobro(self, importe=12.5):
        return self.env["shrimp.charge"].with_context(shrimp_charge_no_invoice=True)._register_charge({
            "charge_type": "commission", "company_id": self.company.id,
            "payer_partner_id": self.cliente.id, "seller_partner_id": self.cliente.id,
            "amount": importe, "invoice_qty": 1.0, "unit_amount": importe,
            "currency_id": self.env.ref("base.USD").id,
            "origin": "TXN-SRI", "description": "Comisión marketplace – prueba"})

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
        with patch.object(Invoice, "action_ec_sri_emit", autospec=True) as emitir:
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
        self.assertEqual(cobro.state, "error")
        self.assertTrue(cobro.invoice_error)
        self.assertFalse(cobro.invoice_id)
        self.company.shrimp_service_sri_point_id = self.punto
        Invoice = self.registry["account.move"]
        with patch.object(Invoice, "action_ec_sri_emit", autospec=True):
            self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)

    def test_nota_de_credito_del_servicio(self):
        cobro = self._cobro()
        Invoice = self.registry["account.move"]
        with patch.object(Invoice, "action_ec_sri_emit", autospec=True):
            cobro._try_invoice()
            cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        nota = cobro.refund_id
        self.assertEqual(nota.state, "posted")
        self.assertEqual(nota.l10n_latam_document_type_id, self.nota_credito)
        self.assertEqual(nota.ec_sri_reason, "Servicio no prestado")
