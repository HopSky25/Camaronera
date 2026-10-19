"""Histórico de evolución del producto y «Estado real del lote».

Cada fila es una foto del lote en un momento. Hay tres ORÍGENES y no se
mezclan en pantalla:

* «Observación del vendedor» (observation): lo que el dueño VE en su lote
  (etapa observada, talla o peso, supervivencia, sanidad, parámetros de agua
  y cantidad estimada). NO toca la publicación: la etapa publicada es la
  teórica de venta y la cambia el vendedor a mano. Se puede corregir o borrar
  durante unas horas (Ajustes, 24 por defecto); después queda fija porque es
  evidencia para los compradores.
* «Cambio en la publicación» (publication): la foto automática que deja
  shrimp.product.write cuando cambian los datos biológicos publicados.
* «Ficha de calidad» (quality): el muestreo que registra una ficha de
  calidad (shrimp.product.quality.action_register_sample).

Cuando una observación DIVERGE de lo publicado (otra etapa, o una talla que se
aparta más del umbral configurado) se avisa al vendedor —sugiriendo actualizar
la publicación, nunca se cambia sola— y a los compradores con compras o
reservas en curso de ese lote. Una misma divergencia no se avisa dos veces.
"""
import logging
from datetime import timedelta

import pytz

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)

EVOLUTION_ORIGINS = [
    ("observation", "Observación del vendedor"),
    ("publication", "Cambio en la publicación"),
    ("quality", "Ficha de calidad"),
]

# Rangos válidos (mínimo, máximo o None) de lo que se observa.
OBSERVATION_RANGES = {
    "survival_rate": (0.0, 100.0),
    "water_temp_c": (0.0, 40.0),
    "salinity_ppt": (0.0, 60.0),
    "dissolved_oxygen_mgl": (0.0, 20.0),
    "water_ph": (0.0, 14.0),
    "avg_size_mg": (0.0, None),
    "estimated_qty": (0.0, None),
}

# Parámetros de Ajustes › CamaronMarket (models/shrimp_evolution_settings.py).
PARAM_SIZE_THRESHOLD = "shrimp_marketplace.evolution_size_threshold_pct"
PARAM_EDIT_HOURS = "shrimp_marketplace.evolution_edit_hours"
DEFAULT_SIZE_THRESHOLD = 20
DEFAULT_EDIT_HOURS = 24


class ShrimpProductEvolution(models.Model):
    _name = "shrimp.product.evolution"
    _inherit = "shrimp.uuid.mixin"
    _description = "Histórico de evolución del producto"
    _order = "date desc, id desc"

    product_id = fields.Many2one(
        "shrimp.product",
        string="Producto",
        required=True,
        ondelete="cascade",
        index=True,
    )

    date = fields.Datetime(
        string="Fecha",
        default=fields.Datetime.now,
        required=True,
    )

    stage_id = fields.Many2one(
        "shrimp.stage",
        string="Estadío",
        ondelete="restrict",
    )

    avg_size_mg = fields.Float(
        string="Tamaño promedio (mg)",
    )

    survival_rate = fields.Float(
        string="Supervivencia (%)",
    )

    health_status = fields.Text(
        string="Estado sanitario / Observaciones",
    )

    available_qty = fields.Float(
        string="Cantidad disponible",
    )

    note = fields.Text(string="Nota")
    user_id = fields.Many2one(
        "res.users",
        string="Registrado por",
        default=lambda self: self.env.user,
        readonly=True,
    )

    # ---- Estado real del lote (19.0.1.30.0) --------------------------------
    origin = fields.Selection(
        EVOLUTION_ORIGINS, string="Origen", required=True, default="observation", index=True,
        help="Observación del vendedor (estado real, no cambia la publicación), cambio en la "
             "publicación (foto automática) o muestreo de una ficha de calidad.")
    published_stage_id = fields.Many2one(
        "shrimp.stage", string="Etapa publicada al observar", ondelete="set null", readonly=True,
        help="La etapa que tenía la publicación cuando se registró la observación.")
    water_temp_c = fields.Float(string="Temperatura del agua (°C)")
    salinity_ppt = fields.Float(string="Salinidad (ppt)")
    dissolved_oxygen_mgl = fields.Float(string="Oxígeno disuelto (mg/L)")
    water_ph = fields.Float(string="pH")
    estimated_qty = fields.Float(
        string="Cantidad estimada",
        help="Lo que el vendedor estima que hay en el lote en ese momento. Es informativo: "
             "NO cambia el stock publicado.")
    divergence_key = fields.Char(string="Divergencia avisada (técnico)", readonly=True, copy=False)

    _AUTO_NOTE = "Actualización automática del producto."

    def shrimp_observation(self):
        """Lo que se enseña en «Observación»: la nota escrita a mano o, si la
        fila la creó el sistema, el estado sanitario (que dice algo) en vez
        del texto genérico."""
        self.ensure_one()
        if self.note and self.note != self._AUTO_NOTE:
            return self.note
        return self.health_status or self.note or "—"

    # ------------------------------------------------------------------
    # Parámetros
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_edit_hours(self):
        return self.env["shrimp.settings"].get_int(PARAM_EDIT_HOURS, DEFAULT_EDIT_HOURS, 0, 24 * 30)

    @api.model
    def _shrimp_size_threshold(self):
        """% de diferencia de talla publicada/observada que cuenta como
        divergencia. 0 = solo se compara la etapa."""
        return self.env["shrimp.settings"].get_float(
            PARAM_SIZE_THRESHOLD, DEFAULT_SIZE_THRESHOLD, 0.0, 1000.0)

    # ------------------------------------------------------------------
    # Ayudas de presentación
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_tz(self):
        tz = self.env.context.get("tz") or self.env.user.tz
        return pytz.timezone(tz if tz in pytz.all_timezones_set else "America/Guayaquil")

    def shrimp_local_dt(self, dt=None):
        """Fecha (UTC en la base) en la hora local del usuario, sin tz."""
        dt = dt or (self.date if self else None)
        if not dt:
            return None
        return pytz.utc.localize(dt).astimezone(self._shrimp_tz()).replace(tzinfo=None)

    @api.model
    def shrimp_parse_local(self, texto):
        """'2026-10-14T09:30' (hora local del usuario) -> datetime UTC sin tz."""
        dt = fields.Datetime.to_datetime(str(texto).replace("T", " ")[:16] + ":00")
        return self._shrimp_tz().localize(dt).astimezone(pytz.utc).replace(tzinfo=None)

    def shrimp_origin_label(self):
        self.ensure_one()
        return dict(EVOLUTION_ORIGINS).get(self.origin, "")

    def shrimp_size_unit(self):
        self.ensure_one()
        return self.product_id.shrimp_observation_size_unit()

    def shrimp_size_value(self):
        """Talla en la unidad del lote (g para camarón, mg para nauplio/larva)."""
        self.ensure_one()
        if not self.avg_size_mg:
            return 0.0
        return self.avg_size_mg / 1000.0 if self.shrimp_size_unit() == "g" else self.avg_size_mg

    def shrimp_size_display(self):
        self.ensure_one()
        valor = self.shrimp_size_value()
        if not valor:
            return "—"
        unidad = self.shrimp_size_unit()
        return "%s %s" % (_fmt(valor, 2 if unidad == "g" else 3), unidad)

    def shrimp_water_rows(self):
        """[(etiqueta, valor con unidad)] de los parámetros de agua medidos."""
        self.ensure_one()
        filas = []
        if self.water_temp_c:
            filas.append((_("Temperatura"), "%s °C" % _fmt(self.water_temp_c)))
        if self.salinity_ppt:
            filas.append((_("Salinidad"), "%s ppt" % _fmt(self.salinity_ppt)))
        if self.dissolved_oxygen_mgl:
            filas.append((_("Oxígeno disuelto"), "%s mg/L" % _fmt(self.dissolved_oxygen_mgl)))
        if self.water_ph:
            filas.append((_("pH"), _fmt(self.water_ph)))
        return filas

    def shrimp_edit_deadline(self):
        self.ensure_one()
        base = self.create_date or fields.Datetime.now()
        return base + timedelta(hours=self._shrimp_edit_hours())

    def shrimp_is_editable(self):
        """Solo las observaciones del vendedor, y solo dentro de la ventana."""
        self.ensure_one()
        if self.origin != "observation":
            return False
        return fields.Datetime.now() < self.shrimp_edit_deadline()

    def shrimp_stock_hint(self):
        """True si la cantidad estimada es menor que el stock publicado."""
        self.ensure_one()
        disponible = self.product_id.available_qty or 0.0
        return bool(self.estimated_qty) and float_compare(
            self.estimated_qty, disponible, precision_digits=4) < 0

    def shrimp_json(self):
        """Datos para precargar el modal de edición."""
        self.ensure_one()
        return {
            "ref": self.uuid_ref,
            "date": self.shrimp_local_dt().strftime("%Y-%m-%dT%H:%M") if self.date else "",
            "stage": self.stage_id.uuid_ref or "",
            "size": self.shrimp_size_value() or "",
            "survival": self.survival_rate or "",
            "health": self.health_status or "",
            "note": self.note or "",
            "temp": self.water_temp_c or "",
            "salinity": self.salinity_ppt or "",
            "oxygen": self.dissolved_oxygen_mgl or "",
            "ph": self.water_ph or "",
            "qty": self.estimated_qty or "",
        }

    def shrimp_json_str(self):
        import json
        return json.dumps(self.shrimp_json())

    # ------------------------------------------------------------------
    # Divergencia con la publicación
    # ------------------------------------------------------------------
    def shrimp_divergence(self):
        """Compara esta observación con lo que publica HOY el producto.

        Devuelve None si coincide, o un dict:
          stage (bool), size (bool), size_pct (float), key (str), text (str).
        Solo para observaciones (y muestreos de calidad) con datos."""
        self.ensure_one()
        if self.origin == "publication":
            return None
        prod = self.product_id.sudo()
        pub_stage = prod.stage_id
        stage_diff = bool(self.stage_id and pub_stage and self.stage_id != pub_stage)
        size_diff, pct = False, 0.0
        umbral = self._shrimp_size_threshold()
        if umbral and self.avg_size_mg and prod.avg_size_mg:
            pct = (self.avg_size_mg - prod.avg_size_mg) / prod.avg_size_mg * 100.0
            size_diff = abs(pct) > umbral
        if not (stage_diff or size_diff):
            return None
        partes, clave = [], []
        if stage_diff:
            partes.append(_("etapa observada %(o)s y publicada %(p)s") % {
                "o": self.stage_id.name, "p": pub_stage.name})
            clave.append("st:%s>%s" % (pub_stage.id, self.stage_id.id))
        if size_diff:
            unidad = prod.shrimp_observation_size_unit()
            factor = 1000.0 if unidad == "g" else 1.0
            partes.append(_("talla observada %(o)s %(u)s y publicada %(p)s %(u)s (%(d)+.1f %%)") % {
                "o": _fmt(self.avg_size_mg / factor, 2 if unidad == "g" else 3),
                "p": _fmt(prod.avg_size_mg / factor, 2 if unidad == "g" else 3),
                "u": unidad, "d": pct})
            clave.append("sz:%s:%s" % (round(prod.avg_size_mg, 4), "+" if pct > 0 else "-"))
        return {
            "stage": stage_diff, "size": size_diff, "size_pct": pct,
            "key": "|".join(clave), "text": "; ".join(partes),
        }

    def _shrimp_notify_divergence(self):
        """Avisa (una sola vez por divergencia) al vendedor y a los compradores
        con compras o reservas en curso. Devuelve True si avisó."""
        self.ensure_one()
        if self.env.context.get("shrimp_evolution_no_notify") or self.origin != "observation":
            return False
        div = self.shrimp_divergence()
        if not div:
            return False
        prod = self.product_id.sudo()
        # ¿Ya se avisó esta misma divergencia (misma publicación, misma etapa
        # observada) en otra observación del lote? Entonces no se repite.
        ya = self.sudo().search_count([
            ("product_id", "=", prod.id), ("origin", "=", "observation"),
            ("divergence_key", "=", div["key"]), ("id", "!=", self.id)])
        if ya or self.divergence_key == div["key"]:
            return False
        self.sudo().with_context(shrimp_evolution_force=True).write({"divergence_key": div["key"]})
        prod._shrimp_notify_observation_divergence(self, div)
        return True

    # ------------------------------------------------------------------
    # Validación e inmutabilidad
    # ------------------------------------------------------------------
    @api.constrains(*OBSERVATION_RANGES)
    def _check_observation_ranges(self):
        etiquetas = {f: self._fields[f].string for f in OBSERVATION_RANGES}
        for rec in self:
            if rec.origin != "observation":
                continue
            for fname, (minimo, maximo) in OBSERVATION_RANGES.items():
                valor = rec[fname] or 0.0
                if valor < minimo or (maximo is not None and valor > maximo):
                    if maximo is None:
                        raise ValidationError(_("«%s» no puede ser negativo.") % etiquetas[fname])
                    raise ValidationError(_("«%(c)s» tiene que estar entre %(a)s y %(b)s.") % {
                        "c": etiquetas[fname], "a": _fmt(minimo), "b": _fmt(maximo)})

    @api.constrains("date", "origin")
    def _check_observation_date(self):
        limite = fields.Datetime.now() + timedelta(hours=1)
        for rec in self:
            if rec.origin == "observation" and rec.date and rec.date > limite:
                raise ValidationError(_("La fecha de la observación no puede ser futura."))

    def _shrimp_check_mutable(self):
        if self.env.context.get("shrimp_evolution_force"):
            return
        for rec in self:
            if rec.origin == "observation" and not rec.shrimp_is_editable():
                raise ValidationError(_(
                    "La observación del %s ya no se puede modificar ni borrar: pasado el plazo "
                    "de corrección queda fija como evidencia para los compradores.")
                    % (rec.shrimp_local_dt().strftime("%d/%m/%Y %H:%M") if rec.date else "—"))

    @api.model_create_multi
    def create(self, vals_list):
        Product = self.env["shrimp.product"].sudo()
        for vals in vals_list:
            # La foto automática de shrimp.product.write llega sin origen y
            # con su nota fija: es un cambio en la publicación.
            if "origin" not in vals and vals.get("note") in (self._AUTO_NOTE, _(self._AUTO_NOTE)):
                vals["origin"] = "publication"
            if vals.get("origin", "observation") == "observation" and vals.get("product_id") \
                    and "published_stage_id" not in vals:
                vals["published_stage_id"] = Product.browse(vals["product_id"]).stage_id.id or False
        records = super().create(vals_list)
        for rec in records.filtered(lambda r: r.origin == "observation"):
            try:
                rec._shrimp_notify_divergence()
            except Exception:  # noqa: BLE001 — un aviso nunca tumba el registro
                _logger.warning("No se pudo avisar la divergencia de %s", rec.uuid_ref, exc_info=True)
        return records

    def write(self, vals):
        self._shrimp_check_mutable()
        if "origin" in vals and not self.env.context.get("shrimp_evolution_force"):
            raise ValidationError(_("El origen de un registro de evolución no se cambia."))
        res = super().write(vals)
        if {"stage_id", "avg_size_mg"} & set(vals) and not self.env.context.get("shrimp_evolution_force"):
            for rec in self.filtered(lambda r: r.origin == "observation"):
                try:
                    rec._shrimp_notify_divergence()
                except Exception:  # noqa: BLE001
                    _logger.warning("No se pudo avisar la divergencia de %s", rec.uuid_ref, exc_info=True)
        return res

    def unlink(self):
        self._shrimp_check_mutable()
        return super().unlink()


def _fmt(valor, digitos=4):
    """1.50 -> '1.5', 12.0 -> '12', 0.0125 -> '0.0125'."""
    if valor is None:
        return "—"
    txt = ("%.*f" % (digitos, float(valor))).rstrip("0").rstrip(".")
    return txt or "0"
