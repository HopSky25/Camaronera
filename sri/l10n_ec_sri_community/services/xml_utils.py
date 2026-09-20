"""XML and access keys. No Odoo dependency; offline-testable."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
import re
from lxml import etree

DS = 'http://www.w3.org/2000/09/xmldsig#'
SCHEMAS = {
    ('factura', '1.0.0'): 'factura_V1.0.0.xsd',
    ('factura', '1.1.0'): 'factura_V1.1.0.xsd',
    ('factura', '2.0.0'): 'factura_V2.0.0.xsd',
    ('factura', '2.1.0'): 'factura_V2.1.0.xsd',
    ('notaCredito', '1.0.0'): 'NotaCredito_V1.0.0.xsd',
    ('notaCredito', '1.1.0'): 'NotaCredito_V1.1.0.xsd',
    ('notaDebito', '1.0.0'): 'NotaDebito_V1.0.0.xsd',
    ('liquidacionCompra', '1.0.0'): 'LiquidacionCompra_V1.0.0.xsd',
    ('liquidacionCompra', '1.1.0'): 'LiquidacionCompra_V1.1.0.xsd',
    ('guiaRemision', '1.0.0'): 'GuiaRemision_V1.0.0.xsd',
    ('guiaRemision', '1.1.0'): 'GuiaRemision_V1.1.0.xsd',
    ('comprobanteRetencion', '2.0.0'): 'ComprobanteRetencion_V2.0.0.xsd',
}
ROOT_CODES = {'factura': '01', 'liquidacionCompra': '03', 'notaCredito': '04',
              'notaDebito': '05', 'guiaRemision': '06', 'comprobanteRetencion': '07'}

def decimal(value):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Valor numérico no finito.')
    return number

def money(value):
    return format(decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), '.2f')

def number(value, places=6):
    return format(decimal(value).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP), 'f')

def digits(value, length, label):
    value = str(value or '')
    if not re.fullmatch(r'[0-9]{%d}' % length, value):
        raise ValueError('%s debe tener %s dígitos.' % (label, length))
    return value

def check_digit(value):
    if not value or not value.isascii() or not value.isdigit():
        raise ValueError('La clave solo admite dígitos ASCII.')
    result = 11 - sum(int(n) * (2 + i % 6) for i, n in enumerate(reversed(value))) % 11
    return str(0 if result == 11 else 1 if result == 10 else result)

def access_key(date, code, ruc, environment, establishment, point, sequence, numeric):
    if code not in ROOT_CODES.values() or environment not in ('1', '2'):
        raise ValueError('Documento o ambiente no admitido.')
    base = (date.strftime('%d%m%Y') + code + digits(ruc,13,'RUC') + environment
            + digits(establishment,3,'Establecimiento') + digits(point,3,'Punto de emisión')
            + digits(sequence,9,'Secuencial') + digits(numeric,8,'Código numérico') + '1')
    return base + check_digit(base)

def validate_key(key):
    digits(key,49,'Clave de acceso')
    if check_digit(key[:-1]) != key[-1]:
        raise ValueError('Dígito verificador incorrecto.')
    datetime.strptime(key[:8], '%d%m%Y')
    if key[8:10] not in ROOT_CODES.values() or key[23] not in '12' or key[47] != '1':
        raise ValueError('La clave no corresponde al esquema offline admitido.')
    return key

def parse_xml(data):
    if isinstance(data, str):
        data = data.encode('utf-8')
    if not data or len(data) > 5 * 1024 * 1024:
        raise ValueError('XML vacío o mayor a 5 MB.')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             huge_tree=False, remove_blank_text=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype or any(isinstance(n, etree._Entity) for n in root.iter()):
        raise ValueError('No se permiten DTD ni entidades en el XML.')
    return root

def serialize(root):
    return etree.tostring(root, encoding='UTF-8', xml_declaration=True, pretty_print=False)

def put(parent, tag, value=None, **attrs):
    child = etree.SubElement(parent, tag, **attrs)
    if value is not None:
        child.text = str(value)
    return child

@lru_cache(maxsize=20)
def schema(filename):
    path = Path(__file__).resolve().parents[1] / 'schemas' / filename
    return etree.XMLSchema(etree.parse(str(path), etree.XMLParser(no_network=True, resolve_entities=False)))

def validate_document(data, ruc=None, environment=None, code=None):
    root = parse_xml(data)
    key = validate_key(root.findtext('infoTributaria/claveAcceso') or '')
    expected_code = ROOT_CODES.get(root.tag)
    if not expected_code or root.get('id') != 'comprobante':
        raise ValueError('Raíz o id de comprobante no admitido.')
    if (root.tag, root.get('version')) not in SCHEMAS:
        raise ValueError('Versión XML no admitida; retenciones requieren 2.0.0.')
    info = root.find('infoTributaria')
    values = {'ruc': key[10:23], 'ambiente': key[23], 'codDoc': key[8:10],
              'estab': key[24:27], 'ptoEmi': key[27:30], 'secuencial': key[30:39], 'tipoEmision': '1'}
    for tag, value in values.items():
        if info.findtext(tag) != value:
            raise ValueError('La clave no coincide con %s.' % tag)
    if expected_code != key[8:10] or (code and code != expected_code):
        raise ValueError('Tipo de documento inconsistente.')
    if ruc and ruc != key[10:23]:
        raise ValueError('El RUC del XML no pertenece a la empresa.')
    if environment and environment != key[23]:
        raise ValueError('Ambiente XML distinto al configurado.')
    issue_date = root.findtext('./*/fechaEmision') or root.findtext('infoGuiaRemision/fechaIniTransporte')
    if not issue_date or datetime.strptime(issue_date, '%d/%m/%Y').strftime('%d%m%Y') != key[:8]:
        raise ValueError('La fecha del XML no coincide con la clave.')
    if len(root.xpath('//*[@id="comprobante"]')) != 1:
        raise ValueError('ID de comprobante duplicado.')
    # XSD checks the XMLDSig structure; cryptographic verification is separate.
    schema(SCHEMAS[(root.tag, root.get('version'))]).assertValid(root)
    return root

def unsigned_copy(root):
    result = deepcopy(root)
    for element in result.findall('{%s}Signature' % DS):
        result.remove(element)
    return result
