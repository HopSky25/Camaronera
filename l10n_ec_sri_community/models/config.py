import os
import re
from pathlib import Path
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

DOC_TYPES=[('01','Factura'),('03','Liquidación de compra'),('04','Nota de crédito'),
           ('05','Nota de débito'),('06','Guía de remisión'),('07','Retención')]
IDENT_TYPES=[('04','RUC'),('05','Cédula'),('06','Pasaporte'),('07','Consumidor final'),('08','Identificación exterior')]

class Company(models.Model):
    _inherit='res.company'
    ec_sri_enabled=fields.Boolean('Habilitar SRI Community')
    ec_sri_environment=fields.Selection([('1','Pruebas'),('2','Producción')],default='1',required=True,string='Ambiente SRI')
    ec_sri_production_ready=fields.Boolean('Pruebas de homologación completadas')
    ec_sri_trade_name=fields.Char('Nombre comercial')
    ec_sri_address=fields.Char('Dirección matriz SRI')
    ec_sri_special=fields.Char('Número contribuyente especial')
    ec_sri_agent=fields.Char('Resolución agente de retención')
    ec_sri_accounting=fields.Boolean('Obligado a llevar contabilidad',default=True)
    ec_sri_regime=fields.Selection([('regular','General'),('rimpe','RIMPE emprendedor'),('popular','RIMPE negocio popular')],default='regular',required=True)
    ec_sri_provider_ruc=fields.Char('RUC proveedor del sistema')
    ec_sri_large_taxpayer=fields.Char('Resolución gran contribuyente')
    ec_sri_secret_name=fields.Char('Prefijo de variables del certificado',groups='base.group_system',
        help='Ejemplo EC_SRI_EMPRESA1. El servidor debe definir EC_SRI_EMPRESA1_P12_PATH y EC_SRI_EMPRESA1_P12_PASSWORD.')
    ec_sri_establishments=fields.Integer('Establecimientos registrados en RUC',default=1)

    def write(self,vals):
        if any(k.startswith('ec_sri_') for k in vals) and not self.env.su and not self.env.user.has_group('account.group_account_manager'):
            raise UserError(_('Solo un administrador contable puede configurar la conexión SRI.'))
        return super().write(vals)

    def _ec_sri_company_data(self):
        self.ensure_one()
        if not self.ec_sri_enabled or self.country_id.code!='EC': raise UserError(_('Habilite SRI en una empresa de Ecuador.'))
        if self.ec_sri_environment=='2' and not self.ec_sri_production_ready:
            raise UserError(_('Complete las pruebas SRI y habilite producción en la empresa.'))
        if not re.fullmatch(r'[0-9]{13}',self.vat or ''): raise UserError(_('Configure el RUC de 13 dígitos.'))
        if not self.ec_sri_address: raise UserError(_('Configure la dirección matriz SRI.'))
        return dict(name=self.name,vat=self.vat,address=self.ec_sri_address,trade_name=self.ec_sri_trade_name,
                    accounting='SI' if self.ec_sri_accounting else 'NO',special=self.ec_sri_special,agent=self.ec_sri_agent,
                    provider_ruc=self.ec_sri_provider_ruc,large_taxpayer=self.ec_sri_large_taxpayer,
                    rimpe={'rimpe':'CONTRIBUYENTE RÉGIMEN RIMPE','popular':'CONTRIBUYENTE NEGOCIO POPULAR - RÉGIMEN RIMPE'}.get(self.ec_sri_regime))

    def _ec_sri_certificate(self):
        self.ensure_one()
        prefix=self.sudo().ec_sri_secret_name or ''
        if not re.fullmatch(r'EC_SRI_[A-Z0-9_]+',prefix):
            raise UserError(_('Un administrador debe configurar el prefijo de secretos EC_SRI_...'))
        path=os.environ.get(prefix+'_P12_PATH')
        password=os.environ.get(prefix+'_P12_PASSWORD')
        if not path or password is None: raise UserError(_('Faltan las variables de entorno del certificado.'))
        try:
            file=Path(path)
            if file.stat().st_size>1024*1024: raise ValueError()
            return file.read_bytes(),password
        except (OSError,ValueError) as exc:
            raise UserError(_('No se pudo leer el certificado configurado en el servidor.')) from exc

class Partner(models.Model):
    _inherit='res.partner'
    ec_sri_identification_type=fields.Selection(IDENT_TYPES,string='Identificación SRI')
    ec_sri_related=fields.Boolean('Parte relacionada')

    def _ec_sri_partner_data(self):
        self.ensure_one()
        partner=self.commercial_partner_id
        code=partner.ec_sri_identification_type
        if not code:
            raise UserError(_('Configure Identificación SRI en el contacto %s.',partner.display_name))
        vat='9999999999999' if code=='07' else partner.vat
        if not vat: raise UserError(_('Falta identificación en %s.',partner.display_name))
        if code in ('04','05') and not re.fullmatch(r'[0-9]{%d}' % (13 if code=='04' else 10),vat):
            raise UserError(_('Longitud de identificación incorrecta en %s.',partner.display_name))
        return dict(type=code,vat=vat,name='CONSUMIDOR FINAL' if code=='07' else partner.name,
                    address=', '.join(filter(None,[self.street,self.street2,self.city])),
                    related=partner.ec_sri_related,subject_type='02' if partner.is_company else '01')

class Tax(models.Model):
    _inherit='account.tax'
    ec_sri_tax_code=fields.Selection([('2','IVA'),('3','ICE'),('5','IRBPNR')],string='Impuesto XML SRI')
    ec_sri_percentage_code=fields.Char('Código porcentaje SRI',help='Código del catálogo SRI, no el porcentaje numérico. Configurar incluso IVA 0, no objeto y exento.')
    ec_sri_ats_bucket=fields.Selection([('zero','IVA cero'),('taxable','IVA gravado'),('non_taxable','No objeto'),('exempt','Exento'),('ice','ICE'),('other','Otro')],string='Base ATS')
    ec_sri_form=fields.Selection([('103','103'),('104','104')],string='Formulario auxiliar')
    ec_sri_base_box=fields.Char('Casillero base')
    ec_sri_tax_box=fields.Char('Casillero impuesto')

class Product(models.Model):
    _inherit='product.template'
    ec_sri_auxiliary_code=fields.Char('Código auxiliar SRI',help='Incluya códigos sectoriales cuando correspondan.')

class EmissionPoint(models.Model):
    _name='ec.sri.point'
    _description='Punto de emisión SRI'
    _check_company_auto=True
    name=fields.Char(required=True)
    company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company)
    establishment=fields.Char('Establecimiento',size=3,required=True,default='001')
    emission=fields.Char('Punto de emisión',size=3,required=True,default='001')
    address=fields.Char('Dirección establecimiento',required=True)
    document_type=fields.Selection(DOC_TYPES,required=True,string='Documento')
    environment=fields.Selection([('1','Pruebas'),('2','Producción')],default='1',required=True)
    sequence_id=fields.Many2one('ir.sequence',required=True,check_company=True,copy=False)
    next_number=fields.Integer('Próximo secuencial',compute='_compute_next',inverse='_inverse_next')
    active=fields.Boolean(default=True)
    _point_unique=models.Constraint('UNIQUE(company_id, establishment, emission, document_type, environment)','El punto ya existe para ese documento y ambiente.')

    @api.depends('sequence_id.number_next_actual')
    def _compute_next(self):
        for rec in self: rec.next_number=rec.sequence_id.number_next_actual if rec.sequence_id else 1

    def _inverse_next(self):
        if not self.env.su and not self.env.user.has_group('account.group_account_manager'):
            raise UserError(_('Requiere administrador contable.'))
        for rec in self:
            if not 1<=rec.next_number<=999999999: raise UserError(_('Secuencial fuera de rango.'))
            rec.sequence_id.sudo().number_next_actual=rec.next_number

    @api.model_create_multi
    def create(self,vals_list):
        for vals in vals_list:
            if not vals.get('sequence_id'):
                company_id=vals.get('company_id',self.env.company.id)
                if company_id not in self.env.companies.ids: raise UserError(_('Empresa no habilitada.'))
                seq=self.env['ir.sequence'].sudo().create(dict(name='SRI '+vals.get('name',''),company_id=company_id,
                    padding=9,implementation='no_gap',number_next=1,number_increment=1))
                vals['sequence_id']=seq.id
        return super().create(vals_list)

    def write(self,vals):
        if set(vals)&{'company_id','establishment','emission','document_type','environment','sequence_id'}:
            if self.env['ec.sri.document'].search_count([('point_id','in',self.ids),('access_key','!=',False)]):
                raise UserError(_('No cambie la identidad de un punto que ya emitió documentos; cree otro punto.'))
        return super().write(vals)

    @api.constrains('establishment','emission','sequence_id')
    def _check_format(self):
        for rec in self:
            if not re.fullmatch('[0-9]{3}',rec.establishment or '') or not re.fullmatch('[0-9]{3}',rec.emission or ''):
                raise ValidationError(_('Establecimiento y punto deben tener tres dígitos.'))
            if rec.sequence_id.prefix or rec.sequence_id.suffix or rec.sequence_id.padding!=9 or rec.sequence_id.use_date_range:
                raise ValidationError(_('Use secuencia sin prefijo/sufijo, relleno 9 y sin rangos de fecha.'))
