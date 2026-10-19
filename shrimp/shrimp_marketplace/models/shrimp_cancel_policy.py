"""Borradores legales: compra no entregada, cierre de la compra verificada,
plazos de la verificadora y cancelación general (huecos #1, #2, #3 y #5 de
la auditoría de procesos).

Igual que shrimp_delay_policy.py: SOLO toca versiones en BORRADOR (nunca una
publicada). Sobre el borrador que ya exista de cada documento (o, si no hay,
uno nuevo copiando la vigente):

* corrige la confirmación automática de la recepción (solo con despacho);
* agrega los desenlaces nuevos de la incidencia;
* agrega a los strikes la cancelación por el vendedor y por la plataforma;
* inserta las secciones «Compra no entregada», «Compra verificada: cierre y
  plazos de la verificadora» y «Cancelación de la compra» antes de las
  cláusulas de cierre;
* en los anexos de perfil que hablan de entregas, ajusta la frase de la
  confirmación automática y agrega una línea sobre la cancelación.

Una vez por base (DONE_PARAM) y, por documento, solo si el borrador no tiene
ya la marca MARKER. Se llama desde shrimp_verification
(data/purchase_policy_data.xml) para incluir los plazos de la verificadora.
"""
import logging
import re

from markupsafe import Markup, escape

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DONE_PARAM = "shrimp_marketplace.purchase_policy_drafts_done"
MARKER = "Cancelación de la compra"
ANNEX_MARKER = "Cancelación de una compra"
SUMMARY = (
    "BORRADOR (cancelación y cierre de compras) — REVISAR CON ABOGADO antes de publicar. "
    "Agrega: sin despacho la recepción no se confirma automáticamente (compra «No entregada», "
    "cancelación sin costo y automática a los {nd} días); la compra verificada se completa al "
    "aceptarse el informe; plazos de la verificadora ({asg} h para asignar técnico, {vdt} días "
    "para el veredicto) con cancelación sin costo o cambio de verificadora y cancelación "
    "automática a los {vauto} días; cancelación por el vendedor (con strike), de mutuo acuerdo "
    "({horas} h para responder) y por la Plataforma; solicitud de anulación de strikes por el vendedor; desenlaces de la incidencia («no se "
    "entregó», entrega parcial). Al publicar, elegir Mayor o Menor."
)


class ShrimpCancelPolicy(models.AbstractModel):
    _name = "shrimp.cancel.policy"
    _description = "Borradores legales de cancelación y cierre de compras"

    @api.model
    def _shrimp_values(self):
        Tx = self.env["shrimp.transaction"]
        v = {
            "dias": Tx._shrimp_reception_days(),
            "nd": Tx._shrimp_not_delivered_cancel_days(),
            "horas": Tx._shrimp_delay_response_hours(),
            "asg": 48, "vdt": 3, "vrec": 24, "vauto": 7,
            "verif": "shrimp.verification" in self.env,
        }
        if v["verif"] and hasattr(self.env["shrimp.verification"], "_shrimp_deadline_params"):
            p = self.env["shrimp.verification"]._shrimp_deadline_params()
            v.update(asg=p["assign_hours"], vdt=p["verdict_days"], vrec=p["reminder_hours"],
                     vauto=p["auto_cancel_days"])
        return v

    @api.model
    def _shrimp_sections(self, v, n0):
        h = []

        def sec(titulo, cuerpo):
            h.append("<h4>%d. %s</h4>%s" % (n0 + len(h) + 1, escape(titulo), cuerpo))

        auto_nd = ("Si el comprador no la confirma ni la cancela en <b>{nd} días</b>, la compra se "
                   "cancela automáticamente sin costo para el comprador. ").format(**v) if v["nd"] else ""
        sec("Compra no entregada", (
            "<p>La confirmación automática de la recepción solo procede si el vendedor marcó el "
            "despacho en la plataforma. Si vence el plazo de recepción y el producto nunca se marcó "
            "como despachado, la compra <b>no</b> se confirma automáticamente ni se considera "
            "cumplida la entrega: queda «No entregada» y la Plataforma es notificada. El comprador "
            "puede confirmar la recepción si efectivamente recibió el producto, o cancelar la "
            "compra sin costo. " + auto_nd + "Desde ese momento el vendedor ya no puede marcar el "
            "despacho; la Plataforma puede reabrir el plazo si las partes lo justifican. La "
            "cancelación por falta de entrega suma un strike al vendedor.</p>"))
        if v["verif"]:
            auto_v = (" Si nadie la resuelve, la compra se cancela automáticamente sin costo "
                      "<b>{vauto} días</b> después del vencimiento.".format(**v)) if v["vauto"] else ""
            sec("Compra verificada: cierre y plazos de la verificadora", (
                "<p>En una compra verificada, el informe aceptado por comprador y vendedor (o dado "
                "por aceptado al vencer el plazo de respuesta) acredita la recepción: la compra se "
                "completa en ese momento y el lote pasa al inventario del comprador, con el peso "
                "verificado en planta.</p>"
                "<p>La verificadora debe asignar un técnico dentro de <b>{asg} horas</b> desde que "
                "recibe la orden y emitir el veredicto dentro de <b>{vdt} días</b> desde la fecha de "
                "entrega o cosecha de la compra (o desde la orden, si es posterior); la plataforma "
                "le recuerda los plazos antes de que venzan. Si un plazo vence, se avisa a las "
                "partes y a la Plataforma, y el comprador puede (a) cambiar a otra verificadora "
                "acreditada, con el mismo honorario y plazos nuevos, o (b) cancelar la compra sin "
                "costo: se cancela la verificación, se anula su honorario (o se emite la nota de "
                "crédito) y se libera el stock reservado, sin strike para el vendedor.{auto} La "
                "Plataforma puede además reasignar la verificadora o cancelar la verificación y la "
                "compra, con un motivo que queda registrado.</p>").format(auto=auto_v, **v))
        sec(MARKER, (
            "<p>Además de los casos de cancelación sin costo para el comprador, una compra que "
            "todavía no se completó puede cancelarse:</p>"
            "<ul>"
            "<li><b>Por el vendedor</b>, antes del despacho (o, en una compra verificada, antes de "
            "que la verificadora vaya a campo), indicando el motivo. La cancelación suma un "
            "strike al vendedor —también por fuerza mayor, como mortalidad o un problema "
            "sanitario— y el comprador no paga nada. El vendedor puede solicitar en la "
            "plataforma la anulación de un strike con una justificación y, si la tiene, una "
            "evidencia; la Plataforma la revisa y la aprueba o la rechaza con su respuesta.</li>"
            "<li><b>De mutuo acuerdo</b>: una parte lo solicita en la plataforma y la otra lo "
            "acepta dentro de <b>{horas} horas</b>; si no responde, la solicitud caduca y la "
            "compra sigue. No genera strike.</li>"
            "<li><b>Por la Plataforma</b>, en cualquier estado anterior a la compra completada, "
            "con un motivo que queda registrado (por ejemplo, fraude, error o una incidencia "
            "resuelta como «no se entregó»). Si la causa es un incumplimiento del vendedor, suma "
            "un strike.</li>"
            "</ul>"
            "<p>Al resolver una incidencia, la Plataforma puede darla por recibida, resolverla como "
            "«no se entregó» (cancela la compra), registrar una <b>entrega parcial</b> (el "
            "comprador recibe la cantidad entregada, lo no entregado vuelve al lote del vendedor y "
            "la comisión se recalcula sobre lo entregado) o reabrir el plazo de recepción.</p>"
            "<p>Efectos de toda cancelación: el producto vuelve al lote del vendedor (o se libera "
            "la reserva, si la compra estaba en verificación) y la devolución queda en la "
            "trazabilidad; la Plataforma anula sus cobros por la compra (comisión y honorario de "
            "una verificación no realizada) o emite la nota de crédito si ya los había facturado; "
            "el honorario de una verificación ya realizada lo mantiene quien lo pagó. La "
            "Plataforma no recibe ni custodia el pago de la mercadería: si el comprador ya la pagó "
            "al vendedor, este debe devolverle ese valor.</p>").format(**v))
        return "".join(h)

    @api.model
    def _shrimp_fix_text(self, base, v):
        """Ajustes puntuales sobre las secciones que ya existían."""
        base = base.replace(
            "la recepción se <b>confirma automáticamente</b>",
            "la recepción se <b>confirma automáticamente</b> (siempre que el vendedor haya marcado "
            "el despacho; si no, ver «Compra no entregada»)", 1)
        base = re.sub(
            r"(Según lo que resulte, la da por recibida o cierra la incidencia)",
            r"\1 (también puede resolverla como «no se entregó» o como entrega parcial; ver "
            r"«" + MARKER + r"»)", base, count=1)
        # Strikes: dos causas nuevas, al final de la lista de la sección.
        m = re.search(r"(Strikes del vendedor</h4>.*?)(</ul>)", base, re.S)
        if m:
            extra = ("<li>el vendedor cancela la venta;</li>"
                     "<li>la Plataforma cancela la compra por un incumplimiento del vendedor "
                     "(incluida la compra no entregada).</li>")
            base = base[:m.start(2)] + extra + base[m.start(2):]
        return base

    @api.model
    def _shrimp_update_purchase_drafts(self, force=False):
        ICP = self.env["ir.config_parameter"].sudo()
        if "shrimp.legal.document.version" not in self.env \
                or (ICP.get_param(DONE_PARAM) and not force):
            return False
        Version = self.env["shrimp.legal.document.version"].sudo()
        Doc = self.env["shrimp.legal.document"].sudo()
        Delay = self.env["shrimp.delay.policy"]
        v = self._shrimp_values()
        resumen = SUMMARY.format(**v)
        tocadas = Version.browse()

        def borrador_de(doc):
            borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1]
            if borrador:
                return borrador
            if not doc.current_version_id:
                return Version.browse()
            return Version.create({
                "document_id": doc.id, "name": Version._suggest_code(doc),
                "content": doc.current_version_id.content, "change_type": "minor",
                "change_summary": "", "state": "draft",
            })

        for codigo in ("contract", "terms"):
            doc = Doc.search([("code", "=", codigo)], limit=1)
            if not doc:
                continue
            borrador = borrador_de(doc)
            if not borrador or MARKER in str(borrador.content or ""):
                continue
            texto = self._shrimp_fix_text(str(borrador.content or ""), v)
            texto = Delay._shrimp_insert_sections(texto, lambda n0: self._shrimp_sections(v, n0))
            borrador.write({"content": Markup(texto), "change_summary": (
                (borrador.change_summary or "") + "\n\n" + resumen).strip()})
            tocadas |= borrador
        for rol in ("semillero", "laboratorio", "camaronera"):
            doc = Doc.search([("doc_type", "=", "annex"), ("role", "=", rol)], limit=1)
            if not doc or not doc.current_version_id:
                continue
            borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1]
            base = str((borrador or doc.current_version_id).content or "")
            if not re.search(r"entreg", base, re.I) or ANNEX_MARKER in base:
                continue
            borrador = borrador or borrador_de(doc)
            texto = str(borrador.content or "").replace(
                "si no lo hace, la recepción se confirma automáticamente",
                "si no lo hace y el producto se marcó como despachado, la recepción se confirma "
                "automáticamente", 1)
            li = ("<li><b>%s.</b> Como vendedor puede cancelar una venta antes del despacho (con "
                  "strike) o acordar la cancelación con el comprador; sin despacho marcado, la "
                  "compra no se da por recibida. Las reglas están en el Contrato de uso.</li>"
                  % ANNEX_MARKER)
            if "</ol>" in texto:
                i = texto.rfind("</ol>")
                texto = texto[:i] + li + texto[i:]
            else:
                texto = Delay._shrimp_insert(texto, "<ol>%s</ol>" % li)
            borrador.write({"content": Markup(texto), "change_summary": (
                (borrador.change_summary or "") + "\n\n" + resumen).strip()})
            tocadas |= borrador
        ICP.set_param(DONE_PARAM, fields.Datetime.now().isoformat(sep=" ", timespec="seconds"))
        if tocadas:
            _logger.info("Borradores legales de cancelación y cierre de compras: %s",
                         ", ".join(tocadas.mapped("display_label")))
        return tocadas
