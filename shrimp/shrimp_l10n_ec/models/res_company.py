import re

from odoo import _, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Con qué se emiten las facturas de SERVICIO de la plataforma. Con el SRI
    # activo son OBLIGATORIOS: sin ellos los cobros se quedan «Por facturar»
    # con el motivo a la vista (antes se usaba en silencio el diario de ventas
    # por defecto, que no emite documentos del SRI).
    shrimp_service_journal_id = fields.Many2one(
        "account.journal", string="Diario de facturas de servicio",
        domain="[('type', '=', 'sale'), ('company_id', '=', id), "
               "('l10n_latam_use_documents', '=', True)]",
        help="Diario de ventas (con «Usar documentos» activado) para las facturas de "
             "comisión, verificación y empaque que emite la plataforma. Obligatorio "
             "con el SRI activo.")
    shrimp_service_sri_point_id = fields.Many2one(
        "ec.sri.point", string="Punto de emisión de la plataforma",
        domain="[('company_id', '=', id)]",
        help="Punto de emisión SRI (001-00X) de las facturas y notas de crédito de "
             "servicio. Tiene que emitir los documentos 01 (Factura) y 04 (Nota de "
             "crédito) y estar en el ambiente SRI de la compañía. Obligatorio con el "
             "SRI activo.")
    shrimp_service_payment_id = fields.Many2one(
        "l10n_ec.sri.payment", string="Forma de pago SRI de los servicios",
        help="Forma de pago que se declara en la factura (p. ej. 20 - Otros con "
             "utilización del sistema financiero). Obligatoria con el SRI activo.")
    shrimp_sri_auto_emit = fields.Boolean(
        string="Enviar al SRI al emitir", default=True,
        help="Al contabilizar una factura de servicio se crea su comprobante "
             "electrónico y se encola para el SRI (el envío lo hace el cron del "
             "módulo SRI). Si falla, la factura queda contabilizada y el aviso "
             "en su historial.")

    def _shrimp_sri_config_problems(self):
        """Qué falta para que la plataforma pueda emitir sus facturas de
        servicio electrónicas. Lista vacía = todo listo.

        Con el SRI desactivado en la compañía no se exige nada (la factura se
        contabiliza sin comprobante electrónico, como cualquier diario sin
        documentos)."""
        self.ensure_one()
        company = self.sudo()
        if not company.ec_sri_enabled:
            return []
        problemas = []
        if not re.fullmatch(r"[0-9]{13}", company.vat or ""):
            problemas.append(_("La compañía %s no tiene RUC de 13 dígitos.") % company.name)
        if not company.ec_sri_address:
            problemas.append(_("Falta la dirección matriz SRI de la compañía."))
        journal = company.shrimp_service_journal_id
        if not journal:
            problemas.append(_(
                "Falta el diario de facturas de servicio (Ajustes › CamaronMarket › "
                "Facturas de servicio de la plataforma)."))
        elif journal.type != "sale" or journal.company_id != company:
            problemas.append(_("El diario %s no es un diario de ventas de la compañía.")
                             % journal.display_name)
        elif not journal.l10n_latam_use_documents:
            problemas.append(_(
                "El diario %s no usa documentos del SRI («Usar documentos» desactivado).")
                % journal.display_name)
        point = company.shrimp_service_sri_point_id
        if not point:
            problemas.append(_(
                "Falta el punto de emisión de la plataforma (Ajustes › CamaronMarket › "
                "Facturas de servicio de la plataforma)."))
        else:
            if point.company_id != company or not point.active:
                problemas.append(_("El punto de emisión %s no está activo en la compañía.")
                                 % point.display_name)
            if point.environment != company.ec_sri_environment:
                problemas.append(_(
                    "El punto de emisión %(p)s está en otro ambiente SRI que la compañía "
                    "(%(a)s).") % {"p": point.display_name,
                                   "a": dict(company._fields["ec_sri_environment"].selection)
                                   .get(company.ec_sri_environment)})
            docs = set(point.line_ids.mapped("document_type"))
            for code, label in (("01", _("01 Factura")), ("04", _("04 Nota de crédito"))):
                if code not in docs:
                    problemas.append(_(
                        "El punto de emisión %(p)s no emite el documento %(d)s: agréguelo "
                        "en «Documentos que emite».") % {"p": point.display_name, "d": label})
        # El IVA de los servicios tiene que llevar los códigos del catálogo del
        # SRI (tabla 16/17); sin ellos el XML no se puede armar.
        # Se revisan los impuestos con los que saldría de verdad cada línea
        # (los del producto o, si no tiene, el IVA de servicios de la
        # compañía), no el impuesto por defecto a secas.
        Charge = self.env["shrimp.charge"].sudo()
        impuestos = self.env["account.tax"]
        hay_productos = False
        for xmlid, _n, _c in Charge._service_product_specs().values():
            producto = self.env.ref(xmlid, raise_if_not_found=False)
            if producto:
                hay_productos = True
                del_producto = Charge._service_taxes_for(producto, company)
                if not del_producto:
                    problemas.append(_("El servicio «%s» no tiene IVA en la compañía y no hay "
                                       "IVA de servicios por defecto.") % producto.display_name)
                impuestos |= del_producto
        if not hay_productos:
            impuestos = Charge._service_vat_fallback(company)
            if not impuestos:
                problemas.append(_("La compañía no tiene impuesto de venta por defecto (IVA) "
                                   "para los servicios."))
        for tax in impuestos.filtered(lambda t: t.amount >= 0 and not (
                t.ec_sri_tax_code and t.ec_sri_percentage_code)):
            problemas.append(_(
                "El impuesto %s no tiene «Impuesto XML SRI» ni «Código porcentaje SRI» "
                "(Contabilidad › Configuración › Impuestos).") % tax.display_name)
        if not company.shrimp_service_payment_id:
            problemas.append(_("Falta la forma de pago SRI de los servicios."))
        if company.shrimp_sri_auto_emit:
            if not company.ec_sri_cert_data:
                problemas.append(_(
                    "No hay certificado de firma electrónica (.p12) cargado en la compañía."))
            elif company.ec_sri_cert_expiry and company.ec_sri_cert_expiry < fields.Date.today():
                problemas.append(_("El certificado de firma electrónica venció el %s.")
                                 % company.ec_sri_cert_expiry)
        return problemas
