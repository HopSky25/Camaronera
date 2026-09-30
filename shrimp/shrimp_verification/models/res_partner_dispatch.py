"""La puntualidad del vendedor: cuánto se parece su cita a su llegada.

Es el mismo tipo de dato que la «puntería» de la reserva anticipada
(``shrimp_packer/models/res_partner_reserva.py``): allí se mide el error entre
las libras declaradas y las cosechadas, aquí entre la hora prometida y la real.
Y se calcula con el mismo criterio, a propósito, porque es el mismo criterio el
que lo hace honesto:

  * No se almacena. Son cuentas baratas sobre pocos registros; almacenarlo
    obligaría a recalcular en cadena cada vez que se estampa una llegada, que
    es justo el tipo de dependencia que después se desincroniza sin avisar.

  * La MUESTRA viaja siempre y con nombre propio, y sin muestra no se enseña
    promedio. Un «0 min de desvío» sobre un solo despacho no es puntualidad, es
    una coincidencia, y enseñado como promedio se lee como un aval. El resumen
    devuelve {} cuando no hay nada que contar para que la pantalla pueda
    callarse en vez de pintar un bloque de ceros.

Va en fichero propio con su ``_inherit``, igual que en el módulo de la
empacadora, para no chocar con ``res_partner.py``, donde se trabaja en
paralelo.
"""

from odoo import api, fields, models


class ResPartnerDispatch(models.Model):
    _inherit = "res.partner"

    dispatch_seller_ids = fields.One2many(
        "shrimp.dispatch", "seller_partner_id",
        string="Despachos hechos como vendedor")

    despacho_muestra = fields.Integer(
        string="Despachos con cita y llegada",
        compute="_compute_despacho_historial",
        help="Sobre cuántos despachos se calcula la puntualidad. Sin esto, un "
             "porcentaje no significa nada.")
    despacho_a_tiempo = fields.Integer(
        string="Despachos a la hora", compute="_compute_despacho_historial")
    despacho_puntualidad_pct = fields.Float(
        string="Puntualidad (%)", digits=(5, 2),
        compute="_compute_despacho_historial",
        help="Qué parte de sus despachos llegó dentro de la media hora de "
             "tolerancia sobre la cita que él mismo fijó.")
    despacho_desvio_medio_min = fields.Float(
        string="Desvío medio de la cita (min)", digits=(16, 1),
        compute="_compute_despacho_historial",
        help="Cuánto se aparta de media la llegada real de la estimada, en "
             "cualquier dirección. Cuanto más bajo, más se puede confiar en la "
             "hora que promete.")
    despacho_retraso_medio_min = fields.Float(
        string="Retraso medio (min)", digits=(16, 1),
        compute="_compute_despacho_historial",
        help="Media con signo: positiva si suele llegar tarde, negativa si "
             "suele adelantarse.")
    despacho_citas_movidas = fields.Integer(
        string="Veces que movió una cita",
        compute="_compute_despacho_historial",
        help="Mover la cita avisando no es un incumplimiento, pero moverla "
             "muchas veces dice tanto como llegar tarde.")

    @api.depends("dispatch_seller_ids.eta",
                 "dispatch_seller_ids.actual_arrival",
                 "dispatch_seller_ids.eta_changes")
    def _compute_despacho_historial(self):
        for socio in self:
            cerrados = socio.dispatch_seller_ids.filtered(
                lambda d: d.eta and d.actual_arrival)
            desfases = [d.delay_minutes for d in cerrados]
            socio.despacho_muestra = len(desfases)
            socio.despacho_a_tiempo = len(cerrados.filtered("on_time"))
            socio.despacho_citas_movidas = sum(
                socio.dispatch_seller_ids.mapped("eta_changes"))
            if desfases:
                socio.despacho_desvio_medio_min = (
                    sum(abs(x) for x in desfases) / len(desfases))
                socio.despacho_retraso_medio_min = sum(desfases) / len(desfases)
                socio.despacho_puntualidad_pct = (
                    100.0 * socio.despacho_a_tiempo / len(desfases))
            else:
                socio.despacho_desvio_medio_min = 0.0
                socio.despacho_retraso_medio_min = 0.0
                socio.despacho_puntualidad_pct = 0.0

    def despacho_resumen(self):
        """Lo que se le enseña a quien tiene que fiarse de la hora de este
        vendedor: la empacadora que compra y el técnico que va a esperarlo.

        {} cuando no hay ni un despacho cerrado. Los promedios viajan en None
        si la muestra es cero, nunca como 0.0: ese cero se lee al revés de lo
        que significa.
        """
        self.ensure_one()
        muestra = self.despacho_muestra
        if not muestra and not self.despacho_citas_movidas:
            return {}
        return {
            "muestra": muestra,
            "a_tiempo": self.despacho_a_tiempo if muestra else None,
            "puntualidad": self.despacho_puntualidad_pct if muestra else None,
            "desvio_min": self.despacho_desvio_medio_min if muestra else None,
            "retraso_min": self.despacho_retraso_medio_min if muestra else None,
            "citas_movidas": self.despacho_citas_movidas,
        }

    def despacho_resumen_texto(self):
        """Una línea para la bandeja del técnico, o cadena vacía.

        Se arma en Python y no en QWeb porque lleva un porcentaje: un '%'
        dentro de un t-esc rompe el render de la página con "incomplete format".
        """
        self.ensure_one()
        datos = self.despacho_resumen()
        if not datos or not datos.get("muestra"):
            return ""
        return "{pct:.0f}% a la hora · desvío medio {des:.0f} min · {n} despacho(s)".format(
            pct=datos["puntualidad"],
            des=datos["desvio_min"],
            n=datos["muestra"],
        )
