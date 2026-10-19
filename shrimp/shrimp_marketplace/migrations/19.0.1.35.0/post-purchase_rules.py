"""Reglas de la compra de la auditoría de procesos (§10–11).

D-26  Toda compra directa lleva confirmación de recepción. Las compras
      directas CONFIRMADAS que no la llevaban (la matriz no la marcaba) pasan
      a «Esperando recepción» con el plazo contado DESDE HOY
      (reception_floor_date): ninguna se confirma ni se marca «No entregada»
      antes de que el comprador tenga los N días completos, y los atrasos
      anteriores a hoy no dan strike. Los avisos de fechas de entrega ya
      pasadas se dan por enviados (sin tanda de «entrega atrasada»). La
      columna «Confirmación de recepción» de la matriz queda activa en todas
      las reglas (es informativa).
D-42  Un strike como máximo por compra: si una compra tiene varios strikes
      vigentes, se conservan el más antiguo y se anulan los demás con la
      justificación «D-42». Después se levantan las suspensiones cuyas
      cuentas ya no llegan al umbral.
D-43  La fecha desde la que es obligatoria la factura del vendedor la fija
      data/purchase_rules_data.xml (hoy); aquí solo se informa.
D-29  Nada que migrar (la tabla de retiros nace vacía); se informa cuántas
      compras dejan de tener página pública (borrador / en verificación /
      canceladas sin confirmar).
"""
import logging

from odoo import SUPERUSER_ID, api, fields
from odoo.tools import sql

_logger = logging.getLogger(__name__)


def _verificadas(cr):
    ids = set()
    if sql.table_exists(cr, "shrimp_verification"):
        cr.execute("SELECT DISTINCT transaction_id FROM shrimp_verification "
                   "WHERE transaction_id IS NOT NULL")
        ids |= {r[0] for r in cr.fetchall()}
    if sql.column_exists(cr, "shrimp_transaction", "needs_verification"):
        cr.execute("SELECT id FROM shrimp_transaction WHERE needs_verification")
        ids |= {r[0] for r in cr.fetchall()}
    return ids


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Tx = env["shrimp.transaction"].with_context(
        active_test=False, tracking_disable=True, mail_notrack=True)
    hoy = Tx._shrimp_today()

    # ---- D-26 -------------------------------------------------------------
    verificadas = _verificadas(cr)
    candidatas = Tx.search([("state", "=", "confirmed"), ("reception_required", "=", False)])
    candidatas = candidatas.filtered(lambda t: t.id not in verificadas)
    for tx in candidatas:
        vals = {"reception_required": True, "reception_floor_date": hoy}
        if not tx.reception_status:
            vals["reception_status"] = "waiting"
        # «d26_from»: los atrasos anteriores a hoy no dan strike ni permiten
        # cancelar por atraso sin aviso (shrimp_transaction_delay).
        marcas = tx._shrimp_marks() | {"d26_from:%s" % hoy.isoformat()}
        if tx.delivery_date and tx.delivery_date < hoy:
            f = tx.delivery_date
            marcas |= {"dlv_pre:%s" % f, "dlv_day:%s" % f, "dlv_late:%s" % f}
        vals["reception_notify_marks"] = " ".join(sorted(marcas))
        tx.write(vals)
    reglas = 0
    if sql.table_exists(cr, "shrimp_verification_rule"):
        cr.execute("UPDATE shrimp_verification_rule SET requires_reception_confirmation = TRUE "
                   "WHERE requires_reception_confirmation IS NOT TRUE")
        reglas = cr.rowcount
    _logger.info("D-26: %s compras directas confirmadas pasan a «Esperando recepción» con el plazo "
                 "desde hoy (%s); %s reglas de la matriz con la recepción activada.",
                 len(candidatas), hoy, reglas)

    # ---- D-42 -------------------------------------------------------------
    anulados = 0
    levantadas = 0
    if sql.table_exists(cr, "shrimp_seller_strike"):
        Strike = env["shrimp.seller.strike"].sudo()
        cr.execute("""SELECT transaction_id FROM shrimp_seller_strike
                      WHERE transaction_id IS NOT NULL AND annulled IS NOT TRUE
                      GROUP BY transaction_id HAVING count(*) > 1""")
        for (tx_id,) in cr.fetchall():
            vivos = Strike.search([("transaction_id", "=", tx_id), ("annulled", "=", False)],
                                  order="date asc, id asc")
            extra = vivos[1:]
            extra.write({
                "annulled": True, "annulled_at": fields.Datetime.now(),
                "annul_justification": "D-42 (auditoría de procesos): una compra suma como "
                                       "máximo un strike; se conserva el primero (%s)."
                                       % (vivos[0].name or vivos[0].id),
            })
            anulados += len(extra)
        if sql.table_exists(cr, "shrimp_seller_suspension"):
            levantadas = len(env["shrimp.seller.suspension"]._shrimp_cron_auto_lift())
    _logger.info("D-42: %s strikes duplicados anulados; %s suspensiones levantadas "
                 "automáticamente.", anulados, levantadas)

    # ---- D-43 / D-29: solo informe ---------------------------------------
    sin_factura = Tx.search_count([("state", "=", "done"), ("seller_invoice_number", "=", False),
                                   ("seller_invoice_access_key", "=", False),
                                   ("seller_invoice_attachment_id", "=", False)])
    _logger.info("D-43: %s compras completadas ANTES de hoy sin factura del vendedor: no reciben "
                 "recordatorios ni se marcan vencidas (la obligación aplica desde %s).",
                 sin_factura, env["ir.config_parameter"].sudo().get_param(
                     "shrimp_marketplace.seller_invoice_rules_since"))
    ocultas = Tx.search_count([("state", "in", ("draft", "pending_verification",
                                                "pending_acceptance"))]) \
        if "pending_verification" in dict(Tx._fields["state"].selection) \
        else Tx.search_count([("state", "=", "draft")])
    _logger.info("D-29: %s compras en borrador / en verificación dejan de tener página pública.",
                 ocultas)
