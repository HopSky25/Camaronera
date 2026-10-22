from odoo import _, fields, models


class ShrimpVerificationFirma(models.Model):
    """Una firma electrónica del informe de verificación.

    Es el registro de la firma, no el certificado: guarda lo que se puede
    publicar del certificado (titular, identificación, emisor, serie, huella)
    y el PDF firmado. La clave privada y la contraseña nunca llegan aquí.

    Una verificación tiene a lo sumo una firma VIGENTE. Si la verificación se
    reabre (sale de los estados con veredicto), la firma pasa a «archivada»:
    sigue existiendo, con su PDF y su página pública, pero ya no es la que vale.
    """

    _name = "shrimp.verification.firma"
    _description = "Firma electrónica del informe de verificación"
    _order = "signed_at desc, id desc"
    _rec_name = "signer_name"

    verification_id = fields.Many2one(
        "shrimp.verification", string="Verificación", required=True,
        ondelete="cascade", index=True, readonly=True)
    token = fields.Char(
        string="Token público", required=True, readonly=True, copy=False, index=True,
        help="Identificador aleatorio de la página pública de comprobación "
             "(/signature/<token>). No es el id del registro.")
    state = fields.Selection(
        [("vigente", "Vigente"), ("archivada", "Archivada")],
        string="Estado", default="vigente", required=True, readonly=True, index=True)

    signed_at = fields.Datetime(string="Firmado el", readonly=True, required=True)
    signed_by_user_id = fields.Many2one(
        "res.users", string="Firmado por (usuario)", readonly=True, ondelete="restrict")
    signer_partner_id = fields.Many2one(
        "res.partner", string="Firmado por (contacto)", readonly=True, ondelete="restrict")
    signer_name = fields.Char(string="Titular del certificado", readonly=True)
    signer_id_number = fields.Char(string="Cédula / RUC del certificado", readonly=True)
    signer_issuer = fields.Char(string="Emisor del certificado", readonly=True)
    signer_chain_verified = fields.Boolean(
        string="Cadena del certificado comprobada", readonly=True,
        help="La cadena del certificado se comprobó hasta una raíz de confianza de la "
             "plataforma (shrimp_firma.trust_roots_dir). Si no, titular y emisor son solo "
             "lo que el certificado declara de sí mismo y se muestran «sin verificar».")
    signer_cert_serial = fields.Char(string="Serie del certificado", readonly=True)
    signer_cert_sha256 = fields.Char(string="Huella SHA-256 del certificado", readonly=True)
    signer_cert_valid_until = fields.Datetime(string="Certificado válido hasta", readonly=True)
    signature_level = fields.Selection(
        [("B-B", "PAdES-B-B"), ("B-T", "PAdES-B-T (con sello de tiempo)")],
        string="Nivel de firma", readonly=True)
    signed_pdf_sha256 = fields.Char(string="Huella SHA-256 del PDF firmado", readonly=True)
    attachment_id = fields.Many2one(
        "ir.attachment", string="PDF firmado", readonly=True, ondelete="restrict")

    archived_at = fields.Datetime(string="Archivada el", readonly=True)
    archived_reason = fields.Char(string="Motivo del archivo", readonly=True)

    _token_unique = models.Constraint(
        "UNIQUE(token)", "El token de comprobación de la firma debe ser único.")

    def public_path(self):
        self.ensure_one()
        return "/signature/%s" % self.token

    def pdf_path(self):
        self.ensure_one()
        return "/signature/%s/pdf" % self.token

    def texto_resumen(self):
        """La frase que se muestra a las partes, al público y en el chatter."""
        self.ensure_one()
        valores = {"quien": self.signer_name, "emisor": self.signer_issuer,
                   "fecha": self.fecha_local()}
        if self.signer_chain_verified:
            return _("Informe firmado electrónicamente por %(quien)s (%(emisor)s) el "
                     "%(fecha)s") % valores
        return _("Informe firmado electrónicamente el %(fecha)s. Firmante declarado en el "
                 "certificado (sin verificar): %(quien)s; emisor declarado: %(emisor)s") % valores

    def fecha_local(self):
        """«13/10/2026 10:15 (UTC-05:00)» en la hora local de la verificadora."""
        self.ensure_one()
        return self.verification_id._firma_fecha_local(self.signed_at)
