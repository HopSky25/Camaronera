# -*- coding: utf-8 -*-
"""Datos maestros de transporte para la Guía de Remisión (comprobante 06),
portados desde odoo_saas_ecuador (l10n_ec.driver / l10n_ec.vehicle) a nuestra
convención. Son datos operativos que el usuario mantiene (no de solo lectura),
usados como fuente estructurada del transportista y la placa en la guía."""

from odoo import fields, models


class EcSriDriver(models.Model):
    _name = 'ec.sri.driver'
    _description = 'Transportista (Guía de Remisión SRI)'
    _order = 'name'

    name = fields.Char(string='Nombre / Razón social', required=True)
    identification_type = fields.Selection(
        [('05', 'Cédula'), ('04', 'RUC'), ('06', 'Pasaporte')],
        string='Tipo de identificación', default='05', required=True)
    identification_number = fields.Char(string='Número de identificación', required=True)
    license_number = fields.Char(string='Licencia de conducir')
    active = fields.Boolean(default=True)


class EcSriVehicle(models.Model):
    _name = 'ec.sri.vehicle'
    _description = 'Vehículo de transporte (Guía de Remisión SRI)'
    _order = 'name'

    name = fields.Char(string='Nombre', required=True, help='Ej. Camión 01')
    license_plate = fields.Char(string='Placa', required=True, help='Ej. ABC-1234')
    model_year = fields.Char(string='Año/Modelo')
    brand = fields.Char(string='Marca')
    active = fields.Boolean(default=True)
