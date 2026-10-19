"""Reglas del SRI: ficha técnica offline 2.34, Reglamento y Res. NAC-DGERCGC25-00000014/17."""
from datetime import date, timedelta
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from ..services import builders, validators, xml_utils

class TestValidators(TransactionCase):
    def test_identification_check_digit(self):
        for vat in ('1790016919001','1790011674001','1710034065','1710034065001'):
            self.assertTrue(validators.ec_identification_ok(vat),vat)
        for vat in ('1790016918001','1710034064','9999999999999','17900169190','2510034065'):
            self.assertFalse(validators.ec_identification_ok(vat),vat)

    def test_iva_table_17(self):
        self.assertTrue(validators.iva_rate_matches('4',15))
        self.assertTrue(validators.iva_rate_matches('5',5))
        self.assertTrue(validators.iva_rate_matches('7',0))
        self.assertTrue(validators.iva_rate_matches('8',8))
        self.assertFalse(validators.iva_rate_matches('4',12))
        self.assertFalse(validators.iva_rate_matches('9',15))

    def test_cancellation_deadline(self):
        self.assertEqual(validators.cancellation_deadline(date(2026,3,20)),date(2026,4,7))
        self.assertEqual(validators.cancellation_deadline(date(2026,1,15)),date(2026,2,9))  # 7 cae sábado
        self.assertEqual(validators.cancellation_deadline(date(2026,12,3)),date(2027,1,7))

    def test_builder_limits(self):
        key=xml_utils.access_key(date(2026,9,11),'01','1790016919001','1','001','001','000000001','12345678')
        company=dict(name='EMISOR',vat='1790016919001',address='Quito',accounting='SI')
        line=dict(code='P1',description='X',quantity=1,unit_price=10,discount=0,subtotal=10,
                  taxes=[dict(code='2',percentage='0',rate=0,base=10,amount=0)])
        data=dict(date='11/09/2026',address='Quito',partner=dict(type='04',vat='1790011674001',name='CLIENTE'),
                  total=10,lines=[line],payments=[dict(code='20',amount=10)])
        builders.invoice('01',company,key,data)
        with self.assertRaisesRegex(ValueError,'300'):
            builders.invoice('01',company,key,dict(data,lines=[dict(line,description='x'*301)]))
        with self.assertRaisesRegex(ValueError,'15 campos'):
            builders.invoice('01',company,key,dict(data,additional={'Campo %s' % n:'v' for n in range(16)}))
        with self.assertRaisesRegex(ValueError,'USD 50'):
            builders.invoice('01',company,key,dict(data,total=60,partner=dict(type='07',vat='9999999999999',name='CONSUMIDOR FINAL'),
                lines=[dict(line,subtotal=60,unit_price=60,taxes=[dict(code='2',percentage='0',rate=0,base=60,amount=0)])],
                payments=[dict(code='20',amount=60)]))

@tagged('post_install','-at_install')
class TestSriRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ec=cls.env.ref('base.ec')
        cls.company=cls.env['res.company'].create(dict(name='SRI REGLAS',country_id=ec.id,vat='1790016919001',
            currency_id=cls.env.ref('base.USD').id))
        cls.env.user.company_ids|=cls.company
        cls.env=cls.env(context=dict(cls.env.context,allowed_company_ids=[cls.company.id]))
        cls.env['account.chart.template'].try_loading('ec',company=cls.company,install_demo=False)
        cls.company=cls.env['res.company'].browse(cls.company.id)
        cls.company.ec_sri_enabled=True
        cls.journal=cls.env['account.journal'].search([('company_id','=',cls.company.id),('type','=','sale'),
            ('l10n_latam_use_documents','=',True)],limit=1)
        cls.establishment=cls.env['ec.sri.establishment'].create(dict(name='Matriz',code='001',company_id=cls.company.id,address='Quito'))
        cls.point=cls.env['ec.sri.point'].create(dict(name='Caja',establishment_id=cls.establishment.id,emission='001',
            line_ids=[(0,0,dict(l10n_latam_document_type_id=cls.env.ref('l10n_ec.ec_dt_01').id)),
                      (0,0,dict(l10n_latam_document_type_id=cls.env.ref('l10n_ec.ec_dt_04').id))]))
        cls.partner=cls.env['res.partner'].create(dict(name='CLIENTE',vat='1790011674001',country_id=ec.id,
            l10n_latam_identification_type_id=cls.env.ref('l10n_ec.ec_ruc').id))
        cls.final_consumer=cls.env['res.partner'].create(dict(name='CONSUMIDOR FINAL',vat='9999999999999',country_id=ec.id,
            l10n_latam_identification_type_id=cls.env.ref('l10n_ec.ec_ruc').id))

    def _move(self,partner=None,amount=10,move_type='out_invoice',**extra):
        vals=dict(move_type=move_type,partner_id=(partner or self.partner).id,journal_id=self.journal.id,
            ec_sri_establishment_id=self.establishment.id,ec_sri_point_id=self.point.id,
            invoice_line_ids=[(0,0,dict(name='Camarón',quantity=1,price_unit=amount,tax_ids=[(5,0,0)]))])
        vals.update(extra)
        return self.env['account.move'].create(vals)

    def test_final_consumer_limit(self):
        self._move(self.final_consumer,amount=50).action_post()
        with self.assertRaisesRegex(UserError,'USD 50'):
            self._move(self.final_consumer,amount=50.01).action_post()

    def test_future_date(self):
        with self.assertRaisesRegex(UserError,'futura'):
            self._move(invoice_date=validators.today_ec()+timedelta(days=1)).action_post()

    def test_chronological_order(self):
        self._move(invoice_date=validators.today_ec()).action_post()
        with self.assertRaisesRegex(UserError,'cronológico'):
            self._move(invoice_date=validators.today_ec()-timedelta(days=1)).action_post()

    def test_credit_note_cannot_exceed_invoice(self):
        invoice=self._move(amount=10); invoice.action_post()
        self._move(amount=6,move_type='out_refund',reversed_entry_id=invoice.id,ec_sri_reason='Devolución').action_post()
        with self.assertRaisesRegex(UserError,'superan'):
            self._move(amount=5,move_type='out_refund',reversed_entry_id=invoice.id,ec_sri_reason='Devolución').action_post()

    def test_production_requires_today(self):
        prod=self.env['ec.sri.point'].create(dict(name='Caja prod',establishment_id=self.establishment.id,emission='001',
            environment='2',line_ids=[(0,0,dict(l10n_latam_document_type_id=self.env.ref('l10n_ec.ec_dt_01').id))]))
        self.company.write(dict(ec_sri_environment='2',ec_sri_production_ready=True))
        with self.assertRaisesRegex(UserError,'hoy'):
            self._move(ec_sri_point_id=prod.id,invoice_date=validators.today_ec()-timedelta(days=1)).action_post()
        self._move(ec_sri_point_id=prod.id,invoice_date=validators.today_ec()).action_post()

    def test_tax_code_must_match_rate(self):
        tax=self.env['account.tax'].search([('company_id','=',self.company.id),('type_tax_use','=','sale'),('amount','=',15)],limit=1)
        with self.assertRaises(ValidationError):
            tax.write(dict(ec_sri_tax_code='2',ec_sri_percentage_code='2'))
        tax.write(dict(ec_sri_tax_code='2',ec_sri_percentage_code='4'))

    def test_company_formats(self):
        with self.assertRaises(ValidationError): self.company.ec_sri_agent='NO'
        with self.assertRaises(ValidationError): self.company.ec_sri_special='NO'
        self.company.write(dict(ec_sri_agent='1',ec_sri_special='12345'))

    def test_generic_vat_is_not_foreign(self):
        partner=self.env['res.partner'].create(dict(name='RUC GENERICO',vat='1790011674001',country_id=self.env.ref('base.ec').id,
            l10n_latam_identification_type_id=self.env.ref('l10n_latam_base.it_vat').id))
        self.assertEqual(partner._ec_sri_identification_code(),'04')

    def test_supplier_authorization_length(self):
        bill=self.env['account.move'].create(dict(move_type='in_invoice',partner_id=self.partner.id,
            invoice_date=validators.today_ec(),l10n_latam_document_number='001-001-000000001',
            ec_sri_supplier_authorization='123',ec_sri_support_code='01',
            invoice_line_ids=[(0,0,dict(name='x',quantity=1,price_unit=10))]))
        with self.assertRaisesRegex(UserError,'49 dígitos'):
            bill._ec_sri_support_data()
