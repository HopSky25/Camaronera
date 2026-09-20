"""Native domestic document builders. Tax values come from Odoo's tax engine."""
from collections import defaultdict
from decimal import Decimal
from lxml import etree
from .xml_utils import put, money, number, decimal, serialize, validate_document

def optional(parent, tag, value):
    if value not in (None,False,''): put(parent,tag,value)

def header(code, company, key):
    root_name={'01':'factura','03':'liquidacionCompra','04':'notaCredito','05':'notaDebito',
               '06':'guiaRemision','07':'comprobanteRetencion'}[code]
    version='2.0.0' if code=='07' else '1.0.0' if code=='05' else '1.1.0'
    root=etree.Element(root_name,id='comprobante',version=version)
    info=put(root,'infoTributaria')
    for tag,value in [('ambiente',key[23]),('tipoEmision','1'),('razonSocial',company['name'])]: put(info,tag,value)
    optional(info,'nombreComercial',company.get('trade_name'))
    for tag,value in [('ruc',company['vat']),('claveAcceso',key),('codDoc',code),('estab',key[24:27]),
                      ('ptoEmi',key[27:30]),('secuencial',key[30:39]),('dirMatriz',company['address'])]: put(info,tag,value)
    optional(info,'agenteRetencion',company.get('agent'))
    optional(info,'contribuyenteRimpe',company.get('rimpe'))
    return root

def fiscal(info,company,guide=False):
    if guide:
        put(info,'obligadoContabilidad',company['accounting'])
        optional(info,'contribuyenteEspecial',company.get('special'))
    else:
        optional(info,'contribuyenteEspecial',company.get('special'))
        put(info,'obligadoContabilidad',company['accounting'])

def additional(root,company,extra):
    data=dict(extra or {})
    if company.get('provider_ruc'): data['RUC Proveedor']=company['provider_ruc']
    if company.get('large_taxpayer'): data['Gran Contribuyente']=company['large_taxpayer']
    if data:
        node=put(root,'infoAdicional')
        for name,value in data.items():
            if value: put(node,'campoAdicional',value,nombre=name)

def aggregate_taxes(lines):
    result={}
    for line in lines:
        for tax in line['taxes']:
            key=(tax['code'],tax['percentage'],str(tax['rate']))
            row=result.setdefault(key,dict(code=key[0],percentage=key[1],rate=key[2],base=Decimal(0),amount=Decimal(0)))
            row['base']+=decimal(tax['base']); row['amount']+=decimal(tax['amount'])
    return list(result.values())

def tax_elements(parent,taxes,total=False):
    container=put(parent,'totalConImpuestos' if total else 'impuestos')
    for tax in taxes:
        node=put(container,'totalImpuesto' if total else 'impuesto')
        put(node,'codigo',tax['code']);put(node,'codigoPorcentaje',tax['percentage'])
        if not total: put(node,'tarifa',money(tax['rate']))
        put(node,'baseImponible',money(tax['base']));put(node,'valor',money(tax['amount']))

def payments(info,payments,total):
    if not payments or abs(sum(decimal(p['amount']) for p in payments)-decimal(total))>Decimal('.01'):
        raise ValueError('Las formas de pago deben sumar el total del comprobante.')
    parent=put(info,'pagos')
    for p in payments:
        node=put(parent,'pago');put(node,'formaPago',p['code']);put(node,'total',money(p['amount']))
        if p.get('days'):
            put(node,'plazo',p['days']);put(node,'unidadTiempo','dias')

def invoice(code,company,key,data):
    if code not in ('01','03','04','05'): raise ValueError('Tipo no admitido por el generador contable.')
    root=header(code,company,key)
    info=put(root,{'01':'infoFactura','03':'infoLiquidacionCompra','04':'infoNotaCredito','05':'infoNotaDebito'}[code])
    put(info,'fechaEmision',data['date']);put(info,'dirEstablecimiento',data['address'])
    if code in ('01','03'): fiscal(info,company)
    suffix='Proveedor' if code=='03' else 'Comprador'
    p=data['partner']
    if p['type']=='07' and code!='01': raise ValueError('Este comprobante requiere un receptor identificado.')
    if p['type']=='07' and decimal(data['total'])>50: raise ValueError('Identifique al comprador: consumidor final supera USD 50.')
    for tag,value in [('tipoIdentificacion'+suffix,p['type']),('razonSocial'+suffix,p['name']),('identificacion'+suffix,p['vat'])]: put(info,tag,value)
    if code in ('01','03'): optional(info,'direccion'+suffix,p.get('address'))
    if code in ('04','05'):
        fiscal(info,company)
        origin=data['origin']
        put(info,'codDocModificado',origin['code']);put(info,'numDocModificado',origin['number'])
        put(info,'fechaEmisionDocSustento',origin['date'])
    lines=data['lines']
    if not lines: raise ValueError('El comprobante no tiene líneas.')
    subtotal=sum(decimal(l['subtotal']) for l in lines)
    taxes=aggregate_taxes(lines)
    total=subtotal+sum(decimal(t['amount']) for t in taxes)
    if abs(total-decimal(data['total']))>Decimal('.01'):
        raise ValueError('El total XML no coincide con el total contable; revise redondeo e impuestos.')
    put(info,'totalSinImpuestos',money(subtotal))
    if code in ('01','03'): put(info,'totalDescuento',money(sum(decimal(l['discount']) for l in lines)))
    if code=='04':
        put(info,'valorModificacion',money(total));put(info,'moneda','DOLAR')
    tax_elements(info,taxes,total=code!='05')
    if code=='04': put(info,'motivo',data['reason'])
    else:
        if code=='01': put(info,'propina','0.00')
        put(info,'valorTotal' if code=='05' else 'importeTotal',money(total))
        if code!='05': put(info,'moneda','DOLAR')
        if code=='01': optional(info,'placa',data.get('plate'))
        payments(info,data['payments'],total)
    if code=='05':
        reasons=put(root,'motivos')
        for line in lines:
            item=put(reasons,'motivo');put(item,'razon',line['description']);put(item,'valor',money(line['subtotal']))
    else:
        details=put(root,'detalles')
        for line in lines:
            if decimal(line['quantity'])<=0 or decimal(line['unit_price'])<0 or decimal(line['subtotal'])<0:
                raise ValueError('Use líneas positivas; los descuentos se expresan en el campo descuento.')
            node=put(details,'detalle')
            put(node,'codigoInterno' if code=='04' else 'codigoPrincipal',line['code'])
            optional(node,'codigoAdicional' if code=='04' else 'codigoAuxiliar',line.get('auxiliary'))
            put(node,'descripcion',line['description'])
            put(node,'cantidad',number(line['quantity']));put(node,'precioUnitario',number(line['unit_price']))
            put(node,'descuento',money(line['discount']));put(node,'precioTotalSinImpuesto',money(line['subtotal']))
            tax_elements(node,line['taxes'])
    additional(root,company,data.get('additional'))
    result=serialize(root);validate_document(result);return result

def withholding(company,key,data):
    root=header('07',company,key);info=put(root,'infoCompRetencion')
    put(info,'fechaEmision',data['date']);put(info,'dirEstablecimiento',data['address']);fiscal(info,company)
    p=data['partner']
    if p['type']=='07': raise ValueError('Identifique al sujeto retenido.')
    put(info,'tipoIdentificacionSujetoRetenido',p['type'])
    optional(info,'tipoSujetoRetenido',p.get('subject_type'))
    put(info,'parteRel','SI' if p.get('related') else 'NO')
    put(info,'razonSocialSujetoRetenido',p['name']);put(info,'identificacionSujetoRetenido',p['vat'])
    put(info,'periodoFiscal',data['period'])
    docs=put(root,'docsSustento')
    for support in data['supports']:
        node=put(docs,'docSustento')
        for tag,field in [('codSustento','support_code'),('codDocSustento','code'),('numDocSustento','number'),
                          ('fechaEmisionDocSustento','date'),('fechaRegistroContable','accounting_date'),
                          ('numAutDocSustento','authorization')]: put(node,tag,support[field])
        put(node,'pagoLocExt','01')
        put(node,'totalSinImpuestos',money(support['subtotal']));put(node,'importeTotal',money(support['total']))
        taxes=put(node,'impuestosDocSustento')
        for tax in support['taxes']:
            item=put(taxes,'impuestoDocSustento')
            for tag,value in [('codImpuestoDocSustento',tax['code']),('codigoPorcentaje',tax['percentage']),
                              ('baseImponible',money(tax['base'])),('tarifa',money(tax['rate'])),('valorImpuesto',money(tax['amount']))]: put(item,tag,value)
        retained=put(node,'retenciones')
        for tax in support['withholdings']:
            item=put(retained,'retencion')
            for tag,value in [('codigo',tax['code']),('codigoRetencion',tax['retention_code']),('baseImponible',money(tax['base'])),
                              ('porcentajeRetener',money(tax['rate'])),('valorRetenido',money(decimal(tax['base'])*decimal(tax['rate'])/100))]: put(item,tag,value)
        payments(node,support['payments'],support['total'])
    additional(root,company,data.get('additional'))
    result=serialize(root);validate_document(result);return result

def delivery(company,key,data):
    root=header('06',company,key);info=put(root,'infoGuiaRemision')
    carrier=data['carrier']
    for tag,value in [('dirEstablecimiento',data['address']),('dirPartida',data['departure']),
                      ('razonSocialTransportista',carrier['name']),('tipoIdentificacionTransportista',carrier['type']),
                      ('rucTransportista',carrier['vat'])]: put(info,tag,value)
    fiscal(info,company,guide=True)
    put(info,'fechaIniTransporte',data['start']);put(info,'fechaFinTransporte',data['end']);put(info,'placa',data['plate'])
    recipients=put(root,'destinatarios')
    for dest in data['recipients']:
        node=put(recipients,'destinatario')
        for tag,field in [('identificacionDestinatario','vat'),('razonSocialDestinatario','name'),('dirDestinatario','address'),('motivoTraslado','reason')]: put(node,tag,dest[field])
        optional(node,'ruta',dest.get('route'))
        if dest.get('origin'):
            for tag,field in [('codDocSustento','code'),('numDocSustento','number'),('numAutDocSustento','authorization'),('fechaEmisionDocSustento','date')]: put(node,tag,dest['origin'][field])
        details=put(node,'detalles')
        for line in dest['lines']:
            if decimal(line['quantity'])<=0: raise ValueError('La cantidad transportada debe ser positiva.')
            item=put(details,'detalle');put(item,'codigoInterno',line['code'])
            put(item,'descripcion',line['description']);put(item,'cantidad',number(line['quantity']))
    additional(root,company,data.get('additional'))
    result=serialize(root);validate_document(result);return result
