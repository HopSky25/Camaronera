import email.utils
import logging
import re

import psycopg2
from lxml import etree, html as lxml_html
from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.mail import email_normalize, email_normalize_all, formataddr, html2plaintext

from .correo_parser import asunto_normalizado, clave_conversacion, direcciones, ids_de_mensaje, leer_correo

_logger = logging.getLogger(__name__)

# Hasta dónde se busca hacia atrás la conversación de una respuesta sin referencias.
DIAS_ENHEBRADO = 90

CARPETAS = [
    ("entrada", "Bandeja de entrada"),
    ("enviados", "Enviados"),
    ("borradores", "Borradores"),
    ("archivados", "Archivados"),
    ("spam", "Spam"),
    ("papelera", "Papelera"),
]

# Acciones de ventana del módulo a las que se puede volver después de mover
# un correo (el contexto de cada acción trae su xmlid en «shrimp_accion»).
ACCIONES = {
    "shrimp_correo_entrante.action_correo_entrada",
    "shrimp_correo_entrante.action_correo_destacados",
    "shrimp_correo_entrante.action_correo_enviados",
    "shrimp_correo_entrante.action_correo_borradores",
    "shrimp_correo_entrante.action_correo_archivados",
    "shrimp_correo_entrante.action_correo_spam",
    "shrimp_correo_entrante.action_correo_papelera",
    "shrimp_correo_entrante.action_correo_todos",
}

# Campos que solo escribe el propio módulo (en sudo), nunca el cliente: el
# vínculo decide en qué registro queda la copia de lo que se envía.
CAMPOS_PROTEGIDOS = {"vinculo_modelo", "vinculo_id", "message_id"}

_RE_CID = re.compile(r"""(["'(])cid:([^"')\s>]+)""", re.IGNORECASE)
_RE_ESPACIOS = re.compile(r"\s+")


def _con_prefijo(prefijo, asunto):
    """«RE: asunto», sin repetir el prefijo si ya lo trae."""
    asunto = (asunto or "").strip()
    if asunto.lower().startswith(prefijo.lower() + ":"):
        return asunto
    return "%s: %s" % (prefijo, asunto) if asunto else "%s:" % prefijo


def _texto_plano(html):
    """Texto corrido de un HTML (para la vista previa de la lista)."""
    if not html:
        return ""
    try:
        raiz = lxml_html.fromstring(str(html))
    except (etree.ParserError, ValueError):
        return _RE_ESPACIOS.sub(" ", html2plaintext(str(html))).strip()
    for nodo in raiz.xpath("//style|//script|//head|//title"):
        nodo.drop_tree()
    for salto in raiz.xpath("//br|//p|//div|//tr|//li"):
        salto.tail = " " + (salto.tail or "")
    return _RE_ESPACIOS.sub(" ", raiz.text_content()).strip()


def _bloque_firma(firma):
    if not firma or not html2plaintext(firma).strip() and "<img" not in str(firma):
        return Markup("")
    return Markup('<div class="o_shrimp_firma">%s</div>') % Markup(firma)


class ShrimpCorreo(models.Model):
    """Un correo de un buzón: recibido, enviado o en borrador.

    No hereda mail.thread a propósito: la bandeja es la conversación, no hace
    falta un chatter (ni sus notificaciones) en cada correo."""

    _name = "shrimp.correo"
    _description = "Correo"
    _order = "fecha desc, id desc"
    _rec_name = "asunto"

    buzon_id = fields.Many2one(
        "shrimp.buzon", "Buzón", required=True, index=True, ondelete="restrict",
        default=lambda self: self.env["shrimp.buzon"]._buzones_permitidos()[:1].id)
    buzon_direccion = fields.Char(related="buzon_id.direccion", string="Dirección del buzón")
    direccion = fields.Selection([("entrante", "Recibido"), ("saliente", "Enviado")],
                                 "Sentido", required=True, default="saliente")
    carpeta = fields.Selection(CARPETAS, "Carpeta", required=True, default="borradores", index=True)
    carpeta_anterior = fields.Selection(CARPETAS, "Carpeta anterior")
    leido = fields.Boolean("Leído", default=True, index=True)
    destacado = fields.Boolean("Destacado")

    email_from = fields.Char("De")
    reply_to = fields.Char("Responder a")
    email_to = fields.Char("Para")
    email_cc = fields.Char("CC")
    email_bcc = fields.Char("CCO")
    asunto = fields.Char("Asunto")
    body_html = fields.Html("Cuerpo", sanitize_style=True)
    resumen = fields.Char("Vista previa", compute="_compute_resumen", store=True)
    fecha = fields.Datetime("Fecha", default=fields.Datetime.now, index=True)
    fecha_corta = fields.Char("Recibido", compute="_compute_fecha_corta")
    contraparte = fields.Char("De / Para", compute="_compute_contraparte")

    message_id = fields.Char("Message-ID", index=True, copy=False)
    in_reply_to = fields.Char("En respuesta a", copy=False)
    references = fields.Text("Referencias", copy=False)
    # Editable para poder agrupar una respuesta enhebrada por asunto (sin
    # referencias válidas) con el correo que responde.
    conversacion = fields.Char("Clave de la conversación", compute="_compute_conversacion",
                               store=True, readonly=False, index=True)
    hilo_id = fields.Many2one("shrimp.correo.hilo", "Conversación", index=True, ondelete="set null",
                              copy=False)
    hilo_cantidad = fields.Integer("Correos en la conversación", compute="_compute_hilo_cantidad")

    attachment_ids = fields.Many2many("ir.attachment", "shrimp_correo_ir_attachment_rel",
                                      "correo_id", "attachment_id", string="Adjuntos")
    tiene_adjuntos = fields.Boolean("Con adjuntos", compute="_compute_tiene_adjuntos", store=True)

    partner_id = fields.Many2one("res.partner", "Contacto", index=True,
                                 help="Contacto de Odoo del remitente (o del destinatario si lo enviamos).")
    para_partner_ids = fields.Many2many("res.partner", "shrimp_correo_para_partner_rel",
                                        "correo_id", "partner_id", string="Contactos",
                                        help="Agrega los correos de estos contactos a «Para».")
    plantilla_id = fields.Many2one("mail.template", "Plantilla",
                                   domain=[("model", "=", "res.partner")],
                                   help="Se completa con los datos del primer contacto elegido.")

    vinculo_modelo = fields.Char("Modelo vinculado", copy=False)
    vinculo_id = fields.Many2oneReference("Registro vinculado", model_field="vinculo_modelo", copy=False)
    vinculo_nombre = fields.Char("Vinculado a", compute="_compute_vinculo_nombre")

    error_envio = fields.Text("Error de envío", copy=False)
    buzones_permitidos_ids = fields.Many2many("shrimp.buzon", compute="_compute_buzones_permitidos_ids")

    _mensaje_uniq = models.UniqueIndex(
        "(buzon_id, direccion, message_id) WHERE message_id IS NOT NULL",
        "Ese correo ya está en el buzón.")

    # ------------------------------------------------------------------
    # Cálculos
    # ------------------------------------------------------------------

    @api.depends("asunto")
    def _compute_display_name(self):
        for correo in self:
            correo.display_name = correo.asunto or self.env._("(sin asunto)")

    @api.depends("body_html")
    def _compute_resumen(self):
        for correo in self:
            correo.resumen = _texto_plano(correo.body_html)[:150]

    @api.depends("message_id", "in_reply_to", "references")
    def _compute_conversacion(self):
        for correo in self:
            correo.conversacion = clave_conversacion(correo.message_id, correo.in_reply_to,
                                                     correo.references)

    @api.depends("attachment_ids")
    def _compute_tiene_adjuntos(self):
        for correo in self:
            correo.tiene_adjuntos = bool(correo.attachment_ids)

    @api.depends("fecha")
    @api.depends_context("tz")
    def _compute_fecha_corta(self):
        hoy = fields.Datetime.context_timestamp(self, fields.Datetime.now()).date()
        for correo in self:
            if not correo.fecha:
                correo.fecha_corta = ""
                continue
            local = fields.Datetime.context_timestamp(correo, correo.fecha)
            correo.fecha_corta = local.strftime("%H:%M") if local.date() == hoy else local.strftime("%d/%m/%Y")

    @api.depends("direccion", "email_from", "email_to", "email_cc")
    def _compute_contraparte(self):
        for correo in self:
            if correo.direccion == "entrante":
                nombre, correo_e = email.utils.parseaddr(correo.email_from or "")
                correo.contraparte = nombre or correo_e or correo.email_from or ""
            else:
                destinos = email.utils.getaddresses([correo.email_to or "", correo.email_cc or ""])
                nombres = [n or c for n, c in destinos if n or c]
                correo.contraparte = (self.env._("Para: %s", ", ".join(nombres)) if nombres
                                      else self.env._("(sin destinatarios)"))

    def _compute_hilo_cantidad(self):
        conteo = dict(self._read_group([("hilo_id", "in", self.hilo_id.ids)], ["hilo_id"], ["__count"]))
        for correo in self:
            correo.hilo_cantidad = conteo.get(correo.hilo_id, 0) if correo.hilo_id else 0

    @api.depends("vinculo_modelo", "vinculo_id")
    def _compute_vinculo_nombre(self):
        for correo in self:
            nombre = False
            if correo.vinculo_modelo in self.env and correo.vinculo_id:
                registro = self.env[correo.vinculo_modelo].browse(correo.vinculo_id)
                try:
                    if registro.exists():
                        registro.check_access("read")
                        nombre = "%s: %s" % (self.env["ir.model"]._get(correo.vinculo_modelo).name,
                                             registro.display_name)
                except AccessError:
                    nombre = self.env._("(registro sin acceso)")
            correo.vinculo_nombre = nombre

    @api.depends_context("uid")
    def _compute_buzones_permitidos_ids(self):
        permitidos = self.env["shrimp.buzon"]._buzones_permitidos()
        for correo in self:
            correo.buzones_permitidos_ids = permitidos

    # ------------------------------------------------------------------
    # ORM: controles de acceso propios
    # ------------------------------------------------------------------

    def _ids_de_comandos(self, comandos):
        ids = []
        for comando in comandos or []:
            if isinstance(comando, int):
                ids.append(comando)
            elif isinstance(comando, (list, tuple)):
                if comando[0] == 6:
                    ids.extend(comando[2] or [])
                elif comando[0] == 4:
                    ids.append(comando[1])
        return ids

    def _validar_vals_usuario(self, vals):
        """Lo que un usuario (no sudo) no puede mandar desde el cliente."""
        if self.env.su:
            return
        if CAMPOS_PROTEGIDOS & set(vals):
            raise AccessError(self.env._("Esos datos del correo no se pueden modificar."))
        if vals.get("buzon_id"):
            buzon = self.env["shrimp.buzon"].sudo().browse(vals["buzon_id"]).exists()
            if not buzon or not buzon._puede_usar():
                raise AccessError(self.env._("No tiene acceso a ese buzón."))
        if "attachment_ids" in vals:
            adjuntos = self.env["ir.attachment"].browse(self._ids_de_comandos(vals["attachment_ids"]))
            # Solo se adjunta lo que uno mismo puede leer.
            adjuntos.exists().check_access("read")

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if self.env.user.share:
                raise AccessError(self.env._("El correo es solo para usuarios internos."))
            for vals in vals_list:
                self._validar_vals_usuario(vals)
                if not vals.get("buzon_id") and not self.env["shrimp.buzon"]._buzones_permitidos():
                    raise UserError(self.env._("No tiene ningún buzón asignado."))
        correos = super().create(vals_list)
        correos._adoptar_adjuntos()
        correos._asignar_hilo()
        return correos

    def write(self, vals):
        self._validar_vals_usuario(vals)
        res = super().write(vals)
        if "attachment_ids" in vals:
            self._adoptar_adjuntos()
        if {"message_id", "in_reply_to", "references", "buzon_id", "asunto", "conversacion"} & set(vals):
            self._asignar_hilo()
        return res

    def unlink(self):
        if not self.env.su and self.filtered(lambda c: c.carpeta not in ("borradores", "papelera")):
            raise UserError(self.env._("Para eliminar un correo, muévalo primero a la Papelera."))
        return super().unlink()

    def _adoptar_adjuntos(self):
        """Los archivos subidos en el formulario quedan con res_id=0: se
        cuelgan de su correo para que hereden sus permisos."""
        for correo in self:
            sueltos = correo.sudo().attachment_ids.filtered(
                lambda a: a.res_model in (False, "shrimp.correo", "mail.compose.message")
                and not a.res_id and (self.env.su or a.create_uid == self.env.user))
            if sueltos:
                sueltos.write({"res_model": "shrimp.correo", "res_id": correo.id})

    def _asignar_hilo(self):
        Hilo = self.env["shrimp.correo.hilo"].sudo()
        for correo in self.sudo():
            if not correo.conversacion or not correo.buzon_id:
                continue
            if correo.hilo_id and correo.hilo_id.clave == correo.conversacion \
                    and correo.hilo_id.buzon_id == correo.buzon_id:
                continue
            hilo = Hilo.search([("buzon_id", "=", correo.buzon_id.id),
                                ("clave", "=", correo.conversacion)], limit=1)
            if not hilo:
                asunto = re.sub(r"^\s*((re|rv|fw|fwd|reenv)\s*:\s*)+", "", correo.asunto or "",
                                flags=re.IGNORECASE).strip()
                try:
                    with self.env.cr.savepoint():
                        hilo = Hilo.create({"name": asunto or self.env._("(sin asunto)"),
                                            "clave": correo.conversacion,
                                            "buzon_id": correo.buzon_id.id})
                except psycopg2.IntegrityError:
                    hilo = Hilo.search([("buzon_id", "=", correo.buzon_id.id),
                                        ("clave", "=", correo.conversacion)], limit=1)
            correo.hilo_id = hilo

    # ------------------------------------------------------------------
    # Lectura: abrir un correo lo marca como leído
    # ------------------------------------------------------------------

    @api.model
    def web_search_read(self, *args, **kwargs):
        # web_search_read llama a web_read: en la lista no se marca nada.
        return super(ShrimpCorreo, self.with_context(shrimp_correo_en_lista=True)).web_search_read(
            *args, **kwargs)

    def web_read(self, specification):
        ctx = self.env.context
        if (len(self) == 1 and ctx.get("shrimp_correo_marcar_leido")
                and not ctx.get("shrimp_correo_en_lista")):
            por_leer = self.filtered(lambda c: c.direccion == "entrante" and not c.leido)
            if por_leer:
                por_leer.check_access("write")
                por_leer.write({"leido": True})
        return super().web_read(specification)

    # Escribe (marca como leído): necesita el cursor de lectura y escritura
    # aunque web_read normalmente vaya por el de solo lectura.
    web_read._readonly = False

    @api.model
    def search_panel_select_range(self, field_name, **kwargs):
        """El panel izquierdo muestra cuántos no leídos tiene cada buzón y la
        bandeja de entrada (y cuántos borradores hay), como Outlook."""
        res = super().search_panel_select_range(field_name, **kwargs)
        if field_name not in ("buzon_id", "carpeta") or not res.get("values"):
            return res
        if field_name == "buzon_id":
            conteo = {buzon.id: n for buzon, n in self._read_group(
                [("carpeta", "=", "entrada"), ("leido", "=", False)], ["buzon_id"], ["__count"])}
        else:
            conteo = {
                "entrada": self.search_count([("carpeta", "=", "entrada"), ("leido", "=", False)]),
                "borradores": self.search_count([("carpeta", "=", "borradores")]),
            }
        for valor in res["values"]:
            n = conteo.get(valor.get("id"))
            if n:
                valor["display_name"] = "%s (%s)" % (valor["display_name"], n)
        return res

    # ------------------------------------------------------------------
    # Recepción (la llama el controlador /correo/entrante, en sudo)
    # ------------------------------------------------------------------

    @api.model
    def _shrimp_recibir(self, crudo, destinatario=None):
        """Procesa un correo que entregó el Email Worker de Cloudflare.

        1. Odoo lo procesa como siempre (message_process): si responde a algo
           enviado desde un registro, queda en el chatter de ese registro.
        2. Si Odoo no le encuentra ruta (el proveedor SMTP reescribe el
           Message-ID de lo que enviamos, así que las referencias de la
           respuesta no le dicen nada), se intenta enhebrar por otros medios:
           ver _shrimp_enhebrar.
        3. Además se guarda en la bandeja de cada buzón al que iba dirigido
           (el destinatario del sobre que manda el Worker, y To/Cc/Delivered-To).

        Nunca lanza por un correo mal formado: lo registra en el log y sigue.
        Devuelve {"enrutado": bool, "correos": shrimp.correo}."""
        self = self.sudo()
        resultado = {"enrutado": False, "correos": self.browse()}
        try:
            datos = leer_correo(crudo)
        except Exception:  # noqa: BLE001
            _logger.exception("Correo entrante ilegible: no se guarda en la bandeja")
            datos = None

        sin_ruta = False
        try:
            with self.env.cr.savepoint():
                hilo = self.env["mail.thread"].with_context(mail_create_nolog=True).message_process(
                    None, crudo)
                resultado["enrutado"] = bool(hilo)
        except ValueError as e:
            # Sin ruta: las referencias no apuntan a nada enviado desde Odoo.
            _logger.info("Correo entrante sin ruta en Odoo: %s", str(e)[:200])
            sin_ruta = True
        except Exception as e:  # noqa: BLE001 - la bandeja se guarda igual
            # Típico de correos mal armados (charset inexistente...): Odoo no
            # lo puede procesar, pero la bandeja sí lo guarda.
            _logger.warning("message_process no pudo con un correo entrante (%s); se guarda en la bandeja igual",
                            str(e)[:200], exc_info=True)

        if datos is None:
            return resultado

        enhebrado = {}
        if sin_ruta:
            try:
                with self.env.cr.savepoint():
                    enhebrado = self._shrimp_enhebrar(crudo, datos)
            except Exception:  # noqa: BLE001 - lo peor que pasa es que no se enhebra
                _logger.warning("No se pudo enhebrar el correo %s", datos["message_id"], exc_info=True)
                enhebrado = {}
            resultado["enrutado"] = bool(enhebrado.get("enrutado"))

        candidatas = direcciones(destinatario) + direcciones(
            datos["email_to"], datos["email_cc"], datos["delivered_to"])
        buzones = self.env["shrimp.buzon"].search([("direccion", "in", candidatas)]) if candidatas else None
        if not buzones:
            _logger.info("Correo %s sin buzón en Odoo (para %s): no se guarda",
                         datos["message_id"], ", ".join(candidatas)[:200])
            return resultado

        vinculo = self.env["mail.message"].search([
            ("message_id", "=", datos["message_id"]), ("model", "!=", False), ("res_id", "!=", 0),
        ], limit=1)
        vinculo = (vinculo.model, vinculo.res_id) if vinculo else None
        if not vinculo and enhebrado.get("registro"):
            vinculo = (enhebrado["registro"]._name, enhebrado["registro"].id)
        remitente = email_normalize(datos["email_from"] or "", strict=False)
        socio = self.env["res.partner"].search(
            [("email_normalized", "=", remitente)], limit=1) if remitente else self.env["res.partner"]
        origen = enhebrado.get("origen")
        conversacion = origen.conversacion if origen else False

        for buzon in buzones:
            if self.search_count([("buzon_id", "=", buzon.id), ("direccion", "=", "entrante"),
                                  ("message_id", "=", datos["message_id"])], limit=1):
                continue
            try:
                with self.env.cr.savepoint():
                    correo = self._crear_recibido(buzon, datos, socio, vinculo, conversacion)
            except psycopg2.IntegrityError:
                # Otra entrega simultánea del mismo correo ya lo guardó.
                continue
            except Exception:  # noqa: BLE001
                _logger.exception("No se pudo guardar el correo %s en %s", datos["message_id"],
                                  buzon.direccion)
                continue
            resultado["correos"] |= correo
        return resultado

    # ------------------------------------------------------------------
    # Enhebrado de respuestas que message_process no reconoce
    # ------------------------------------------------------------------

    @api.model
    def _shrimp_enhebrar(self, crudo, datos):
        """Busca a qué conversación responde un correo sin ruta en Odoo.

        El proveedor SMTP (Resend) reescribe el Message-ID de todo lo que
        enviamos: la respuesta trae en In-Reply-To/References un id que Odoo
        nunca vio. En orden:

        1. Referencias: algún id de In-Reply-To/References es el Message-ID de
           un correo guardado en la bandeja (copias internas, envíos que el
           proveedor no tocó). No se guarda el id del proveedor: smtplib no
           expone la respuesta final del servidor, así que no hay de dónde
           sacarlo con fiabilidad.
        2. Heurística sobre los registros: el mail.message saliente más
           reciente (90 días) con un registro detrás, enviado a ese mismo
           remitente y con el mismo asunto normalizado (o, si no tenía
           asunto, el nombre del registro).
        3. Heurística sobre la bandeja: el último correo enviado desde un
           buzón a ese remitente con el mismo asunto normalizado.

        Si aparece un registro, la respuesta se publica en su chatter como
        correo entrante, con el mismo mecanismo de Odoo (message_process
        forzado a ese registro: message_update, autor, adjuntos).

        Devuelve {"origen": shrimp.correo (para agrupar la conversación),
        "registro": registro vinculado o None, "enrutado": bool}."""
        nombre, remitente = email.utils.parseaddr(datos["email_from"] or "")
        remitente = email_normalize(remitente or "")
        asunto = asunto_normalizado(datos["asunto"])
        propias = set(self.env["shrimp.buzon"].with_context(active_test=False).search([]).mapped("direccion"))
        heuristica = bool(remitente and asunto and remitente not in propias)
        resultado = {"origen": self.browse(), "registro": None, "enrutado": False}

        # 1. Referencias contra la bandeja.
        refs = ids_de_mensaje("%s %s" % (datos["in_reply_to"] or "", datos["references"] or ""))
        origen = self.search([("message_id", "in", refs)], order="fecha desc, id desc", limit=1) \
            if refs else self.browse()
        # 2. Heurística contra los mensajes de los registros.
        registro = self._buscar_registro_respondido(remitente, asunto) if heuristica else None
        # 3. Heurística contra lo enviado desde los buzones.
        if not origen and heuristica:
            origen = self._buscar_enviado_respondido(remitente, asunto)
        if not registro and origen and origen.vinculo_modelo in self.env and origen.vinculo_id:
            candidato = self.env[origen.vinculo_modelo].browse(origen.vinculo_id).exists()
            if candidato and hasattr(candidato, "message_post"):
                registro = candidato
        resultado.update(origen=origen, registro=registro)
        if not registro:
            return resultado

        # El autor del mensaje en el chatter: el contacto con ese correo.
        if remitente and not self.env["res.partner"].search_count(
                [("email_normalized", "=", remitente)], limit=1):
            self.env["res.partner"].create({"name": nombre or remitente, "email": remitente})
        try:
            with self.env.cr.savepoint():
                hilo = self.env["mail.thread"].with_context(mail_create_nolog=True).message_process(
                    registro._name, crudo, thread_id=registro.id)
            resultado["enrutado"] = bool(hilo)
            _logger.info("Correo %s enhebrado a %s,%s (sin referencias válidas)",
                         datos["message_id"], registro._name, registro.id)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo publicar el correo %s en %s,%s", datos["message_id"],
                            registro._name, registro.id, exc_info=True)
        return resultado

    @api.model
    def _buscar_registro_respondido(self, remitente, asunto):
        """Registro del último mensaje saliente a `remitente` con ese asunto."""
        desde = fields.Datetime.subtract(fields.Datetime.now(), days=DIAS_ENHEBRADO)
        mensajes = self.env["mail.message"].search([
            ("model", "!=", False), ("res_id", "!=", 0), ("date", ">=", desde),
            ("message_type", "in", ("comment", "email", "email_outgoing")),
            "|", "|",
            ("partner_ids.email_normalized", "=", remitente),
            ("notification_ids.res_partner_id.email_normalized", "=", remitente),
            ("outgoing_email_to", "ilike", remitente),
        ], order="date desc, id desc", limit=200)
        for mensaje in mensajes:
            if mensaje.model not in self.env or mensaje.author_id.email_normalized == remitente:
                continue
            if mensaje.subtype_id.internal:
                continue  # una nota interna no le llegó al cliente
            destinatarios = set(mensaje.partner_ids.mapped("email_normalized")) \
                | set(mensaje.notification_ids.res_partner_id.mapped("email_normalized")) \
                | set(email_normalize_all(mensaje.outgoing_email_to or ""))
            if remitente not in destinatarios:
                continue
            registro = self.env[mensaje.model].browse(mensaje.res_id).exists()
            if not registro or not hasattr(registro, "message_post"):
                continue
            if mensaje.subject:
                coincide = asunto_normalizado(mensaje.subject) == asunto
            else:
                # Sin asunto, Odoo envía «Re: <nombre del registro>».
                coincide = asunto in {asunto_normalizado(mensaje.record_name),
                                      asunto_normalizado(registro.display_name)}
            if coincide:
                return registro
        return None

    @api.model
    def _buscar_enviado_respondido(self, remitente, asunto):
        """Último correo enviado desde un buzón a `remitente` con ese asunto."""
        desde = fields.Datetime.subtract(fields.Datetime.now(), days=DIAS_ENHEBRADO)
        enviados = self.search([
            ("direccion", "=", "saliente"), ("carpeta", "!=", "borradores"), ("fecha", ">=", desde),
            "|", "|", ("email_to", "ilike", remitente), ("email_cc", "ilike", remitente),
            ("email_bcc", "ilike", remitente),
        ], order="fecha desc, id desc", limit=100)
        for enviado in enviados:
            if remitente in direcciones(enviado.email_to, enviado.email_cc, enviado.email_bcc) \
                    and asunto_normalizado(enviado.asunto) == asunto:
                return enviado
        return self.browse()

    def _crear_recibido(self, buzon, datos, socio, vinculo, conversacion=False):
        vals = {
            "buzon_id": buzon.id,
            "direccion": "entrante",
            "carpeta": "entrada",
            "leido": False,
            "email_from": datos["email_from"],
            "reply_to": datos["reply_to"],
            "email_to": datos["email_to"],
            "email_cc": datos["email_cc"],
            "asunto": datos["asunto"],
            "fecha": datos["fecha"],
            "message_id": datos["message_id"],
            "in_reply_to": datos["in_reply_to"],
            "references": datos["references"],
            "partner_id": socio.id,
            "vinculo_modelo": vinculo[0] if vinculo else False,
            "vinculo_id": vinculo[1] if vinculo else False,
        }
        if conversacion:
            # Respuesta enhebrada por asunto: se agrupa con lo que responde.
            vals["conversacion"] = conversacion
        correo = self.create(vals)
        cuerpo = datos["cuerpo"] or ""
        referidos = {m.group(2).lower() for m in _RE_CID.finditer(cuerpo)}
        Adjunto = self.env["ir.attachment"]
        visibles, por_cid = Adjunto, {}
        for adj in datos["adjuntos"]:
            en_linea = bool(adj["cid"]) and adj["cid"].lower() in referidos
            adjunto = Adjunto.create({
                "name": adj["nombre"][:250],
                "raw": adj["datos"],
                "mimetype": adj["mimetype"],
                "res_model": "shrimp.correo",
                "res_id": correo.id,
            })
            if en_linea:
                # Con token: la imagen se ve también si el correo se cita al
                # responder (quien lo recibe no tiene sesión en Odoo).
                adjunto.generate_access_token()
                ruta = "/web/image" if adj["mimetype"].startswith("image/") else "/web/content"
                por_cid[adj["cid"].lower()] = "%s/%s?access_token=%s" % (ruta, adjunto.id, adjunto.access_token)
            else:
                visibles |= adjunto
        if por_cid:
            cuerpo = _RE_CID.sub(
                lambda m: m.group(1) + por_cid.get(m.group(2).lower(), "cid:" + m.group(2)), cuerpo)
        correo.write({"body_html": cuerpo, "attachment_ids": [(6, 0, visibles.ids)]})
        return correo

    # ------------------------------------------------------------------
    # Utilidades de redacción
    # ------------------------------------------------------------------

    def _comprobar_acceso(self, operacion="write"):
        """Toda acción pública empieza aquí: el cliente puede mandar los ids
        que quiera por RPC y call_kw no revisa las reglas por su cuenta."""
        if self.env.user.share and not self.env.su:
            raise AccessError(self.env._("El correo es solo para usuarios internos."))
        self.check_access(operacion)

    def _volver(self):
        """Tras mover un correo, de vuelta a la carpeta de donde se vino."""
        xmlid = self.env.context.get("shrimp_accion")
        if xmlid in ACCIONES:
            accion = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
            accion["target"] = "main"
            return accion
        return False

    def _abrir(self):
        self.ensure_one()
        ctx = {k: v for k, v in self.env.context.items()
               if k in ("shrimp_accion", "shrimp_correo_marcar_leido")}
        return {
            "type": "ir.actions.act_window",
            "res_model": "shrimp.correo",
            "res_id": self.id,
            "view_mode": "form",
            "views": [(self.env.ref("shrimp_correo_entrante.view_shrimp_correo_form").id, "form")],
            "target": "current",
            "context": ctx,
        }

    def _fecha_local(self):
        if not self.fecha:
            return ""
        return fields.Datetime.context_timestamp(self, self.fecha).strftime("%d/%m/%Y %H:%M")

    def _cita_html(self):
        """El correo original citado al pie, como lo hace Outlook."""
        self.ensure_one()
        filas = [
            (self.env._("De"), self.email_from or self.buzon_id.direccion),
            (self.env._("Enviado"), self._fecha_local()),
            (self.env._("Para"), self.email_to),
            (self.env._("CC"), self.email_cc),
            (self.env._("Asunto"), self.asunto),
        ]
        cabecera = Markup("<br/>").join(
            Markup("<b>%s:</b> %s") % (etiqueta, valor) for etiqueta, valor in filas if valor)
        return Markup(
            '<div class="o_shrimp_cita"><hr/><p>%s</p>'
            '<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;padding-left:1ex">%s</blockquote>'
            "</div>") % (cabecera, Markup(self.body_html or ""))

    @api.model
    def _direcciones_formateadas(self, texto):
        """[(normalizada, formateada)] de un campo Para/CC/CCO."""
        salida = []
        for nombre, direccion in email.utils.getaddresses([(texto or "").replace(";", ",")]):
            normal = email_normalize(direccion)
            if normal and normal not in [n for n, __ in salida]:
                salida.append((normal, formataddr((nombre, normal))))
        return salida

    @api.model
    def _validar_campo_direcciones(self, texto, etiqueta):
        """Lista de direcciones formateadas; error claro si alguna no sirve."""
        malas = []
        for nombre, direccion in email.utils.getaddresses([(texto or "").replace(";", ",")]):
            if (nombre or direccion) and not email_normalize(direccion or ""):
                malas.append(direccion or nombre)
        if malas:
            raise UserError(self.env._("Revise «%(campo)s»: %(malas)s no es una dirección válida.",
                                       campo=etiqueta, malas=", ".join(malas)))
        return [formateada for __, formateada in self._direcciones_formateadas(texto)]

    @api.model
    def _poner_firma(self, cuerpo, firma):
        """Cambia la firma dentro del cuerpo (al cambiar de buzón)."""
        bloque = _bloque_firma(firma)
        cuerpo = str(cuerpo or "")
        if "o_shrimp_firma" not in cuerpo:
            if not bloque:
                return Markup(cuerpo)
            if "o_shrimp_cita" in cuerpo:
                return Markup(cuerpo.replace('<div class="o_shrimp_cita"', str(bloque) + '<div class="o_shrimp_cita"', 1))
            return Markup(cuerpo + str(bloque))
        try:
            raiz = lxml_html.fragment_fromstring(cuerpo, create_parent="div")
        except (etree.ParserError, ValueError):
            return Markup(cuerpo)
        for nodo in raiz.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " o_shrimp_firma ")]'):
            if bloque:
                nuevo = lxml_html.fragment_fromstring(str(bloque))
                nodo.getparent().replace(nodo, nuevo)
                bloque = None
            else:
                nodo.getparent().remove(nodo)
        interno = (raiz.text or "") + "".join(
            etree.tostring(hijo, encoding="unicode", method="html") for hijo in raiz)
        return Markup(interno)

    @api.onchange("buzon_id")
    def _onchange_buzon_id(self):
        if self.carpeta == "borradores" and self.buzon_id:
            self.body_html = self._poner_firma(self.body_html, self.buzon_id.firma)

    @api.onchange("para_partner_ids")
    def _onchange_para_partner_ids(self):
        actuales = self._direcciones_formateadas(self.email_to)
        normales = {n for n, __ in actuales}
        nuevos = [f for __, f in actuales]
        for socio in self.para_partner_ids:
            normal = email_normalize(socio.email or "")
            if normal and normal not in normales:
                normales.add(normal)
                nuevos.append(formataddr((socio.name or "", normal)))
        self.email_to = ", ".join(nuevos)
        if not self.partner_id and self.para_partner_ids:
            self.partner_id = self.para_partner_ids[:1]

    @api.onchange("plantilla_id")
    def _onchange_plantilla_id(self):
        plantilla = self.plantilla_id
        if not plantilla:
            return None
        socio = self.para_partner_ids[:1] or self.partner_id
        if not socio:
            self.plantilla_id = False
            return {"warning": {
                "title": self.env._("Falta el contacto"),
                "message": self.env._("Elija primero un contacto en «Contactos» para completar la plantilla "
                                      "con sus datos."),
            }}
        socio_id = socio._origin.id or socio.id
        asunto = plantilla._render_field("subject", [socio_id], compute_lang=True)[socio_id]
        cuerpo = plantilla._render_field("body_html", [socio_id], compute_lang=True)[socio_id]
        if not self.asunto and asunto:
            self.asunto = asunto
        self.body_html = Markup(cuerpo or "") + Markup(self.body_html or "")
        return None

    # ------------------------------------------------------------------
    # Acciones: redactar, responder, reenviar, enviar
    # ------------------------------------------------------------------

    def _buzon_para_responder(self):
        permitidos = self.env["shrimp.buzon"]._buzones_permitidos()
        if not permitidos:
            raise UserError(self.env._("No tiene ningún buzón asignado para escribir."))
        return self.buzon_id if self.buzon_id in permitidos else permitidos[:1]

    def action_redactar(self):
        """Correo nuevo (botón «Redactar» de la lista; funciona sin selección)."""
        if self.env.user.share:
            raise AccessError(self.env._("El correo es solo para usuarios internos."))
        permitidos = self.env["shrimp.buzon"]._buzones_permitidos()
        if not permitidos:
            raise UserError(self.env._("No tiene ningún buzón asignado para escribir."))
        buzon = permitidos[:1]
        if self.env.context.get("searchpanel_default_buzon_id") in permitidos.ids:
            buzon = permitidos.browse(self.env.context["searchpanel_default_buzon_id"])
        borrador = self.create({
            "buzon_id": buzon.id,
            "body_html": Markup("<p><br/></p>") + _bloque_firma(buzon.firma),
        })
        return borrador._abrir()

    def _preparar(self, modo):
        self.ensure_one()
        self._comprobar_acceso("read")
        buzon = self._buzon_para_responder()
        propia = buzon.direccion
        vals = {
            "buzon_id": buzon.id,
            "partner_id": self.partner_id.id,
            "body_html": Markup("<p><br/></p>") + _bloque_firma(buzon.firma) + self._cita_html(),
        }
        if modo == "reenviar":
            vals["asunto"] = _con_prefijo("RV", self.asunto)
        else:
            vals["asunto"] = _con_prefijo("RE", self.asunto)
            if self.direccion == "entrante":
                para = self._direcciones_formateadas(self.reply_to or self.email_from)
            else:
                para = self._direcciones_formateadas(self.email_to)
            para = [(n, f) for n, f in para if n != propia]
            cc = []
            if modo == "todos":
                vistos = {n for n, __ in para} | {propia}
                otros = self._direcciones_formateadas(self.email_to) + self._direcciones_formateadas(self.email_cc)
                if self.direccion == "saliente":
                    otros = self._direcciones_formateadas(self.email_cc)
                for normal, formateada in otros:
                    if normal not in vistos:
                        vistos.add(normal)
                        cc.append(formateada)
            vals.update({
                "email_to": ", ".join(f for __, f in para),
                "email_cc": ", ".join(cc),
                "in_reply_to": self.message_id or False,
                "references": " ".join(filter(None, [self.references, self.message_id])) or False,
            })
        borrador = self.create(vals)
        if modo == "reenviar" and self.attachment_ids:
            copias = self.env["ir.attachment"]
            for adjunto in self.sudo().attachment_ids:
                copias |= adjunto.copy({"res_model": "shrimp.correo", "res_id": borrador.id})
            borrador.sudo().attachment_ids = copias
        if modo != "reenviar" and self.vinculo_modelo and self.vinculo_id:
            borrador.sudo().write({"vinculo_modelo": self.vinculo_modelo, "vinculo_id": self.vinculo_id})
        return borrador

    def action_responder(self):
        return self._preparar("responder")._abrir()

    def action_responder_todos(self):
        return self._preparar("todos")._abrir()

    def action_reenviar(self):
        return self._preparar("reenviar")._abrir()

    def action_guardar_borrador(self):
        """El formulario ya guardó al pulsar el botón: solo se vuelve a la lista."""
        self._comprobar_acceso("write")
        return self._volver() or {"type": "ir.actions.client", "tag": "display_notification",
                                  "params": {"message": self.env._("Borrador guardado."),
                                             "type": "info"}}

    def action_descartar(self):
        self._comprobar_acceso("write")
        borradores = self.filtered(lambda c: c.carpeta == "borradores")
        volver = self._volver()
        borradores.unlink()
        return volver or {"type": "ir.actions.act_window_close"}

    def _registro_vinculado(self):
        """Registro al que se cuelga la copia del envío, si quien envía lo puede leer."""
        if not (self.vinculo_modelo in self.env and self.vinculo_id):
            return None
        registro = self.env[self.vinculo_modelo].browse(self.vinculo_id).exists()
        if not registro or "message_ids" not in registro._fields:
            return None
        try:
            registro.check_access("read")
        except AccessError:
            return None
        return registro

    def action_enviar(self):
        self.ensure_one()
        self._comprobar_acceso("write")
        if self.carpeta != "borradores":
            raise UserError(self.env._("Solo se envían borradores."))
        buzon = self.buzon_id
        # Nunca se confía en el cliente: se comprueba el buzón en el servidor.
        if not buzon or not buzon._puede_usar():
            raise AccessError(self.env._("No puede enviar desde el buzón %s.", buzon.direccion or ""))
        para = self._validar_campo_direcciones(self.email_to, self.env._("Para"))
        cc = self._validar_campo_direcciones(self.email_cc, self.env._("CC"))
        cco = self._validar_campo_direcciones(self.email_bcc, self.env._("CCO"))
        if not (para or cc or cco):
            raise UserError(self.env._("Escriba al menos un destinatario."))

        buzon_s = buzon.sudo()
        remitente = formataddr((buzon_s.nombre_remitente or buzon_s.name or "", buzon_s.direccion))
        cabeceras = {}
        if self.in_reply_to:
            cabeceras["In-Reply-To"] = self.in_reply_to
        cuerpo = self.body_html or Markup("<p></p>")
        base = {
            "email_from": remitente,
            "reply_to": buzon_s.direccion,
            "subject": self.asunto or "",
            "body_html": str(cuerpo),
            "references": self.references or False,
            "attachment_ids": [(6, 0, self.sudo().attachment_ids.ids)],
            "auto_delete": True,
        }
        Mail = self.env["mail.mail"].sudo()
        correos_mail = Mail
        registro = self._registro_vinculado()
        if para or cc:
            vals = dict(base, email_to=", ".join(para), email_cc=", ".join(cc), headers=repr(cabeceras))
            if registro:
                # La copia queda en el chatter del registro y las respuestas vuelven a él.
                vals.update(model=registro._name, res_id=registro.id, is_notification=True)
            correos_mail |= Mail.create(vals)
        if cco:
            # Copia oculta: un envío aparte con el mismo Message-ID; quien la
            # recibe ve en «Para» a los destinatarios visibles, no a sí mismo.
            vals = dict(base, email_to=", ".join(cco), email_cc=False,
                        headers=repr(dict(cabeceras, **{"X-Forge-To": ", ".join(para) or "undisclosed-recipients:;"})))
            if correos_mail:
                vals["message_id"] = correos_mail[0].message_id
            correos_mail |= Mail.create(vals)
        message_id = correos_mail[0].message_id

        motivo = ""
        try:
            correos_mail.send(raise_exception=False)
        except psycopg2.Error:
            raise
        except Exception as e:  # noqa: BLE001 - p. ej. el SMTP cortó la sesión a la mitad
            motivo = str(e) or e.__class__.__name__

        fallidos = correos_mail.exists().filtered(lambda m: m.state == "exception")
        if fallidos or motivo:
            motivo = motivo or "; ".join(filter(None, fallidos.mapped("failure_reason"))) \
                or self.env._("error desconocido")
            # Fuera de la cola: si no, el cron lo reintentaría a espaldas de quien escribe.
            correos_mail.exists().unlink()
            self.sudo().error_envio = self.env._("No se pudo enviar: %s", motivo)
            _logger.warning("Correo %s desde %s no enviado: %s", self.id, buzon_s.direccion, motivo[:300])
            return False

        primero = self._direcciones_formateadas(", ".join(para + cc + cco))[:1]
        socio = self.partner_id
        if not socio and primero:
            socio = self.env["res.partner"].sudo().search([("email_normalized", "=", primero[0][0])], limit=1)
        self.sudo().write({
            "carpeta": "enviados",
            "direccion": "saliente",
            "leido": True,
            "email_from": remitente,
            "email_to": ", ".join(para),
            "email_cc": ", ".join(cc),
            "email_bcc": ", ".join(cco),
            "message_id": message_id,
            "fecha": fields.Datetime.now(),
            "error_envio": False,
            "partner_id": socio.id,
            "plantilla_id": False,
        })
        siguiente = self._volver()
        aviso = {"type": "ir.actions.client", "tag": "display_notification",
                 "params": {"message": self.env._("Correo enviado."), "type": "success"}}
        if siguiente:
            aviso["params"]["next"] = siguiente
        return aviso

    # ------------------------------------------------------------------
    # Acciones: carpetas y marcas
    # ------------------------------------------------------------------

    def action_marcar_leido(self):
        self._comprobar_acceso("write")
        self.write({"leido": True})
        return False

    def action_marcar_no_leido(self):
        self._comprobar_acceso("write")
        self.write({"leido": False})
        return self._volver()

    def action_destacar(self):
        self._comprobar_acceso("write")
        for correo in self:
            correo.destacado = not correo.destacado
        return False

    def _mover(self, carpeta):
        self._comprobar_acceso("write")
        for correo in self.filtered(lambda c: c.carpeta != carpeta and c.carpeta != "borradores"):
            correo.write({"carpeta_anterior": correo.carpeta, "carpeta": carpeta})
        return self._volver()

    def action_archivar(self):
        return self._mover("archivados")

    def action_spam(self):
        return self._mover("spam")

    def action_papelera(self):
        self._comprobar_acceso("write")
        # Un borrador a la papelera no se envía nunca: se guarda como tal.
        for correo in self.filtered(lambda c: c.carpeta != "papelera"):
            correo.write({"carpeta_anterior": correo.carpeta, "carpeta": "papelera"})
        return self._volver()

    def action_restaurar(self):
        self._comprobar_acceso("write")
        for correo in self:
            # «No es spam» devuelve a la bandeja de entrada, como Outlook.
            destino = correo.carpeta_anterior if correo.carpeta != "spam" else False
            if not destino or destino in ("papelera", "spam"):
                destino = "entrada" if correo.direccion == "entrante" else "enviados"
            if destino == "enviados" and not correo.message_id:
                destino = "borradores"
            correo.write({"carpeta": destino, "carpeta_anterior": False})
        return self._volver()

    def action_eliminar_definitivo(self):
        self._comprobar_acceso("unlink")
        if self.filtered(lambda c: c.carpeta != "papelera"):
            raise UserError(self.env._("Solo se eliminan definitivamente los correos de la Papelera."))
        volver = self._volver()
        self.unlink()
        return volver or {"type": "ir.actions.act_window_close"}

    def action_abrir(self):
        """Abre el correo (icono del clip en la lista)."""
        self.ensure_one()
        self._comprobar_acceso("read")
        return self._abrir()

    def action_ver_conversacion(self):
        self.ensure_one()
        self._comprobar_acceso("read")
        return {
            "type": "ir.actions.act_window",
            "name": self.hilo_id.name or self.env._("Conversación"),
            "res_model": "shrimp.correo",
            "view_mode": "list,form",
            "views": [(False, "list"), (self.env.ref("shrimp_correo_entrante.view_shrimp_correo_form").id, "form")],
            "domain": [("hilo_id", "=", self.hilo_id.id)],
            "context": {"shrimp_correo_marcar_leido": True},
        }

    # ------------------------------------------------------------------
    # Acciones: contacto, CRM y registro vinculado
    # ------------------------------------------------------------------

    def _remitente_o_destinatario(self):
        """(nombre, correo) del otro lado de la conversación."""
        texto = self.email_from if self.direccion == "entrante" else self.email_to
        if self.direccion == "entrante" and self.reply_to:
            texto = self.reply_to
        nombre, direccion = email.utils.getaddresses([texto or ""])[0] if texto else ("", "")
        return nombre, email_normalize(direccion or "")

    def action_vincular_contacto(self):
        """Busca o crea el contacto del remitente y lo deja vinculado."""
        self.ensure_one()
        self._comprobar_acceso("write")
        socio = self.partner_id
        if not socio:
            nombre, direccion = self._remitente_o_destinatario()
            if not direccion:
                raise UserError(self.env._("El correo no trae una dirección de remitente válida."))
            socio = self.env["res.partner"].search([("email_normalized", "=", direccion)], limit=1)
            if not socio:
                socio = self.env["res.partner"].create({"name": nombre or direccion, "email": direccion})
            # El mismo remitente en los demás correos que el usuario ve.
            otros = self.search([("partner_id", "=", False), ("direccion", "=", "entrante"),
                                 ("email_from", "ilike", direccion)])
            (self | otros).write({"partner_id": socio.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": socio.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_crear_oportunidad(self):
        self.ensure_one()
        self._comprobar_acceso("write")
        if self.vinculo_modelo == "crm.lead" and self.vinculo_id:
            lead = self.env["crm.lead"].browse(self.vinculo_id).exists()
            if lead:
                return self.action_abrir_vinculo()
        nombre, direccion = self._remitente_o_destinatario()
        vals = {
            "name": self.asunto or self.env._("Correo de %s", nombre or direccion or "?"),
            "type": "opportunity",
            "partner_id": self.partner_id.id,
            "email_from": direccion or False,
            "contact_name": False if self.partner_id else (nombre or False),
            "description": self.body_html,
            "user_id": self.env.uid,
        }
        lead = self.env["crm.lead"].create(vals)
        nota = Markup("<p>%s</p>%s") % (
            self.env._("Creada desde el correo «%(asunto)s» recibido en %(buzon)s.",
                       asunto=self.asunto or "", buzon=self.buzon_id.direccion),
            self._cita_html())
        lead.message_post(
            body=nota, message_type="comment", subtype_xmlid="mail.mt_note",
            attachments=[(a.name, a.raw) for a in self.sudo().attachment_ids])
        self.sudo().write({"vinculo_modelo": "crm.lead", "vinculo_id": lead.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": lead.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_abrir_vinculo(self):
        self.ensure_one()
        self._comprobar_acceso("read")
        registro = self._registro_vinculado() if self.vinculo_modelo else None
        if not registro:
            raise UserError(self.env._("El registro vinculado ya no existe o no tiene acceso a él."))
        return {
            "type": "ir.actions.act_window",
            "res_model": registro._name,
            "res_id": registro.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }
