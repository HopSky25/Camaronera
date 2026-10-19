"""Permisos Ecuador SRI: NO / Usuario / Administrador, probados con usuarios reales."""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, tagged

@tagged('post_install','-at_install')
class TestSriPermissions(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ec=cls.env.ref('base.ec')
        cls.company=cls.env['res.company'].create(dict(name='SRI PERMISOS',country_id=ec.id,vat='1790016919001',
            currency_id=cls.env.ref('base.USD').id))
        cls.env.user.company_ids|=cls.company
        cls.env=cls.env(context=dict(cls.env.context,allowed_company_ids=[cls.company.id]))
        cls.env['account.chart.template'].try_loading('ec',company=cls.company,install_demo=False)
        cls.company=cls.env['res.company'].browse(cls.company.id)
        cls.company.write(dict(ec_sri_enabled=True,ec_sri_address='Quito'))
        cls.journal=cls.env['account.journal'].search([('company_id','=',cls.company.id),('type','=','sale'),
            ('l10n_latam_use_documents','=',True)],limit=1)
        cls.establishment=cls.env['ec.sri.establishment'].create(dict(name='Matriz',code='001',company_id=cls.company.id,address='Quito'))
        cls.point=cls.env['ec.sri.point'].create(dict(name='Caja',establishment_id=cls.establishment.id,emission='001',
            line_ids=[(0,0,dict(l10n_latam_document_type_id=cls.env.ref('l10n_ec.ec_dt_01').id))]))
        cls.partner=cls.env['res.partner'].create(dict(name='CLIENTE',vat='1790011674001',country_id=ec.id,
            l10n_latam_identification_type_id=cls.env.ref('l10n_ec.ec_ruc').id))
        def user(login,groups):
            return cls.env['res.users'].with_context(no_reset_password=True).create(dict(name=login,login=login,
                company_id=cls.company.id,company_ids=[(6,0,cls.company.ids)],group_ids=[(6,0,[cls.env.ref(g).id for g in groups])]))
        cls.no_sri=user('factura_sin_sri',['account.group_account_invoice'])
        cls.sri_user=user('sri_usuario',['l10n_ec_sri_community.group_sri_user'])
        cls.sri_manager=user('sri_admin',['l10n_ec_sri_community.group_sri_manager'])

    def _invoice(self,user):
        env=self.env(user=user,context=dict(self.env.context,allowed_company_ids=[self.company.id]))
        move=env['account.move'].create(dict(move_type='out_invoice',partner_id=self.partner.id,journal_id=self.journal.id,
            ec_sri_establishment_id=self.establishment.id,ec_sri_point_id=self.point.id,
            invoice_line_ids=[(0,0,dict(name='Camarón',quantity=1,price_unit=10,tax_ids=[(5,0,0)]))]))
        move.action_post()
        return move

    def test_levels(self):
        sri=self.env.ref('l10n_ec_sri_community.group_sri_user')
        self.assertFalse(self.no_sri.has_group('l10n_ec_sri_community.group_sri_user'))
        self.assertTrue(self.sri_manager.has_group('l10n_ec_sri_community.group_sri_user'))
        self.assertTrue(self.sri_user.has_group('account.group_account_invoice'))
        self.assertEqual(sri.privilege_id.name,'Ecuador SRI')

    def test_no_sri_can_invoice_but_not_emit(self):
        move=self._invoice(self.no_sri)
        self.assertEqual(move.name,'Fact 001-001-000000001')
        self.assertFalse(move.ec_sri_state)
        Form(move.with_user(self.no_sri))  # el formulario de la factura abre sin errores de acceso
        with self.assertRaises(AccessError):
            self.env['ec.sri.document'].with_user(self.no_sri).search([])
        with self.assertRaises(AccessError):
            move.with_user(self.no_sri).action_ec_sri_document()
        with self.assertRaises(AccessError):
            self.env['ec.sri.point'].with_user(self.no_sri).create(dict(name='X',establishment_id=self.establishment.id,emission='009'))
        move.with_user(self.no_sri).button_draft()  # leer el estado SRI usa sudo

    def test_user_emits_but_does_not_administer(self):
        move=self._invoice(self.sri_user)
        action=move.with_user(self.sri_user).action_ec_sri_document()
        doc=self.env['ec.sri.document'].with_user(self.sri_user).browse(action['res_id'])
        self.assertEqual(doc.point_id,self.point)
        with self.assertRaises(AccessError):
            self.env['ec.sri.point'].with_user(self.sri_user).create(dict(name='X',establishment_id=self.establishment.id,emission='009'))
        # Aunque sea administrador de Odoo, sin Administrador SRI no cambia la configuración SRI.
        self.sri_user.group_ids|=self.env.ref('base.group_system')
        with self.assertRaises(UserError):
            self.company.with_user(self.sri_user).write(dict(ec_sri_address='Otra'))
        with self.assertRaises(AccessError):
            doc.with_user(self.sri_user).action_record_cancellation()

    def test_manager_administers(self):
        point=self.env['ec.sri.point'].with_user(self.sri_manager).create(dict(name='Caja 9',
            establishment_id=self.establishment.id,emission='009'))
        self.assertTrue(point)
        # Los ajustes de la compañía son de Ajustes de Odoo: además requieren administrador de Odoo.
        self.sri_manager.group_ids|=self.env.ref('base.group_system')
        self.company.with_user(self.sri_manager).write(dict(ec_sri_address='Guayaquil'))
        self.assertEqual(self.company.ec_sri_address,'Guayaquil')
