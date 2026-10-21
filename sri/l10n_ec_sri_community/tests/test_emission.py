"""Emisión de punta a punta con el SRI simulado (rama feat-sri, 2026-10-20).

* F1: el .p12 se sube desde Ajustes (archivo + contraseña juntos) y queda cifrado.
* Un fallo al firmar no hace perder el comprobante ni su clave; un comprobante que
  nunca se transmitió y quedó con fecha vieja se vuelve a fechar a hoy.
* Al autorizarse, el cliente recibe por correo (en cola) el RIDE y el XML.
* El RIDE se renderiza (sin formato % en QWeb).
* Un punto de producción no salta los secuenciales usados en pruebas en otro diario.
"""
import base64
import os
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..services import crypto
from .common import CERT_PASSWORD, SriEmissionCase, make_p12


@tagged('post_install', '-at_install')
class TestSriEmission(SriEmissionCase):

    # -- F1: certificado desde Ajustes -------------------------------------
    def test_upload_certificate_from_settings(self):
        p12 = make_p12(common_name='AJUSTES TEST')
        with patch.dict(os.environ, {crypto.MASTER_ENV: 'dummy-test-key'}):
            settings = self.env['res.config.settings'].with_company(self.company).create(dict(
                ec_sri_cert_file=base64.b64encode(p12), ec_sri_cert_filename='firma.p12',
                ec_sri_cert_password=CERT_PASSWORD))
            settings.execute()
            # Ni el archivo ni la contraseña se guardan en Ajustes: no son almacenados.
            for fname in ('ec_sri_cert_file', 'ec_sri_cert_password', 'ec_sri_cert_filename'):
                self.assertFalse(settings._fields[fname].store, fname)
            self.assertFalse(settings.ec_sri_cert_file)
            self.assertFalse(settings.ec_sri_cert_password)
            company = self.company.sudo()
            self.assertEqual(company.ec_sri_cert_subject, 'CN=AJUSTES TEST')
            self.assertEqual(company.ec_sri_cert_filename, 'firma.p12')
            self.assertTrue(company.ec_sri_cert_data and company.ec_sri_cert_password_enc)
            self.assertNotIn(CERT_PASSWORD, company.ec_sri_cert_password_enc)
            # El patch de la clase base sustituye _ec_sri_certificate: se descifra a mano.
            self.assertEqual(crypto.decrypt(company.ec_sri_cert_data, company.ec_sri_cert_salt), p12)
            self.env.flush_all()
            self.env.cr.execute('SELECT ec_sri_cert_password FROM res_company WHERE id = %s', (company.id,))
            self.assertIsNone(self.env.cr.fetchone()[0])
            self.env.cr.execute("SELECT count(*) FROM ir_attachment WHERE res_field = 'ec_sri_cert_file'")
            self.assertEqual(self.env.cr.fetchone()[0], 0)
            # Sin contraseña: el error claro de siempre, no un .p12 en claro.
            with self.assertRaisesRegex(UserError, 'contraseña'):
                self.env['res.config.settings'].with_company(self.company).create(dict(
                    ec_sri_cert_file=base64.b64encode(p12)))

    # -- Fallo al firmar: el comprobante y su clave se conservan --------------
    def test_failed_signature_keeps_document_and_key(self):
        prod = self._production()
        invoice = self._invoice(point=prod)
        with patch.object(type(self.company), '_ec_sri_certificate', return_value=(b'roto', 'roto')):
            doc, error = invoice._ec_sri_emit_keep()
        self.assertTrue(error)
        self.assertEqual(doc.state, 'draft')
        key = doc.access_key
        self.assertTrue(key, 'La clave se asigna antes de firmar y no se pierde')
        self.assertEqual(key[:8], invoice.invoice_date.strftime('%d%m%Y'))
        self.assertEqual(doc.environment, '2')
        self.assertIn(error, doc.last_message)
        self.assertFalse(doc.date_expired)
        # Al día siguiente el reintento reutiliza el comprobante y la clave (no la regla de «hoy»).
        with self.today_patch(1):
            again, error = invoice._ec_sri_emit_keep()
        self.assertFalse(error)
        self.assertEqual(again, doc)
        self.assertEqual(doc.state, 'queued')
        self.assertEqual(doc.access_key, key)
        self.assertEqual(len(invoice.ec_sri_document_ids), 1)

    def test_expired_never_sent_document_is_redated(self):
        prod = self._production()
        invoice = self._invoice(point=prod)
        name = invoice.name
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({})._create_payments()
        paid_state = invoice.payment_state
        self.assertIn(paid_state, ('paid', 'in_payment'))
        # Falla ANTES de la clave (dirección matriz vacía): el comprobante queda sin clave.
        self.company.ec_sri_address = False
        doc, error = invoice._ec_sri_emit_keep()
        self.assertTrue(error)
        self.assertFalse(doc.access_key)
        self.company.ec_sri_address = 'Av. Principal, Quito'
        with self.today_patch(1) as today:
            tomorrow = today.return_value
            _doc, error = invoice._ec_sri_emit_keep()
            self.assertIn('Fecha vencida', error)
            self.assertTrue(doc.date_expired)
            invoice.invalidate_recordset(['ec_sri_date_expired'])
            self.assertTrue(invoice.ec_sri_date_expired)
            invoice.action_ec_sri_redate_today()
            self.assertEqual(invoice.invoice_date, tomorrow)
            self.assertEqual(invoice.name, name, 'Conserva el número')
            self.assertEqual(invoice.state, 'posted')
            self.assertEqual(invoice.payment_state, paid_state, 'Conserva el pago conciliado')
            self.assertEqual(doc.date, tomorrow)
            self.assertEqual(doc.state, 'queued')
            self.assertEqual(doc.access_key[:8], tomorrow.strftime('%d%m%Y'))
            self.assertTrue(doc.event_ids.filtered(lambda e: e.operation == 'redate'))
            invoice.invalidate_recordset(['ec_sri_date_expired'])
            self.assertFalse(invoice.ec_sri_date_expired)

    def test_redate_without_document_and_never_if_transmitted(self):
        prod = self._production()
        invoice = self._invoice(point=prod)
        other = self._invoice(point=prod)
        with self.today_patch(1) as today:
            # Sin comprobante todavía (p. ej. nota de crédito en revisión de contabilidad).
            self.assertTrue(invoice.ec_sri_date_expired)
            with self.assertRaisesRegex(UserError, 'Fecha vencida'):
                invoice.action_ec_sri_emit()
            invoice.action_ec_sri_redate_today()
            doc = self._doc(invoice)
            self.assertEqual(doc.state, 'queued')
            self.assertEqual(doc.date, today.return_value)
        # Un comprobante que pudo llegar al SRI no se vuelve a fechar.
        draft = self.env['ec.sri.document'].browse(other._ec_sri_open_document('01')['res_id'])
        draft._set({'was_sent': True})
        with self.today_patch(1), self.assertRaisesRegex(UserError, 'nunca se envió'):
            draft.action_redate_today()

    def test_pruebas_never_expires(self):
        invoice = self._invoice()
        with self.today_patch(3):
            self.assertFalse(invoice.ec_sri_date_expired)
            doc, error = invoice._ec_sri_emit_keep()
        self.assertFalse(error)
        self.assertEqual(doc.state, 'queued')

    # -- Correo al cliente ------------------------------------------------------
    def _mails(self, doc):
        return self.env['mail.mail'].sudo().search([('model', '=', 'ec.sri.document'), ('res_id', '=', doc.id)])

    def test_authorized_document_is_mailed_once(self):
        invoice = self._invoice()
        doc, error = invoice._ec_sri_emit_keep()
        self.assertFalse(error)
        self._process(doc)
        self.assertEqual(doc.state, 'authorized')
        self.assertTrue(doc.customer_mail_sent)
        mail = self._mails(doc)
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.state, 'outgoing', 'Solo se encola: no se envía en la transacción')
        self.assertEqual(mail.recipient_ids, self.partner | self.billing)
        names = mail.attachment_ids.mapped('name')
        self.assertIn(doc.authorized_filename, names)
        self.assertIn('RIDE-%s.pdf' % doc.access_key, names)
        self.assertIn('[PRUEBAS]', mail.subject)
        # Idempotente: otra autorización (consulta) no lo duplica.
        doc._ec_sri_notify_customer()
        self.assertEqual(len(self._mails(doc)), 1)
        # El botón sí lo reenvía.
        doc.action_send_customer_mail()
        self.assertEqual(len(self._mails(doc)), 2)

    def test_mail_failure_never_changes_state(self):
        invoice = self._invoice()
        doc, _error = invoice._ec_sri_emit_keep()
        Template = self.registry['mail.template']
        with patch.object(Template, 'send_mail', side_effect=RuntimeError('sin plantilla')):
            self._process(doc)
        self.assertEqual(doc.state, 'authorized')
        self.assertFalse(doc.customer_mail_sent)
        self.assertTrue(doc.event_ids.filtered(lambda e: e.operation == 'mail_error'))
        self.assertFalse(self._mails(doc))

    def test_ride_failure_still_sends_xml(self):
        invoice = self._invoice()
        doc, _error = invoice._ec_sri_emit_keep()
        Report = self.registry['ir.actions.report']
        with patch.object(Report, '_render_qweb_pdf', side_effect=OSError('sin wkhtmltopdf')):
            self._process(doc)
        self.assertTrue(doc.customer_mail_sent)
        mail = self._mails(doc)
        self.assertEqual(mail.attachment_ids.mapped('name'), [doc.authorized_filename])
        self.assertTrue(doc.event_ids.filtered(lambda e: e.operation == 'mail_error'))

    def test_credit_note_is_mailed(self):
        invoice = self._invoice()
        doc, _error = invoice._ec_sri_emit_keep()
        self._process(doc)
        refund = invoice._reverse_moves([dict(ec_sri_reason='Devolución', l10n_latam_document_type_id=self.dt_credit.id)])
        refund.action_post()
        credit, error = refund._ec_sri_emit_keep()
        self.assertFalse(error)
        self._process(credit)
        self.assertEqual(credit.state, 'authorized')
        self.assertEqual(len(self._mails(credit)), 1)

    # -- RIDE ----------------------------------------------------------------
    def test_ride_renders(self):
        invoice = self._invoice()
        doc, _error = invoice._ec_sri_emit_keep()
        self._process(doc)
        html, _fmt = self.env['ir.actions.report']._render_qweb_html('l10n_ec_sri_community.report_ride', doc.ids)
        html = html.decode()
        self.assertIn(doc.access_key, html)
        self.assertIn('/report/barcode/Code128/%s?width=700&amp;height=70' % doc.access_key, html)
        self.assertIn('PRUEBAS', html)

    # -- Secuencial de producción vs. pruebas ----------------------------------
    def test_production_point_does_not_skip_test_numbers(self):
        first = self._invoice()
        self.assertTrue(first.name.endswith('001-001-000000001'), first.name)
        # Producción con el MISMO 001-001.
        self.company.write(dict(ec_sri_environment='2', ec_sri_production_ready=True))
        prod = self.env['ec.sri.point'].create(dict(
            name='Producción', establishment_id=self.establishment.id, emission='001', environment='2',
            line_ids=[(0, 0, dict(l10n_latam_document_type_id=self.dt_invoice.id))]))
        self.assertEqual(prod.line_ids.next_number, 1, 'Los números de pruebas no cuentan en producción')
        # En otro diario empieza en 1: el SRI de producción no conoce la factura de pruebas.
        journal = self.journal.copy(dict(name='Ventas producción', code='VPRO'))
        invoice = self._invoice(point=prod, journal_id=journal.id)
        self.assertTrue(invoice.name.endswith('001-001-000000001'), invoice.name)
        # En el diario de la factura de pruebas se salta su número (Odoo: nombre único por diario).
        same = self._invoice(point=prod)
        self.assertTrue(same.name.endswith('001-001-000000002'), same.name)
