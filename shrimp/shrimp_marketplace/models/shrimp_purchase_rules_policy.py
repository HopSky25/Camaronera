"""Borradores legales con las reglas de la compra decididas en la auditoría de
procesos: precio congelado (D-17), recepción en toda compra directa (D-26),
anulación de la comisión en toda cancelación (D-27), un strike por compra y
levantamiento automático de la suspensión (D-42), factura del vendedor
obligatoria con plazo y bloqueo (D-43) y trazabilidad pública (D-29).

Igual que shrimp_delay_policy.py y shrimp_cancel_policy.py: SOLO toca
versiones en BORRADOR (nunca una publicada). Trabaja sobre el borrador que ya
exista de cada documento (en Pruebas, el 2026-10.2) o, si no hay, uno nuevo
copiando la vigente. Agrega un párrafo al final de cada sección que habla de
estas reglas y corrige las frases que las contradicen. Una vez por base
(DONE_PARAM) y, por documento, solo si el borrador no tiene la marca.

No usa las marcas de los otros generadores («Notificación de retraso de
entrega», «Cancelación de la compra»): si este corre antes, los otros siguen
agregando lo suyo.
"""
import logging
import re

from markupsafe import Markup

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DONE_PARAM = "shrimp_marketplace.purchase_rules_drafts_done"
MARKER = "queda fijado cuando se crea"
SUMMARY = (
    "BORRADOR (reglas de la compra, auditoría de procesos) — REVISAR CON ABOGADO antes de "
    "publicar. Agrega: el precio de cada compra queda fijado al crearla y, mientras un lote tenga "
    "compras, el vendedor no cambia sus datos críticos y, mientras tenga compras en curso, tampoco su precio; toda compra directa lleva "
    "confirmación de recepción; la comisión se factura al confirmar la compra y se anula (o se "
    "emite nota de crédito) en toda cancelación; como máximo un strike por compra y la suspensión "
    "se levanta sola al bajar de {susp} strikes vigentes; la factura del vendedor es obligatoria "
    "dentro de {inv} días desde que la compra se completa y no se puede modificar pasados {lock} "
    "días; la trazabilidad pública solo existe para compras confirmadas o completadas, una "
    "compra cancelada se muestra con el sello «Compra cancelada» y las partes pueden retirar o "
    "renovar su enlace. Al publicar, elegir Mayor o Menor."
)


class ShrimpPurchaseRulesPolicy(models.AbstractModel):
    _name = "shrimp.purchase.rules.policy"
    _description = "Borradores legales de las reglas de la compra (auditoría de procesos)"

    @api.model
    def _shrimp_values(self):
        Tx = self.env["shrimp.transaction"]
        Strike = self.env["shrimp.seller.strike"]
        return {
            "dias": Tx._shrimp_reception_days(),
            "susp": Strike._shrimp_thresholds()[1],
            "inv": Tx._shrimp_invoice_days(),
            "lock": Tx._shrimp_invoice_lock_days(),
        }

    @staticmethod
    def _p(texto):
        return "<p>%s</p>" % texto

    @api.model
    def _shrimp_paragraphs(self, v):
        """{título de sección (regex, sin número): [párrafos]}."""
        p = self._p
        return {
            r"(Precios del servicio|Tarifas y facturaci)": [
                p("La comisión por venta se factura al confirmarse la compra. Si la compra se "
                  "cancela, por cualquier causa y la cancele quien la cancele (comprador, vendedor, "
                  "las dos partes, la Plataforma o el sistema), la Plataforma anula esa comisión y, "
                  "si ya la había facturado, emite la nota de crédito; en una entrega parcial la "
                  "recalcula sobre lo entregado."),
                p(("El vendedor registra en la plataforma la factura de la mercadería que emite "
                   "desde su sistema (número, clave de acceso o archivo) dentro de <b>{inv} días</b> "
                   "desde que la compra se completa. La plataforma le envía recordatorios y, si "
                   "vence el plazo, la venta queda marcada como «factura vencida». Pasados "
                   "<b>{lock} días</b> desde que la compra se completó, la factura registrada ya no "
                   "se puede modificar.").format(**v)),
            ],
            r"(Obligaciones del Socio|Publicaciones y operaciones)": [
                p("El precio de cada compra " + MARKER + " (compra directa, verificación o reserva) y "
                  "no cambia aunque el vendedor modifique después su publicación. Desde la primera "
                  "compra no cancelada de un lote, el vendedor ya no puede cambiar sus datos "
                  "críticos (especie, estadio, talla, presentación, línea genética, origen, unidad "
                  "y cantidad inicial); el precio de publicación no se puede cambiar mientras el "
                  "lote tenga compras en curso (ni canceladas ni completadas) y, después, solo "
                  "rige para las compras nuevas."),
            ],
            r"(Trazabilidad e integridad|Verificación y certificados)": [
                p("La página pública de trazabilidad (la del código QR del certificado) solo existe "
                  "para compras confirmadas o completadas; nunca para compras en borrador o en "
                  "verificación. Si la compra se cancela, la página lo indica con el sello «Compra "
                  "cancelada». Cada parte de la compra puede retirar la página pública o renovar su "
                  "enlace desde la compra; al renovarlo, los códigos QR ya impresos dejan de "
                  "funcionar."),
            ],
            r"Entrega y confirmación de recepción": [
                p("Toda compra directa (sin verificación) lleva esta confirmación de recepción, sea "
                  "cual sea la pareja de perfiles. La confirmación automática solo procede si el "
                  "vendedor marcó el despacho. Las compras verificadas se completan con el informe "
                  "aceptado por las partes."),
            ],
            r"Strikes del vendedor": [
                p(("Una misma compra genera como máximo <b>un strike</b>, aunque concurran varias "
                   "causas. La suspensión de la publicación se levanta <b>automáticamente</b>, con "
                   "aviso al vendedor, cuando sus strikes vigentes bajan de {susp} (porque vencen o "
                   "porque la Plataforma los anula); la Plataforma también puede levantarla antes "
                   "tras revisar la cuenta.").format(**v)),
            ],
        }

    @api.model
    def _shrimp_fix_text(self, base, v):
        """Frases que contradicen las reglas nuevas."""
        base = base.replace(
            "hasta que la Plataforma revise la cuenta y levante la suspensión;",
            "hasta que sus strikes vigentes bajen de %s (la suspensión se levanta sola) o la "
            "Plataforma la levante antes tras revisar la cuenta;" % v["susp"], 1)
        base = base.replace(
            "Si una operación se cancela por causas previstas en las reglas de la plataforma, la "
            "Plataforma emite la nota de crédito que corresponda por su servicio.",
            "Si una operación se cancela, la Plataforma anula su servicio por esa operación y, si ya "
            "lo había facturado, emite la nota de crédito que corresponda.", 1)
        return base

    @api.model
    def _shrimp_add_to_sections(self, base, parrafos):
        """Agrega los párrafos al final de cada sección cuyo título coincide."""
        heads = list(re.finditer(r"<h4>\s*(?:\d+\.)?\s*([^<]*)</h4>", base))
        inserciones = []
        for patron, textos in parrafos.items():
            for i, m in enumerate(heads):
                if re.search(patron, m.group(1), re.I):
                    fin = heads[i + 1].start() if i + 1 < len(heads) else None
                    if fin is None:
                        fin = base.rfind("</div>") if base.rstrip().endswith("</div>") else len(base)
                    inserciones.append((fin, "".join(textos)))
                    break
        for pos, html in sorted(inserciones, key=lambda x: -x[0]):
            base = base[:pos] + html + base[pos:]
        return base, bool(inserciones)

    @api.model
    def _shrimp_update_rules_drafts(self, force=False):
        ICP = self.env["ir.config_parameter"].sudo()
        if "shrimp.legal.document.version" not in self.env \
                or (ICP.get_param(DONE_PARAM) and not force):
            return False
        Version = self.env["shrimp.legal.document.version"].sudo()
        Doc = self.env["shrimp.legal.document"].sudo()
        v = self._shrimp_values()
        resumen = SUMMARY.format(**v)
        tocadas = Version.browse()
        for codigo in ("contract", "terms"):
            doc = Doc.search([("code", "=", codigo)], limit=1)
            if not doc:
                continue
            borrador = doc.version_ids.filtered(lambda x: x.state == "draft")[:1]
            if not borrador:
                if not doc.current_version_id:
                    continue
                borrador = Version.create({
                    "document_id": doc.id, "name": Version._suggest_code(doc),
                    "content": doc.current_version_id.content, "change_type": "minor",
                    "change_summary": "", "state": "draft",
                })
            texto = str(borrador.content or "")
            if MARKER in texto:
                continue
            texto = self._shrimp_fix_text(texto, v)
            texto, hubo = self._shrimp_add_to_sections(texto, self._shrimp_paragraphs(v))
            if not hubo:
                continue
            borrador.write({"content": Markup(texto), "change_summary": (
                (borrador.change_summary or "") + "\n\n" + resumen).strip()})
            tocadas |= borrador
        ICP.set_param(DONE_PARAM, fields.Datetime.now().isoformat(sep=" ", timespec="seconds"))
        if tocadas:
            _logger.info("Borradores legales de las reglas de la compra: %s",
                         ", ".join(tocadas.mapped("display_label")))
        return tocadas
