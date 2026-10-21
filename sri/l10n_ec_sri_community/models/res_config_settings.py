# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Expone la configuración del SRI (que se almacena por compañía en
    res.company) dentro de Ajustes -> Configuración general. Son campos
    'related' con readonly=False, así que editarlos aquí escribe en la
    compañía activa del selector de Ajustes. El certificado es la excepción:
    ver los campos ec_sri_cert_* más abajo."""

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
    # F1: el .p12, su nombre y su contraseña NO son 'related'. Odoo escribe cada
    # campo related por separado (orm/fields.py, _inverse_related) y la compañía
    # recibía el archivo sin la contraseña: «Ingrese la contraseña...» siempre.
    # Son campos NO almacenados con un ÚNICO inverse: Odoo agrupa los inverse por
    # método y lo llama una sola vez con los tres valores, que llegan juntos a
    # res.company.write() (allí se valida, se cifra y no se guarda nada en claro).
    # Al no almacenarse, tampoco quedan en la tabla de Ajustes, que es transitoria
    # pero vive en la base hasta que el vacuum la limpia.
    ec_sri_cert_file = fields.Binary(
        string='Subir certificado .p12', compute='_compute_ec_sri_cert_upload',
        inverse='_inverse_ec_sri_cert_upload', readonly=False, store=False,
        groups='base.group_system')
    ec_sri_cert_filename = fields.Char(
        compute='_compute_ec_sri_cert_upload', inverse='_inverse_ec_sri_cert_upload',
        readonly=False, store=False, groups='base.group_system')
    ec_sri_cert_password = fields.Char(
        string='Contraseña del certificado', compute='_compute_ec_sri_cert_upload',
        inverse='_inverse_ec_sri_cert_upload', readonly=False, store=False,
        groups='base.group_system')
    ec_sri_cert_subject = fields.Char(
        related='company_id.ec_sri_cert_subject', string='Certificado cargado')
    ec_sri_cert_expiry = fields.Date(
        related='company_id.ec_sri_cert_expiry', string='Vence')

    def _compute_ec_sri_cert_upload(self):
        # Siempre vacíos al leer: el certificado guardado solo existe cifrado y
        # no se devuelve nunca (ni el archivo ni la contraseña).
        for rec in self:
            rec.ec_sri_cert_file = False
            rec.ec_sri_cert_filename = False
            rec.ec_sri_cert_password = False

    def _inverse_ec_sri_cert_upload(self):
        for rec in self:
            if not rec.ec_sri_cert_file:
                continue
            # Una sola escritura con archivo + contraseña: Company.write() los saca
            # de vals, valida el .p12 y guarda solo el material cifrado.
            vals = {'ec_sri_cert_file': rec.ec_sri_cert_file,
                    'ec_sri_cert_password': rec.ec_sri_cert_password}
            if rec.ec_sri_cert_filename:
                vals['ec_sri_cert_filename'] = rec.ec_sri_cert_filename
            rec.company_id.write(vals)
        # Fuera de la caché también: la contraseña en claro no debe sobrevivir a la escritura.
        self.invalidate_recordset(['ec_sri_cert_file', 'ec_sri_cert_filename', 'ec_sri_cert_password'])
