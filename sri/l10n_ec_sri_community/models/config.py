import base64
import re
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from ..services import crypto, signature, validators

DOC_TYPES=[('01','Factura'),('03','Liquidación de compra'),('04','Nota de crédito'),
           ('05','Nota de débito'),('06','Guía de remisión'),('07','Retención')]

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
    ec_sri_establishments=fields.Integer('Establecimientos registrados en RUC',default=1)
    ec_sri_establishment_ids=fields.One2many('ec.sri.establishment','company_id',string='Establecimientos')
    # --- Certificado de firma (.p12): se SUBE por la interfaz y se guarda CIFRADO ---
    ec_sri_cert_file=fields.Binary('Subir certificado .p12',groups='base.group_system',
        help='El archivo se cifra con la llave EC_SRI_MASTER_KEY del servidor y solo se guarda cifrado.')
    ec_sri_cert_filename=fields.Char(groups='base.group_system')
    ec_sri_cert_password=fields.Char('Contraseña del certificado',groups='base.group_system',
        help='Se usa para validar y cifrar; no se guarda en claro.')
    ec_sri_cert_data=fields.Char(copy=False,groups='base.group_system')          # p12 cifrado
    ec_sri_cert_password_enc=fields.Char(copy=False,groups='base.group_system')  # contraseña cifrada
    ec_sri_cert_salt=fields.Char(copy=False,groups='base.group_system')
    ec_sri_cert_subject=fields.Char('Certificado',readonly=True,copy=False)
    ec_sri_cert_expiry=fields.Date('Vence',readonly=True,copy=False)

    @api.model_create_multi
    def create(self,vals_list):
        # Igual que write(): el .p12 y su contraseña no se guardan en claro al crear
        # (importación, XML-RPC, scripts); se cifran con el mismo helper.
        if any(k.startswith('ec_sri_') for vals in vals_list for k in vals) and not self.env.su \
                and not self.env.user.has_group('l10n_ec_sri_community.group_sri_manager'):
            raise UserError(_('Solo un Administrador de Ecuador SRI puede configurar la conexión SRI.'))
        certs=[(vals.pop('ec_sri_cert_file',None),vals.pop('ec_sri_cert_password',None)) for vals in vals_list]
        companies=super().create(vals_list)
        for company,(cert_b64,cert_pwd) in zip(companies,certs):
            if cert_b64:
                company._ec_sri_store_certificate(base64.b64decode(cert_b64),cert_pwd)
        return companies

    def write(self,vals):
        if any(k.startswith('ec_sri_') for k in vals) and not self.env.su and not self.env.user.has_group('l10n_ec_sri_community.group_sri_manager'):
            raise UserError(_('Solo un Administrador de Ecuador SRI puede configurar la conexión SRI.'))
        # El .p12 y su contraseña nunca se persisten en claro: se cifran y se limpian.
        cert_b64=vals.pop('ec_sri_cert_file',None)
        cert_pwd=vals.pop('ec_sri_cert_password',None)
        res=super().write(vals)
        if cert_b64:
            for company in self:
                company._ec_sri_store_certificate(base64.b64decode(cert_b64),cert_pwd)
        return res

    @api.constrains('ec_sri_special','ec_sri_agent','ec_sri_provider_ruc','ec_sri_trade_name','ec_sri_address')
    def _check_ec_sri_formats(self):
        for company in self:
            if company.ec_sri_special and not re.fullmatch(r'[A-Za-z0-9]{3,13}',company.ec_sri_special):
                raise ValidationError(_('El número de contribuyente especial es la resolución del SRI: de 3 a 13 caracteres '
                    'alfanuméricos. Déjelo vacío si no aplica.'))
            if company.ec_sri_agent and not re.fullmatch(r'[1-9][0-9]{0,7}',company.ec_sri_agent):
                raise ValidationError(_('La resolución de agente de retención es un número de hasta 8 dígitos, sin ceros '
                    'a la izquierda (por ejemplo 1). Déjela vacía si no aplica.'))
            if company.ec_sri_provider_ruc and not re.fullmatch(r'[0-9]{13}',company.ec_sri_provider_ruc):
                raise ValidationError(_('El RUC del proveedor del sistema debe tener 13 dígitos.'))
            for value,label in ((company.ec_sri_trade_name,_('El nombre comercial')),(company.ec_sri_address,_('La dirección matriz'))):
                if value and len(value)>validators.MAX_TEXT:
                    raise ValidationError(_('%(label)s supera %(max)s caracteres.',label=label,max=validators.MAX_TEXT))

    def _ec_sri_store_certificate(self,p12_bytes,password):
        """Valida el .p12, extrae metadatos y guarda el material CIFRADO."""
        self.ensure_one()
        if not password:
            raise UserError(_('Ingrese la contraseña del certificado para poder cifrarlo.'))
        if len(p12_bytes)>1024*1024:
            raise UserError(_('El certificado supera 1 MB; un .p12 de firma pesa unos pocos KB.'))
        try:
            load_key_and_certificates(p12_bytes,password.encode('utf-8'))
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('No se pudo abrir el .p12 con esa contraseña.')) from exc
        try:
            _key,cert=signature.load_certificate(p12_bytes,password)
        except ValueError as exc:
            raise UserError(_('El certificado no sirve para firmar comprobantes del SRI: %s',exc)) from exc
        salt=crypto.new_salt()
        subject=cert.subject.rfc4514_string()
        self.sudo().write(dict(
            ec_sri_cert_salt=salt,
            ec_sri_cert_data=crypto.encrypt(p12_bytes,salt),
            ec_sri_cert_password_enc=crypto.encrypt(password.encode('utf-8'),salt),
            ec_sri_cert_subject=subject[:120],
            ec_sri_cert_expiry=signature.validity(cert)[1].date(),
            ec_sri_cert_filename=self.ec_sri_cert_filename or 'certificado.p12'))

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
        company=self.sudo()
        if not company.ec_sri_cert_data or not company.ec_sri_cert_salt:
            raise UserError(_('No se ha cargado el certificado de firma (.p12) para esta compañía.'))
        try:
            p12=crypto.decrypt(company.ec_sri_cert_data,company.ec_sri_cert_salt)
            password=crypto.decrypt(company.ec_sri_cert_password_enc,company.ec_sri_cert_salt).decode('utf-8')
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        return p12,password

class Partner(models.Model):
    _inherit='res.partner'
    ec_sri_related=fields.Boolean('Parte relacionada')
    ec_sri_rimpe_type=fields.Selection([('popular_business','RIMPE - Negocio Popular'),('entrepreneur','RIMPE - Emprendedor')],
        string='Régimen RIMPE',help='Clasificación RIMPE del contribuyente (Art. 97). Determina el código de retención de renta aplicable.')
    ec_sri_rimpe_start_date=fields.Date('RIMPE desde')
    ec_sri_rimpe_end_date=fields.Date('RIMPE hasta')

    def _ec_sri_identification_code(self):
        """Código SRI (Tabla 6) derivado del tipo de identificación NATIVO de
        Odoo (l10n_latam_identification_type_id), sin duplicar el campo."""
        self.ensure_one()
        partner=self.commercial_partner_id
        if partner.vat and partner.vat=='9'*13:
            return '07'  # consumidor final
        itype=partner._l10n_ec_get_identification_type()
        # l10n_ec traduce el tipo genérico NIF/VAT (el que Odoo pone por defecto) a "foreign".
        # Un número ecuatoriano de 13 o 10 dígitos con ese tipo es RUC o cédula, no del exterior.
        if itype=='foreign' and partner.l10n_latam_identification_type_id==self.env.ref('l10n_latam_base.it_vat',False) \
                and partner.country_id.code in (False,'EC') and re.fullmatch(r'[0-9]{13}|[0-9]{10}',partner.vat or ''):
            return '04' if len(partner.vat)==13 else '05'
        return {'ruc':'04','cedula':'05','passport':'06','ec_passport':'06','foreign':'08'}.get(itype)

    def _ec_sri_identification_warning(self):
        """Aviso (no bloqueo) si el dígito verificador de la cédula o RUC no cuadra:
        el SRI solo emite la advertencia 62 y hay RUC nuevos que no siguen el algoritmo."""
        self.ensure_one()
        partner=self.commercial_partner_id
        if partner._ec_sri_identification_code() in ('04','05') and not validators.ec_identification_ok(partner.vat):
            return _('La identificación %(vat)s de %(name)s no supera el dígito verificador; revísela (advertencia 62 del SRI).',
                vat=partner.vat,name=partner.display_name)
        return False

    def _ec_sri_partner_data(self):
        self.ensure_one()
        partner=self.commercial_partner_id
        code=partner._ec_sri_identification_code()
        if not code:
            raise UserError(_('Configure el tipo de identificación (RUC/Cédula/Pasaporte) del contacto %s.',partner.display_name))
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

    @api.constrains('ec_sri_tax_code','ec_sri_percentage_code','amount','amount_type')
    def _check_ec_sri_iva_code(self):
        for tax in self.filtered(lambda t:t.ec_sri_tax_code=='2' and t.ec_sri_percentage_code):
            code=tax.ec_sri_percentage_code
            if code not in validators.IVA_RATES and code!=validators.IVA_DIFFERENTIATED:
                raise ValidationError(_('El código %s no está en la tabla 17 del SRI (0, 2, 3, 4, 5, 6, 7, 8, 10).',code))
            if tax.amount_type=='percent' and not validators.iva_rate_matches(code,tax.amount):
                raise ValidationError(_('El impuesto %(tax)s es de %(rate)s %% pero el código SRI %(code)s corresponde a %(expected)s %%. '
                    'Tabla 17: 0 → 0 %%, 2 → 12 %%, 3 → 14 %%, 4 → 15 %%, 5 → 5 %%, 6 no objeto, 7 exento, 10 → 13 %%.',
                    tax=tax.display_name,rate=tax.amount,code=code,expected=validators.IVA_RATES[code]))

class Product(models.Model):
    _inherit='product.template'
    ec_sri_auxiliary_code=fields.Char('Código auxiliar SRI',help='Incluya códigos sectoriales cuando correspondan.')

class Establishment(models.Model):
    """Establecimiento del contribuyente (matriz 001, sucursales 002, 003…).
    Tabla propia: NO es una compañía. Un RUC (res.company) tiene varios
    establecimientos; comparten RUC, certificado y ambiente."""
    _name='ec.sri.establishment'
    _description='Establecimiento SRI'
    _check_company_auto=True
    _order='company_id,code'
    name=fields.Char('Nombre',required=True)
    code=fields.Char('Nº de establecimiento',size=3,required=True,help='001 para la matriz, 002, 003… para las sucursales.')
    company_id=fields.Many2one('res.company',required=True,default=lambda s:s.env.company,string='Compañía (RUC)')
    address=fields.Char('Dirección del establecimiento',required=True)
    is_headquarters=fields.Boolean('Es matriz')
    point_ids=fields.One2many('ec.sri.point','establishment_id',string='Puntos de emisión')
    active=fields.Boolean(default=True)
    _code_unique=models.Constraint('UNIQUE(company_id, code)','El código de establecimiento ya existe en esta compañía.')

    @api.depends('code','name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name='%s - %s' % (rec.code or '', rec.name or '')

    @api.constrains('code')
    def _check_code(self):
        for rec in self:
            if not re.fullmatch('[0-9]{3}',rec.code or ''):
                raise ValidationError(_('El Nº de establecimiento debe tener 3 dígitos (ej. 001).'))

class EmissionPoint(models.Model):
    _name='ec.sri.point'
    _description='Punto de emisión SRI'
    _check_company_auto=True
    name=fields.Char('Nombre',required=True)
    establishment_id=fields.Many2one('ec.sri.establishment',required=True,string='Establecimiento',ondelete='restrict')
    company_id=fields.Many2one('res.company',related='establishment_id.company_id',store=True,readonly=True)
    # El código de establecimiento (001/002…) se hereda del establecimiento.
    establishment=fields.Char('Nº establecimiento',size=3,related='establishment_id.code',store=True,readonly=True)
    emission=fields.Char('Punto de emisión',size=3,required=True,default='001')
    address=fields.Char('Dirección establecimiento',related='establishment_id.address',store=True,readonly=True)
    environment=fields.Selection([('1','Pruebas'),('2','Producción')],default='1',required=True,string='Ambiente')
    # Un punto de emisión emite VARIOS tipos de documento, cada uno con su secuencia.
    line_ids=fields.One2many('ec.sri.point.line','point_id',string='Documentos que emite')
    establishment_name=fields.Char('Sucursal',related='establishment_id.name')
    document_summary=fields.Char('Documentos que emite',compute='_compute_document_summary')
    journal_ids=fields.One2many('account.journal','ec_sri_point_id',string='Diarios')
    active=fields.Boolean(default=True)
    _point_unique=models.Constraint('UNIQUE(establishment_id, emission, environment)','Ya existe ese punto de emisión en el establecimiento y ambiente.')

    @api.depends('establishment','emission','name')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name='%s-%s %s' % (rec.establishment or '', rec.emission or '', rec.name or '')

    @api.depends('line_ids.document_type')
    def _compute_document_summary(self):
        names=dict(DOC_TYPES)
        for rec in self:
            rec.document_summary=', '.join(names.get(t,t) for t in rec.line_ids.mapped('document_type'))

    def write(self,vals):
        # El establecimiento y el punto de emisión son INMUTABLES una vez creado.
        if set(vals)&{'establishment_id','emission'}:
            raise UserError(_('No se puede cambiar el establecimiento ni el punto de emisión '
                'de un punto ya creado. Cree un punto nuevo si lo necesita.'))
        if 'environment' in vals and self.env['ec.sri.document'].search_count(
                [('point_id','in',self.ids),('access_key','!=',False)]):
            raise UserError(_('No cambie el ambiente de un punto que ya emitió documentos; cree otro punto.'))
        return super().write(vals)

    @api.constrains('emission')
    def _check_format(self):
        for rec in self:
            if not re.fullmatch('[0-9]{3}',rec.emission or ''):
                raise ValidationError(_('El punto de emisión debe ser numérico de tres dígitos (ej. 001).'))

    def _sequence_for(self,document_type):
        """Secuencia del punto para un tipo de documento (retenciones/guías)."""
        self.ensure_one()
        line=self.line_ids.filtered(lambda l:l.document_type==document_type)[:1]
        if not line:
            raise UserError(_('El punto de emisión "%s" no está habilitado para ese tipo de documento.',self.name or ''))
        return line.sequence_id


class EmissionPointLine(models.Model):
    _name='ec.sri.point.line'
    _description='Tipo de documento del punto de emisión'
    _check_company_auto=True
    point_id=fields.Many2one('ec.sri.point',required=True,ondelete='cascade',index=True)
    company_id=fields.Many2one(related='point_id.company_id',store=True)
    # Tipo de documento NATIVO de Odoo (l10n_latam). document_type guarda su código SRI
    # (01, 03…), que es el que usan el XML, la clave de acceso y las búsquedas.
    l10n_latam_document_type_id=fields.Many2one('l10n_latam.document.type',string='Documento',
        compute='_compute_l10n_latam_document_type_id',store=True,readonly=False,
        domain="[('country_id.code','=','EC'),('code','in',%r)]" % [c for c,_n in DOC_TYPES])
    document_type=fields.Selection(DOC_TYPES,required=True,string='Código SRI')
    sequence_id=fields.Many2one('ir.sequence',required=True,check_company=True,copy=False)
    next_number=fields.Integer('Próximo secuencial',compute='_compute_next',inverse='_inverse_next')
    # La unicidad se valida en _check_unique_document (mensaje con el nombre del documento).

    @api.depends('document_type')
    def _compute_l10n_latam_document_type_id(self):
        for rec in self:
            if rec.document_type and rec.l10n_latam_document_type_id.code!=rec.document_type:
                rec.l10n_latam_document_type_id=self.env['l10n_latam.document.type'].search([
                    ('country_id.code','=','EC'),('code','=',rec.document_type)],limit=1)

    @api.onchange('l10n_latam_document_type_id')
    def _onchange_l10n_latam_document_type_id(self):
        # En el formulario se elige el Documento: el Código SRI (obligatorio, solo
        # lectura) se llena con su código para poder guardar.
        for rec in self:
            code=rec.l10n_latam_document_type_id.code
            rec.document_type=code if code in dict(DOC_TYPES) else False

    def _ec_sri_last_used(self):
        """Mayor secuencial ya usado por facturas y notas con este punto (001-002-…),
        para que la secuencia del punto nunca repita un número ante el SRI."""
        self.ensure_one()
        if self.document_type not in ('01','03','04','05'): return 0
        prefix='%s-%s-' % (self.point_id.establishment,self.point_id.emission)
        moves=self.env['account.move'].sudo().search([('company_id','=',self.company_id.id),
            ('l10n_latam_document_type_id.code','=',self.document_type),('name','like','%'+prefix+'%')])
        pattern=re.compile(r'(?<!\d)%s(\d{9})$' % re.escape(prefix))
        return max([int(m.group(1)) for m in map(pattern.search,moves.mapped('name')) if m] or [0])

    def _ec_sri_skip_used(self):
        for rec in self:
            last=rec._ec_sri_last_used()
            if rec.sequence_id.number_next_actual<=last:
                rec.sequence_id.sudo().number_next_actual=last+1

    @api.depends('sequence_id.number_next_actual')
    def _compute_next(self):
        for rec in self: rec.next_number=rec.sequence_id.number_next_actual if rec.sequence_id else 1

    def _inverse_next(self):
        if not self.env.su and not self.env.user.has_group('l10n_ec_sri_community.group_sri_manager'):
            raise UserError(_('Requiere el permiso Administrador de Ecuador SRI.'))
        for rec in self:
            if not 1<=rec.next_number<=999999999: raise UserError(_('Secuencial fuera de rango.'))
            rec.sequence_id.sudo().number_next_actual=rec.next_number

    def write(self,vals):
        # El tipo de documento y la secuencia son inmutables una vez creada la línea.
        if {'document_type','l10n_latam_document_type_id'}&set(vals):
            raise UserError(_('No se puede cambiar el tipo de documento de una línea ya creada. '
                'Elimínela y agregue otra si es necesario.'))
        if {'sequence_id','next_number'}&set(vals):
            raise UserError(_('La secuencia no se puede cambiar una vez creada la línea.'))
        return super().write(vals)

    @api.model_create_multi
    def create(self,vals_list):
        for vals in vals_list:
            native=self.env['l10n_latam.document.type'].browse(vals.get('l10n_latam_document_type_id'))
            if native and not vals.get('document_type'):
                if native.code not in dict(DOC_TYPES):
                    raise UserError(_('El documento %s no se emite al SRI desde un punto de emisión.',native.display_name))
                vals['document_type']=native.code
            if not vals.get('sequence_id'):
                point=self.env['ec.sri.point'].browse(vals.get('point_id'))
                company_id=point.company_id.id or self.env.company.id
                if company_id not in self.env.companies.ids: raise UserError(_('Empresa no habilitada.'))
                start=int(vals.get('next_number') or 1)
                if not 1<=start<=999999999: raise UserError(_('Secuencial inicial fuera de rango.'))
                seq=self.env['ir.sequence'].sudo().create(dict(
                    name='SRI %s %s' % (point.name or '', dict(DOC_TYPES).get(vals.get('document_type'),'')),
                    company_id=company_id,padding=9,implementation='no_gap',number_next=start,number_increment=1))
                vals['sequence_id']=seq.id
        lines=super().create(vals_list)
        lines._ec_sri_skip_used()
        return lines

    def unlink(self):
        for rec in self:
            if rec.sequence_id and rec.sequence_id.number_next_actual>1:
                raise UserError(_('No se puede eliminar "%s": ya se emitió al menos un secuencial. '
                    'Por trazabilidad, el detalle debe conservarse.') % (dict(DOC_TYPES).get(rec.document_type,rec.document_type or '')))
        return super().unlink()

    @api.constrains('document_type','point_id')
    def _check_unique_document(self):
        for rec in self:
            if self.search_count([('point_id','=',rec.point_id.id),('document_type','=',rec.document_type),('id','!=',rec.id)]):
                raise ValidationError(_('El documento "%s" ya está agregado en este punto de emisión.')
                    % (dict(DOC_TYPES).get(rec.document_type,rec.document_type or '')))

    @api.constrains('sequence_id')
    def _check_sequence(self):
        for rec in self:
            if rec.sequence_id.prefix or rec.sequence_id.suffix or rec.sequence_id.padding!=9 or rec.sequence_id.use_date_range:
                raise ValidationError(_('Use secuencia sin prefijo/sufijo, relleno 9 y sin rangos de fecha.'))
