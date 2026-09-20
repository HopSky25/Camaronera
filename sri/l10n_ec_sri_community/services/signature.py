"""SRI XAdES-BES 1.3.2, three signed references, inclusive C14N.

RSA-SHA1 is the profile specified by SRI technical sheet 2.34, section 6.8.
Never use SHA1 for other application cryptography.
"""
import base64
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from lxml import etree
from .xml_utils import DS, parse_xml, serialize, unsigned_copy, put

XA = 'http://uri.etsi.org/01903/v1.3.2#'
C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'

def canonical(node):
    return etree.tostring(node, method='c14n', exclusive=False, with_comments=False)

def b64(data):
    return base64.b64encode(data).decode('ascii')

def digest(node):
    return b64(hashlib.sha1(canonical(node)).digest())

def sign_xml(data, p12, password):
    root = parse_xml(data)
    if root.get('id') != 'comprobante' or root.findall('{%s}Signature' % DS):
        raise ValueError('Se requiere XML sin firma y con id comprobante.')
    private, cert, chain = load_key_and_certificates(p12, password.encode() if password else None)
    if not isinstance(private, rsa.RSAPrivateKey) or not cert or private.key_size < 2048:
        raise ValueError('Se requiere certificado RSA con clave privada de al menos 2048 bits.')
    now = datetime.now(timezone.utc)
    if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
        raise ValueError('El certificado no está vigente.')
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        if not (usage.digital_signature or usage.content_commitment):
            raise ValueError('El certificado no permite firma digital.')
    except x509.ExtensionNotFound:
        pass
    serial = uuid4().hex
    sid, kid, pid, rid = ['%s-%s' % (prefix, serial) for prefix in ('Signature','KeyInfo','SignedProperties','Document')]
    sig = etree.SubElement(root, '{%s}Signature' % DS, nsmap={'ds':DS,'etsi':XA}, Id=sid)
    info = put(sig, '{%s}SignedInfo' % DS)
    put(info, '{%s}CanonicalizationMethod' % DS, Algorithm=C14N)
    put(info, '{%s}SignatureMethod' % DS, Algorithm=DS+'rsa-sha1')
    signature_value = put(sig, '{%s}SignatureValue' % DS)
    ki = put(sig, '{%s}KeyInfo' % DS, Id=kid)
    xd = put(ki, '{%s}X509Data' % DS)
    put(xd, '{%s}X509Certificate' % DS, b64(cert.public_bytes(serialization.Encoding.DER)))
    kv = put(ki,'{%s}KeyValue' % DS)
    rv = put(kv,'{%s}RSAKeyValue' % DS)
    numbers=private.public_key().public_numbers()
    for tag,val in [('Modulus',numbers.n),('Exponent',numbers.e)]:
        put(rv,'{%s}%s' % (DS,tag),b64(val.to_bytes((val.bit_length()+7)//8,'big')))
    obj = put(sig, '{%s}Object' % DS)
    qual = put(obj, '{%s}QualifyingProperties' % XA, Target='#'+sid)
    props = put(qual, '{%s}SignedProperties' % XA, Id=pid)
    signed = put(props, '{%s}SignedSignatureProperties' % XA)
    put(signed,'{%s}SigningTime' % XA, now.isoformat(timespec='seconds'))
    sc = put(signed,'{%s}SigningCertificate' % XA)
    ce = put(sc,'{%s}Cert' % XA)
    cd = put(ce,'{%s}CertDigest' % XA)
    put(cd,'{%s}DigestMethod' % DS, Algorithm=DS+'sha1')
    put(cd,'{%s}DigestValue' % DS,b64(cert.fingerprint(hashes.SHA1())))
    issuer = put(ce,'{%s}IssuerSerial' % XA)
    put(issuer,'{%s}X509IssuerName' % DS, cert.issuer.rfc4514_string())
    put(issuer,'{%s}X509SerialNumber' % DS,str(cert.serial_number))
    dop = put(props,'{%s}SignedDataObjectProperties' % XA)
    fmt = put(dop,'{%s}DataObjectFormat' % XA,ObjectReference='#'+rid)
    put(fmt,'{%s}Description' % XA,'Comprobante electrónico')
    put(fmt,'{%s}MimeType' % XA,'text/xml')
    for uri, node, attrs in [('#'+pid,props,{'Type':'http://uri.etsi.org/01903#SignedProperties'}),
                             ('#'+kid,ki,{}),('#comprobante',unsigned_copy(root),{'Id':rid})]:
        ref = put(info,'{%s}Reference' % DS,URI=uri,**attrs)
        if uri == '#comprobante':
            trans=put(ref,'{%s}Transforms' % DS)
            put(trans,'{%s}Transform' % DS,Algorithm=DS+'enveloped-signature')
        put(ref,'{%s}DigestMethod' % DS,Algorithm=DS+'sha1')
        put(ref,'{%s}DigestValue' % DS,digest(node))
    signature_value.text=b64(private.sign(canonical(info),padding.PKCS1v15(),hashes.SHA1()))
    result=serialize(root)
    verify_xml(result)
    return result

def verify_xml(data):
    """Cryptographic integrity check, NOT certificate trust/revocation validation."""
    root=parse_xml(data)
    ns={'ds':DS,'etsi':XA}
    signatures=root.findall('{%s}Signature' % DS)
    if len(signatures)!=1:
        raise ValueError('Debe existir exactamente una firma.')
    sig=signatures[0]
    ids={}
    for node in root.iter():
        for attr in ('Id','id'):
            if node.get(attr):
                if node.get(attr) in ids: raise ValueError('ID XML duplicado.')
                ids[node.get(attr)]=node
    info=sig.find('ds:SignedInfo',ns)
    if info is None or info.find('ds:SignatureMethod',ns).get('Algorithm') != DS+'rsa-sha1':
        raise ValueError('Perfil de firma no admitido.')
    if info.find('ds:CanonicalizationMethod',ns).get('Algorithm') != C14N:
        raise ValueError('Canonicalización no admitida.')
    refs=info.findall('ds:Reference',ns)
    ki=sig.find('ds:KeyInfo',ns)
    props=sig.find('.//etsi:SignedProperties',ns)
    if ki is None or props is None or len(refs)!=3:
        raise ValueError('Se requieren las tres referencias XAdES-BES.')
    if {r.get('URI') for r in refs}!={'#comprobante','#'+ki.get('Id',''),'#'+props.get('Id','')}:
        raise ValueError('Referencias de firma inesperadas.')
    for ref in refs:
        uri=ref.get('URI','')
        target=ids.get(uri[1:]) if uri.startswith('#') else None
        if target is None or ref.find('ds:DigestMethod',ns).get('Algorithm')!=DS+'sha1':
            raise ValueError('Referencia de firma inválida.')
        transforms=[n.get('Algorithm') for n in ref.findall('ds:Transforms/ds:Transform',ns)]
        if transforms != ([DS+'enveloped-signature'] if uri=='#comprobante' else []):
            raise ValueError('Transformaciones no admitidas.')
        if uri=='#comprobante': target=unsigned_copy(target)
        if digest(target)!=ref.findtext('ds:DigestValue',namespaces=ns):
            raise ValueError('El XML fue modificado después de firmarlo.')
    cert=x509.load_der_x509_certificate(base64.b64decode(ki.findtext('ds:X509Data/ds:X509Certificate',namespaces=ns)))
    cd=props.findtext('.//etsi:CertDigest/ds:DigestValue',namespaces=ns)
    if cd != b64(cert.fingerprint(hashes.SHA1())):
        raise ValueError('Certificado de firma inconsistente.')
    cert.public_key().verify(base64.b64decode(sig.findtext('ds:SignatureValue',namespaces=ns)),
                             canonical(info),padding.PKCS1v15(),hashes.SHA1())
    return cert
