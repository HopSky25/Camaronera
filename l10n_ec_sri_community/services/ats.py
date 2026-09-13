"""ATS XML rendering against the official at.xsd. Monthly domestic operations."""
from collections import defaultdict
from decimal import Decimal
from lxml import etree
from .xml_utils import put, money, decimal, schema, serialize

BUCKETS={'non_taxable':'baseNoGraIva','zero':'baseImponible','taxable':'baseImpGrav','exempt':'baseImpExe','ice':'montoIce'}
VAT_RET={10:'valRetBien10',20:'valRetServ20',30:'valorRetBienes',50:'valRetServ50',70:'valorRetServicios',100:'valRetServ100'}

def bases(lines):
    amounts={name:Decimal(0) for name in list(BUCKETS.values())+['montoIva']}
    for line in lines:
        for tax in line['taxes']:
            bucket=tax.get('ats_bucket')
            if tax['code']=='2':
                if bucket not in ('zero','taxable','non_taxable','exempt'):
                    raise ValueError('Falta clasificación de IVA para ATS.')
                amounts[BUCKETS[bucket]]+=decimal(tax['base'])
                amounts['montoIva']+=decimal(tax['amount'])
            elif tax['code']=='3': amounts['montoIce']+=decimal(tax['amount'])
            else: raise ValueError('El ATS nativo no cubre este impuesto; complete el ATS externo.')
    return amounts

def build(company,year,month,purchases,sales,cancelled=()):
    root=etree.Element('iva')
    totals=sum((decimal(s['subtotal'])*(-1 if s['code']=='04' else 1) for s in sales),Decimal(0))
    for tag,value in [('TipoIDInformante','R'),('IdInformante',company['vat']),('razonSocial',company['name']),
                      ('Anio',str(year)),('Mes','%02d' % month),('numEstabRuc','%03d' % company['establishments']),
                      ('totalVentas',money(totals)),('codigoOperativo','IVA')]: put(root,tag,value)
    if purchases:
        container=put(root,'compras')
        for p in purchases:
            node=put(container,'detalleCompras')
            for tag,value in [('codSustento',p['support_code']),('tpIdProv',p['id_type']),('idProv',p['vat']),('tipoComprobante',p['code']),
                              ('parteRel','SI' if p['related'] else 'NO'),('fechaRegistro',p['accounting_date']),('establecimiento',p['number'][0]),
                              ('puntoEmision',p['number'][1]),('secuencial',str(int(p['number'][2]))),('fechaEmision',p['date']),('autorizacion',p['authorization'])]: put(node,tag,value)
            for tag in ('baseNoGraIva','baseImponible','baseImpGrav','baseImpExe','montoIce','montoIva'): put(node,tag,money(p['bases'][tag]))
            vat=defaultdict(Decimal)
            for t in p.get('retentions',[]):
                if t['code']=='2':
                    rate=decimal(t['rate'])
                    if rate not in VAT_RET: raise ValueError('Porcentaje de retención IVA no cubierto por el ATS nativo.')
                    vat[VAT_RET[rate]]+=decimal(t['amount'])
            for tag in VAT_RET.values(): put(node,tag,money(vat[tag]))
            put(node,'totbasesImpReemb','0.00')
            exterior=put(node,'pagoExterior');put(exterior,'pagoLocExt','01')
            put(exterior,'paisEfecPago','NA');put(exterior,'aplicConvDobTrib','NA');put(exterior,'pagExtSujRetNorLeg','NA')
            if p['code']!='04':
                payment=put(node,'formasDePago')
                for code in sorted(set(p['payments'])): put(payment,'formaPago',code)
            income=[t for t in p.get('retentions',[]) if t['code']=='1']
            if income:
                air=put(node,'air')
                for t in income:
                    item=put(air,'detalleAir')
                    for tag,value in [('codRetAir',t['retention_code']),('baseImpAir',money(t['base'])),('porcentajeAir',money(t['rate'])),('valRetAir',money(t['amount']))]: put(item,tag,value)
            if p.get('retention'):
                r=p['retention'];key=r['key']
                for tag,value in [('estabRetencion1',key[24:27]),('ptoEmiRetencion1',key[27:30]),('secRetencion1',str(int(key[30:39]))),
                                  ('autRetencion1',key),('fechaEmiRet1',r['date'])]: put(node,tag,value)
            if p.get('origin'):
                o=p['origin']
                for tag,value in [('docModificado',o['code']),('estabModificado',o['number'][0]),('ptoEmiModificado',o['number'][1]),
                                  ('secModificado',str(int(o['number'][2]))),('autModificado',o['authorization'])]: put(node,tag,value)
    establishments=defaultdict(Decimal)
    if sales:
        container=put(root,'ventas');groups={}
        for sale in sales:
            k=(sale['id_type'],sale['vat'],sale['code'],sale['related'])
            group=groups.setdefault(k,dict(count=0,bases=defaultdict(Decimal),vat_ret=Decimal(0),income_ret=Decimal(0),payments=set()))
            group['count']+=1
            for name,value in sale['bases'].items(): group['bases'][name]+=decimal(value)
            group['vat_ret']+=decimal(sale['vat_ret']);group['income_ret']+=decimal(sale['income_ret'])
            group['payments'].update(sale['payments'])
            establishments[sale['number'][0]]+=decimal(sale['subtotal'])*(-1 if sale['code']=='04' else 1)
        for (id_type,vat,code,related),g in groups.items():
            node=put(container,'detalleVentas')
            for tag,value in [('tpIdCliente',id_type),('idCliente',vat),('parteRelVtas','SI' if related else 'NO'),
                              ('tipoComprobante','18' if code=='01' else code),('tipoEmision','E'),('numeroComprobantes',str(g['count']))]: put(node,tag,value)
            # ATS sales has no baseImpExe; include exempt bases in non-taxed sales.
            g['bases']['baseNoGraIva']+=g['bases']['baseImpExe']
            for tag in ('baseNoGraIva','baseImponible','baseImpGrav','montoIva','montoIce'): put(node,tag,money(g['bases'][tag]))
            put(node,'valorRetIva',money(g['vat_ret']));put(node,'valorRetRenta',money(g['income_ret']))
            if code!='04':
                payment=put(node,'formasDePago')
                for code in sorted(g['payments']): put(payment,'formaPago',code)
        parent=put(root,'ventasEstablecimiento')
        for establishment,total in sorted(establishments.items()):
            node=put(parent,'ventaEst');put(node,'codEstab',establishment);put(node,'ventasEstab',money(total));put(node,'ivaComp','0.00')
    if cancelled:
        parent=put(root,'anulados')
        for entry in cancelled:
            key=entry['key'];node=put(parent,'detalleAnulados')
            for tag,value in [('tipoComprobante',key[8:10]),('establecimiento',key[24:27]),('puntoEmision',key[27:30]),
                              ('secuencialInicio',str(int(key[30:39]))),('secuencialFin',str(int(key[30:39]))),('autorizacion',key)]: put(node,tag,value)
    schema('at.xsd').assertValid(root)
    return serialize(root)
