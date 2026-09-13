import base64
import pytest
import requests
from sri_core.transport import SriClient, SriTransportError
from sri_core.xml_utils import parse_xml

class Response:
    status_code=200
    def __init__(self,data,status=200): self.data=data;self.status_code=status
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def iter_content(self,size): yield self.data

class Session:
    def __init__(self,response): self.response=response;self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs))
        if isinstance(self.response,Exception): raise self.response
        return self.response

def envelope(body):
    return ('<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'+body+'</soap:Body></soap:Envelope>').encode()

def test_reception_soap_binary_and_tls():
    session=Session(Response(envelope('<RespuestaRecepcionComprobante><estado>RECIBIDA</estado></RespuestaRecepcionComprobante>')))
    result=SriClient('1',session).receive(b'<factura/>')
    assert result['state']=='RECIBIDA'
    url,params=session.calls[0]
    assert 'celcer.sri.gob.ec' in url and params['verify'] is True and params['allow_redirects'] is False
    request=parse_xml(params['data'])
    assert base64.b64decode(request.findtext('.//xml'))==b'<factura/>'
    assert params['timeout']==(10,40)

def test_rejected_reception_preserves_codes():
    response=envelope('<RespuestaRecepcionComprobante><estado>DEVUELTA</estado><comprobantes><comprobante><mensajes><mensaje><identificador>43</identificador><mensaje>CLAVE REGISTRADA</mensaje></mensaje></mensajes></comprobante></comprobantes></RespuestaRecepcionComprobante>')
    result=SriClient('2',Session(Response(response))).receive(b'xml')
    assert result['codes']==['43'] and 'CLAVE REGISTRADA' in result['messages'][0]

def test_authorization_pending_is_not_rejection():
    response=envelope('<RespuestaAutorizacionComprobante><claveAccesoConsultada>123</claveAccesoConsultada><autorizaciones/></RespuestaAutorizacionComprobante>')
    assert SriClient('1',Session(Response(response))).authorize('123')['state']=='PENDING'

def test_authorization_keeps_cdata_and_timestamp():
    response=envelope('<RespuestaAutorizacionComprobante><claveAccesoConsultada>123</claveAccesoConsultada><autorizaciones><autorizacion><estado>AUTORIZADO</estado><numeroAutorizacion>123</numeroAutorizacion><fechaAutorizacion>2026-09-11T10:00:00-05:00</fechaAutorizacion><comprobante><![CDATA[<factura/>]]></comprobante></autorizacion></autorizaciones></RespuestaAutorizacionComprobante>')
    result=SriClient('1',Session(Response(response))).authorize('123')
    assert result['document']=='<factura/>'
    assert result['date'].endswith('-05:00')
    assert parse_xml(result['authorization']).findtext('estado')=='AUTORIZADO'

@pytest.mark.parametrize('response',[
    requests.Timeout(),Response(b'invalid XML'),Response(b'',500),
    Response(envelope('<soap:Fault xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"/>')),
    Response(envelope('<unexpected/>')),
])
def test_transport_failures_never_become_authorized(response):
    with pytest.raises(SriTransportError): SriClient('1',Session(response)).authorize('123')

def test_mismatched_key():
    data=envelope('<RespuestaAutorizacionComprobante><claveAccesoConsultada>OTHER</claveAccesoConsultada></RespuestaAutorizacionComprobante>')
    with pytest.raises(SriTransportError): SriClient('1',Session(Response(data))).authorize('123')
