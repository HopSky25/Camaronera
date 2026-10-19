"""Hueco crítico #4 de la auditoría: el SRI cierra el circuito de facturación.

Respuestas del SRI SIMULADAS (autorizado, devuelta, no autorizado, timeout).
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import SriPlatformCase


@tagged("post_install", "-at_install")
class TestSriQueue(SriPlatformCase):

    # -- Configuración explícita -----------------------------------------
    def test_sin_diario_el_cobro_espera_con_motivo(self):
        self.company.shrimp_service_journal_id = False
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "pending")
        self.assertIn("diario de facturas de servicio", cobro.invoice_error)
        self.assertEqual(cobro.invoice_attempts, 0, "Un bloqueo no gasta intentos")
        self.assertFalse(cobro.invoice_id)
        self.assertEqual(cobro.sri_queue_status, "blocked")
        # Nunca cae en otro diario de ventas.
        self.assertFalse(self.env["account.move"].search([("ref", "=", cobro.name)]))
        self.company.shrimp_service_journal_id = self.journal
        self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.invoice_id.journal_id, self.journal)

    def test_problemas_de_configuracion(self):
        self.assertEqual(self.company._shrimp_sri_config_problems(), [])
        linea_nc = self.punto.line_ids.filtered(lambda l: l.document_type == "04")
        linea_nc.unlink()
        self.company.shrimp_service_payment_id = False
        self.company.sudo().ec_sri_cert_data = False
        problemas = " ".join(self.company._shrimp_sri_config_problems())
        self.assertIn("04", problemas)
        self.assertIn("forma de pago", problemas)
        self.assertIn("certificado", problemas)
        settings = self.env["res.config.settings"].with_company(self.company).create({})
        self.assertFalse(settings.shrimp_sri_config_ok)
        self.assertIn("INCOMPLETA", settings.shrimp_sri_config_status)

    # -- Factura autorizada / devuelta / timeout --------------------------
    def test_factura_autorizada(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        doc = self._doc(cobro.invoice_id, "01")
        self.assertEqual(doc.state, "queued")
        self.assertEqual(cobro.sri_queue_status, "sri_pending")
        self.assertIn(cobro, self.env["shrimp.charge"].search([("sri_q_pending", "=", True)]))
        self.assertIn(cobro, self.env["shrimp.charge"].search(
            [("sri_queue_status", "=", "sri_pending")]))
        self._process(cobro.invoice_id)
        self.assertEqual(doc.state, "authorized")
        self.assertEqual(cobro.state, "invoiced")
        self.assertFalse(cobro.invoice_error)
        self.assertEqual(cobro.sri_queue_status, "ok")
        self.assertNotIn(cobro, self.env["shrimp.charge"].search([("sri_q_pending", "=", True)]))

    def test_devuelta_marca_el_cobro_avisa_y_se_reenvia(self):
        cobro = self._cobro()
        cobro._try_invoice()
        doc = self._process(cobro.invoice_id, mode="devuelta")
        self.assertEqual(doc.state, "rejected")
        self.assertEqual(cobro.state, "sri_rejected")
        self.assertIn("ARCHIVO NO CUMPLE", cobro.invoice_error)
        self.assertIn(cobro, self.env["shrimp.charge"].search([("sri_q_rejected", "=", True)]))
        act = cobro.activity_ids
        self.assertEqual(len(act), 1)
        self.assertEqual(act.user_id, self.responsable)
        # Corregir y reenviar (botón): conserva la clave de acceso.
        clave = doc.access_key
        cobro.action_retry_invoice()
        self.assertEqual(doc.state, "queued")
        self.assertEqual(doc.access_key, clave)
        self.assertEqual(cobro.invoice_attempts, 1)
        self._process(cobro.invoice_id)
        self.assertEqual(doc.state, "authorized")
        self.assertEqual(cobro.state, "invoiced")
        self.assertFalse(cobro.invoice_error)
        self.assertFalse(cobro.activity_ids, "El aviso se cierra al autorizarse")

    def test_no_autorizado_y_reenvio_automatico_tras_corregir_cliente(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self._process(cobro.invoice_id, mode="no_autorizado")
        self.assertEqual(cobro.state, "sri_rejected")
        self.assertIn("IDENTIFICACION NO VALIDA", cobro.invoice_error)
        # Sin corrección, el cron no lo reenvía (daría el mismo rechazo).
        cobro.last_attempt_at = "2030-01-01 00:00:00"
        self.env["shrimp.charge"]._cron_retry_fiscal()
        self.assertEqual(self._doc(cobro.invoice_id, "01").state, "rejected")
        # Al corregir el cliente, el cron lo reenvía solo.
        cobro.last_attempt_at = "2020-01-01 00:00:00"
        self.cliente.write({"street": "Calle corregida 123"})
        self.env["shrimp.charge"]._cron_retry_fiscal()
        self.assertEqual(self._doc(cobro.invoice_id, "01").state, "queued")

    def test_reenvios_agotados_escalan(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self._process(cobro.invoice_id, mode="devuelta")
        cobro.invoice_attempts = cobro._shrimp_max_auto_attempts()
        self.assertEqual(cobro.sri_queue_status, "exhausted")
        self.assertIn(cobro, self.env["shrimp.charge"].search([("sri_q_exhausted", "=", True)]))
        cobro.last_attempt_at = "2020-01-01 00:00:00"
        self.cliente.write({"street": "Otra"})
        self.env["shrimp.charge"]._cron_retry_fiscal()
        self.assertEqual(self._doc(cobro.invoice_id, "01").state, "rejected",
                         "Agotado: el cron ya no lo reenvía")

    def test_timeout_no_es_rechazo(self):
        cobro = self._cobro()
        cobro._try_invoice()
        doc = self._process(cobro.invoice_id, mode="timeout")
        self.assertEqual(doc.state, "queued")
        self.assertEqual(cobro.state, "invoiced")
        self.assertEqual(cobro.sri_queue_status, "sri_pending")
        self.assertFalse(cobro.activity_ids)

    def test_intentos_agotados_al_facturar_escalan(self):
        cobro = self._cobro()
        Charge = self.registry["shrimp.charge"]
        with patch.object(Charge, "_create_invoice_documents", autospec=True,
                          side_effect=UserError("fallo simulado")):
            for _i in range(cobro._shrimp_max_auto_attempts()):
                cobro._try_invoice()
        self.assertEqual(cobro.state, "error")
        self.assertEqual(cobro.sri_queue_status, "exhausted")
        self.assertEqual(len(cobro.activity_ids), 1)
        self.assertEqual(cobro.activity_ids.user_id, self.responsable)

    # -- Nota de crédito ----------------------------------------------------
    def test_nota_de_credito_va_al_sri(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self._process(cobro.invoice_id)
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        nota = cobro.refund_id
        self.assertEqual(nota.l10n_latam_document_type_id, self.nota_credito)
        doc = self._doc(nota, "04")
        self.assertEqual(doc.state, "queued", "La NC se firma y encola como la factura")
        self.assertEqual(cobro.sri_queue_status, "sri_pending")
        self._process(nota, "04")
        self.assertEqual(doc.state, "authorized")
        self.assertEqual(cobro.sri_queue_status, "ok")

    def test_nota_de_credito_rechazada_se_avisa_y_reenvia(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self._process(cobro.invoice_id)
        cobro.action_cancel_charge("Servicio no prestado")
        self._process(cobro.refund_id, "04", mode="devuelta")
        self.assertEqual(cobro.state, "credited")
        self.assertEqual(cobro.sri_queue_status, "sri_rejected")
        self.assertTrue(cobro.activity_ids)
        cobro.action_retry_invoice()
        self.assertEqual(self._doc(cobro.refund_id, "04").state, "queued")

    def test_nota_de_credito_espera_la_autorizacion(self):
        cobro = self._cobro()
        cobro._try_invoice()
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "to_credit")
        self.assertIn("sigue en el SRI", cobro.invoice_error)
        self.assertFalse(cobro.refund_id)
        self._process(cobro.invoice_id)
        self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        self.assertEqual(self._doc(cobro.refund_id, "04").state, "queued")

    def test_anular_factura_rechazada_sin_nota_de_credito(self):
        cobro = self._cobro()
        cobro._try_invoice()
        factura = cobro.invoice_id
        self._process(factura, mode="devuelta")
        cobro.action_cancel_charge("Compra cancelada")
        self.assertEqual(cobro.state, "cancelled")
        self.assertFalse(cobro.refund_id)
        self.assertEqual(factura.state, "cancel")

    def test_nota_de_credito_con_revision_de_contabilidad(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_l10n_ec.credit_auto_emit", "False")
        cobro = self._cobro()
        cobro._try_invoice()
        self._process(cobro.invoice_id)
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "credited")
        self.assertFalse(self._doc(cobro.refund_id, "04"))
        self.assertTrue(cobro.activity_ids)
        self.assertEqual(cobro.sri_queue_status, "sri_not_sent")
        cobro.action_retry_invoice()
        self.assertEqual(self._doc(cobro.refund_id, "04").state, "queued")

    # -- Consumidor final ---------------------------------------------------
    def _sin_ruc(self):
        return self.env["res.partner"].create({
            "name": "Comprador sin RUC", "email": "sinruc@prueba.test"})

    def test_consumidor_final_hasta_el_limite(self):
        cobro = self._cobro(10.0, payer=self._sin_ruc())
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.invoice_id.partner_id, self.env.ref("l10n_ec.ec_final_consumer"))
        self.assertEqual(self._doc(cobro.invoice_id, "01").state, "queued")

    def test_sin_ruc_sobre_el_limite_espera_y_pide_datos(self):
        payer = self._sin_ruc()
        cobro = self._cobro(100.0, payer=payer)
        cobro._try_invoice()
        self.assertEqual(cobro.state, "pending")
        self.assertIn("no tiene RUC ni cédula", cobro.invoice_error)
        self.assertEqual(cobro.invoice_attempts, 0)
        aviso = self.env["mail.message"].search([
            ("model", "=", "shrimp.charge"), ("res_id", "=", cobro.id),
            ("partner_ids", "in", payer.ids)])
        self.assertTrue(aviso, "Se le piden los datos fiscales al pagador")
        payer.write({"vat": "0912345675"})
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.invoice_id.partner_id, payer)

    def test_consumidor_final_desactivado(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_l10n_ec.final_consumer_fallback", "False")
        cobro = self._cobro(10.0, payer=self._sin_ruc())
        cobro._try_invoice()
        self.assertEqual(cobro.state, "pending")

    def test_consumidor_final_autorizada_no_admite_nota(self):
        cobro = self._cobro(10.0, payer=self._sin_ruc())
        cobro._try_invoice()
        self._process(cobro.invoice_id)
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "cancelled")
        self.assertFalse(cobro.refund_id)
        self.assertTrue(cobro.activity_ids)

    # -- Backlog y resumen diario -------------------------------------------
    def test_reencolar_backlog_solo_con_configuracion(self):
        maximo = self.env["shrimp.charge"]._shrimp_max_auto_attempts()
        viejos = self._cobro() | self._cobro(20.0)
        viejos.write({"invoice_attempts": maximo, "state": "error",
                      "invoice_error": "El diario Sales no usa documentos del SRI"})
        demo = self._cobro(5.0)
        demo.write({"invoice_attempts": maximo, "invoice_error":
                    "Datos de demostración: los cobros de ejemplo no se facturan automáticamente."})
        self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(set(viejos.mapped("state")), {"error"}, "El cron no toca el backlog")
        self.company.shrimp_service_sri_point_id = False
        settings = self.env["res.config.settings"].with_company(self.company).create({})
        self.assertEqual(settings.shrimp_sri_backlog_count, 2)
        with self.assertRaises(UserError):
            settings.action_shrimp_sri_requeue_backlog()
        self.company.shrimp_service_sri_point_id = self.punto
        settings.action_shrimp_sri_requeue_backlog()
        self.assertEqual(set(viejos.mapped("state")), {"pending"})
        self.assertEqual(set(viejos.mapped("invoice_attempts")), {0})
        self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(set(viejos.mapped("state")), {"invoiced"})
        self.assertEqual(demo.state, "pending", "Los cobros de demostración no se reencolan")
        self.assertEqual(demo.invoice_attempts, maximo)

    def test_resumen_diario(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self._process(cobro.invoice_id, mode="devuelta")
        antes = self.env["mail.mail"].search_count([])
        self.assertTrue(self.env["shrimp.charge"]._cron_sri_daily_summary())
        correo = self.env["mail.mail"].search([], order="id desc", limit=1)
        self.assertEqual(self.env["mail.mail"].search_count([]), antes + 1)
        self.assertIn("Cola SRI", correo.subject)
        self.assertIn("contadora.sri@prueba.test", correo.email_to)
        self.env["ir.config_parameter"].sudo().set_param("shrimp_l10n_ec.daily_summary", "False")
        self.assertFalse(self.env["shrimp.charge"]._cron_sri_daily_summary())

    def test_cola_sri_vista(self):
        action = self.env.ref("shrimp_l10n_ec.action_shrimp_sri_queue")
        self.assertEqual(action.res_model, "shrimp.charge")
        menu = self.env.ref("shrimp_l10n_ec.menu_shrimp_sri_queue")
        self.assertEqual(menu.parent_id, self.env.ref("shrimp_marketplace.menu_shrimp_reports_root"))
        self.assertIn(self.env.ref("shrimp_marketplace.group_marketplace_manager"), menu.group_ids)
        for fname in ("sri_q_pending", "sri_q_error", "sri_q_rejected", "sri_q_retry",
                      "sri_q_exhausted"):
            self.env["shrimp.charge"].search([(fname, "=", True)])
