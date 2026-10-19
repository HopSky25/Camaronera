"""19.0.1.38.0 — Decisiones de la auditoría de procesos D-28, D-30, D-31,
D-49 (siembras) y D-13.

* D-28 Solicitud de chequeo RETIRADA: se cancelan las abiertas (aviso a las
  dos partes; la cantidad reservada vuelve al disponible), se borran el cron
  del plazo de respuesta y los parámetros «costo del chequeo» y «horas para
  responder». El histórico queda de solo lectura.
* D-30 PCR positivo: los lotes cuya ficha de calidad más reciente es positiva
  y siguen publicados (o con compras abiertas) se bloquean y se avisa.
* D-31 Caducidad: las publicaciones sin caducidad útil (vencida o a menos del
  aviso) reciben «Disponible hasta» = hoy + 30 días; no se vence nada en masa.
* D-49 Las siembras «Asignado» que ya son origen de una cosecha se cierran.
* D-13 La vigencia de los certificados (columna nueva) la calcula el ORM al
  crear la columna; aquí solo se refresca y se cuentan los vencidos. Los
  avisos los manda el cron diario.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})

    # ---- D-28 ---------------------------------------------------------
    Check = env["shrimp.check.request"]
    cerradas = Check._shrimp_retire_open()
    cron = env.ref("shrimp_marketplace.ir_cron_shrimp_check_request_expire", raise_if_not_found=False)
    if cron:
        cron.unlink()
    env["ir.model.data"].search([
        ("module", "=", "shrimp_marketplace"),
        ("name", "=", "ir_cron_shrimp_check_request_expire")]).unlink()
    params = env["ir.config_parameter"].search([("key", "in", (
        "shrimp_marketplace.check_fee", "shrimp_marketplace.check_request_response_hours"))])
    n_params = len(params)
    params.unlink()
    _logger.info("D-28 chequeo retirado: %s solicitudes abiertas canceladas (%s unidades "
                 "liberadas), cron borrado: %s, parámetros borrados: %s; histórico: %s solicitudes.",
                 len(cerradas), sum(cerradas.mapped("qty")), bool(cron), n_params,
                 Check.sudo().search_count([]))

    # ---- D-30 ---------------------------------------------------------
    Product = env["shrimp.product"].with_context(active_test=False)
    bloqueados = Product.search([("pcr_blocked", "=", True)])
    aplicados = 0
    for prod in bloqueados:
        if prod.state in ("published", "sold") or prod._shrimp_pcr_open_purchases():
            prod._shrimp_pcr_block()
            aplicados += 1
    _logger.info("D-30 PCR positivo: %s lotes con la ficha más reciente positiva; %s bloqueados "
                 "ahora (estaban publicados o con compras abiertas).", len(bloqueados), aplicados)

    # ---- D-31 ---------------------------------------------------------
    tocadas = env["shrimp.product"]._shrimp_expiry_migrate()
    _logger.info("D-31 caducidad: %s publicaciones recibieron «Disponible hasta» = hoy + 30 días.",
                 tocadas)

    # ---- D-49 (siembras) ----------------------------------------------
    cerradas_siembra = env["shrimp.product"]._shrimp_close_harvested_sowings_migrate()
    _logger.info("D-49 siembras: %s siembras «Asignado» que ya eran origen de una cosecha se "
                 "cerraron.", cerradas_siembra)

    # ---- D-13 ---------------------------------------------------------
    for model in ("shrimp.user.certificate.line", "shrimp.product.certificate.line"):
        Model = env[model].with_context(active_test=False)
        Model._shrimp_refresh_validity()
        _logger.info("D-13 %s: %s vencidos (%s aprobados), %s por vencer.", model,
                     Model.search_count([("validity_state", "=", "expired")]),
                     Model.search_count([("validity_state", "=", "expired"),
                                         ("status", "=", "approved")]),
                     Model.search_count([("validity_state", "=", "expiring")]))

    # ---- Borradores legales 2026-10.2 ----------------------------------
    tocadas = env["shrimp.decision.policy"]._shrimp_update_decision_drafts()
    _logger.info("Borradores legales actualizados: %s", len(tocadas or []))
