"""Lectura de un correo crudo (RFC 822) con la librería estándar de Python.

No usa el ORM: devuelve un diccionario con lo que la bandeja guarda. Todo lo
que puede venir mal armado (cabeceras, juegos de caracteres, fechas) se lee
con tolerancia: un correo raro se guarda como se pueda, nunca rompe la
entrega.
"""

import email
import email.policy
import email.utils
import hashlib
import logging
import re
from datetime import datetime, timezone

from odoo.tools.mail import email_normalize, email_split, plaintext2html

_logger = logging.getLogger(__name__)

_RE_MSGID = re.compile(r"<[^<>\s]+>")

# Nombre que se le pone a un adjunto que llega sin nombre, por tipo.
_EXTENSIONES = {
    "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp",
    "application/pdf": "pdf", "text/calendar": "ics", "message/rfc822": "eml",
    "text/plain": "txt", "text/html": "html",
}


# RE:, Re:, RV:, FW:, Fwd:, AW: (y «RE[2]:» de algunos clientes), al inicio del asunto.
_RE_PREFIJO = re.compile(r"^\s*(?:re|rv|fw|fwd|aw|reenv)\s*(?:\[\d+\]|\(\d+\))?\s*:\s*", re.IGNORECASE)


def asunto_normalizado(asunto):
    """Asunto sin prefijos de respuesta/reenvío (quitados una y otra vez, para
    «RE: RV: Re: X»), sin espacios de más y en minúsculas, para comparar."""
    texto = asunto or ""
    while True:
        nuevo = _RE_PREFIJO.sub("", texto, count=1)
        if nuevo == texto:
            break
        texto = nuevo
    return re.sub(r"\s+", " ", texto).strip().casefold()


def ids_de_mensaje(texto):
    """Lista de Message-ID (con sus <>) que aparecen en una cabecera."""
    return _RE_MSGID.findall(texto or "")


def clave_conversacion(message_id, in_reply_to, references):
    """Clave del hilo: el primer mensaje de References (la raíz de la
    conversación); si no hay, el que se responde; si tampoco, el propio."""
    refs = ids_de_mensaje(references)
    if refs:
        return refs[0]
    refs = ids_de_mensaje(in_reply_to)
    if refs:
        return refs[0]
    return (message_id or "").strip() or False


def _cabecera(msg, nombre):
    """Valor de una cabecera como texto; '' si falta o está mal formada."""
    try:
        valores = msg.get_all(nombre) or []
        return ", ".join(str(v).strip() for v in valores if v is not None and str(v).strip())
    except Exception:  # noqa: BLE001 - una cabecera dañada no detiene la lectura
        try:
            # Sin interpretar: el texto tal como vino en el correo.
            return ", ".join(str(v).strip() for k, v in msg.raw_items() if k.lower() == nombre.lower())
        except Exception:  # noqa: BLE001
            return ""


def _decodificar(datos, juego):
    """bytes -> str probando el juego declarado, luego UTF-8 y al final latin-1."""
    if isinstance(datos, str):
        return datos
    for candidato in (juego, "utf-8", "cp1252"):
        if not candidato:
            continue
        try:
            return datos.decode(candidato)
        except (LookupError, UnicodeDecodeError):
            continue
    return datos.decode("latin-1", errors="replace")


def _texto_de(parte):
    datos = parte.get_payload(decode=True)
    if datos is None:
        datos = parte.get_payload()
        if isinstance(datos, list):
            return ""
    return _decodificar(datos, parte.get_content_charset())


def _fecha(msg):
    valor = _cabecera(msg, "Date")
    if valor:
        try:
            fecha = email.utils.parsedate_to_datetime(valor)
            if fecha.tzinfo:
                fecha = fecha.astimezone(timezone.utc).replace(tzinfo=None)
            return fecha
        except (TypeError, ValueError, IndexError, OverflowError):
            pass
    return datetime.utcnow().replace(microsecond=0)


def direcciones(*textos):
    """Correos normalizados (minúsculas) que aparecen en las cabeceras dadas."""
    vistos = []
    for texto in textos:
        for parte in email_split(texto or ""):
            normal = email_normalize(parte, strict=False)
            if normal and normal not in vistos:
                vistos.append(normal)
    return vistos


def leer_correo(crudo):
    """Lee un correo crudo y devuelve un dict con cabeceras, cuerpo y adjuntos.

    adjuntos: lista de dicts {nombre, datos (bytes), mimetype, cid}.
    El cuerpo es HTML sin sanear (lo sanea el campo Html al guardarse)."""
    if isinstance(crudo, str):
        crudo = crudo.encode("utf-8", errors="replace")
    msg = email.message_from_bytes(crudo, policy=email.policy.default)

    message_id = (ids_de_mensaje(_cabecera(msg, "Message-ID")) or [""])[0]
    if not message_id:
        # Sin Message-ID no hay cómo deduplicar, salvo por el contenido.
        message_id = "<%s@correo-sin-id.camaronmarket>" % hashlib.sha256(crudo).hexdigest()[:40]

    htmls, textos, adjuntos = [], [], []
    for parte in msg.walk():
        try:
            if parte.is_multipart():
                continue
            tipo = (parte.get_content_type() or "application/octet-stream").lower()
            disposicion = (parte.get_content_disposition() or "").lower()
            try:
                nombre = parte.get_filename()
            except Exception:  # noqa: BLE001
                nombre = None
            cid = (parte.get("Content-ID") or "").strip().strip("<>").strip()
            es_cuerpo = tipo in ("text/plain", "text/html") and disposicion != "attachment" and not nombre
            if es_cuerpo:
                (htmls if tipo == "text/html" else textos).append(_texto_de(parte))
                continue
            if tipo == "message/rfc822":
                interno = parte.get_payload()
                interno = interno[0] if isinstance(interno, list) and interno else interno
                datos = interno.as_bytes() if hasattr(interno, "as_bytes") else b""
            else:
                datos = parte.get_payload(decode=True) or b""
            if not nombre:
                nombre = "adjunto.%s" % _EXTENSIONES.get(tipo, "bin")
            adjuntos.append({"nombre": nombre, "datos": datos, "mimetype": tipo, "cid": cid})
        except Exception:  # noqa: BLE001 - una parte dañada se salta, el resto se guarda
            _logger.warning("Correo %s: parte ilegible, se omite", message_id, exc_info=True)

    if htmls:
        cuerpo = "\n".join(htmls)
    elif textos:
        cuerpo = str(plaintext2html("\n\n".join(textos)))
    else:
        cuerpo = ""

    return {
        "message_id": message_id,
        "in_reply_to": " ".join(ids_de_mensaje(_cabecera(msg, "In-Reply-To"))),
        "references": " ".join(ids_de_mensaje(_cabecera(msg, "References"))),
        "email_from": _cabecera(msg, "From"),
        "reply_to": _cabecera(msg, "Reply-To"),
        "email_to": _cabecera(msg, "To"),
        "email_cc": _cabecera(msg, "Cc"),
        "delivered_to": _cabecera(msg, "Delivered-To"),
        "asunto": _cabecera(msg, "Subject"),
        "fecha": _fecha(msg),
        "cuerpo": cuerpo,
        "adjuntos": adjuntos,
    }
