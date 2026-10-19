"""Numeración desde el punto de emisión: 001-002 + próximo secuencial de su línea."""
from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged

@tagged('post_install','-at_install')
class TestPointNumbering(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ec=cls.env.ref('base.ec')
        cls.company=cls.env['res.company'].create(dict(name='SRI NUMERACION',country_id=ec.id,vat='1790016919001',
            currency_id=cls.env.ref('base.USD').id))
        cls.env.user.company_ids|=cls.company
        cls.env=cls.env(context=dict(cls.env.context,allowed_company_ids=[cls.company.id]))
        cls.env['account.chart.template'].try_loading('ec',company=cls.company,install_demo=False)
        cls.company=cls.env['res.company'].browse(cls.company.id)
        cls.company.ec_sri_enabled=True
        cls.journal=cls.env['account.journal'].search([('company_id','=',cls.company.id),('type','=','sale'),
            ('l10n_latam_use_documents','=',True)],limit=1)
        cls.invoice_type=cls.env.ref('l10n_ec.ec_dt_01')
        cls.establishment=cls.env['ec.sri.establishment'].create(dict(name='Matriz',code='001',company_id=cls.company.id,address='Quito'))
        cls.point=cls.env['ec.sri.point'].create(dict(name='Caja 2',establishment_id=cls.establishment.id,emission='002',
            line_ids=[(0,0,dict(l10n_latam_document_type_id=cls.invoice_type.id))]))
        cls.partner=cls.env['res.partner'].create(dict(name='CLIENTE',vat='1790011674001',country_id=ec.id,
            l10n_latam_identification_type_id=cls.env.ref('l10n_ec.ec_ruc').id))

    def _invoice(self,point=True):
        vals=dict(move_type='out_invoice',partner_id=self.partner.id,journal_id=self.journal.id,
            invoice_line_ids=[(0,0,dict(name='Camarón',quantity=1,price_unit=10))])
        if point: vals.update(ec_sri_establishment_id=self.establishment.id,ec_sri_point_id=self.point.id)
        return self.env['account.move'].create(vals)

    def test_line_uses_native_document_type(self):
        line=self.point.line_ids
        self.assertEqual(line.l10n_latam_document_type_id,self.invoice_type)
        self.assertEqual(line.document_type,'01')
        self.assertTrue(self.env.ref('l10n_ec_sri_community.ec_dt_06_delivery_guide').code=='06')

    def test_number_comes_from_point(self):
        first=self._invoice()
        self.assertEqual(first.ec_sri_next_number,'001-002-000000001')
        first.action_post()
        self.assertEqual(first.name,'Fact 001-002-000000001')
        second=self._invoice(); second.action_post()
        self.assertEqual(second.name,'Fact 001-002-000000002')
        self.assertEqual(self.point.line_ids.next_number,3)

    def test_repost_keeps_number(self):
        move=self._invoice(); move.action_post(); name=move.name
        move.button_draft(); move.action_post()
        self.assertEqual(move.name,name)
        self.assertEqual(self.point.line_ids.next_number,2)

    def test_point_required(self):
        with self.assertRaises(UserError): self._invoice(point=False).action_post()

    def test_form_hides_manual_number(self):
        form=Form(self.env['account.move'].with_context(default_move_type='out_invoice'))
        form.partner_id=self.partner; form.journal_id=self.journal
        form.ec_sri_establishment_id=self.establishment; form.ec_sri_point_id=self.point
        self.assertEqual(form.ec_sri_next_number,'001-002-000000001')

    def test_new_line_skips_used_numbers(self):
        self._invoice().action_post(); self._invoice().action_post()
        other=self.env['ec.sri.point'].create(dict(name='Caja 2 bis',establishment_id=self.establishment.id,emission='002',
            environment='2',line_ids=[(0,0,dict(l10n_latam_document_type_id=self.invoice_type.id))]))
        self.assertEqual(other.line_ids.next_number,3)

    def test_journal_takes_codes_from_point(self):
        journal=self.journal.copy()
        journal.ec_sri_point_id=self.point
        self.assertEqual((journal.l10n_ec_entity,journal.l10n_ec_emission),('001','002'))
        self.assertEqual(journal.ec_sri_establishment_id,self.establishment)
        with self.assertRaises(UserError): journal.l10n_ec_emission='009'
        self.assertIn(journal,self.point.journal_ids)

    def test_journal_point_used_on_invoice(self):
        journal=self.journal.copy(); journal.ec_sri_point_id=self.point
        form=Form(self.env['account.move'].with_context(default_move_type='out_invoice'))
        form.partner_id=self.partner; form.journal_id=journal
        self.assertEqual(form.ec_sri_point_id,self.point)
        # Facturas creadas por código (ventas, suscripciones): toman el punto de su diario al confirmar.
        move=self.env['account.move'].create(dict(move_type='out_invoice',partner_id=self.partner.id,journal_id=journal.id,
            invoice_line_ids=[(0,0,dict(name='x',quantity=1,price_unit=10))]))
        move.action_post()
        self.assertEqual(move.ec_sri_point_id,self.point)
        self.assertEqual(move.name,'Fact 001-002-000000001')
