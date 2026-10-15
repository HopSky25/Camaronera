"""Bandeja de avisos de la app móvil (docs/app/CONTRATO-MEJORAS-APP.md, C3).

Sin Firebase: la app consulta GET /me/notifications?unread=true cada ~15 min
y muestra una notificación local. Los avisos nacen del MISMO punto que los
webhooks (shrimp.webhook.delivery._emit, al que llaman todos los eventos de
models/events.py) y pasan la MISMA regla de audiencia del evento
(_audience_allows: audiencia + has_access del usuario). Lo único propio de
aquí es a quién de la audiencia le interesa cada evento y con qué texto.
"""

import logging
from datetime import timedelta

import pytz

from odoo import api, fields, models

from ..controllers.framework import iso_dt
from .webhook import EVENTS

_logger = logging.getLogger(__name__)

RETENTION_DAYS = 60
DEFAULT_TZ = "America/Guayaquil"
# Horas antes del vencimiento del plazo de aceptación en que se avisa.
DUE_SOON_HOURS = 12


def _name(rec):
    return (rec.sudo().name or "").strip() if rec else ""


def _local(dt, partner=None):
    """dd/mm/aaaa HH:MM en la zona del socio (Ecuador por defecto)."""
    if not dt:
        return ""
    tz = pytz.timezone((partner and partner.tz) or DEFAULT_TZ)
    return pytz.UTC.localize(fields.Datetime.to_datetime(dt)).astimezone(tz).strftime("%d/%m/%Y %H:%M")


def _tx_start(env):
    """Inicio de la transacción de base de datos (create_date de lo creado en
    ella), al segundo: así se distingue «nació en esta operación»."""
    return env.cr.now().replace(microsecond=0)


def _qty(env, value, uom):
    return env["shrimp.transaction"].shrimp_fmt_qty(value, uom)


def _item(partner, role, kind, title, body, target_type, target_id, link):
    return {"partner": partner, "role": role or False, "kind": kind, "title": title[:120],
            "body": (body or "")[:500], "target_type": target_type, "target_uuid": target_id,
            "link": link}


# ---------------------------------------------------------------------------
# Qué aviso deja cada evento y a quién (dentro de la audiencia del evento)
# ---------------------------------------------------------------------------
def _ver_link_party(v):
    return "/marketplace/verifications/%s/acceptance" % v.uuid_ref


def _ver_link_verifier(v):
    return "/verifier/verifications/%s" % v.uuid_ref


def _ver_roles(v):
    tx = v.transaction_id
    return tx.buyer_role or False, tx.seller_role or False


def _b_verification_assigned(env, v):
    """Al técnico cuando se le asigna; a la verificadora cuando le llega
    (al crearse la verificación, todavía sin técnico)."""
    product = _name(v.product_id)
    if v.technician_partner_id:
        return [_item(v.technician_partner_id, "verificador", "verification_assigned",
                      "Verificación asignada",
                      "Te asignaron %s: %s de %s." % (_name(v), product, _name(v.seller_partner_id)),
                      "verification", v.uuid_ref, _ver_link_verifier(v))]
    if v.verifier_partner_id:
        return [_item(v.verifier_partner_id, "verificador", "verification_assigned",
                      "Nueva verificación",
                      "%s contrató %s: %s. Asigna un técnico." % (_name(v.buyer_partner_id),
                                                                   _name(v), product),
                      "verification", v.uuid_ref, _ver_link_verifier(v))]
    return []


def _b_verdict_issued(env, v):
    buyer_role, seller_role = _ver_roles(v)
    parties = [(v.buyer_partner_id, buyer_role), (v.seller_partner_id, seller_role)]
    if v.state == "rejected":
        return [_item(p, role, "verification_rejected", "Verificación rechazada",
                      "%s: el lote %s no pasó la verificación y la compra se cancela."
                      % (_name(v), _name(v.product_id)),
                      "verification", v.uuid_ref, _ver_link_party(v)) for p, role in parties]
    return [_item(p, role, "report_ready", "Informe listo para aceptar",
                  "El informe de %s (%s) espera tu respuesta." % (_name(v), _name(v.product_id)),
                  "verification", v.uuid_ref, _ver_link_party(v)) for p, role in parties]


def _b_declared_submitted(env, v):
    """Solo a la parte que tiene que confirmarlo (el declarante ya lo firmó)."""
    buyer_role, seller_role = _ver_roles(v)
    if v.declarant_role == "buyer":
        other, role = v.seller_partner_id, seller_role
    else:
        other, role = v.buyer_partner_id, buyer_role
    return [_item(other, role, "report_ready", "Informe declarado por aceptar",
                  "%s presentó el informe de %s (%s). Revísalo y responde."
                  % (_name(v.declarant_partner_id) or "La otra parte", _name(v), _name(v.product_id)),
                  "verification", v.uuid_ref, _ver_link_party(v))]


def _b_acceptance_due(env, v):
    """A las partes que todavía no respondieron."""
    buyer_role, seller_role = _ver_roles(v)
    out = []
    for postura in v.acceptance_ids.filtered(lambda a: a.decision == "pending"):
        partner, role = ((v.buyer_partner_id, buyer_role) if postura.role == "buyer"
                         else (v.seller_partner_id, seller_role))
        out.append(_item(partner, role, "acceptance_due", "Tu aceptación vence pronto",
                         "Responde al informe de %s antes del %s; si no, se dará por aceptado."
                         % (_name(v), _local(v.acceptance_deadline, partner)),
                         "verification", v.uuid_ref, _ver_link_party(v)))
    return out


def _b_transaction(env, tx):
    """Compra recibida (vendedor) al nacer la compra; compra confirmada (las
    dos partes) al confirmarse. En la compra directa las dos cosas pasan en
    la misma operación: el vendedor recibe solo «Nueva compra»."""
    product = _name(tx.product_id)
    qty = _qty(env, tx.transaction_qty, tx.product_id.uom_id)
    sale_link = "/marketplace/sales/%s" % tx.uuid_ref
    buy_link = "/marketplace/purchases/%s" % tx.uuid_ref
    if tx.state in ("draft", "pending_verification") and tx.create_date \
            and tx.create_date >= _tx_start(env):
        extra = " (con verificación)" if tx.state == "pending_verification" else ""
        return [_item(tx.seller_partner_id, tx.seller_role, "purchase_received", "Nueva compra",
                      "%s compró %s de %s%s." % (_name(tx.buyer_partner_id), qty, product, extra),
                      "transaction", tx.uuid_ref, sale_link)]
    if tx.state == "confirmed":
        out = [_item(tx.buyer_partner_id, tx.buyer_role, "purchase_confirmed", "Compra confirmada",
                     "Tu compra de %s de %s (%s) está confirmada." % (qty, product, _name(tx)),
                     "transaction", tx.uuid_ref, buy_link)]
        recien = env["shrimp.app.notification"].sudo().search_count([
            ("partner_id", "=", tx.seller_partner_id.id), ("kind", "=", "purchase_received"),
            ("target_uuid", "=", tx.uuid_ref), ("create_date", ">=", _tx_start(env))], limit=1)
        if not recien:
            out.append(_item(tx.seller_partner_id, tx.seller_role, "purchase_confirmed",
                             "Venta confirmada",
                             "La venta de %s de %s a %s está confirmada."
                             % (qty, product, _name(tx.buyer_partner_id)),
                             "transaction", tx.uuid_ref, sale_link))
        return out
    return []


def _dispatch_parties(d):
    """(comprador, verificadora, técnico) con su perfil y su enlace."""
    tx = d.transaction_id
    # La pantalla web del despacho se abre por la compra (/marketplace/dispatch/<compra>).
    dsp_link = "/marketplace/dispatch/%s" % tx.uuid_ref
    ver = d.verification_id
    ver_link = _ver_link_verifier(ver) if ver else dsp_link
    out = [(d.buyer_partner_id, tx.buyer_role, dsp_link)]
    for p in (d.verifier_partner_id, d.technician_partner_id):
        if p:
            out.append((p, "verificador", ver_link))
    return out


def _b_dispatch_eta(env, d):
    Notif = env["shrimp.app.notification"].sudo()
    out = []
    for partner, role, link in _dispatch_parties(d):
        previo = Notif.search_count([("partner_id", "=", partner.id),
                                     ("kind", "in", ("dispatch_scheduled", "dispatch_eta_changed")),
                                     ("target_uuid", "=", d.uuid_ref)], limit=1)
        kind, title = (("dispatch_eta_changed", "Cambió la llegada estimada") if previo
                       else ("dispatch_scheduled", "Despacho programado"))
        out.append(_item(partner, role, kind, title,
                         "%s: llegada estimada a planta el %s." % (_name(d.product_id),
                                                                   _local(d.eta, partner)),
                         "dispatch", d.uuid_ref, link))
    return out


def _b_dispatch_arrived(env, d):
    tx = d.transaction_id
    return [_item(d.buyer_partner_id, tx.buyer_role, "truck_arrived", "Llegó el camión",
                  "El camión con %s llegó a planta el %s." % (_name(d.product_id),
                                                              _local(d.actual_arrival, d.buyer_partner_id)),
                  "dispatch", d.uuid_ref, "/marketplace/dispatch/%s" % tx.uuid_ref)]


def _b_copack_request_published(env, req):
    """A los maquiladores que la pueden ver: el de destino si va dirigida o
    todos los que pueden actuar como maquilador si está abierta (la regla
    de audiencia y la ir.rule de la bandeja filtran después)."""
    if req.copacker_partner_id:
        candidatos = req.copacker_partner_id
    else:
        Partner = env["res.partner"].sudo()
        candidatos = Partner.search(Partner._shrimp_role_domain("maquilador") + [
            ("id", "!=", req.client_partner_id.id), ("user_ids.share", "=", True)])
    lb = _qty(env, req.quantity_lb, "lb")
    body = "%s necesita empacar %s del %s al %s." % (
        _name(req.client_partner_id), lb, req.needed_from.strftime("%d/%m") if req.needed_from else "",
        req.needed_to.strftime("%d/%m") if req.needed_to else "")
    return [_item(p, "maquilador", "copack_request_published", "Nueva solicitud de empaque", body,
                  "copack_request", req.uuid_ref, "/copacker/requests/%s" % req.uuid_ref)
            for p in candidatos]


def _b_copack_order(env, order):
    """Oferta aceptada: la orden nace adjudicada (confirmed) desde una oferta."""
    if order.state != "confirmed" or not order.offer_id or order.self_packing:
        return []
    lb = _qty(env, order.agreed_qty_lb, "lb")
    return [_item(order.copacker_partner_id, "maquilador", "copack_offer_accepted",
                  "Oferta de empaque aceptada",
                  "%s aceptó tu oferta: %s (%s)." % (_name(order.client_partner_id), lb, _name(order)),
                  "copack_order", order.uuid_ref, "/copacker/orders/%s" % order.uuid_ref)]


BUILDERS = {
    "verification.assigned": _b_verification_assigned,
    "verification.verdict_issued": _b_verdict_issued,
    "verification.declared_submitted": _b_declared_submitted,
    "verification.acceptance_due": _b_acceptance_due,
    "transaction.state_changed": _b_transaction,
    "dispatch.eta_changed": _b_dispatch_eta,
    "dispatch.arrived": _b_dispatch_arrived,
    "copack.request_published": _b_copack_request_published,
    "copack.order_state_changed": _b_copack_order,
}


class ShrimpAppNotification(models.Model):
    _name = "shrimp.app.notification"
    _description = "Aviso de la app móvil"
    _inherit = ["shrimp.uuid.mixin"]
    _order = "id desc"

    partner_id = fields.Many2one(
        "res.partner", string="Destinatario", required=True, index=True, ondelete="cascade",
        help="Socio (cuenta del usuario de portal) que recibe el aviso.")
    role = fields.Char(string="Perfil", index=True,
                       help="Perfil con el que el socio participa (camaronera, empacadora, "
                            "verificador…). Vacío = cualquiera.")
    kind = fields.Char(string="Tipo", required=True, index=True)
    event_type = fields.Char(string="Evento de origen", readonly=True)
    title = fields.Char(string="Título", required=True)
    body = fields.Text(string="Texto")
    target_type = fields.Char(string="Tipo de destino")
    target_uuid = fields.Char(string="Destino (uuid)", index=True)
    link = fields.Char(string="Enlace")
    is_read = fields.Boolean(string="Leído", default=False, index=True)
    read_at = fields.Datetime(string="Leído el", readonly=True)

    def _api_dict(self):
        self.ensure_one()
        return {
            "id": self.uuid_ref,
            "kind": self.kind,
            "title": self.title,
            "body": self.body or None,
            "target": {"type": self.target_type or None, "id": self.target_uuid or None},
            "link": self.link or None,
            "created_at": iso_dt(self.create_date),
            "read": bool(self.is_read),
        }

    def action_mark_read(self):
        unread = self.filtered(lambda n: not n.is_read)
        if unread:
            unread.sudo().write({"is_read": True, "read_at": fields.Datetime.now()})
        return len(unread)

    # ------------------------------------------------------------------
    @api.model
    def _from_event(self, event_type, records):
        """Crea los avisos de un evento. Nunca rompe el negocio."""
        builder = BUILDERS.get(event_type)
        spec = EVENTS.get(event_type)
        if not builder or not spec or not records:
            return self.browse()
        created = self.browse()
        try:
            with self.env.cr.savepoint():
                created = self._create_from_event(event_type, spec, builder, records)
        except Exception:  # noqa: BLE001 - un aviso nunca tumba una venta
            _logger.exception("No se pudieron crear los avisos de la app del evento %s", event_type)
        return created

    def _create_from_event(self, event_type, spec, builder, records):
        env = self.env(su=True)
        Delivery = env["shrimp.webhook.delivery"]
        vals_list, seen = [], set()
        for rec in records.sudo():
            for item in builder(env, rec):
                partner = item.pop("partner")
                key = (partner.id, item["kind"], item["target_uuid"])
                if not partner or key in seen:
                    continue
                seen.add(key)
                user = partner.sudo().user_ids.filtered(lambda u: u.active and u.share)[:1]
                # Misma regla que un webhook: audiencia del evento y permiso de
                # lectura del usuario sobre el recurso.
                if not user or not Delivery._audience_allows(spec, rec, partner, user):
                    continue
                vals_list.append(dict(item, partner_id=partner.id, event_type=event_type))
        return self.sudo().create(vals_list) if vals_list else self.browse()

    @api.model
    def _cron_cleanup(self):
        """Borra los avisos LEÍDOS de más de RETENTION_DAYS días."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=RETENTION_DAYS)
        self.sudo().search([("is_read", "=", True), ("create_date", "<", limit)]).unlink()
        return True


class ShrimpVerification(models.Model):
    _inherit = "shrimp.verification"

    api_due_notified_deadline = fields.Datetime(
        string="Aviso de plazo enviado para", readonly=True, copy=False,
        help="Plazo de aceptación del que ya se avisó (app y webhook). Si el plazo cambia, "
             "se vuelve a avisar.")

    @api.model
    def _cron_api_acceptance_due(self, hours=DUE_SOON_HOURS):
        """Avisa (evento verification.acceptance_due) a las partes que no
        respondieron cuando quedan menos de `hours` horas de plazo. Una vez
        por plazo."""
        now = fields.Datetime.now()
        candidatas = self.sudo().search([
            ("acceptance_state", "=", "waiting"),
            ("acceptance_deadline", ">", now),
            ("acceptance_deadline", "<=", now + timedelta(hours=hours)),
        ])
        pendientes = candidatas.filtered(
            lambda v: v.api_due_notified_deadline != v.acceptance_deadline
            and v.acceptance_ids.filtered(lambda a: a.decision == "pending"))
        for v in pendientes:
            self.env["shrimp.webhook.delivery"].sudo()._emit("verification.acceptance_due", v)
            v.write({"api_due_notified_deadline": v.acceptance_deadline})
        return len(pendientes)
