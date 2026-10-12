"""Puente entre los cobros de la plataforma y el SRI (l10n_ec_sri_community).

Cómo emite el módulo SRI una factura (y por qué este puente hace lo que hace)
=============================================================================
* El NÚMERO de la factura (001-003-000000007) lo pone la numeración estándar
  de Odoo/l10n_ec al contabilizar: sale del diario de ventas
  (``l10n_ec_entity`` = establecimiento, ``l10n_ec_emission`` = punto) y del
  tipo de documento latam (01 factura, 04 nota de crédito). El secuencial de
  ``ec.sri.point`` NO se usa para facturas ni notas (solo en guías y
  retenciones).
* El comprobante es un ``ec.sri.document`` (borrador) atado a la factura
  contabilizada (``move_id``) y a un ``ec.sri.point`` del MISMO tipo de
  documento, del MISMO ambiente que la compañía y con establecimiento y punto
  IGUALES a los del número de la factura; si no, ``action_queue`` lo rechaza.
* ``action_queue`` valida la compañía (``_ec_sri_company_data``: RUC de 13
  dígitos, dirección matriz...), arma el XML desde la factura, lo FIRMA con el
  certificado del servidor y lo deja «En cola»; el cron del módulo SRI lo
  envía y consulta la autorización.

Qué hace el puente
==================
1. Antes de contabilizar (``_prepare_service_invoice``): elige diario y punto
   de emisión, pone el documento 01 y la forma de pago, y valida todo lo que
   el SRI va a exigir. Si falta algo, lanza: la factura NO se contabiliza (no
   se gasta un número fiscal) y el cobro queda en «Error» con el motivo.
2. Después de contabilizar (``_after_service_invoice_posted``): crea el
   comprobante en BORRADOR y despierta el cron de este módulo. En la petición
   del usuario no se firma ni se envía nada.
3. El cron ``_cron_sri_queue_service_documents`` firma y encola
   (``action_queue``) los borradores de los cobros; si falla (p. ej. sin
   certificado) lo anota en el historial del comprobante y reintenta.
4. La anulación sale como nota de crédito 04 con el mismo circuito.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Tipo de documento SRI -> xmlid del tipo de documento latam de l10n_ec.
_LATAM_DOC = {"01": "l10n_ec.ec_dt_01", "04": "l10n_ec.ec_dt_04"}
_DOC_NAME = {"01": "Factura (01)", "04": "Nota de crédito (04)"}
_ENV_NAME = {"1": "Pruebas", "2": "Producción"}


def _sri_error(message):
    return UserError(_("Factura electrónica (SRI): %s", message))


class ShrimpCharge(models.Model):
    _inherit = "shrimp.charge"

    sri_document_id = fields.Many2one(
        "ec.sri.document", string="Comprobante SRI",
        compute="_compute_sri_document", groups="account.group_account_invoice",
        help="Comprobante electrónico de la factura del servicio.")
    sri_document_state = fields.Selection(
        selection=lambda self: self.env["ec.sri.document"]._fields["state"].selection,
        string="Estado SRI", compute="_compute_sri_document",
        groups="account.group_account_invoice")
    sri_refund_document_id = fields.Many2one(
        "ec.sri.document", string="Comprobante SRI de la nota de crédito",
        compute="_compute_sri_document", groups="account.group_account_invoice")

    @api.depends("invoice_id.ec_sri_document_ids.state", "refund_id.ec_sri_document_ids")
    def _compute_sri_document(self):
        for charge in self:
            charge.sri_document_id = charge.invoice_id.ec_sri_document_ids.filtered(
                lambda d: d.document_type == "01")[:1]
            charge.sri_document_state = charge.sri_document_id.state or False
            charge.sri_refund_document_id = charge.refund_id.ec_sri_document_ids.filtered(
                lambda d: d.document_type == "04")[:1]

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------
    def _shrimp_sri_enabled(self):
        self.ensure_one()
        return bool(self.company_id.ec_sri_enabled)

    @api.model
    def _shrimp_sri_find_point(self, company, establishment, emission, code):
        """El punto de emisión (único por compañía, establecimiento, punto,
        tipo de documento y ambiente) que corresponde a un número fiscal."""
        if not establishment or not emission:
            return self.env["ec.sri.point"]
        return self.env["ec.sri.point"].sudo().search([
            ("company_id", "=", company.id),
            ("establishment", "=", establishment),
            ("emission", "=", emission),
            ("document_type", "=", code),
            ("environment", "=", company.ec_sri_environment),
        ], limit=1)

    @api.model
    def _shrimp_sri_point_missing(self, company, journal, code):
        return _sri_error(_(
            "falta el punto de emisión de %(doc)s %(est)s-%(emi)s en ambiente "
            "%(amb)s de la compañía %(cia)s (diario «%(diario)s»). Créelo en "
            "Ecuador SRI › Establecimientos › Puntos de emisión.",
            doc=_DOC_NAME[code], est=journal.l10n_ec_entity or "???",
            emi=journal.l10n_ec_emission or "???",
            amb=_ENV_NAME.get(company.ec_sri_environment, company.ec_sri_environment),
            cia=company.name, diario=journal.display_name))

    @api.model
    def _shrimp_sri_check_journal(self, journal):
        if not journal.l10n_latam_use_documents:
            raise _sri_error(_(
                "el diario «%s» no usa documentos (Usar documentos en el diario).",
                journal.display_name))
        if not journal.l10n_ec_entity or not journal.l10n_ec_emission:
            raise _sri_error(_(
                "el diario «%s» no tiene establecimiento y punto de emisión "
                "(001-001) configurados.", journal.display_name))

    def _shrimp_sri_journal_and_point(self, invoice):
        """Diario de la factura de servicio y su punto de emisión (Factura 01).

        * Con punto configurado en la compañía: se usa ese punto y el diario de
          ventas que numera con su mismo establecimiento y punto (el
          configurado, o el primero que coincida).
        * Sin punto configurado: el diario (configurado o el de la factura) y
          el punto cuyo establecimiento y punto son los del diario.
        """
        self.ensure_one()
        company = self.company_id
        point = company.shrimp_service_sri_point_id.sudo()
        journal = company.shrimp_service_journal_id
        if point:
            if point.company_id != company:
                raise _sri_error(_("el punto de emisión «%s» es de otra compañía.",
                                   point.display_name))
            if not point.active:
                raise _sri_error(_("el punto de emisión «%s» está archivado.",
                                   point.display_name))
            if point.document_type != "01":
                raise _sri_error(_(
                    "el punto de emisión «%s» configurado para la plataforma no es "
                    "de Factura (01).", point.display_name))
            if point.environment != company.ec_sri_environment:
                raise _sri_error(_(
                    "el punto de emisión «%(p)s» es del ambiente %(pa)s y la "
                    "compañía emite en %(ca)s.", p=point.display_name,
                    pa=_ENV_NAME.get(point.environment, point.environment),
                    ca=_ENV_NAME.get(company.ec_sri_environment, company.ec_sri_environment)))
            numeracion = (point.establishment, point.emission)
            if journal:
                if (journal.l10n_ec_entity, journal.l10n_ec_emission) != numeracion:
                    raise _sri_error(_(
                        "el diario «%(d)s» numera %(de)s-%(dp)s y el punto de emisión "
                        "«%(p)s» es %(pe)s-%(pp)s. Tienen que coincidir.",
                        d=journal.display_name, de=journal.l10n_ec_entity or "???",
                        dp=journal.l10n_ec_emission or "???", p=point.display_name,
                        pe=point.establishment, pp=point.emission))
            elif (invoice.journal_id.l10n_ec_entity, invoice.journal_id.l10n_ec_emission) == numeracion \
                    and invoice.journal_id.l10n_latam_use_documents:
                journal = invoice.journal_id
            else:
                journal = self.env["account.journal"].sudo().search([
                    ("company_id", "=", company.id), ("type", "=", "sale"),
                    ("l10n_latam_use_documents", "=", True),
                    ("l10n_ec_entity", "=", point.establishment),
                    ("l10n_ec_emission", "=", point.emission),
                ], limit=1)
                if not journal:
                    raise _sri_error(_(
                        "no hay un diario de ventas con documentos que numere "
                        "%(e)s-%(p)s para el punto de emisión «%(n)s». Créelo o "
                        "elija el diario en Ajustes › CamaronMarket.",
                        e=point.establishment, p=point.emission, n=point.display_name))
            self._shrimp_sri_check_journal(journal)
            return journal, point
        journal = journal or invoice.journal_id
        self._shrimp_sri_check_journal(journal)
        point = self._shrimp_sri_find_point(
            company, journal.l10n_ec_entity, journal.l10n_ec_emission, "01")
        if not point:
            raise self._shrimp_sri_point_missing(company, journal, "01")
        return journal, point

    def _shrimp_sri_sync_partner(self, partner):
        partner = partner.sudo()
        (partner | partner.commercial_partner_id)._shrimp_sync_identification_type()

    @api.model
    def _shrimp_sri_validate(self, move):
        """Lo que el módulo SRI exigirá al firmar, comprobado ANTES de
        contabilizar para no gastar un número fiscal en una factura que no se
        podrá emitir. Traduce los errores del módulo SRI a un mensaje del
        cobro."""
        try:
            move.company_id._ec_sri_company_data()
            move.partner_id._ec_sri_partner_data()
            move._ec_sri_lines()
        except UserError as e:
            raise _sri_error(e.args[0]) from e
        if not move.l10n_ec_sri_payment_id:
            raise _sri_error(_("falta la forma de pago SRI (Ajustes › CamaronMarket)."))

    @api.model
    def _shrimp_sri_clean_lines(self, move):
        """Descripción de cada línea en UNA línea y hasta 300 caracteres: el
        XSD del SRI (``descripcion``, patrón ``[^\\n]*``) rechaza saltos de
        línea, y la línea que genera Ventas es «[CÓDIGO] Producto\\nconcepto».
        Solo lo detectaría la validación al firmar, con el número ya gastado."""
        for line in move.invoice_line_ids.filtered(lambda l: l.display_type == "product"):
            name = " ".join((line.name or "").split())[:300]
            if name != line.name:
                line.name = name

    @api.model
    def _shrimp_sri_set_document_type(self, move, code):
        doc_type = self.env.ref(_LATAM_DOC[code])
        if move.l10n_latam_document_type_id != doc_type:
            if doc_type not in move.l10n_latam_available_document_type_ids:
                raise _sri_error(_(
                    "%(doc)s no está permitida para %(p)s con su tipo de "
                    "identificación (%(t)s).", doc=_DOC_NAME[code],
                    p=move.partner_id.display_name,
                    t=move.partner_id.l10n_latam_identification_type_id.name or _("sin tipo")))
            move.l10n_latam_document_type_id = doc_type

    # ------------------------------------------------------------------
    # Factura de servicio
    # ------------------------------------------------------------------
    def _prepare_service_invoice(self, invoice):
        """Deja la factura de servicio lista para el SRI antes de contabilizar."""
        invoice = super()._prepare_service_invoice(invoice)
        if not self._shrimp_sri_enabled():
            return invoice
        company = self.company_id
        try:
            company._ec_sri_company_data()
        except UserError as e:
            raise _sri_error(e.args[0]) from e
        self._shrimp_sri_sync_partner(self.payer_partner_id)
        journal, _point = self._shrimp_sri_journal_and_point(invoice)
        vals = {}
        if invoice.journal_id != journal:
            vals["journal_id"] = journal.id
        if company.shrimp_service_payment_id:
            vals["l10n_ec_sri_payment_id"] = company.shrimp_service_payment_id.id
        if vals:
            invoice.write(vals)
        self._shrimp_sri_set_document_type(invoice, "01")
        self._shrimp_sri_clean_lines(invoice)
        self._shrimp_sri_validate(invoice)
        return invoice

    def _shrimp_sri_auto_emit(self):
        self.ensure_one()
        return self._shrimp_sri_enabled() and self.company_id.shrimp_sri_auto_emit

    def _shrimp_sri_create_document(self, move, code, reason=False):
        """Comprobante en BORRADOR para la factura/nota contabilizada.

        No firma ni envía: lo encola el cron de este módulo, fuera de la
        petición del usuario. Idempotente."""
        self.ensure_one()
        existing = move.ec_sri_document_ids.filtered(lambda d: d.document_type == code)[:1]
        if existing:
            return existing
        establishment, emission, _seq = move._ec_sri_number_parts()
        point = self._shrimp_sri_find_point(move.company_id, establishment, emission, code)
        if not point:
            raise self._shrimp_sri_point_missing(move.company_id, move.journal_id, code)
        document = self.env["ec.sri.document"].sudo().with_company(move.company_id).create({
            "company_id": move.company_id.id,
            "partner_id": move.partner_id.id,
            "document_type": code,
            "point_id": point.id,
            "date": move.invoice_date,
            "move_id": move.id,
            "mode": "native",
            "reason": reason or False,
        })
        cron = self.env.ref("shrimp_l10n_ec.ir_cron_shrimp_sri_queue", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return document

    def _after_service_invoice_posted(self, invoice):
        res = super()._after_service_invoice_posted(invoice)
        if self._shrimp_sri_auto_emit() and invoice.l10n_latam_use_documents:
            self._shrimp_sri_create_document(invoice, "01")
        return res

    # ------------------------------------------------------------------
    # Nota de crédito
    # ------------------------------------------------------------------
    def _prepare_refund_vals(self, reason):
        """La nota de crédito del SRI es el documento 04 y lleva el motivo."""
        vals = super()._prepare_refund_vals(reason)
        if self._shrimp_sri_enabled() and self.invoice_id.l10n_latam_use_documents:
            vals["l10n_latam_document_type_id"] = self.env.ref(_LATAM_DOC["04"]).id
            vals["ec_sri_reason"] = (reason or _("Anulación del servicio"))[:300]
        return vals

    def _prepare_service_refund(self, refund, reason):
        """Valida la nota de crédito antes de contabilizarla: punto de emisión
        04 con el establecimiento y punto del diario, y los datos del SRI."""
        refund = super()._prepare_service_refund(refund, reason)
        if not self._shrimp_sri_enabled() or not refund.l10n_latam_use_documents:
            return refund
        if not refund.ec_sri_reason:
            refund.ec_sri_reason = (reason or _("Anulación del servicio"))[:300]
        self._shrimp_sri_set_document_type(refund, "04")
        journal = refund.journal_id
        self._shrimp_sri_check_journal(journal)
        if not self._shrimp_sri_find_point(
                refund.company_id, journal.l10n_ec_entity, journal.l10n_ec_emission, "04"):
            raise self._shrimp_sri_point_missing(refund.company_id, journal, "04")
        self._shrimp_sri_clean_lines(refund)
        self._shrimp_sri_validate(refund)
        return refund

    def _try_credit(self):
        to_credit = self.sudo().filtered(lambda c: c.state == "to_credit")
        res = super()._try_credit()
        for charge in to_credit.filtered(lambda c: c.state == "credited" and c.refund_id):
            refund = charge.refund_id
            if not charge._shrimp_sri_auto_emit() or not refund.l10n_latam_use_documents \
                    or refund.state != "posted":
                continue
            try:
                with self.env.cr.savepoint():
                    charge._shrimp_sri_create_document(refund, "04", refund.ec_sri_reason)
            except Exception as e:  # noqa: BLE001 - la nota ya está contabilizada
                _logger.warning("Cobro %s: no se pudo crear el comprobante SRI de la "
                                "nota %s: %s", charge.name, refund.name, e)
                charge.message_post(body=_(
                    "La nota de crédito %(f)s quedó contabilizada, pero no se pudo "
                    "crear su comprobante electrónico: %(e)s", f=refund.name, e=e))
        return res

    # ------------------------------------------------------------------
    # Cron: firmar y encolar fuera de la petición del usuario
    # ------------------------------------------------------------------
    @api.model
    def _cron_sri_queue_service_documents(self, limit=50):
        """Firma y encola (``action_queue``) los comprobantes en borrador de
        las facturas y notas de crédito de los cobros. El envío al SRI lo hace
        después el cron del módulo SRI.

        Solo toca borradores nunca numerados (sin clave de acceso): uno
        corregido tras un rechazo lo vuelve a encolar una persona."""
        Document = self.env["ec.sri.document"].sudo()
        docs = Document.search([
            ("state", "=", "draft"), ("mode", "=", "native"),
            ("document_type", "in", ("01", "04")), ("access_key", "=", False),
            ("move_id", "!=", False),
        ], order="id", limit=limit * 4)
        docs = docs.filtered(lambda d: d.company_id.ec_sri_enabled
                             and d.company_id.shrimp_sri_auto_emit)
        if not docs:
            return True
        moves = docs.move_id
        charges = self.sudo().search(["|", ("invoice_id", "in", moves.ids),
                                      ("refund_id", "in", moves.ids)])
        own = set((charges.invoice_id | charges.refund_id).ids)
        for doc in docs.filtered(lambda d: d.move_id.id in own)[:limit]:
            try:
                with self.env.cr.savepoint():
                    doc.action_queue()
            except Exception as e:  # noqa: BLE001 - que un comprobante no frene a los demás
                message = _("No se pudo firmar y encolar: %s",
                            e.args[0] if isinstance(e, UserError) and e.args else e)
                _logger.warning("SRI: comprobante %s de %s: %s", doc.id,
                                doc.move_id.name, message)
                last = doc.event_ids.filtered(lambda ev: ev.operation == "shrimp_queue")[:1]
                if last.description != message:
                    doc._event("shrimp_queue", message)
        return True
