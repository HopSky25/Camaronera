"""Matriz de verificación: cuándo una compra lleva verificación en campo.

Antes la regla estaba fija en el código: camarón adulto (vendedor camaronera)
= verificación obligatoria; todo lo demás = opcional. Ahora la decide esta
tabla, editable en Camaronera › Configuración › Matriz de verificación, por
pareja de perfiles (vendedor → comprador) y, si hace falta, por tipo de
producto (nauplio, larva, camarón).

Política:
  * ``none``      «No aplica»: la compra es directa; no se ofrece ni se acepta
                  verificación (ni plataforma ni declarada), así que tampoco
                  hay honorario, informe ni plazo de aceptación.
  * ``optional``  «Opcional»: compra directa o, si el comprador la pide,
                  verificación (plataforma o declarada).
  * ``mandatory`` «Obligatoria»: solo se compra con verificación.

Precedencia: gana la regla MÁS ESPECÍFICA (comprador concreto y tipo de
producto concreto cuentan un punto cada uno); a igual especificidad, la de
menor secuencia. Sin ninguna regla aplicable se usa la lógica anterior
(camaronera vendedora = obligatoria; resto = opcional).

Un único punto de lectura: ``shrimp.product._shrimp_verification_policy()``.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

POLICIES = [
    ("none", "No aplica"),
    ("optional", "Opcional"),
    ("mandatory", "Obligatoria"),
]
PRODUCT_TIPOS = [
    ("nauplio", "Nauplio"),
    ("larva", "Larva"),
    ("camaron", "Camarón"),
]


class ShrimpVerificationRule(models.Model):
    _name = "shrimp.verification.rule"
    _description = "Matriz de verificación (vendedor → comprador)"
    _order = "seller_role, sequence, buyer_role, product_tipo, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    seller_role = fields.Selection(
        selection="_selection_role", string="Perfil vendedor", required=True, index=True)
    buyer_role = fields.Selection(
        selection="_selection_role", string="Perfil comprador", index=True,
        help="Vacío = cualquier comprador.")
    product_tipo = fields.Selection(
        PRODUCT_TIPOS, string="Tipo de producto", index=True,
        help="Vacío = cualquier producto de ese vendedor.")
    policy = fields.Selection(
        POLICIES, string="Verificación", required=True, default="optional")
    note = fields.Char(string="Nota")
    # Recepción por el comprador (shrimp_marketplace,
    # shrimp_transaction_reception.py): en las compras DIRECTAS de esta
    # pareja el comprador confirma la recepción en N días (Ajustes) o se
    # confirma sola. Las compras verificadas se cierran con la aceptación del
    # informe y no la llevan, marque lo que marque aquí.
    #
    # D-26 (auditoría de procesos): TODA compra directa lleva la confirmación
    # de recepción, marque lo que marque la matriz. La columna queda como
    # dato informativo y siempre activa: no se puede configurar una pareja
    # directa sin recepción (ver _check_reception_always).
    requires_reception_confirmation = fields.Boolean(
        string="Confirmación de recepción", default=True,
        help="Informativo: toda compra directa (sin verificación) lleva la confirmación de "
             "recepción. El comprador confirma que la recibió (o reporta un problema) dentro "
             "del plazo de Ajustes › CamaronMarket › «Días para confirmar recepción»; si no "
             "responde y el vendedor marcó el despacho, se confirma automáticamente. Sin "
             "despacho, la compra queda «No entregada». Las compras verificadas se cierran "
             "con el informe aceptado.")

    @api.constrains("requires_reception_confirmation")
    def _check_reception_always(self):
        if self.filtered(lambda r: not r.requires_reception_confirmation):
            raise ValidationError(_(
                "Toda compra directa lleva confirmación de recepción (con confirmación "
                "automática si el vendedor marcó el despacho): no se puede desactivar para una "
                "pareja de perfiles."))

    _shrimp_verification_rule_uniq = models.Constraint(
        "UNIQUE NULLS NOT DISTINCT (seller_role, buyer_role, product_tipo)",
        "Ya hay una regla para esa combinación de perfiles y tipo de producto.",
    )

    @api.model
    def _selection_role(self):
        # Los perfiles de la plataforma (con los que agregan otros módulos).
        # «Semillero» se muestra como «Naupliero»: solo la etiqueta.
        roles = list(self.env["res.partner"]._fields["shrimp_user_type"].selection)
        return [(k, "Naupliero" if k == "semillero" else v) for k, v in roles]

    @api.depends("seller_role", "buyer_role", "product_tipo", "policy")
    def _compute_display_name(self):
        etiquetas = dict(self._selection_role())
        tipos = dict(PRODUCT_TIPOS)
        politicas = dict(POLICIES)
        for rec in self:
            nombre = "%s → %s" % (etiquetas.get(rec.seller_role, rec.seller_role or "?"),
                                  etiquetas.get(rec.buyer_role, _("cualquiera")))
            if rec.product_tipo:
                nombre += " (%s)" % tipos.get(rec.product_tipo)
            rec.display_name = "%s: %s" % (nombre, politicas.get(rec.policy, ""))

    # ------------------------------------------------------------------
    # Resolución
    # ------------------------------------------------------------------
    @api.model
    def _fallback_policy(self, seller_role):
        """Lógica anterior a la matriz: el camarón adulto (vendedor
        camaronera) se verifica siempre; lo demás, si el comprador lo pide."""
        return "mandatory" if seller_role == "camaronera" else "optional"

    @api.model
    def _policy_for(self, seller_role, buyer_role=None, product_tipo=None):
        """Política para (vendedor, comprador, tipo). Ver docstring del módulo."""
        reglas = self.sudo().search([
            ("seller_role", "=", seller_role or False),
            ("buyer_role", "in", [buyer_role or False, False]),
            ("product_tipo", "in", [product_tipo or False, False]),
        ])
        if not reglas:
            return self._fallback_policy(seller_role)
        mejor = max(reglas, key=lambda r: (
            bool(r.buyer_role) + bool(r.product_tipo), -r.sequence, -r.id))
        return mejor.policy

    @api.model
    def _reception_for(self, seller_role, buyer_role=None, product_tipo=None):
        """«Confirmación de recepción» de la regla más específica que aplica
        (misma precedencia que _policy_for), o None si no hay regla."""
        reglas = self.sudo().search([
            ("seller_role", "=", seller_role or False),
            ("buyer_role", "in", [buyer_role or False, False]),
            ("product_tipo", "in", [product_tipo or False, False]),
        ])
        if not reglas:
            return None
        mejor = max(reglas, key=lambda r: (
            bool(r.buyer_role) + bool(r.product_tipo), -r.sequence, -r.id))
        return bool(mejor.requires_reception_confirmation)

    # ------------------------------------------------------------------
    # Mantener al día el campo almacenado requires_verification
    # ------------------------------------------------------------------
    def _recompute_products(self, roles):
        roles = [r for r in set(roles) if r]
        if not roles:
            return
        productos = self.env["shrimp.product"].sudo().with_context(active_test=False).search(
            [("seller_role", "in", roles)])
        if productos:
            self.env.add_to_compute(productos._fields["requires_verification"], productos)
            productos.flush_recordset(["requires_verification"])

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._recompute_products(recs.mapped("seller_role"))
        return recs

    def write(self, vals):
        antes = self.mapped("seller_role")
        res = super().write(vals)
        self._recompute_products(antes + self.mapped("seller_role"))
        return res

    def unlink(self):
        roles = self.mapped("seller_role")
        res = super().unlink()
        self._recompute_products(roles)
        return res

    @api.constrains("seller_role", "buyer_role")
    def _check_roles(self):
        for rec in self:
            if rec.seller_role not in ("semillero", "laboratorio", "camaronera"):
                raise ValidationError(_(
                    "Solo venden en el marketplace los perfiles Naupliero, "
                    "Laboratorio y Camaronera."))
