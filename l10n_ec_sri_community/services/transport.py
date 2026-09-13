"""Bounded SOAP 1.1 client. No credentials or payload in log messages."""
import base64
import requests
from lxml import etree
from .xml_utils import parse_xml, serialize, put

SOAP='http://schemas.xmlsoap.org/soap/envelope/'
HOSTS={'1':'celcer.sri.gob.ec','2':'cel.sri.gob.ec'}

class SriTransportError(Exception):
    pass

def messages(root):
    return [' | '.join(filter(None, (n.findtext('identificador'), n.findtext('mensaje'),
                                    n.findtext('informacionAdicional'))))
            for n in root.findall('.//mensajes/mensaje')]

class SriClient:
    def __init__(self, environment, session=None):
        if environment not in HOSTS: raise ValueError('Ambiente inválido.')
        self.host=HOSTS[environment]
        self.session=session or requests.Session()

    def _request(self, service, namespace, operation, tag, value):
        envelope=etree.Element('{%s}Envelope' % SOAP,nsmap={'soap':SOAP})
        body=put(envelope,'{%s}Body' % SOAP)
        op=put(body,'{%s}%s' % (namespace,operation))
        put(op,tag,value)
        url='https://%s/comprobantes-electronicos-ws/%s' % (self.host,service)
        try:
            response=self.session.post(url,data=serialize(envelope),
                headers={'Content-Type':'text/xml; charset=utf-8','SOAPAction':'""'},
                timeout=(10,40),verify=True,allow_redirects=False,stream=True)
            with response:
                if response.status_code != 200:
                    raise SriTransportError('Respuesta HTTP %s del SRI.' % response.status_code)
                chunks=[]; size=0
                for chunk in response.iter_content(65536):
                    size+=len(chunk)
                    if size>5*1024*1024: raise SriTransportError('Respuesta del SRI demasiado grande.')
                    chunks.append(chunk)
                raw=b''.join(chunks)
            root=parse_xml(raw)
            if root.find('.//{%s}Fault' % SOAP) is not None:
                raise SriTransportError('El SRI devolvió una falla SOAP.')
            return root,raw
        except (requests.RequestException,etree.XMLSyntaxError,ValueError) as exc:
            raise SriTransportError('No se pudo confirmar la respuesta del SRI (%s).' % type(exc).__name__) from exc

    def receive(self, signed_xml):
        root,raw=self._request('RecepcionComprobantesOffline','http://ec.gob.sri.ws.recepcion',
                              'validarComprobante','xml',base64.b64encode(signed_xml).decode('ascii'))
        result=root.find('.//RespuestaRecepcionComprobante')
        if result is None: raise SriTransportError('Respuesta de recepción inesperada.')
        return {'state':result.findtext('estado'),'messages':messages(result),
                'codes':[n.text for n in result.findall('.//identificador')], 'raw':raw}

    def authorize(self, key):
        root,raw=self._request('AutorizacionComprobantesOffline','http://ec.gob.sri.ws.autorizacion',
                              'autorizacionComprobante','claveAccesoComprobante',key)
        result=root.find('.//RespuestaAutorizacionComprobante')
        if result is None: raise SriTransportError('Respuesta de autorización inesperada.')
        if result.findtext('claveAccesoConsultada') not in (None,key):
            raise SriTransportError('El SRI respondió para otra clave.')
        auths=result.findall('autorizaciones/autorizacion')
        auth=next((n for n in auths if n.findtext('estado')=='AUTORIZADO'),auths[-1] if auths else None)
        if auth is None: return {'state':'PENDING','raw':raw,'messages':messages(result)}
        return {'state':auth.findtext('estado'),'number':auth.findtext('numeroAutorizacion'),
                'date':auth.findtext('fechaAutorizacion'),'document':auth.findtext('comprobante'),
                'authorization':serialize(auth),'messages':messages(auth),'raw':raw}
