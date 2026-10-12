import re

from odoo import api, models

# Reglas de Ecuador para el tipo de identificación (tabla 6 del SRI):
#   13 dígitos terminados en 001  -> RUC
#   10 dígitos                    -> cédula
#   cualquier otra cosa           -> pasaporte
_RE_RUC = re.compile(r"^\d{10}001$|^\d{13}$")
_RE_CEDULA = re.compile(r"^\d{10}$")


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def _shrimp_identification_type_for(self, vat):
        vat = (vat or "").strip()
        if not vat:
            return self.env["l10n_latam.identification.type"]
        if vat == "9" * 13:
            # Consumidor final: el SRI lo reconoce por el número.
            return self.env.ref("l10n_ec.ec_ruc", raise_if_not_found=False)
        if _RE_RUC.match(vat):
            return self.env.ref("l10n_ec.ec_ruc", raise_if_not_found=False)
        if _RE_CEDULA.match(vat):
            return self.env.ref("l10n_ec.ec_dni", raise_if_not_found=False)
        return (self.env.ref("l10n_ec.ec_passport", raise_if_not_found=False)
                or self.env.ref("l10n_latam_base.it_pass", raise_if_not_found=False))

    @api.model
    def _shrimp_sri_identification_code_for(self, vat):
        """Código del SRI (tabla 6) que lee l10n_ec_sri_community al emitir
        (``ec_sri_identification_type``): 04 RUC, 05 cédula, 06 pasaporte,
        07 consumidor final."""
        vat = (vat or "").strip()
        if not vat:
            return False
        if vat == "9" * 13:
            return "07"
        if _RE_RUC.match(vat):
            return "04"
        if _RE_CEDULA.match(vat):
            return "05"
        return "06"

    def _shrimp_sync_identification_type(self):
        """Pone el tipo de identificación que corresponde al número, en los
        dos campos: el de l10n_latam (decide qué documentos se permiten) y el
        del módulo SRI (va en el XML).

        Sin validar el dígito verificador: hay RUC nuevos que no siguen el
        algoritmo y el SRI solo lo advierte (el módulo SRI lo avisa al
        emitir). En el código SRI, un número que no es RUC, cédula ni
        consumidor final solo se rellena si está vacío: «pasaporte» e
        «identificación del exterior» no se distinguen por el número."""
        for rec in self.sudo().with_context(no_vat_validation=True):
            if not rec.shrimp_user_type or not rec.vat:
                continue
            vals = {}
            tipo = self._shrimp_identification_type_for(rec.vat)
            if tipo and rec.l10n_latam_identification_type_id != tipo:
                vals["l10n_latam_identification_type_id"] = tipo.id
            codigo = self._shrimp_sri_identification_code_for(rec.vat)
            if codigo and rec.ec_sri_identification_type != codigo \
                    and (codigo != "06" or not rec.ec_sri_identification_type):
                vals["ec_sri_identification_type"] = codigo
            if vals:
                rec.write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.filtered(lambda r: r.shrimp_user_type and r.vat)._shrimp_sync_identification_type()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "vat" in vals and "l10n_latam_identification_type_id" not in vals \
                and not self.env.context.get("shrimp_syncing_identification"):
            self.with_context(shrimp_syncing_identification=True).filtered(
                lambda r: r.shrimp_user_type and r.vat)._shrimp_sync_identification_type()
        return res
