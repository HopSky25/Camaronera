# -*- coding: utf-8 -*-
"""Catálogos fiscales/geográficos del SRI portados desde la suite
odoo_saas_ecuador (l10n_ec_base) hacia nuestra convención ec.sri.*.

Son datos de referencia de solo lectura, precargados por data. Nuestro
módulo es la fuente de verdad: aquí solo se incorporan catálogos que NO
teníamos (provincias, cantones, tipos de contribuyente, códigos de
sustento). Las formas de pago y el tipo de identificación se mantienen
en su implementación actual (ec.sri.catalog / res.partner)."""

from odoo import fields, models


class EcSriCanton(models.Model):
    # Los cantones no existen como tabla nativa (res.city de EC viene vacío),
    # así que se mantienen. La provincia sí es nativa: se referencia a
    # res.country.state (códigos 01-24 idénticos a los del SRI).
    _name = 'ec.sri.canton'
    _description = 'Cantón (SRI/INEC)'
    _order = 'province_id, name'

    code = fields.Char(string='Código', required=True, size=4, index=True)
    name = fields.Char(string='Nombre', required=True, translate=True)
    province_id = fields.Many2one('res.country.state', string='Provincia',
                                  required=True, ondelete='cascade',
                                  domain="[('country_id.code', '=', 'EC')]")
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint('UNIQUE(code)', 'El código de cantón debe ser único.')


class EcSriContributorType(models.Model):
    _name = 'ec.sri.contributor.type'
    _description = 'Tipo de contribuyente SRI'
    _order = 'code'

    code = fields.Char(string='Código', required=True, index=True)
    name = fields.Char(string='Nombre', required=True, translate=True)
    obligado_contabilidad = fields.Boolean(string='Obligado a contabilidad')
    retention_agent = fields.Boolean(string='Agente de retención')
    special_contributor = fields.Boolean(string='Contribuyente especial')
    rimpe = fields.Boolean(string='Régimen RIMPE')
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint('UNIQUE(code)', 'El código de tipo de contribuyente debe ser único.')


class EcSriTaxSupport(models.Model):
    _name = 'ec.sri.tax.support'
    _description = 'Código de sustento tributario (ATS)'
    _order = 'code'

    code = fields.Char(string='Código', required=True, index=True)
    name = fields.Char(string='Descripción', required=True, translate=True)
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint('UNIQUE(code)', 'El código de sustento debe ser único.')
