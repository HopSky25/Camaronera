# -*- coding: utf-8 -*-
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


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
    # _name explícito: al heredar de dos modelos Odoo no puede deducirlo y
    # avisa en cada arranque. El otro res.partner del proyecto ya lo tenía.
    _name = "res.partner"
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

    # Aprobación interna de la cuenta. Los roles con poder operativo sobre
    # terceros (empacadora, maquilador) nacen "pendientes" al registrarse por
    # la web y no operan hasta que un administrador los aprueba. El valor por
    # defecto es "approved" para no bloquear a los contactos ya existentes ni
    # a los demás roles.
    shrimp_account_state = fields.Selection(
        [
            ("pending", "Pendiente de aprobación"),
            ("approved", "Aprobada"),
            ("rejected", "Rechazada"),
        ],
        string="Estado de la cuenta",
        default="approved",
        required=True,
        index=True,
        copy=False,
        tracking=True,
        groups="base.group_user",
        help="Las cuentas pendientes o rechazadas no pueden usar las "
             "funciones operativas de su rol en el portal. Con varios "
             "perfiles, refleja el estado del perfil ACTIVO (cada perfil se "
             "aprueba por separado en shrimp_role_ids).",
    )

    # ------------------------------------------------------------------
    # Varios perfiles por cuenta
    # ------------------------------------------------------------------
    # shrimp_user_type pasa a ser el perfil ACTIVO ("Actuar como"): se guarda
    # en la cuenta, así que persiste entre sesiones, y todo lo que ya lo leía
    # (menús, reglas, plantillas, API) sigue la elección del usuario. Los
    # perfiles que la cuenta TIENE, cada uno con su aprobación, viven aquí.
    shrimp_role_ids = fields.One2many(
        "shrimp.partner.role", "partner_id", string="Perfiles",
        help="Roles de la cuenta. Cada uno se aprueba por separado; el activo "
             "es el «Tipo (Shrimp)».")
    shrimp_role_codes = fields.Char(
        string="Códigos de perfiles", compute="_compute_shrimp_role_codes",
        help="Perfiles no rechazados, separados por comas (para vistas).")
    shrimp_roles_display = fields.Char(
        string="Perfiles de la cuenta", compute="_compute_shrimp_role_codes")
    shrimp_role_count = fields.Integer(
        string="N.º de perfiles aprobados", compute="_compute_shrimp_role_count",
        store=True, index=True)

    @api.depends("shrimp_role_ids.state", "shrimp_role_ids.role")
    def _compute_shrimp_role_count(self):
        for rec in self:
            rec.shrimp_role_count = len(rec.sudo().shrimp_role_ids.filtered(
                lambda r: r.state == "approved"))

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

    # ------------------------------------------------------------------
    # Perfil de empresa COMÚN a todos los roles
    # ------------------------------------------------------------------
    # Cada módulo tenía su copia (lab_, farm_, emp_, ver_, pack_) de los
    # mismos cuatro datos. Ahora hay un solo juego y los nombres viejos quedan
    # como alias (related) durante una versión para no romper plantillas,
    # demos ni integraciones que todavía los lean. Provincia y cantón son los
    # campos estándar de Odoo (state_id y city); la identificación, el
    # estándar `vat` (sincronizado desde vat_or_id).
    #
    # Representante y teléfono quedan, como antes, solo para usuarios internos
    # por RPC: el portal los ve a través de las pantallas, que los leen en sudo.
    shrimp_razon_social = fields.Char(string="Razón social")
    shrimp_representante = fields.Char(
        string="Representante legal", groups="base.group_user")
    shrimp_telefono = fields.Char(string="Teléfono", groups="base.group_user")
    shrimp_ubicacion = fields.Char(
        string="Ubicación", help="Dirección o sector de la planta, finca o laboratorio.")
    shrimp_capacity_value = fields.Float(string="Capacidad")
    shrimp_capacity_unit = fields.Selection(
        [("ton_year", "ton/año"), ("lb_day", "lb/día"),
         ("lb_week", "lb/semana"), ("lots_day", "lotes/día")],
        string="Unidad de capacidad")

    # Laboratorio
    lab_razon_social = fields.Char(
        string="Razón Social (Lab)", related="shrimp_razon_social", readonly=False,
        help="Obsoleto: usar shrimp_razon_social.")
    lab_global_gap = fields.Boolean(string="GlobalG.A.P. (Lab)")
    lab_social_ship_partner = fields.Boolean(string="Social Ship Partner (Lab)")
    lab_ubicacion = fields.Char(
        string="Ubicación (Lab)", related="shrimp_ubicacion", readonly=False,
        help="Obsoleto: usar shrimp_ubicacion.")

    # Camaronera (alias obsoletos del perfil común, salvo el área)
    farm_razon_social = fields.Char(
        string="Razón Social (Camaronera)", related="shrimp_razon_social", readonly=False)
    farm_representante = fields.Char(
        string="Representante legal (Camaronera, obsoleto)", related="shrimp_representante", readonly=False,
        groups="base.group_user")
    farm_telefono = fields.Char(
        string="Teléfono (Camaronera, obsoleto)", related="shrimp_telefono", readonly=False,
        groups="base.group_user")
    farm_ubicacion = fields.Char(
        string="Ubicación (Camaronera)", related="shrimp_ubicacion", readonly=False)
    farm_capacidad = fields.Float(
        string="Capacidad (ton/año)", compute="_compute_capacity_alias_ton_year",
        inverse="_inverse_capacity_alias_ton_year")
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

    # ------------------------------------------------------------------
    # Alias de capacidad: cada rol la expresaba en su propia unidad
    # ------------------------------------------------------------------
    def _capacity_alias_compute(self, fname, unit):
        for rec in self:
            rec[fname] = (rec.shrimp_capacity_value
                          if rec.shrimp_capacity_unit == unit else 0.0)

    def _capacity_alias_inverse(self, fname, unit):
        for rec in self:
            rec.write({"shrimp_capacity_value": rec[fname] or 0.0,
                       "shrimp_capacity_unit": unit})

    @api.depends("shrimp_capacity_value", "shrimp_capacity_unit")
    def _compute_capacity_alias_ton_year(self):
        self._capacity_alias_compute("farm_capacidad", "ton_year")

    def _inverse_capacity_alias_ton_year(self):
        self._capacity_alias_inverse("farm_capacidad", "ton_year")

    # ------------------------------------------------------------------
    # Matriz de capacidades por rol
    # ------------------------------------------------------------------
    # Quién compra a quién, quién publica, quién pide empaque... estaba
    # repartido en tuplas literales por controladores, modelos y plantillas,
    # y cada copia se desincronizaba de las demás (el catálogo ofrecía
    # "Comprar" a quien después la restricción rechazaba). Ahora hay UNA
    # tabla: cada módulo que agrega un rol o una capacidad la amplía con
    # super().
    #
    # Valor: conjunto de tipos de usuario. SHRIMP_ANY ("*") significa
    # "cualquier socio con tipo de usuario".
    SHRIMP_ANY = "*"

    @api.model
    def _shrimp_capability_matrix(self):
        return {
            # Alta desde el formulario común del marketplace.
            "signup": {"semillero", "laboratorio", "camaronera"},
            # Roles que nacen pendientes de aprobación al registrarse.
            "requires_approval": set(),
            # Publicar productos (vender en el marketplace).
            "sell_products": {"semillero", "laboratorio", "camaronera"},
            # La cadena: quién le compra a cada tipo de vendedor.
            #
            # Compras al MISMO nivel: cada productor puede comprarle a otro
            # de su mismo eslabón (semillero↔semillero: nauplio y
            # reproductores para cubrir un faltante; laboratorio↔laboratorio:
            # nauplio o larva para completar un pedido; camaronera↔camaronera:
            # juveniles para transferencia/precría y camarón adulto que se
            # revende). La transacción toma un tipo propio
            # (<rol>_to_<rol>, ver shrimp.transaction._shrimp_tx_type).
            # Comprarse a sí mismo (la misma entidad, aunque sea con otro
            # perfil) sigue prohibido: _shrimp_same_entity_as.
            "buy_from_semillero": {"laboratorio", "semillero"},
            "buy_from_laboratorio": {"camaronera", "laboratorio"},
            # Sin el módulo de empacadoras el adulto lo compra cualquiera;
            # shrimp_packer lo restringe a la empacadora (y a otra camaronera).
            "buy_from_camaronera": {self.SHRIMP_ANY},
            # Perfiles que una cuenta ya creada puede AGREGARSE desde «Mi
            # cuenta» (cada módulo suma el suyo).
            "add_profile": {"semillero", "laboratorio", "camaronera"},
            # Perfiles que no conviven con ningún otro en la misma cuenta
            # (shrimp_verification: el verificador).
            "exclusive_profile": set(),
        }

    def _shrimp_effective_type(self):
        """El perfil ACTIVO del socio, o el de su empresa si es un contacto hijo."""
        self.ensure_one()
        socio = self.sudo()
        if not socio.shrimp_user_type and socio.parent_id:
            return socio.parent_id.shrimp_user_type
        return socio.shrimp_user_type

    # ------------------------------------------------------------------
    # Perfiles (multi-rol): API para el resto de módulos
    # ------------------------------------------------------------------
    def _shrimp_role_holder(self):
        """La cuenta dueña de los perfiles: el propio socio o, si es un
        contacto hijo sin tipo (técnico, empleado), su empresa."""
        self.ensure_one()
        socio = self.sudo()
        if not socio.shrimp_user_type and socio.parent_id:
            return socio.parent_id
        return socio

    def _shrimp_active_role(self):
        """Perfil con el que la cuenta opera ahora (alias de
        _shrimp_effective_type, con un nombre que dice lo que es)."""
        return self._shrimp_effective_type()

    def _shrimp_roles(self, include_pending=False):
        """Códigos de los perfiles con los que la cuenta PUEDE actuar.

        Los aprobados más el activo (el activo puede estar pendiente en una
        cuenta recién registrada: esa aprobación la sigue controlando
        shrimp_is_operational, como antes). Con include_pending también los
        pendientes (para mostrar, nunca para autorizar). En el orden del
        campo shrimp_user_type.
        """
        self.ensure_one()
        socio = self._shrimp_role_holder()
        estados = ("approved", "pending") if include_pending else ("approved",)
        codigos = set(socio.shrimp_role_ids.filtered(lambda r: r.state in estados).mapped("role"))
        if socio.shrimp_user_type:
            codigos.add(socio.shrimp_user_type)
        orden = [v for v, _l in self._fields["shrimp_user_type"].selection]
        return [c for c in orden if c in codigos]

    def _shrimp_has_role(self, role, include_pending=False):
        """True si la cuenta puede actuar como `role` (perfil aprobado o el activo)."""
        self.ensure_one()
        return bool(role) and role in self._shrimp_roles(include_pending=include_pending)

    def _shrimp_role_state(self, role):
        """Estado de aprobación del perfil `role` en la cuenta (o False)."""
        self.ensure_one()
        socio = self._shrimp_role_holder()
        linea = socio.shrimp_role_ids.filtered(lambda r: r.role == role)[:1]
        if linea:
            return linea.state
        if role and role == socio.shrimp_user_type:
            return socio.shrimp_account_state
        return False

    def _shrimp_same_entity_as(self, other):
        """True si este socio y `other` son la MISMA entidad comercial: el
        mismo socio, la misma empresa (contactos hijos, técnicos), la misma
        cuenta de perfiles o el mismo RUC/cédula. Con varios perfiles, una
        cuenta camaronera+empacadora no se puede comprar a sí misma aunque
        cambie de perfil: eso no es una compra, es mover su propio producto."""
        self.ensure_one()
        if not other:
            return False
        a, b = self.sudo(), other.sudo()
        if a == b or a.commercial_partner_id == b.commercial_partner_id:
            return True
        ha, hb = a._shrimp_role_holder(), b._shrimp_role_holder()
        if ha == hb:
            return True
        va, vb = ha.x_vat_or_id_normalized, hb.x_vat_or_id_normalized
        return bool(va) and va == vb

    def _shrimp_roles_with(self, capability):
        """Perfiles de la cuenta que tienen `capability` (aprobados o el activo)."""
        self.ensure_one()
        return [r for r in self._shrimp_roles() if self._shrimp_type_can(r, capability)]

    def _shrimp_can_any(self, capability):
        """True si ALGUNO de los perfiles de la cuenta tiene `capability`.

        Para validar a una CONTRAPARTE (el destinatario de una lista, la
        planta de una orden): no importa con qué perfil está navegando ahora,
        sino si la cuenta puede ocupar ese lugar."""
        self.ensure_one()
        return bool(self._shrimp_roles_with(capability))

    @api.model
    def _shrimp_role_domain(self, roles):
        """Dominio de res.partner: cuentas que pueden actuar como alguno de
        `roles` (perfil activo o un perfil aprobado). Sustituye a
        [("shrimp_user_type", "in", roles)] al buscar contrapartes."""
        if isinstance(roles, str):
            roles = [roles]
        roles = list(roles or [])
        return ["|", ("shrimp_user_type", "in", roles),
                ("shrimp_role_ids", "any", [("role", "in", roles), ("state", "=", "approved")])]

    def _shrimp_set_active_role(self, role):
        """Cambia el perfil activo ("Actuar como"). Solo a un perfil aprobado."""
        self.ensure_one()
        socio = self._shrimp_role_holder()
        if role == socio.shrimp_user_type:
            return True
        if role not in socio._shrimp_roles():
            raise UserError(_("No tienes el perfil «%s» aprobado.") % self._shrimp_type_label(role))
        socio.write({"shrimp_user_type": role})
        return True

    @api.model
    def _shrimp_addable_roles(self):
        """Perfiles que una cuenta puede AGREGAR desde «Mi cuenta» (matriz:
        add_profile). Cada módulo que trae un rol lo suma; el verificador no
        está porque es exclusivo."""
        return self._shrimp_types_with("add_profile")

    def _shrimp_request_role(self, role, vals=None):
        """Agrega el perfil `role` a la cuenta (o lo vuelve a pedir si fue
        rechazado). Nace pendiente si el rol requiere aprobación; si no, ya
        aprobado. `vals`: datos del perfil común/rol a guardar antes (solo se
        escriben los que traen valor). Devuelve la línea shrimp.partner.role.
        """
        self.ensure_one()
        socio = self._shrimp_role_holder()
        if role not in self._shrimp_addable_roles():
            raise ValidationError(_("Ese perfil no se puede agregar a una cuenta."))
        exclusivos = set(self._shrimp_types_with("exclusive_profile"))
        if set(socio._shrimp_roles(include_pending=True)) & exclusivos:
            raise ValidationError(_("Una cuenta de verificación no puede tener otros perfiles."))
        linea = socio.shrimp_role_ids.filtered(lambda r: r.role == role)[:1]
        if linea and linea.state != "rejected" or role == socio.shrimp_user_type:
            raise ValidationError(_("Tu cuenta ya tiene el perfil «%s».") % self._shrimp_type_label(role))
        if vals:
            # Solo lo que trae valor: un campo vacío del formulario no borra
            # el dato que la cuenta ya tenía por otro perfil.
            limpios = {k: v for k, v in vals.items() if v}
            if "shrimp_capacity_value" not in limpios:
                # Sin número no se cambia la unidad (pisaría la de otro rol).
                limpios.pop("shrimp_capacity_unit", None)
            if limpios:
                socio.write(limpios)
        estado = "pending" if self._shrimp_type_can(role, "requires_approval") else "approved"
        if linea:
            linea.write({"state": estado, "request_date": fields.Datetime.now()})
        else:
            linea = self.env["shrimp.partner.role"].sudo().create({
                "partner_id": socio.id, "role": role, "state": estado})
        # Los datos obligatorios del nuevo rol (razón social, representante...)
        # se validan igual que en el alta.
        socio._shrimp_validate_role_profile(role, for_request=True)
        # Constancia en la ficha (nota interna): quien aprueba ve cuándo y qué
        # se pidió sin salir del contacto.
        socio.message_post(body=_("Perfil «%(r)s» solicitado desde el portal (%(e)s).") % {
            "r": self._shrimp_type_label(role),
            "e": _("pendiente de aprobación") if estado == "pending" else _("aprobado")})
        return linea

    def _shrimp_validate_role_profile(self, role, for_request=False):
        """Lanza ValidationError si faltan los datos que exige `role`.

        Lo usa la restricción del perfil común (for_request=False: las reglas
        de siempre) y el alta de un perfil nuevo desde «Mi cuenta»
        (for_request=True: además lo que el formulario de alta de ese rol
        exige). Cada módulo con un rol propio lo amplía."""
        self.ensure_one()
        rec = self.sudo()
        if rec.shrimp_capacity_value and rec.shrimp_capacity_value < 0:
            raise ValidationError(_("La capacidad no puede ser negativa."))
        if role == "laboratorio":
            if not rec.shrimp_razon_social:
                raise ValidationError(_("La razón social del laboratorio es obligatoria."))
            if not rec.shrimp_ubicacion:
                raise ValidationError(_("La ubicación del laboratorio es obligatoria."))
        elif role == "camaronera":
            if not rec.shrimp_razon_social:
                raise ValidationError(_("La razón social de la camaronera es obligatoria."))
            if not rec.shrimp_representante:
                raise ValidationError(_("El representante legal es obligatorio."))
            if not rec.shrimp_telefono:
                raise ValidationError(_("El teléfono es obligatorio."))
            if not rec.shrimp_ubicacion:
                raise ValidationError(_("La ubicación de la camaronera es obligatoria."))
            if rec.farm_area_ha and rec.farm_area_ha < 0:
                raise ValidationError(_("El área no puede ser negativa."))
        # Semillero: no se exigen fotos desde el modelo.
        return True

    def _shrimp_profile_switcher_info(self):
        """Datos del selector «Perfil: X ▾» de la barra superior, o None si
        no aplica (sin tipo, contacto hijo o perfil exclusivo)."""
        self.ensure_one()
        socio = self.sudo()
        if not socio.shrimp_user_type or socio._shrimp_role_holder() != socio:
            return None
        exclusivos = set(self._shrimp_types_with("exclusive_profile"))
        if socio.shrimp_user_type in exclusivos:
            return None
        etiqueta = self._shrimp_type_label
        aprobados = socio._shrimp_roles()
        pendientes = [r for r in socio._shrimp_roles(include_pending=True) if r not in aprobados]
        tiene = set(aprobados) | set(pendientes)
        return {
            "active": socio.shrimp_user_type,
            "active_label": etiqueta(socio.shrimp_user_type),
            "active_pending": socio.shrimp_account_state != "approved",
            "roles": [(r, etiqueta(r), r == socio.shrimp_user_type) for r in aprobados],
            "pending": [(r, etiqueta(r)) for r in pendientes],
            "can_add": any(r not in tiene for r in self._shrimp_addable_roles()),
        }

    @api.depends("shrimp_user_type", "shrimp_role_ids.role", "shrimp_role_ids.state")
    def _compute_shrimp_role_codes(self):
        etiquetas = dict(self._fields["shrimp_user_type"]._description_selection(self.env))
        estados = {"pending": _("pendiente"), "rejected": _("rechazado")}
        for rec in self:
            socio = rec.sudo()
            lineas = socio.shrimp_role_ids.filtered(lambda r: r.state != "rejected")
            codigos = lineas.mapped("role")
            if socio.shrimp_user_type and socio.shrimp_user_type not in codigos:
                codigos.append(socio.shrimp_user_type)
            rec.shrimp_role_codes = ",".join(codigos) or False
            partes = []
            for linea in socio.shrimp_role_ids:
                txt = etiquetas.get(linea.role, linea.role)
                if linea.state in estados:
                    txt += " (%s)" % estados[linea.state]
                if linea.role == socio.shrimp_user_type:
                    txt = "★ " + txt
                partes.append(txt)
            rec.shrimp_roles_display = ", ".join(partes) or etiquetas.get(socio.shrimp_user_type) or False

    @api.model
    def _shrimp_types_with(self, capability):
        """Tipos de usuario que tienen `capability`, en el orden del campo."""
        permitidos = self._shrimp_capability_matrix().get(capability, set())
        todos = [v for v, _l in self._fields["shrimp_user_type"]._description_selection(self.env)]
        if self.SHRIMP_ANY in permitidos:
            return todos
        return [t for t in todos if t in permitidos]

    @api.model
    def _shrimp_type_can(self, user_type, capability):
        if not user_type:
            return False
        permitidos = self._shrimp_capability_matrix().get(capability, set())
        return self.SHRIMP_ANY in permitidos or user_type in permitidos

    def _shrimp_can(self, capability, role=None):
        """True si este socio tiene la capacidad actuando con `role`.

        Sin `role`, con el perfil ACTIVO (lo de siempre). Con `role`, ese
        perfil tiene que ser uno con el que la cuenta puede actuar (aprobado o
        el activo): un perfil pendiente no da ninguna capacidad."""
        self.ensure_one()
        activo = self._shrimp_effective_type()
        if role and role != activo:
            if not self._shrimp_has_role(role):
                return False
            return self._shrimp_type_can(role, capability)
        return self._shrimp_type_can(activo, capability)

    @api.model
    def _shrimp_type_label(self, user_type):
        etiquetas = dict(self._fields["shrimp_user_type"]._description_selection(self.env))
        return etiquetas.get(user_type, user_type or _("sin tipo"))

    @api.model
    def _shrimp_types_label(self, capability, sep=None):
        """'laboratorio o camaronera' — para mensajes de error."""
        tipos = [self._shrimp_type_label(t) for t in self._shrimp_types_with(capability)]
        if not tipos:
            return ""
        if len(tipos) == 1:
            return tipos[0]
        return "%s %s %s" % (", ".join(tipos[:-1]), sep or _("o"), tipos[-1])

    def shrimp_is_operational(self, role=None):
        """True si la cuenta (o la empresa de la que cuelga) está aprobada.

        Sin `role`, la aprobación del perfil activo (shrimp_account_state);
        con `role`, la de ese perfil. Se lee en sudo porque el campo es solo
        para usuarios internos y quien pregunta suele ser el propio usuario
        del portal.
        """
        self.ensure_one()
        socio = self.sudo()
        if socio.parent_id and not socio.shrimp_user_type:
            socio = socio.parent_id
        if role and role != socio.shrimp_user_type:
            return socio._shrimp_role_state(role) == "approved"
        return socio.shrimp_account_state == "approved"

    # ------------------------------------------------------------------
    # Sincronía perfil activo <-> líneas de perfil
    # ------------------------------------------------------------------
    def _shrimp_sync_roles_from_type(self, state_given=False, type_changed=False):
        """Mantiene la invariante «el perfil activo siempre tiene su línea».

        - Escribir shrimp_user_type con un rol que la cuenta no tenía lo
          AGREGA (estado = shrimp_account_state), salvo que un rol exclusivo
          (verificador) esté en juego: entonces reemplaza, como antes.
        - Cambiar a un rol que ya tenía sincroniza shrimp_account_state con
          el estado de ese perfil.
        - Escribir shrimp_account_state lo copia al perfil activo.
        """
        Role = self.env["shrimp.partner.role"].sudo().with_context(shrimp_role_sync=True)
        exclusivos = set(self._shrimp_types_with("exclusive_profile"))
        for rec in self.sudo().with_context(shrimp_role_sync=True):
            tipo = rec.shrimp_user_type
            if not tipo:
                continue
            linea = rec.shrimp_role_ids.filtered(lambda r: r.role == tipo)[:1]
            if not linea:
                otros = rec.shrimp_role_ids
                if otros and (tipo in exclusivos or set(otros.mapped("role")) & exclusivos):
                    otros.with_context(shrimp_role_sync=True).unlink()
                Role.create({"partner_id": rec.id, "role": tipo,
                             "state": rec.shrimp_account_state or "approved"})
            elif state_given:
                if linea.state != rec.shrimp_account_state:
                    linea.write({"state": rec.shrimp_account_state})
            elif type_changed and rec.shrimp_account_state != linea.state:
                rec.write({"shrimp_account_state": linea.state})

    def action_shrimp_approve_account(self):
        self.write({"shrimp_account_state": "approved"})
        for rec in self:
            # Se avisa al propio contacto (correo): ya puede operar.
            rec.message_post(
                body=_("Tu cuenta en la plataforma fue aprobada. Ya puedes usar "
                       "todas las funciones de tu rol."),
                partner_ids=rec.ids, subtype_xmlid="mail.mt_comment")
        return True

    def action_shrimp_reject_account(self):
        self.write({"shrimp_account_state": "rejected"})
        for rec in self:
            rec.message_post(body=_("Cuenta rechazada por %s.") % self.env.user.name)
        return True

    def action_shrimp_reset_account(self):
        self.write({"shrimp_account_state": "pending"})
        return True

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

    # ------------------------------------------------------------------
    # Alias obsoletos del perfil común
    # ------------------------------------------------------------------
    # Escribir un alias (p. ej. farm_razon_social) se traduce al campo común
    # ANTES de guardar: Odoo valida las restricciones con los campos
    # almacenados antes de ejecutar la inversa de un related no almacenado, y
    # la restricción de "razón social obligatoria" no vería el dato.
    @api.model
    def _shrimp_profile_alias_map(self):
        """alias -> (campo común, unidad de capacidad o None)."""
        return {
            "lab_razon_social": ("shrimp_razon_social", None),
            "lab_ubicacion": ("shrimp_ubicacion", None),
            "farm_razon_social": ("shrimp_razon_social", None),
            "farm_representante": ("shrimp_representante", None),
            "farm_telefono": ("shrimp_telefono", None),
            "farm_ubicacion": ("shrimp_ubicacion", None),
            "farm_capacidad": ("shrimp_capacity_value", "ton_year"),
        }

    def _shrimp_map_profile_aliases(self, vals):
        mapa = self._shrimp_profile_alias_map()
        for alias, (campo, unidad) in mapa.items():
            if alias not in vals:
                continue
            valor = vals.pop(alias)
            if unidad:
                # La capacidad en otra unidad solo se escribe si trae número
                # (cero o vacío no pisa la capacidad de otro rol).
                if valor:
                    vals.setdefault(campo, valor)
                    vals.setdefault("shrimp_capacity_unit", unidad)
            else:
                vals.setdefault(campo, valor)
        return vals

    def _prepare_normalized_vals(self, vals):
        vals = self._shrimp_map_profile_aliases(dict(vals))

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

        # Limpieza de textos adicionales (perfil común y sus alias).
        text_fields = [
            "shrimp_razon_social", "shrimp_representante", "shrimp_telefono",
            "shrimp_ubicacion",
            "lab_razon_social", "lab_ubicacion", "farm_razon_social",
            "farm_representante", "farm_telefono", "farm_ubicacion",
        ]
        for field_name in text_fields:
            if field_name in vals:
                vals[field_name] = _normalize_text(vals.get(field_name)) or False

        return vals

    # ------------------------------------------------------------------
    # Identificación estándar (vat)
    # ------------------------------------------------------------------
    # El RUC/cédula se guardaba solo en vat_or_id y el campo estándar `vat`
    # quedaba vacío: ninguna factura electrónica a estos socios podía
    # emitirse. vat_or_id se conserva (lo usa el índice único del registro y
    # media plataforma), pero `vat` pasa a ser la fuente fiscal y se rellena
    # desde él cuando está vacío o cuando cambia la identificación.
    @api.model
    def _shrimp_vat_from_vals(self, vals):
        raw = vals.get("vat_or_id")
        return _normalize_vat(raw) or False

    def _shrimp_sync_vat(self):
        """Rellena `vat` desde vat_or_id en los socios del marketplace que no
        lo tienen. Sin validación de dígito verificador: un RUC nuevo puede no
        seguir el algoritmo y el SRI solo lo advierte."""
        for rec in self.sudo().with_context(no_vat_validation=True):
            if rec.shrimp_user_type and rec.vat_or_id and not rec.vat:
                rec.vat = _normalize_vat(rec.vat_or_id) or False

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._prepare_normalized_vals(vals) for vals in vals_list]
        for vals in vals_list:
            if vals.get("vat_or_id") and not vals.get("vat") and vals.get("shrimp_user_type"):
                vals["vat"] = self._shrimp_vat_from_vals(vals)
        if any(v.get("vat") and v.get("shrimp_user_type") for v in vals_list):
            self = self.with_context(no_vat_validation=True)
        records = super(ResPartner, self).create(vals_list)
        con_tipo = records.filtered("shrimp_user_type")
        if con_tipo and not self.env.context.get("shrimp_role_sync"):
            con_tipo._shrimp_sync_roles_from_type(state_given=True)
        return records

    def write(self, vals):
        vals = self._prepare_normalized_vals(vals)
        res = super().write(vals)
        if ("shrimp_user_type" in vals or "shrimp_account_state" in vals) \
                and not self.env.context.get("shrimp_role_sync"):
            self._shrimp_sync_roles_from_type(
                state_given="shrimp_account_state" in vals,
                type_changed="shrimp_user_type" in vals)
        if "vat_or_id" in vals and "vat" not in vals:
            nuevo = self._shrimp_vat_from_vals(vals)
            if nuevo:
                self.filtered(lambda r: r.shrimp_user_type and r.vat != nuevo).sudo().with_context(
                    no_vat_validation=True).write({"vat": nuevo})
        return res

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

    @api.constrains("shrimp_user_type", "shrimp_razon_social", "shrimp_ubicacion",
                    "shrimp_representante", "shrimp_telefono",
                    "shrimp_capacity_value", "farm_area_ha",
                    # alias obsoletos: escribirlos también dispara la validación
                    "lab_razon_social", "lab_ubicacion", "farm_razon_social",
                    "farm_representante", "farm_telefono", "farm_ubicacion",
                    "farm_capacidad")
    def _check_required_fields_by_type(self):
        # Se valida sobre el perfil común: los alias (lab_*, farm_*) no se
        # guardan y escribirlos escribe estos campos. Con varios perfiles se
        # validan TODOS los que la cuenta tiene (aprobados o pendientes): el
        # perfil común es uno solo y tiene que servirle a cada rol.
        for rec in self:
            if rec.shrimp_capacity_value and rec.shrimp_capacity_value < 0:
                raise ValidationError(_("La capacidad no puede ser negativa."))
            if not rec.shrimp_user_type:
                continue
            for tipo in rec._shrimp_roles(include_pending=True):
                rec._shrimp_validate_role_profile(tipo)
