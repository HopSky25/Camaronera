"""Base común de las pruebas de emisión: una compañía de Ecuador con el plan
contable, el IVA con sus códigos del SRI, la matriz 001 y su punto 001 (01 y
04), y un certificado autofirmado generado aquí.

Nada sale a la red: SriClient._request falla si alguien lo llama sin simular,
y receive/authorize se reemplazan en cada prueba con sri(mode)."""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree

from odoo.tests import TransactionCase

from ..hooks import _configurar_impuestos
from ..services import transport, validators

CERT_PASSWORD = 'ClaveSecreta123'


def make_p12(password=CERT_PASSWORD, common_name='TEST CERTIFICATE', days=30):
    """Certificado .p12 autofirmado (RSA 2048, vigente ``days`` días)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=days)).sign(key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(
        b'test', key, cert, None, serialization.BestAvailableEncryption(password.encode()))


def authorization_xml(doc, date='2026-10-14T10:00:00-05:00'):
    """<autorizacion> como la devuelve el SRI: con el comprobante firmado dentro."""
    auth = etree.Element('autorizacion')
    for tag, value in (('estado', 'AUTORIZADO'), ('numeroAutorizacion', doc.access_key),
                       ('fechaAutorizacion', date),
                       ('ambiente', 'PRODUCCIÓN' if doc.environment == '2' else 'PRUEBAS')):
        etree.SubElement(auth, tag).text = value
    signed = base64.b64decode(doc.with_context(bin_size=False).signed_xml).decode('utf-8')
    etree.SubElement(auth, 'comprobante').text = etree.CDATA(signed)
    return etree.tostring(auth, encoding='utf-8')


class SriEmissionCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ec = cls.env.ref('base.ec')
        cls.company = cls.env['res.company'].create(dict(
            name='SRI EMISION TEST', country_id=ec.id, vat='1790016919001',
            currency_id=cls.env.ref('base.USD').id, email='facturacion@emisor.test'))
        cls.env.user.company_ids |= cls.company
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.env['account.chart.template'].try_loading('ec', company=cls.company, install_demo=False)
        cls.company = cls.env['res.company'].browse(cls.company.id)
        _configurar_impuestos(cls.env)
        cls.company.write(dict(ec_sri_enabled=True, ec_sri_environment='1', ec_sri_address='Av. Principal, Quito',
                               tax_calculation_rounding_method='round_per_line'))
        cls.p12 = make_p12()
        cls.journal = cls.env['account.journal'].search([
            ('company_id', '=', cls.company.id), ('type', '=', 'sale'),
            ('l10n_latam_use_documents', '=', True)], limit=1)
        cls.dt_invoice = cls.env.ref('l10n_ec.ec_dt_01')
        cls.dt_credit = cls.env.ref('l10n_ec.ec_dt_04')
        cls.establishment = cls.env['ec.sri.establishment'].create(dict(
            name='Matriz', code='001', company_id=cls.company.id, address='Quito'))
        cls.point = cls._new_point('001', '1')
        cls.payment = cls.env['l10n_ec.sri.payment'].search([('code', '=', '20')], limit=1) \
            or cls.env['l10n_ec.sri.payment'].search([], limit=1)
        cls.tax = cls.env['account.tax'].search([
            ('company_id', '=', cls.company.id), ('type_tax_use', '=', 'sale'),
            ('ec_sri_tax_code', '=', '2'), ('ec_sri_percentage_code', '=', '4')], limit=1)
        cls.partner = cls.env['res.partner'].create(dict(
            name='CLIENTE SRI', vat='1790011674001', country_id=ec.id, is_company=True,
            email='cliente@receptor.test', street='Calle 1', city='Quito',
            l10n_latam_identification_type_id=cls.env.ref('l10n_ec.ec_ruc').id))
        cls.billing = cls.env['res.partner'].create(dict(
            name='Cuentas por pagar', type='invoice', parent_id=cls.partner.id, email='pagos@receptor.test'))

    @classmethod
    def _new_point(cls, emission, environment):
        return cls.env['ec.sri.point'].create(dict(
            name='Punto %s' % emission, establishment_id=cls.establishment.id, emission=emission,
            environment=environment,
            line_ids=[(0, 0, dict(l10n_latam_document_type_id=cls.dt_invoice.id)),
                      (0, 0, dict(l10n_latam_document_type_id=cls.dt_credit.id))]))

    def setUp(self):
        super().setUp()
        cert = patch.object(type(self.company), '_ec_sri_certificate', return_value=(self.p12, CERT_PASSWORD))
        cert.start()
        self.addCleanup(cert.stop)
        red = patch.object(transport.SriClient, '_request', side_effect=AssertionError('llamada real al SRI'))
        red.start()
        self.addCleanup(red.stop)

    # ------------------------------------------------------------------
    def _production(self):
        """Compañía en PRODUCCIÓN con un punto 001-002 de producción."""
        self.company.write(dict(ec_sri_environment='2', ec_sri_production_ready=True))
        return self._new_point('002', '2')

    def _invoice(self, amount=100.0, point=None, post=True, **extra):
        point = point or self.point
        vals = dict(move_type='out_invoice', partner_id=self.partner.id, journal_id=self.journal.id,
                    ec_sri_establishment_id=point.establishment_id.id, ec_sri_point_id=point.id,
                    l10n_ec_sri_payment_id=self.payment.id,
                    invoice_line_ids=[(0, 0, dict(name='Servicio de prueba', quantity=1, price_unit=amount,
                                                  tax_ids=[(6, 0, self.tax.ids)]))])
        vals.update(extra)
        move = self.env['account.move'].create(vals)
        if post:
            move.action_post()
        return move

    @staticmethod
    def _doc(move, code='01'):
        return move.sudo().ec_sri_document_ids.filtered(lambda d: d.document_type == code)[:1]

    @staticmethod
    def today_patch(days):
        """Simula que «hoy» en Ecuador es dentro de ``days`` días."""
        return patch.object(validators, 'today_ec', return_value=validators.today_ec() + timedelta(days=days))

    def _authorized(self, client, key):
        doc = self.env['ec.sri.document'].sudo().with_context(bin_size=False).search([('access_key', '=', key)], limit=1)
        return {'state': 'AUTORIZADO', 'number': key, 'date': '2026-10-14T10:00:00-05:00',
                'document': base64.b64decode(doc.signed_xml).decode('utf-8'),
                'authorization': authorization_xml(doc), 'messages': [], 'raw': b'<response/>'}

    @contextmanager
    def sri(self, mode='authorized'):
        recibida = {'state': 'RECIBIDA', 'messages': [], 'codes': [], 'raw': b'<r/>'}
        if mode == 'authorized':
            rec = patch.object(transport.SriClient, 'receive', return_value=recibida)
            aut = patch.object(transport.SriClient, 'authorize', autospec=True, side_effect=self._authorized)
        else:
            rec = patch.object(transport.SriClient, 'receive', side_effect=transport.SriTransportError('Timeout'))
            aut = patch.object(transport.SriClient, 'authorize', side_effect=transport.SriTransportError('Timeout'))
        with rec, aut:
            yield

    def _process(self, doc, mode='authorized'):
        with self.sri(mode):
            doc._process()
        return doc
