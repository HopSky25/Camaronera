"""«Mis certificados» de la cuenta, para el portal y la API.

Vivía en el controlador de Mi cuenta (account_portal.py: my_certificate_add,
_renew y _available_certificates). La app los gestiona por la API con las
mismas reglas, así que el núcleo está aquí:

* el catálogo que puede subir la cuenta: el de TODOS sus perfiles (aprobados
  o pendientes) más los de «todos»;
* subir uno nuevo o renovarlo deja la línea pendiente de revisión: la
  plataforma la aprueba antes de que sirva para ligarla a un lote.
"""

from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ResPartnerAccountCertificates(models.Model):
    _inherit = "res.partner"

    def shrimp_certificate_catalog(self):
        """Tipos de certificado que puede subir esta cuenta."""
        self.ensure_one()
        roles = self.sudo()._shrimp_roles(include_pending=True)
        return self.env["shrimp.certificate"].sudo().search([
            ("active", "=", True), ("role", "in", roles + ["all"])], order="name asc")

    def shrimp_account_certificates(self):
        self.ensure_one()
        return self.env["shrimp.user.certificate.line"].sudo().search(
            [("partner_id", "=", self.id)], order="id desc")

    def _shrimp_account_cert_attachment(self, content, mimetype, filename, catalogo):
        return self.env["ir.attachment"].sudo().create({
            "name": "cert_%s_%s" % (catalogo.id, filename),
            "type": "binary",
            "raw": content,
            "mimetype": mimetype,
            "res_model": "res.partner",
            "res_id": self.id,
            "public": False,
        })

    def shrimp_add_account_certificate(self, catalogo, attachment, number=False, issue_date=False,
                                       expiry_date=False):
        """Certificado nuevo de la cuenta (pendiente de revisión)."""
        self.ensure_one()
        if catalogo not in self.shrimp_certificate_catalog():
            raise ValidationError(_("«%s» no es un certificado de tus perfiles.") % catalogo.name)
        return self.env["shrimp.user.certificate.line"].sudo().create({
            "partner_id": self.id,
            "certificate_id": catalogo.id,
            "certificate_number": number or False,
            "issue_date": issue_date or False,
            "expiry_date": expiry_date or False,
            "file_attachment_id": attachment.id,
            "status": "pending",
        })

    def shrimp_renew_account_certificate(self, line, number=False, issue_date=False,
                                         expiry_date=False, attachment=None):
        """Renueva (número, fechas y, si viene, el archivo): vuelve a revisión."""
        self.ensure_one()
        line = line.sudo()
        if line.partner_id != self:
            raise ValidationError(_("Ese certificado no es de tu cuenta."))
        vals = {
            "certificate_number": number or False,
            "issue_date": fields.Date.to_date(issue_date) or False,
            "expiry_date": fields.Date.to_date(expiry_date) or False,
            "status": "pending",
        }
        if attachment:
            vals["file_attachment_id"] = attachment.id
        line.write(vals)
        return line
