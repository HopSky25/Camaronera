from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError
from odoo.tools.float_utils import float_compare

from .shrimp_selection import PRESENTATIONS


class ShrimpProduct(models.Model):
    _name = "shrimp.product"
    _description = "Productos"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin",
                "shrimp.notify.mixin"]

    name = fields.Char(string="Nombre", required=True)
    seller_partner_id = fields.Many2one("res.partner", string="Vendedor", required=True, index=True)

    # species = fields.Char(string="Especie")

    # stage = fields.Selection(
    #     [
    #         ("nauplio", "Nauplio"),
    #         ("pl10", "PL10"),
    #         ("pl12", "PL12"),
    #         ("pl15", "PL15"),
    #         ("otro", "Otro"),
    #     ],
    #     default="pl12",
    #     ,
    # )

    # genetics_line = fields.Char(string="Línea Genética/Cepa")

    species_id = fields.Many2one(
        "shrimp.species",
        string="Especie",
        ondelete="restrict",
        index=True,
    )

    stage_id = fields.Many2one(
        "shrimp.stage",
        string="Estadío",
        ondelete="restrict",
        index=True,
    )

    genetics_line_id = fields.Many2one(
        "shrimp.genetics.line",
        string="Línea genética",
        ondelete="restrict",
        index=True,
    )
    avg_size_mg = fields.Float(string="Tamaño promedio (mg)")
    survival_rate = fields.Float(string="Supervivencia (%)")
    health_status = fields.Text(string="Estado sanitario / Observaciones")

    initial_qty = fields.Float(
        string="Cantidad inicial",
        required=True,
        default=0.0,
        help="Cantidad inicial publicada. Al crear el producto, esta cantidad genera el lote base.",
    )

    available_qty = fields.Float(
        string="Cantidad disponible",
        compute="_compute_available_qty",
        store=True,
    )

    check_request_ids = fields.One2many(
        "shrimp.check.request",
        "product_id",
        string="Solicitudes de chequeo",
    )

    reserved_qty = fields.Float(
        string="Cantidad reservada",
        compute="_compute_available_qty",
        store=True,
        help="Cantidad reservada por solicitudes de chequeo activas "
             "(solicitadas o en revisión).",
    )

    uom_id = fields.Many2one(
        "shrimp.uom",
        string="Unidad de medida",
        default=lambda self: self.env.ref("shrimp_marketplace.uom_libra", raise_if_not_found=False),
    )

    presentation = fields.Selection(PRESENTATIONS, string="Presentación", index=True)

    size_grade_id = fields.Many2one(
        "shrimp.size.grade",
        string="Talla",
        ondelete="restrict",
        index=True,
    )

    price = fields.Float(string="Precio", required=True, default=0.0)
    location = fields.Char(string="Ubicación")

    active = fields.Boolean(default=True)

    available_from = fields.Datetime(string="Disponible desde")
    available_to = fields.Datetime(string="Disponible hasta")

    cert_attachment_ids = fields.Many2many(
        "ir.attachment",
        "shrimp_prod_cert_rel",
        "product_id",
        "attachment_id",
        string="Certificados (archivos)",
        help="Campo heredado. La fuente principal de certificados debe ser certificate_line_ids.",
    )

    photo_attachment_ids = fields.Many2many(
        "ir.attachment",
        "shrimp_prod_photo_rel",
        "product_id",
        "attachment_id",
        string="Fotos",
    )

    state = fields.Selection(
        [("draft", "Borrador"), ("published", "Publicado"), ("sold", "Vendido"), ("cancel", "Cancelado")],
        string="Estado",
        default="draft",
        required=True,
        index=True,
    )

    published_date = fields.Datetime(string="Fecha de publicación", readonly=True, copy=False)

    # La camaronera también vende: el camarón de engorde a la empacadora, que
    # es la última pata de la cadena. Faltaba aquí, y el hueco solo se notaba al
    # crear: el permiso para publicar y la resolución del vendedor ya se habían
    # ampliado, pero este campo seguía admitiendo dos valores, así que el alta
    # moría con "Wrong value for seller_role: 'camaronera'".
    #
    # La empacadora NO entra: compra y exporta, no publica en el marketplace.
    seller_role = fields.Selection(
        [("semillero", "Semillero"), ("laboratorio", "Laboratorio"),
         ("camaronera", "Camaronera")],
        string="Rol del vendedor",
        required=True,
        default="semillero",
        index=True,
    )

    expected_delivery_date = fields.Date(
        string="Fecha tentativa de entrega",
        help="Fecha estimada en la que el lote estaría listo para entrega.",
    )

    transaction_ids = fields.One2many(
        "shrimp.transaction",
        "product_id",
        string="Transacciones",
        readonly=True,
    )

    certificate_line_ids = fields.One2many(
        "shrimp.product.certificate.line",
        "product_id",
        string="Certificados (detalle)",
        copy=False,
    )

    # Certificados del vendedor (heredados por el producto), solo lectura.
    seller_certificate_line_ids = fields.One2many(
        related="seller_partner_id.certificate_line_ids",
        string="Certificados del vendedor",
        readonly=True,
    )

    stock_lot_ids = fields.One2many(
        "shrimp.stock.lot",
        "product_id",
        string="Lotes",
        readonly=True,
    )

    origin_facility_id = fields.Many2one(
        "shrimp.partner.facility",
        string="Instalación de origen",
        ondelete="set null",
        index=True,
    )

    origin_pond_id = fields.Many2one(
        "shrimp.partner.pond",
        string="Piscina de origen",
        ondelete="set null",
        index=True,
    )

    # Las siembras de las que sale esta cosecha. Es el eslabón que faltaba
    # entre la larva y el camarón adulto: el lote de la cosecha nace sin
    # movimiento de origen (lo crea el vendedor), así que la trazabilidad se
    # cortaba justo ahí y la empacadora no veía ni el laboratorio ni el
    # semillero. Se rellena sola con las siembras de la piscina de origen
    # anteriores a la fecha de producción, y el dueño la puede corregir.
    origin_allocation_ids = fields.Many2many(
        "shrimp.lot.allocation", "shrimp_product_origin_alloc_rel",
        "product_id", "allocation_id", string="Siembras de origen", copy=False,
        help="Siembras (lote de larva → piscina) de las que procede este "
             "producto. Se proponen solas a partir de la piscina de origen.")

    batch_code = fields.Char(
        string="Código de lote / producción",
    )

    production_date = fields.Date(
        string="Fecha de producción",
    )

    traceability_notes = fields.Text(
        string="Notas de trazabilidad",
    )

    evolution_ids = fields.One2many(
        "shrimp.product.evolution",
        "product_id",
        string="Histórico de evolución",
        readonly=True,
    )

    evolution_count = fields.Integer(
        string="Evoluciones",
        compute="_compute_evolution_count",
    )

    # ------------------------------------------------------------------
    # URLs públicas de adjuntos: por el token del adjunto, nunca por su id
    # ------------------------------------------------------------------
    def shrimp_photo_url(self, attachment):
        self.ensure_one()
        token = attachment.sudo().generate_access_token()[0]
        return "/marketplace/product/%s/photo/%s" % (self.uuid_ref, token)

    def shrimp_cert_url(self, attachment):
        self.ensure_one()
        token = attachment.sudo().generate_access_token()[0]
        return "/marketplace/product/%s/certificate/%s" % (self.uuid_ref, token)

    def shrimp_visible_certificate_lines(self, owner_view=False):
        """Certificados del producto que se pueden mostrar.

        Al público solo los aprobados; el dueño (y el personal interno) ve
        también los pendientes y rechazados, con su estado.
        """
        self.ensure_one()
        lineas = self.sudo().certificate_line_ids
        if owner_view:
            return lineas
        return lineas.filtered(lambda l: l.status == "approved")

    def price_for_partner(self, partner):
        """Precio efectivo para un comprador. El precio por cliente se retiró:
        siempre es el precio de publicación. Se conserva el método para no
        romper a quienes lo llaman."""
        self.ensure_one()
        return self.price

    def has_purchases(self):
        """True si el producto ya tiene transacciones de compra (confirmadas/hechas)."""
        self.ensure_one()
        return bool(self.env["shrimp.transaction"].sudo().search_count([
            ("product_id", "=", self.id),
            ("state", "in", ["confirmed", "done"]),
        ]))

    # Campos críticos que NO pueden cambiar una vez que el producto tiene compras.
    _LOCKED_AFTER_PURCHASE = {
        "species_id": "Especie",
        "stage_id": "Estadío",
        "genetics_line_id": "Línea genética",
        "presentation": "Presentación",
        "size_grade_id": "Talla",
        "uom_id": "Unidad de medida",
        "price": "Precio",
        "initial_qty": "Cantidad inicial",
        "seller_role": "Rol del vendedor",
    }

    @staticmethod
    def _field_value_changed(rec, fname, newval):
        """Compara el valor nuevo (tal como llega en vals) con el actual."""
        cur = rec[fname]
        if isinstance(cur, models.BaseModel):        # Many2one
            return cur.id != (newval or False)
        if isinstance(cur, float):                   # Float (precio, cantidad)
            return float_compare(cur, newval or 0.0, precision_digits=2) != 0
        return (cur or False) != (newval or False)   # Selection / Char

    # ------------------------------------------------------------------
    # Quién puede gestionar productos y cuándo se puede publicar.
    # ------------------------------------------------------------------
    # Estas reglas vivían solo en el controlador del portal. El portal ya no
    # escribe por RPC (ACL de solo lectura), pero los flujos del servidor sí
    # escriben en sudo en nombre del usuario: por eso se validan aquí, en el
    # modelo, mirando al usuario real de la sesión (env.user sigue siendo el
    # del portal aunque el entorno esté en sudo).
    # Quién vende lo decide la matriz de capacidades ("sell_products").
    @api.model
    def _shrimp_user_can_manage_products(self, user=None):
        user = user or self.env.user
        if not user.share:
            return True
        return user.partner_id._shrimp_can("sell_products")

    @staticmethod
    def _shrimp_requires_size_grade(stage):
        """Solo el camarón de engorde tiene talla comercial (30/40, 41/50…)."""
        return (stage.code or "").strip().upper() == "ENGORDE"

    def _shrimp_is_growout_seed(self):
        """True si el lote es SEMILLA para engorde (larva, postlarva o
        juvenil): lo que una camaronera siembra. False para el camarón de
        engorde (adulto) y para un lote sin estadío (se trata como adulto).
        Decide si la camaronera que lo compra a otra lo siembra o lo
        revende (shrimp.transaction._buyer_can_republish)."""
        self.ensure_one()
        return bool(self.stage_id) and not self._shrimp_requires_size_grade(self.stage_id)

    def _shrimp_publish_problems(self, check_stock=True):
        """Motivos por los que este producto NO se puede publicar (lista)."""
        self.ensure_one()
        problemas = []
        if not self.active:
            problemas.append(_("el producto está dado de baja"))
        if check_stock and float_compare(self.available_qty or 0.0, 0.0, precision_digits=6) <= 0:
            problemas.append(_("no tiene stock disponible"))
        if self._shrimp_requires_size_grade(self.stage_id) and (
                not self.presentation or not self.size_grade_id):
            problemas.append(_("el camarón de engorde exige presentación y talla"))
        return problemas

    def action_publish(self):
        """Publica el producto aplicando TODAS las validaciones de negocio."""
        user = self.env.user
        for rec in self:
            if user.share and rec.seller_partner_id != user.partner_id:
                raise AccessError(_("Solo el vendedor publica sus productos."))
            if rec.state != "draft":
                raise ValidationError(_("Solo se publica un producto en borrador."))
            problemas = rec._shrimp_publish_problems()
            if problemas:
                raise ValidationError(_("No se puede publicar «%(p)s»: %(m)s.") % {
                    "p": rec.name, "m": "; ".join(problemas)})
            rec.with_context(shrimp_publish_checked=True).write({
                "state": "published", "published_date": fields.Datetime.now()})
        return True

    def _shrimp_check_untrusted_write(self, vals):
        """Un usuario de portal solo toca productos propios (también en sudo)
        y no publica sin pasar por las validaciones."""
        user = self.env.user
        if not user.share or self.env.context.get("shrimp_system_flow"):
            return
        if not self.env.su:
            raise AccessError(_("No tienes permiso para modificar productos."))

    def write(self, vals):
        self._shrimp_check_untrusted_write(vals)
        if vals.get("state") == "published" and not self.env.context.get("shrimp_publish_checked"):
            for rec in self.filtered(lambda r: r.state != "published"):
                # Volver de "vendido" a "publicado" al reponer stock no es una
                # publicación nueva: solo se exige la talla.
                problemas = rec._shrimp_publish_problems(check_stock=rec.state != "sold")
                if problemas:
                    raise ValidationError(_("No se puede publicar «%(p)s»: %(m)s.") % {
                        "p": rec.name, "m": "; ".join(problemas)})
        # Restricción: campos críticos bloqueados si el producto ya tiene compras.
        present = set(self._LOCKED_AFTER_PURCHASE).intersection(vals.keys())
        if self.env.context.get("shrimp_profile_transfer"):
            # Mover el producto a otro perfil de la MISMA cuenta
            # (action_change_profile, que ya validó sus reglas y deja la
            # transferencia interna registrada) sí puede cambiar el rol.
            present.discard("seller_role")
        if present:
            for rec in self:
                if not rec.has_purchases():
                    continue
                blocked = [f for f in present if self._field_value_changed(rec, f, vals[f])]
                if blocked:
                    labels = ", ".join(self._LOCKED_AFTER_PURCHASE[f] for f in blocked)
                    raise ValidationError(_(
                        "No puedes modificar estos campos de «%s» porque ya tiene "
                        "compras registradas: %s."
                    ) % (rec.name, labels))

        # Solo lo BIOLÓGICO deja fila de evolución. El stock y el estado
        # comercial (vendido, publicado) cambian con cada venta y llenaban el
        # histórico —y el certificado del comprador— de «Actualización
        # automática del producto» que además enseñaban el stock que le queda
        # al vendedor. Las cantidades ya se documentan con los movimientos.
        tracked_fields = {
            "stage_id",
            "avg_size_mg",
            "survival_rate",
            "health_status",
        }

        # Se fotografía solo si algún campo rastreado CAMBIA de verdad.
        #
        # Antes bastaba con que el campo viniera en el guardado, aunque trajera
        # el mismo valor. Cualquier "guardar" sin tocar nada dejaba otra fila, y
        # un lote acabó con siete entradas idénticas de "Actualización
        # automática del producto" que ocupan media página del certificado de
        # trazabilidad: ruido en el documento que el comprador enseña.
        #
        # La comparación va ANTES del write, que es cuando todavía se puede
        # saber qué cambió, y reutiliza el mismo _field_value_changed que usa la
        # comprobación de campos bloqueados, para no tener dos criterios.
        presentes = tracked_fields.intersection(vals.keys())
        a_fotografiar = self.browse()
        if presentes and not self.env.context.get("skip_evolution_snapshot"):
            for rec in self:
                if any(self._field_value_changed(rec, f, vals[f]) for f in presentes):
                    a_fotografiar |= rec

        result = super().write(vals)

        # Cambió la piscina o la fecha de producción y el dueño no eligió las
        # siembras a mano: se vuelven a proponer.
        if {"origin_pond_id", "production_date"} & set(vals) and "origin_allocation_ids" not in vals:
            self._shrimp_autofill_origin_allocations()

        if a_fotografiar:
            for rec in a_fotografiar:
                self.env["shrimp.product.evolution"].create({
                    "product_id": rec.id,
                    "stage_id": rec.stage_id.id if rec.stage_id else False,
                    "avg_size_mg": rec.avg_size_mg,
                    "survival_rate": rec.survival_rate,
                    "health_status": rec.health_status,
                    "available_qty": rec.available_qty,
                    "note": _("Actualización automática del producto."),
                })

        return result

    @api.depends("evolution_ids")
    def _compute_evolution_count(self):
        for rec in self:
            rec.evolution_count = len(rec.evolution_ids)

    stock_lot_count = fields.Integer(string="N.º de lotes", compute="_compute_stock_lot_count")
    transaction_count = fields.Integer(string="N.º de transacciones", compute="_compute_transaction_count")

    @api.depends("transaction_ids")
    def _compute_transaction_count(self):
        for rec in self:
            rec.transaction_count = len(rec.transaction_ids)

    @api.depends("stock_lot_ids")
    def _compute_stock_lot_count(self):
        for rec in self:
            rec.stock_lot_count = len(rec.stock_lot_ids)

    @api.depends("stock_lot_ids.available_qty", "stock_lot_ids.state", "stock_lot_ids.owner_id", "seller_partner_id",
                 "check_request_ids.qty", "check_request_ids.state")
    def _compute_available_qty(self):
        for rec in self:
            seller = rec.seller_partner_id
            lots = rec.stock_lot_ids.filtered(
                lambda l: l.owner_id == seller and l.state == "available" and l.available_qty > 0
            )
            on_hand = sum(lots.mapped("available_qty"))
            # Las solicitudes de chequeo activas reservan cantidad: se descuenta
            # del disponible para que no se venda dos veces.
            reserved = sum(rec.check_request_ids.filtered(
                lambda c: c.state in ("requested", "under_review")).mapped("qty"))
            rec.reserved_qty = reserved
            rec.available_qty = max(0.0, on_hand - reserved)

    @api.constrains("seller_partner_id", "seller_role")
    def _check_seller_type(self):
        # Varios perfiles: el lote es de UN perfil del vendedor (seller_role)
        # y es ese perfil el que tiene que poder vender.
        for rec in self:
            if not rec.seller_partner_id._shrimp_can("sell_products", role=rec._shrimp_seller_role()):
                raise ValidationError(_("Solo pueden publicar productos: %s.") % (
                    self.env["res.partner"]._shrimp_types_label("sell_products", sep=_("y"))))

    def _shrimp_seller_role(self):
        """Perfil del vendedor con el que se vende este lote.

        Es seller_role cuando la cuenta tiene ese perfil (aprobado o activo).
        Si no lo tiene (datos antiguos en los que seller_role quedó con el
        valor por defecto) se usa el perfil activo del vendedor, que es lo
        que hacía la plataforma antes de los perfiles múltiples."""
        self.ensure_one()
        seller = self.seller_partner_id
        if not seller:
            return self.seller_role or False
        if self.seller_role and seller._shrimp_has_role(self.seller_role):
            return self.seller_role
        return seller._shrimp_effective_type()

    @api.constrains("initial_qty")
    def _check_initial_qty(self):
        for rec in self:
            if rec.initial_qty <= 0:
                raise ValidationError(_("La cantidad inicial debe ser mayor a 0."))

    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        if user.share:
            # Portal: ni por RPC (ACL) ni en sudo en nombre de otro.
            if not self.env.su:
                raise AccessError(_("No tienes permiso para crear productos."))
            if not self._shrimp_user_can_manage_products(user):
                raise AccessError(_("Tu tipo de cuenta no puede publicar productos."))
            for vals in vals_list:
                if vals.get("seller_partner_id") and vals["seller_partner_id"] != user.partner_id.id:
                    raise AccessError(_("Solo puedes crear productos a tu nombre."))
        # Sin rol explícito, el lote es del perfil ACTIVO del vendedor (el
        # valor por defecto "semillero" del campo era un error esperando a
        # pasar con cualquier otro vendedor).
        roles_lote = [v for v, _l in self._fields["seller_role"].selection]
        for vals in vals_list:
            if "seller_role" not in vals and vals.get("seller_partner_id"):
                activo = self.env["res.partner"].sudo().browse(
                    vals["seller_partner_id"])._shrimp_effective_type()
                if activo in roles_lote:
                    vals["seller_role"] = activo
        # Evita mensajes de chatter automáticos al crear (log de creación y
        # auto-suscripción) que generaban correos "vacíos"; el correo real
        # de "producto creado" se envía aparte con su plantilla.
        records = super(
            ShrimpProduct,
            self.with_context(mail_create_nolog=True, mail_create_nosubscribe=True, tracking_disable=True),
        ).create(vals_list)

        if not self.env.context.get("skip_initial_lot"):
            for rec in records:
                rec._create_initial_lot_if_needed()

        for rec, vals in zip(records, vals_list):
            if rec.origin_pond_id and "origin_allocation_ids" not in vals:
                rec._shrimp_autofill_origin_allocations()

        # Un producto que nace publicado pasa por las mismas validaciones que
        # la publicación manual (talla en engorde; stock por initial_qty > 0).
        for rec in records.filtered(lambda r: r.state == "published"):
            problemas = rec._shrimp_publish_problems(check_stock=False)
            if problemas:
                raise ValidationError(_("No se puede publicar «%(p)s»: %(m)s.") % {
                    "p": rec.name, "m": "; ".join(problemas)})

        return records


    @api.constrains("presentation", "size_grade_id")
    def _check_size_grade_presentation(self):
        for rec in self:
            if rec.size_grade_id and rec.presentation and rec.size_grade_id.presentation != rec.presentation:
                raise ValidationError(_("La talla seleccionada no corresponde a la presentación elegida."))

    @api.constrains("origin_facility_id", "origin_pond_id", "seller_partner_id")
    def _check_origin_facility_pond(self):
        for rec in self:
            if rec.origin_facility_id and rec.origin_facility_id.partner_id != rec.seller_partner_id:
                raise ValidationError(_("La instalación de origen debe pertenecer al mismo vendedor."))

            if rec.origin_pond_id and rec.origin_pond_id.partner_id != rec.seller_partner_id:
                raise ValidationError(_("La piscina de origen debe pertenecer al mismo vendedor."))

            if rec.origin_pond_id and rec.origin_facility_id and rec.origin_pond_id.facility_id != rec.origin_facility_id:
                raise ValidationError(_("La piscina seleccionada no pertenece a la instalación de origen."))

    @api.constrains("origin_allocation_ids", "seller_partner_id")
    def _check_origin_allocations(self):
        for rec in self:
            ajenas = rec.origin_allocation_ids.sudo().filtered(
                lambda a: a.partner_id != rec.seller_partner_id)
            if ajenas:
                raise ValidationError(_(
                    "Las siembras de origen tienen que ser de piscinas del propio vendedor."))

    def _shrimp_candidate_origin_allocations(self):
        """Siembras de la piscina de origen vigentes a la fecha de producción."""
        self.ensure_one()
        if not self.origin_pond_id:
            return self.env["shrimp.lot.allocation"]
        tope = self.production_date or fields.Date.context_today(self)
        return self.env["shrimp.lot.allocation"].sudo().search([
            ("pond_id", "=", self.origin_pond_id.id),
            ("state", "in", ("allocated", "released")),
            ("allocation_date", "<=", tope),
        ], order="allocation_date asc, id asc")

    def _shrimp_autofill_origin_allocations(self):
        for rec in self:
            candidatas = rec._shrimp_candidate_origin_allocations()
            if set(candidatas.ids) != set(rec.origin_allocation_ids.ids):
                rec.sudo().with_context(skip_evolution_snapshot=True, shrimp_system_flow=True).write(
                    {"origin_allocation_ids": [(6, 0, candidatas.ids)]})

    @api.model
    def _shrimp_origin_allocation_options(self, partner):
        """Siembras del vendedor que puede elegir como origen (portal)."""
        if not partner:
            return self.env["shrimp.lot.allocation"]
        return self.env["shrimp.lot.allocation"].sudo().search([
            ("partner_id", "=", partner.id),
            ("state", "in", ("allocated", "released")),
        ], order="allocation_date desc, id desc", limit=200)

    def _shrimp_origin_moves(self):
        """Movimientos de los que procede físicamente este producto cuando su
        lote nace sin movimiento de origen (cosecha): la siembra de cada
        siembra de origen, o, si es antigua y no la tiene, el movimiento de
        origen del lote sembrado."""
        Move = self.env["shrimp.stock.move"].sudo()
        moves = Move
        for alloc in self.sudo().origin_allocation_ids:
            moves |= alloc.sowing_move_id or alloc.stock_lot_id.origin_move_id
        return moves

    # ------------------------------------------------------------------
    # Producción real declarada (laboratorio)
    # ------------------------------------------------------------------
    def action_declare_production(self, actual_qty, reason=None, actor=None, date=None):
        """El dueño declara cuánto salió de verdad (p. ej. 90.000 millares de
        nauplio -> 64.800 de PL12 con 72 % de supervivencia).

        Ajusta los lotes del vendedor con un movimiento «production» que
        documenta el antes, el después y el motivo. Devuelve los movimientos.
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if self.env.user.share and actor != self.seller_partner_id:
            raise AccessError(_("La producción la declara el dueño del lote."))
        actual_qty = float(actual_qty or 0.0)
        if actual_qty < 0:
            raise ValidationError(_("La producción real no puede ser negativa."))
        lots = self.env["shrimp.stock.lot"].sudo().search([
            ("product_id", "=", self.id), ("owner_id", "=", self.seller_partner_id.id),
            ("state", "=", "available"), ("available_qty", ">", 0)], order="id asc")
        if not lots:
            raise ValidationError(_("Este producto no tiene stock propio que ajustar."))
        actual = sum(lots.mapped("available_qty"))
        delta = actual_qty - actual
        pct = (100.0 * actual_qty / actual) if actual else 0.0
        motivo = reason or _("Producción real declarada: %(antes)s → %(despues)s (%(pct)s %%)") % {
            "antes": self.env["shrimp.transaction"].shrimp_fmt_qty(actual),
            "despues": self.env["shrimp.transaction"].shrimp_fmt_qty(actual_qty),
            "pct": self.env["shrimp.transaction"].shrimp_fmt_num(pct, 1)}
        moves = self.env["shrimp.stock.move"]
        if float_compare(delta, 0.0, precision_digits=6) == 0:
            return moves
        if delta > 0:
            moves |= lots[-1]._shrimp_internal_move("production", delta, motivo, direction="in", date=date)
        else:
            falta = -delta
            for lot in lots:
                if float_compare(falta, 0.0, precision_digits=6) <= 0:
                    break
                take = min(lot.available_qty, falta)
                moves |= lot._shrimp_internal_move("production", take, motivo, direction="out", date=date)
                falta -= take
        if self.state in ("published", "sold"):
            self._update_state_from_stock()
        return moves

    def _create_initial_lot_if_needed(self):
        self.ensure_one()
        StockLot = self.env["shrimp.stock.lot"]

        has_lot = StockLot.search_count([
            ("product_id", "=", self.id),
            ("owner_id", "=", self.seller_partner_id.id),
        ])

        if not has_lot:
            StockLot.create({
                "product_id": self.id,
                "owner_id": self.seller_partner_id.id,
                "origin_move_id": False,
                "initial_qty": self.initial_qty,
                "available_qty": self.initial_qty,
                "uom_id": self.uom_id.id,
                "state": "available",
            })

    def action_view_transactions(self):
        self.ensure_one()
        action = self.env.ref("shrimp_marketplace.action_shrimp_transaction").read()[0]
        action["domain"] = [("product_id", "=", self.id)]
        action["context"] = {
            "default_product_id": self.id,
            "search_default_product_id": self.id,
        }
        return action

    def action_view_stock_lots(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Lotes"),
            "res_model": "shrimp.stock.lot",
            "view_mode": "list,form",
            "domain": [("product_id", "=", self.id)],
            "context": {
                "default_product_id": self.id,
                "search_default_product_id": self.id,
            },
        }

    def _get_tx_type_by_partners(self, seller_partner, buyer_partner, seller_role=None,
                                 buyer_role=None):
        """Tipo de transacción para esta pareja, validando quién compra a quién
        con la matriz de capacidades (la misma regla que aplica la restricción
        de shrimp.transaction y que el catálogo usa para pintar «Comprar»).

        Varios perfiles: cuenta el ROL del lote (seller_role; por defecto el
        de este producto) y el rol con el que compra el comprador
        (buyer_role; por defecto su perfil activo)."""
        Tx = self.env["shrimp.transaction"]
        if not seller_role:
            seller_role = (self._shrimp_seller_role() if len(self) == 1
                           and self.seller_partner_id == seller_partner
                           else seller_partner._shrimp_effective_type())
        motivo = Tx._shrimp_buyer_problem(seller_partner, buyer_partner,
                                          seller_role=seller_role, buyer_role=buyer_role)
        if motivo:
            raise ValidationError(motivo)
        # El tipo depende de la PAREJA de roles: al mismo nivel
        # (camaronera → camaronera...) tiene tipo propio.
        buyer_role = (buyer_role or self.env.context.get("shrimp_buyer_role")
                      or buyer_partner._shrimp_effective_type())
        return Tx._shrimp_tx_type(seller_role, buyer_role)

    # ------------------------------------------------------------------
    # Verificación obligatoria
    # ------------------------------------------------------------------
    def _shrimp_requires_verification(self):
        """True si este lote solo se puede comprar con verificación en campo.

        El marketplace no verifica nada: lo decide shrimp_verification (camarón
        adulto). Se pregunta aquí para que TODOS los caminos de compra
        (directa, chequeo, reserva, API) apliquen la misma regla.
        """
        self.ensure_one()
        return False

    def _shrimp_check_direct_purchase_allowed(self):
        """Corta la compra directa de un lote con verificación obligatoria.

        Solo la deja pasar el flujo verificado, que lo declara con el contexto
        ``shrimp_verified_flow``. Antes la obligación solo vivía en la
        plantilla: un POST a mano, una solicitud de chequeo o una reserva
        compraban camarón adulto sin que nadie lo inspeccionara.
        """
        self.ensure_one()
        if self._shrimp_requires_verification() and not self.env.context.get(
                "shrimp_verified_flow"):
            raise ValidationError(_(
                "Este lote exige verificación en campo antes de concretar la "
                "compra. Cómpralo con verificación."))

    def execute_purchase_flow(self, buyer_partner, qty, buyer_role=None):
        """Compra directa. `buyer_role`: perfil con el que compra (por defecto
        su perfil activo); queda en transaction.buyer_role."""
        self.ensure_one()
        buyer_role = buyer_role or self.env.context.get("shrimp_buyer_role") \
            or buyer_partner._shrimp_effective_type()

        seller_partner = self.seller_partner_id

        if not self.active:
            raise ValidationError(_("El producto no está activo."))

        if self.state != "published":
            raise ValidationError(_("Solo se pueden comprar productos publicados."))

        if buyer_partner.id == seller_partner.id:
            raise ValidationError(_("No puedes comprar tu propio producto."))

        # La regla de quién compra a quién vive en el modelo (también la
        # aplica el controlador, pero no debe depender de él).
        motivo = self.motivo_no_comprable(buyer_partner, role=buyer_role)
        if motivo:
            raise ValidationError(motivo)

        if qty <= 0:
            raise ValidationError(_("La cantidad debe ser mayor a 0."))

        self._shrimp_check_direct_purchase_allowed()

        if float_compare(qty, self.available_qty, precision_digits=6) == 1:
            raise ValidationError(_("La cantidad solicitada supera el stock disponible."))

        source_lots = self.env["shrimp.stock.lot"].search([
            ("product_id", "=", self.id),
            ("owner_id", "=", seller_partner.id),
            ("state", "=", "available"),
            ("available_qty", ">", 0),
        ], order="id asc")

        if not source_lots:
            raise ValidationError(_("Este producto no tiene lotes disponibles para la venta."))

        total_lot_qty = sum(source_lots.mapped("available_qty"))
        if float_compare(qty, total_lot_qty, precision_digits=6) == 1:
            raise ValidationError(_("El vendedor no tiene suficiente stock en sus lotes."))

        seller_role = self._shrimp_seller_role()
        tx_type = self._get_tx_type_by_partners(seller_partner, buyer_partner,
                                                seller_role=seller_role, buyer_role=buyer_role)

        # Precio efectivo: el mismo cálculo en todos los caminos de compra
        # (directa, chequeo, verificada, reserva).
        unit_price = self.price_for_partner(buyer_partner)

        tx_vals = {
            "transaction_type": tx_type,
            "product_id": self.id,
            "seller_partner_id": seller_partner.id,
            "buyer_partner_id": buyer_partner.id,
            "seller_role": seller_role,
            "buyer_role": buyer_role,
            "location": self.location,
            "state": "draft",
            "transaction_qty": qty,
            "price_unit": unit_price,
            "amount_total": qty * unit_price,
        }

        # Lo que vende un semillero (a un laboratorio o a otro semillero) se
        # registra como cantidad vendida.
        if seller_role == "semillero":
            tx_vals.update({
                "sold_qty": qty,
                "sold_date": fields.Date.context_today(self),
                "production_note": self.name,
            })
        else:
            tx_vals.update({
                "desired_qty": qty,
                "desired_date": self.expected_delivery_date or fields.Date.context_today(self),
                "code": self.name,
            })

        tx = self.env["shrimp.transaction"].create(tx_vals)
        tx.action_confirm()

        buyer_new_product = tx.result_product_id if hasattr(tx, "result_product_id") else False

        source_lot_ids = source_lots.filtered(lambda l: l.available_qty > 0)[:1]

        return {
            "transaction": tx,
            "new_product": buyer_new_product,
            "source_lot_ids": source_lot_ids,
        }

    def _update_state_from_stock(self):
        for rec in self:
            if float_compare(rec.available_qty, 0.0, precision_digits=6) <= 0:
                rec.write({"state": "sold"})
            elif rec.state == "sold" and float_compare(rec.available_qty, 0.0, precision_digits=6) == 1:
                rec.with_context(shrimp_publish_checked=True).write({"state": "published"})

    # ------------------------------------------------------------------
    # Quién puede comprar este lote
    # ------------------------------------------------------------------
    # La cadena tiene un orden: el semillero vende al laboratorio, el
    # laboratorio a la camaronera. Los controladores ya lo hacían cumplir
    # cortando con un 403, pero el catálogo pintaba "Comprar" en todo: de las
    # veinte tarjetas de la primera página, quince acababan en "No tienes
    # autorización". El usuario no lee eso como una regla de negocio, lo lee
    # como que la aplicación está rota.
    #
    # La regla vive aquí, en el modelo, y la usan la plantilla y el guard del
    # controlador. Dos copias de la misma regla se separan siempre.
    def motivo_no_comprable(self, partner, role=None):
        """Cadena vacía si `partner` puede comprar este lote; si no, el porqué.

        `role`: perfil con el que compraría (por defecto su perfil activo).

        Devuelve texto y no un booleano a propósito: la tarjeta necesita
        explicar por qué no se puede comprar, y "no puedes" sin razón es lo
        que hace que alguien piense que hay un error. La regla es la de la
        matriz de capacidades, la misma que aplica la restricción de la
        transacción.
        """
        self.ensure_one()
        if not partner:
            return _("Inicia sesión para comprar.")
        if partner.id == self.seller_partner_id.id:
            return _("Es tu propio lote.")
        # Otro perfil/contacto de la misma empresa: _shrimp_buyer_problem lo
        # explica (comprarse a sí mismo no se permite ni al mismo nivel).
        return self.env["shrimp.transaction"]._shrimp_buyer_problem(
            self.seller_partner_id, partner, seller_role=self._shrimp_seller_role(),
            buyer_role=role)
