"""El certificado y su contraseña nunca quedan en claro (create, write, migración)."""
import base64
import importlib.util
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from odoo.tests import TransactionCase, tagged
from ..services import crypto

PASSWORD = 'ClaveSecreta123'


@tagged('post_install', '-at_install')
class TestSriCertCrypto(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, 'TEST CERTIFICATE')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=1)).sign(key, hashes.SHA256()))
        cls.p12 = pkcs12.serialize_key_and_certificates(
            b'test', key, cert, None, serialization.BestAvailableEncryption(PASSWORD.encode()))

    def setUp(self):
        super().setUp()
        env_patch = patch.dict(os.environ, {crypto.MASTER_ENV: 'dummy-test-key'})
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def _clear_text(self, company):
        self.env.flush_all()
        self.env.cr.execute('SELECT ec_sri_cert_password FROM res_company WHERE id = %s', (company.id,))
        password = self.env.cr.fetchone()[0]
        self.env.cr.execute("SELECT count(*) FROM ir_attachment WHERE res_model = 'res.company' "
                            "AND res_field = 'ec_sri_cert_file' AND res_id = %s", (company.id,))
        return password, self.env.cr.fetchone()[0]

    def test_create_encrypts_certificate(self):
        company = self.env['res.company'].create(dict(
            name='SRI CERT CREATE', ec_sri_cert_file=base64.b64encode(self.p12), ec_sri_cert_password=PASSWORD))
        self.assertEqual(self._clear_text(company), (None, 0))
        self.assertTrue(company.ec_sri_cert_data and company.ec_sri_cert_password_enc and company.ec_sri_cert_salt)
        self.assertNotIn(PASSWORD, company.ec_sri_cert_password_enc)
        self.assertEqual(company._ec_sri_certificate(), (self.p12, PASSWORD))
        self.assertEqual(company.ec_sri_cert_subject, 'CN=TEST CERTIFICATE')

    def test_write_encrypts_certificate(self):
        company = self.env['res.company'].create(dict(name='SRI CERT WRITE'))
        company.write(dict(ec_sri_cert_file=base64.b64encode(self.p12), ec_sri_cert_password=PASSWORD))
        self.assertEqual(self._clear_text(company), (None, 0))
        self.assertEqual(company._ec_sri_certificate(), (self.p12, PASSWORD))

    def test_master_key_file(self):
        with tempfile.NamedTemporaryFile('w', suffix='.key') as fh:
            fh.write('dummy-test-key\n')
            fh.flush()
            with patch.dict(os.environ, {crypto.MASTER_FILE_ENV: fh.name}):
                os.environ.pop(crypto.MASTER_ENV)
                salt = crypto.new_salt()
                token = crypto.encrypt(b'x', salt)
            # Misma llave por variable o por archivo.
            self.assertEqual(crypto.decrypt(token, salt), b'x')

    def test_migration_encrypts_leftover_clear_text(self):
        company = self.env['res.company'].create(dict(name='SRI CERT LEGACY'))
        # Estado que dejaba la versión anterior al crear: contraseña en la columna, .p12 como adjunto.
        self.env.cr.execute('UPDATE res_company SET ec_sri_cert_password = %s WHERE id = %s', (PASSWORD, company.id))
        self.env['ir.attachment'].create(dict(name='ec_sri_cert_file', res_model='res.company', res_id=company.id,
                                              res_field='ec_sri_cert_file', datas=base64.b64encode(self.p12)))
        self.env.invalidate_all()
        self.assertEqual(self._clear_text(company), (PASSWORD, 1))
        path = Path(__file__).parents[1] / 'migrations' / '19.0.1.2.1' / 'post-migrate.py'
        spec = importlib.util.spec_from_file_location('sri_mig_19_0_1_2_1', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, '19.0.1.2.0')
        self.env.invalidate_all()
        self.assertEqual(self._clear_text(company), (None, 0))
        self.assertEqual(company._ec_sri_certificate(), (self.p12, PASSWORD))
        data = company.ec_sri_cert_data
        module.migrate(self.env.cr, '19.0.1.2.0')  # idempotente
        self.env.invalidate_all()
        self.assertEqual(company.ec_sri_cert_data, data)
