"""Odoo registry/ORM integration tests. Run with --test-enable after installation."""
import base64
from datetime import date, datetime, timezone, timedelta
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import AccessError, UserError
from ..services import builders, xml_utils, transport

@tagged('post_install','-at_install')
class TestSriDocument(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company=cls.env['res.company'].create(dict(name='SRI TEST',vat='1790016919001',
            country_id=cls.env.ref('base.ec').id,ec_sri_enabled=True,ec_sri_address='Quito'))
        cls.partner=cls.env['res.partner'].create(dict(name='RECEPTOR TEST',vat='1790011674001',
            country_id=cls.env.ref('base.ec').id,ec_sri_identification_type='04'))
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'TEST CERTIFICATE')])
        now=datetime.now(timezone.utc)
        certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
            .not_valid_after(now+timedelta(days=1)).sign(key,hashes.SHA256()))
        cls.p12=pkcs12.serialize_key_and_certificates(b'test',key,certificate,None,serialization.BestAvailableEncryption(b'test'))

    def _document(self):
        key=xml_utils.access_key(date(2026,9,11),'01',self.company.vat,'1','001','001','000000001','12345678')
        data=dict(date='11/09/2026',address='Quito',partner=dict(type='04',vat=self.partner.vat,name=self.partner.name),
            total=115,lines=[dict(code='T',description='TEST',quantity=1,unit_price=100,discount=0,subtotal=100,
                taxes=[dict(code='2',percentage='4',rate=15,base=100,amount=15)])],payments=[dict(code='20',amount=115)])
        raw=builders.invoice('01',self.company._ec_sri_company_data(),key,data)
        return self.env['ec.sri.document'].with_company(self.company).create(dict(company_id=self.company.id,partner_id=self.partner.id,
            mode='xml',date=date(2026,9,11),document_type='01',import_xml=base64.b64encode(raw)))

    def _queued(self):
        document=self._document()
        with patch.object(type(self.company),'_ec_sri_certificate',return_value=(self.p12,'test')):
            document.action_queue()
        return document

    def test_queue_immutable_payload(self):
        document=self._queued()
        self.assertEqual(document.state,'queued')
        self.assertTrue(document.signed_xml)
        with self.assertRaises(AccessError): document.write({'state':'authorized'})
        with self.assertRaises(UserError): document.write({'import_xml':False})
        with self.assertRaises(UserError): document.unlink()

    def test_correction_preserves_number(self):
        document=self._queued();key=document.access_key
        document._set({'state':'rejected'})
        document.action_correct()
        self.assertEqual(document.state,'draft');self.assertEqual(document.access_key,key)
        self.assertTrue(document.event_ids.filtered(lambda e:e.operation=='correction'))

    def test_timeout_is_not_rejection(self):
        document=self._queued()
        with patch.object(transport.SriClient,'receive',side_effect=transport.SriTransportError('Timeout')):
            document._process()
        self.assertEqual(document.state,'queued');self.assertTrue(document.was_sent)
        self.assertTrue(document.next_attempt)

    def test_pending_authorization_no_resend(self):
        document=self._queued();document._set({'state':'waiting','was_sent':True})
        with patch.object(transport.SriClient,'authorize',return_value={'state':'PENDING','messages':[],'raw':b'<response/>'}), \
             patch.object(transport.SriClient,'receive') as receive:
            document._process();receive.assert_not_called()
        self.assertEqual(document.state,'waiting')

    def test_confirmed_reception_then_pending(self):
        document=self._queued()
        with patch.object(transport.SriClient,'receive',return_value={'state':'RECIBIDA','messages':[],'codes':[],'raw':b'<response/>'}), \
             patch.object(transport.SriClient,'authorize',return_value={'state':'PENDING','messages':[],'raw':b'<response/>'}):
            document._process()
        self.assertEqual(document.state,'waiting')

    def test_bad_p12_rolls_back_preparation(self):
        document=self._document()
        with patch.object(type(self.company),'_ec_sri_certificate',return_value=(b'bad','bad')):
            with self.assertRaises(UserError): document.action_queue()
        self.assertEqual(document.state,'draft');self.assertFalse(document.signed_xml)
