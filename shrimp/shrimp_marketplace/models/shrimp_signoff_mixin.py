"""La firma de dos partes, una sola vez.

Tres modelos hacen lo mismo con palabras distintas: la aceptación del informe
de verificación (comprador / vendedor), el acta de empaque (cliente /
maquilador) y la confirmación de una cosecha fuera de banda (camaronera /
empacadora). En los tres:

* cada parte firma SOLO la suya: el controlador resuelve la fila en sudo, así
  que el control de quién firma no puede delegarse en el ACL;
* una firma ya decidida no se cambia, y una de una ronda archivada tampoco;
* no aceptar exige decir por qué;
* las firmas de una ronda cerrada se archivan con su traza, nunca se borran.

Cada modelo conserva sus campos, sus selecciones y lo que pasa al decidir
(``_signoff_after_decision``); lo común —quién puede firmar y con qué
condiciones— vive aquí y se comprueba igual en los tres.

DESHACER UNA DECISIÓN
---------------------
Una parte que aceptó, rechazó o contraofertó sin querer puede devolver SU
postura a como estaba antes (normalmente «pendiente») mientras el proceso no
haya terminado. Lo común vive aquí:

* solo el titular de la fila deshace lo suyo (o un gestor de la plataforma,
  con motivo, en su nombre); el verificador o la otra parte, nunca;
* una decisión automática (vencimiento del plazo) no se deshace;
* cada decisión y cada «deshacer» queda en ``shrimp.signoff.event`` (con la
  foto de la postura anterior, que es lo que se restaura) y en el chatter del
  expediente;
* a la otra parte se le avisa por correo (y por webhook, en shrimp_api).

Lo que cambia en cada flujo —hasta cuándo se puede deshacer y qué hay que
recalcular después— lo pone cada modelo en ``_signoff_undo_blocker``,
``_signoff_after_undo`` y ``_signoff_undo_until``.
"""

from datetime import timedelta


from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

# Minutos de gracia para deshacer un rechazo que YA liberó algo reversible
# (la confirmación de cosecha). Ver shrimp.harvest.confirmation.
UNDO_GRACE_PARAM = "shrimp.signoff_undo_minutes"
UNDO_GRACE_DEFAULT = 15
# Margen mínimo que le queda a quien deshace antes de que el vencimiento del
# plazo decida por él (aceptación del informe de verificación).
UNDO_MARGIN_PARAM = "shrimp.signoff_undo_margin_minutes"
UNDO_MARGIN_DEFAULT = 60


class ShrimpSignoffMixin(models.AbstractModel):
    _name = "shrimp.signoff.mixin"
    _description = "Firma de una de las dos partes"

    # Texto del error cuando no se da conformidad sin motivo. Cada modelo lo
    # redefine con su propio lenguaje.
    _SIGNOFF_REASON_REQUIRED = (
        "Para no dar conformidad hay que decir por qué: es lo que abre la "
        "discusión con la otra parte.")
    _SIGNOFF_ARCHIVED_MSG = (
        "Esta firma es de una ronda anterior que ya se cerró; firme la vigente.")

    def _signoff_check_actor(self, actor=None):
        """`actor` es quien dice firmar; tiene que ser el titular de la fila."""
        self.ensure_one()
        actor = actor or self.env.user.partner_id
        if actor != self.partner_id:
            raise AccessError(_(
                "Cada parte firma la suya. Esta corresponde a «%s».")
                % (self.partner_id.name or ""))
        return actor

    def _signoff_check_open(self):
        """La fila tiene que estar vigente y sin decidir."""
        self.ensure_one()
        if "active" in self._fields and not self.active:
            raise ValidationError(self._SIGNOFF_ARCHIVED_MSG)
        if self.decision != "pending":
            raise ValidationError(_("Esta parte ya firmó; no se puede cambiar."))

    def _signoff_check_reason(self, decision, motivo):
        if decision == "rejected" and not (motivo or "").strip():
            raise ValidationError(self._SIGNOFF_REASON_REQUIRED)

    def _signoff_archive(self, motivo):
        """Retira estas firmas de la ronda vigente dejando constancia."""
        vals = {"active": False}
        for campo, valor in (("archived_at", fields.Datetime.now()),
                             ("archived_by_uid", self.env.user.id),
                             ("archive_reason", motivo)):
            if campo in self._fields:
                vals[campo] = valor
        return self.write(vals)

    # ==================================================================
    # Historial y «deshacer mi decisión»
    # ==================================================================
    # Campo que apunta al expediente (verificación, orden, compromiso).
    _SIGNOFF_PARENT_FIELD = None
    # Campos que forman la postura: se fotografían antes de cada decisión y se
    # restauran al deshacerla.
    _SIGNOFF_SNAPSHOT_FIELDS = ("decision", "reason", "decided_at")

    def _signoff_parent(self):
        self.ensure_one()
        return self[self._SIGNOFF_PARENT_FIELD]

    def _signoff_round(self):
        return self.ronda if "ronda" in self._fields else 1

    def _signoff_others(self):
        """Las demás posturas vigentes de la misma ronda."""
        self.ensure_one()
        dominio = [(self._SIGNOFF_PARENT_FIELD, "=", self._signoff_parent().id),
                   ("id", "!=", self.id)]
        if "ronda" in self._fields:
            dominio.append(("ronda", "=", self.ronda))
        return self.sudo().search(dominio)

    def _signoff_label(self, campo, valor):
        if not valor:
            return ""
        return dict(self._fields[campo]._description_selection(self.env)).get(valor, valor)

    @staticmethod
    def _signoff_param_int(env, clave, defecto):
        # Ajustes › CamaronMarket (ventana y margen de «deshacer»).
        return env["shrimp.settings"].get_int(clave, defecto, minimum=0)

    # ---- foto de la postura ----
    def _signoff_snapshot(self):
        self.ensure_one()
        foto = {}
        for campo in self._SIGNOFF_SNAPSHOT_FIELDS:
            if campo not in self._fields:
                continue
            valor = self[campo]
            tipo = self._fields[campo].type
            if tipo == "many2one":
                valor = valor.id or False
            elif tipo == "datetime":
                valor = fields.Datetime.to_string(valor) if valor else False
            foto[campo] = valor
        return foto

    def _signoff_pending_snapshot(self):
        """Postura «en blanco»: lo que se restaura si una decisión antigua no
        dejó foto (las que se tomaron antes de existir el historial)."""
        foto = {}
        for campo in self._SIGNOFF_SNAPSHOT_FIELDS:
            if campo not in self._fields:
                continue
            tipo = self._fields[campo].type
            foto[campo] = 0.0 if tipo in ("float", "monetary") else False
        foto["decision"] = "pending"
        return foto

    def _signoff_restore(self, foto):
        self.ensure_one()
        vals = {k: v for k, v in (foto or {}).items() if k in self._fields}
        if vals:
            self.sudo().with_context(_shrimp_firma_ok=True, shrimp_signoff_undo=True).write(vals)

    # ---- historial ----
    def _signoff_last_decision_event(self):
        self.ensure_one()
        return self.env["shrimp.signoff.event"].sudo().search([
            ("res_model", "=", self._name), ("res_id", "=", self.id),
            ("kind", "=", "decision"), ("undone", "=", False)], limit=1)

    def _signoff_log(self, kind, before, reason=None, auto=False, on_behalf=False,
                     others_before=None, extra=None):
        """Deja la decisión (o su reversión) en el historial y en el chatter."""
        self.ensure_one()
        parent = self._signoff_parent()
        antes = (before or {}).get("decision") or "pending"
        evento = self.env["shrimp.signoff.event"].sudo().create({
            "kind": kind,
            "res_model": self._name,
            "res_id": self.id,
            "parent_model": parent._name,
            "parent_id": parent.id,
            "parent_name": parent.display_name,
            "ronda": self._signoff_round(),
            "role": self.role,
            "role_label": self._signoff_label("role", self.role),
            "partner_id": self.partner_id.id,
            "user_id": self.env.user.id,
            "decision": self.decision,
            "decision_label": self._signoff_label("decision", self.decision),
            "previous_decision": antes,
            "previous_label": self._signoff_label("decision", antes),
            "counter_price": self["counter_price"] if "counter_price" in self._fields else 0.0,
            "previous_counter_price": (before or {}).get("counter_price") or 0.0,
            "reason": (reason or "").strip() or False,
            "auto": bool(auto),
            "on_behalf": bool(on_behalf),
            "snapshot_before": before or False,
            "others_before": others_before or False,
            "extra": extra or False,
        })
        cuando = fields.Datetime.context_timestamp(self, evento.date).strftime("%d/%m/%Y %H:%M")
        datos = {
            "parte": evento.role_label, "titular": self.partner_id.name or "",
            "quien": evento.actor_label(), "cuando": cuando,
            "decision": evento.decision_label, "antes": evento.previous_label,
        }
        if kind == "undo":
            cuerpo = _("Decisión deshecha · %(parte)s (%(titular)s): «%(antes)s» vuelve a "
                       "«%(decision)s». Lo hizo %(quien)s el %(cuando)s.") % datos
        else:
            cuerpo = _("Decisión · %(parte)s (%(titular)s): %(decision)s. "
                       "Lo hizo %(quien)s el %(cuando)s.") % datos
            if evento.decision == "counter" and evento.counter_price:
                cuerpo += " " + _("Precio propuesto: %s.") % evento.counter_price
        if evento.reason:
            cuerpo += " " + _("Motivo: %s") % evento.reason
        if hasattr(parent, "message_post"):
            parent.sudo().message_post(body=cuerpo, subtype_xmlid="mail.mt_note")
        return evento

    def signoff_history(self):
        self.ensure_one()
        return self.env["shrimp.signoff.event"].history_for(self._signoff_parent())

    # ---- reglas de «deshacer» ----
    def _signoff_undo_blocker(self):
        """Motivo por el que NO se puede deshacer, o False. Cada modelo añade
        sus condiciones (hasta cuándo) llamando a super()."""
        self.ensure_one()
        if "active" in self._fields and not self.active:
            return self._SIGNOFF_ARCHIVED_MSG
        if self.decision == "pending":
            return _("No hay ninguna decisión tuya que deshacer.")
        ultimo = self._signoff_last_decision_event()
        if (ultimo and ultimo.auto) or ("auto" in self._fields and self.auto):
            return _("Esta decisión la tomó el sistema al vencer el plazo; no se deshace.")
        return False

    def _signoff_undo_until(self):
        """Texto corto: hasta cuándo se puede deshacer."""
        return ""

    def _signoff_after_undo(self, evento):
        """Recalcula el expediente después de restaurar la postura."""
        return True

    def _signoff_portal_url(self, partner):
        return "/my"

    def signoff_undo_info(self, actor=None):
        """Lo que necesita la pantalla: ¿puede deshacer?, ¿por qué no?, ¿hasta cuándo?"""
        self.ensure_one()
        rec = self.sudo()
        actor = actor or self.env.user.partner_id
        if actor != rec.partner_id:
            return {"can": False, "blocker": _("No es tu firma."), "until": ""}
        bloqueo = rec._signoff_undo_blocker()
        return {"can": not bloqueo, "blocker": bloqueo or "",
                "until": "" if bloqueo else rec._signoff_undo_until()}

    def signoff_can_undo(self, actor=None):
        return self.signoff_undo_info(actor=actor)["can"]

    def action_signoff_undo(self, reason=None, actor=None, on_behalf=False):
        """Devuelve la postura a como estaba antes de su última decisión.

        `actor` se comprueba SIEMPRE, igual que al firmar: el controlador
        resuelve la fila en sudo. `on_behalf` es el gestor de la plataforma,
        que tiene que dar motivo.
        """
        self.ensure_one()
        reason = (reason or "").strip() or False
        if on_behalf:
            if not self.env.user.has_group("shrimp_marketplace.group_shrimp_manager"):
                raise AccessError(_(
                    "Solo un gestor de la plataforma deshace la decisión en nombre de una parte."))
            if not reason:
                raise ValidationError(_(
                    "Para deshacer en nombre de una parte hay que dejar el motivo."))
        else:
            self._signoff_check_actor(actor)
        rec = self.sudo()
        bloqueo = rec._signoff_undo_blocker()
        if bloqueo:
            raise UserError(bloqueo)
        decision = rec._signoff_last_decision_event()
        antes = rec._signoff_snapshot()
        foto = (decision.snapshot_before if decision else None) or rec._signoff_pending_snapshot()
        rec._signoff_restore(foto)
        # Las posturas que esta decisión tocó (p. ej. la contraoferta devolvió
        # la del vendedor a «pendiente») vuelven a como estaban.
        for rid, otra_foto in ((decision.others_before or {}) if decision else {}).items():
            otra = rec.browse(int(rid)).exists()
            if otra:
                otra._signoff_restore(otra_foto)
        rec.with_context(shrimp_signoff_undo=True)._signoff_after_undo(decision)
        evento = rec._signoff_log("undo", antes, reason=reason, on_behalf=on_behalf)
        if decision:
            decision.write({"undone": True, "undo_event_id": evento.id})
        rec._signoff_notify_undo(evento)
        return True

    def _signoff_notify_undo(self, evento):
        """Correo a la otra parte; constancia en el chatter de a quién se avisó."""
        self.ensure_one()
        parent = self._signoff_parent()
        entregados = []
        for otra in self._signoff_others():
            socio = otra.partner_id
            if not socio or socio == self.partner_id:
                continue
            ok = evento._send_template(
                "shrimp_marketplace.mail_template_signoff_reverted", socio.email,
                ctx={"portal_url": self._signoff_portal_url(socio),
                     "destinatario": socio.name})
            entregados.append((socio, ok))
        if entregados and hasattr(parent, "_message_log"):
            ok = [p.name for p, enviado in entregados if enviado]
            fallo = [p.name for p, enviado in entregados if not enviado]
            partes = []
            if ok:
                partes.append(_("Aviso de la decisión deshecha enviado a: %s.") % ", ".join(ok))
            if fallo:
                partes.append(_("No se pudo enviar por correo el aviso de la decisión "
                                "deshecha a: %s.") % ", ".join(fallo))
            parent.sudo()._message_log(body=" ".join(partes))
        return entregados

    def _signoff_grace_minutes(self):
        return self._signoff_param_int(self.env, UNDO_GRACE_PARAM, UNDO_GRACE_DEFAULT)

    def _signoff_margin_minutes(self):
        return self._signoff_param_int(self.env, UNDO_MARGIN_PARAM, UNDO_MARGIN_DEFAULT)

    def _signoff_within_grace(self):
        """¿Sigue abierta la ventana de gracia desde la decisión?"""
        self.ensure_one()
        if not self.decided_at:
            return False
        limite = self.decided_at + timedelta(minutes=self._signoff_grace_minutes())
        return fields.Datetime.now() <= limite

    def action_open_signoff_undo_wizard(self):
        """Botón del backend: el gestor deshace en nombre de la parte."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deshacer la decisión de la parte"),
            "res_model": "shrimp.signoff.undo.wizard",
            "view_mode": "form",
            "target": "new",
            # default_* y no active_*: el cliente web pisa active_model con el
            # del registro desde el que se pulsa el botón (p. ej. el historial).
            "context": {"default_res_model": self._name, "default_res_id": self.id,
                        "default_posture_name": self.display_name},
        }
