from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools.float_utils import float_compare, float_is_zero


class ShrimpVerification(models.Model):
    """Orden de trabajo y a la vez informe de la inspección en campo.

    Cubre los cinco análisis del verificador: peso, cuerpo o cola,
    metabisulfito, clasificación y sabor.
    """

    _name = "shrimp.verification"
    _description = "Verificación de camarón en campo"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Referencia", required=True, copy=False,
        default=lambda self: _("Nuevo"), tracking=True,
    )

    # ------------------------------------------------------------------
    # Partes implicadas
    # ------------------------------------------------------------------
    transaction_id = fields.Many2one(
        "shrimp.transaction", string="Compra", required=True,
        ondelete="cascade", index=True, tracking=True,
    )
    verifier_partner_id = fields.Many2one(
        "res.partner", string="Verificador", required=True,
        ondelete="restrict", index=True, tracking=True,
        domain=[("shrimp_user_type", "=", "verificador")],
    )
    buyer_partner_id = fields.Many2one(
        "res.partner", string="Comprador", related="transaction_id.buyer_partner_id",
        store=True, index=True,
    )
    seller_partner_id = fields.Many2one(
        "res.partner", string="Vendedor", related="transaction_id.seller_partner_id",
        store=True, index=True,
    )
    product_id = fields.Many2one(
        "shrimp.product", string="Producto", related="transaction_id.product_id",
        store=True, index=True,
    )
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id)

    # ------------------------------------------------------------------
    # Contexto de campo (cabecera del parte)
    # ------------------------------------------------------------------
    batch_code = fields.Char(string="Lote", tracking=True, help="Ej: 262326")
    pond_id = fields.Many2one("shrimp.partner.pond", string="Piscina", ondelete="set null")
    pond_label = fields.Char(string="Piscina (texto)", help="Si la piscina no está dada de alta.")
    facility_id = fields.Many2one("shrimp.partner.facility", string="Instalación / sector", ondelete="set null")
    plant_name = fields.Char(string="Planta procesadora", help="Ej: Total Seafood")
    harvest_date = fields.Date(string="Fecha de cosecha")
    process_date = fields.Date(string="Fecha de proceso")

    # Qué se le pide a esta verificación depende del producto: al camarón
    # adulto los cinco análisis; a la larva, cantidad, supervivencia, tamaño y
    # estado sanitario (medir metabisulfito o sabor en un nauplio no tiene sentido).
    scope = fields.Selection(
        [("adult", "Camarón adulto"), ("larvae", "Larva")],
        string="Alcance",
        related="product_id.verification_scope",
        store=True,
        index=True,
    )

    # ------------------------------------------------------------------
    # VERIFICACIÓN DE LARVA
    # ------------------------------------------------------------------
    larvae_qty_verified = fields.Float(
        string="Cantidad verificada", digits=(16, 2),
        help="Cantidad realmente contada o estimada en campo.")
    larvae_survival_rate = fields.Float(
        string="Supervivencia medida (%)", digits=(5, 2), tracking=True)
    larvae_avg_size_mg = fields.Float(
        string="Tamaño promedio medido (mg)", digits=(16, 3), tracking=True)
    larvae_health_status = fields.Selection(
        [
            ("excellent", "Excelente"),
            ("good", "Bueno"),
            ("acceptable", "Aceptable"),
            ("rejected", "Rechazado"),
        ],
        string="Estado sanitario", tracking=True)
    larvae_health_notes = fields.Text(string="Observaciones sanitarias")
    larvae_qty_diff_pct = fields.Float(
        string="Desvío de cantidad (%)", compute="_compute_larvae_diff", store=True,
        digits=(6, 2),
        help="Diferencia entre lo comprado y lo verificado en campo.")
    larvae_survival_diff = fields.Float(
        string="Desvío de supervivencia (pp)", compute="_compute_larvae_diff",
        store=True, digits=(6, 2),
        help="Diferencia en puntos porcentuales frente a lo que publicó el vendedor.")

    # ------------------------------------------------------------------
    # ANÁLISIS 1 — PESO
    # ------------------------------------------------------------------
    weight_sent_lb = fields.Float(string="Peso enviado (lb)", digits=(16, 2), tracking=True)
    weight_plant_lb = fields.Float(string="Peso en planta (lb)", digits=(16, 2), tracking=True)
    trash_lb = fields.Float(string="Basura (lb)", digits=(16, 2))

    overweight_lb = fields.Float(
        string="Sobrepeso (lb)", compute="_compute_weights", store=True, digits=(16, 2),
        help="Peso en planta menos peso enviado.",
    )
    overweight_factor = fields.Float(
        string="Factor de sobrepeso", compute="_compute_weights", store=True, digits=(16, 4),
        help="Peso en planta / peso enviado. Lo normal ronda 1,05 (5 % de sobrepeso). "
             "OJO: es un ratio, no un porcentaje.",
    )
    net_weight_lb = fields.Float(
        string="Peso neto (lb)", compute="_compute_weights", store=True, digits=(16, 2),
        help="Peso en planta menos basura. Es la base sobre la que se calcula el rendimiento.",
    )

    # ------------------------------------------------------------------
    # ANÁLISIS 2 — CUERPO O COLA
    # ------------------------------------------------------------------
    presentation = fields.Selection(
        [("entero", "Entero (cuerpo)"), ("cola", "Cola")],
        string="Presentación", tracking=True,
    )
    presentation_matches_product = fields.Boolean(
        string="Coincide con lo publicado", compute="_compute_presentation_match",
        help="Falso si el vendedor publicó una presentación distinta a la encontrada en campo.",
    )

    # ------------------------------------------------------------------
    # ANÁLISIS 3 — METABISULFITO
    # ------------------------------------------------------------------
    metabisulfite_ppm = fields.Float(string="Metabisulfito (ppm)", digits=(16, 2), tracking=True)
    metabisulfite_limit_ppm = fields.Float(
        string="Límite admitido (ppm)", default=100.0,
        help="Límite por encima del cual el lote se considera no conforme.",
    )
    metabisulfite_result = fields.Selection(
        [("pass", "Conforme"), ("fail", "No conforme"), ("na", "No aplica")],
        string="Resultado metabisulfito", compute="_compute_metabisulfite_result",
        store=True, readonly=False, tracking=True,
    )
    metabisulfite_notes = fields.Text(string="Observaciones de metabisulfito")

    # ------------------------------------------------------------------
    # ANÁLISIS 4 — CLASIFICACIÓN
    # ------------------------------------------------------------------
    line_ids = fields.One2many(
        "shrimp.verification.line", "verification_id", string="Clasificación por talla")

    total_processed_lb = fields.Float(
        string="Total procesado (lb)", compute="_compute_yields", store=True, digits=(16, 2))
    class_a_lb = fields.Float(
        string="Clase A (lb)", compute="_compute_yields", store=True, digits=(16, 2))
    class_b_lb = fields.Float(
        string="Clase B (lb)", compute="_compute_yields", store=True, digits=(16, 2))
    class_c_lb = fields.Float(
        string="Clase C (lb)", compute="_compute_yields", store=True, digits=(16, 2))

    yield_pct = fields.Float(
        string="Rendimiento (%)", compute="_compute_yields", store=True, digits=(5, 2),
        help="Total procesado sobre el peso neto (peso en planta menos basura).",
    )
    yield_class_a_pct = fields.Float(
        string="Rend. Clase A (%)", compute="_compute_yields", store=True, digits=(5, 2))
    yield_class_b_pct = fields.Float(
        string="Rend. Clase B (%)", compute="_compute_yields", store=True, digits=(5, 2))
    yield_class_c_pct = fields.Float(
        string="Rend. Clase C (%)", compute="_compute_yields", store=True, digits=(5, 2))

    # ------------------------------------------------------------------
    # ANÁLISIS 5 — SABOR
    # ------------------------------------------------------------------
    taste_result = fields.Selection(
        [
            ("excellent", "Excelente"),
            ("good", "Bueno"),
            ("acceptable", "Aceptable"),
            ("rejected", "Rechazado"),
        ],
        string="Sabor", tracking=True,
    )
    taste_notes = fields.Text(string="Observaciones de sabor")
    smell_ok = fields.Boolean(string="Olor correcto", default=True)
    color_ok = fields.Boolean(string="Color correcto", default=True)

    # ------------------------------------------------------------------
    # Gramajes y conteos
    # ------------------------------------------------------------------
    grams_farm = fields.Float(string="Gramaje camaronera", digits=(16, 2))
    grams_plant_1 = fields.Float(string="Gramaje planta 1", digits=(16, 2))
    grams_plant_2 = fields.Float(string="Gramaje planta 2", digits=(16, 2))
    grams_variation = fields.Float(
        string="Variación de gramaje", compute="_compute_grams_variation",
        store=True, digits=(16, 2),
        help="Gramaje de camaronera menos el promedio de los gramajes de planta.",
    )
    count_ids = fields.One2many(
        "shrimp.verification.count", "verification_id", string="Conteos")

    # ------------------------------------------------------------------
    # Evidencia e incidencias
    # ------------------------------------------------------------------
    photo_ids = fields.Many2many(
        "ir.attachment", "shrimp_verification_photo_rel", "verification_id", "attachment_id",
        string="Fotos de campo",
    )
    incident_notes = fields.Text(
        string="Incidencias",
        help="Problemas detectados: rotura de cadena de frío, volcamiento, "
             "demoras, mal olor, etc.",
    )
    gps_latitude = fields.Float(string="Latitud", digits=(10, 7))
    gps_longitude = fields.Float(string="Longitud", digits=(10, 7))

    # ------------------------------------------------------------------
    # Flujo
    # ------------------------------------------------------------------
    state = fields.Selection(
        [
            ("assigned", "Asignada"),
            ("in_field", "En campo"),
            ("done", "Informe completo"),
            ("approved", "Aprobada"),
            ("approved_obs", "Aprobada con observaciones"),
            ("rejected", "Rechazada"),
            ("cancelled", "Cancelada"),
        ],
        string="Estado", default="assigned", required=True, index=True, tracking=True,
    )

    assigned_date = fields.Datetime(string="Fecha de asignación", default=fields.Datetime.now, readonly=True)
    field_start_date = fields.Datetime(string="Inicio en campo", readonly=True)
    verified_date = fields.Datetime(string="Fecha de veredicto", readonly=True)
    verdict_notes = fields.Text(string="Conclusión del verificador")

    fee = fields.Monetary(string="Honorario de verificación", currency_field="currency_id")

    is_final = fields.Boolean(compute="_compute_is_final", string="Cerrada")
    buyer_notified = fields.Boolean(string="Comprador avisado", readonly=True, copy=False)

    # ==================================================================
    # Cálculos
    # ==================================================================
    @api.depends("weight_sent_lb", "weight_plant_lb", "trash_lb")
    def _compute_weights(self):
        for rec in self:
            rec.overweight_lb = (rec.weight_plant_lb or 0.0) - (rec.weight_sent_lb or 0.0)
            rec.overweight_factor = (
                (rec.weight_plant_lb / rec.weight_sent_lb)
                if rec.weight_sent_lb else 0.0
            )
            rec.net_weight_lb = max(0.0, (rec.weight_plant_lb or 0.0) - (rec.trash_lb or 0.0))

    @api.depends("line_ids.weight_lb", "line_ids.quality_class", "net_weight_lb")
    def _compute_yields(self):
        for rec in self:
            def _sum(cls):
                return sum(rec.line_ids.filtered(lambda l: l.quality_class == cls).mapped("weight_lb"))

            a, b, c = _sum("a"), _sum("b"), _sum("c")
            total = a + b + c

            rec.class_a_lb, rec.class_b_lb, rec.class_c_lb = a, b, c
            rec.total_processed_lb = total
            # El rendimiento va sobre el peso NETO (planta menos basura), que es
            # como lo calcula el equipo en los partes de planta.
            rec.yield_pct = (100.0 * total / rec.net_weight_lb) if rec.net_weight_lb else 0.0
            rec.yield_class_a_pct = (100.0 * a / total) if total else 0.0
            rec.yield_class_b_pct = (100.0 * b / total) if total else 0.0
            rec.yield_class_c_pct = (100.0 * c / total) if total else 0.0

    @api.depends("larvae_qty_verified", "larvae_survival_rate",
                 "transaction_id.transaction_qty", "product_id.survival_rate")
    def _compute_larvae_diff(self):
        for rec in self:
            comprada = rec.transaction_id.transaction_qty or 0.0
            rec.larvae_qty_diff_pct = (
                100.0 * (rec.larvae_qty_verified - comprada) / comprada
                if comprada and rec.larvae_qty_verified else 0.0)
            publicada = rec.product_id.survival_rate or 0.0
            rec.larvae_survival_diff = (
                rec.larvae_survival_rate - publicada
                if rec.larvae_survival_rate and publicada else 0.0)

    @api.depends("grams_farm", "grams_plant_1", "grams_plant_2")
    def _compute_grams_variation(self):
        for rec in self:
            plant = [g for g in (rec.grams_plant_1, rec.grams_plant_2) if g]
            avg = sum(plant) / len(plant) if plant else 0.0
            rec.grams_variation = (rec.grams_farm - avg) if (rec.grams_farm and avg) else 0.0

    @api.depends("metabisulfite_ppm", "metabisulfite_limit_ppm")
    def _compute_metabisulfite_result(self):
        for rec in self:
            if not rec.metabisulfite_ppm:
                rec.metabisulfite_result = "na"
            elif rec.metabisulfite_limit_ppm and rec.metabisulfite_ppm > rec.metabisulfite_limit_ppm:
                rec.metabisulfite_result = "fail"
            else:
                rec.metabisulfite_result = "pass"

    @api.depends("presentation", "product_id.presentation")
    def _compute_presentation_match(self):
        for rec in self:
            if not rec.presentation or not rec.product_id.presentation:
                rec.presentation_matches_product = True
            else:
                rec.presentation_matches_product = rec.presentation == rec.product_id.presentation

    @api.depends("state")
    def _compute_is_final(self):
        for rec in self:
            rec.is_final = rec.state in ("approved", "approved_obs", "rejected", "cancelled")

    # ==================================================================
    # Validaciones
    # ==================================================================
    @api.constrains("verifier_partner_id")
    def _check_verifier_role(self):
        for rec in self:
            if rec.verifier_partner_id.shrimp_user_type != "verificador":
                raise ValidationError(_("El verificador asignado debe ser un contacto de tipo Verificador."))

    @api.constrains("verifier_partner_id", "buyer_partner_id", "seller_partner_id")
    def _check_verifier_independence(self):
        """El verificador tiene que ser un tercero: si es el propio comprador o
        vendedor, la verificación no garantiza nada."""
        for rec in self:
            if not rec.verifier_partner_id:
                continue
            if rec.verifier_partner_id in (rec.buyer_partner_id, rec.seller_partner_id):
                raise ValidationError(
                    _("El verificador no puede ser el comprador ni el vendedor de la compra."))

    @api.constrains("weight_sent_lb", "weight_plant_lb", "trash_lb")
    def _check_weights(self):
        for rec in self:
            if rec.weight_sent_lb < 0 or rec.weight_plant_lb < 0 or rec.trash_lb < 0:
                raise ValidationError(_("Los pesos no pueden ser negativos."))
            if rec.weight_plant_lb and rec.trash_lb > rec.weight_plant_lb:
                raise ValidationError(_("La basura no puede superar el peso recibido en planta."))

    _uniq_verification_per_tx = models.Constraint(
        "unique(transaction_id)",
        "Esa compra ya tiene una verificación asignada.",
    )

    # ==================================================================
    # Creación
    # ==================================================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "shrimp.verification") or _("VER-000000")
        records = super().create(vals_list)
        for rec in records:
            rec._notify_verifier_assigned()
        return records

    # ==================================================================
    # Acciones del flujo
    # ==================================================================
    def action_start_field(self):
        for rec in self:
            if rec.state != "assigned":
                raise UserError(_("Solo se puede iniciar el trabajo de campo de una verificación asignada."))
            rec.write({"state": "in_field", "field_start_date": fields.Datetime.now()})

    def _missing_report_fields(self):
        """Qué falta para poder emitir un veredicto. Depende del alcance:
        a una larva no se le exigen metabisulfito, sabor ni tallas."""
        self.ensure_one()
        missing = []

        if self.scope == "larvae":
            if not self.larvae_qty_verified:
                missing.append(_("cantidad verificada"))
            if not self.larvae_survival_rate:
                missing.append(_("supervivencia medida"))
            if not self.larvae_avg_size_mg:
                missing.append(_("tamaño promedio medido"))
            if not self.larvae_health_status:
                missing.append(_("estado sanitario"))
            return missing

        if not self.weight_plant_lb:
            missing.append(_("peso en planta"))
        if not self.presentation:
            missing.append(_("presentación (cuerpo o cola)"))
        if not self.metabisulfite_result or self.metabisulfite_result == "na":
            missing.append(_("análisis de metabisulfito"))
        if not self.line_ids:
            missing.append(_("clasificación por tallas"))
        if not self.taste_result:
            missing.append(_("evaluación de sabor"))
        return missing

    def action_mark_done(self):
        for rec in self:
            missing = rec._missing_report_fields()
            if missing:
                raise UserError(
                    _("Faltan datos del informe: %s.") % ", ".join(missing))
            rec.state = "done"

    def _close(self, state, notes=None):
        self.ensure_one()
        if self.state in ("approved", "approved_obs", "rejected", "cancelled"):
            raise UserError(_("Esta verificación ya está cerrada."))
        if state in ("approved", "approved_obs"):
            missing = self._missing_report_fields()
            if missing:
                raise UserError(_("Faltan datos del informe: %s.") % ", ".join(missing))
        vals = {
            "state": state,
            "verified_date": fields.Datetime.now(),
        }
        if notes:
            vals["verdict_notes"] = notes
        self.write(vals)
        self._notify_buyer_verdict()

    def action_approve(self):
        for rec in self:
            rec._close("approved")

    def action_approve_with_observations(self):
        for rec in self:
            rec._close("approved_obs")

    def action_reject(self):
        for rec in self:
            rec._close("rejected")
            # Al rechazar, la compra se cancela y se libera la reserva de stock.
            rec.transaction_id.action_cancel_for_verification()

    def action_cancel(self):
        for rec in self:
            rec.write({"state": "cancelled"})
            rec.transaction_id.action_cancel_for_verification()

    # ==================================================================
    # Avisos
    # ==================================================================
    def _notify_verifier_assigned(self):
        """Deja la orden en la bandeja del verificador: actividad + correo."""
        self.ensure_one()
        verifier_user = self.env["res.users"].sudo().search(
            [("partner_id", "=", self.verifier_partner_id.id)], limit=1)
        if verifier_user:
            try:
                self.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=verifier_user.id,
                    summary=_("Verificar en campo: %s") % (self.product_id.display_name or ""),
                    note=_("Compra %s. Cantidad: %s.") % (
                        self.transaction_id.name, self.transaction_id.transaction_qty),
                )
            except Exception:
                # Un fallo de actividad no debe impedir registrar la compra.
                pass
        self._send_template("shrimp_verification.mail_template_verification_assigned",
                            self.verifier_partner_id.email)

    def _notify_buyer_verdict(self):
        self.ensure_one()
        self._send_template("shrimp_verification.mail_template_verification_verdict",
                            self.buyer_partner_id.email)
        self.sudo().write({"buyer_notified": True})

    def _send_template(self, xmlid, email_to):
        if not email_to:
            return
        try:
            template = self.env.ref(xmlid, raise_if_not_found=False)
            if template:
                template.sudo().send_mail(
                    self.id, force_send=True, email_values={"email_to": email_to})
        except Exception:
            # El correo nunca debe romper el flujo de compra ni el de verificación.
            pass

    # ==================================================================
    # Parte para WhatsApp — mismo formato que ya usa el equipo
    # ==================================================================
    def _fmt(self, value, decimals=2):
        """Formatea al estilo local: punto de millares y coma decimal."""
        txt = f"{value:,.{decimals}f}"
        return txt.replace(",", "\x00").replace(".", ",").replace("\x00", ".")

    def whatsapp_report(self):
        """Devuelve el parte en texto plano, con el mismo formato y orden que el
        equipo ya envía por WhatsApp. Así el módulo no les cambia la costumbre:
        siguen mandando su mensaje, pero calculado y sin errores de dedo."""
        self.ensure_one()
        L = []
        if self.harvest_date:
            L.append(self.harvest_date.strftime("%d/%m/%Y"))
        if self.process_date:
            L.append("*Proceso %s*" % self.process_date.strftime("%d/%m/%Y"))
        if self.plant_name:
            L.append("*%s*" % self.plant_name)

        sector = self.facility_id.name or self.seller_partner_id.name or ""
        if sector:
            L.append(sector)
        if self.batch_code:
            L.append("Lote. %s" % self.batch_code)
        pond = self.pond_label or self.pond_id.name
        if pond:
            L.append("Piscina. %s" % pond)

        L.append("Peso enviado. %s" % self._fmt(self.weight_sent_lb))
        L.append("Peso planta.    %s" % self._fmt(self.weight_plant_lb))
        L.append("basura. %s" % self._fmt(self.trash_lb, 0))
        # El factor se TRUNCA, no se redondea: en los partes reales 1,0575 se
        # escribe 1,05. Redondear daria 1,06 y no cuadraria con lo que envian.
        factor_trunc = int((self.overweight_factor or 0.0) * 100) / 100.0
        L.append("*Sobrp. %s  lbs  %s*" % (
            self._fmt(self.overweight_lb), self._fmt(factor_trunc, 2)))

        if self.presentation:
            L.append("*%s*" % ("cola directa" if self.presentation == "cola" else "entero"))

        labels = {"a": "*Clase A*", "b": "*Clase B*", "c": "*Clase C*"}
        for cls in ("a", "b", "c"):
            rows = self.line_ids.filtered(lambda l: l.quality_class == cls)
            if not rows:
                continue
            L.append(labels[cls])
            for row in rows:
                L.append("%s= %s" % (row.size_code, self._fmt(row.weight_lb)))

        L.append("*Total %s*" % self._fmt(self.total_processed_lb))
        L.append("Rendimiento:\t%s%%" % self._fmt(self.yield_pct))
        L.append("Rend. Clase A.  %s%%" % self._fmt(self.yield_class_a_pct))
        L.append("Rend. Clase B.    %s%%" % self._fmt(self.yield_class_b_pct))

        if self.grams_farm:
            L.append("Grs. Camaronera %s" % self._fmt(self.grams_farm))
        plant_grams = [g for g in (self.grams_plant_1, self.grams_plant_2) if g]
        if plant_grams:
            L.append("Grs. Planta. %s" % ".. ".join(self._fmt(g) for g in plant_grams))
        if self.grams_variation:
            L.append("%s variacion de gramaje." % self._fmt(self.grams_variation))

        if self.count_ids:
            L.append("")
            for c in self.count_ids:
                L.append("Conteo. %s" % self._fmt(c.value, 0))

        if self.incident_notes:
            L.append("")
            L.append("*Nota: %s*" % self.incident_notes.strip())

        return "\n".join(L)

    def action_copy_whatsapp(self):
        """Muestra el parte listo para copiar."""
        self.ensure_one()
        raise UserError(self.whatsapp_report())

    # ==================================================================
    # Evidencia para el PDF
    # ==================================================================
    def photo_data_uris(self, limit=6, max_px=900):
        """Fotos de campo como data-URI, para incrustarlas en el certificado.

        wkhtmltopdf no puede pedir las imágenes por URL (la ruta exige sesión),
        así que se embeben en el propio HTML. Se redimensionan porque una foto
        de móvil son varios MB y el PDF se volvería inmanejable.
        """
        self.ensure_one()
        import base64 as _b64
        import io

        uris = []
        for att in self.photo_ids[:limit]:
            if not att.datas:
                continue
            raw = _b64.b64decode(att.datas)
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(raw))
                img.thumbnail((max_px, max_px))
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                raw, mime = buf.getvalue(), "image/jpeg"
            except Exception:
                # Si Pillow no puede con el formato, se usa el original.
                mime = att.mimetype or "image/jpeg"
            uris.append({
                "name": att.name or "",
                "uri": "data:%s;base64,%s" % (mime, _b64.b64encode(raw).decode()),
            })
        return uris

    # ==================================================================
    # Coherencia del informe (avisos, no bloqueos)
    # ==================================================================
    def report_warnings(self):
        """Incoherencias que merecen una mirada antes de aprobar. No bloquean:
        el verificador es quien decide, el módulo solo señala."""
        self.ensure_one()
        w = []

        if self.scope == "larvae":
            if self.larvae_qty_diff_pct and abs(self.larvae_qty_diff_pct) > 5.0:
                w.append(_("La cantidad verificada difiere un %.2f %% de la comprada.")
                         % self.larvae_qty_diff_pct)
            if self.larvae_survival_diff and self.larvae_survival_diff < -5.0:
                w.append(_("La supervivencia medida está %.2f puntos por debajo de la publicada.")
                         % abs(self.larvae_survival_diff))
            if self.larvae_health_status == "rejected":
                w.append(_("El estado sanitario fue rechazado."))
            if self.larvae_survival_rate and not (0.0 <= self.larvae_survival_rate <= 100.0):
                w.append(_("La supervivencia debe estar entre 0 y 100 %."))
            return w

        if self.weight_sent_lb and self.overweight_factor:
            if self.overweight_factor < 1.0:
                w.append(_("Llegó a planta menos peso del enviado (factor %.4f).") % self.overweight_factor)
            elif self.overweight_factor > 1.15:
                w.append(_("Sobrepeso inusualmente alto (factor %.4f).") % self.overweight_factor)
        if self.net_weight_lb and self.total_processed_lb > self.net_weight_lb:
            w.append(_("El total procesado supera el peso neto: revisa las tallas."))
        if self.yield_pct and not (40.0 <= self.yield_pct <= 90.0):
            w.append(_("Rendimiento fuera del rango habitual (%.2f %%).") % self.yield_pct)
        if self.metabisulfite_result == "fail":
            w.append(_("Metabisulfito por encima del límite (%.2f ppm).") % self.metabisulfite_ppm)
        if not self.presentation_matches_product:
            w.append(_("La presentación encontrada no coincide con la publicada por el vendedor."))
        if self.taste_result == "rejected":
            w.append(_("El sabor fue rechazado."))
        if not self.smell_ok or not self.color_ok:
            w.append(_("Se marcó olor o color fuera de norma."))
        tx_qty = self.transaction_id.transaction_qty or 0.0
        if tx_qty and self.weight_plant_lb and float_compare(
                abs(self.weight_plant_lb - tx_qty), tx_qty * 0.10, precision_digits=2) == 1:
            w.append(_("El peso en planta difiere más de un 10 %% de la cantidad comprada (%s).") % tx_qty)
        return w
