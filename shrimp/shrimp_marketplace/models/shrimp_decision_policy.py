"""Borradores legales de las decisiones D-28, D-30, D-31 y D-13.

Igual que shrimp_cancel_policy.py y shrimp_delay_policy.py: SOLO toca
versiones en BORRADOR que ya existan (las 2026-10.2), nunca una publicada, y
una vez por documento (marca MARKER). Agrega al Contrato de uso y a los
Términos la sección «Publicaciones, sanidad y certificados» y, en los anexos
de los perfiles que venden nauplio o larva, una línea sobre el PCR positivo.
"""
import logging

from markupsafe import Markup, escape

from odoo import api, models

_logger = logging.getLogger(__name__)

MARKER = "Publicaciones, sanidad y certificados"
ANNEX_MARKER = "PCR positivo"
SUMMARY = (
    "BORRADOR (publicaciones, sanidad y certificados) — REVISAR CON ABOGADO antes de publicar. "
    "Agrega: caducidad de las publicaciones («Disponible hasta» o {dias} días tras la entrega "
    "tentativa, aviso {aviso} días antes); lote bloqueado por PCR positivo, con cancelación sin "
    "costo ni strike para los compradores; certificados vencidos marcados «Vencido» sin "
    "suspender la cuenta; retiro de la solicitud de chequeo."
)


class ShrimpDecisionPolicy(models.AbstractModel):
    _name = "shrimp.decision.policy"
    _description = "Borradores legales: publicaciones, sanidad y certificados"

    @api.model
    def _shrimp_values(self):
        Product = self.env["shrimp.product"]
        return {"dias": Product._shrimp_expiry_days(), "aviso": Product._shrimp_expiry_notice_days(),
                "cert": self.env["shrimp.cert.expiry.mixin"]._shrimp_cert_alert_days()}

    @api.model
    def _shrimp_section(self, v, n0):
        return "<h4>%d. %s</h4>%s" % (n0 + 1, escape(MARKER), (
            "<ul>"
            "<li><b>Vigencia de las publicaciones.</b> Una publicación vence en su fecha "
            "«Disponible hasta» o, si no la tiene, <b>{dias} días</b> después de su fecha "
            "tentativa de entrega (o de su publicación). La plataforma avisa al vendedor "
            "<b>{aviso} días</b> antes y, al vencer, la publicación pasa a borrador; para "
            "volver a publicarla el vendedor indica una fecha vigente.</li>"
            "<li><b>PCR positivo.</b> Si la ficha de calidad más reciente de un lote tiene algún "
            "PCR positivo (WSSV, IHHNV, AHPND o EHP), el lote queda bloqueado: se retira del "
            "marketplace y no se puede publicar ni comprar hasta que el vendedor registre una "
            "ficha nueva, de un muestreo posterior, sin positivos. La ficha positiva queda en el "
            "historial del lote y el vendedor no puede corregirla ni borrarla. Los compradores con "
            "compras de ese lote aún no recibidas son avisados y pueden cancelarlas sin costo; "
            "por tratarse de un evento sanitario, esta cancelación no suma strike al vendedor."
            "</li>"
            "<li><b>Certificados vencidos.</b> Los certificados del socio y de los lotes con fecha "
            "de expiración se avisan <b>{cert} días</b> antes y al vencer, a los administradores "
            "de la cuenta y a la Plataforma. Un certificado vencido se sigue mostrando, marcado "
            "«Vencido», y no se presenta como vigente; vencer no suspende la cuenta ni el lote, "
            "salvo la acreditación de una verificadora, que la deja sin recibir encargos nuevos."
            "</li>"
            "<li><b>Compra directa o verificada.</b> La plataforma ya no ofrece la «solicitud de "
            "chequeo»: el comprador compra directo o con verificación en campo por una "
            "verificadora acreditada.</li>"
            "</ul>").format(**v))

    @api.model
    def _shrimp_update_decision_drafts(self):
        if "shrimp.legal.document.version" not in self.env:
            return False
        Doc = self.env["shrimp.legal.document"].sudo()
        Delay = self.env["shrimp.delay.policy"]
        v = self._shrimp_values()
        resumen = SUMMARY.format(**v)
        tocadas = self.env["shrimp.legal.document.version"].sudo().browse()
        for codigo in ("contract", "terms"):
            doc = Doc.search([("code", "=", codigo)], limit=1)
            borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1] if doc else None
            if not borrador or MARKER in str(borrador.content or ""):
                continue
            texto = Delay._shrimp_insert_sections(
                str(borrador.content or ""), lambda n0: self._shrimp_section(v, n0))
            borrador.write({"content": Markup(texto), "change_summary": (
                (borrador.change_summary or "") + "\n\n" + resumen).strip()})
            tocadas |= borrador
        for rol in ("semillero", "laboratorio"):
            doc = Doc.search([("doc_type", "=", "annex"), ("role", "=", rol)], limit=1)
            borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1] if doc else None
            if not borrador or ANNEX_MARKER in str(borrador.content or ""):
                continue
            texto = str(borrador.content or "")
            li = ("<li><b>%s.</b> Si la ficha de calidad más reciente de un lote tiene un PCR "
                  "positivo, el lote se bloquea (no se publica ni se vende) hasta registrar una "
                  "ficha nueva sin positivos, y sus compradores pueden cancelar sin costo. Las "
                  "reglas están en el Contrato de uso.</li>" % ANNEX_MARKER)
            if "</ol>" in texto:
                i = texto.rfind("</ol>")
                texto = texto[:i] + li + texto[i:]
            elif "</ul>" in texto:
                i = texto.rfind("</ul>")
                texto = texto[:i] + li + texto[i:]
            else:
                texto = Delay._shrimp_insert(texto, "<ul>%s</ul>" % li)
            borrador.write({"content": Markup(texto), "change_summary": (
                (borrador.change_summary or "") + "\n\n" + resumen).strip()})
            tocadas |= borrador
        if tocadas:
            _logger.info("Borradores legales (publicaciones, sanidad y certificados): %s",
                         ", ".join(tocadas.mapped("display_label")))
        return tocadas
