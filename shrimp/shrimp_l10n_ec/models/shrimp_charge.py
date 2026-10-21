"""Puente entre los cobros de la plataforma y el SRI.

Cierra el circuito de facturación electrónica (auditoría de procesos, hueco
crítico #4 / CR-04, CR-05, CR-06, ME-20):

* Configuración explícita: con el SRI activo, sin diario de servicios, punto
  de emisión (01 y 04), forma de pago o certificado, el cobro NO se factura:
  se queda «Por facturar» con el motivo a la vista, sin gastar intentos. Antes
  se usaba en silencio el diario de ventas por defecto, que no emite.
* Nota de crédito (04) por el mismo camino que la factura (01): se firma, se
  envía y se espera la autorización. Solo se emite sobre una factura
  AUTORIZADA; si el SRI no autorizó la factura, se anula en contabilidad sin
  nota de crédito.
* El estado del comprobante en el SRI vuelve al cobro: «Rechazado por el SRI»
  con el mensaje del SRI, actividad al responsable de facturación, reenvío
  tras corregir (botón o cron) con tope de intentos y aviso al agotarlos.
* Pagador sin RUC ni cédula: consumidor final si el total no pasa del límite
  del SRI (USD 50) y está permitido en Ajustes; si no, el cobro espera y se le
  piden los datos fiscales al pagador.
"""
import logging

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.osv import expression

_logger = logging.getLogger(__name__)

# Motivo con el que nacen los cobros de los datos de demostración.
DEMO_MARK = "Datos de demostración"

# Estados de ec.sri.document que significan «no autorizado».
SRI_FAILED = ("rejected", "error")
SRI_IN_FLIGHT = ("queued", "waiting")

QUEUE_STATUS = [
    ("blocked", "Bloqueado (configuración o datos del pagador)"),
    ("to_invoice", "Por facturar"),
    ("exhausted", "Intentos agotados"),
    ("error", "Error al facturar"),
    ("sri_pending", "En el SRI (en cola / esperando)"),
    ("sri_not_sent", "Contabilizado, sin enviar al SRI"),
    ("sri_rejected", "Rechazado por el SRI"),
    ("credit_pending", "Nota de crédito pendiente"),
    ("ok", "Autorizado / cerrado"),
]


class ShrimpCharge(models.Model):
    _inherit = "shrimp.charge"

    # Todo calculado (sin columnas nuevas): se lee del comprobante SRI de la
    # factura y de la nota de crédito.
    sri_invoice_state = fields.Selection(
        related="invoice_id.ec_sri_state", string="SRI · factura")
    sri_refund_state = fields.Selection(
        related="refund_id.ec_sri_state", string="SRI · nota de crédito")
    sri_last_message = fields.Text(
        string="Última respuesta del SRI", compute="_compute_sri_queue")
    sri_short_error = fields.Char(
        string="Motivo", compute="_compute_sri_queue",
        help="Resumen del último error o respuesta del SRI (completo en la ficha).")
    sri_queue_status = fields.Selection(
        QUEUE_STATUS, string="Situación en la cola SRI", compute="_compute_sri_queue",
        search="_search_sri_queue_status")
    sri_q_pending = fields.Boolean(
        string="Cola SRI: pendientes", compute="_compute_sri_queue",
        search="_search_sri_q_pending")
    sri_q_error = fields.Boolean(
        string="Cola SRI: en error", compute="_compute_sri_queue",
        search="_search_sri_q_error")
    sri_q_rejected = fields.Boolean(
        string="Cola SRI: rechazados", compute="_compute_sri_queue",
        search="_search_sri_q_rejected")
    sri_q_retry = fields.Boolean(
        string="Cola SRI: por reintentar", compute="_compute_sri_queue",
        search="_search_sri_q_retry")
    sri_q_exhausted = fields.Boolean(
        string="Cola SRI: intentos agotados", compute="_compute_sri_queue",
        search="_search_sri_q_exhausted")

    # ------------------------------------------------------------------
    # Lectura del estado SRI
    # ------------------------------------------------------------------
    @staticmethod
    def _sri_main_doc(move, code):
        if not move:
            return move.env["ec.sri.document"]
        return move.sudo().ec_sri_document_ids.filtered(
            lambda d: d.document_type == code)[:1]

    def _sri_invoice_doc(self):
        self.ensure_one()
        return self._sri_main_doc(self.invoice_id, "01")

    def _sri_refund_doc(self):
        self.ensure_one()
        return self._sri_main_doc(self.refund_id, "04")

    def _sri_status_of(self, maximo):
        """Situación del cobro en la cola (una sola, para la columna)."""
        self.ensure_one()
        st = self.state
        sri = self._shrimp_sri_enabled()
        if st in ("pending", "error"):
            if self.invoice_attempts >= maximo:
                return "exhausted"
            if st == "pending" and self.invoice_error and not self.invoice_attempts:
                return "blocked"
            return "error" if st == "error" else "to_invoice"
        if st == "sri_rejected":
            if self.invoice_attempts >= maximo:
                return "exhausted"
            return "sri_rejected"
        if st == "to_credit":
            return "exhausted" if self.invoice_attempts >= maximo else "credit_pending"
        move_doc = False
        if st == "invoiced" and sri and self.invoice_id.l10n_latam_use_documents:
            move_doc = self._sri_invoice_doc()
        elif st == "credited" and sri and self.refund_id.l10n_latam_use_documents:
            move_doc = self._sri_refund_doc()
        else:
            return "ok"
        if not move_doc or move_doc.state == "draft":
            return "sri_not_sent"
        if move_doc.state in SRI_IN_FLIGHT:
            return "sri_pending"
        if move_doc.state in SRI_FAILED:
            return "sri_rejected"
        return "ok"

    @api.depends("state", "invoice_attempts", "invoice_error", "invoice_id", "refund_id",
                 "invoice_id.ec_sri_document_ids.state", "refund_id.ec_sri_document_ids.state",
                 "invoice_id.ec_sri_document_ids.last_message",
                 "refund_id.ec_sri_document_ids.last_message")
    def _compute_sri_queue(self):
        maximo = self._shrimp_max_auto_attempts()
        for charge in self:
            status = charge._sri_status_of(maximo)
            doc = charge._sri_refund_doc() if charge.state == "credited" \
                else charge._sri_invoice_doc()
            charge.sri_queue_status = status
            charge.sri_last_message = doc.last_message if doc else False
            texto = " ".join((charge.invoice_error or charge.sri_last_message or "").split())
            charge.sri_short_error = (texto[:110] + "…") if len(texto) > 110 else texto
            charge.sri_q_pending = status in (
                "blocked", "to_invoice", "sri_pending", "sri_not_sent", "credit_pending")
            charge.sri_q_error = status in ("error", "exhausted") or bool(
                doc and doc.state == "error")
            charge.sri_q_rejected = status == "sri_rejected"
            charge.sri_q_retry = status in (
                "to_invoice", "error", "sri_rejected", "credit_pending")
            charge.sri_q_exhausted = status == "exhausted"

    # Búsquedas: se calculan con sudo (el filtro lo usa quien no tiene
    # permisos del SRI) y devuelven un dominio por id.
    @api.model
    def _sri_queue_ids(self, predicate):
        # Abiertos + facturados/acreditados cuyo comprobante no está autorizado.
        candidatos = self.sudo().search([
            "|", "|",
            ("state", "in", ("pending", "error", "to_credit", "sri_rejected")),
            "&", ("state", "=", "invoiced"),
            ("invoice_id.ec_sri_document_ids.state", "!=", "authorized"),
            "&", "&", ("state", "=", "credited"), ("refund_id", "!=", False),
            ("refund_id.ec_sri_document_ids.state", "!=", "authorized"),
        ])
        return candidatos.filtered(predicate).ids

    @api.model
    def _sri_bool_search(self, fname, operator, value):
        # Odoo 19 normaliza «= True» a «in [True]».
        if operator in ("in", "not in"):
            valores = {bool(v) for v in value}
            if len(valores) != 1:
                return expression.TRUE_DOMAIN if (len(valores) > 1) == (operator == "in") \
                    else expression.FALSE_DOMAIN
            value, operator = valores.pop(), ("=" if operator == "in" else "!=")
        if operator not in ("=", "!="):
            return expression.FALSE_DOMAIN
        ids = self._sri_queue_ids(lambda c: c[fname])
        positivo = (operator == "=") == bool(value)
        return [("id", "in" if positivo else "not in", ids)]

    def _search_sri_q_pending(self, operator, value):
        return self._sri_bool_search("sri_q_pending", operator, value)

    def _search_sri_q_error(self, operator, value):
        return self._sri_bool_search("sri_q_error", operator, value)

    def _search_sri_q_rejected(self, operator, value):
        return self._sri_bool_search("sri_q_rejected", operator, value)

    def _search_sri_q_retry(self, operator, value):
        return self._sri_bool_search("sri_q_retry", operator, value)

    def _search_sri_q_exhausted(self, operator, value):
        return self._sri_bool_search("sri_q_exhausted", operator, value)

    def _search_sri_queue_status(self, operator, value):
        if operator in ("=", "!="):
            valores = {value}
        elif operator in ("in", "not in"):
            valores = set(value)
        else:
            return expression.FALSE_DOMAIN
        ids = self._sri_queue_ids(lambda c: c.sri_queue_status in valores)
        return [("id", "not in" if operator in ("!=", "not in") else "in", ids)]

    # ------------------------------------------------------------------
    # Configuración y datos del pagador
    # ------------------------------------------------------------------
    def _shrimp_sri_enabled(self):
        self.ensure_one()
        return bool(self.company_id.ec_sri_enabled)

    def _shrimp_sri_point(self, journal):
        return self.company_id.shrimp_service_sri_point_id or journal.ec_sri_point_id

    @api.model
    def _shrimp_final_consumer_allowed(self):
        return self.env["shrimp.settings"].get_bool(
            "shrimp_l10n_ec.final_consumer_fallback", True)

    @api.model
    def _shrimp_final_consumer_partner(self):
        return self.env.ref("l10n_ec.ec_final_consumer", raise_if_not_found=False)

    def _shrimp_payer_has_tax_id(self):
        """¿El pagador tiene una identificación que el SRI acepte?"""
        self.ensure_one()
        payer = self.payer_partner_id.commercial_partner_id.sudo()
        if not payer.vat:
            return False
        payer._shrimp_sync_identification_type()
        try:
            data = payer._ec_sri_partner_data()
        except Exception:  # noqa: BLE001 - UserError del SRI: datos incompletos
            return False
        return data.get("type") not in (False, None, "07")

    def _shrimp_estimated_total(self):
        """Total con impuestos que tendría la factura (para el límite del
        consumidor final)."""
        self.ensure_one()
        product = self._get_service_product(self.charge_type, self.company_id)
        # Los mismos impuestos y la misma línea (1 × importe) que la factura.
        taxes = self._service_taxes_for(product, self.company_id)
        if not taxes:
            return self.amount or 0.0
        res = taxes.compute_all(self.amount or 0.0, currency=self.currency_id,
                                quantity=1.0, product=product)
        return res["total_included"]

    def _shrimp_use_final_consumer(self):
        """Factura a «consumidor final» (9999999999999): pagador sin RUC ni
        cédula, permitido en Ajustes y total dentro del límite del SRI."""
        self.ensure_one()
        if not self._shrimp_sri_enabled() or self._shrimp_payer_has_tax_id():
            return False
        if not self._shrimp_final_consumer_allowed() or not self._shrimp_final_consumer_partner():
            return False
        from odoo.addons.l10n_ec_sri_community.services import validators
        return self._shrimp_estimated_total() <= validators.FINAL_CONSUMER_LIMIT

    def _invoicing_blocker(self):
        motivo = super()._invoicing_blocker()
        if motivo or not self._shrimp_sri_enabled():
            return motivo
        problemas = self.company_id._shrimp_sri_config_problems()
        if problemas:
            return _("Facturación electrónica sin configurar: %s") % " ".join(problemas)
        if not self._shrimp_payer_has_tax_id() and not self._shrimp_use_final_consumer():
            from odoo.addons.l10n_ec_sri_community.services import validators
            return _(
                "El pagador %(p)s no tiene RUC ni cédula válidos. Se le pidieron sus "
                "datos fiscales; el cobro se facturará cuando los complete (a consumidor "
                "final solo se factura hasta USD %(l)s y si está permitido en Ajustes).") % {
                    "p": self.payer_partner_id.commercial_partner_id.display_name,
                    "l": validators.FINAL_CONSUMER_LIMIT}
        return False

    def _on_invoicing_blocked(self, reason):
        """Primer bloqueo por falta de datos fiscales: se le piden al pagador."""
        res = super()._on_invoicing_blocked(reason)
        payer = self.payer_partner_id.commercial_partner_id
        if self._shrimp_sri_enabled() and payer.email \
                and not self.company_id._shrimp_sri_config_problems() \
                and not self._shrimp_payer_has_tax_id():
            self.message_notify(
                partner_ids=payer.ids,
                subject=_("Complete sus datos de facturación"),
                body=_(
                    "Para emitirle la factura electrónica del servicio %(c)s (%(d)s) "
                    "necesitamos su RUC o cédula. Complételo en «Mi cuenta» de la "
                    "plataforma; la factura se emitirá automáticamente.") % {
                        "c": self.name, "d": self.description or self.origin or ""})
        return res

    # ------------------------------------------------------------------
    # Factura (01)
    # ------------------------------------------------------------------
    def _prepare_sale_order_vals(self, product):
        vals = super()._prepare_sale_order_vals(product)
        if self._shrimp_use_final_consumer():
            vals["partner_id"] = self._shrimp_final_consumer_partner().id
            vals["note"] = _("Servicio prestado a %s (consumidor final: sin RUC ni "
                             "cédula registrados).") % self.payer_partner_id.display_name
        return vals

    def _prepare_service_invoice(self, invoice):
        """Deja la factura de servicio lista para el SRI antes de contabilizar.

        Diario y punto de emisión de la plataforma (obligatorios: los exige
        _invoicing_blocker), tipo de documento 01 (Factura) y forma de pago.
        El cliente necesita su tipo de identificación (RUC/cédula/pasaporte):
        se sincroniza desde su número.
        """
        invoice = super()._prepare_service_invoice(invoice)
        if not self._shrimp_sri_enabled():
            return invoice
        company = self.company_id
        self.payer_partner_id.commercial_partner_id._shrimp_sync_identification_type()
        vals = {}
        journal = company.shrimp_service_journal_id
        if journal and invoice.journal_id != journal:
            vals["journal_id"] = journal.id
        point = company.shrimp_service_sri_point_id
        if point:
            vals["ec_sri_point_id"] = point.id
            vals["ec_sri_establishment_id"] = point.establishment_id.id
        if company.shrimp_service_payment_id:
            vals["l10n_ec_sri_payment_id"] = company.shrimp_service_payment_id.id
        if vals:
            invoice.write(vals)
        # El XSD del SRI no admite saltos de línea en <descripcion>, y Ventas
        # arma la línea como «[CÓDIGO] Producto\nConcepto»: una sola línea.
        for line in invoice.invoice_line_ids.filtered(
                lambda l: l.display_type == "product" and "\n" in (l.name or "")):
            line.name = " – ".join(p.strip() for p in line.name.splitlines() if p.strip())[:300]
        factura = self.env.ref("l10n_ec.ec_dt_01", raise_if_not_found=False)
        if factura and invoice.l10n_latam_use_documents \
                and invoice.l10n_latam_document_type_id != factura \
                and factura in invoice.l10n_latam_available_document_type_ids:
            invoice.l10n_latam_document_type_id = factura
        return invoice

    def _after_service_invoice_posted(self, invoice):
        """Firma y encola el comprobante electrónico para el SRI (si está activado).

        El envío lo hace el cron del módulo SRI. Si la preparación o la firma
        fallan, la factura sigue contabilizada y el cobro pasa a «Rechazado por
        el SRI» con el motivo (ver _after_service_invoice_failed).

        _ec_sri_emit_keep NO lanza por esos fallos: así el comprobante y su clave
        de acceso (con su fecha de emisión) se conservan para el reintento. Si se
        lanzara, el savepoint de shrimp_marketplace los desharía y el reintento
        crearía el comprobante de nuevo; en producción, al día siguiente ya no
        se podría emitir («fecha de emisión = hoy»).
        """
        res = super()._after_service_invoice_posted(invoice)
        if self._shrimp_sri_enabled() and self.company_id.shrimp_sri_auto_emit \
                and invoice.l10n_latam_use_documents:
            _doc, error = invoice._ec_sri_emit_keep()
            if error:
                # Lo mismo que hace shrimp_marketplace cuando este gancho lanza.
                self.message_post(body=_(
                    "La factura %(f)s quedó contabilizada, pero no se pudo completar "
                    "el envío electrónico: %(e)s") % {"f": invoice.name, "e": error})
                self._after_service_invoice_failed(invoice, error)
        return res

    def _after_service_invoice_failed(self, invoice, error):
        res = super()._after_service_invoice_failed(invoice, error)
        if self._shrimp_sri_enabled() and invoice.l10n_latam_use_documents:
            self.write({"state": "sri_rejected", "invoice_attempts": 0,
                        "invoice_error": _("Factura %(f)s contabilizada, pero no se pudo "
                                           "preparar/enviar al SRI: %(e)s") % {
                                               "f": invoice.name, "e": str(error)[:1500]}})
            self._shrimp_escalate(
                _("La factura %(f)s del cobro %(c)s no se pudo enviar al SRI: %(e)s. "
                  "Corrija la causa y use «Reintentar» en el cobro.") % {
                      "f": invoice.name, "c": self.name, "e": str(error)[:500]},
                summary=_("Factura no enviada al SRI"))
        return res

    # ------------------------------------------------------------------
    # Nota de crédito (04)
    # ------------------------------------------------------------------
    def _prepare_refund_vals(self, reason):
        """La nota de crédito del SRI es el documento 04 (no la 01 de la
        factura que anula) y lleva el motivo."""
        vals = super()._prepare_refund_vals(reason)
        if self._shrimp_sri_enabled() and self.invoice_id.l10n_latam_use_documents:
            nota = self.env.ref("l10n_ec.ec_dt_04", raise_if_not_found=False)
            if nota:
                vals["l10n_latam_document_type_id"] = nota.id
            vals["ec_sri_reason"] = (reason or _("Anulación del servicio"))[:300]
        return vals

    def _prepare_service_refund(self, refund, reason):
        """Nota de crédito del SRI (04): exige el motivo y el punto de emisión."""
        refund = super()._prepare_service_refund(refund, reason)
        if not self._shrimp_sri_enabled():
            return refund
        vals = {"ec_sri_reason": (reason or _("Anulación del servicio"))[:300]}
        point = self._shrimp_sri_point(refund.journal_id) or self.invoice_id.ec_sri_point_id
        if point:
            vals["ec_sri_point_id"] = point.id
            vals["ec_sri_establishment_id"] = point.establishment_id.id
        refund.write(vals)
        return refund

    def _credit_precheck(self):
        """Nota de crédito solo sobre una factura AUTORIZADA por el SRI."""
        decision = super()._credit_precheck()
        if decision[0] != "proceed" or not self._shrimp_sri_enabled() \
                or not self.invoice_id.l10n_latam_use_documents:
            return decision
        invoice = self.invoice_id
        doc = self._sri_invoice_doc()
        if doc.state == "authorized":
            if invoice.partner_id.commercial_partner_id._ec_sri_identification_code() == "07":
                return "keep", _(
                    "La factura %s se emitió a consumidor final: el SRI no admite nota de "
                    "crédito ni anulación (Res. NAC-DGERCGC25-00000014). Queda vigente; "
                    "la devolución, si corresponde, se gestiona fuera del comprobante."
                ) % invoice.name
            return "proceed", None
        if doc.state in SRI_IN_FLIGHT:
            return "wait", _(
                "La factura %s sigue en el SRI (en cola / esperando autorización): la nota "
                "de crédito se emitirá cuando el SRI la autorice.") % invoice.name
        if doc.state == "error" and doc.was_sent:
            motivo = _(
                "La factura %s quedó «Requiere revisión» en el SRI después de enviarse: "
                "consulte su estado en el SRI (botón «Consultar» del comprobante) antes "
                "de anular el cobro.") % invoice.name
            if self.invoice_error != motivo:
                self._shrimp_escalate(motivo, summary=_("Anulación pendiente: consultar SRI"))
            return "wait", motivo
        estado = dict(doc._fields["state"].selection).get(doc.state) if doc \
            else _("sin comprobante")
        return "void", _("estado SRI: %s") % estado

    @api.model
    def _shrimp_credit_auto_emit(self):
        return self.env["shrimp.settings"].get_bool("shrimp_l10n_ec.credit_auto_emit", True)

    def _after_service_refund_posted(self, refund):
        """La NC (04) va por el mismo camino que la factura: se firma, se
        encola y el cron del SRI la envía y espera la autorización. Si en
        Ajustes se pidió revisión de contabilidad, queda una actividad."""
        res = super()._after_service_refund_posted(refund)
        if not (self._shrimp_sri_enabled() and refund.l10n_latam_use_documents):
            return res
        if self._shrimp_credit_auto_emit() and self.company_id.shrimp_sri_auto_emit:
            # Como en la factura: un fallo no deshace el comprobante (ni su clave).
            _doc, error = refund._ec_sri_emit_keep()
            if error:
                self.write({"invoice_error": str(error)[:2000]})
                self._shrimp_escalate(_(
                    "La nota de crédito %(n)s del cobro %(c)s quedó contabilizada, pero "
                    "no se pudo enviar al SRI: %(e)s") % {
                        "n": refund.name, "c": self.name, "e": str(error)[:500]})
        else:
            self._shrimp_escalate(
                _("La nota de crédito %(n)s del cobro %(c)s está contabilizada y espera "
                  "revisión de contabilidad antes de enviarse al SRI (use «Reintentar» "
                  "en el cobro o «Emitir» en la nota).") % {"n": refund.name, "c": self.name},
                summary=_("Revisar y emitir nota de crédito"))
        return res

    # ------------------------------------------------------------------
    # Respuesta del SRI -> cobro
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_on_sri_state(self, docs):
        """Lo llama ec.sri.document al cambiar de estado. Nunca lanza."""
        reintentar_credito = False
        for doc in docs.sudo():
            move = doc.move_id
            if not move or doc.document_type not in ("01", "04"):
                continue
            campo = "invoice_id" if doc.document_type == "01" else "refund_id"
            charges = self.sudo().search([(campo, "=", move.id)])
            mensaje = doc.last_message or ""
            for charge in charges:
                if doc.state == "authorized":
                    if charge.state == "sri_rejected":
                        charge.write({"state": "invoiced", "invoice_error": False})
                    elif charge.state in ("invoiced", "credited") and charge.invoice_error:
                        charge.write({"invoice_error": False})
                    charge.message_post(body=_("%(d)s autorizado por el SRI (%(n)s).") % {
                        "d": doc.name, "n": doc.authorization_number or ""})
                    charge._shrimp_close_escalations(_("Autorizado por el SRI."))
                    if charge.state == "to_credit":
                        reintentar_credito = True
                elif doc.state in SRI_FAILED:
                    estado = dict(doc._fields["state"].selection).get(doc.state)
                    texto = _("%(d)s: %(e)s por el SRI. %(m)s") % {
                        "d": doc.name, "e": estado, "m": mensaje}
                    vals = {"invoice_error": texto[:2000]}
                    if campo == "invoice_id" and charge.state == "invoiced":
                        # Primer rechazo: los intentos cuentan los reenvíos.
                        vals.update(state="sri_rejected", invoice_attempts=0)
                    charge.write(vals)
                    if charge.state == "to_credit":
                        reintentar_credito = True
                        continue
                    agotado = charge.invoice_attempts >= self._shrimp_max_auto_attempts()
                    charge._shrimp_escalate(
                        texto + " " + (_("Se agotaron los reenvíos automáticos: corrija y use "
                                         "«Reintentar».") if agotado else
                                       _("Corrija los datos (cliente, impuestos, punto) y use "
                                         "«Reintentar» en el cobro; si corrige el cliente, el "
                                         "reenvío es automático.")),
                        summary=_("Comprobante rechazado por el SRI"))
        if reintentar_credito:
            cron = self.env.ref("shrimp_marketplace.ir_cron_shrimp_charge_retry",
                                raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return True

    # ------------------------------------------------------------------
    # Reenvío tras corregir
    # ------------------------------------------------------------------
    def _sri_failed_doc(self):
        """Comprobante del cobro que hay que reenviar (o crear y enviar)."""
        self.ensure_one()
        if self.state == "sri_rejected" and self.invoice_id:
            doc = self._sri_invoice_doc()
            return self.invoice_id, doc
        if self.state == "invoiced" and self.invoice_id.l10n_latam_use_documents:
            doc = self._sri_invoice_doc()
            if not doc or doc.state in SRI_FAILED + ("draft",):
                return self.invoice_id, doc
        if self.state == "credited" and self.refund_id \
                and self.refund_id.l10n_latam_use_documents:
            doc = self._sri_refund_doc()
            if not doc or doc.state in SRI_FAILED + ("draft",):
                return self.refund_id, doc
        return False, False

    def _sri_resend(self, move, doc):
        """Corrige (si hace falta) y vuelve a encolar el comprobante.

        Devuelve el motivo si no se pudo preparar o firmar (el comprobante y su
        clave de acceso se conservan, ver _ec_sri_emit_keep) o False."""
        self.payer_partner_id.commercial_partner_id._shrimp_sync_identification_type()
        if not doc:
            return move._ec_sri_emit_keep()[1]
        doc = doc.sudo()
        if doc.state == "rejected":
            doc.action_correct()
        if doc.state == "error":
            if doc.signed_xml:
                doc.action_resume()
                self.env.ref("l10n_ec_sri_community.ir_cron_sri_queue").sudo()._trigger()
                return False
        if doc.state == "draft":
            company = self.company_id
            if company.shrimp_service_payment_id and move.move_type == "out_invoice" \
                    and not move.l10n_ec_sri_payment_id:
                move.write({"l10n_ec_sri_payment_id": company.shrimp_service_payment_id.id})
            return move._ec_sri_emit_keep()[1]
        return False

    def _retry_fiscal(self, manual=False):
        res = super()._retry_fiscal(manual=manual)
        maximo = self._shrimp_max_auto_attempts()
        for charge in self.sudo():
            if not charge._shrimp_sri_enabled():
                continue
            move, doc = charge._sri_failed_doc()
            if not move:
                continue
            if doc and doc.state in SRI_IN_FLIGHT + ("authorized",):
                continue
            if not manual and charge.invoice_attempts >= maximo:
                continue
            problemas = charge.company_id._shrimp_sri_config_problems()
            if problemas:
                charge.write({"invoice_error": _("Facturación electrónica sin configurar: %s")
                              % " ".join(problemas)})
                continue
            intentos = charge.invoice_attempts + 1
            ahora = fields.Datetime.now()
            try:
                with self.env.cr.savepoint():
                    e = charge._sri_resend(move, doc)
            except Exception as exc:  # noqa: BLE001
                e = exc
            if e:
                _logger.warning("No se pudo reenviar al SRI el cobro %s: %s", charge.name, e)
                charge.write({"invoice_error": _("Reenvío al SRI fallido: %s") % str(e)[:1900],
                              "invoice_attempts": intentos, "last_attempt_at": ahora})
                if intentos >= maximo or manual:
                    charge._shrimp_escalate(_(
                        "No se pudo reenviar al SRI %(m)s del cobro %(c)s (intento %(n)s): "
                        "%(e)s") % {"m": move.name, "c": charge.name, "n": intentos,
                                    "e": str(e)[:500]},
                        summary=_("Comprobante rechazado por el SRI"))
                continue
            charge.write({"invoice_attempts": intentos, "last_attempt_at": ahora,
                          "invoice_error": _("%s reenviado al SRI; esperando su respuesta.")
                          % move.name})
            charge.message_post(body=_("%(m)s reenviado al SRI (intento %(n)s).") % {
                "m": move.name, "n": intentos})
        return res

    @api.model
    def _cron_retry_fiscal(self):
        """Reenvía solos los comprobantes rechazados cuya causa probable ya se
        corrigió: el cliente se modificó después del último intento, o el
        comprobante quedó en «Requiere revisión» por fallos de conexión. El
        resto espera al botón «Reintentar» (reenviar lo mismo daría el mismo
        rechazo)."""
        res = super()._cron_retry_fiscal()
        maximo = self._shrimp_max_auto_attempts()
        candidatos = self.sudo().search([
            ("state", "in", ("sri_rejected", "credited")),
            ("invoice_attempts", "<", maximo),
        ], limit=200, order="last_attempt_at asc nulls first, id asc")
        listos = self.browse()
        for charge in candidatos:
            move, doc = charge._sri_failed_doc()
            if not move:
                continue
            corregido = not charge.last_attempt_at or (
                charge.payer_partner_id.commercial_partner_id.write_date
                and charge.payer_partner_id.commercial_partner_id.write_date
                > charge.last_attempt_at)
            conexion = doc and doc.state == "error" and doc.signed_xml \
                and "Límite de intentos" in (doc.last_message or "")
            sin_enviar = not doc or doc.state == "draft"
            if corregido or conexion or sin_enviar:
                listos |= charge
        listos._retry_fiscal()
        return res

    # ------------------------------------------------------------------
    # Backlog y resumen diario
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_sri_backlog_domain(self, company):
        """Backlog: cobros sin factura que ya agotaron los intentos automáticos
        (el cron no los vuelve a tomar)."""
        return [("company_id", "=", company.id), ("state", "in", ("pending", "error")),
                ("invoice_id", "=", False),
                ("invoice_attempts", ">=", self._shrimp_max_auto_attempts()),
                # Los cobros de los datos de demostración no se facturan nunca
                # (scripts/demo_masivo/comun.py: CHG_DEMO).
                ("invoice_error", "not like", DEMO_MARK)]

    @api.model
    def _shrimp_sri_requeue_backlog(self, company):
        """Vuelve a poner en la cola los cobros pendientes o con error que ya
        agotaron sus intentos (backlog), SOLO con la configuración completa.
        No factura nada aquí: el cron horario los toma de 200 en 200, los más
        antiguos primero."""
        from odoo.exceptions import UserError
        problemas = company._shrimp_sri_config_problems()
        if problemas:
            raise UserError(_("Complete primero la configuración de la facturación "
                              "electrónica:\n- %s") % "\n- ".join(problemas))
        charges = self.sudo().search(self._shrimp_sri_backlog_domain(company))
        anular = charges.filtered(lambda c: c._shrimp_backlog_should_cancel())
        if anular:
            anular.action_cancel_charge(_(
                "Backlog: compra cancelada sin verificación realizada; el honorario no "
                "se factura."))
            charges -= anular
        if charges:
            charges.write({"state": "pending", "invoice_attempts": 0,
                           "invoice_error": False, "last_attempt_at": False})
            cron = self.env.ref("shrimp_marketplace.ir_cron_shrimp_charge_retry",
                                raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
            _logger.info("Cola SRI: %s cobros reencolados por %s", len(charges),
                         self.env.user.login)
        return len(charges)

    @api.model
    def _shrimp_sri_queue_summary(self):
        """Conteo por situación (solo lo que no está cerrado)."""
        maximo = self._shrimp_max_auto_attempts()
        ids = self._sri_queue_ids(lambda c: True)
        resumen = {}
        antiguo = {}
        for charge in self.sudo().browse(ids):
            if (charge.invoice_error or "").startswith(DEMO_MARK):
                continue
            status = charge._sri_status_of(maximo)
            if status == "ok":
                continue
            resumen[status] = resumen.get(status, 0) + 1
            fecha = charge.date or charge.create_date
            if fecha and (status not in antiguo or fecha < antiguo[status]):
                antiguo[status] = fecha
        return resumen, antiguo

    @api.model
    def _cron_sri_daily_summary(self):
        """Correo diario a los administradores de CamaronMarket con el estado
        de la cola SRI (solo si hay algo pendiente o la configuración está
        incompleta). Ajustes › CamaronMarket › «Resumen diario de la cola SRI»."""
        if not self.env["shrimp.settings"].get_bool("shrimp_l10n_ec.daily_summary", True):
            return False
        resumen, antiguo = self._shrimp_sri_queue_summary()
        companies = self.env["res.company"].sudo().search([("ec_sri_enabled", "=", True)])
        problemas = {c: c._shrimp_sri_config_problems() for c in companies}
        problemas = {c: p for c, p in problemas.items() if p}
        if not resumen and not problemas:
            return False
        grupo = self.env.ref("shrimp_marketplace.group_marketplace_manager",
                             raise_if_not_found=False)
        usuarios = grupo.sudo().all_user_ids.filtered(
            lambda u: u.active and u._is_internal() and u.email and u.id != 1) if grupo \
            else self.env["res.users"]
        if not usuarios:
            return False
        etiquetas = dict(QUEUE_STATUS)
        filas = Markup("").join(
            Markup("<tr><td style='padding:2px 12px 2px 0'>%s</td>"
                   "<td style='text-align:right'><b>%s</b></td>"
                   "<td style='padding-left:12px;color:#666'>%s</td></tr>") % (
                etiquetas[k], resumen[k],
                fields.Date.to_string(antiguo[k].date()) if antiguo.get(k) else "")
            for k, _l in QUEUE_STATUS if resumen.get(k))
        cuerpo = Markup("<p>%s</p>") % _("Estado de la cola de facturación electrónica (SRI) "
                                         "de CamaronMarket:")
        if filas:
            cuerpo += Markup("<table><tr><th style='text-align:left'>%s</th><th>%s</th>"
                             "<th style='padding-left:12px'>%s</th></tr>%s</table>") % (
                _("Situación"), _("Cobros"), _("Más antiguo"), filas)
        for company, lista in problemas.items():
            cuerpo += Markup("<p><b>%s</b></p><ul>%s</ul>") % (
                _("Configuración incompleta en %s (nada se factura hasta completarla):")
                % company.name,
                Markup("").join(Markup("<li>%s</li>") % escape(p) for p in lista))
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        accion = self.env.ref("shrimp_l10n_ec.action_shrimp_sri_queue", raise_if_not_found=False)
        if accion and base:
            cuerpo += Markup("<p><a href='%s/odoo/action-%s'>%s</a></p>") % (
                base, accion.id, _("Abrir la Cola SRI"))
        self.env["mail.mail"].sudo().create({
            "subject": _("CamaronMarket · Cola SRI: %s cobros requieren seguimiento")
            % sum(resumen.values()),
            "body_html": cuerpo,
            "email_to": ",".join(usuarios.mapped("email_formatted")),
            "auto_delete": True,
        })
        return True
