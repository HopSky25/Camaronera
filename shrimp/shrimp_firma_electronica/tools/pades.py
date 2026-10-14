"""Firma PAdES de un PDF con un certificado PKCS#12 (.p12 / .pfx) en memoria.

Este archivo no depende de Odoo a propósito: se puede probar suelto y deja en
un solo sitio todo lo que toca el material criptográfico del firmante.

Reglas que cumple (y que los tests comprueban):

* El .p12 y su contraseña solo viven en memoria, dentro de la llamada. Nada se
  escribe en disco, en la base de datos ni en el log. Al terminar se sueltan
  las referencias (``CertificadoCargado.olvidar``). Python no garantiza que la
  memoria se sobrescriba, pero ningún objeto que sobreviva a la petición
  conserva la clave.
* Los errores que llegan al usuario son ``FirmaError`` con un texto en
  español, sin detalles técnicos (ni la excepción de OpenSSL ni la ruta).
"""
import hashlib
import io
import logging
import re
import unicodedata
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtensionOID, NameOID

_logger = logging.getLogger(__name__)


class FirmaError(Exception):
    """Error que se le puede mostrar tal cual al firmante."""


# ----------------------------------------------------------------------
# Identificación del titular en los certificados ecuatorianos
# ----------------------------------------------------------------------
# Cada entidad de certificación acreditada pone la cédula y el RUC del
# titular en una extensión propia (además, o en lugar, del serialNumber del
# sujeto). Estos son los que publica FirmaEC; si una entidad usa otro, el
# barrido genérico de abajo (cualquier extensión cuyo OID acabe en .3.1 o
# .3.11 y cuyo valor sean 10 o 13 dígitos) lo encuentra igual.
OIDS_CEDULA = {
    "1.3.6.1.4.1.37746.3.1",     # Banco Central del Ecuador
    "1.3.6.1.4.1.37947.3.1",     # Security Data
    "1.3.6.1.4.1.43745.3.1",     # Consejo de la Judicatura
}
OIDS_RUC = {
    "1.3.6.1.4.1.37746.3.11",
    "1.3.6.1.4.1.37947.3.11",
    "1.3.6.1.4.1.43745.3.11",
}

_DIGITOS = re.compile(r"(\d{13}|\d{10})")


def _sin_acentos(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower()


def _texto_de_extension(valor):
    """El texto de una extensión desconocida. Suelen ser un UTF8String o un
    PrintableString en DER; a veces van los bytes en crudo."""
    raw = getattr(valor, "value", b"") or b""
    try:
        from asn1crypto.core import Asn1Value
        texto = Asn1Value.load(raw).native
        if isinstance(texto, bytes):
            texto = texto.decode("utf-8", "ignore")
        return str(texto or "")
    except Exception:  # noqa: BLE001 - extensión con formato propio
        return raw.decode("latin-1", "ignore")


def _atributo(nombre, oid):
    valores = nombre.get_attributes_for_oid(oid)
    return valores[0].value if valores else ""


def identificacion_del_titular(cert):
    """(cédula, ruc) del titular, lo que se encuentre. Cadenas vacías si no."""
    cedula, ruc = "", ""
    serial = _atributo(cert.subject, NameOID.SERIAL_NUMBER)
    m = _DIGITOS.search(serial or "")
    if m:
        if len(m.group(1)) == 13:
            ruc = m.group(1)
        else:
            cedula = m.group(1)
    for ext in cert.extensions:
        oid = ext.oid.dotted_string
        conocido = oid in OIDS_CEDULA or oid in OIDS_RUC
        generico = oid.startswith("1.3.6.1.4.1.") and (oid.endswith(".3.1") or oid.endswith(".3.11"))
        if not (conocido or generico):
            continue
        m = _DIGITOS.search(_texto_de_extension(ext.value))
        if not m:
            continue
        if (oid in OIDS_RUC or oid.endswith(".3.11")) and len(m.group(1)) == 13:
            ruc = ruc or m.group(1)
        elif len(m.group(1)) == 10:
            cedula = cedula or m.group(1)
    return cedula, ruc


def texto_identificacion(cedula, ruc):
    partes = []
    if cedula:
        partes.append("CI %s" % cedula)
    if ruc:
        partes.append("RUC %s" % ruc)
    return " / ".join(partes)


def _nombre_legible(nombre):
    cn = _atributo(nombre, NameOID.COMMON_NAME)
    org = _atributo(nombre, NameOID.ORGANIZATION_NAME)
    if cn and org and _sin_acentos(org) not in _sin_acentos(cn):
        return "%s (%s)" % (cn, org)
    return cn or org or nombre.rfc4514_string()


def datos_del_certificado(cert):
    """Lo que se guarda y se muestra del certificado. Nada de la clave."""
    cedula, ruc = identificacion_del_titular(cert)
    return {
        "titular": _atributo(cert.subject, NameOID.COMMON_NAME) or cert.subject.rfc4514_string(),
        "cedula": cedula,
        "ruc": ruc,
        "identificacion": texto_identificacion(cedula, ruc),
        "emisor": _nombre_legible(cert.issuer),
        "emisor_dn": cert.issuer.rfc4514_string(),
        "serie": format(cert.serial_number, "X"),
        "sha256": cert.fingerprint(hashes.SHA256()).hex().upper(),
        "desde": cert.not_valid_before_utc,
        "hasta": cert.not_valid_after_utc,
    }


# ----------------------------------------------------------------------
# Carga y validación
# ----------------------------------------------------------------------
class CertificadoCargado:
    """Clave y certificados del .p12, solo durante la firma."""

    def __init__(self, clave, cert, otros):
        self.clave = clave
        self.cert = cert
        self.otros = list(otros or [])

    def olvidar(self):
        self.clave = None
        self.cert = None
        self.otros = []


def cargar_p12(contenido, contrasena):
    """Abre el .p12/.pfx en memoria. ``FirmaError`` si no se puede."""
    if not contenido:
        raise FirmaError("Sube el archivo de tu firma electrónica (.p12 o .pfx).")
    if contrasena is None:
        contrasena = ""
    if isinstance(contrasena, str):
        contrasena = contrasena.encode("utf-8")
    try:
        clave, cert, otros = pkcs12.load_key_and_certificates(contenido, contrasena or None)
    except Exception:  # noqa: BLE001 - nunca se muestra el detalle de OpenSSL
        # cryptography no distingue contraseña errada de archivo dañado: el
        # mensaje cubre los dos casos sin dar pistas a quien prueba claves.
        raise FirmaError(
            "No se pudo abrir la firma electrónica: la contraseña es incorrecta "
            "o el archivo no es un certificado .p12/.pfx válido.") from None
    finally:
        contrasena = None
    if clave is None or cert is None:
        raise FirmaError(
            "El archivo no contiene una firma electrónica utilizable (falta el "
            "certificado o la clave privada).")
    return CertificadoCargado(clave, cert, otros)


def validar_certificado(cert, ahora=None, emisores_aceptados=None):
    """Vigencia, uso de clave y (opcional) emisor acreditado.

    ``emisores_aceptados``: lista de subcadenas del DN del emisor (sin
    distinguir mayúsculas ni tildes). ``None`` o vacía = no se filtra.
    """
    ahora = ahora or datetime.now(timezone.utc)
    if ahora < cert.not_valid_before_utc:
        raise FirmaError(
            "Tu certificado de firma electrónica todavía no está vigente "
            "(es válido desde el %s)." % cert.not_valid_before_utc.strftime("%d/%m/%Y"))
    if ahora > cert.not_valid_after_utc:
        raise FirmaError(
            "Tu certificado de firma electrónica está vencido (venció el %s). "
            "Renuévalo con tu entidad de certificación." % cert.not_valid_after_utc.strftime("%d/%m/%Y"))
    try:
        uso = cert.extensions.get_extension_for_oid(ExtensionOID.KEY_USAGE).value
    except x509.ExtensionNotFound:
        uso = None
    if uso is not None and not (uso.digital_signature or uso.content_commitment):
        raise FirmaError(
            "Ese certificado no está habilitado para firmar documentos (su uso "
            "de clave no incluye la firma digital). Usa el certificado de firma "
            "que te entregó tu entidad de certificación.")
    try:
        basicas = cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
        if basicas.ca:
            raise FirmaError(
                "Ese archivo es el certificado de una entidad de certificación, "
                "no la firma electrónica de una persona.")
    except x509.ExtensionNotFound:
        pass
    if emisores_aceptados:
        dn = _sin_acentos(cert.issuer.rfc4514_string())
        if not any(_sin_acentos(e).strip() and _sin_acentos(e).strip() in dn
                   for e in emisores_aceptados):
            raise FirmaError(
                "El certificado no fue emitido por una entidad de certificación "
                "acreditada en Ecuador. Usa una firma electrónica del Banco "
                "Central, Security Data, Uanataca u otra entidad acreditada por "
                "ARCOTEL.")
    return True


# ----------------------------------------------------------------------
# Firma
# ----------------------------------------------------------------------
# Caja de la firma visible, en puntos PDF (A4 = 595 x 842), en la franja
# inferior de la última página. El informe se imprime con un margen
# inferior amplio precisamente para que esta franja quede libre.
CAJA_FIRMA = (36, 26, 559, 100)
CAMPO_FIRMA = "FirmaVerificadora"


def _texto_para_sello(texto):
    """La firma visible usa Courier con WinAnsiEncoding: se queda con
    caracteres latinos (tildes y eñes sí) y cambia los demás."""
    texto = (texto or "").replace("—", "-").replace("–", "-").replace("%", "%%")
    return "".join(c if ord(c) < 256 else "?" for c in texto)


def lineas_del_sello(datos, firmado_en_local):
    lineas = [
        "Firmado electrónicamente por: %s" % datos["titular"],
    ]
    if datos.get("identificacion"):
        lineas.append("Identificación: %s" % datos["identificacion"])
    lineas.append("Emisor: %s" % datos["emisor"])
    lineas.append("Fecha y hora: %s" % firmado_en_local)
    lineas.append("Serie del certificado: %s" % datos["serie"])
    # Courier 8 pt: unos 105 caracteres caben en el recuadro.
    return [l if len(l) <= 104 else l[:101] + "..." for l in lineas]


def firmar_pdf(pdf, cargado, lineas_sello, motivo=None, lugar=None, tsa_url=None):
    """Devuelve el PDF firmado (PAdES-B-B, o B-T si hay ``tsa_url``)."""
    from asn1crypto import keys as a_keys, x509 as a_x509
    from pyhanko.pdf_utils import layout as ph_layout, text as ph_text
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields as ph_fields, signers, timestamps
    from pyhanko.stamp import TextStampStyle
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    der_clave = cargado.clave.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    try:
        cadena = SimpleCertificateStore()
        cadena.register_multiple([
            a_x509.Certificate.load(c.public_bytes(serialization.Encoding.DER))
            for c in cargado.otros])
        firmante = signers.SimpleSigner(
            signing_cert=a_x509.Certificate.load(
                cargado.cert.public_bytes(serialization.Encoding.DER)),
            signing_key=a_keys.PrivateKeyInfo.load(der_clave),
            cert_registry=cadena,
        )
        estilo = TextStampStyle(
            stamp_text=_texto_para_sello("\n".join(lineas_sello)),
            text_box_style=ph_text.TextBoxStyle(font_size=8, leading=10),
            border_width=1,
            inner_content_layout=ph_layout.SimpleBoxLayoutRule(
                x_align=ph_layout.AxisAlignment.ALIGN_MIN,
                y_align=ph_layout.AxisAlignment.ALIGN_MID,
                margins=ph_layout.Margins(left=10, right=10)),
        )
        meta = signers.PdfSignatureMetadata(
            field_name=CAMPO_FIRMA,
            md_algorithm="sha256",
            subfilter=ph_fields.SigSeedSubFilter.PADES,
            reason=motivo,
            location=lugar,
        )
        sellador = timestamps.HTTPTimeStamper(tsa_url, timeout=20) if tsa_url else None
        escritor = IncrementalPdfFileWriter(io.BytesIO(pdf))
        firmador = signers.PdfSigner(
            meta, signer=firmante, stamp_style=estilo, timestamper=sellador,
            new_field_spec=ph_fields.SigFieldSpec(
                CAMPO_FIRMA, on_page=-1, box=CAJA_FIRMA),
        )
        salida = io.BytesIO()
        try:
            firmador.sign_pdf(escritor, output=salida)
        except FirmaError:
            raise
        except Exception:  # noqa: BLE001
            # Al log va el error de pyHanko (sin la clave ni la contraseña,
            # que nunca llegan a esta función); al usuario, un texto llano.
            _logger.warning("Firma PAdES fallida", exc_info=True)
            if sellador:
                raise FirmaError(
                    "No se pudo obtener el sello de tiempo del servidor configurado. "
                    "Inténtalo de nuevo en unos minutos o avisa a la plataforma.") from None
            raise FirmaError(
                "No se pudo firmar el informe con ese certificado. Comprueba que "
                "sea tu firma electrónica vigente.") from None
        return salida.getvalue()
    finally:
        der_clave = None
        firmante = None


def sha256_hex(contenido):
    return hashlib.sha256(contenido).hexdigest().upper()
