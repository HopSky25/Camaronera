import base64
import io
import logging
import secrets

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools import pades

_logger = logging.getLogger(__name__)

# Estados con veredicto emitido: los únicos en los que el informe se firma.
VEREDICTOS = ("approved", "approved_obs", "rejected")

PARAM_TSA = "shrimp_firma.tsa_url"
PARAM_EMISORES = "shrimp_firma.emisores_aceptados"
PARAM_SOLO_ACREDITADOS = "shrimp_firma.solo_emisores_acreditados"
PARAM_OBLIGATORIA = "shrimp_firma.obligatoria"

REPORTE = "shrimp_firma_electronica.report_informe_verificacion"


class ShrimpVerification(models.Model):
    _inherit = "shrimp.verification"

    # ------------------------------------------------------------------
    # Firma vigente (copia de la firma activa, para listas, filtros y API)
    # ------------------------------------------------------------------
    firma_id = fields.Many2one(
        "shrimp.verification.firma", string="Firma electrónica vigente",
        readonly=True, copy=False, ondelete="set null")
    firma_ids = fields.One2many(
        "shrimp.verification.firma", "verification_id",
        string="Historial de firmas electrónicas", readonly=True)
    signed_at = fields.Datetime(string="Firmado electrónicamente el", readonly=True, copy=False)
    signed_by_user_id = fields.Many2one(
        "res.users", string="Firmado por (usuario)", readonly=True, copy=False,
        ondelete="set null")
    signer_name = fields.Char(string="Titular del certificado", readonly=True, copy=False)
    signer_id_number = fields.Char(string="Cédula / RUC del firmante", readonly=True, copy=False)
    signer_issuer = fields.Char(string="Emisor del certificado", readonly=True, copy=False)
    signer_cert_serial = fields.Char(string="Serie del certificado", readonly=True, copy=False)
    signer_cert_sha256 = fields.Char(
        string="Huella SHA-256 del certificado", readonly=True, copy=False)
    signed_pdf_sha256 = fields.Char(
        string="Huella SHA-256 del PDF firmado", readonly=True, copy=False)
    signed_pdf_attachment_id = fields.Many2one(
        "ir.attachment", string="Informe firmado (PDF)", readonly=True, copy=False,
        ondelete="set null")

    # ==================================================================
    # Parámetros
    # ==================================================================
    def _firma_settings(self):
        return self.env["shrimp.settings"]

    def _firma_obligatoria(self):
        return self._firma_settings().get_bool(PARAM_OBLIGATORIA, False)

    def _firma_solo_acreditados(self):
        return self._firma_settings().get_bool(PARAM_SOLO_ACREDITADOS, True)

    def _firma_emisores(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(PARAM_EMISORES) or ""
        return [linea.strip() for linea in raw.splitlines() if linea.strip()]

    def _firma_tsa_url(self):
        return (self.env["ir.config_parameter"].sudo().get_param(PARAM_TSA) or "").strip()

    # ==================================================================
    # Estado de la firma
    # ==================================================================
    def firma_aplica(self):
        """Solo el informe de una verificadora de la plataforma, con veredicto."""
        self.ensure_one()
        return self.verification_mode == "platform" and self.state in VEREDICTOS

    def firma_vigente(self):
        self.ensure_one()
        return self.firma_id if self.firma_id.state == "vigente" else self.env[
            "shrimp.verification.firma"]

    def firma_bloquea_aceptacion(self):
        """True si las partes todavía no pueden aceptar: la firma es
        obligatoria y la verificadora aún no firmó el informe."""
        self.ensure_one()
        return (self.verification_mode == "platform"
                and self._firma_obligatoria()
                and not self.sudo().firma_vigente())

    def firma_resumen(self):
        """«Informe firmado electrónicamente por X (emisor) el fecha»."""
        self.ensure_one()
        firma = self.sudo().firma_vigente()
        if not firma:
            return _("Pendiente de firma electrónica de la verificadora")
        return _("Informe firmado electrónicamente por %(quien)s (%(emisor)s) el %(fecha)s") % {
            "quien": firma.signer_name, "emisor": firma.signer_issuer,
            "fecha": firma.fecha_local()}

    def _firma_fecha_local(self, valor):
        """Fecha y hora de la firma con su desfase UTC, en la zona de la
        plataforma (Ecuador continental si la compañía no dice otra): la misma
        hora para cualquiera que la mire, como en la trazabilidad pública, y
        la misma que queda estampada en el PDF."""
        if not valor:
            return "—"
        local = self.env["shrimp.transaction"].with_context(
            shrimp_public_tz=True).shrimp_local_dt(valor)
        desfase = local.strftime("%z")
        desfase = "%s:%s" % (desfase[:3], desfase[3:]) if desfase else ""
        return "%s (UTC%s)" % (local.strftime("%d/%m/%Y %H:%M"), desfase)

    # ==================================================================
    # URLs y QR
    # ==================================================================
    def _firma_base_url(self):
        sitio = self.env["website"].sudo()._shrimp_main_site()
        dominio = (sitio.domain or "").strip().rstrip("/") if sitio else ""
        if dominio and not dominio.startswith("http"):
            dominio = "https://" + dominio
        return dominio or (self.get_base_url() or "").rstrip("/")

    def firma_url_publica(self, token=None):
        self.ensure_one()
        token = token or self.sudo().firma_id.token
        if not token:
            return ""
        return "%s/signature/%s" % (self._firma_base_url(), token)

    def firma_qr_uri(self, url):
        """data-URI PNG del QR (mismo método que el de la trazabilidad)."""
        if not url:
            return False
        try:
            import qrcode
            img = qrcode.make(url, box_size=8, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return "data:image/png;base64,%s" % base64.b64encode(buf.getvalue()).decode()
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo generar el QR de la firma", exc_info=True)
            return False

    # ==================================================================
    # PDF del informe
    # ==================================================================
    def _firma_render_pdf(self, token=None):
        """El informe en PDF (sin firmar). Con `token`, el QR apunta a la
        página pública de comprobación de esa firma."""
        self.ensure_one()
        reporte = self.env["ir.actions.report"].sudo().with_context(
            force_report_rendering=True, firma_token=token or False,
            report_pdf_no_attachment=True)
        pdf, _tipo = reporte._render_qweb_pdf(REPORTE, res_ids=[self.id])
        return pdf

    def informe_nombre_archivo(self, firmado=False):
        self.ensure_one()
        sufijo = " (firmado)" if firmado else ""
        return "Informe de verificación %s%s.pdf" % (self.name or "", sufijo)

    # ==================================================================
    # Firmar
    # ==================================================================
    def _firma_check_firmable(self):
        self.ensure_one()
        if self.verification_mode != "platform":
            raise UserError(_(
                "Solo se firma electrónicamente el informe de una verificadora de la plataforma."))
        if self.state not in VEREDICTOS:
            raise UserError(_(
                "El informe se firma cuando ya tiene veredicto (aprobado, aprobado "
                "con observaciones o rechazado)."))
        if self.sudo().firma_vigente():
            raise UserError(_(
                "Este informe ya está firmado electrónicamente. Una verificación "
                "firmada no se vuelve a firmar."))

    def action_firmar_informe(self, contenido_p12, contrasena, partner, user=None):
        """Firma el informe con la firma electrónica (.p12/.pfx) del firmante.

        `contenido_p12` y `contrasena` solo se usan aquí, en memoria: no se
        guardan en ningún campo, adjunto, mensaje ni log. Errores de
        certificado → UserError con un texto en español para el usuario.
        """
        self.ensure_one()
        rec = self.sudo()
        user = user or self.env.user
        if not rec._puede_dictaminar(partner):
            raise AccessError(_(
                "El informe lo firma el técnico asignado o el administrador de la "
                "empresa verificadora."))
        rec._firma_check_firmable()

        cargado = None
        try:
            cargado = pades.cargar_p12(contenido_p12, contrasena)
            contenido_p12 = contrasena = None
            pades.validar_certificado(
                cargado.cert,
                emisores_aceptados=rec._firma_emisores() if rec._firma_solo_acreditados() else None)
            datos = pades.datos_del_certificado(cargado.cert)
            token = secrets.token_urlsafe(32)
            ahora = fields.Datetime.now()
            pdf = rec._firma_render_pdf(token=token)
            tsa = rec._firma_tsa_url()
            firmado = pades.firmar_pdf(
                pdf, cargado, pades.lineas_del_sello(datos, rec._firma_fecha_local(ahora)),
                motivo=_("Informe de verificación %s") % rec.name,
                lugar="Ecuador", tsa_url=tsa or None)
            rec._firma_autocomprobar(firmado, cargado.cert)
        except pades.FirmaError as e:
            raise UserError(str(e)) from None
        finally:
            if cargado is not None:
                cargado.olvidar()
            cargado = contenido_p12 = contrasena = None

        huella_pdf = pades.sha256_hex(firmado)
        firma = self.env["shrimp.verification.firma"].sudo().create({
            "verification_id": rec.id,
            "token": token,
            "signed_at": ahora,
            "signed_by_user_id": user.id,
            "signer_partner_id": partner.id,
            "signer_name": datos["titular"],
            "signer_id_number": datos["identificacion"],
            "signer_issuer": datos["emisor"],
            "signer_cert_serial": datos["serie"],
            "signer_cert_sha256": datos["sha256"],
            "signer_cert_valid_until": datos["hasta"].replace(tzinfo=None),
            "signature_level": "B-T" if tsa else "B-B",
            "signed_pdf_sha256": huella_pdf,
        })
        adjunto = self.env["ir.attachment"].sudo().create({
            "name": rec.informe_nombre_archivo(firmado=True),
            "type": "binary",
            "raw": firmado,
            "mimetype": "application/pdf",
            "res_model": "shrimp.verification.firma",
            "res_id": firma.id,
            "public": False,
        })
        firma.write({"attachment_id": adjunto.id})
        rec.write({
            "firma_id": firma.id,
            "signed_at": ahora,
            "signed_by_user_id": user.id,
            "signer_name": datos["titular"],
            "signer_id_number": datos["identificacion"],
            "signer_issuer": datos["emisor"],
            "signer_cert_serial": datos["serie"],
            "signer_cert_sha256": datos["sha256"],
            "signed_pdf_sha256": huella_pdf,
            "signed_pdf_attachment_id": adjunto.id,
        })
        rec._message_log(body=_(
            "Informe firmado electrónicamente por %(quien)s (%(emisor)s), usuario "
            "%(usuario)s. Huella SHA-256 del PDF: %(huella)s.") % {
                "quien": datos["titular"], "emisor": datos["emisor"],
                "usuario": user.name, "huella": huella_pdf})
        # Con la firma obligatoria las partes no podían aceptar mientras
        # tanto: el plazo para responder corre desde la firma.
        if rec._firma_obligatoria() and rec.acceptance_state == "waiting":
            rec._abrir_plazo()
        return firma

    def _firma_autocomprobar(self, firmado, cert):
        """Comprueba el PDF recién firmado antes de guardarlo: firma íntegra,
        criptográficamente válida, que cubre el archivo entero y hecha con el
        certificado que se subió. Solo integridad: la confianza en la cadena
        (y en la TSA) la evalúa quien valida, con sus raíces (FirmaEC, Adobe)."""
        try:
            from cryptography.hazmat.primitives import hashes
            from pyhanko.pdf_utils.reader import PdfFileReader
            from pyhanko.sign.validation.generic_cms import validate_sig_integrity
            from pyhanko.sign.validation.status import SignatureCoverageLevel

            firma = PdfFileReader(io.BytesIO(firmado)).embedded_signatures[-1]
            firma.compute_integrity_info(skip_diff=True)
            intacta, valida = validate_sig_integrity(
                firma.signer_info, firma.signer_cert, expected_content_type="data",
                actual_digest=firma.external_digest)
            ok = (intacta and valida
                  and firma.coverage == SignatureCoverageLevel.ENTIRE_FILE
                  and firma.signer_cert.sha256 == cert.fingerprint(hashes.SHA256()))
        except Exception:  # noqa: BLE001
            _logger.warning("Autocomprobación de la firma fallida", exc_info=True)
            ok = False
        if not ok:
            raise pades.FirmaError(_(
                "El informe no quedó bien firmado. Inténtalo de nuevo; si se "
                "repite, avisa a la plataforma."))

    # ==================================================================
    # Reapertura: la firma anterior queda archivada
    # ==================================================================
    def _firma_archivar(self, motivo):
        for rec in self.sudo():
            firma = rec.firma_id
            if not firma:
                continue
            if firma.state == "vigente":
                firma.write({"state": "archivada", "archived_at": fields.Datetime.now(),
                             "archived_reason": motivo})
            rec.write({
                "firma_id": False, "signed_at": False, "signed_by_user_id": False,
                "signer_name": False, "signer_id_number": False, "signer_issuer": False,
                "signer_cert_serial": False, "signer_cert_sha256": False,
                "signed_pdf_sha256": False, "signed_pdf_attachment_id": False,
            })
            rec._message_log(body=_(
                "La firma electrónica del informe quedó archivada: %s") % motivo)

    def write(self, vals):
        reabiertas = self.browse()
        if "state" in vals and vals["state"] not in VEREDICTOS:
            reabiertas = self.filtered(lambda r: r.sudo().firma_id)
        res = super().write(vals)
        if reabiertas:
            reabiertas._firma_archivar(_(
                "la verificación salió del veredicto firmado (nuevo estado: %s).")
                % dict(self._fields["state"]._description_selection(self.env)).get(
                    vals["state"], vals["state"]))
        return res
