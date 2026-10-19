"""«Estado real del lote»: observaciones del vendedor sobre su producto.

La etapa que se elige al publicar es la TEÓRICA de venta. El lote real sigue
creciendo (o no) y el vendedor deja constancia de lo que ve sin tocar la
publicación. Aquí viven las reglas del producto:

* quién puede registrar observaciones (el dueño con permiso «Vender» en el
  perfil del lote; los subusuarios sin ese permiso, no),
* qué etapas se pueden observar (las del tipo de lote del perfil),
* el alta validada (shrimp_register_observation),
* el aviso de divergencia al vendedor y a los compradores en curso,
* «Actualizar publicación a la etapa observada» — explícito, nunca solo.

Sin campos nuevos en shrimp.product: todo se lee de shrimp.product.evolution.
"""
import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .shrimp_product_evolution import OBSERVATION_RANGES, _fmt

_logger = logging.getLogger(__name__)

# Tipos de estadío que observa cada perfil (mismo criterio que el formulario
# de publicación: controllers/product_portal.py STAGE_TIPOS_POR_ROL).
OBS_TIPOS_POR_ROL = {
    "semillero": ("nauplio", "larva"),
    "laboratorio": ("nauplio", "larva"),
    "camaronera": ("camaron",),
}

PUBLIC_ORIGINS = ("observation", "quality")


class ShrimpProductObservation(models.Model):
    _inherit = "shrimp.product"

    # ------------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------------
    def shrimp_observations(self, limit=None):
        """Observaciones del vendedor y muestreos de calidad, del más nuevo al
        más viejo (lo que enseña «Estado real del lote»)."""
        self.ensure_one()
        return self.env["shrimp.product.evolution"].sudo().search([
            ("product_id", "=", self.id), ("origin", "in", PUBLIC_ORIGINS),
        ], order="date desc, id desc", limit=limit)

    def shrimp_publication_changes(self):
        self.ensure_one()
        return self.env["shrimp.product.evolution"].sudo().search([
            ("product_id", "=", self.id), ("origin", "=", "publication"),
        ], order="date desc, id desc")

    def shrimp_last_observation(self):
        self.ensure_one()
        return self.shrimp_observations(limit=1)

    def shrimp_observation_divergence(self):
        """Divergencia de la última observación con la publicación actual."""
        self.ensure_one()
        ultima = self.shrimp_last_observation()
        return ultima.shrimp_divergence() if ultima else None

    def shrimp_observation_size_unit(self):
        """Gramos para camarón (juvenil/engorde), miligramos para nauplio y larva."""
        self.ensure_one()
        return "g" if self._shrimp_tipo() == "camaron" else "mg"

    def shrimp_published_size_display(self):
        self.ensure_one()
        if not self.avg_size_mg:
            return "—"
        unidad = self.shrimp_observation_size_unit()
        return "%s %s" % (_fmt(self.avg_size_mg / (1000.0 if unidad == "g" else 1.0),
                               2 if unidad == "g" else 3), unidad)

    def shrimp_observation_stages(self):
        """Etapas que se pueden observar en este lote: las del tipo que
        publica su perfil (nauplio/larva o camarón)."""
        self.ensure_one()
        tipos = OBS_TIPOS_POR_ROL.get(self.seller_role) or (
            ("camaron",) if self._shrimp_tipo() == "camaron" else ("nauplio", "larva"))
        stages = self.env["shrimp.stage"].sudo().search([("active", "=", True)])
        return stages.filtered(lambda s: s._shrimp_tipo() in tipos)

    def shrimp_observation_chart(self):
        """Mini gráficos SVG (talla y supervivencia vs tiempo) de las
        observaciones. Dos gráficos de una serie: nunca dos escalas en uno."""
        self.ensure_one()
        obs = self.shrimp_observations().sorted(lambda e: (e.date, e.id))
        unidad = self.shrimp_observation_size_unit()
        factor = 1000.0 if unidad == "g" else 1.0
        talla = [(e.date, e.avg_size_mg / factor) for e in obs if e.avg_size_mg]
        superv = [(e.date, e.survival_rate) for e in obs if e.survival_rate]
        graficos = []
        if len(talla) >= 2:
            graficos.append(_sparkline(_("Talla promedio (%s)") % unidad, talla, unidad,
                                       "evo-c-size"))
        if len(superv) >= 2:
            graficos.append(_sparkline(_("Supervivencia (%)"), superv, "%", "evo-c-surv",
                                       clamp=(0.0, 100.0)))
        return graficos

    # ------------------------------------------------------------------
    # Permisos
    # ------------------------------------------------------------------
    def _shrimp_user_can_observe(self, user=None):
        """Dueño del lote con permiso «Vender» en su perfil, o interno."""
        self.ensure_one()
        user = user or self.env.user
        if not user or user._is_public():
            return False
        if not user.share:
            return True
        actor = user.sudo()._shrimp_actor_partner()
        if not actor or actor != self.sudo().seller_partner_id:
            return False
        return actor.with_user(user)._shrimp_can("sell_products", role=self.sudo().seller_role)

    def _shrimp_check_can_observe(self):
        for rec in self:
            if not rec._shrimp_user_can_observe():
                raise AccessError(_("Solo el vendedor del lote (con permiso «Vender») registra "
                                    "observaciones."))

    # ------------------------------------------------------------------
    # Alta / edición
    # ------------------------------------------------------------------
    def _shrimp_observation_vals(self, data):
        """Valida y normaliza los datos de una observación.

        `data`: date (datetime o texto), stage (shrimp.stage), size (en la
        unidad del lote), survival_rate, health_status, note, water_temp_c,
        salinity_ppt, dissolved_oxygen_mgl, water_ph, estimated_qty."""
        self.ensure_one()
        stage = data.get("stage")
        if not stage:
            raise ValidationError(_("Indica la etapa observada."))
        if stage not in self.shrimp_observation_stages():
            raise ValidationError(_("La etapa «%s» no corresponde a este tipo de lote.") % stage.name)
        fecha = data.get("date") or fields.Datetime.now()
        if isinstance(fecha, str):
            try:
                fecha = self.env["shrimp.product.evolution"].shrimp_parse_local(fecha)
            except (ValueError, TypeError):
                raise ValidationError(_("La fecha de la observación no es válida."))
        factor = 1000.0 if self.shrimp_observation_size_unit() == "g" else 1.0
        if data.get("avg_size_mg") is not None:   # API: siempre en mg
            size = (data.get("avg_size_mg") or 0.0) / factor
        else:
            size = data.get("size") or 0.0
        if size < 0:
            raise ValidationError(_("La talla o peso promedio no puede ser negativo."))
        vals = {
            "date": fecha,
            "stage_id": stage.id,
            "avg_size_mg": size * factor,
            "survival_rate": data.get("survival_rate") or 0.0,
            "health_status": (data.get("health_status") or "").strip()[:1000] or False,
            "note": (data.get("note") or "").strip()[:1000] or False,
            "water_temp_c": data.get("water_temp_c") or 0.0,
            "salinity_ppt": data.get("salinity_ppt") or 0.0,
            "dissolved_oxygen_mgl": data.get("dissolved_oxygen_mgl") or 0.0,
            "water_ph": data.get("water_ph") or 0.0,
            "estimated_qty": data.get("estimated_qty") or 0.0,
        }
        etiquetas = self.env["shrimp.product.evolution"]._fields
        for fname, (minimo, maximo) in OBSERVATION_RANGES.items():
            valor = vals[fname]
            if valor < minimo or (maximo is not None and valor > maximo):
                if maximo is None:
                    raise ValidationError(_("«%s» no puede ser negativo.") % etiquetas[fname].string)
                raise ValidationError(_("«%(c)s» tiene que estar entre %(a)s y %(b)s.") % {
                    "c": etiquetas[fname].string, "a": _fmt(minimo), "b": _fmt(maximo)})
        return vals

    def shrimp_register_observation(self, data):
        """Registra una observación del vendedor. NO toca la publicación ni el
        stock: la cantidad estimada es informativa."""
        self.ensure_one()
        self._shrimp_check_can_observe()
        if not self.sudo().active:
            raise ValidationError(_("El producto está dado de baja."))
        vals = self._shrimp_observation_vals(data)
        vals.update({
            "product_id": self.id,
            "origin": "observation",
            "user_id": self.env.user.id,
            "available_qty": self.sudo().available_qty,
        })
        return self.env["shrimp.product.evolution"].sudo().create(vals)

    def shrimp_update_observation(self, observation, data):
        self.ensure_one()
        self._shrimp_check_can_observe()
        if observation.product_id != self or observation.origin != "observation":
            raise AccessError(_("Esa observación no es de este lote."))
        vals = self._shrimp_observation_vals(data)
        observation.sudo().write(vals)
        return observation

    def shrimp_delete_observation(self, observation):
        self.ensure_one()
        self._shrimp_check_can_observe()
        if observation.product_id != self or observation.origin != "observation":
            raise AccessError(_("Esa observación no es de este lote."))
        observation.sudo().unlink()
        return True

    # ------------------------------------------------------------------
    # «Actualizar publicación a la etapa observada» (explícito)
    # ------------------------------------------------------------------
    def shrimp_apply_observation(self, observation):
        """Lleva la etapa (y la talla, si también diverge) observada a la
        publicación. Solo cuando el vendedor lo pide; deja la foto
        «Cambio en la publicación» de siempre."""
        self.ensure_one()
        self._shrimp_check_can_observe()
        prod = self.sudo()
        if observation.product_id != self or observation.origin not in PUBLIC_ORIGINS:
            raise AccessError(_("Esa observación no es de este lote."))
        div = observation.shrimp_divergence()
        if not div:
            raise ValidationError(_("La publicación ya coincide con lo observado."))
        vals = {}
        if div["stage"]:
            if prod._shrimp_requires_size_grade(observation.stage_id) and (
                    not prod.presentation or not prod.size_grade_id):
                raise ValidationError(_(
                    "Para publicar como «%s» el lote necesita presentación y talla comercial: "
                    "edita el producto.") % observation.stage_id.name)
            vals["stage_id"] = observation.stage_id.id
        if div["size"] and observation.avg_size_mg:
            vals["avg_size_mg"] = observation.avg_size_mg
        if "stage_id" in vals and prod.has_purchases():
            raise ValidationError(_(
                "La etapa publicada no se puede cambiar porque el lote ya tiene compras: lo que se "
                "vendió se vendió con esa etapa. Tus observaciones siguen visibles para los "
                "compradores."))
        prod.write(vals)
        return True

    # ------------------------------------------------------------------
    # Avisos
    # ------------------------------------------------------------------
    def _shrimp_ongoing_buyers(self):
        """{comprador: (registro donde avisar, rol)} con compras confirmadas
        sin recibir (también despachadas) o reservas (chequeo) activas."""
        self.ensure_one()
        out = {}
        txs = self.env["shrimp.transaction"].sudo().search([
            ("product_id", "=", self.id), ("state", "=", "confirmed"),
        ], order="id desc")
        for tx in txs:
            if tx.buyer_partner_id and tx.buyer_partner_id not in out:
                out[tx.buyer_partner_id] = (tx, tx.buyer_role)
        reservas = self.env["shrimp.check.request"].sudo().search([
            ("product_id", "=", self.id), ("state", "in", ("requested", "under_review")),
        ])
        for cr in reservas:
            if cr.buyer_partner_id and cr.buyer_partner_id not in out:
                out[cr.buyer_partner_id] = (self, None)
        return out

    @api.model
    def _shrimp_account_users(self, partner, role, permission):
        Tx = self.env["shrimp.transaction"]
        if hasattr(Tx, "_shrimp_party_users"):
            return Tx._shrimp_party_users(partner, role, permission)
        return self.env["res.users"].sudo().search([("partner_id", "=", partner.id)])

    @staticmethod
    def _shrimp_post(record, partners, subject, body):
        if not partners:
            return
        try:
            record.sudo().with_context(mail_notify_force_send=False).message_post(
                body=body, subject=subject, partner_ids=partners.ids,
                message_type="comment", subtype_xmlid="mail.mt_comment",
                email_layout_xmlid="mail.mail_notification_light")
        except Exception:  # noqa: BLE001 — un aviso nunca tumba la observación
            _logger.warning("No se pudo enviar el aviso de divergencia (%s)", subject, exc_info=True)

    def _shrimp_notify_observation_divergence(self, observation, div):
        """Vendedor: sugerencia de actualizar la publicación. Compradores en
        curso: el estado real difiere de lo publicado. Uno por persona."""
        self.ensure_one()
        prod = self.sudo()
        base = prod.get_base_url()
        url = "%s/marketplace/product/%s#evo_real" % (base, prod.uuid_ref)
        seller_users = self._shrimp_account_users(prod.seller_partner_id, prod.seller_role, "sell")
        seller_partners = seller_users.mapped("partner_id").filtered("email") or \
            prod.seller_partner_id.filtered("email")
        asunto = _("Tu lote %s no coincide con lo publicado") % prod.name
        texto = _(
            "Registraste una observación de «%(p)s» que no coincide con la publicación: %(d)s. "
            "Si el lote ya es así, actualiza la publicación con el botón «Actualizar publicación "
            "a la etapa observada» de la ficha del producto. No se cambia nada sin que lo pidas.") % {
            "p": prod.name, "d": div["text"]}
        prod._shrimp_post(prod, seller_partners, asunto, Markup("<p>%s</p><p><a href=\"%s\">%s</a></p>") % (
            texto, url, _("Ver el estado real del lote")))

        avisados = seller_partners
        for comprador, (registro, rol) in prod._shrimp_ongoing_buyers().items():
            users = self._shrimp_account_users(comprador, rol, "buy")
            destinatarios = (users.mapped("partner_id").filtered("email")
                             or comprador.filtered("email")) - avisados
            if not destinatarios:
                continue
            avisados |= destinatarios
            asunto_c = _("Novedad en el lote %s que compraste") % prod.name
            texto_c = _(
                "El vendedor registró el estado real del lote «%(p)s» y no coincide con lo publicado: "
                "%(d)s. Revisa el detalle; si tienes dudas, coordina con el vendedor antes de la "
                "entrega.") % {"p": prod.name, "d": div["text"]}
            prod._shrimp_post(registro, destinatarios, asunto_c, Markup(
                "<p>%s</p><p><a href=\"%s\">%s</a></p>") % (texto_c, url, _("Ver el estado real del lote")))


def _sparkline(titulo, puntos, unidad, clase, clamp=None):
    """SVG en línea de una serie (fecha, valor). Colores por clase CSS (modo
    claro/oscuro desde la hoja); cada punto lleva <title> como tooltip. La
    escala sigue a los datos (con un margen) para que se vea la tendencia;
    `clamp` la limita (0-100 en la supervivencia)."""
    W, H, PL, PR, PT, PB = 320, 120, 36, 10, 12, 22
    valores = [v for _d, v in puntos]
    lo, hi = min(valores), max(valores)
    margen = (hi - lo) * 0.15 or max(abs(hi) * 0.05, 1.0)
    lo, hi = lo - margen, hi + margen
    lo = max(0.0, lo)
    if clamp:
        lo, hi = max(clamp[0], lo), min(clamp[1], hi)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    t0 = puntos[0][0].timestamp()
    t1 = puntos[-1][0].timestamp()
    span = (t1 - t0) or 1.0

    def X(d):
        return PL + (d.timestamp() - t0) / span * (W - PL - PR)

    def Y(v):
        return PT + (1 - (v - lo) / (hi - lo)) * (H - PT - PB)

    linea = " ".join("%.1f,%.1f" % (X(d), Y(v)) for d, v in puntos)
    marcas = "".join(
        '<circle class="evo-c-dot" cx="%.1f" cy="%.1f" r="4"><title>%s · %s %s</title></circle>' % (
            X(d), Y(v), d.strftime("%d/%m/%Y"), _fmt(v, 2), unidad)
        for d, v in puntos)
    ejes = (
        '<line class="evo-c-grid" x1="%d" y1="%d" x2="%d" y2="%d"/>'
        '<line class="evo-c-grid" x1="%d" y1="%d" x2="%d" y2="%d"/>' % (
            PL, PT, W - PR, PT, PL, H - PB, W - PR, H - PB))
    etiquetas = (
        '<text class="evo-c-lbl" x="%d" y="%d" text-anchor="end">%s</text>'
        '<text class="evo-c-lbl" x="%d" y="%d" text-anchor="end">%s</text>'
        '<text class="evo-c-lbl" x="%d" y="%d">%s</text>'
        '<text class="evo-c-lbl" x="%d" y="%d" text-anchor="end">%s</text>' % (
            PL - 4, PT + 4, _fmt(round(hi, 2)), PL - 4, H - PB + 4, _fmt(round(lo, 2)),
            PL, H - 6, puntos[0][0].strftime("%d/%m"),
            W - PR, H - 6, puntos[-1][0].strftime("%d/%m")))
    ultimo = puntos[-1]
    svg = (
        '<figure class="evo-chart %s"><figcaption>%s <b>%s %s</b></figcaption>'
        '<svg viewBox="0 0 %d %d" role="img" aria-label="%s" preserveAspectRatio="none">'
        '%s%s<polyline class="evo-c-line" fill="none" points="%s"/>%s</svg></figure>' % (
            clase, Markup.escape(titulo), _fmt(ultimo[1], 2), Markup.escape(unidad),
            W, H, Markup.escape(titulo), ejes, etiquetas, linea, marcas))
    return Markup(svg)
