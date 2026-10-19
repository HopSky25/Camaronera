"""Borradores legales con las reglas de maquila de la auditoría de procesos
(D-14, D-34, D-47).

Mismo criterio que shrimp_marketplace/models/shrimp_delay_policy.py: las
versiones PUBLICADAS no se tocan nunca; se escribe sobre el BORRADOR que ya
tenga el anexo (p. ej. 2026-10.2) o, si no hay, se crea uno copiando la
versión vigente. La administración lo revisa con su abogado y lo publica
eligiendo Mayor / Menor. Una sola vez por base (DONE_PARAM); por documento,
no se repite si el borrador o la vigente ya tienen la marca.
"""
import logging

from markupsafe import Markup

from odoo import api, fields, models

from . import copack_plazos

_logger = logging.getLogger(__name__)

DONE_PARAM = "shrimp_copacking.legal_drafts_d14_done"
MARKER = "Actas, plazos y cancelaciones de maquila"
CLIENT_MARKER = "Maquila: actas y plazos"
SUMMARY = (
    "BORRADOR generado por el sistema — REVISAR CON ABOGADO antes de publicar. Agrega las reglas "
    "de maquila: plazo de {h} horas para firmar el acta con recordatorios y aceptación por "
    "silencio; empaque facturable y en el certificado solo con el acta firmada; mediación de "
    "disputas por la plataforma; cancelación del maquilador (antes de recibir, con motivo y "
    "reapertura de la solicitud; con el camarón en planta, solo con acta de devolución firmada por "
    "el cliente o aprobación de la plataforma); vencimiento de solicitudes ({rd} días) y ofertas "
    "({od} días); rechazo de ofertas; remanente de una adjudicación parcial publicado "
    "automáticamente; y cancelación en cascada si se cancela la compra de origen."
)


class ShrimpCopackLegal(models.AbstractModel):
    _name = "shrimp.copack.legal"
    _description = "Borradores legales de las reglas de maquila"

    @api.model
    def _shrimp_values(self):
        return {"h": copack_plazos.acceptance_hours(self.env),
                "rd": copack_plazos.request_days(self.env),
                "od": copack_plazos.offer_days(self.env)}

    @api.model
    def _item_maquilador(self, v):
        return (
            "<li><b>{m}.</b> Al registrar el empaque se abre el acta de la orden; el cliente tiene "
            "{h} horas para firmarla o dar su disconformidad, con recordatorios, y si no responde "
            "el acta se da por aceptada. El empaque solo es facturable y solo consta en el "
            "certificado de trazabilidad desde que el acta está firmada (por las dos partes o por "
            "ese silencio); mientras tanto figura como «por cobrar (sin firmar)». Las disputas las "
            "media la Plataforma, que puede dar por buena el acta o reabrirla para rectificar el "
            "empaque. Antes de recibir el camarón, el maquilador puede cancelar indicando el "
            "motivo, y la solicitud se vuelve a publicar para otras plantas; con el camarón del "
            "cliente en su planta solo puede cancelar con un acta de devolución firmada por el "
            "cliente o con la aprobación de la Plataforma. Sus ofertas vencen a los {od} días (o "
            "al terminar su disponibilidad) y el cliente puede rechazarlas. La orden firmada se "
            "cierra automáticamente.</li>").format(m=MARKER, **v)

    @api.model
    def _item_cliente(self, v):
        return (
            "<li><b>{m}.</b> Como cliente de un servicio de empaque (maquila), firma el acta de "
            "cada orden o da su disconformidad, con motivo, dentro de {h} horas desde que la "
            "planta registra el empaque; si no responde, el acta se da por aceptada. Las disputas "
            "las media la Plataforma. Sus solicitudes de empaque vencen a los {rd} días sin "
            "adjudicarse (o al pasar la fecha final pedida) y puede rechazar las ofertas que no le "
            "sirvan; si adjudica a una planta que toma menos libras de las pedidas, el resto se "
            "publica automáticamente como una solicitud nueva, y si la planta cancela, la "
            "solicitud se vuelve a publicar para las demás. Con su camarón en la planta, la planta "
            "no puede cancelar sin su firma en un acta de devolución o la aprobación de la "
            "Plataforma. Si se cancela la compra de origen del camarón, sus solicitudes y órdenes "
            "de empaque aún no recibidas se cancelan.</li>").format(m=CLIENT_MARKER, **v)

    @api.model
    def _shrimp_create_drafts(self, force=False):
        ICP = self.env["ir.config_parameter"].sudo()
        if "shrimp.legal.document.version" not in self.env:
            return False
        Version = self.env["shrimp.legal.document.version"].sudo()
        if ICP.get_param(DONE_PARAM) and not force:
            return Version.browse()
        Doc = self.env["shrimp.legal.document"].sudo()
        v = self._shrimp_values()
        creadas = Version.browse()
        for rol, marca, item in (("maquilador", MARKER, self._item_maquilador(v)),
                                 ("camaronera", CLIENT_MARKER, self._item_cliente(v)),
                                 ("empacadora", CLIENT_MARKER, self._item_cliente(v))):
            doc = Doc.search([("doc_type", "=", "annex"), ("role", "=", rol)], limit=1)
            if not doc or not doc.current_version_id:
                continue
            if any(marca in str(x.content or "") for x in doc.version_ids
                   if x.state == "draft" or x == doc.current_version_id):
                continue
            creadas |= self._new_draft(doc, item, v)
        ICP.set_param(DONE_PARAM, fields.Datetime.now().isoformat(sep=" ", timespec="seconds"))
        if creadas:
            _logger.info("Borradores legales de maquila: %s", ", ".join(creadas.mapped("name")))
        return creadas

    @api.model
    def _insert(self, base, li):
        base = (base or "").strip()
        if "</ol>" in base:
            i = base.rfind("</ol>")
            return base[:i] + li + base[i:]
        if base.endswith("</div>"):
            return base[:-len("</div>")] + "<ol>%s</ol>" % li + "</div>"
        return base + "<ol>%s</ol>" % li

    @api.model
    def _new_draft(self, doc, li, v):
        Version = self.env["shrimp.legal.document.version"].sudo()
        borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1]
        resumen = SUMMARY.format(**v)
        if borrador:
            borrador.write({
                "content": Markup(self._insert(str(borrador.content or ""), li)),
                "change_summary": ((borrador.change_summary or "") + "\n\n" + resumen).strip(),
            })
            return borrador
        return Version.create({
            "document_id": doc.id,
            "name": Version._suggest_code(doc),
            "content": Markup(self._insert(str(doc.current_version_id.content or ""), li)),
            "change_type": "minor",
            "change_summary": resumen,
            "state": "draft",
        })
