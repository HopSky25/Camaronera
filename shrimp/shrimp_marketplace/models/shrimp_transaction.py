import base64
import re

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools.float_utils import float_compare, float_is_zero


class ShrimpTransaction(models.Model):
    _name = "shrimp.transaction"
    _description = "Transacciones Shrimp"
    _inherit = ["mail.thread", "mail.activity.mixin", "shrimp.uuid.mixin"]
    _order = "create_date desc"

    name = fields.Char(
        string="Referencia",
        required=True,
        copy=False,
        default=lambda self: _("Nuevo"),
        tracking=True,
    )

    state = fields.Selection(
        [("draft", "Borrador"), ("confirmed", "Confirmada"), ("done", "Completada"), ("cancel", "Cancelada")],
        string="Estado",
        default="draft",
        tracking=True,
    )

    transaction_type = fields.Selection(
        [
            ("semillero_to_laboratorio", "Semillero → Laboratorio"),
            ("laboratorio_to_camaronera", "Laboratorio → Camaronera"),
            ("camaronera_to_buyer", "Camaronera → Comprador"),
            # Compras al mismo nivel de la cadena (ver _TX_TYPE_SAME_LEVEL).
            ("semillero_to_semillero", "Semillero → Semillero"),
            ("laboratorio_to_laboratorio", "Laboratorio → Laboratorio"),
            ("camaronera_to_camaronera", "Camaronera → Camaronera"),
        ],
        string="Tipo de transacción",
        required=True,
        tracking=True,
    )

    product_id = fields.Many2one("shrimp.product", string="Producto", required=True)
    seller_partner_id = fields.Many2one("res.partner", string="Vendedor", required=True, tracking=True)
    buyer_partner_id = fields.Many2one("res.partner", string="Comprador", required=True, tracking=True)

    # Con qué PERFIL actuó cada parte (una cuenta puede ser a la vez
    # laboratorio y camaronera). Se fijan al crear la transacción y no se
    # recalculan: si mañana la cuenta cambia de perfil activo, la compra
    # sigue diciendo con qué rol se hizo. La trazabilidad los muestra en cada
    # eslabón.
    seller_role = fields.Selection(
        selection="_selection_shrimp_role", string="Rol del vendedor", index=True,
        copy=False, help="Perfil con el que vendió: el rol del lote (product.seller_role).")
    buyer_role = fields.Selection(
        selection="_selection_shrimp_role", string="Rol del comprador", index=True,
        copy=False, help="Perfil con el que compró (su perfil activo al comprar).")

    @api.model
    def _selection_shrimp_role(self):
        return list(self.env["res.partner"]._fields["shrimp_user_type"].selection)

    def shrimp_role_label(self, role):
        """Etiqueta legible de un código de perfil (para plantillas)."""
        return self.env["res.partner"]._shrimp_type_label(role) if role else ""

    location = fields.Char(string="Ubicación", tracking=True)

    invoice_attachment_ids = fields.Many2many(
        "ir.attachment", "shrimp_tx_invoice_rel", "tx_id", "attachment_id", string="Factura(s)"
    )

    production_note = fields.Char(string="Producción / Lote")
    sold_qty = fields.Float(string="Cantidad vendida")
    sold_date = fields.Date(string="Fecha de venta")

    desired_qty = fields.Float(string="Cantidad deseada")
    desired_date = fields.Date(string="Fecha")

    # La fecha de entrega que de verdad manda, almacenada para poder filtrar y
    # ordenar por ella.
    #
    # Antes el portal filtraba y ordenaba por product_id.expected_delivery_date
    # mientras action_receive exigía _delivery_date(), que da prioridad a
    # desired_date. Y no es un matiz: 97 de las 98 transacciones tienen
    # desired_date, y no siempre coincide con la del producto —TXN-000078 pide
    # el 15/05 y el producto dice 22/12—. Resultado: la columna "Entrega"
    # mostraba una fecha y el filtro respondía por otra.
    delivery_date = fields.Date(
        string="Entrega", compute="_compute_delivery_date", store=True,
        index=True,
        help="La fecha comprometida con el comprador si existe; si no, la "
             "prevista del lote.")

    @api.depends("desired_date", "product_id.expected_delivery_date")
    def _compute_delivery_date(self):
        for rec in self:
            rec.delivery_date = (rec.desired_date
                                 or rec.product_id.expected_delivery_date
                                 or False)
    code = fields.Char(string="Código")

    transaction_qty = fields.Float(string="Cantidad operativa", required=True, tracking=True)

    # Precio congelado al momento de la operación (el precio del producto puede
    # cambiar después, por eso se guarda aquí).
    price_unit = fields.Float(string="Precio unitario", tracking=True)
    amount_total = fields.Float(string="Total pagado", tracking=True)

    # Factura de MERCADERÍA emitida por la plataforma: solo existe en datos
    # antiguos. La plataforma ya no factura bienes (ver action_generar_factura
    # y shrimp.charge); se conserva el campo para no perder ese historial.
    invoice_id = fields.Many2one(
        "account.move", string="Factura (histórica)", readonly=True, copy=False,
        help="Factura de mercadería emitida por la plataforma en versiones "
             "anteriores. Hoy la mercadería la factura el vendedor.")

    # ------------------------------------------------------------------
    # Factura del VENDEDOR (paso 2 de la facturación)
    # ------------------------------------------------------------------
    # La mercadería la factura el vendedor desde su propio sistema. Aquí solo
    # queda constancia (opcional) de esa factura para el comprador y la
    # trazabilidad privada: número, clave de acceso, fecha y el XML/PDF.
    seller_invoice_number = fields.Char(
        string="N.º de factura del vendedor", copy=False, tracking=True,
        help="Formato SRI 001-001-000000001.")
    seller_invoice_access_key = fields.Char(
        string="Clave de acceso (SRI)", copy=False,
        help="Los 49 dígitos de la clave de acceso del comprobante electrónico.")
    seller_invoice_date = fields.Date(string="Fecha de la factura del vendedor", copy=False)
    seller_invoice_attachment_id = fields.Many2one(
        "ir.attachment", string="Archivo de la factura (XML o PDF)", copy=False,
        ondelete="set null")
    seller_invoice_registered_at = fields.Datetime(
        string="Factura registrada el", readonly=True, copy=False)
    has_seller_invoice = fields.Boolean(
        string="Factura del vendedor registrada", compute="_compute_has_seller_invoice")

    @api.depends("seller_invoice_number", "seller_invoice_access_key",
                 "seller_invoice_attachment_id")
    def _compute_has_seller_invoice(self):
        for rec in self:
            rec.has_seller_invoice = bool(
                rec.seller_invoice_number or rec.seller_invoice_access_key
                or rec.seller_invoice_attachment_id)

    _RE_NUMERO_SRI = re.compile(r"^\d{3}-\d{3}-\d{9}$")
    _RE_CLAVE_SRI = re.compile(r"^\d{49}$")

    @api.constrains("seller_invoice_number", "seller_invoice_access_key")
    def _check_seller_invoice(self):
        for rec in self:
            if rec.seller_invoice_number and not self._RE_NUMERO_SRI.match(rec.seller_invoice_number):
                raise ValidationError(_(
                    "El número de la factura del vendedor debe tener el formato "
                    "001-001-000000001."))
            if rec.seller_invoice_access_key and not self._RE_CLAVE_SRI.match(
                    rec.seller_invoice_access_key):
                raise ValidationError(_("La clave de acceso tiene 49 dígitos."))

    def action_register_seller_invoice(self, vals, actor=None):
        """El vendedor deja constancia de la factura que él emitió.

        Solo el vendedor de la compra, y solo con la compra confirmada o
        completada: antes no hay venta que facturar. `vals` admite number,
        access_key, date y attachment_id (ya validado por el controlador).
        """
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.seller_partner_id and self.env.user.share:
            raise AccessError(_("La factura de la mercadería la registra el vendedor."))
        if self.state not in ("confirmed", "done"):
            raise ValidationError(_(
                "La factura se registra cuando la compra está confirmada."))
        numero = (vals.get("number") or "").strip() or False
        clave = re.sub(r"\s", "", vals.get("access_key") or "") or False
        if not (numero or clave or vals.get("attachment_id")):
            raise ValidationError(_(
                "Indica al menos el número, la clave de acceso o el archivo de la factura."))
        escribir = {
            "seller_invoice_number": numero,
            "seller_invoice_access_key": clave,
            "seller_invoice_date": vals.get("date") or False,
            "seller_invoice_registered_at": fields.Datetime.now(),
        }
        if vals.get("attachment_id"):
            escribir["seller_invoice_attachment_id"] = vals["attachment_id"]
        self.sudo().write(escribir)
        self.sudo().message_post(body=_("El vendedor registró su factura %s.")
                                 % (numero or clave or _("(archivo)")))
        return True

    def _get_mkt_sale_product(self):
        """Obsoleto: la plataforma ya no factura mercadería."""
        raise UserError(_(
            "La plataforma no factura mercadería: la factura la emite el vendedor "
            "desde su sistema y aquí solo se registra."))

    def action_generar_factura(self):
        """OBSOLETO. La plataforma ya no emite facturas de mercadería.

        Se conserva el nombre para que ningún botón o integración vieja falle
        en silencio: devuelve la factura histórica si existe y, si no, explica
        el flujo actual en vez de contabilizar nada.
        """
        self.ensure_one()
        if self.invoice_id:
            return self.invoice_id
        raise UserError(_(
            "La plataforma solo factura sus servicios (comisión, verificación, "
            "empaque). La mercadería la factura el vendedor y la registra en la "
            "compra (número, clave de acceso o archivo)."))

    # Utilidad: la transacción usa "cancel" y los demás modelos "cancelled".
    # No se renombra el valor guardado (sería una migración de datos sin
    # beneficio real); para preguntar se usa esto.
    charge_ids = fields.One2many(
        "shrimp.charge", "transaction_id", string="Cobros de la plataforma")

    is_cancelled = fields.Boolean(
        string="Cancelada", compute="_compute_is_cancelled")

    @api.depends("state")
    def _compute_is_cancelled(self):
        for rec in self:
            rec.is_cancelled = rec.state == "cancel"

    stock_move_ids = fields.One2many(
        "shrimp.stock.move",
        "transaction_id",
        string="Movimientos de stock",
    )

    available_qty_seller = fields.Float(
        compute="_compute_available_qty_seller",
        string="Stock disponible vendedor"
    )

    result_product_id = fields.Many2one(
        "shrimp.product",
        string="Producto resultado",
        readonly=True,
        ondelete="set null",
    )

    # Cuándo confirmó el comprador que recibió la mercadería. La fecha de
    # entrega (delivery_date) es la COMPROMETIDA; la trazabilidad y la API
    # tienen que dar la real.
    received_date = fields.Datetime(string="Recibida el", readonly=True, copy=False)

    # Mismo padding que data/sequence.xml. Solo se usa para el respaldo de
    # abajo; la referencia buena la emite ir.sequence.
    _REF_PREFIJO = "TXN-"
    _REF_PADDING = 10

    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]

        for vals in vals_list:
            if vals.get("name", _("Nuevo")) == _("Nuevo"):
                # El respaldo iba traducido y con 6 dígitos: una referencia no se
                # traduce (cambiaría según el idioma de quien crea el registro) y
                # con 6 no cuadra con lo que emite la secuencia, así que el único
                # caso en que se ve —secuencia ausente— dejaba un identificador
                # con otro formato que los filtros del portal no encuentran.
                vals["name"] = (
                    seq.next_by_code("shrimp.transaction")
                    or self._REF_PREFIJO + "0" * self._REF_PADDING
                )

        if self._shrimp_untrusted_env():
            # El portal no crea compras por RPC: las crea el servidor (en
            # sudo) tras validar producto, stock, rol y precio.
            raise AccessError(_("No tienes permiso para crear transacciones."))
        for vals in vals_list:
            self._shrimp_default_roles(vals)
            self._shrimp_normalize_tx_type(vals)
        return super().create(vals_list)

    # Lo que puede cambiar el tipo de transacción: el tipo sale de la pareja
    # de roles (vendedor, comprador).
    _SHRIMP_TX_TYPE_INPUTS = frozenset({
        "transaction_type", "seller_role", "buyer_role", "seller_partner_id", "buyer_partner_id"})

    def _shrimp_normalize_tx_type(self, vals, rec=None):
        """Ajusta transaction_type a la pareja de roles de la compra.

        El tipo ya no depende solo del vendedor: camaronera → empacadora es
        «camaronera_to_buyer» y camaronera → camaronera, «camaronera_to_camaronera».
        Quien crea o cambia una compra indicando el tipo «de la cadena» (datos
        y demos anteriores, integraciones) o cambia el comprador (p. ej. de
        una camaronera a una empacadora) no tiene que recalcularlo: se deriva
        aquí. Solo se corrige el lado del COMPRADOR: si el tipo no
        corresponde al rol del vendedor, lo rechaza _check_types.
        """
        tipo = vals.get("transaction_type", rec.transaction_type if rec else False)
        vende = vals.get("seller_role", rec.seller_role if rec else False)
        compra = vals.get("buyer_role", rec.buyer_role if rec else False)
        if not (tipo and vende and compra) or self._shrimp_tx_type_seller_role(tipo) != vende:
            return vals
        nuevo = self._shrimp_tx_type(vende, compra)
        if nuevo and nuevo != tipo:
            vals["transaction_type"] = nuevo
        return vals

    @api.model
    def _shrimp_default_roles(self, vals):
        """Rellena seller_role / buyer_role si no vienen.

        - Vendedor: el rol del lote (product.seller_role) si la cuenta lo
          tiene; si no, su perfil activo.
        - Comprador: el contexto ``shrimp_buyer_role`` (lo pone
          execute_purchase_flow) o su perfil activo.
        """
        Partner = self.env["res.partner"].sudo()
        if not vals.get("seller_role") and vals.get("seller_partner_id"):
            seller = Partner.browse(vals["seller_partner_id"])
            product = self.env["shrimp.product"].sudo().browse(vals.get("product_id") or [])
            vals["seller_role"] = (product._shrimp_seller_role() if product
                                   else seller._shrimp_active_role()) or False
        if not vals.get("buyer_role") and vals.get("buyer_partner_id"):
            buyer = Partner.browse(vals["buyer_partner_id"])
            rol = self.env.context.get("shrimp_buyer_role")
            vals["buyer_role"] = (rol if rol and buyer._shrimp_has_role(rol)
                                  else buyer._shrimp_active_role()) or False
        return vals

    # Campos que fijan el trato: solo los mueve el servidor (flujos en sudo o
    # usuarios internos). Defensa en profundidad además de la ACL de portal
    # de solo lectura: si alguien vuelve a abrir la escritura al portal, el
    # comprador seguiría sin poder bajarse el precio o darse la compra por
    # recibida.
    _SHRIMP_PROTECTED_FIELDS = frozenset({
        "state", "price_unit", "amount_total", "seller_partner_id",
        "buyer_partner_id", "transaction_qty", "sold_qty", "desired_qty",
        "product_id", "transaction_type", "invoice_id", "result_product_id",
        "seller_role", "buyer_role", "seller_invoice_number", "seller_invoice_access_key", "seller_invoice_date",
        "seller_invoice_attachment_id", "seller_invoice_registered_at",
    })

    def _shrimp_untrusted_env(self):
        """True si quien escribe es un usuario de portal/público fuera de sudo."""
        return not self.env.su and self.env.user.share

    def write(self, vals):
        if self._shrimp_untrusted_env():
            tocados = self._SHRIMP_PROTECTED_FIELDS.intersection(vals)
            if tocados:
                raise AccessError(_(
                    "No puedes modificar estos datos de la transacción: %s."
                ) % ", ".join(sorted(tocados)))
        # Si el servidor cambia una de las partes sin decir con qué perfil
        # actúa la nueva, se toma el de siempre (rol del lote / perfil activo).
        if ("buyer_partner_id" in vals and "buyer_role" not in vals) \
                or ("seller_partner_id" in vals and "seller_role" not in vals):
            vals = dict(vals)
            ref = {"product_id": vals.get("product_id") or (self[:1].product_id.id if len(self) == 1 else False)}
            if "buyer_partner_id" in vals and "buyer_role" not in vals:
                vals["buyer_role"] = self._shrimp_default_roles(
                    {"buyer_partner_id": vals["buyer_partner_id"]}).get("buyer_role", False)
            if "seller_partner_id" in vals and "seller_role" not in vals:
                vals["seller_role"] = self._shrimp_default_roles(
                    dict(ref, seller_partner_id=vals["seller_partner_id"])).get("seller_role", False)
        if self._SHRIMP_TX_TYPE_INPUTS.intersection(vals):
            # El tipo se deriva de los roles de CADA compra.
            por_tipo = {}
            for rec in self:
                nuevos = self._shrimp_normalize_tx_type(dict(vals), rec)
                por_tipo.setdefault(nuevos.get("transaction_type"), self.browse())
                por_tipo[nuevos.get("transaction_type")] |= rec
            if len(por_tipo) > 1 or (por_tipo and next(iter(por_tipo)) != vals.get("transaction_type")):
                for tipo, recs in por_tipo.items():
                    nuevos = dict(vals)
                    if tipo:
                        nuevos["transaction_type"] = tipo
                    super(ShrimpTransaction, recs).write(nuevos)
                return True
        return super().write(vals)

    @api.depends("seller_partner_id", "product_id")
    def _compute_available_qty_seller(self):
        for rec in self:
            if not rec.seller_partner_id or not rec.product_id:
                rec.available_qty_seller = 0.0
                continue

            lots = self.env["shrimp.stock.lot"].search([
                ("owner_id", "=", rec.seller_partner_id.id),
                ("product_id", "=", rec.product_id.id),
                ("state", "=", "available"),
                ("available_qty", ">", 0),
            ])
            rec.available_qty_seller = sum(lots.mapped("available_qty"))

    # Tipo de transacción según el tipo del VENDEDOR. Quién puede comprarle lo
    # decide la matriz de capacidades (res.partner._shrimp_capability_matrix,
    # capacidad "buy_from_<tipo de vendedor>"): una sola regla para el
    # catálogo, los controladores y esta restricción.
    _TX_TYPE_BY_SELLER = {
        "semillero": "semillero_to_laboratorio",
        "laboratorio": "laboratorio_to_camaronera",
        "camaronera": "camaronera_to_buyer",
    }

    # Compra al MISMO nivel (vendedor y comprador actúan con el mismo rol):
    # tipo propio, para que reportes, trazabilidad y reglas de recepción
    # distingan «camaronera → empacadora» de «camaronera → camaronera».
    # Si la matriz no habilita la pareja (buy_from_<rol>), no se llega aquí.
    _TX_TYPE_SAME_LEVEL = {
        "semillero": "semillero_to_semillero",
        "laboratorio": "laboratorio_to_laboratorio",
        "camaronera": "camaronera_to_camaronera",
    }

    @api.model
    def _shrimp_tx_type(self, seller_role, buyer_role=None):
        """Tipo de transacción para la pareja de ROLES (vendedor, comprador).

        El mismo rol a los dos lados -> tipo de mismo nivel; si no, el de
        siempre, que depende solo del rol del vendedor."""
        if seller_role and seller_role == buyer_role and seller_role in self._TX_TYPE_SAME_LEVEL:
            return self._TX_TYPE_SAME_LEVEL[seller_role]
        return self._TX_TYPE_BY_SELLER.get(seller_role)

    @api.model
    def _shrimp_tx_type_seller_role(self, tx_type):
        """Rol del vendedor que exige un tipo de transacción (o None)."""
        for tabla in (self._TX_TYPE_BY_SELLER, self._TX_TYPE_SAME_LEVEL):
            for rol, tipo in tabla.items():
                if tipo == tx_type:
                    return rol
        return None

    def _shrimp_is_same_level(self):
        """True si es una compra entre dos productores del mismo eslabón."""
        self.ensure_one()
        return self.transaction_type in self._TX_TYPE_SAME_LEVEL.values()

    @api.model
    def _shrimp_buyer_problem(self, seller, buyer, seller_role=None, buyer_role=None):
        """'' si `buyer` puede comprarle a `seller`; si no, el motivo.

        Varios perfiles por cuenta: la regla se aplica a los ROLES de la
        operación. `seller_role` es el rol con el que se vende (el del lote;
        por defecto el perfil activo del vendedor) y `buyer_role` el rol con
        el que se compra (por defecto el perfil activo del comprador). Un
        perfil que la cuenta no tiene aprobado no sirve para comprar.
        """
        Partner = self.env["res.partner"]
        # El contexto shrimp_buyer_role ("comprar actuando como") vale como
        # buyer_role explícito para todos los caminos de compra.
        buyer_role = buyer_role or self.env.context.get("shrimp_buyer_role") or None
        # Comprarse a sí mismo nunca: ni con otro perfil de la misma cuenta,
        # ni desde un contacto de la propia empresa, ni con el mismo RUC.
        # Ahora que se compra al mismo nivel, es la única barrera entre dos
        # perfiles iguales.
        if seller and buyer and seller._shrimp_same_entity_as(buyer):
            return _("No puedes comprarte a ti mismo: el vendedor es tu propia "
                     "empresa (aunque compres con otro perfil o desde otro "
                     "contacto). Mover producto entre tus perfiles no es una compra.")
        vende = seller_role or (seller._shrimp_effective_type() if seller else False)
        if vende not in self._TX_TYPE_BY_SELLER:
            return _("«%s» no es un vendedor del marketplace.") % (seller.name or "")
        compra = buyer_role or buyer._shrimp_effective_type()
        if buyer_role and not buyer._shrimp_has_role(buyer_role):
            return _("Tu cuenta no tiene aprobado el perfil «%s».") % Partner._shrimp_type_label(buyer_role)
        capacidad = "buy_from_%s" % vende
        if not Partner._shrimp_type_can(compra, capacidad):
            motivo = _("Lo que vende un %(vende)s solo lo compra un %(compra)s.") % {
                "vende": Partner._shrimp_type_label(vende),
                "compra": Partner._shrimp_types_label(capacidad) or _("comprador habilitado"),
            }
            # Si otro perfil de la misma cuenta sí puede, se le dice cuál.
            otros = [r for r in buyer._shrimp_roles_with(capacidad) if r != compra]
            if otros and compra == buyer._shrimp_effective_type():
                motivo += " " + _("Cambia tu perfil activo a «%s» para comprarlo.") % (
                    Partner._shrimp_type_label(otros[0]))
            return motivo
        return ""

    @api.constrains("transaction_type", "seller_partner_id", "buyer_partner_id",
                    "seller_role", "buyer_role")
    def _check_types(self):
        # Se comprueba solo al crear o al cambiar las partes (no en cada cambio
        # de estado): las compras antiguas siguen cerrándose sin tropezar.
        Partner = self.env["res.partner"]
        for rec in self:
            esperado = self._shrimp_tx_type_seller_role(rec.transaction_type)
            vende = rec.seller_role or rec.seller_partner_id._shrimp_effective_type()
            if esperado and vende != esperado:
                raise ValidationError(_("El vendedor debe ser %s.") % (
                    Partner._shrimp_type_label(esperado)))
            if rec.seller_role and not rec.seller_partner_id._shrimp_has_role(rec.seller_role):
                raise ValidationError(_("«%(p)s» no tiene el perfil «%(r)s».") % {
                    "p": rec.seller_partner_id.name,
                    "r": self.env["res.partner"]._shrimp_type_label(rec.seller_role)})
            motivo = self._shrimp_buyer_problem(
                rec.seller_partner_id, rec.buyer_partner_id,
                seller_role=vende, buyer_role=rec.buyer_role or None)
            if motivo:
                raise ValidationError(motivo)
            # El tipo sale de la pareja de roles: una compra al mismo nivel
            # no se graba como «→ comprador» ni al revés.
            compra = rec.buyer_role or rec.buyer_partner_id._shrimp_effective_type()
            tipo = self._shrimp_tx_type(vende, compra)
            if esperado and tipo and rec.transaction_type != tipo:
                etiquetas = dict(self._fields["transaction_type"]._description_selection(self.env))
                raise ValidationError(_(
                    "El tipo de transacción no corresponde a los perfiles de las "
                    "partes (%(v)s → %(c)s): debería ser «%(t)s».") % {
                        "v": Partner._shrimp_type_label(vende),
                        "c": Partner._shrimp_type_label(compra),
                        "t": etiquetas.get(tipo, tipo)})

    @api.constrains("seller_partner_id", "buyer_partner_id")
    def _check_partners_not_equal(self):
        for rec in self:
            if rec.seller_partner_id == rec.buyer_partner_id:
                raise ValidationError(_("El vendedor y comprador no pueden ser el mismo."))

    @api.constrains("transaction_qty")
    def _check_transaction_qty(self):
        for rec in self:
            if rec.transaction_qty <= 0:
                raise ValidationError(_("La cantidad operativa debe ser mayor a 0."))

    @api.constrains("transaction_type", "sold_qty", "desired_qty")
    def _check_qty_by_type(self):
        for rec in self:
            if rec.transaction_type == "semillero_to_laboratorio" and rec.sold_qty <= 0:
                raise ValidationError(_("La cantidad vendida debe ser mayor a 0."))

            if rec.transaction_type == "laboratorio_to_camaronera" and rec.desired_qty <= 0:
                raise ValidationError(_("La cantidad deseada debe ser mayor a 0."))

    @api.constrains("transaction_qty", "seller_partner_id", "product_id")
    def _check_stock_available(self):
        for rec in self:
            if rec.state != "draft":
                continue
            if float_compare(rec.transaction_qty, rec.available_qty_seller, precision_digits=6) == 1:
                raise ValidationError(_("No hay suficiente stock disponible."))

    def _get_transaction_qty(self):
        self.ensure_one()
        return self.transaction_qty

    def _get_source_lots(self):
        self.ensure_one()
        return self.env["shrimp.stock.lot"].search([
            ("owner_id", "=", self.seller_partner_id.id),
            ("product_id", "=", self.product_id.id),
            ("state", "=", "available"),
            ("available_qty", ">", 0),
        ], order="create_date asc, id asc")

    def _consume_source_lots(self):
        self.ensure_one()

        lots = self._get_source_lots()
        qty_to_consume = self._get_transaction_qty()
        consumed = []

        for lot in lots:
            if float_is_zero(qty_to_consume, precision_digits=6):
                break

            take_qty = min(lot.available_qty, qty_to_consume)
            new_qty = lot.available_qty - take_qty

            lot.write({
                "available_qty": new_qty,
                "state": "consumed" if float_is_zero(new_qty, precision_digits=6) else "available",
            })

            consumed.append((lot, take_qty))
            qty_to_consume -= take_qty

        if not float_is_zero(qty_to_consume, precision_digits=6):
            raise ValidationError(_("No fue posible consumir toda la cantidad solicitada desde los lotes disponibles."))

        return consumed

    def _create_stock_moves(self, consumed_lots):
        self.ensure_one()
        move_model = self.env["shrimp.stock.move"]
        moves = self.env["shrimp.stock.move"]

        for lot, take_qty in consumed_lots:
            move = move_model.create({
                "product_id": self.product_id.id,
                "source_partner_id": self.seller_partner_id.id,
                "dest_partner_id": self.buyer_partner_id.id,
                "qty": take_qty,
                "parent_move_id": lot.origin_move_id.id if lot.origin_move_id else False,
                "transaction_id": self.id,
                "date": fields.Datetime.now(),
                "move_type": "transfer",
                "lot_id": lot.id,
            })
            moves |= move

        return moves

    def _buyer_can_republish(self):
        self.ensure_one()
        rol = self.buyer_role or self.buyer_partner_id._shrimp_effective_type()
        if self._shrimp_is_same_level():
            # Mismo nivel: semillero y laboratorio revenden lo que compran a
            # un par (como hacen con lo que compran aguas arriba). La
            # camaronera revende el camarón ADULTO que le compra a otra (a una
            # empacadora o a otra camaronera, y la cadena A → B → empacadora
            # sigue enlazada por el lote); los juveniles, en cambio, los
            # siembra: le llegan como lote del producto de origen y la
            # siembra los consume, igual que la larva del laboratorio.
            if rol == "camaronera":
                return not self.product_id._shrimp_is_growout_seed()
            return True
        # El camarón adulto es producto final para la empacadora: no lo
        # revende en el marketplace.
        if self.transaction_type == "camaronera_to_buyer":
            return False
        return rol in ("semillero", "laboratorio")

    def _prepare_new_product_vals(self):
        self.ensure_one()
        product = self.product_id
        buyer = self.buyer_partner_id
        qty = self._get_transaction_qty()

        return {
            "name": product.name,
            "seller_partner_id": buyer.id,
            "species_id": product.species_id.id if product.species_id else False,
            "stage_id": product.stage_id.id if product.stage_id else False,
            "genetics_line_id": product.genetics_line_id.id if product.genetics_line_id else False,
            "avg_size_mg": product.avg_size_mg,
            "survival_rate": product.survival_rate,
            "health_status": product.health_status,
            "initial_qty": qty,
            "uom_id": product.uom_id.id,
            "price": product.price,
            "location": product.location,
            "active": True,
            "available_from": product.available_from,
            "available_to": product.available_to,
            "state": "draft",
            # El lote que nace en manos del comprador es del PERFIL con el que
            # compró (un laboratorio que compra nauplios los revende como
            # laboratorio aunque la cuenta sea también camaronera).
            "seller_role": self._shrimp_new_product_role(),
            "expected_delivery_date": product.expected_delivery_date,
            "photo_attachment_ids": [(6, 0, product.photo_attachment_ids.ids)],
            # Lo comercial del lote viaja con él: el camarón adulto que una
            # camaronera revende sigue siendo de la misma presentación y talla
            # (sin ellas no se podría publicar) y conserva el código de lote
            # y la fecha de producción de origen. La piscina y las siembras
            # de origen NO: son del vendedor anterior, y la cadena se sigue
            # por el movimiento de compra (origin_move_id del lote).
            "presentation": product.presentation or False,
            "size_grade_id": product.size_grade_id.id or False,
            "batch_code": product.batch_code or False,
            "production_date": product.production_date or False,
        }
    
    def _shrimp_new_product_role(self):
        """Rol del lote que recibe el comprador: el suyo en esta compra, si
        con ese rol puede vender; si no, el del lote de origen."""
        self.ensure_one()
        rol = self.buyer_role or self.buyer_partner_id._shrimp_effective_type()
        roles_lote = [v for v, _l in self.env["shrimp.product"]._fields["seller_role"].selection]
        if rol in roles_lote and self.env["res.partner"]._shrimp_type_can(rol, "sell_products"):
            return rol
        return self.product_id.seller_role

    def _copy_product_certificates(self, new_product):
        self.ensure_one()
        for cert in self.product_id.certificate_line_ids:
            self.env["shrimp.product.certificate.line"].with_context(
                shrimp_keep_cert_status=True).create({
                "status": cert.status,
                "product_id": new_product.id,
                "source_user_certificate_line_id": cert.source_user_certificate_line_id.id if cert.source_user_certificate_line_id else False,
                "certificate_id": cert.certificate_id.id,
                "number": cert.number,
                "issue_date": cert.issue_date,
                "expiry_date": cert.expiry_date,
                "attachment_id": cert.attachment_id.id,
                "active": cert.active,
            })

    def _create_buyer_side_records(self, moves):
        self.ensure_one()

        StockLot = self.env["shrimp.stock.lot"]
        new_product = False

        if self._buyer_can_republish():
            new_product = self.env["shrimp.product"].with_context(skip_initial_lot=True).create(
                self._prepare_new_product_vals()
            )
            self._copy_product_certificates(new_product)

            for move in moves:
                StockLot.create({
                    "product_id": new_product.id,
                    "owner_id": self.buyer_partner_id.id,
                    "origin_move_id": move.id,
                    "initial_qty": move.qty,
                    "available_qty": move.qty,
                    "uom_id": new_product.uom_id.id,
                    "state": "available",
                })

            self.result_product_id = new_product.id
        else:
            for move in moves:
                StockLot.create({
                    "product_id": self.product_id.id,
                    "owner_id": self.buyer_partner_id.id,
                    "origin_move_id": move.id,
                    "initial_qty": move.qty,
                    "available_qty": move.qty,
                    "uom_id": self.product_id.uom_id.id,
                    "state": "available",
                })

        return new_product

    def action_confirm(self):
        """Compra confirmada: se descuenta el stock del vendedor y se generan los
        movimientos (mercadería "en tránsito"). El inventario del comprador NO se
        crea aún: eso ocurre cuando el comprador confirma la recepción
        (`action_receive`), respetando la fecha de entrega.

        Es el ÚNICO punto de cierre de una compra, venga de donde venga
        (directa, chequeo aprobado, verificación aceptada, reserva): por eso la
        comisión se registra aquí, una sola vez por compra.
        """
        for rec in self:
            if rec.state != "draft":
                continue

            consumed_lots = rec._consume_source_lots()
            rec._create_stock_moves(consumed_lots)

            rec.state = "confirmed"

            rec.product_id._compute_available_qty()
            rec.product_id._update_state_from_stock()
            rec._shrimp_after_confirm()
        return True

    def _shrimp_after_confirm(self):
        """Lo que sigue a cerrar la compra: el cobro de la comisión."""
        self.ensure_one()
        self.env["shrimp.charge"].sudo()._register_commission(self)

    def _delivery_date(self):
        """Fecha de entrega a respetar para la recepción."""
        self.ensure_one()
        # El campo almacenado puede estar vacío si no hay ninguna de las dos
        # fechas; para la recepción hace falta una, así que se cae a hoy.
        return self.delivery_date or fields.Date.context_today(self)

    def action_receive(self):
        """El comprador confirma la recepción: se sube su inventario y (si aplica)
        se crea su producto en BORRADOR. Solo se permite en/después de la fecha de
        entrega."""
        for rec in self:
            if rec.state != "confirmed":
                raise ValidationError(_(
                    "Solo puedes confirmar la recepción de una compra confirmada."))

            delivery = rec._delivery_date()
            if fields.Date.context_today(rec) < delivery:
                raise ValidationError(_(
                    "Aún no puedes confirmar la recepción: la fecha de entrega es %s."
                ) % delivery.strftime("%d/%m/%Y"))

            # Idempotencia: no duplicar inventario si ya existe para estos movimientos.
            already = self.env["shrimp.stock.lot"].sudo().search_count([
                ("origin_move_id", "in", rec.stock_move_ids.ids),
                ("owner_id", "=", rec.buyer_partner_id.id),
            ])
            if not already:
                rec._create_buyer_side_records(rec.stock_move_ids)
            rec.state = "done"
            if not rec.received_date:
                rec.received_date = fields.Datetime.now()

    def action_receive_ref(self):
        """Wrapper público para invocar desde botones/controladores."""
        return self.action_receive()

    # ------------------------------------------------------------------
    # Trazabilidad: la cadena física completa de esta compra
    # ------------------------------------------------------------------
    # Tipos de movimiento interno que se enseñan de los lotes AGUAS ARRIBA (de
    # otros dueños): solo la producción declarada, que explica de dónde salió
    # la cantidad que se vendió. Del comprador (aguas abajo) se enseña todo lo
    # que le pasó a su lote: peso en planta, empaque, merma y salida.
    _UPSTREAM_INTERNAL_TYPES = ("production",)

    def get_full_traceability_data(self):
        """Cadena de la compra, del origen a la salida.

        Aguas arriba se sigue `parent_move_id` y, cuando un movimiento sale de
        un lote sin origen (la cosecha que crea el vendedor), se continúa por
        las siembras de origen del producto (product.origin_allocation_ids):
        siembra -> lote de larva -> compra al laboratorio -> nauplio del
        semillero. Aguas abajo se sigue lo que el comprador hizo con SU lote:
        ajustes de peso, empaque (lote empacado) y salida/exportación.

        Nunca entra el stock que le queda al vendedor: solo los lotes que
        nacen de un movimiento de la cadena y los del propio comprador.
        """
        self.ensure_one()
        tx = self.sudo()
        Move = self.env["shrimp.stock.move"].sudo()
        StockLot = self.env["shrimp.stock.lot"].sudo()
        Allocation = self.env["shrimp.lot.allocation"].sudo()
        Evolution = self.env["shrimp.product.evolution"].sudo()

        # 1) Aguas arriba (padres, y siembras de origen cuando no hay padre)
        upstream = {}
        chain = Move
        origin_allocs = Allocation
        todo = list(tx.stock_move_ids)
        while todo:
            m = todo.pop()
            if m.id in upstream:
                continue
            if m.parent_move_id:
                parents = m.parent_move_id
            else:
                parents = m.product_id._shrimp_origin_moves()
                origin_allocs |= m.product_id.origin_allocation_ids
            upstream[m.id] = parents
            chain |= m
            todo.extend(parents)

        chain_lots = StockLot.search([("origin_move_id", "in", chain.ids)]) if chain else StockLot
        chain_lots |= origin_allocs.mapped("stock_lot_id")
        # Producción declarada de los lotes de la cadena (otros dueños).
        extra = Move.search([
            ("lot_id", "in", chain_lots.ids),
            ("move_type", "in", self._UPSTREAM_INTERNAL_TYPES),
        ]) if chain_lots else Move

        # 2) Aguas abajo: lo que el comprador hizo con lo que recibió
        buyer = tx.buyer_partner_id
        own_lots = StockLot.search([
            ("origin_move_id", "in", tx.stock_move_ids.ids),
            ("owner_id", "=", buyer.id)]) if tx.stock_move_ids else StockLot
        downstream = Move
        frontier = own_lots
        visited = StockLot
        while frontier:
            visited |= frontier
            internos = Move.search([
                ("lot_id", "in", frontier.ids),
                ("move_type", "!=", "transfer"),
            ])
            downstream |= internos
            nuevos = StockLot.search([
                ("origin_move_id", "in", internos.ids),
                ("owner_id", "=", buyer.id),
            ]) - visited
            own_lots |= nuevos
            frontier = nuevos

        all_moves = chain | extra | downstream

        # 3) Orden topológico: el origen primero aunque su fecha sea posterior
        # (hay datos con fechas incoherentes); a igual profundidad, por fecha.
        depth_cache = {}

        def _depth(move, guard=()):
            if move.id in depth_cache:
                return depth_cache[move.id]
            if move.id in guard:
                return 0
            parents = upstream.get(move.id, move.parent_move_id)
            d = 0
            for p in parents:
                d = max(d, _depth(p, guard + (move.id,)) + 1)
            depth_cache[move.id] = d
            return d

        all_moves = all_moves.sorted(
            key=lambda m: (_depth(m), m.date or m.create_date, m.id))

        lots = chain_lots | own_lots
        allocations = origin_allocs | Allocation.search([
            ("stock_lot_id", "in", own_lots.ids)])
        allocations = allocations.sorted(lambda a: (a.allocation_date, a.id))

        products = all_moves.mapped("product_id") | tx.product_id | lots.mapped("product_id")
        evolutions = Evolution.search([
            ("product_id", "in", products.ids),
        ], order="date asc, id asc")

        exports = all_moves.filtered(lambda m: m.move_type == "export").mapped("export_id")

        return {
            "moves": all_moves,
            "lots": lots,
            "own_lots": own_lots,
            "allocations": allocations,
            "evolutions": evolutions,
            "products": products,
            "exports": exports,
        }

    def shrimp_trace_certificates(self, data=None, public=False):
        """Certificados aprobados de los productos de la cadena, SIN repetir.

        Al comprar, los certificados del lote se copian al producto que nace
        en manos del comprador; sin deduplicar, el mismo certificado salía
        dos y tres veces. Se considera el mismo si coinciden tipo y número.
        """
        self.ensure_one()
        data = data or self.get_full_traceability_data()
        lineas = data["products"].sudo().certificate_line_ids.filtered(
            lambda l: l.status == "approved")
        return self.shrimp_dedupe_certificates(lineas)

    @api.model
    def shrimp_dedupe_certificates(self, lines):
        vistos, salida = set(), lines.browse()
        for line in lines.sorted(lambda l: (l.create_date or fields.Datetime.now(), l.id)):
            clave = (line.certificate_id.id,
                     (line.number or "").strip().upper() or "id:%s" % line.id)
            if clave in vistos:
                continue
            vistos.add(clave)
            salida |= line
        return salida

    # ------------------------------------------------------------------
    # Formato: zona horaria, números y marca
    # ------------------------------------------------------------------
    _SHRIMP_DEFAULT_TZ = "America/Guayaquil"

    @api.model
    def _shrimp_tz(self, partner=None):
        """Zona horaria para mostrar fechas: la del usuario conectado, la del
        socio indicado o, por defecto, la de Ecuador continental.

        Con el contexto ``shrimp_public_tz`` (página y API públicas de un
        token de trazabilidad, que son anónimas) NO cuenta quién consulta ni
        el socio: manda la zona de la plataforma (la de la compañía principal
        si la tiene, si no Ecuador continental), para que el mismo token dé
        siempre las mismas horas a cualquiera."""
        import pytz
        if self.env.context.get("shrimp_public_tz"):
            main = self.env.ref("base.main_company", raise_if_not_found=False)
            tz = main and main.sudo().partner_id.tz
            return pytz.timezone(tz if tz in pytz.all_timezones_set else self._SHRIMP_DEFAULT_TZ)
        user = self.env.user
        for tz in ((not user._is_public() and user.tz) or None,
                   partner and partner.tz or None,
                   self._SHRIMP_DEFAULT_TZ):
            if tz and tz in pytz.all_timezones_set:
                return pytz.timezone(tz)
        return pytz.timezone(self._SHRIMP_DEFAULT_TZ)

    @api.model
    def shrimp_local_dt(self, value, partner=None):
        """Datetime UTC de Odoo -> datetime consciente en la zona local."""
        import pytz
        if not value:
            return None
        value = fields.Datetime.to_datetime(value)
        return pytz.UTC.localize(value.replace(tzinfo=None)).astimezone(self._shrimp_tz(partner))

    @api.model
    def shrimp_fmt_dt(self, value, partner=None, with_time=True):
        """'21/07/2026 04:20' en hora local (o '21/07/2026' si es una fecha)."""
        import datetime as _dt
        if not value:
            return "—"
        if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
            return value.strftime("%d/%m/%Y")
        local = self.shrimp_local_dt(value, partner)
        return local.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")

    @api.model
    def shrimp_iso_local(self, value, partner=None):
        """ISO-8601 con el desfase de la zona local (2026-07-21T04:20:00-05:00)."""
        local = self.shrimp_local_dt(value, partner)
        return local.replace(microsecond=0).isoformat() if local else None

    @api.model
    def shrimp_local_date(self, value, partner=None):
        """Fecha (sin hora) de un Datetime, en la zona local: la fecha UTC de
        una cita a las 22:00 de Guayaquil ya es el día siguiente."""
        local = self.shrimp_local_dt(value, partner)
        return local.date() if local else None

    @api.model
    def shrimp_noon_utc(self, day, partner=None):
        """Una fecha (sin hora) como mediodía local, en UTC ingenuo: para
        fechar movimientos que solo tienen día (siembra, salida) sin que la
        conversión a la zona local los mueva al día anterior."""
        import datetime as _dt
        if not day:
            return None
        import pytz
        day = fields.Date.to_date(day)
        # La zona del dueño del dato (quien siembra o despacha) manda sobre la
        # de quien lo registra.
        tz = (pytz.timezone(partner.tz) if partner and partner.tz in pytz.all_timezones_set
              else self._shrimp_tz())
        local = tz.localize(_dt.datetime.combine(day, _dt.time(12, 0)))
        return local.astimezone(_dt.timezone.utc).replace(tzinfo=None)

    def shrimp_operation_datetime(self):
        """Cuándo se cerró de verdad la operación (Datetime UTC).

        Es la fecha de la confirmación: la del movimiento de transferencia que
        crea action_confirm (el mismo que pinta la tabla de movimientos de la
        trazabilidad, la pantalla y el PDF). Si la compra aún no tiene
        movimientos, la fecha de venta registrada (sold_date) y, por último,
        la de creación del registro. Antes el certificado mostraba sold_date o
        create_date —el momento en que se creó la compra, que en una compra
        verificada o con chequeo va días antes del cierre— y no coincidía con
        la trazabilidad pública que abre su propio QR.
        """
        self.ensure_one()
        moves = self.sudo().stock_move_ids.filtered(
            lambda m: m.move_type == "transfer" and m.date)
        if moves:
            return min(moves.mapped("date"))
        if self.sold_date:
            return self.shrimp_noon_utc(self.sold_date, self.seller_partner_id)
        return self.create_date

    @api.model
    def shrimp_join(self, *parts):
        """Une los textos no vacíos con coma (para plantillas)."""
        return ", ".join(str(p) for p in parts if p)

    @api.model
    def shrimp_fmt_iso(self, value):
        """Texto ISO (de la API pública) a formato local de lectura:
        '2026-07-21' -> '21/07/2026'; '2026-07-21T04:20:00-05:00' -> '21/07/2026 04:20'."""
        import datetime as _dt
        if not value:
            return "—"
        try:
            if "T" in value:
                return _dt.datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
            return _dt.date.fromisoformat(value[:10]).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return value

    @api.model
    def _shrimp_separators(self):
        lang = self.env["res.lang"]._lang_get(self.env.lang or "es_EC")
        if lang and lang.code != "en_US" and lang.decimal_point:
            return lang.thousands_sep or "", lang.decimal_point
        return ".", ","

    @api.model
    def shrimp_fmt_num(self, value, digits=2):
        """126336 -> '126.336,00' (separadores del idioma; es_EC por defecto)."""
        miles, decimal = self._shrimp_separators()
        texto = "{:,.{d}f}".format(float(value or 0.0), d=max(0, int(digits)))
        return texto.replace(",", "\x00").replace(".", decimal).replace("\x00", miles)

    @api.model
    def shrimp_fmt_qty(self, value, uom=None, digits=None):
        """Cantidad con separador de miles: '40.000 lb', '47.912,50 lb'.
        Sin decimales si es entera; con dos si no lo es."""
        value = float(value or 0.0)
        if digits is None:
            digits = 0 if abs(value - round(value)) < 0.005 else 2
        texto = self.shrimp_fmt_num(value, digits)
        nombre = uom.name if uom and hasattr(uom, "name") else (uom or "")
        return ("%s %s" % (texto, nombre)).strip()

    @api.model
    def shrimp_brand_name(self):
        """Marca de la plataforma para los documentos (no la razón social de
        la compañía de Odoo, que en una base compartida puede ser otra)."""
        sitio = self.env["website"].sudo()._shrimp_main_site() \
            if hasattr(self.env["website"], "_shrimp_main_site") else False
        nombre = sitio.name if sitio else ""
        if not nombre or nombre in ("My Website", "Mi sitio web"):
            nombre = "CamaronMarket"
        return nombre

    def _shrimp_chain_parties(self, data=None):
        """Cadena de custodia como [(partner, código de rol)], del origen al
        destino. El rol es el de CADA eslabón (seller_role / buyer_role de la
        transacción del movimiento): una cuenta laboratorio+camaronera sale
        como laboratorio donde compró nauplios y como camaronera donde compró
        larva. Sin transacción (datos antiguos) se usa su perfil activo."""
        self.ensure_one()
        data = data or self.get_full_traceability_data()
        seq = []
        servicio = set()   # índices de eslabones de servicio (maquilador)
        for m in data["moves"]:
            tx = m.transaction_id
            if m.move_type and m.move_type != "transfer":
                # Movimiento interno: no cambia el dueño. Solo el empaque
                # añade un eslabón (la planta que prestó el servicio).
                extra = self._shrimp_chain_service_party(m)
                if extra and not (seq and seq[-1][0].id == extra[0].id):
                    # El dueño del lote va antes que la planta que le presta
                    # el servicio (si la cadena empieza en su cosecha).
                    dueno = m.source_partner_id
                    if dueno and not (seq and seq[-1][0].id == dueno.id):
                        seq.append((dueno, dueno._shrimp_effective_type()))
                    servicio.add(len(seq))
                    seq.append(extra)
                continue
            for p, rol in ((m.source_partner_id, tx.seller_role),
                           (m.dest_partner_id, tx.buyer_role)):
                if not p:
                    continue
                rol = rol or p._shrimp_effective_type()
                if seq and seq[-1][0].id == p.id and seq[-1][1] == rol:
                    continue
                # Tras el servicio de empaque el dueño sigue siendo el mismo:
                # no se repite.
                if (seq and (len(seq) - 1) in servicio and len(seq) > 1
                        and seq[-2][0].id == p.id):
                    continue
                seq.append((p, rol))
        return seq

    def _shrimp_chain_service_party(self, move):
        """(partner, rol) del eslabón de servicio de un movimiento interno, o
        None. Lo amplía shrimp_copacking para el empaque (el maquilador)."""
        return None

    def traceability_chain(self):
        """Secuencia ordenada de partners en la cadena de custodia (para el gráfico).
        Devuelve [{'name':.., 'role':..}] desde el origen hasta el destino final;
        'role' es el perfil con el que actuó en ese eslabón."""
        self.ensure_one()
        role_sel = dict(self.env["res.partner"]._fields["shrimp_user_type"]._description_selection(self.env))
        data = self.get_full_traceability_data()
        nodos = [{"name": p.name or "—", "role": role_sel.get(rol) or "Productor", "role_code": rol or False}
                 for p, rol in self._shrimp_chain_parties(data)]
        # Último eslabón: la salida de la plataforma (exportación / venta
        # externa) registrada por el comprador.
        for exp in data.get("exports", []):
            nodos.append({"name": exp.chain_label(), "role": _("Salida / Exportación"),
                          "role_code": "export"})
        return nodos

    # ------------------------------------------------------------------
    # Certificado PDF: cadena de custodia en tabla e indicadores de cultivo
    # ------------------------------------------------------------------
    def traceability_chain_rows(self, data=None):
        """Cadena de custodia en filas para el certificado PDF: eslabón (rol),
        empresa, qué hizo, fecha y referencia (TXN / OEM / EXP).

        Los eslabones son los mismos de traceability_chain() (misma lógica de
        roles, servicio de empaque y transferencias internas); aquí solo se
        les añade lo que cada uno hizo con el producto."""
        self.ensure_one()
        data = data or self.get_full_traceability_data()
        role_sel = dict(self.env["res.partner"]._fields["shrimp_user_type"]._description_selection(self.env))
        rows = []
        for partner, rol in self._shrimp_chain_parties(data):
            det = self._shrimp_chain_node_detail(partner, rol, data)
            rows.append({
                "role": role_sel.get(rol) or _("Productor"),
                "role_code": rol or False,
                "name": partner.name or "—",
                "action": " · ".join(det.get("parts") or []) or "—",
                "date": det.get("date"),
                "ref": det.get("ref") or "—",
            })
        for exp in data.get("exports", []):
            cantidad = self.shrimp_fmt_qty(exp.qty, (exp.uom_id.name or "").lower())
            if exp.destination_country_id:
                txt = _("Exportó %(qty)s a %(pais)s") % {
                    "qty": cantidad, "pais": exp.destination_country_id.name}
            else:
                txt = _("Vendió %s fuera de la plataforma") % cantidad
            rows.append({
                "role": _("Salida / Exportación"), "role_code": "export",
                "name": exp.partner_id.name or "—", "action": txt,
                "date": exp.date, "ref": exp.name or "—",
            })
        return rows

    @api.model
    def _shrimp_chain_product_short(self, product):
        """' de Nauplio' / ' de PL12' para la larva; vacío para el camarón en
        libras (la cantidad en lb ya dice qué es)."""
        if not product:
            return ""
        uom = (product.uom_id.name or "").lower()
        if uom in ("lb", "libra", "libras", "kg"):
            return ""
        nombre = product.stage_id.name or product.name or ""
        return (" " + _("de %s") % nombre) if nombre else ""

    def _shrimp_chain_node_detail(self, partner, rol, data):
        """Qué hizo un eslabón de la cadena: {'parts': [textos], 'date', 'ref'}.

        Transformaciones primero (producción declarada, siembra y cosecha,
        transferencia interna); si el eslabón no transformó nada, su compra
        y/o su venta. shrimp_copacking añade el empaque."""
        moves = data["moves"]

        def fq(qty, uom):
            # '90.000 millares', '48.000 lb': la unidad en minúscula dentro de la frase.
            nombre = uom.name if uom and hasattr(uom, "name") else (uom or "")
            return self.shrimp_fmt_qty(qty, (nombre or "").lower())

        def es_rol(actual):
            return not actual or not rol or actual == rol

        transfers = moves.filtered(lambda m: (m.move_type or "transfer") == "transfer")
        compras = transfers.filtered(
            lambda m: m.dest_partner_id == partner and es_rol(m.transaction_id.buyer_role))
        ventas = transfers.filtered(
            lambda m: m.source_partner_id == partner and es_rol(m.transaction_id.seller_role))
        parts, fechas = [], []

        # Producción declarada (larvicultura): antes -> después por lote.
        prod = moves.filtered(
            lambda m: m.move_type == "production" and m.lot_id.owner_id == partner
            and es_rol(m.lot_id.held_role))
        if prod:
            antes = despues = 0.0
            for lot in prod.mapped("lot_id"):
                del_lote = prod.filtered(lambda m: m.lot_id == lot).sorted(
                    lambda m: (m.date or m.create_date, m.id))
                antes += del_lote[0].qty_before
                despues += del_lote[-1].qty_after
            p = prod[0].product_id
            txt = _("Produjo %s") % fq(despues, p.uom_id) + self._shrimp_chain_product_short(p)
            if antes:
                txt += " (%s %%)" % self.shrimp_fmt_num(100.0 * despues / antes, 0)
            parts.append(txt)
            fechas.append(prod[-1].date)

        # Siembra y cosecha (camaronera): siembras de origen de lo que vendió.
        siembras = data["allocations"].filtered(lambda a: a.partner_id == partner)
        cosechas = ventas.mapped("product_id").filtered("origin_allocation_ids")
        if siembras and (cosechas or not ventas):
            if cosechas:
                siembras = (siembras & cosechas.mapped("origin_allocation_ids")) or siembras
            total = sum(siembras.mapped("allocated_qty"))
            piscinas = siembras.mapped("pond_id")
            donde = (_("en %s") % ", ".join(piscinas.mapped("name"))) if len(piscinas) <= 2 \
                else (_("en %s piscinas") % len(piscinas))
            parts.append(_("Sembró %(qty)s %(donde)s") % {
                "qty": fq(total, siembras[:1].stock_lot_id.uom_id), "donde": donde})
            fechas.append(min(siembras.mapped("allocation_date")))
        for p in cosechas:
            txt = _("cosechó %s") % fq(p.initial_qty, p.uom_id)
            if p.size_grade_id:
                txt += " " + _("(talla %s)") % p.size_grade_id.name
            parts.append(txt if parts else txt[:1].upper() + txt[1:])
            fechas.append(p.production_date)

        # Transferencia interna entre perfiles de la misma empresa.
        for m in moves.filtered(lambda m: m.move_type == "profile_transfer"
                                and m.source_partner_id == partner and m.to_role == rol):
            parts.append(_("Recibió %s por transferencia interna de perfil") % fq(m.qty, m.product_id.uom_id))
            fechas.append(m.date)

        if not parts:
            if compras:
                c = compras[0]
                txt = _("Compró %s") % fq(sum(compras.mapped("qty")), c.product_id.uom_id) \
                    + self._shrimp_chain_product_short(c.product_id)
                ctx = c.transaction_id
                ver = ctx.verification_ids if "verification_ids" in ctx._fields else None
                if ver and ver.filtered(lambda v: v.state in ("approved", "approved_obs")):
                    txt += " " + _("(verificada)")
                parts.append(txt)
                fechas.append(c.date)
            if ventas:
                v = ventas[0]
                txt = _("Vendió %s") % fq(sum(ventas.mapped("qty")), v.product_id.uom_id) \
                    + self._shrimp_chain_product_short(v.product_id)
                parts.append(txt if not parts else txt[:1].lower() + txt[1:])
                fechas.append(v.date)

        ref_move = compras[:1] or ventas[:1]
        return {
            "parts": parts,
            "date": next((f for f in fechas if f), False),
            "ref": ref_move.transaction_id.name or "",
        }

    @api.model
    def _shrimp_fmt_size(self, mg):
        """Tamaño: en mg para la larva, en g desde 1 g (camarón adulto)."""
        mg = float(mg or 0.0)
        if mg >= 1000.0:
            return "%s g" % self.shrimp_fmt_num(mg / 1000.0, 1)
        return "%s mg" % self.shrimp_fmt_num(mg, 2 if mg < 10 else 1)

    def traceability_indicators(self, data=None):
        """Indicadores de cultivo del certificado, calculados en el servidor
        (tabla + barras HTML: wkhtmltopdf no ejecuta JS y no dimensiona el SVG
        en línea con height:auto). Lista de dicts:
        {'group', 'label', 'value', 'pct' (0-100 para la barra o None), 'detail'}.

        Por cada producto de la cadena con mediciones: supervivencia y tamaño
        (primera -> última etapa); la cosecha (cantidad, talla, días de
        cultivo); shrimp_verification añade el rendimiento verificado."""
        self.ensure_one()
        data = data or self.get_full_traceability_data()
        role_sel = dict(self.env["res.partner"]._fields["shrimp_user_type"]._description_selection(self.env))
        evs_all = data["evolutions"]
        rows = []

        def grupo_de(p):
            rol = p.seller_role or p.seller_partner_id._shrimp_effective_type()
            return "%s · %s" % (role_sel.get(rol) or _("Productor"), p.seller_partner_id.name or "—")

        # Orden de la cadena: por la primera medición de cada producto.
        primeras = {}
        for e in evs_all:
            primeras.setdefault(e.product_id.id, e.date or fields.Datetime.now())
        for p in evs_all.mapped("product_id").sorted(lambda p: (primeras[p.id], p.id)):
            evs = evs_all.filtered(lambda e: e.product_id == p)
            first, last = evs[0], evs[-1]
            grupo = grupo_de(p)
            periodo = self.shrimp_fmt_dt(first.date, with_time=False)
            if len(evs) > 1:
                periodo += " – " + self.shrimp_fmt_dt(last.date, with_time=False)
            n_txt = _("%s mediciones") % len(evs) if len(evs) > 1 else _("1 medición")
            if last.survival_rate:
                det = "%s · %s" % (n_txt, periodo)
                if len(evs) > 1 and first.survival_rate != last.survival_rate:
                    det = _("%(a)s %% (%(ea)s) → %(b)s %% (%(eb)s) · %(n)s") % {
                        "a": self.shrimp_fmt_num(first.survival_rate, 0), "ea": first.stage_id.name or "—",
                        "b": self.shrimp_fmt_num(last.survival_rate, 0), "eb": last.stage_id.name or "—",
                        "n": n_txt}
                rows.append({
                    "group": grupo,
                    "label": _("Supervivencia (%s)") % (last.stage_id.name or p.stage_id.name or "—"),
                    "value": "%s %%" % self.shrimp_fmt_num(last.survival_rate, 1),
                    "pct": max(0.0, min(100.0, last.survival_rate)),
                    "detail": det,
                })
            if last.avg_size_mg:
                det = periodo
                if len(evs) > 1 and first.avg_size_mg != last.avg_size_mg:
                    det = _("%(a)s (%(ea)s) → %(b)s (%(eb)s)") % {
                        "a": self._shrimp_fmt_size(first.avg_size_mg), "ea": first.stage_id.name or "—",
                        "b": self._shrimp_fmt_size(last.avg_size_mg), "eb": last.stage_id.name or "—"}
                rows.append({
                    "group": grupo,
                    "label": _("Peso promedio") if last.avg_size_mg >= 1000 else _("Tamaño promedio"),
                    "value": self._shrimp_fmt_size(last.avg_size_mg),
                    "pct": None,
                    "detail": det,
                })
        # Cosecha: cantidad, talla y días de cultivo de lo cosechado en la cadena.
        for p in data["products"].filtered("origin_allocation_ids"):
            det = []
            sembrado = min(p.origin_allocation_ids.mapped("allocation_date") or [False]) or False
            if sembrado and p.production_date:
                det.append(_("%s días de cultivo") % (p.production_date - sembrado).days)
            if p.origin_pond_id:
                det.append(p.origin_pond_id.name)
            if p.size_grade_id:
                det.append(_("talla %s") % p.size_grade_id.name)
            rows.append({
                "group": grupo_de(p),
                "label": _("Cosecha"),
                "value": self.shrimp_fmt_qty(p.initial_qty, p.uom_id),
                "pct": None,
                "detail": " · ".join(det),
            })
        return rows

    def _line_chart_svg(self, title, points, color, suffix=""):
        """Genera un gráfico de líneas SVG (inline) a partir de (etiqueta, valor)."""
        W, H, L, R, T, B = 480, 210, 50, 16, 28, 38
        if not points:
            return Markup('<div style="color:#8a98a0;font-size:11px;">Sin datos suficientes.</div>')
        vals = [p[1] for p in points]
        vmin, vmax = min(vals), max(vals)
        if vmax == vmin:
            vmax = vmin + (abs(vmin) or 1.0)
        span = vmax - vmin
        n = len(points)

        def px(i):
            return L + (W - L - R) * (i / (n - 1) if n > 1 else 0.5)

        def py(v):
            return T + (H - T - B) * (1 - (v - vmin) / span)

        poly = " ".join("%.1f,%.1f" % (px(i), py(v)) for i, (_, v) in enumerate(points))
        area = "%.1f,%.1f %s %.1f,%.1f" % (px(0), H - B, poly, px(n - 1), H - B)
        dots = "".join(
            '<circle cx="%.1f" cy="%.1f" r="3.2" fill="#fff" stroke="%s" stroke-width="2"/>' % (px(i), py(v), color)
            for i, (_, v) in enumerate(points))
        grid = labels_y = ""
        for frac in (0.0, 0.5, 1.0):
            val = vmin + span * frac
            yy = py(val)
            grid += '<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e3e8ec" stroke-width="1"/>' % (L, yy, W - R, yy)
            labels_y += '<text x="%d" y="%.1f" font-size="9" fill="#5a6b74" text-anchor="end">%.1f%s</text>' % (L - 6, yy + 3, val, suffix)
        xlab = ""
        for i in sorted(set([0, n // 2, n - 1])):
            xlab += '<text x="%.1f" y="%d" font-size="8.5" fill="#5a6b74" text-anchor="middle">%s</text>' % (px(i), H - B + 16, escape(str(points[i][0])))
        svg = (
            '<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" style="width:100%%;height:auto;">' % (W, H)
            + '<rect x="0" y="0" width="%d" height="%d" rx="8" fill="#f8fafc" stroke="#e3e8ec"/>' % (W, H)
            + '<text x="%d" y="18" font-size="11" font-weight="bold" fill="#123e5c">%s</text>' % (L, escape(title))
            + grid
            + '<polygon points="%s" fill="%s" opacity="0.10"/>' % (area, color)
            + '<polyline points="%s" fill="none" stroke="%s" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>' % (poly, color)
            + dots + labels_y + xlab
            + '</svg>'
        )
        return Markup(svg)

    def evolution_charts(self):
        """Lista de gráficos (SVG) de la evolución productiva: supervivencia y tamaño vs tiempo."""
        self.ensure_one()
        evs = self.get_full_traceability_data()["evolutions"]

        def lbl(e):
            return e.date.strftime("%d/%m/%y") if e.date else "—"

        surv = [(lbl(e), e.survival_rate or 0.0) for e in evs]
        size = [(lbl(e), e.avg_size_mg or 0.0) for e in evs]
        charts = []
        if len(evs) >= 2:
            charts.append(self._line_chart_svg("Supervivencia (%) vs tiempo", surv, "#123e5c", "%"))
            charts.append(self._line_chart_svg("Tamaño promedio (mg) vs tiempo", size, "#14a0b0", ""))
        return charts

    def traceability_url(self):
        """URL pública de la página de trazabilidad on-screen de esta transacción."""
        self.ensure_one()
        return "%s/marketplace/purchases/%s/traceability" % (self.get_base_url(), self.uuid_ref)

    def traceability_qr_uri(self):
        """Devuelve un data-URI PNG con el QR que apunta a la trazabilidad en línea.
        Usa la librería qrcode + Pillow (el backend de barcode de Odoo/reportlab
        requiere rlPyCairo, ausente en este entorno)."""
        self.ensure_one()
        try:
            import io
            import qrcode
            img = qrcode.make(self.traceability_url(), box_size=8, border=2)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return "data:image/png;base64,%s" % base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return False
