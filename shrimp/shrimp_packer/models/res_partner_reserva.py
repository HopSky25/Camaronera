"""Lo que la reserva anticipada añade al socio.

Dos cosas, y las dos existen por el mismo motivo: una empacadora se está
comprometiendo a un precio sobre camarón que todavía no ha visto, así que lo
único que puede mirar antes de firmar es el pasado de esa camaronera.

1. El interruptor para recibir declaraciones abiertas. Sin él, «abrirla a
   todas» le metería la cosecha de cualquiera en la bandeja de cualquier
   empacadora, incluida la que solo compra a sus proveedores de siempre.

2. El historial de reservas: cuántas cumplió, cuántas rompió y —lo más
   útil— con cuánta puntería estima. Una camaronera que siempre declara
   40.000 y siempre entrega 39.000 vale más que una que declara 40.000 y
   entrega entre 20.000 y 60.000, aunque las dos tengan cero incumplimientos.
   Esa puntería es la información que el ranking de rendimiento verificado no
   puede dar, porque el ranking mide lo que pasó EN PLANTA y esto mide lo que
   pasó ENTRE LO DICHO Y LO HECHO.

Va en fichero propio con su `_inherit` para no chocar con `res_partner.py`,
donde se trabaja en paralelo.
"""

from odoo import api, fields, models


class ResPartnerReserva(models.Model):
    _inherit = "res.partner"

    # ------------------------------------------------------------------
    # La empacadora decide si quiere que le lleguen reservas abiertas
    # ------------------------------------------------------------------
    reserva_acepta = fields.Boolean(
        string="Acepto reservas anticipadas",
        help="Si lo marcas, las cosechas que se publiquen abiertas te llegan a "
             "la bandeja. Las dirigidas a ti te llegan igual, marques o no.")

    # ------------------------------------------------------------------
    # Inversos. Además de servir para las pantallas, son lo que permite
    # escribir las reglas de acceso de la contraparte: sin ellos no hay forma
    # de decir «los contactos con los que tengo una reserva» en un dominio.
    # ------------------------------------------------------------------
    reserva_forecast_ids = fields.One2many(
        "shrimp.harvest.forecast", "farmer_partner_id",
        string="Cosechas declaradas")
    reserva_commitment_farmer_ids = fields.One2many(
        "shrimp.harvest.commitment", "farmer_partner_id",
        string="Compromisos recibidos")
    reserva_commitment_packer_ids = fields.One2many(
        "shrimp.harvest.commitment", "packer_partner_id",
        string="Compromisos hechos")

    reserva_cumplidas = fields.Integer(
        string="Reservas cumplidas", compute="_compute_reserva_historial")
    reserva_incumplidas = fields.Integer(
        string="Reservas incumplidas", compute="_compute_reserva_historial")
    reserva_liberadas = fields.Integer(
        string="Reservas liberadas de común acuerdo",
        compute="_compute_reserva_historial")
    reserva_fiabilidad = fields.Float(
        string="Fiabilidad en reservas (%)", digits=(5, 2),
        compute="_compute_reserva_historial")
    reserva_punteria_pct = fields.Float(
        string="Error medio de la estimación (%)", digits=(5, 2),
        compute="_compute_reserva_historial",
        help="Cuánto se aparta de media la cosecha real de la declarada. "
             "Cuanto más bajo, más se puede confiar en lo que declara.")
    reserva_muestra = fields.Integer(
        string="Cosechas declaradas ya cosechadas",
        compute="_compute_reserva_historial")

    @api.depends("reserva_commitment_farmer_ids.state",
                 "reserva_commitment_packer_ids.state",
                 "reserva_forecast_ids.state")
    def _compute_reserva_historial(self):
        """Los números se calculan sobre los compromisos de AMBOS lados.

        Un socio puede romper como camaronera y como empacadora, y la
        fiabilidad que le interesa a quien lo mira es la suya, no la de un rol.
        Por eso se suman los dos conjuntos en vez de mantener dos contadores
        que en la práctica nadie compararía.

        No se almacena: son cuentas baratas sobre pocos registros y almacenarlo
        obligaría a recalcular en cadena cada vez que se mueve un compromiso,
        que es justo el tipo de dependencia que después se desincroniza.
        """
        for socio in self:
            compromisos = (socio.reserva_commitment_farmer_ids
                           | socio.reserva_commitment_packer_ids)
            cumplidas = len(compromisos.filtered(lambda c: c.state == "honored"))
            # Solo cuenta como incumplimiento si lo rompió ESTE socio. Que la
            # contraparte se eche atrás no puede manchar a quien lo sufrió.
            incumplidas = len(compromisos.filtered(
                lambda c: c.state == "broken" and c.broken_by_partner_id == socio))
            liberadas = len(compromisos.filtered(lambda c: c.state == "released"))
            socio.reserva_cumplidas = cumplidas
            socio.reserva_incumplidas = incumplidas
            socio.reserva_liberadas = liberadas
            cerradas = cumplidas + incumplidas
            socio.reserva_fiabilidad = (
                100.0 * cumplidas / cerradas) if cerradas else 0.0

            # Puntería: sobre las cosechas declaradas que ya se cosecharon, sin
            # importar si hubo compromiso. Es una medida de la calidad de la
            # biometría de la finca, no de su comportamiento comercial.
            cosechadas = socio.reserva_forecast_ids.filtered(
                lambda f: f.state == "harvested" and f.expected_lb and f.actual_lb)
            errores = [
                100.0 * abs(f.actual_lb - f.expected_lb) / f.expected_lb
                for f in cosechadas
            ]
            socio.reserva_muestra = len(errores)
            socio.reserva_punteria_pct = (
                sum(errores) / len(errores)) if errores else 0.0

    def reserva_resumen(self):
        """Lo que se le enseña a la empacadora antes de que se comprometa.

        Devuelve {} cuando no hay nada que contar, para que la pantalla pueda
        callarse en vez de pintar un bloque de ceros. Un cero al lado de
        «incumplimientos» sobre una muestra de cero reservas se lee como un
        aval, y no lo es.

        La muestra viaja SIEMPRE y con nombre propio, por el mismo motivo por
        el que el ranking de proveedores lleva su contador en cada métrica:
        el único uso deshonesto de esta caja sería enseñar un porcentaje sin
        decir de cuántos casos sale.
        """
        self.ensure_one()
        cerradas = self.reserva_cumplidas + self.reserva_incumplidas
        if not cerradas and not self.reserva_muestra:
            return {}
        return {
            "cumplidas": self.reserva_cumplidas,
            "incumplidas": self.reserva_incumplidas,
            "liberadas": self.reserva_liberadas,
            "cerradas": cerradas,
            "fiabilidad": self.reserva_fiabilidad if cerradas else None,
            "punteria": (self.reserva_punteria_pct
                         if self.reserva_muestra else None),
            "muestra": self.reserva_muestra,
        }
