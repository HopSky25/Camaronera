"""scripts/configurar_sri.py contra los modelos actuales (F9): idempotente, sin
encender el SRI salvo --activar y con la lista de pendientes."""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


def _script():
    path = Path(__file__).parents[1] / 'scripts' / 'configurar_sri.py'
    spec = importlib.util.spec_from_file_location('l10n_ec_sri_configurar_sri', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged('post_install', '-at_install')
class TestConfigurarSri(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.script = _script()
        ec = cls.env.ref('base.ec')
        cls.company = cls.env['res.company'].create(dict(
            name='SCRIPT SRI TEST', country_id=ec.id, currency_id=cls.env.ref('base.USD').id))
        cls.env.user.company_ids |= cls.company
        cls.env['account.chart.template'].try_loading('ec', company=cls.company, install_demo=False)
        cls.company.sudo().ec_sri_enabled = False

    def _datos(self, *args):
        return self.script.leer_datos(['--compania', str(self.company.id)] + list(args), environ={})

    def test_configura_y_es_idempotente(self):
        datos = self._datos('--ruc', '1790016919001', '--razon-social', 'SCRIPT SRI S.A.S.',
                            '--nombre-comercial', 'Script', '--direccion-matriz', 'Av. Principal, Quito',
                            '--regimen', 'rimpe', '--obligado-contabilidad', 'no', '--agente-retencion', '1')
        company, informe = self.script.configurar(self.env, datos)
        self.assertEqual(company, self.company)
        self.assertEqual((company.vat, company.name, company.ec_sri_trade_name, company.ec_sri_address),
                         ('1790016919001', 'SCRIPT SRI S.A.S.', 'Script', 'Av. Principal, Quito'))
        self.assertEqual((company.ec_sri_regime, company.ec_sri_accounting, company.ec_sri_agent),
                         ('rimpe', False, '1'))
        self.assertFalse(company.ec_sri_enabled, 'Sin --activar el SRI sigue apagado')
        self.assertEqual(company.ec_sri_environment, '1')
        est = self.env['ec.sri.establishment'].search([('company_id', '=', company.id)])
        self.assertEqual((est.code, est.address, est.is_headquarters), ('001', 'Av. Principal, Quito', True))
        point = self.env['ec.sri.point'].search([('company_id', '=', company.id)])
        self.assertEqual((point.emission, point.environment), ('001', '1'))
        self.assertEqual(sorted(point.line_ids.mapped('document_type')), ['01', '04'])
        if 'shrimp_service_journal_id' in company._fields:
            journal = company.shrimp_service_journal_id
            self.assertTrue(journal.l10n_latam_use_documents)
            self.assertEqual(journal.ec_sri_point_id, point)
            self.assertEqual(company.shrimp_service_sri_point_id, point)
            self.assertEqual(company.shrimp_service_payment_id.code, '20')
        with patch.dict(os.environ):
            os.environ.pop('EC_SRI_MASTER_KEY', None)
            os.environ.pop('EC_SRI_MASTER_KEY_FILE', None)
            pendientes = ' '.join(self.script.pendientes(self.env, company))
        self.assertIn('.p12', pendientes)
        self.assertIn('EC_SRI_MASTER_KEY', pendientes)
        self.assertIn('APAGADO', pendientes)
        # Segunda pasada: nada nuevo.
        self.script.configurar(self.env, datos)
        self.assertEqual(self.env['ec.sri.establishment'].search_count([('company_id', '=', company.id)]), 1)
        self.assertEqual(self.env['ec.sri.point'].search_count([('company_id', '=', company.id)]), 1)
        self.assertEqual(len(point.line_ids), 2)
        # --activar lo enciende; nunca pasa solo a producción.
        self.script.configurar(self.env, self._datos('--activar'))
        self.assertTrue(company.ec_sri_enabled)
        self.assertEqual(company.ec_sri_environment, '1')
        self.assertEqual(company.vat, '1790016919001', 'Lo que no se pasa no se toca')

    def test_variables_de_entorno_y_validacion(self):
        datos = self.script.leer_datos(None, environ={
            'SRI_ARGS': '--compania %s --punto 002' % self.company.id, 'SRI_RUC': '1790016919001',
            'SRI_DIRECCION_MATRIZ': 'Guayaquil'})
        self.assertEqual((datos['ruc'], datos['punto'], datos['direccion_matriz']), ('1790016919001', '002', 'Guayaquil'))
        self.script.configurar(self.env, datos)
        self.assertEqual(self.env['ec.sri.point'].search([('company_id', '=', self.company.id)]).emission, '002')
        with self.assertRaises(self.script.ConfigError):
            self.script.configurar(self.env, self._datos('--ruc', '123'))
        with self.assertRaises(self.script.ConfigError):
            self.script.configurar(self.env, self._datos('--activar', '--desactivar'))
