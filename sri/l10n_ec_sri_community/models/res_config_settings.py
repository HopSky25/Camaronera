# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Expone la configuración del SRI (que se almacena por compañía en
    res.company) dentro de Ajustes -> Configuración general. Son campos
    'related' con readonly=False, así que editarlos aquí escribe en la
    compañía activa del selector de Ajustes."""

    _inherit = 'res.config.settings'

    ec_sri_enabled = fields.Boolean(
        related='company_id.ec_sri_enabled', readonly=False,
        string='Habilitar SRI Community')
    ec_sri_environment = fields.Selection(
        related='company_id.ec_sri_environment', readonly=False,
        string='Ambiente SRI')
    ec_sri_production_ready = fields.Boolean(
        related='company_id.ec_sri_production_ready', readonly=False,
        string='Pruebas de homologación completadas')
    ec_sri_regime = fields.Selection(
        related='company_id.ec_sri_regime', readonly=False,
        string='Régimen tributario')
    ec_sri_accounting = fields.Boolean(
        related='company_id.ec_sri_accounting', readonly=False,
        string='Obligado a llevar contabilidad')
    ec_sri_trade_name = fields.Char(
        related='company_id.ec_sri_trade_name', readonly=False,
        string='Nombre comercial')
    ec_sri_address = fields.Char(
        related='company_id.ec_sri_address', readonly=False,
        string='Dirección matriz SRI')
    ec_sri_special = fields.Char(
        related='company_id.ec_sri_special', readonly=False,
        string='Número contribuyente especial')
    ec_sri_agent = fields.Char(
        related='company_id.ec_sri_agent', readonly=False,
        string='Resolución agente de retención')
    ec_sri_provider_ruc = fields.Char(
        related='company_id.ec_sri_provider_ruc', readonly=False,
        string='RUC proveedor del sistema')
    ec_sri_large_taxpayer = fields.Char(
        related='company_id.ec_sri_large_taxpayer', readonly=False,
        string='Resolución gran contribuyente')
    ec_sri_establishments = fields.Integer(
        related='company_id.ec_sri_establishments', readonly=False,
        string='Establecimientos registrados en RUC')
    ec_sri_secret_name = fields.Char(
        related='company_id.ec_sri_secret_name', readonly=False,
        string='Prefijo de variables del certificado')
