"""Base común: una compañía con el SRI COMPLETO (ambiente de PRUEBAS) y el
SRI simulado. Nada sale a la red: SriClient.receive/authorize se reemplazan
en cada prueba y el certificado es autofirmado y generado aquí."""
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12

from odoo.tests import TransactionCase

from odoo.addons.l10n_ec_sri_community.hooks import _configurar_impuestos
from odoo.addons.l10n_ec_sri_community.services import transport
from odoo.addons.l10n_ec_sri_community.tests.common import authorization_xml


def _p12():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "PLATAFORMA TEST")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(
        b"test", key, cert, None, serialization.BestAvailableEncryption(b"test"))


class SriPlatformCase(TransactionCase):

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
        _configurar_impuestos(cls.env)
        cls.company.write({"ec_sri_enabled": True, "ec_sri_environment": "1",
                           "ec_sri_address": "Av. Principal, Guayaquil",
                           "tax_calculation_rounding_method": "round_per_line"})
        cls.company.sudo().ec_sri_cert_data = "cifrado-de-prueba"
        cls.p12 = _p12()
        cls.journal = cls.env["account.journal"].search([
            ("company_id", "=", cls.company.id), ("type", "=", "sale"),
            ("l10n_latam_use_documents", "=", True)], limit=1)
        cls.factura = cls.env.ref("l10n_ec.ec_dt_01")
        cls.nota_credito = cls.env.ref("l10n_ec.ec_dt_04")
        cls.establecimiento = cls.env["ec.sri.establishment"].create({
            "name": "Matriz", "code": "001", "company_id": cls.company.id, "address": "Guayaquil"})
        cls.punto = cls.env["ec.sri.point"].create({
            "name": "Plataforma", "establishment_id": cls.establecimiento.id, "emission": "003",
            "line_ids": [(0, 0, {"l10n_latam_document_type_id": cls.factura.id}),
                         (0, 0, {"l10n_latam_document_type_id": cls.nota_credito.id})]})
        cls.pago = cls.env["l10n_ec.sri.payment"].search([("code", "=", "20")], limit=1) \
            or cls.env["l10n_ec.sri.payment"].search([], limit=1)
        cls.company.write({"shrimp_service_journal_id": cls.journal.id,
                           "shrimp_service_sri_point_id": cls.punto.id,
                           "shrimp_service_payment_id": cls.pago.id})
        cls.cliente = cls.env["res.partner"].create({
            "name": "Laboratorio SRI", "is_company": True, "email": "lab.sri@prueba.test",
            "vat_or_id": "1790011674001", "shrimp_user_type": "laboratorio",
            "lab_razon_social": "Laboratorio SRI S.A.", "lab_ubicacion": "Quito"})
        cls.responsable = cls.env["res.users"].create({
            "name": "Contadora SRI", "login": "contadora.sri@prueba.test",
            "email": "contadora.sri@prueba.test",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id,
                                  cls.env.ref("shrimp_marketplace.group_marketplace_manager").id])]})
        cls.env["ir.config_parameter"].sudo().set_param(
            "shrimp_marketplace.billing_responsible_user_id", str(cls.responsable.id))

    # ------------------------------------------------------------------
    def setUp(self):
        super().setUp()
        cert = patch.object(type(self.company), "_ec_sri_certificate",
                            return_value=(self.p12, "test"))
        cert.start()
        self.addCleanup(cert.stop)
        # Ninguna prueba debe llegar a la red: si algo llama al SRI sin
        # simulación explícita, falla.
        red = patch.object(transport.SriClient, "_request",
                           side_effect=AssertionError("llamada real al SRI"))
        red.start()
        self.addCleanup(red.stop)

    def _cobro(self, importe=12.5, payer=None, **extra):
        vals = {
            "charge_type": "commission", "company_id": self.company.id,
            "payer_partner_id": (payer or self.cliente).id,
            "seller_partner_id": (payer or self.cliente).id,
            "amount": importe, "invoice_qty": 1.0, "unit_amount": importe,
            "currency_id": self.env.ref("base.USD").id,
            "origin": "TXN-SRI", "description": "Comisión marketplace – prueba"}
        vals.update(extra)
        return self.env["shrimp.charge"].with_context(
            shrimp_charge_no_invoice=True)._register_charge(vals)

    @staticmethod
    def _doc(move, code):
        return move.sudo().ec_sri_document_ids.filtered(lambda d: d.document_type == code)[:1]

    # Respuestas simuladas del SRI ------------------------------------
    def _authorized(self, client, key):
        doc = self.env["ec.sri.document"].sudo().with_context(bin_size=False).search(
            [("access_key", "=", key)], limit=1)
        # <autorizacion> completa (con el comprobante dentro): la usa el RIDE del correo.
        return {"state": "AUTORIZADO", "number": key, "date": "2026-10-14T10:00:00-05:00",
                "document": base64.b64decode(doc.signed_xml).decode("utf-8"),
                "authorization": authorization_xml(doc),
                "messages": [], "raw": b"<response/>"}

    @contextmanager
    def sri(self, mode):
        """mode: authorized | devuelta | no_autorizado | timeout"""
        recibida = {"state": "RECIBIDA", "messages": [], "codes": [], "raw": b"<r/>"}
        if mode == "authorized":
            rec = patch.object(transport.SriClient, "receive", return_value=recibida)
            aut = patch.object(transport.SriClient, "authorize", autospec=True,
                               side_effect=self._authorized)
        elif mode == "devuelta":
            rec = patch.object(transport.SriClient, "receive", return_value={
                "state": "DEVUELTA", "codes": ["35"], "raw": b"<r/>",
                "messages": ["35 | ARCHIVO NO CUMPLE ESTRUCTURA XML | identificacionComprador"]})
            aut = patch.object(transport.SriClient, "authorize", return_value={
                "state": "PENDING", "messages": [], "raw": b"<r/>"})
        elif mode == "no_autorizado":
            rec = patch.object(transport.SriClient, "receive", return_value=recibida)
            aut = patch.object(transport.SriClient, "authorize", return_value={
                "state": "NO AUTORIZADO", "messages": ["39 | IDENTIFICACION NO VALIDA"],
                "raw": b"<r/>"})
        else:
            rec = patch.object(transport.SriClient, "receive",
                               side_effect=transport.SriTransportError("Timeout"))
            aut = patch.object(transport.SriClient, "authorize",
                               side_effect=transport.SriTransportError("Timeout"))
        with rec, aut:
            yield

    def _process(self, move, code="01", mode="authorized"):
        doc = self._doc(move, code)
        self.assertTrue(doc, "No hay comprobante %s para %s" % (code, move.name))
        with self.sri(mode):
            doc._process()
        return doc
