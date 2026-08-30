# -*- coding: utf-8 -*-
import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


def _normalize_text(value):
    value = (value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def _normalize_name(value):
    return _normalize_text(value).lower()


def _normalize_email(value):
    return (value or "").strip().lower()


def _normalize_vat(value):
    value = (value or "").strip().upper()
    # elimina espacios, guiones, puntos y otros separadores
    value = re.sub(r"[^A-Z0-9]", "", value)
    return value


class ResPartner(models.Model):
    _inherit = ["res.partner", "shrimp.uuid.mixin"]

    shrimp_user_type = fields.Selection(
        [
            ("semillero", "Semillero"),
            ("laboratorio", "Laboratorio"),
            ("camaronera", "Camaronera"),
        ],
        string="Tipo (Shrimp)",
        index=True,
    )

    # Básicos (registro)
    vat_or_id = fields.Char(string="RUC o Cédula", index=True)

    # Campos técnicos para validaciones de unicidad
    x_name_normalized = fields.Char(
        string="Nombre normalizado",
        copy=False,
        index=True,
    )
    x_email_normalized = fields.Char(
        string="Correo normalizado",
        copy=False,
        index=True,
    )
    x_vat_or_id_normalized = fields.Char(
        string="Identificación normalizada",
        copy=False,
        index=True,
    )

    # Laboratorio
    lab_razon_social = fields.Char(string="Razón Social (Lab)")
    lab_global_gap = fields.Boolean(string="GlobalG.A.P. (Lab)")
    lab_social_ship_partner = fields.Boolean(string="Social Ship Partner (Lab)")
    lab_ubicacion = fields.Char(string="Ubicación (Lab)")

    # Camaronera
    farm_razon_social = fields.Char(string="Razón Social (Camaronera)")
    farm_representante = fields.Char(string="Representante Legal")
    farm_telefono = fields.Char(string="Teléfono")
    farm_ubicacion = fields.Char(string="Ubicación (Camaronera)")
    farm_capacidad = fields.Float(string="Capacidad (ton/año)")
    farm_area_ha = fields.Float(string="Área (ha)")

    # =========
    # Certificados: SOLO tabla (líneas) relacionada al partner
    # =========
    certificate_line_ids = fields.One2many(
        "shrimp.user.certificate.line",
        "partner_id",
        string="Certificados",
    )

    # =========
    # Semillero: Fotos / instalaciones
    # =========
    sem_photo_attachment_ids = fields.Many2many(
        "ir.attachment",
        "sem_partner_photo_rel",
        "partner_id",
        "attachment_id",
        string="Fotos (Semillero)",
    )
    sem_facility_photo_attachment_ids = fields.Many2many(
        "ir.attachment",
        "sem_partner_facility_photo_rel",
        "partner_id",
        "attachment_id",
        string="Fotos Instalaciones (Semillero)",
    )

    def init(self):
        # Unicidad SOLO para partners del marketplace (con shrimp_user_type).
        # Índices únicos PARCIALES: no afectan a los contactos estándar de Odoo
        # (CRM, ventas, compras...), que sí pueden repetir nombre/correo/RUC.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS shrimp_partner_uniq_name_normalized
            ON res_partner (x_name_normalized)
            WHERE shrimp_user_type IS NOT NULL AND x_name_normalized IS NOT NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS shrimp_partner_uniq_email_normalized
            ON res_partner (x_email_normalized)
            WHERE shrimp_user_type IS NOT NULL AND x_email_normalized IS NOT NULL;

            CREATE UNIQUE INDEX IF NOT EXISTS shrimp_partner_uniq_vat_normalized
            ON res_partner (x_vat_or_id_normalized)
            WHERE shrimp_user_type IS NOT NULL AND x_vat_or_id_normalized IS NOT NULL;
        """)

    def _prepare_normalized_vals(self, vals):
        vals = dict(vals)

        if "name" in vals:
            clean_name = _normalize_text(vals.get("name"))
            vals["name"] = clean_name or False
            vals["x_name_normalized"] = _normalize_name(clean_name) or False

        if "email" in vals:
            clean_email = _normalize_email(vals.get("email"))
            vals["email"] = clean_email or False
            vals["x_email_normalized"] = clean_email or False

        if "vat_or_id" in vals:
            raw_vat = (vals.get("vat_or_id") or "").strip()
            vals["vat_or_id"] = raw_vat or False
            vals["x_vat_or_id_normalized"] = _normalize_vat(raw_vat) or False

        # Limpieza de textos adicionales
        text_fields = [
            "lab_razon_social",
            "lab_ubicacion",
            "farm_razon_social",
            "farm_representante",
            "farm_telefono",
            "farm_ubicacion",
        ]
        for field_name in text_fields:
            if field_name in vals:
                vals[field_name] = _normalize_text(vals.get(field_name)) or False

        return vals

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._prepare_normalized_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        vals = self._prepare_normalized_vals(vals)
        return super().write(vals)

    @api.constrains("name", "shrimp_user_type")
    def _check_name_required(self):
        for rec in self:
            if not rec.shrimp_user_type:
                continue
            if not rec.name or not _normalize_text(rec.name):
                raise ValidationError(_("El nombre es obligatorio."))

    @api.constrains("email", "shrimp_user_type")
    def _check_email_constraints(self):
        email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        for rec in self:
            if not rec.shrimp_user_type:
                continue
            if not rec.email:
                raise ValidationError(_("El correo electrónico es obligatorio."))
            if not email_re.match(rec.email.strip()):
                raise ValidationError(_("El correo electrónico no tiene un formato válido."))

    @api.constrains("vat_or_id", "shrimp_user_type")
    def _check_vat_or_id_constraints(self):
        for rec in self:
            if not rec.shrimp_user_type:
                continue
            if not rec.vat_or_id or not _normalize_vat(rec.vat_or_id):
                raise ValidationError(_("El RUC o cédula es obligatorio."))

    @api.constrains(
        "shrimp_user_type",
        "lab_razon_social",
        "lab_ubicacion",
        "farm_razon_social",
        "farm_representante",
        "farm_telefono",
        "farm_ubicacion",
        "farm_capacidad",
        "farm_area_ha",
    )
    def _check_required_fields_by_type(self):
        for rec in self:
            if rec.shrimp_user_type == "laboratorio":
                if not rec.lab_razon_social:
                    raise ValidationError(_("La razón social del laboratorio es obligatoria."))
                if not rec.lab_ubicacion:
                    raise ValidationError(_("La ubicación del laboratorio es obligatoria."))

            elif rec.shrimp_user_type == "camaronera":
                if not rec.farm_razon_social:
                    raise ValidationError(_("La razón social de la camaronera es obligatoria."))
                if not rec.farm_representante:
                    raise ValidationError(_("El representante legal es obligatorio."))
                if not rec.farm_telefono:
                    raise ValidationError(_("El teléfono es obligatorio."))
                if not rec.farm_ubicacion:
                    raise ValidationError(_("La ubicación de la camaronera es obligatoria."))
                if rec.farm_capacidad and rec.farm_capacidad < 0:
                    raise ValidationError(_("La capacidad no puede ser negativa."))
                if rec.farm_area_ha and rec.farm_area_ha < 0:
                    raise ValidationError(_("El área no puede ser negativa."))

            elif rec.shrimp_user_type == "semillero":
                # Aquí no obligo fotos desde modelo porque a veces el partner puede crearse
                # primero y subir archivos después. Si quieres endurecerlo, se puede activar.
                pass