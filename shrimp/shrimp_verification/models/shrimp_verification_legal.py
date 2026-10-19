"""Borradores legales de las decisiones de verificación (19.0.1.24.0).

Igual que shrimp_marketplace/models/shrimp_cancel_policy.py: SOLO toca
versiones en BORRADOR (nunca una publicada). Sobre el borrador que ya exista
(2026-10.2) o, si no hay, uno nuevo copiando la vigente:

* Contrato de uso y Términos: inserta la sección «Verificación: respuesta de
  la verificadora, contraoferta, segunda opinión y liquidación» antes de las
  cláusulas de cierre.
* Anexo del perfil Verificador: agrega los puntos de respuesta a la orden,
  acreditación que vence, producto no presentado, segunda opinión y
  liquidación.

Una vez por base (DONE_PARAM) y, por documento, solo si el borrador no tiene
ya la marca MARKER / ANNEX_MARKER.
"""
import logging

from markupsafe import Markup, escape

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DONE_PARAM = "shrimp_verification.decision_drafts_done"
MARKER = "Verificación: respuesta de la verificadora"
ANNEX_MARKER = "Respuesta a la orden"
SUMMARY = (
    "BORRADOR (decisiones de verificación) — REVISAR CON ABOGADO antes de publicar. Agrega: la "
    "verificadora acepta o declina la orden en {resp} h (si declina o calla, el comprador elige "
    "otra o cancela sin costo); con la acreditación vencida termina sus órdenes pero no recibe "
    "nuevas; rechazo sin trabajo de campo solo por «producto no presentado» con evidencia "
    "(honorario y strike al vendedor); una sola contraoferta, sin reiniciar el plazo; si el "
    "vendedor la rechaza o no responde, la compra se cierra al precio original; segunda opinión a pedido del vendedor dentro de "
    "{impugnar} h, pagada por quien la pide; la verificación declarada se presenta en {decl} días "
    "o la compra se cancela sin costo; la verificadora cobra al cerrarse la ronda de aceptación. "
    "Al publicar, elegir Mayor o Menor."
)


class ShrimpVerificationDecisionPolicy(models.AbstractModel):
    _name = "shrimp.verification.decision.policy"
    _description = "Borradores legales de las decisiones de verificación"

    @api.model
    def _shrimp_values(self):
        V = self.env["shrimp.verification"]
        return {
            "resp": V._shrimp_response_hours(),
            "impugnar": V._shrimp_contest_hours(),
            "decl": V._shrimp_declared_days(),
            "aviso": self.env["shrimp.user.certificate.line"]._shrimp_warn_days(),
            "acept": V._horas_de_plazo(),
        }

    @api.model
    def _shrimp_sections(self, v, n0):
        def sec(titulo, cuerpo):
            return "<h4>%d. %s</h4>%s" % (n0 + 1, escape(titulo), cuerpo)

        impugnar = (
            "<li><b>Segunda opinión.</b> Si la verificadora rechaza el producto por su informe de "
            "campo, el vendedor puede impugnar el rechazo dentro de <b>{impugnar} horas</b> "
            "pidiendo que otra verificadora acreditada vuelva a verificar; mientras tanto la compra "
            "queda en espera. El honorario de la segunda verificación lo paga quien la pide, según "
            "la tarifa de la verificadora elegida y cualquiera sea su resultado. Si la segunda "
            "verificación aprueba, la compra sigue normalmente y el honorario de la primera se rige "
            "por las reglas generales; si rechaza, el rechazo se mantiene y no admite una nueva "
            "impugnación. Sin impugnación, el rechazo queda firme al vencer el plazo: la compra se "
            "cancela y el honorario lo paga el vendedor.</li>").format(**v) if v["impugnar"] else ""
        return sec(MARKER + ", contraoferta, segunda opinión y liquidación", (
            "<ul>"
            "<li><b>Respuesta de la verificadora.</b> La verificadora acepta o declina la orden "
            "dentro de <b>{resp} horas</b> desde que la recibe (asignar un técnico equivale a "
            "aceptarla). Si la declina o no responde en ese plazo, el comprador elige otra "
            "verificadora acreditada (el honorario se recalcula con la tarifa publicada por la "
            "nueva) o cancela la compra sin costo, sin strike para el vendedor.</li>"
            "<li><b>Acreditación vencida.</b> La verificadora cuya acreditación vence durante una "
            "orden puede terminar sus órdenes abiertas, pero no recibe órdenes nuevas mientras no "
            "la renueve. La Plataforma le avisa {aviso} días antes del vencimiento y el día en "
            "que vence.</li>"
            "<li><b>Veredicto y trabajo de campo.</b> Todo veredicto exige el trabajo de campo, "
            "salvo el rechazo por <b>producto no presentado</b>: el técnico llegó a la planta y el "
            "producto no estaba. Ese rechazo exige evidencia (fotos y hora de llegada a la planta); "
            "el honorario de la verificación lo paga el vendedor, que además recibe un strike, y la "
            "compra se cancela.</li>"
            "<li><b>Contraoferta.</b> Cuando el informe muestra que el producto no cumplió lo "
            "publicado, el comprador puede proponer una sola vez un precio menor; hacerlo no "
            "amplía el plazo de respuesta de {acept} horas. Si el vendedor la acepta, la compra se "
            "cierra al precio propuesto; si la rechaza o no responde dentro del plazo, la compra "
            "se cierra al precio original (el producto ya está en la planta del comprador y no "
            "se devuelve), sin strike para ninguna de las partes.</li>" + impugnar +
            "<li><b>Verificación declarada.</b> El informe de una verificación declarada por las "
            "partes debe presentarse dentro de <b>{decl} días</b> desde la fecha de entrega; si no "
            "se presenta, la compra se cancela sin costo. Antes de presentarlo, el vendedor puede "
            "desistir de la venta, con strike.</li>"
            "<li><b>Liquidación a la verificadora.</b> La Plataforma liquida a la verificadora su "
            "parte del honorario cuando se cierra la ronda de aceptación del informe (aceptado o "
            "con el trato caído) o, en un rechazo, cuando el rechazo queda firme; la retribución "
            "no depende del resultado del veredicto. El estado de cada liquidación se informa en "
            "el portal de la verificadora.</li>"
            "</ul>").format(**v))

    @api.model
    def _shrimp_annex_items(self, v):
        return (
            "<li><b>%s.</b> Acepta o declina cada orden dentro de {resp} horas desde que la "
            "recibe; si la declina o no responde, el comprador elige otra verificadora.</li>"
            "<li><b>Acreditación vencida.</b> Con la acreditación vencida puede terminar sus "
            "órdenes abiertas, pero no recibe órdenes nuevas; la Plataforma avisa {aviso} días "
            "antes y el día del vencimiento.</li>"
            "<li><b>Producto no presentado.</b> Solo ese rechazo se emite sin trabajo de campo, "
            "con fotos y la hora de llegada a la planta como evidencia.</li>"
            "<li><b>Segunda opinión.</b> Su rechazo puede ser impugnado por el vendedor; otra "
            "verificadora acreditada vuelve a verificar de forma independiente.</li>"
            "<li><b>Liquidación.</b> Su parte del honorario se liquida al cerrarse la ronda de "
            "aceptación del informe o al quedar firme el rechazo, y el estado se ve en su "
            "portal.</li>" % ANNEX_MARKER).format(**v)

    @api.model
    def _shrimp_update_verification_drafts(self, force=False):
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
            texto = Delay._shrimp_insert_sections(
                str(borrador.content or ""), lambda n0: self._shrimp_sections(v, n0))
            borrador.write({"content": Markup(texto), "change_summary": (
                (borrador.change_summary or "") + "\n\n" + resumen).strip()})
            tocadas |= borrador
        doc = Doc.search([("doc_type", "=", "annex"), ("role", "=", "verificador")], limit=1)
        if doc:
            borrador = borrador_de(doc)
            if borrador and ANNEX_MARKER not in str(borrador.content or ""):
                texto = str(borrador.content or "")
                items = self._shrimp_annex_items(v)
                if "</ol>" in texto:
                    i = texto.rfind("</ol>")
                    texto = texto[:i] + items + texto[i:]
                else:
                    texto = Delay._shrimp_insert(texto, "<ol>%s</ol>" % items)
                borrador.write({"content": Markup(texto), "change_summary": (
                    (borrador.change_summary or "") + "\n\n" + resumen).strip()})
                tocadas |= borrador
        ICP.set_param(DONE_PARAM, fields.Datetime.now().isoformat(sep=" ", timespec="seconds"))
        if tocadas:
            _logger.info("Borradores legales de las decisiones de verificación: %s",
                         ", ".join(tocadas.mapped("display_label")))
        return tocadas
