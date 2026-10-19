"""Borradores de las versiones nuevas de los documentos legales con las reglas
de entrega, recepción, retrasos y strikes.

Las versiones PUBLICADAS son inmutables: aquí solo se crean BORRADORES
(state «draft») copiando el texto vigente y agregando las secciones nuevas.
La administración los revisa (con su abogado) y los publica eligiendo el tipo
de cambio (Mayor / Menor) desde Documentos legales.

Se ejecuta en la instalación y en el -u (data/delay_policy_data.xml), una sola
vez por base (parámetro DONE_PARAM): si se borra un borrador no vuelve a
aparecer. Por documento, no se crea si ya hay un borrador que contiene las
secciones (marca: el título «Notificación de retraso de entrega») o si la
versión vigente ya las contiene. Los anexos de perfil solo se tocan si su
texto vigente habla de entregas.
"""
import logging
import re

from markupsafe import Markup, escape

from odoo import api, fields, models

from .shrimp_transaction_delay import DELAY_SINCE_PARAM

_logger = logging.getLogger(__name__)

DONE_PARAM = "shrimp_marketplace.delay_policy_drafts_done"
MARKER = "Notificación de retraso de entrega"
ANNEX_MARKER = "Entregas, retrasos y strikes"
SUMMARY = (
    "BORRADOR generado por el sistema — REVISAR CON ABOGADO antes de publicar. Agrega las reglas "
    "de: confirmación de recepción por el comprador y confirmación automática a los {dias} días "
    "con recordatorios; incidencias; notificación de retraso de entrega por el vendedor (plazo de "
    "respuesta de {horas} horas, máximo {avisos} avisos por compra y {tope} días de retraso "
    "total); negociación en una sola ronda; cancelación sin costo para el comprador y sus efectos "
    "(stock, comisión y verificación); y strikes del vendedor (causas, vigencia de {vigencia} "
    "días, umbrales de {aviso} y {susp} y sus consecuencias, anulación por la plataforma). Al "
    "publicar, elegir si el cambio es Mayor (pide nueva aceptación) o Menor."
)


class ShrimpDelayPolicy(models.AbstractModel):
    _name = "shrimp.delay.policy"
    _description = "Borradores legales de entregas, retrasos y strikes"

    # ------------------------------------------------------------------
    @api.model
    def _shrimp_values(self):
        Tx = self.env["shrimp.transaction"]
        Strike = self.env["shrimp.seller.strike"]
        aviso, susp = Strike._shrimp_thresholds()
        recordatorios = Tx._shrimp_reception_reminders()
        return {
            "dias": Tx._shrimp_reception_days(),
            "recordatorios": " y ".join(str(r) for r in recordatorios) or "—",
            "horas": Tx._shrimp_delay_response_hours(),
            "avisos": Tx._shrimp_delay_max_notices(),
            "tope": Tx._shrimp_delay_max_days(),
            "gracia": Tx._shrimp_delay_grace_days(),
            "vigencia": Strike._shrimp_expiry_days(),
            "aviso": aviso,
            "susp": susp,
        }

    @api.model
    def _shrimp_sections(self, v, n0, terms=False):
        """Secciones nuevas (HTML), numeradas desde n0 + 1."""
        h = []

        def sec(titulo, cuerpo):
            h.append("<h4>%d. %s</h4>%s" % (n0 + len(h) + 1, escape(titulo), cuerpo))

        sec("Entrega y confirmación de recepción", (
            "<p>El vendedor entrega el producto en la fecha acordada en la compra y puede marcar el "
            "despacho en la plataforma. Desde el despacho o desde la fecha de entrega, el comprador "
            "confirma en la plataforma que recibió el producto (puede agregar una nota y una foto) o "
            "reporta un problema.</p>"
            "<p>Si el comprador no confirma ni reporta un problema dentro de <b>{dias} días</b> "
            "(plazo configurable; la plataforma informa el vigente en cada compra), la recepción se "
            "<b>confirma automáticamente</b> y la entrega del vendedor se considera cumplida. Antes "
            "del vencimiento la plataforma envía recordatorios al comprador (por defecto, {rec} "
            "día(s) antes). Al confirmarse la recepción, el lote pasa al inventario del comprador en "
            "la plataforma.</p>").format(dias=v["dias"], rec=escape(v["recordatorios"])))
        sec("Incidencias", (
            "<p>En lugar de confirmar, el comprador puede reportar un problema con la entrega "
            "(cantidad, calidad, mortalidad, producto no recibido u otro), con una descripción y, si "
            "la tiene, una foto. La compra queda «con incidencia»: no se confirma automáticamente y "
            "la Plataforma revisa el caso con las dos partes. Según lo que resulte, la da por "
            "recibida o cierra la incidencia y el comprador vuelve a tener el plazo completo para "
            "confirmar. La Plataforma actúa como intermediario tecnológico y no responde por la "
            "calidad del producto.</p>"))
        sec(MARKER, (
            "<p>Si el vendedor prevé que no llegará a la fecha acordada, debe avisarlo en la "
            "plataforma con «Notificar retraso de entrega», desde la confirmación de la compra y hasta "
            "<b>{gracia} días</b> después de la fecha acordada, indicando la nueva fecha, el motivo "
            "(aguaje o clima, producción, sanitario, transporte u otro) y, si quiere, una nota.</p>"
            "<ul>"
            "<li><b>Límites:</b> como máximo <b>{avisos} avisos</b> por compra, y la nueva fecha no "
            "puede superar en más de <b>{tope} días</b> la fecha original de la compra (tope total, "
            "sumando todos los avisos).</li>"
            "<li><b>Negociación en una sola ronda:</b> el comprador tiene <b>{horas} horas</b> para "
            "aceptar la nueva fecha o proponer otra, una sola vez y dentro del tope. Si no responde en "
            "ese plazo, la nueva fecha se considera <b>aceptada tácitamente</b>.</li>"
            "<li>Si el comprador propone otra fecha, el vendedor tiene <b>{horas} horas</b> para "
            "aceptarla o rechazarla. Si la rechaza o no responde, se considera <b>rechazada</b>: la "
            "fecha acordada no cambia y el comprador puede cancelar la compra sin costo.</li>"
            "<li>Aceptada la nueva fecha (de forma expresa o tácita), la plataforma reprograma los "
            "avisos de entrega y el plazo de confirmación de recepción.</li>"
            "</ul>").format(**v))
        sec("Cancelación sin costo para el comprador", (
            "<p>El comprador puede cancelar la compra <b>sin costo</b>, mientras el producto no haya "
            "sido despachado, cuando:</p>"
            "<ul>"
            "<li>el vendedor rechazó su contrapropuesta o no la respondió en el plazo;</li>"
            "<li>el retraso total supera los <b>{tope} días</b> sobre la fecha original; o</li>"
            "<li>pasaron <b>{gracia} días</b> desde la fecha acordada sin despacho y sin aviso de "
            "retraso.</li>"
            "</ul>"
            "<p>Efectos de la cancelación: (a) la compra queda cancelada; (b) el producto vuelve al "
            "lote del vendedor y la devolución queda registrada en la trazabilidad; (c) la Plataforma "
            "anula la comisión del vendedor por esa venta y, si ya la había facturado, emite la nota "
            "de crédito; (d) si el comprador pagó una verificación que no llegó a realizarse, se anula "
            "o se emite la nota de crédito correspondiente. La Plataforma no recibe ni custodia el "
            "pago de la mercadería: si el comprador ya pagó el producto al vendedor, este debe "
            "devolverle ese valor.</p>").format(**v))
        sec("Strikes del vendedor", (
            "<p>Para proteger a los compradores, la plataforma registra <b>strikes</b> (sin multa en "
            "dinero) cuando el vendedor incumple la entrega:</p>"
            "<ul>"
            "<li>pasan <b>{gracia} días</b> de la fecha acordada sin despacho y sin aviso de retraso;</li>"
            "<li>el comprador cancela la compra por el retraso;</li>"
            "<li>el retraso total supera los <b>{tope} días</b> sobre la fecha original.</li>"
            "</ul>"
            "<p>Un retraso avisado a tiempo y aceptado por el comprador (expresa o tácitamente) "
            "<b>no</b> genera strike; solo se refleja en el indicador «Entregas a tiempo». Cada strike "
            "está vigente <b>{vigencia} días</b>. Con <b>{aviso} strikes vigentes</b> la plataforma "
            "advierte al vendedor, sus productos bajan en el orden del catálogo y no aparecen en "
            "destacados. Con <b>{susp} strikes vigentes</b> se suspende la publicación (no puede "
            "publicar productos nuevos y los suyos dejan de mostrarse y de poder comprarse); la "
            "suspensión se levanta sola, con aviso al vendedor, cuando sus strikes vigentes bajan "
            "de {susp}, y la Plataforma puede levantarla antes tras revisar la cuenta; las ventas en "
            "curso siguen normalmente. Una misma compra genera como máximo <b>un strike</b>, aunque "
            "concurran varias causas. La Plataforma puede anular un strike, con una justificación que queda "
            "registrada, cuando el incumplimiento no fue responsabilidad del vendedor. Los strikes "
            "no se publican; en el perfil público solo se muestran los indicadores «Entregas "
            "cumplidas» y «Entregas a tiempo».</p>").format(**v))
        if terms:
            h.append("<p>Los plazos y límites indicados son los vigentes por defecto; la Plataforma "
                     "puede ajustarlos e informa los aplicables en cada compra. El detalle está en "
                     "el Contrato de uso (sección general).</p>")
        return "".join(h)

    @api.model
    def _shrimp_annex_item(self, v):
        return ("<li><b>{m}.</b> Como vendedor, entrega en la fecha acordada, marca el despacho y, si "
                "no puede cumplir, avisa el retraso por la plataforma dentro de los plazos y límites "
                "del Contrato de uso (hasta {avisos} avisos y {tope} días de retraso total, con "
                "negociación de una ronda). Acepta que el comprador pueda cancelar sin costo en los "
                "casos previstos y el régimen de strikes ({aviso} strikes vigentes: advertencia y "
                "menor visibilidad; {susp}: suspensión de la publicación). Como comprador, confirma la "
                "recepción o reporta un problema dentro de {dias} días; si no lo hace, la recepción se "
                "confirma automáticamente.</li>").format(m=ANNEX_MARKER, **v)

    # ------------------------------------------------------------------
    @api.model
    def _shrimp_create_policy_drafts(self, force=False):
        """Crea los borradores (una vez por base). Devuelve las versiones creadas."""
        ICP = self.env["ir.config_parameter"].sudo()
        if not ICP.get_param(DELAY_SINCE_PARAM):
            ICP.set_param(DELAY_SINCE_PARAM, fields.Date.context_today(self).isoformat())
        Version = self.env["shrimp.legal.document.version"].sudo() \
            if "shrimp.legal.document.version" in self.env else None
        if Version is None or (ICP.get_param(DONE_PARAM) and not force):
            return Version.browse() if Version is not None else False
        Doc = self.env["shrimp.legal.document"].sudo()
        v = self._shrimp_values()
        creadas = Version.browse()
        for codigo, es_terminos in (("contract", False), ("terms", True)):
            doc = Doc.search([("code", "=", codigo)], limit=1)
            if not doc or not doc.current_version_id:
                continue
            if self._shrimp_has(doc, MARKER):
                continue

            def secciones(base, es_terminos=es_terminos):
                return self._shrimp_insert_sections(
                    base, lambda n0: self._shrimp_sections(v, n0, terms=es_terminos))
            creadas |= self._shrimp_new_draft(doc, secciones, v)
        for rol in ("semillero", "laboratorio", "camaronera"):
            doc = Doc.search([("doc_type", "=", "annex"), ("role", "=", rol)], limit=1)
            if not doc or not doc.current_version_id:
                continue
            # Solo los anexos que ya hablan de entregas.
            if not re.search(r"entreg", str(doc.current_version_id.content or ""), re.I) \
                    or self._shrimp_has(doc, ANNEX_MARKER):
                continue

            def item(base):
                li = self._shrimp_annex_item(v)
                if "</ol>" in base:
                    i = base.rfind("</ol>")
                    return base[:i] + li + base[i:]
                return self._shrimp_insert(base, "<ol>%s</ol>" % li)
            creadas |= self._shrimp_new_draft(doc, item, v)
        ICP.set_param(DONE_PARAM, fields.Datetime.now().isoformat(sep=" ", timespec="seconds"))
        if creadas:
            _logger.info("Borradores legales de entregas y strikes: %s",
                         ", ".join(creadas.mapped("display_label")))
        return creadas

    @api.model
    def _shrimp_has(self, doc, marca):
        """¿La versión vigente o un borrador ya tienen las secciones?"""
        return any(marca in str(x.content or "") for x in doc.version_ids
                   if x.state == "draft" or x == doc.current_version_id)

    @api.model
    def _shrimp_insert_sections(self, base, construir):
        """Inserta las secciones nuevas antes de «Cambios…» o «Ley…» (las
        cláusulas de cierre) y renumera las siguientes; si no las encuentra,
        al final. `construir(n0)` devuelve el HTML numerado desde n0 + 1."""
        heads = list(re.finditer(r"<h4>\s*(\d+)\.\s*([^<]*)</h4>", base))
        destino = next((m for m in heads
                        if re.match(r"(cambios|ley)\b", m.group(2).strip(), re.I)), None)
        if not destino:
            return self._shrimp_insert(base, construir(len(heads)))
        n0 = int(destino.group(1)) - 1
        nuevas = construir(n0)
        k = len(re.findall(r"<h4>", nuevas))
        resto = re.sub(r"<h4>\s*(\d+)\.",
                       lambda m: "<h4>%d." % (int(m.group(1)) + k), base[destino.start():])
        return base[:destino.start()] + nuevas + resto

    @api.model
    def _shrimp_insert(self, base, nuevas):
        """Agrega `nuevas` al final del texto, dentro del <div> que lo envuelve."""
        base = base.strip()
        if base.endswith("</div>"):
            return base[:-len("</div>")] + nuevas + "</div>"
        return base + nuevas

    @api.model
    def _shrimp_new_draft(self, doc, transformar, v):
        """Borrador con `transformar(texto)`: sobre el borrador que ya hubiera
        (un documento, un borrador) o, si no hay, copiando la vigente."""
        Version = self.env["shrimp.legal.document.version"].sudo()
        borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1]
        resumen = SUMMARY.format(**v)
        if borrador:
            borrador.write({
                "content": Markup(transformar(str(borrador.content or ""))),
                "change_summary": ((borrador.change_summary or "") + "\n\n" + resumen).strip(),
            })
            return borrador
        return Version.create({
            "document_id": doc.id,
            "name": Version._suggest_code(doc),
            "content": Markup(transformar(str(doc.current_version_id.content or ""))),
            "change_type": "minor",
            "change_summary": resumen,
            "state": "draft",
        })
