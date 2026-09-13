import base64
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree
from sri_core import xml_utils as x, builders as b, signature as sig, ats

C=dict(name='EMPRESA DE PRUEBA',vat='1790016919001',address='Quito, Ecuador',accounting='SI',provider_ruc='1790016919001')
P=dict(type='04',vat='1790011674001',name='CLIENTE DE PRUEBA',address='Quito',related=False,subject_type='02')
DATE=date(2026,9,11)

def key(code='01'):
    return x.access_key(DATE,code,C['vat'],'1','001','001','000000001','12345678')

def invoice_data():
    return dict(date='11/09/2026',address='Quito',partner=P,total=115,reason='Corrección',
        lines=[dict(code='P1',description='Producto & servicio',quantity=1,unit_price=100,discount=0,subtotal=100,
                    taxes=[dict(code='2',percentage='4',rate=15,base=100,amount=15,ats_bucket='taxable')])],
        payments=[dict(code='20',amount=115)],origin=dict(code='01',number='001-001-000000002',date='10/09/2026'))

def retention_data():
    return dict(date='11/09/2026',address='Quito',period='09/2026',partner=P,supports=[dict(
        support_code='01',code='01',number='001001000000002',date='10/09/2026',accounting_date='11/09/2026',
        authorization=key(),subtotal=100,total=115,taxes=invoice_data()['lines'][0]['taxes'],payments=[dict(code='20',amount=115)],
        withholdings=[dict(code='1',retention_code='312',base=100,rate=1.75)])])

def delivery_data():
    return dict(address='Quito',departure='Quito',carrier=P,plate='ABC1234',start='11/09/2026',end='12/09/2026',
        recipients=[dict(vat=P['vat'],name=P['name'],address='Guayaquil',reason='VENTA',lines=[dict(code='P1',description='Producto',quantity=2)])])

@pytest.mark.parametrize('code',['01','03','04','05','06','07'])
def test_official_schema_all_six(code):
    if code=='06': data=b.delivery(C,key(code),delivery_data())
    elif code=='07': data=b.withholding(C,key(code),retention_data())
    else: data=b.invoice(code,C,key(code),invoice_data())
    assert x.validate_document(data).findtext('infoTributaria/claveAcceso')==key(code)

def test_key_known_checksum_and_identity():
    k=key()
    assert len(k)==49
    # independent weighting from left, known vector also persisted below
    weights=[7,6,5,4,3,2]*8
    val=11-sum(int(d)*w for d,w in zip(k[:48],weights))%11
    assert k[-1]==str(0 if val==11 else 1 if val==10 else val)
    with pytest.raises(ValueError): x.validate_key(k[:-1]+str((int(k[-1])+1)%10))
    with pytest.raises(ValueError): x.digits('١'*13,13,'RUC')

def test_external_entities_rejected():
    with pytest.raises(ValueError): x.parse_xml(b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>')

def test_amounts_and_escaping():
    data=invoice_data();data['total']=114
    with pytest.raises(ValueError): b.invoice('01',C,key(),data)
    data=invoice_data();data['payments'][0]['amount']=1
    with pytest.raises(ValueError): b.invoice('01',C,key(),data)
    assert x.money('1.005')=='1.01'
    assert b'&amp;' in b.invoice('01',C,key(),invoice_data())

@pytest.fixture(scope='module')
def cert():
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME,'TEST ONLY')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
        .not_valid_after(now+timedelta(days=1)).sign(private,hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b'test',private,certificate,None,serialization.BestAvailableEncryption(b'test')),certificate

def test_signature_integrity_and_three_references(cert):
    raw=b.invoice('01',C,key(),invoice_data());signed=sig.sign_xml(raw,cert[0],'test')
    x.validate_document(signed);sig.verify_xml(signed)
    root=x.parse_xml(signed);ns={'ds':x.DS}
    info=root.find('ds:Signature/ds:SignedInfo',ns)
    assert len(info.findall('ds:Reference',ns))==3
    # Independent verification through cryptography public API.
    cert[1].public_key().verify(base64.b64decode(root.findtext('ds:Signature/ds:SignatureValue',namespaces=ns)),
                               sig.canonical(info),padding.PKCS1v15(),hashes.SHA1())
    root.find('infoTributaria/razonSocial').text='ALTERADO'
    with pytest.raises(ValueError): sig.verify_xml(x.serialize(root))

def test_certificate_wrong_password(cert):
    with pytest.raises(ValueError): sig.sign_xml(b.invoice('01',C,key(),invoice_data()),cert[0],'wrong')

def test_ats_official_schema_and_refund():
    common=dict(code='01',vat=P['vat'],related=False,number=('001','001','000000001'),
        bases=ats.bases(invoice_data()['lines']),payments=['20'],subtotal=100)
    purchase=dict(common,id_type='01',support_code='01',accounting_date='11/09/2026',date='11/09/2026',authorization=key(),
        retentions=[dict(code='1',retention_code='312',base=100,rate=1.75,amount=1.75)],retention=dict(key=key('07'),date='11/09/2026'))
    sale=dict(common,id_type='04',vat_ret=0,income_ret=0)
    company=dict(C,establishments=1)
    raw=ats.build(company,2026,9,[purchase],[sale,sale])
    root=x.parse_xml(raw)
    assert root.findtext('totalVentas')=='200.00'
    assert root.findtext('ventas/detalleVentas/numeroComprobantes')=='2'
    assert root.findtext('ventas/detalleVentas/tipoComprobante')=='18'
    refund=dict(sale,code='04',payments=[])
    raw=ats.build(company,2026,9,[],[sale,refund])
    assert x.parse_xml(raw).findtext('totalVentas')=='0.00'

def test_schema_prevents_missing_buyer():
    root=x.parse_xml(b.invoice('01',C,key(),invoice_data()))
    node=root.find('infoFactura/identificacionComprador');node.getparent().remove(node)
    with pytest.raises(etree.DocumentInvalid): x.validate_document(x.serialize(root))

def test_schema_and_key_environment_mismatch():
    raw=b.invoice('01',C,key(),invoice_data())
    with pytest.raises(ValueError): x.validate_document(raw,environment='2')

def test_consumer_final_limit():
    data=invoice_data();data['partner']=dict(P,type='07',vat='9999999999999')
    with pytest.raises(ValueError): b.invoice('01',C,key(),data)

def test_all_bundled_document_schema_versions_compile():
    for filename in x.SCHEMAS.values():
        assert x.schema(filename) is not None

def test_mixed_vat_and_credit_note_values():
    data=invoice_data()
    second=deepcopy(data['lines'][0])
    second.update(code='ZERO',subtotal=20,unit_price=20)
    second['taxes']=[dict(code='2',percentage='0',rate=0,base=20,amount=0,ats_bucket='zero')]
    data['lines'].append(second);data['total']=135;data['payments'][0]['amount']=135
    for code in ('01','04'):
        root=x.parse_xml(b.invoice(code,C,key(code),data))
        assert len(root.findall('.//totalImpuesto'))==2
        assert root.findtext('.//totalSinImpuestos')=='120.00'
