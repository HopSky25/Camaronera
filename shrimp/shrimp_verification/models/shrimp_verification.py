import logging

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError, UserError
from odoo.tools.float_utils import float_compare, float_is_zero

from odoo.addons.shrimp_marketplace.models.shrimp_selection import PRESENTATIONS

_logger = logging.getLogger(__name__)


class ShrimpVerification(models.Model):
    """Orden de trabajo y a la vez informe de la inspección en campo.

    Cubre los cinco análisis del verificador: peso, cuerpo o cola,
    metabisulfito, clasificación y sabor.
    """

    _name = "shrimp.verification"
    _description = "Verificación de camarón en campo"
    # shrimp.notify.mixin aporta _send_template / _log_notificacion /
    # _hay_servidor_de_correo, que antes estaban escritos aquí dentro. Se
    # sacaron a un mixin porque el seguimiento del despacho avisa igual y una
    # copia de esos tres métodos se habría desincronizado a la primera
    # corrección.
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin",
                "shrimp.notify.mixin"]
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
    # ------------------------------------------------------------------
    # MODO DE VERIFICACIÓN (lo elige el comprador al comprar)
    # ------------------------------------------------------------------
    # platform: la de siempre — verificadora acreditada de la plataforma,
    #   técnico, veredicto, honorario y aceptación de las partes.
    # declared: las partes verificaron por su cuenta (una verificadora
    #   externa por convenio, o ellas mismas) y UNA de ellas sube el informe;
    #   la otra lo acepta, rechaza o contraoferta con la misma ronda de
    #   aceptación. Sin verificadora de la plataforma ni honorario.
    # No existe "no aplica": un lote que exige verificación se compra en uno
    # de estos dos modos (lo garantizan el required y el CHECK de abajo).
    verification_mode = fields.Selection(
        [("platform", "Verificadora de la plataforma"),
         ("declared", "Verificación declarada por las partes")],
        string="Modo de verificación", required=True, default="platform",
        index=True, tracking=True, copy=False,
        help="Verificadora de la plataforma: un verificador acreditado inspecciona "
             "y dictamina (con honorario). Declarada por las partes: el comprador "
             "o el vendedor sube el informe de una verificadora externa o de su "
             "propia verificación, y la otra parte lo confirma (sin honorario).")

    # Obligatoria solo en modo plataforma (_check_mode_requirements): en la
    # declarada no hay verificadora de la plataforma.
    verifier_partner_id = fields.Many2one(
        "res.partner", string="Empresa verificadora",
        ondelete="restrict", index=True, tracking=True,
        domain=["|", ("shrimp_user_type", "=", "verificador"),
                ("shrimp_role_ids", "any", [("role", "=", "verificador"), ("state", "=", "approved")])],
    )

    # ---- Datos propios de la verificación declarada ----
    declared_source = fields.Selection(
        [("external", "Verificadora externa"),
         ("self", "Verificación propia de las partes")],
        string="Quién verificó", default="external", tracking=True,
        help="Verificadora externa (no registrada en la plataforma o por "
             "convenio entre las partes) o verificación hecha por las propias partes.")
    external_verifier_name = fields.Char(
        string="Verificadora externa", tracking=True,
        help="Nombre de la empresa que verificó fuera de la plataforma (opcional).")
    external_verifier_vat = fields.Char(
        string="RUC de la verificadora externa", tracking=True)
    declared_report_file = fields.Binary(
        string="Informe de verificación (PDF)", attachment=True, copy=False)
    declared_report_filename = fields.Char(string="Nombre del informe", copy=False)
    declarant_partner_id = fields.Many2one(
        "res.partner", string="Declarado por", readonly=True, copy=False,
        ondelete="restrict", index=True,
        help="La parte (comprador o vendedor) que presentó el informe declarado. "
             "La otra parte es la que lo confirma.")
    declarant_role = fields.Selection(
        [("buyer", "Comprador"), ("seller", "Vendedor")],
        string="Parte declarante", readonly=True, copy=False)
    declared_submitted_date = fields.Datetime(
        string="Fecha de presentación", readonly=True, copy=False)
    declared_last_editor_id = fields.Many2one(
        "res.partner", string="Última edición del borrador", readonly=True, copy=False,
        ondelete="set null")

    # Quien realmente pisa el campo. La empresa es la acreditada y la que elige
    # el comprador; el técnico es el que firma lo que vio.
    technician_partner_id = fields.Many2one(
        "res.partner", string="Técnico de campo",
        ondelete="restrict", index=True, tracking=True,
        help="Técnico de la empresa que hace la inspección. Solo él puede "
             "iniciar el trabajo de campo.",
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
    # OJO: estas dos las escribe el TÉCNICO, después, y no llevan hora. No
    # sirven para citarlo: cuando existen, él ya estuvo en la planta. La cita
    # es shrimp.dispatch.eta, que declara el vendedor en cuanto se confirma la
    # compra. Las dos cosas conviven a propósito: la del despacho es lo que el
    # vendedor DICE, esta es lo que el técnico VIO.
    harvest_date = fields.Date(string="Fecha de cosecha")
    process_date = fields.Date(string="Fecha de proceso")

    # ------------------------------------------------------------------
    # La cita en planta, traída del seguimiento del despacho
    # ------------------------------------------------------------------
    dispatch_id = fields.Many2one(
        "shrimp.dispatch", string="Despacho",
        related="transaction_id.dispatch_id", store=True, readonly=True,
    )
    # Almacenada para poder ORDENAR la bandeja por ella: la pregunta del
    # técnico al abrir su bandeja es «¿cuál me toca antes?», y esa no se puede
    # responder con un campo que haya que calcular registro a registro.
    dispatch_eta = fields.Datetime(
        string="Llegada estimada a planta", related="dispatch_id.eta",
        store=True, readonly=True, index=True,
    )
    dispatch_arrival = fields.Datetime(
        string="Llegada real a planta", related="dispatch_id.actual_arrival",
        store=True, readonly=True,
    )

    # Qué se le pide a esta verificación depende del producto: al camarón
    # adulto los cinco análisis; a la larva, cantidad, supervivencia, tamaño y
    # estado sanitario (medir metabisulfito o sabor en un nauplio no tiene sentido).
    # Sin selection propia: en un campo related Odoo la descarta con un aviso
    # y usa la del campo original (shrimp.product.verification_scope), que
    # tiene los mismos dos valores. Declararla aquí solo creaba una segunda
    # copia que se puede desincronizar sin que nada lo detecte.
    scope = fields.Selection(
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
    # Lista única de presentaciones (shrimp_marketplace/models/shrimp_selection.py).
    presentation = fields.Selection(
        PRESENTATIONS, string="Presentación", tracking=True,
        help="Entero (cuerpo) o cola, tal como llegó a planta.",
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
    taste_criteria_ok_ids = fields.Many2many(
        "shrimp.taste.criterion",
        string="Criterios de cata correctos",
        help="Criterios de la tabla de cata que el verificador marcó como correctos.")

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
            ("received", "Recibida"),
            ("assigned", "Asignada"),
            ("in_field", "En campo"),
            ("done", "Informe completo"),
            ("approved", "Aprobada"),
            ("approved_obs", "Aprobada con observaciones"),
            ("rejected", "Rechazada"),
            ("cancelled", "Cancelada"),
            # Modo «declarada por las partes»: borrador que llenan comprador o
            # vendedor, y el informe ya presentado (que la otra parte confirma
            # en la ronda de aceptación).
            ("declared_draft", "Declaración en preparación"),
            ("declared", "Informe declarado"),
        ],
        string="Estado", default="received", required=True, index=True, tracking=True,
    )

    assigned_date = fields.Datetime(string="Fecha de asignación", default=fields.Datetime.now, readonly=True)
    field_start_date = fields.Datetime(string="Inicio en campo", readonly=True)
    verified_date = fields.Datetime(string="Fecha de veredicto", readonly=True)
    verdict_notes = fields.Text(string="Conclusión del verificador")

    fee = fields.Monetary(string="Honorario de verificación", currency_field="currency_id")

    # ---- Circuito económico del honorario ----
    # Lo paga el comprador; la plataforma lo factura, retiene su margen y liquida
    # el resto a la empresa verificadora.
    margin_pct = fields.Float(
        string="Margen de la plataforma (%)", digits=(5, 2), readonly=True,
        help="Porcentaje retenido, congelado al emitir los documentos.")
    platform_amount = fields.Monetary(
        string="Retiene la plataforma", currency_field="currency_id",
        compute="_compute_reparto", store=True)
    verifier_amount = fields.Monetary(
        string="Liquidar al verificador", currency_field="currency_id",
        compute="_compute_reparto", store=True)

    # Cobros del honorario (shrimp.charge, tipo «verification_fee»). Hay uno
    # vigente; si el honorario se reasigna, el anterior queda acreditado.
    charge_ids = fields.One2many(
        "shrimp.charge", "verification_id", string="Cobros del honorario")
    fee_charge_id = fields.Many2one(
        "shrimp.charge", string="Cobro vigente del honorario",
        compute="_compute_fee_charge")
    sale_order_id = fields.Many2one(
        "sale.order", string="Pedido al pagador", compute="_compute_fee_charge")
    invoice_id = fields.Many2one(
        "account.move", string="Factura del honorario", compute="_compute_fee_charge")
    vendor_bill_id = fields.Many2one(
        "account.move", string="Factura del verificador", readonly=True, copy=False)
    invoice_state = fields.Selection(
        [("draft", "Borrador"), ("posted", "Contabilizada"), ("cancel", "Cancelada")],
        string="Estado de la factura", compute="_compute_fee_charge")

    @api.depends("charge_ids.state", "charge_ids.invoice_id", "charge_ids.sale_order_id",
                 "charge_ids.invoice_id.state")
    def _compute_fee_charge(self):
        for rec in self:
            vigente = rec.charge_ids.filtered(
                lambda c: c.charge_type == "verification_fee"
                and c.state not in ("cancelled", "credited", "to_credit")
            ).sorted("id", reverse=True)[:1]
            rec.fee_charge_id = vigente
            rec.sale_order_id = vigente.sale_order_id
            rec.invoice_id = vigente.invoice_id
            rec.invoice_state = vigente.invoice_id.state or False

    @api.depends("fee", "margin_pct")
    def _compute_reparto(self):
        for rec in self:
            pct = rec.margin_pct or 0.0
            rec.platform_amount = round((rec.fee or 0.0) * pct / 100.0, 2)
            rec.verifier_amount = round((rec.fee or 0.0) - rec.platform_amount, 2)

    review_ids = fields.One2many(
        "shrimp.verifier.review", "verification_id", string="Reseñas del comprador")

    acceptance_ids = fields.One2many(
        "shrimp.verification.acceptance", "verification_id", string="Posturas de las partes")
    acceptance_deadline = fields.Datetime(
        string="Plazo para aceptar", readonly=True, copy=False,
        help="Vencido el plazo sin respuesta, la postura pendiente se da por aceptada.")
    acceptance_state = fields.Selection(
        [
            ("na", "No corresponde"),
            ("waiting", "Esperando a las partes"),
            ("closed", "Aceptada por ambas"),
            ("broken", "Trato caído"),
        ],
        string="Aceptación de las partes", default="na", readonly=True, copy=False, index=True)

    # Quién termina pagando la verificación. Arranca en el comprador, que es
    # quien la contrata, y se reasigna si el trato se cae por culpa medible de
    # la otra parte.
    fee_payer_partner_id = fields.Many2one(
        "res.partner", string="Paga la verificación", readonly=True, copy=False)

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
            #
            # Se intentó cambiarlo al peso en planta con el argumento de que
            # "A + B + C + basura = 100 % del peso en planta", pero esa identidad
            # no se cumple con datos reales: en VER-000010, A+B+C+basura da 6.840
            # lb contra 10.500 de planta. Falta todo el peso de cabeza y
            # caparazón, que es justamente por lo que el rendimiento ronda el
            # 65 % y no el 99 %. Confirmado además contra un parte real del
            # equipo que da 67,22 %: eso sale exacto sobre el neto (7.079,74 /
            # 10.532) y da 66,95 % sobre planta.
            rec.yield_pct = (100.0 * total / rec.net_weight_lb) if rec.net_weight_lb else 0.0
            # Los porcentajes por clase van sobre el total clasificado, no sobre
            # el peso: en el parte real, un lote entero en clase A da "Rend.
            # Clase A 100,00 %", que solo sale si el divisor es el clasificado.
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
            # «declared»: el informe declarado ya se presentó; se congela igual
            # que un informe con veredicto (la otra parte decide sobre ESE).
            rec.is_final = rec.state in ("approved", "approved_obs", "rejected", "cancelled",
                                         "declared")

    # ==================================================================
    # Validaciones
    # ==================================================================
    PLATFORM_STATES = ("received", "assigned", "in_field", "done",
                       "approved", "approved_obs", "rejected")
    DECLARED_STATES = ("declared_draft", "declared")

    _verification_mode_valid = models.Constraint(
        "CHECK(verification_mode IN ('platform', 'declared'))",
        "El modo de verificación debe ser «Verificadora de la plataforma» o "
        "«Verificación declarada por las partes»: no existe «no aplica».",
    )

    def is_declared(self):
        self.ensure_one()
        return self.verification_mode == "declared"

    @api.constrains("verification_mode", "verifier_partner_id", "technician_partner_id",
                    "fee", "state")
    def _check_mode_requirements(self):
        """Lo que exige cada modo.

        Plataforma: verificadora obligatoria y solo los estados de su flujo.
        Declarada: sin verificadora de la plataforma, sin técnico y sin
        honorario (no hay servicio de la plataforma que cobrar), y solo los
        estados de la declaración (o cancelada).
        """
        for rec in self:
            if rec.verification_mode not in ("platform", "declared"):
                raise ValidationError(_(
                    "Elige cómo se verifica la compra: con la verificadora de la "
                    "plataforma o con una verificación declarada por las partes."))
            if rec.verification_mode == "platform":
                if not rec.verifier_partner_id:
                    raise ValidationError(_(
                        "Una verificación de la plataforma necesita una empresa verificadora."))
                if rec.state in self.DECLARED_STATES:
                    raise ValidationError(_(
                        "Ese estado es de las verificaciones declaradas por las partes."))
            else:
                if rec.verifier_partner_id or rec.technician_partner_id:
                    raise ValidationError(_(
                        "Una verificación declarada por las partes no lleva verificadora "
                        "ni técnico de la plataforma: los datos de la verificadora "
                        "externa van en sus propios campos."))
                if rec.fee:
                    raise ValidationError(_(
                        "La verificación declarada por las partes no tiene honorario "
                        "de verificación de la plataforma."))
                if rec.state not in self.DECLARED_STATES + ("cancelled",):
                    raise ValidationError(_(
                        "Una verificación declarada solo puede estar en preparación, "
                        "declarada o cancelada."))

    @api.constrains("verifier_partner_id")
    def _check_verifier_role(self):
        for rec in self:
            if not rec.verifier_partner_id:
                continue    # declarada: _check_mode_requirements ya lo exige vacío
            if not rec.verifier_partner_id._shrimp_has_role("verificador"):
                raise ValidationError(_("El verificador asignado debe ser un contacto de tipo Verificador."))

    @api.constrains("verifier_partner_id", "buyer_partner_id", "seller_partner_id")
    def _check_verifier_independence(self):
        """El verificador tiene que ser un tercero: si es el propio comprador o
        vendedor, la verificación no garantiza nada.

        No aplica a la verificación DECLARADA: ahí son las propias partes las
        que verifican (o traen a su verificadora externa) y la garantía es que
        la otra parte tiene que confirmar el informe. Se salta por el modo, de
        forma explícita, y no por la mera ausencia de verificadora."""
        for rec in self:
            if rec.verification_mode == "declared":
                continue
            if not rec.verifier_partner_id:
                continue
            # Misma ENTIDAD comercial, no solo el mismo contacto: con varios
            # perfiles por cuenta una empresa puede ser verificadora y a la
            # vez compradora o vendedora, y nunca debe verificarse a sí misma.
            if rec.verifier_partner_id in (rec.buyer_partner_id, rec.seller_partner_id) \
                    or rec.verifier_partner_id.shrimp_same_entity(
                        rec.buyer_partner_id, rec.seller_partner_id):
                raise ValidationError(
                    _("El verificador no puede ser el comprador ni el vendedor de la compra."))

    @api.constrains("technician_partner_id", "verifier_partner_id",
                    "buyer_partner_id", "seller_partner_id")
    def _check_technician_belongs_to_company(self):
        for rec in self:
            tec = rec.technician_partner_id.sudo()
            if not tec:
                continue
            # Nunca una de las partes: el técnico es los ojos del tercero.
            partes = rec.buyer_partner_id | rec.seller_partner_id
            if tec in partes or (tec.parent_id and tec.parent_id in partes):
                raise ValidationError(_(
                    "El técnico no puede ser el comprador ni el vendedor de la compra."))
            # Vale el propio partner de la empresa (cuando el admin hace el
            # trabajo) o un técnico ACTIVO dado de alta por ella.
            if tec == rec.verifier_partner_id:
                continue
            activos = rec.verifier_partner_id.sudo().field_tech_ids.filtered("active")
            if not (tec.shrimp_is_field_tech and tec.parent_id == rec.verifier_partner_id
                    and tec in activos):
                raise ValidationError(_(
                    "El técnico «%s» no es un técnico activo de la empresa verificadora «%s»."
                ) % (tec.name, rec.verifier_partner_id.name))

    # ------------------------------------------------------------------
    # Escritura: el informe cerrado no se toca
    # ------------------------------------------------------------------
    # Tras el veredicto el flujo sigue escribiendo datos de PROCESO
    # (aceptación de las partes, facturación, avisos). Todo lo demás —pesos,
    # análisis, veredicto, estado, honorario— queda congelado para cualquiera
    # que no sea el propio servidor (sudo).
    _FINAL_WRITABLE_FIELDS = frozenset({
        "acceptance_state", "acceptance_deadline", "fee_payer_partner_id",
        "margin_pct", "vendor_bill_id", "buyer_notified",
        "message_main_attachment_id", "activity_ids", "message_follower_ids",
    })

    def write(self, vals):
        user = self.env.user
        if "fee" in vals and user.share:
            # El honorario lo fija la plataforma al crear la verificación; el
            # verificador no se lo sube por su cuenta, ni siquiera vía sudo.
            raise AccessError(_("El honorario de la verificación no se puede modificar."))
        if not self.env.su:
            if user.share:
                raise AccessError(_("Las verificaciones solo se modifican desde sus pantallas."))
            congelados = set(vals) - self._FINAL_WRITABLE_FIELDS
            if congelados and any(rec.is_final for rec in self):
                raise UserError(_("Esta verificación ya está cerrada: su informe no se puede modificar."))
        return super().write(vals)

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
            if rec.verification_mode == "declared":
                # Sin verificadora a la que avisar y con el despacho opcional:
                # la pantalla del despacho se crea si una parte la abre. Se
                # avisa a las partes de que el informe lo suben ellas.
                rec._notify_stage()
                continue
            # El seguimiento del despacho nace con la verificación, vacío: es
            # lo que el vendedor tiene que llenar y no puede tener que pedirlo.
            # Si se creara solo cuando el vendedor entra a la pantalla, la
            # compra que nadie abre se queda sin cita y sin nadie a quien
            # reclamársela.
            rec.transaction_id._ensure_dispatch()
            rec._notify_verifier_assigned()
            rec._notify_stage()
        return records

    # ==================================================================
    # Acciones del flujo
    # ==================================================================
    def action_assign_technician(self, technician=None):
        """El admin de la empresa reparte el trabajo entre sus técnicos.

        La pertenencia (técnico activo de la empresa, nunca comprador ni
        vendedor) la garantiza _check_technician_belongs_to_company."""
        for rec in self:
            if rec.is_final:
                raise UserError(_("Esta verificación ya está cerrada."))
            if technician is not None:
                rec.technician_partner_id = technician
            if not rec.technician_partner_id:
                raise UserError(_("Debes elegir un técnico de campo."))
            if rec.state == "received":
                rec.state = "assigned"
            rec._notify_technician_assigned()
            rec._notify_stage()
            # Si el vendedor ya había fijado la cita, el técnico que acaba de
            # entrar no la recibió: llegó después del aviso. Se le manda a él
            # solo, porque la empacadora y la verificadora ya la tienen.
            if rec.dispatch_id and rec.dispatch_id.eta:
                rec.dispatch_id.sudo()._notify_eta(
                    solo=rec.technician_partner_id)

    def action_start_field(self):
        for rec in self:
            if rec.state != "assigned":
                raise UserError(_(
                    "Solo se puede iniciar el trabajo de campo de una verificación "
                    "asignada a un técnico."))
            # Quién puede iniciar (el técnico asignado o el administrador de la
            # empresa) se valida en el controlador con el usuario real de la
            # sesión: aquí la orden viaja en sudo y self.env.user sería el
            # superusuario.
            rec.write({"state": "in_field", "field_start_date": fields.Datetime.now()})
            # Comprador y vendedor se enteran en el momento en que el técnico
            # pisa el sitio: es el dato que más preguntan por WhatsApp.
            rec._notify_stage()

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
            rec._notify_stage()

    def _puede_dictaminar(self, partner):
        """El técnico que fue a campo, o el admin de su empresa."""
        self.ensure_one()
        if not partner:
            return False
        if self.technician_partner_id and partner == self.technician_partner_id:
            return True
        return partner == self.verifier_partner_id

    def _close(self, state, notes=None):
        self.ensure_one()
        if self.state in ("approved", "approved_obs", "rejected", "cancelled"):
            raise UserError(_("Esta verificación ya está cerrada."))
        if state in ("approved", "approved_obs"):
            missing = self._missing_report_fields()
            if missing:
                raise UserError(_("Faltan datos del informe: %s.") % ", ".join(missing))
        # El texto puede llegar de dos sitios: del formulario del portal, que ya
        # trae su propio textarea, o del backoffice, cuyos botones de cabecera no
        # piden nada pero guardan el registro antes de ejecutarse. Por eso, si no
        # viene por parámetro, se toma lo que el verificador ya escribió en el
        # campo del informe: así el backoffice no necesita un asistente aparte.
        texto = (notes or self.verdict_notes or "").strip()

        # Un dictamen con observaciones o un rechazo sin motivo escrito es lo que
        # la parte perjudicada va a discutir: el informe cancela una compra y
        # carga el honorario a alguien, y no queda constancia de por qué. En
        # estos dos casos el texto es obligatorio.
        if state in ("approved_obs", "rejected") and not texto:
            raise UserError(_(
                "Escribe la conclusión del verificador antes de cerrar: "
                "un dictamen con observaciones o un rechazo tiene que decir "
                "por qué, porque es lo que se le opone a la parte afectada."))

        # En una aprobación limpia no hay nada que justificar: el motivo es que
        # el informe salió conforme, y sus datos ya se validaron arriba en
        # _missing_report_fields(). Aun así no se deja vacío, porque el portal y
        # el PDF de trazabilidad ocultan el bloque del veredicto cuando no hay
        # texto y el comprador se queda sin ver ninguna conclusión.
        if state == "approved" and not texto:
            texto = _("Aprobado sin observaciones: el informe de campo cumple "
                      "con lo declarado en la publicación.")

        vals = {
            "state": state,
            "verified_date": fields.Datetime.now(),
        }
        if texto:
            vals["verdict_notes"] = texto
        self.write(vals)
        # Al verificador se le liquida con el veredicto emitido, aprobado o
        # rechazado: retribuye la inspección, no su resultado.
        if state in ("approved", "approved_obs", "rejected"):
            self._create_verifier_bill()
        if state == "rejected":
            # Nadie tiene que aceptar nada: el informe ya tumbó la compra y el
            # honorario lo carga el vendedor, cuyo producto no pasó.
            self.fee_payer_partner_id = self.seller_partner_id.id
            self._create_payer_invoice()
        self._notify_buyer_verdict()
        if state in ("approved", "approved_obs"):
            # El veredicto favorable no cierra la compra: la pone a la firma de
            # comprador y vendedor.
            self._abrir_ronda_aceptacion()

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

    # ==================================================================
    # VERIFICACIÓN DECLARADA POR LAS PARTES
    # ==================================================================
    # Quién llena: cualquiera de las dos partes (comprador o vendedor) puede
    # editar el borrador; la que lo PRESENTA queda como declarante y su
    # postura en la ronda de aceptación queda aceptada en el acto (presentar
    # el informe es suscribirlo). La OTRA parte lo acepta, lo rechaza o —si
    # es el comprador y el informe muestra que el producto no cumplió—
    # contraoferta, exactamente como frente al informe de un verificador.
    # Si el declarante se equivocó, «deshacer mi decisión» mientras la otra
    # parte no haya respondido retira la declaración y la devuelve a
    # borrador (shrimp.verification.acceptance._signoff_after_undo).
    def _declared_role_of(self, partner):
        """«buyer» / «seller» si `partner` es parte de la compra, si no False."""
        self.ensure_one()
        if partner and partner == self.buyer_partner_id:
            return "buyer"
        if partner and partner == self.seller_partner_id:
            return "seller"
        return False

    def _check_declared_editable(self, actor):
        self.ensure_one()
        if self.verification_mode != "declared":
            raise UserError(_("Esta verificación la hace la verificadora de la plataforma."))
        if not self._declared_role_of(actor):
            raise AccessError(_(
                "El informe declarado solo lo pueden cargar el comprador o el vendedor de la compra."))
        if self.state != "declared_draft":
            raise UserError(_(
                "El informe declarado ya se presentó: ahora la otra parte lo "
                "acepta o lo rechaza. Para corregirlo, el declarante puede "
                "deshacer su presentación mientras la otra parte no haya respondido."))

    def action_declared_save(self, vals, actor):
        """Guarda (por partes) el borrador del informe declarado.

        `vals` son campos del informe y de la verificadora externa; nunca
        estado, partes ni honorario (esos los fija el flujo)."""
        self.ensure_one()
        self._check_declared_editable(actor)
        prohibidos = set(vals) & {
            "state", "verification_mode", "verifier_partner_id", "technician_partner_id",
            "fee", "transaction_id", "declarant_partner_id", "declarant_role",
            "declared_submitted_date", "acceptance_state", "acceptance_deadline",
            "fee_payer_partner_id", "verified_date"}
        if prohibidos:
            raise AccessError(_("Esos datos no se cargan desde el informe declarado: %s")
                              % ", ".join(sorted(prohibidos)))
        vals = dict(vals, declared_last_editor_id=actor.id)
        self.sudo().write(vals)
        return True

    def _missing_declared_fields(self):
        """Lo que falta para PRESENTAR el informe declarado: los mismos datos
        mínimos que el informe de un verificador, más el informe en PDF cuando
        verificó una empresa externa (es el respaldo de lo declarado)."""
        self.ensure_one()
        missing = self._missing_report_fields()
        if self.declared_source == "external":
            if not (self.external_verifier_name or "").strip():
                missing.append(_("nombre de la verificadora externa"))
            if not self.declared_report_file:
                missing.append(_("informe de la verificadora externa (PDF)"))
        return missing

    def action_declared_submit(self, actor, notes=None):
        """Presenta el informe declarado: lo congela y lo pone a la firma.

        `actor` (comprador o vendedor) queda como declarante; su postura queda
        aceptada y la otra parte pasa a tener que responder en el plazo."""
        self.ensure_one()
        self._check_declared_editable(actor)
        missing = self._missing_declared_fields()
        if missing:
            raise UserError(_("Faltan datos del informe declarado: %s.") % ", ".join(missing))
        rol = self._declared_role_of(actor)
        vals = {
            "state": "declared",
            "declarant_partner_id": actor.id,
            "declarant_role": rol,
            "declared_submitted_date": fields.Datetime.now(),
            "verified_date": fields.Datetime.now(),
        }
        texto = (notes or "").strip()
        if texto:
            vals["verdict_notes"] = texto
        self.sudo().write(vals)
        self.sudo()._message_log(body=_(
            "Informe declarado presentado por %(quien)s (%(rol)s). %(origen)s") % {
                "quien": actor.name, "rol": dict(self._fields["declarant_role"].selection)[rol],
                "origen": self.declared_origin_text()})
        self.sudo()._abrir_ronda_aceptacion()
        # Presentar el informe es suscribirlo: la postura del declarante queda
        # aceptada (con su historial, así que «deshacer» la puede revertir).
        postura = self.acceptance_ids.filtered(lambda a: a.role == rol)[:1]
        if postura and postura.decision == "pending":
            postura.sudo().action_accept(
                reason=_("Presentó el informe declarado."), actor=actor)
        return True

    def _reabrir_declaracion(self):
        """El declarante retiró su presentación (deshizo su aceptación antes
        de que la otra parte respondiera): el informe vuelve a borrador, la
        ronda se cierra sin efecto y la compra vuelve a «pendiente de
        verificación». Las posturas quedan en «pendiente» (con su historial)."""
        for rec in self.sudo():
            if rec.verification_mode != "declared" or rec.state != "declared":
                continue
            quien = rec.declarant_partner_id
            rec.write({
                "state": "declared_draft",
                "acceptance_state": "na",
                "acceptance_deadline": False,
                "declarant_partner_id": False,
                "declarant_role": False,
                "declared_submitted_date": False,
                "verified_date": False,
            })
            tx = rec.transaction_id
            if tx.state == "pending_acceptance":
                tx.write({"state": "pending_verification"})
            rec._message_log(body=_(
                "%s retiró el informe declarado para corregirlo: vuelve a borrador.")
                % (quien.name or _("El declarante")))
        return True

    # ---- textos para pantallas, PDF, página pública y API ----
    def declared_origin_text(self):
        """«externa: X (RUC …)» o «verificación propia de las partes»."""
        self.ensure_one()
        if self.declared_source == "self":
            return _("verificación propia de las partes")
        nombre = (self.external_verifier_name or "").strip() or _("verificadora externa no indicada")
        if (self.external_verifier_vat or "").strip():
            return _("externa: %(n)s, RUC %(r)s") % {"n": nombre, "r": self.external_verifier_vat.strip()}
        return _("externa: %s") % nombre

    def verification_label(self):
        """La etiqueta que distingue los dos modos en la trazabilidad.

        Plataforma: «Verificado por <verificadora acreditada>».
        Declarada:  «Verificación declarada por <empresa> (externa: <verificadora>
        RUC …)» — nunca se presenta como si la hubiera hecho un acreditado."""
        self.ensure_one()
        if self.verification_mode == "declared":
            quien = self.declarant_partner_id.commercial_partner_id.name \
                or self.declarant_partner_id.name
            if not quien:
                return _("Verificación declarada por las partes (pendiente de presentar)")
            return _("Verificación declarada por %(empresa)s (%(origen)s)") % {
                "empresa": quien, "origen": self.declared_origin_text()}
        empresa = self.verifier_partner_id.name or "—"
        if self.verifier_partner_id.verifier_is_accredited:
            return _("Verificado por %s (verificadora acreditada de la plataforma)") % empresa
        return _("Verificado por %s (verificadora de la plataforma)") % empresa

    def declared_other_party(self):
        """La parte que tiene que confirmar el informe declarado."""
        self.ensure_one()
        if self.declarant_role == "buyer":
            return self.seller_partner_id
        if self.declarant_role == "seller":
            return self.buyer_partner_id
        return self.env["res.partner"]

    def action_cancel(self):
        for rec in self:
            rec.write({"state": "cancelled"})
            rec._notify_stage()
            rec.transaction_id.action_cancel_for_verification()
            # Sin inspección no hay servicio que cobrar: el honorario que se
            # facturó al iniciar la compra se anula con nota de crédito.
            rec.charge_ids.filtered(
                lambda c: c.charge_type == "verification_fee"
            ).action_cancel_charge(_("Verificación %s cancelada") % rec.name)

    # ==================================================================
    # Aceptación de las dos partes
    # ==================================================================
    # Un informe que solo vincula al comprador no es un arbitraje, es la
    # herramienta de una de las partes. Por eso la compra no se cierra con el
    # veredicto: se cierra cuando comprador y vendedor firman que lo aceptan.

    def _horas_de_plazo(self):
        """Ajustes › CamaronMarket › Verificación (acceptance_hours, 48)."""
        return self.env["shrimp.settings"].get_int(
            "shrimp_verification.acceptance_hours", 48, minimum=1)

    def _tolerancia_peso(self):
        """Cuánto puede faltar del peso vendido sin considerarlo incumplimiento.
        Entre la pesada del vendedor y la de planta siempre hay merma.
        Ajustes › CamaronMarket › Verificación (weight_tolerance_pct, 2 %)."""
        return self.env["shrimp.settings"].get_float(
            "shrimp_verification.weight_tolerance_pct", 2.0, minimum=0)

    def cumple_lo_publicado(self):
        """¿El producto entregado cumple lo que el anuncio prometía?

        Devuelve (bool, [motivos]). De aquí sale quién carga con el honorario
        cuando el trato se cae, así que solo entran datos medidos por el
        verificador y comparables contra el anuncio: nada de apreciaciones.
        """
        self.ensure_one()
        motivos = []
        tx = self.transaction_id

        if self.state == "rejected":
            motivos.append(_("el verificador rechazó la inspección"))
            return False, motivos
        if self.state == "approved_obs":
            motivos.append(_("el verificador aprobó con observaciones"))

        if self.scope == "larvae":
            if (self.larvae_qty_diff_pct or 0.0) < -self._tolerancia_peso():
                motivos.append(_("llegaron menos larvas de las vendidas"))
            if self.larvae_health_status == "rejected":
                motivos.append(_("el estado sanitario no es aceptable"))
            return (not motivos), motivos

        # Peso: solo cuenta el faltante. Que venga de más no es incumplimiento.
        vendido = tx.transaction_qty or self.weight_sent_lb or 0.0
        if vendido and self.weight_plant_lb:
            faltante_pct = (vendido - self.weight_plant_lb) / vendido * 100.0
            if faltante_pct > self._tolerancia_peso():
                motivos.append(_("faltó peso: llegaron %.2f lb de %.2f vendidas") % (
                    self.weight_plant_lb, vendido))

        if self.metabisulfite_result == "fail":
            motivos.append(_("el metabisulfito no dio conforme"))
        if self.taste_result == "rejected":
            motivos.append(_("el sabor fue rechazado"))

        # Talla: la que domina el lote verificado contra la publicada.
        talla_publicada = (tx.product_id.size_grade_id.name or "").strip().upper()
        if talla_publicada and self.line_ids:
            dominante = max(self.line_ids, key=lambda l: l.weight_lb or 0.0)
            talla_real = (dominante.size_code or "").strip().upper()
            if talla_real and talla_real != talla_publicada:
                motivos.append(_("la talla predominante es %s y se publicó %s") % (
                    talla_real, talla_publicada))

        return (not motivos), motivos

    def _abrir_plazo(self):
        for rec in self:
            rec.acceptance_deadline = fields.Datetime.add(
                fields.Datetime.now(), hours=rec._horas_de_plazo())

    def _abrir_ronda_aceptacion(self):
        """Tras un veredicto favorable, pone la compra a la firma de las partes."""
        Postura = self.env["shrimp.verification.acceptance"].sudo()
        for rec in self:
            if rec.acceptance_state != "na":
                continue
            existentes = rec.acceptance_ids.mapped("role")
            for rol, partner in (("buyer", rec.buyer_partner_id),
                                 ("seller", rec.seller_partner_id)):
                if rol not in existentes and partner:
                    Postura.create({
                        "verification_id": rec.id,
                        "role": rol,
                        "partner_id": partner.id,
                    })
            rec.acceptance_state = "waiting"
            if rec.verification_mode == "platform":
                rec.fee_payer_partner_id = rec.buyer_partner_id.id
            rec._abrir_plazo()
            rec.transaction_id.action_await_acceptance()
            rec._notify_acceptance("opened")

    def _resolver_aceptacion(self):
        """Cierra la ronda si ya hay decisión de las dos partes.

        Una decisión surte efecto cuando la ronda se cierra, no al hacer clic:
        un rechazo con la otra parte todavía pendiente NO tumba la compra
        (quien rechazó puede deshacerlo). La compra se cae cuando las dos
        decidieron —o al vencer el plazo, cuando el cron da por aceptada la
        pendiente— y alguna rechazó.
        """
        for rec in self:
            if rec.acceptance_state != "waiting":
                continue
            posturas = rec.acceptance_ids
            if len(posturas) < 2:
                continue

            pendientes = posturas.filtered(lambda a: a.decision == "pending")
            rechazo = posturas.filtered(lambda a: a.decision == "rejected")
            if rechazo and pendientes:
                # Rechazo todavía reversible: se avisa a la otra parte de que
                # la pelota está en su cancha, pero la compra sigue en pie.
                rec._notify_acceptance("rejected_pending")
                continue
            if rechazo:
                rec._cerrar_trato_caido(rechazo[0])
                continue

            comprador = posturas.filtered(lambda a: a.role == "buyer")[:1]
            vendedor = posturas.filtered(lambda a: a.role == "seller")[:1]

            # Una contraoferta del comprador vale como su aceptación del precio
            # nuevo: lo que falta es que el vendedor la firme.
            comprador_ok = comprador.decision in ("accepted", "counter")
            if not (comprador_ok and vendedor.decision == "accepted"):
                # Todavía falta uno: avisarle que la pelota está en su cancha.
                if len(posturas.filtered(lambda a: a.decision == "pending")) == 1:
                    rec._notify_acceptance("waiting_on_one")
                continue

            if comprador.decision == "counter":
                rec._aplicar_contraoferta(comprador)
            rec._cerrar_trato_aceptado()

    def _aplicar_contraoferta(self, postura):
        """Deja la compra al precio que las dos partes terminaron firmando."""
        self.ensure_one()
        tx = self.transaction_id
        anterior = tx.price_unit or 0.0
        tx.sudo().write({
            "price_unit": postura.counter_price,
            "amount_total": postura.counter_total,
        })
        tx.sudo().message_post(body=_(
            "Precio ajustado tras la verificación: de %(antes)s a %(ahora)s por unidad. "
            "Total %(total)s. Lo propuso el comprador y lo aceptó el vendedor."
        ) % {
            "antes": anterior,
            "ahora": postura.counter_price,
            "total": postura.counter_total,
        })

    def _cerrar_trato_aceptado(self):
        self.ensure_one()
        self.acceptance_state = "closed"
        # El honorario lo paga el comprador: contrató la verificación y el
        # trato salió adelante. (La declarada no tiene honorario; la comisión
        # de la venta sí se cobra, en action_confirm, como en toda compra.)
        if self.verification_mode == "platform":
            self.fee_payer_partner_id = self.buyer_partner_id.id
        self._create_payer_invoice()
        self.transaction_id.action_complete_after_verification()
        self._notify_acceptance("closed")

    def _cerrar_trato_caido(self, rechazo):
        """El trato se cayó. Lo que hay que decidir aquí es quién paga la
        verificación, y la regla es una sola: la carga el que se salió del
        trato sin un motivo medible en el informe."""
        self.ensure_one()
        cumple, motivos = self.cumple_lo_publicado()
        if cumple:
            # El producto era lo prometido: paga el que se echó atrás.
            culpable = rechazo.partner_id
            explicacion = _(
                "El informe confirmó lo publicado, así que la verificación la "
                "paga quien se retiró del trato.")
        else:
            # El producto no era lo prometido: paga el vendedor, haya rechazado
            # él o se haya retirado el comprador con razón.
            culpable = self.seller_partner_id
            explicacion = _(
                "El producto no cumplió lo publicado (%s), así que la "
                "verificación la paga el vendedor.") % "; ".join(motivos)

        self.acceptance_state = "broken"
        if self.verification_mode == "declared":
            # Sin honorario de la plataforma: no hay nada que cargarle a nadie.
            self.sudo()._message_log(body=_(
                "Trato caído: %(quien)s rechazó el informe declarado. Al ser una "
                "verificación declarada por las partes, no hay honorario de "
                "verificación.") % {"quien": rechazo.partner_id.name})
            self.transaction_id.action_cancel_for_verification()
            self._notify_acceptance("broken")
            return
        self.fee_payer_partner_id = culpable.id
        self.sudo()._message_log(body=_(
            "Trato caído: lo rechazó %(quien)s. %(explicacion)s Se factura a "
            "%(pagador)s."
        ) % {
            "quien": rechazo.partner_id.name,
            "explicacion": explicacion,
            "pagador": culpable.name,
        })
        self._create_payer_invoice()
        self.transaction_id.action_cancel_for_verification()
        self._notify_acceptance("broken")

    @api.model
    def _cron_vencer_plazos(self):
        """Da por aceptadas las posturas que se quedaron sin respuesta.

        El informe lo firmó un tercero acreditado: si nadie lo objeta dentro
        del plazo, callar es consentir. La alternativa —cancelar por silencio—
        mataría ventas buenas porque alguien no revisó el correo el fin de
        semana, y dejaría el stock trabado.
        """
        vencidas = self.sudo().search([
            ("acceptance_state", "=", "waiting"),
            ("acceptance_deadline", "<=", fields.Datetime.now()),
        ])
        for rec in vencidas:
            pendientes = rec.acceptance_ids.filtered(lambda a: a.decision == "pending")
            if not pendientes:
                continue
            pendientes.action_accept(
                reason=_("Aceptada automáticamente: venció el plazo sin respuesta."),
                auto=True)
        return True

    def _texto_plazo(self):
        self.ensure_one()
        if not self.acceptance_deadline:
            return ""
        local = fields.Datetime.context_timestamp(self, self.acceptance_deadline)
        return local.strftime("%d/%m/%Y a las %H:%M")

    def _notify_acceptance(self, evento):
        """Avisos de la ronda de firmas.

        Van por la misma plantilla de etapa, forzando titular y detalle desde
        el contexto: para el sistema la verificación sigue "aprobada" todo el
        tiempo, pero para las partes están pasando cosas distintas.
        """
        self.ensure_one()
        tx = self.transaction_id
        plazo = self._texto_plazo()
        contra = self.acceptance_ids.filtered(lambda a: a.decision == "counter")[:1]
        _cumple, motivos = self.cumple_lo_publicado()

        declarada = self.verification_mode == "declared"
        if evento == "opened" and declarada:
            # Solo a la parte que tiene que confirmar: el declarante ya lo
            # suscribió al presentarlo.
            destinos = ["seller" if self.declarant_role == "buyer" else "buyer"]
            titular = _("Confirma el informe de verificación declarado")
            detalle = _(
                "%(quien)s presentó el informe de la verificación hecha fuera de "
                "la plataforma (%(origen)s). La compra se cierra cuando lo "
                "aceptes. Revísalo y responde antes del %(plazo)s: si no dices "
                "nada, se dará por aceptado."
            ) % {"quien": self.declarant_partner_id.name or _("La otra parte"),
                 "origen": self.declared_origin_text(), "plazo": plazo}

        elif evento == "opened":
            destinos = ["buyer", "seller"]
            titular = _("Falta tu aceptación del informe")
            detalle = _(
                "%(empresa)s terminó la inspección. Antes de cerrar la compra, "
                "comprador y vendedor tienen que aceptar el informe. Revísalo y "
                "responde antes del %(plazo)s: si no dices nada, se dará por "
                "aceptado."
            ) % {"empresa": self.verifier_partner_id.name or "", "plazo": plazo}

        elif evento == "counter":
            destinos = ["seller"]
            titular = _("El comprador propone otro precio")
            detalle = _(
                "El informe encontró que el producto no cumplió lo publicado "
                "(%(motivos)s). En vez de tumbar la compra, el comprador propone "
                "pagar %(nuevo)s por unidad en lugar de %(antes)s, o sea "
                "%(total)s en total. Acéptalo o recházalo antes del %(plazo)s."
            ) % {
                "motivos": "; ".join(motivos) or _("hay diferencias con el anuncio"),
                "nuevo": contra.counter_price,
                "antes": tx.price_unit,
                "total": contra.counter_total,
                "plazo": plazo,
            }

        elif evento == "rejected_pending":
            pendiente = self.acceptance_ids.filtered(lambda a: a.decision == "pending")[:1]
            rechazo = self.acceptance_ids.filtered(lambda a: a.decision == "rejected")[:1]
            if not pendiente or not rechazo:
                return
            destinos = [pendiente.role]
            titular = _("La otra parte rechazó el informe")
            detalle = _(
                "%(quien)s rechazó el informe%(motivo)s. El rechazo surte efecto "
                "cuando respondas o al vencer el plazo (%(plazo)s): entonces la "
                "compra se cancela. Hasta ese momento %(quien)s todavía puede "
                "deshacer su rechazo."
            ) % {
                "quien": rechazo.partner_id.name or _("Una de las partes"),
                "motivo": (_(": %s") % rechazo.reason) if rechazo.reason else "",
                "plazo": plazo,
            }

        elif evento == "waiting_on_one":
            pendiente = self.acceptance_ids.filtered(lambda a: a.decision == "pending")[:1]
            if not pendiente:
                return
            destinos = [pendiente.role]
            titular = _("Solo faltas tú para cerrar la compra")
            detalle = _(
                "La otra parte ya aceptó el informe declarado. En cuanto "
                "respondas, la compra queda cerrada. Tienes hasta el %(plazo)s; "
                "si no dices nada, se dará por aceptado."
            ) % {"plazo": plazo} if declarada else _(
                "La otra parte ya aceptó el informe del verificador. En cuanto "
                "respondas, la compra queda cerrada. Tienes hasta el %(plazo)s; "
                "si no dices nada, se dará por aceptado."
            ) % {"plazo": plazo}

        elif evento == "closed":
            destinos = ["buyer", "seller"]
            titular = _("Compra cerrada")
            detalle = _(
                "Comprador y vendedor aceptaron el informe de %(empresa)s. La "
                "compra quedó concretada por %(total)s y la trazabilidad ya "
                "está registrada."
            ) % {
                "empresa": (self.verification_label() if declarada
                            else self.verifier_partner_id.name) or "",
                "total": tx.amount_total,
            }

        elif evento == "broken":
            destinos = ["buyer", "seller", "verifier"]
            rechazo = self.acceptance_ids.filtered(lambda a: a.decision == "rejected")[:1]
            titular = _("El trato no se concretó")
            detalle = _(
                "%(quien)s no aceptó el informe%(motivo)s. La compra se canceló "
                "y el producto vuelve a quedar disponible. El honorario de la "
                "verificación lo asume %(pagador)s."
            ) % {
                "quien": rechazo.partner_id.name or _("Una de las partes"),
                "motivo": (_(": %s") % rechazo.reason) if rechazo.reason else "",
                "pagador": (self.fee_payer_partner_id.name or ""),
            } if not declarada else _(
                "%(quien)s no aceptó el informe declarado%(motivo)s. La compra se "
                "canceló y el producto vuelve a quedar disponible. Al ser una "
                "verificación declarada por las partes, no hay honorario de "
                "verificación que cobrar."
            ) % {
                "quien": rechazo.partner_id.name or _("Una de las partes"),
                "motivo": (_(": %s") % rechazo.reason) if rechazo.reason else "",
            }
        else:
            return

        entregados = []
        for partner, url in self._stage_recipients(destinos):
            ok = self._send_template(
                "shrimp_verification.mail_template_verification_stage",
                partner.email,
                ctx={"portal_url": url, "destinatario": partner.name,
                     "titular": titular, "detalle": detalle},
            )
            entregados.append((partner, ok))
        self._log_notificacion(entregados)

    @api.model
    def pendientes_de_aceptar(self, partner=None):
        """Verificaciones esperando la firma del usuario actual.

        Se usa para el aviso que va arriba de "mis compras" y "mis ventas": es
        una acción con plazo, esconderla dentro de una fila de la tabla haría
        que se venciera sin que nadie la viera.
        """
        partner = partner or self.env.user.partner_id
        if not partner:
            return self.browse()
        return self.sudo().search([
            ("acceptance_state", "=", "waiting"),
            ("acceptance_ids.partner_id", "=", partner.id),
            ("acceptance_ids.decision", "=", "pending"),
        ]).filtered(lambda v: any(
            a.partner_id == partner and a.decision == "pending" for a in v.acceptance_ids))

    def action_open_acceptances(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "shrimp.verification.acceptance",
            "name": _("Posturas de las partes"),
            "view_mode": "list,form",
            "domain": [("verification_id", "=", self.id)],
        }

    # ==================================================================
    # Facturación del honorario (vía shrimp.charge)
    # ==================================================================
    # El honorario es un cobro de la PLATAFORMA (factura electrónica propia)
    # y se emite al iniciar la compra verificada, al comprador que la
    # contrata. Si al final paga otro (veredicto rechazado o trato caído por
    # culpa medible del vendedor), el cobro se reasigna: nota de crédito al
    # comprador y factura al nuevo pagador. La parte del verificador es un
    # documento de COMPRA (factura de proveedor) y va por otro camino.
    def _get_verification_product(self):
        """Producto de servicio con el que se factura la verificación."""
        return self.env["shrimp.charge"]._get_service_product("verification_fee")

    def _margen_configurado(self):
        # Sin parámetro, 0 como siempre (la instalación lo siembra en 15 %).
        return self.env["shrimp.settings"].get_float(
            "shrimp_verification.margin_pct", 0.0, minimum=0, maximum=100)

    def _congelar_margen(self):
        """El margen se congela al emitir el primer documento: cambiarlo
        después no debe alterar documentos ya emitidos."""
        for rec in self:
            if not rec.margin_pct:
                rec.sudo().write({"margin_pct": rec._margen_configurado()})

    def _register_fee_charge(self):
        """Registra (una vez) el cobro del honorario y lo intenta facturar."""
        Charge = self.env["shrimp.charge"].sudo()
        for rec in self:
            if (rec.fee or 0.0) <= 0 or rec.fee_charge_id:
                continue
            pagador = rec.fee_payer_partner_id or rec.buyer_partner_id
            if not pagador:
                continue
            rec._congelar_margen()
            Charge._register_charge({
                "charge_type": "verification_fee",
                "verification_id": rec.id,
                "transaction_id": rec.transaction_id.id,
                "payer_partner_id": pagador.id,
                "buyer_partner_id": rec.buyer_partner_id.id,
                "seller_partner_id": rec.seller_partner_id.id,
                "product_id": rec.product_id.id,
                "qty": rec.transaction_id.transaction_qty,
                "uom_id": rec.product_id.uom_id.id,
                "amount": rec.fee,
                "invoice_qty": 1.0,
                "origin": rec.name,
                "description": _("Verificación en campo – %s") % (rec.name or ""),
            })
            rec.invalidate_recordset(["charge_ids", "fee_charge_id"])
        return True

    def _create_verification_documents(self):
        """Emite todo lo que ya se pueda emitir del honorario (botón de
        reintento): el cobro al pagador y la liquidación al verificador."""
        self._create_payer_invoice()
        for rec in self:
            rec.charge_ids.filtered(lambda c: c.state in ("pending", "error", "to_credit")) \
                .action_retry_invoice()
        self.filtered(lambda r: r.state in ("approved", "approved_obs", "rejected"))._create_verifier_bill()

    def _create_verifier_bill(self):
        """Liquidación a la EMPRESA VERIFICADORA por su parte del honorario.

        Se emite al cerrar el veredicto, sea aprobado o rechazado: el honorario
        retribuye la inspección hecha, no su resultado. Pagarle solo cuando
        aprueba le daría al verificador un incentivo a aprobar. Pasa por el
        único creador de facturas de proveedor (shrimp.charge._create_vendor_bill),
        que la deja en borrador cuando el diario exige documentos del SRI.
        """
        Charge = self.env["shrimp.charge"].sudo()
        for rec in self:
            if rec.vendor_bill_id or (rec.fee or 0.0) <= 0 or not rec.verifier_partner_id:
                continue
            rec._congelar_margen()
            if (rec.verifier_amount or 0.0) <= 0:
                continue
            try:
                with self.env.cr.savepoint():
                    gasto = Charge._create_vendor_bill(
                        rec.verifier_partner_id, rec._get_verification_product(),
                        _("Verificación en campo – %s") % (rec.name or ""),
                        rec.verifier_amount,
                        _("Honorario de verificación %s") % (rec.name or ""))
            except Exception as e:  # noqa: BLE001
                _logger.exception("Verificación %s: fallo al liquidar al verificador", rec.name)
                rec.message_post(body=_(
                    "No se pudo registrar la liquidación al verificador: %s") % e)
                continue
            rec.sudo().write({"vendor_bill_id": gasto.id})

    def _create_payer_invoice(self):
        """Deja el honorario facturado a quien corresponda pagarlo.

        El cobro se emitió al iniciar la compra (al comprador). Si al
        resolverse la aceptación resulta que paga otro, se reasigna: nota de
        crédito al anterior y factura al nuevo. Las verificaciones iniciadas
        antes de esta versión no tienen cobro: se emite aquí, al resolverse,
        como se hacía entonces.
        """
        for rec in self:
            if (rec.fee or 0.0) <= 0:
                continue
            pagador = rec.fee_payer_partner_id or rec.buyer_partner_id
            vigente = rec.fee_charge_id
            if not vigente:
                rec._register_fee_charge()
                continue
            if pagador and vigente.payer_partner_id != pagador:
                vigente._reassign_payer(pagador, _(
                    "El honorario de %(v)s lo asume %(p)s") % {
                        "v": rec.name, "p": pagador.name})
                rec.invalidate_recordset(["charge_ids", "fee_charge_id"])
                rec.sudo()._message_log(body=_(
                    "Honorario de verificación reasignado a %s.") % pagador.name)

    def action_generate_verification_documents(self):
        """Botón: reintentar la facturación si algo falló."""
        self._create_verification_documents()
        return True

    def action_open_invoice(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move",
                "res_id": self.invoice_id.id, "view_mode": "form", "target": "current"}

    def action_open_vendor_bill(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move",
                "res_id": self.vendor_bill_id.id, "view_mode": "form", "target": "current"}

    # ==================================================================
    # Seguimiento por etapas
    # ==================================================================
    # Las etapas visibles del proceso, en orden. La cancelación no está aquí
    # porque no es un paso del avance sino una salida del camino.
    ETAPAS = [
        ("received", "Recibida",         "La verificadora recibió la orden"),
        ("assigned", "Asignada",         "Hay un técnico responsable"),
        ("in_field", "En campo",         "El técnico está inspeccionando"),
        ("done",     "Informe completo", "Los cinco análisis quedaron registrados"),
        ("verdict",  "Verificada",       "Se emitió el veredicto"),
        ("accepted", "Aceptada",         "Comprador y vendedor firmaron el informe"),
    ]
    # Verificación declarada por las partes: no hay verificadora ni técnico.
    ETAPAS_DECLARADA = [
        ("declared_draft", "En preparación", "Comprador o vendedor cargan el informe"),
        ("declared",       "Presentada",     "Una parte presentó el informe declarado"),
        ("accepted",       "Aceptada",       "La otra parte confirmó el informe"),
    ]

    def _etapas(self):
        self.ensure_one()
        return self.ETAPAS_DECLARADA if self.verification_mode == "declared" else self.ETAPAS

    # Quién se entera de cada cambio de etapa. Comprador y vendedor siguen el
    # avance de punta a punta: son los que tienen plata en juego. La empresa
    # verificadora solo recibe aviso de lo que no disparó ella misma.
    AUDIENCIA_ETAPA = {
        "received":     ("buyer", "seller"),
        # El técnico no va aquí: _notify_technician_assigned ya le manda su
        # propia plantilla, más detallada. Ponerlo sería un correo duplicado.
        "assigned":     ("buyer", "seller"),
        "in_field":     ("buyer", "seller", "verifier"),
        "done":         ("buyer", "seller", "verifier"),
        "cancelled":    ("buyer", "seller", "verifier", "technician"),
        # Declarada: las dos partes se enteran de que el informe lo cargan ellas.
        "declared_draft": ("buyer", "seller"),
    }

    def _stage_recipients(self, roles):
        """Devuelve [(partner, url_de_su_portal)] sin repetidos ni vacíos.

        Cada parte entra por una puerta distinta, así que el botón del correo
        no puede ser el mismo para todos: al comprador se le manda a sus
        compras, al vendedor a sus ventas y a la verificadora a la orden.
        """
        self.ensure_one()
        base = self.get_base_url()
        mapa = {
            "buyer":      (self.buyer_partner_id,      "/marketplace/purchases"),
            "seller":     (self.seller_partner_id,     "/marketplace/sales"),
            "verifier":   (self.verifier_partner_id,   "/verifier/verifications/%s" % self.uuid_ref),
            "technician": (self.technician_partner_id, "/verifier/verifications/%s" % self.uuid_ref),
        }
        if self.verification_mode == "declared":
            # En la declarada las partes van a la pantalla del informe.
            ruta_decl = "/marketplace/verifications/%s/declare" % self.uuid_ref
            mapa["buyer"] = (self.buyer_partner_id, ruta_decl)
            mapa["seller"] = (self.seller_partner_id, ruta_decl)
        salida, vistos = [], set()
        for rol in roles:
            partner, ruta = mapa.get(rol, (None, ""))
            if not partner or not partner.email or partner.id in vistos:
                continue
            vistos.add(partner.id)
            salida.append((partner, base + ruta))
        return salida

    def _notify_stage(self):
        """Avisa por correo a quien corresponda del cambio de etapa."""
        self.ensure_one()
        roles = self.AUDIENCIA_ETAPA.get(self.state)
        if not roles:
            return
        entregados = [
            (partner, self._send_template(
                "shrimp_verification.mail_template_verification_stage",
                partner.email,
                ctx={"portal_url": url, "destinatario": partner.name},
            ))
            for partner, url in self._stage_recipients(roles)
        ]
        self._log_notificacion(entregados)

    def _stage_index(self):
        """Hasta qué etapa llegó realmente, como índice de ETAPAS.

        Para una verificación cancelada el estado ya no dice nada del avance,
        así que se deduce de los hitos que sí quedaron grabados: si hay fecha de
        veredicto llegó al final, si hay fecha de inicio en campo llegó ahí, y
        así hacia atrás. Sin esto una cancelación mostraría la línea de tiempo
        entera en gris, como si nunca hubiera pasado nada.
        """
        self.ensure_one()
        if self.verification_mode == "declared":
            if self.acceptance_state == "closed":
                return 2
            return 1 if (self.state == "declared" or self.declared_submitted_date) else 0
        if self.acceptance_state == "closed":
            return 5
        por_estado = {"received": 0, "assigned": 1, "in_field": 2, "done": 3,
                      "approved": 4, "approved_obs": 4, "rejected": 4}
        if self.state in por_estado:
            return por_estado[self.state]
        if self.verified_date:
            return 4
        if self.field_start_date:
            return 2
        if self.technician_partner_id:
            return 1
        return 0

    def stage_timeline(self):
        """Línea de tiempo para el correo: qué etapas ya pasaron, cuál es la
        actual y cuáles faltan. Se calcula acá y no en la plantilla para que el
        HTML del correo quede legible."""
        self.ensure_one()
        indice = self._stage_index()
        cancelada = self.state == "cancelled"
        pasos = []
        for i, (_clave, titulo, detalle) in enumerate(self._etapas()):
            # "Verificada" solo es cierto si el veredicto fue favorable; en un
            # rechazo la etapa igual se cumplió, pero se llama de otro modo.
            if _clave == "verdict" and self.state in ("rejected", "cancelled"):
                titulo = _("Veredicto")
            if cancelada:
                estado = "hecha" if i <= indice else "pendiente"
            else:
                estado = "hecha" if i < indice else ("actual" if i == indice else "pendiente")
            pasos.append({"titulo": titulo, "detalle": detalle, "estado": estado})
        if cancelada:
            pasos.append({
                "titulo": _("Cancelada"),
                "detalle": _("El proceso se interrumpió aquí"),
                "estado": "cancelada",
            })
        return pasos

    def etiqueta(self, campo):
        """Texto legible de un campo de selección. En los correos no puede
        salir la clave interna: al comprador "good" no le dice nada, "Bueno" sí."""
        self.ensure_one()
        valor = self[campo]
        if not valor:
            return "-"
        seleccion = self._fields[campo]._description_selection(self.env)
        return dict(seleccion).get(valor, valor)

    def stage_headline(self):
        """Titular del correo. En castellano llano: lo lee un camaronero, no un
        operador del sistema.

        Se puede forzar desde el contexto porque los avisos de la ronda de
        aceptación no son cambios de estado —la verificación sigue "aprobada"
        mientras las partes firman— y aun así necesitan su propio titular.
        """
        self.ensure_one()
        forzado = self.env.context.get("titular")
        if forzado:
            return forzado
        if self.acceptance_state == "waiting":
            return _("Falta la aceptación de las partes")
        if self.acceptance_state == "broken":
            return _("El trato no se concretó")
        if self.acceptance_state == "closed":
            return _("Compra cerrada")
        return {
            "received":     _("Tu compra entró a verificación"),
            "assigned":     _("Ya hay un técnico asignado"),
            "in_field":     _("Comenzó la inspección en campo"),
            "done":         _("El informe de campo está completo"),
            "approved":     _("La verificación fue aprobada"),
            "approved_obs": _("Aprobada con observaciones"),
            "rejected":     _("La verificación fue rechazada"),
            "cancelled":    _("La verificación fue cancelada"),
            "declared_draft": _("Carga el informe de verificación declarado"),
            "declared":     _("El informe declarado fue presentado"),
        }.get(self.state, _("Avance de la verificación"))

    def stage_detail(self):
        """Explicación de qué pasó y qué sigue, según la etapa."""
        self.ensure_one()
        forzado = self.env.context.get("detalle")
        if forzado:
            return forzado
        empresa = self.verifier_partner_id.name or _("la verificadora")
        tecnico = self.technician_partner_id.name or _("un técnico")
        return {
            "received": _(
                "%(empresa)s recibió la orden de verificación y va a asignar un "
                "técnico de campo."
            ) % {"empresa": empresa},
            "assigned": _(
                "%(empresa)s asignó a %(tecnico)s como técnico responsable de la "
                "inspección. Él es quien va a ir al sitio y quien firma el informe."
            ) % {"empresa": empresa, "tecnico": tecnico},
            "in_field": _(
                "%(tecnico)s está en el sitio realizando los cinco análisis: peso, "
                "cuerpo o cola, metabisulfito, clasificación y sabor."
            ) % {"tecnico": tecnico},
            "done": _(
                "%(tecnico)s terminó de registrar los análisis. %(empresa)s va a "
                "revisar el informe y emitir el veredicto."
            ) % {"tecnico": tecnico, "empresa": empresa},
            "cancelled": _(
                "La verificación se canceló y la compra quedó sin efecto. El "
                "producto vuelve a estar disponible."
            ),
            "declared_draft": _(
                "Elegiste una verificación declarada por las partes: no interviene "
                "una verificadora de la plataforma. El comprador o el vendedor "
                "carga el informe (pesos, clasificación, metabisulfito, sabor, "
                "fotos y el PDF de la verificadora externa, si la hubo) y lo "
                "presenta; la otra parte lo confirma para cerrar la compra."
            ),
        }.get(self.state, "")

    # ==================================================================
    # Avisos
    # ==================================================================
    def _notify_technician_assigned(self):
        """Al asignar, avisa por correo al TÉCNICO asignado y al ADMINISTRADOR
        de la empresa (dueño de la cuenta)."""
        self.ensure_one()
        tec = self.technician_partner_id
        if not tec:
            return
        admin = self.verifier_partner_id

        # 1) Técnico asignado (si es distinto del admin): actividad + correo.
        if tec != admin:
            usuario = self.env["res.users"].sudo().search(
                [("partner_id", "=", tec.id)], limit=1)
            if usuario:
                try:
                    self.activity_schedule(
                        "mail.mail_activity_data_todo",
                        user_id=usuario.id,
                        summary=_("Verificar en campo: %s") % (self.product_id.display_name or ""),
                        note=_("Compra %s. Cantidad: %s.") % (
                            self.transaction_id.name, self.transaction_id.transaction_qty),
                    )
                except Exception:
                    pass
            self._send_template("shrimp_verification.mail_template_verification_assigned",
                                tec.email)

        # 2) Administrador de la empresa: correo indicando a quién se asignó.
        #    Se evita duplicar si el admin es el propio técnico y comparten correo.
        if admin.email and admin.email != tec.email:
            self._send_template(
                "shrimp_verification.mail_template_verification_assigned_admin",
                admin.email)

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
        """El veredicto le importa a las dos partes: al comprador porque decide
        si concluye la compra, y al vendedor porque de él salió el producto."""
        self.ensure_one()
        entregados = []
        for partner, url in self._stage_recipients(("buyer", "seller")):
            ok = self._send_template(
                "shrimp_verification.mail_template_verification_verdict",
                partner.email,
                ctx={"portal_url": url, "destinatario": partner.name},
            )
            entregados.append((partner, ok))
        self.sudo().write({"buyer_notified": True})
        self._log_notificacion(entregados)

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

        # Cuerpo o cola: lo declarado por el vendedor vs. lo verificado en campo.
        pres_lbl = {"cola": "cola directa", "entero": "entero"}
        enviado = pres_lbl.get(self.product_id.presentation)
        recibido = pres_lbl.get(self.presentation)
        if enviado or recibido:
            L.append("*Cuerpo/cola: enviado %s / recibido %s*" % (
                enviado or "—", recibido or "—"))

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

        # Sabor: resultado y criterios de cata marcados como correctos.
        res_lbl = dict(self._fields["taste_result"].selection).get(self.taste_result)
        if res_lbl or self.taste_criteria_ok_ids:
            L.append("")
            if res_lbl:
                L.append("*Sabor: %s*" % res_lbl)
            if self.taste_criteria_ok_ids:
                L.append("Cata: %s" % ", ".join(self.taste_criteria_ok_ids.mapped("name")))

        if self.incident_notes:
            L.append("")
            L.append("*Nota: %s*" % self.incident_notes.strip())

        # Enlace al informe completo (pantalla de detalle del verificador).
        site = self.env["website"].sudo()._shrimp_verifier_site()
        base = (site.domain or "").rstrip("/") or (self.get_base_url() or "").rstrip("/")
        if base and self.uuid_ref and self.verification_mode == "platform":
            L.append("")
            L.append("Informe completo: %s/verifier/verifications/%s/detail" % (
                base, self.uuid_ref))

        return "\n".join(L)

    def action_copy_whatsapp(self):
        """Muestra el parte listo para copiar."""
        self.ensure_one()
        raise UserError(self.whatsapp_report())

    # ==================================================================
    # Evidencia para el PDF
    # ==================================================================
    def photo_token(self, attachment):
        """Token del adjunto para las URLs de fotos (nunca su id)."""
        return attachment.sudo().generate_access_token()[0]

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
        faltantes = self.env["shrimp.taste.criterion"].search(
            [("active", "=", True)]) - self.taste_criteria_ok_ids
        if faltantes:
            w.append(_("Criterios de cata fuera de norma: %s.")
                     % ", ".join(faltantes.mapped("name")))
        tx_qty = self.transaction_id.transaction_qty or 0.0
        if tx_qty and self.weight_plant_lb and float_compare(
                abs(self.weight_plant_lb - tx_qty), tx_qty * 0.10, precision_digits=2) == 1:
            w.append(_("El peso en planta difiere más de un 10 %% de la cantidad comprada (%s).") % tx_qty)
        return w
