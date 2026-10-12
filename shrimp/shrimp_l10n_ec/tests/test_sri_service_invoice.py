"""Factura electrónica del SERVICIO de la plataforma contra el módulo SRI real.

Se usa el modelo de verdad de l10n_ec_sri_community: compañía con plan
contable ``ec`` y RUC válido, diario de ventas 001-003 con documentos latam y
``ec.sri.point`` reales de Factura (01) y Nota de crédito (04).

Nada sale hacia el SRI: el transporte (``SriClient.receive/authorize``) está
parcheado para FALLAR si alguien lo llama. La firma usa un certificado .p12
autofirmado generado en la prueba (como hacen los tests del módulo SRI), así
que el XML se arma, se firma y se valida contra el XSD de verdad.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12

from odoo.tests import TransactionCase, tagged

from odoo.addons.l10n_ec_sri_community.hooks import _configurar_impuestos
from odoo.addons.l10n_ec_sri_community.services import transport


def _no_network(*args, **kwargs):
    raise AssertionError("La prueba intentó hablar con el SRI.")


@tagged("post_install", "-at_install")
class TestSriServiceInvoice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ec = cls.env.ref("base.ec")
        cls.company = cls.env["res.company"].create({
            "name": "PLATAFORMA SRI TEST", "country_id": ec.id, "vat": "1790016919001",
            "currency_id": cls.env.ref("base.USD").id})
        cls.env.user.company_ids |= cls.company
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        cls.env["account.chart.template"].try_loading("ec", company=cls.company, install_demo=False)
        cls.company = cls.env["res.company"].browse(cls.company.id)
        cls.company.write({
            "ec_sri_enabled": True, "ec_sri_environment": "1",
            "ec_sri_address": "Av. 9 de Octubre 100, Guayaquil",
            "tax_calculation_rounding_method": "round_per_line"})
        # Lo que hace el post_init_hook del módulo SRI con las compañías que ya
        # existían al instalarlo: el código del catálogo en cada IVA.
        _configurar_impuestos(cls.env)

        cls.factura = cls.env.ref("l10n_ec.ec_dt_01")
        cls.nota_credito = cls.env.ref("l10n_ec.ec_dt_04")
        cls.diario_general = cls.env["account.journal"].search([
            ("company_id", "=", cls.company.id), ("type", "=", "sale"),
            ("l10n_latam_use_documents", "=", True)], limit=1)
        assert cls.diario_general, "El plan ec no creó el diario de ventas con documentos."
        cls.diario = cls.diario_general.copy({
            "name": "001-003 Plataforma", "code": "PLT", "l10n_ec_entity": "001",
            "l10n_ec_emission": "003", "l10n_latam_use_documents": True})
        Point = cls.env["ec.sri.point"]
        cls.punto = Point.create({
            "name": "Plataforma facturas", "company_id": cls.company.id,
            "establishment": "001", "emission": "003", "document_type": "01",
            "environment": "1", "address": "Guayaquil"})
        cls.punto_nc = Point.create({
            "name": "Plataforma notas de crédito", "company_id": cls.company.id,
            "establishment": "001", "emission": "003", "document_type": "04",
            "environment": "1", "address": "Guayaquil"})
        cls.forma_pago = cls.env.ref("l10n_ec.P20")
        cls.company.write({"shrimp_service_journal_id": cls.diario.id,
                           "shrimp_service_sri_point_id": cls.punto.id,
                           "shrimp_service_payment_id": cls.forma_pago.id,
                           "shrimp_sri_auto_emit": True})
        cls.cliente = cls.env["res.partner"].create({
            "name": "Laboratorio SRI", "is_company": True, "email": "lab.sri@prueba.test",
            "vat_or_id": "1790011674001", "shrimp_user_type": "laboratorio",
            "country_id": ec.id, "street": "Km 5 vía a la costa", "city": "Guayaquil",
            "lab_razon_social": "Laboratorio SRI S.A.", "lab_ubicacion": "Quito"})

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "TEST CERTIFICATE")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                .sign(key, hashes.SHA256()))
        cls.p12 = pkcs12.serialize_key_and_certificates(
            b"test", key, cert, None, serialization.BestAvailableEncryption(b"test"))

    def setUp(self):
        super().setUp()
        # Ninguna prueba puede llegar al SRI.
        for method in ("receive", "authorize"):
            patcher = patch.object(transport.SriClient, method, side_effect=_no_network)
            self.addCleanup(patcher.stop)
            patcher.start()

    # ------------------------------------------------------------------
    def _cobro(self, importe=12.5):
        return self.env["shrimp.charge"].with_context(shrimp_charge_no_invoice=True)._register_charge({
            "charge_type": "commission", "company_id": self.company.id,
            "payer_partner_id": self.cliente.id, "seller_partner_id": self.cliente.id,
            "amount": importe, "invoice_qty": 1.0, "unit_amount": importe,
            "currency_id": self.env.ref("base.USD").id,
            "origin": "TXN-SRI", "description": "Comisión marketplace – prueba"})

    def _firmar_y_encolar(self):
        """El cron del puente, con el certificado de prueba."""
        with patch.object(type(self.company), "_ec_sri_certificate",
                          return_value=(self.p12, "test")):
            self.env["shrimp.charge"]._cron_sri_queue_service_documents()

    def _facturas_contabilizadas(self):
        return self.env["account.move"].search_count([
            ("company_id", "=", self.company.id), ("move_type", "=", "out_invoice"),
            ("state", "=", "posted")])

    # ------------------------------------------------------------------
    def test_identificacion_segun_el_numero(self):
        self.assertEqual(self.cliente.vat, "1790011674001")
        self.assertEqual(self.cliente.l10n_latam_identification_type_id,
                         self.env.ref("l10n_ec.ec_ruc"))
        # Lo que lee el módulo SRI para el XML.
        self.assertEqual(self.cliente.ec_sri_identification_type, "04")
        P = self.env["res.partner"]
        self.assertEqual(P._shrimp_identification_type_for("0912345678"),
                         self.env.ref("l10n_ec.ec_dni"))
        self.assertEqual(P._shrimp_sri_identification_code_for("0912345678"), "05")
        self.assertEqual(P._shrimp_sri_identification_code_for("9999999999999"), "07")
        self.assertEqual(P._shrimp_sri_identification_code_for("AB123456"), "06")

    def test_factura_publicada_y_comprobante_en_borrador(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        factura = cobro.invoice_id
        self.assertEqual(factura.state, "posted")
        self.assertEqual(factura.journal_id, self.diario)
        self.assertEqual(factura.l10n_latam_document_type_id, self.factura)
        self.assertEqual(factura.l10n_ec_sri_payment_id, self.forma_pago)
        self.assertEqual(factura._ec_sri_number_parts(), ("001", "003", "000000001"))
        # El XSD del SRI no admite saltos de línea en la descripción.
        self.assertNotIn("\n", factura.invoice_line_ids[0].name)
        documento = factura.ec_sri_document_ids
        self.assertEqual(len(documento), 1)
        self.assertEqual(documento.document_type, "01")
        self.assertEqual(documento.point_id, self.punto)
        self.assertEqual(documento.date, factura.invoice_date)
        self.assertEqual(documento.partner_id, self.cliente)
        # En la petición del usuario no se firma nada: queda en borrador y el
        # cron del puente queda despertado.
        self.assertEqual(documento.state, "draft")
        self.assertFalse(documento.signed_xml)
        self.assertFalse(documento.access_key)
        self.assertEqual(cobro.sri_document_id, documento)
        cron = self.env.ref("shrimp_l10n_ec.ir_cron_shrimp_sri_queue")
        self.assertTrue(self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron.id)]))

    def test_cron_firma_y_encola_sin_enviar(self):
        cobro = self._cobro()
        cobro._try_invoice()
        documento = cobro.sri_document_id
        self._firmar_y_encolar()
        self.assertEqual(documento.state, "queued", documento.event_ids.mapped("description"))
        self.assertTrue(documento.signed_xml)
        self.assertEqual(documento.environment, "1")
        self.assertFalse(documento.was_sent)
        clave = documento.access_key
        self.assertEqual(len(clave), 49)
        self.assertEqual(clave[8:10], "01")                 # factura
        self.assertEqual(clave[10:23], "1790016919001")     # RUC de la plataforma
        self.assertEqual(clave[23], "1")                    # ambiente de pruebas
        self.assertEqual(clave[24:39], "001003000000001")   # serie y secuencial de la factura
        self.assertEqual(documento.fiscal_number, "001003000000001")
        # El envío lo hará el cron del módulo SRI.
        cron_sri = self.env.ref("l10n_ec_sri_community.ir_cron_sri_queue")
        self.assertTrue(self.env["ir.cron.trigger"].search_count([("cron_id", "=", cron_sri.id)]))

    def test_sin_certificado_queda_en_borrador_con_aviso(self):
        cobro = self._cobro()
        cobro._try_invoice()
        documento = cobro.sri_document_id
        self.company.sudo().ec_sri_secret_name = False
        Charge = self.env["shrimp.charge"]
        Charge._cron_sri_queue_service_documents()
        Charge._cron_sri_queue_service_documents()
        self.assertEqual(documento.state, "draft")
        self.assertFalse(documento.signed_xml)
        avisos = documento.event_ids.filtered(lambda e: e.operation == "shrimp_queue")
        self.assertEqual(len(avisos), 1, "El mismo error no se repite en el historial.")
        self.assertIn("EC_SRI_", avisos.description)
        # La factura sigue contabilizada y el cobro, facturado.
        self.assertEqual(cobro.state, "invoiced")

    def test_sin_punto_configurado_queda_en_error(self):
        # Sin punto de la plataforma y con un diario (001-001) que no tiene
        # punto de emisión SRI.
        self.company.write({"shrimp_service_sri_point_id": False,
                            "shrimp_service_journal_id": self.diario_general.id})
        antes = self._facturas_contabilizadas()
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "error")
        self.assertFalse(cobro.invoice_id)
        self.assertIn("falta el punto de emisión de Factura (01) 001-001", cobro.invoice_error)
        # No se gastó un número fiscal.
        self.assertEqual(self._facturas_contabilizadas(), antes)
        # Configurado el punto (sin diario), se usa el diario que numera 001-003.
        self.company.write({"shrimp_service_sri_point_id": self.punto.id,
                            "shrimp_service_journal_id": False})
        self.env["shrimp.charge"]._cron_retry_invoices()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.invoice_id.journal_id, self.diario)
        self.assertEqual(cobro.sri_document_id.point_id, self.punto)

    def test_punto_tomado_del_diario(self):
        self.company.shrimp_service_sri_point_id = False
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.sri_document_id.point_id, self.punto)

    def test_diario_y_punto_que_no_coinciden(self):
        self.company.shrimp_service_journal_id = self.diario_general
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "error")
        self.assertIn("Tienen que coincidir", cobro.invoice_error)

    def test_sin_ruc_queda_en_error(self):
        self.company.vat = False
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "error")
        self.assertFalse(cobro.invoice_id)
        self.assertIn("RUC", cobro.invoice_error)
        self.assertTrue(cobro.invoice_error.startswith("Factura electrónica (SRI)"))

    def test_impuesto_sin_codigo_sri_queda_en_error(self):
        impuestos = self.env["account.tax"].search([("company_id", "=", self.company.id)])
        impuestos.write({"ec_sri_percentage_code": False})
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "error")
        self.assertIn("código de porcentaje SRI", cobro.invoice_error)

    def test_nota_de_credito_del_servicio(self):
        cobro = self._cobro()
        cobro._try_invoice()
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "credited", cobro.invoice_error)
        nota = cobro.refund_id
        self.assertEqual(nota.state, "posted")
        self.assertEqual(nota.l10n_latam_document_type_id, self.nota_credito)
        self.assertEqual(nota.ec_sri_reason, "Servicio no prestado")
        self.assertEqual(nota._ec_sri_number_parts(), ("001", "003", "000000001"))
        documento = cobro.sri_refund_document_id
        self.assertEqual(documento.document_type, "04")
        self.assertEqual(documento.point_id, self.punto_nc)
        self.assertEqual(documento.state, "draft")
        # El cron encola la factura y la nota (la nota cita a la factura).
        self._firmar_y_encolar()
        self.assertEqual(cobro.sri_document_id.state, "queued")
        self.assertEqual(documento.state, "queued", documento.event_ids.mapped("description"))
        self.assertEqual(documento.access_key[8:10], "04")
        self.assertEqual(documento.access_key[24:39], "001003000000001")

    def test_nota_de_credito_sin_punto_04(self):
        cobro = self._cobro()
        cobro._try_invoice()
        self.punto_nc.active = False
        cobro.action_cancel_charge("Servicio no prestado")
        self.assertEqual(cobro.state, "to_credit")
        self.assertFalse(cobro.refund_id)
        self.assertIn("Nota de crédito (04)", cobro.invoice_error)

    def test_sri_apagado_no_toca_el_sri(self):
        self.company.ec_sri_enabled = False
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertFalse(cobro.invoice_id.ec_sri_document_ids)

    def test_sin_envio_automatico_no_crea_comprobante(self):
        self.company.shrimp_sri_auto_emit = False
        cobro = self._cobro()
        cobro._try_invoice()
        self.assertEqual(cobro.state, "invoiced", cobro.invoice_error)
        self.assertEqual(cobro.invoice_id.l10n_latam_document_type_id, self.factura)
        self.assertFalse(cobro.invoice_id.ec_sri_document_ids)
