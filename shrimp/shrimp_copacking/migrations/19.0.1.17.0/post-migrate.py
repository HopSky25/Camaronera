"""19.0.1.17.0 — plazos y reglas de maquila (auditoría de procesos D-14, D-34, D-47).

Pone al día los datos que existían antes de las reglas nuevas, sin avisar a
nadie por correo (solo constancia en el chatter y actividades de mediación):

* «Facturable» se recalcula: ahora solo cuenta desde «firmada».
* Actas abiertas: reciben su plazo de firma CONTANDO DESDE HOY (no se dan
  por aceptadas de golpe actas viejas que nadie sabía que vencían).
* Actas en disputa: fecha de la disputa y actividad de mediación para la
  plataforma.
* Órdenes firmadas: fecha de firma (el cron las cerrará pasado el plazo).
* Solicitudes publicadas y ofertas enviadas: fecha de vencimiento (lo primero
  entre su fecha final y hoy + días de vigencia). Las que ya pasaron su fecha
  vencerán en la siguiente pasada del cron, con aviso.
* Ofertas descartadas: motivo del cierre (adjudicación a otra / solicitud
  cancelada) para poder recuperarlas si la solicitud se reabre.
* Solicitudes huérfanas («adjudicada» con la orden cancelada): se reabren si
  su ventana sigue vigente o se dan por vencidas; la oferta de la orden
  cancelada pasa a «cancelada».

Idempotente.
"""
import logging
from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.shrimp_copacking.models import copack_plazos as P

    Order = env["shrimp.copack.order"].with_context(tracking_disable=True, mail_notrack=True)
    Request = env["shrimp.copack.request"].with_context(tracking_disable=True, mail_notrack=True)
    Offer = env["shrimp.copack.offer"].with_context(tracking_disable=True, mail_notrack=True)
    ahora = fields.Datetime.now()
    hoy = fields.Date.context_today(Order)
    cuentas = {}

    # 1) Facturable: solo desde «firmada».
    todas = Order.search([])
    antes = set(todas.filtered("es_facturable").ids)
    todas._compute_es_facturable()
    todas.flush_recordset(["es_facturable"])
    despues = set(todas.filtered("es_facturable").ids)
    cuentas["dejan_de_ser_facturables"] = len(antes - despues)

    # 2) Actas abiertas: plazo desde hoy.
    abiertas = Order.search([("state", "=", "packed"), ("acceptance_state", "=", "open"),
                             ("acceptance_deadline", "=", False)])
    plazo = ahora + timedelta(hours=P.acceptance_hours(env))
    abiertas.write({"acceptance_deadline": plazo})
    for o in abiertas:
        o.message_post(body="Reglas nuevas del acta: si una parte no firma ni da su "
                            "disconformidad antes del %s, el acta se da por aceptada."
                       % o._cpk_fmt_dt(plazo))
    cuentas["actas_abiertas_con_plazo"] = len(abiertas)

    # 3) Actas en disputa: mediación.
    disputadas = Order.search([("state", "=", "packed"), ("acceptance_state", "=", "disputed"),
                               ("dispute_date", "=", False)])
    for o in disputadas:
        rechazo = o.acceptance_ids.filtered(lambda f: f.active and f.decision == "rejected")[:1]
        o.write({"dispute_date": rechazo.decided_at or o.write_date or ahora})
        o._shrimp_open_mediation()
    cuentas["actas_en_disputa_a_mediacion"] = len(disputadas)

    # 4) Firmadas: fecha de firma.
    firmadas = Order.search([("state", "in", ("signed", "closed")), ("signed_date", "=", False),
                             ("self_packing", "=", False)])
    for o in firmadas:
        fechas = [d for d in o.acceptance_ids.filtered("active").mapped("decided_at") if d]
        o.signed_date = max(fechas) if fechas else (o.write_date or ahora)
    cuentas["ordenes_firmadas_con_fecha"] = len(firmadas)

    # 5) Huérfanas: solicitud «adjudicada» con la orden cancelada (o sin orden).
    huerfanas = Request.search([("state", "=", "assigned")]).filtered(
        lambda r: not r.order_id or r.order_id.state == "cancelled")
    reabiertas = vencidas = 0
    for sol in huerfanas:
        orden = sol.order_id
        if orden and orden.offer_id and orden.offer_id.state == "accepted":
            orden.offer_id.write({"state": "cancelled", "closed_reason": "order_cancelled"})
        if sol.needed_to and sol.needed_to >= hoy:
            sol.write({"state": "published", "order_id": False, "published_date": ahora,
                       "expires_on": sol._shrimp_new_expiry()})
            sol.message_post(body="Solicitud reabierta: su orden %s estaba cancelada y la "
                                  "solicitud se había quedado «adjudicada»." % (orden.name or ""))
            reabiertas += 1
        else:
            sol.write({"state": "expired", "order_id": False})
            sol.message_post(body="Solicitud vencida: su orden %s estaba cancelada y la "
                                  "ventana de fechas ya pasó." % (orden.name or ""))
            vencidas += 1
    cuentas["solicitudes_huerfanas_reabiertas"] = reabiertas
    cuentas["solicitudes_huerfanas_vencidas"] = vencidas

    # 6) Vencimientos.
    publicadas = Request.search([("state", "=", "published"), ("expires_on", "=", False)])
    for sol in publicadas:
        sol.write({"expires_on": sol._shrimp_new_expiry(),
                   "published_date": sol.published_date or sol.create_date})
    cuentas["solicitudes_publicadas_con_vencimiento"] = len(publicadas)
    cuentas["solicitudes_que_venceran_en_el_cron"] = len(publicadas.filtered(
        lambda r: r.expires_on and r.expires_on < hoy))
    enviadas = Offer.search([("state", "=", "sent"), ("expires_on", "=", False)])
    for of in enviadas:
        of.expires_on = of._shrimp_new_expiry()
    cuentas["ofertas_enviadas_con_vencimiento"] = len(enviadas)

    # 7) Motivo del cierre de las ofertas descartadas.
    descartadas = Offer.search([("state", "=", "rejected"), ("closed_reason", "=", False)])
    for of in descartadas:
        if of.request_id.state == "cancelled":
            of.closed_reason = "request_cancelled"
        else:
            of.closed_reason = "award"
    cuentas["ofertas_descartadas_con_motivo"] = len(descartadas)

    _logger.info("shrimp_copacking 19.0.1.17.0 (D-14/D-34/D-47): %s", cuentas)
