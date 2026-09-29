"""Preferencias de la empacadora sobre los avisos de lotes nuevos.

Va en un archivo aparte de res_partner.py a propósito: son los mandos de un
canal que se puede apagar, y el que los toque tiene que ver junto al campo la
razón por la que existe cada uno.

El canal entero se juega su vida en estos cuatro campos. Un aviso que no se
puede silenciar, que llega uno por lote o que trae lotes que a la empacadora
no le sirven se marca como spam en dos semanas, y cuando eso pasa no se
recupera: el filtro del correo lo aprende y ya no vuelve a entregar nada, ni
siquiera lo bueno. Por eso el silencio se ofrece de entrada y en el perfil, no
escondido en un pie de correo.
"""

from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Horas locales a las que sale cada resumen. Son decisiones de negocio, no de
# programación:
#
#  - 07:00 porque la cosecha se decide de madrugada y la compra se cierra por
#    la mañana. Un aviso de las 11 llega cuando el camión ya salió.
#  - 15:00 en la modalidad de dos tandas, para lo que se publicó durante la
#    mañana. El camarón pierde valor en 48 horas; esperar al día siguiente es
#    perder un turno de colocación.
#
# No hay opción de "cada lote al instante" y no es un olvido: es la diferencia
# entre un canal que se lee y uno que se silencia.
HORAS_AVISO = {
    "diario": (7,),
    "dos_veces": (7, 15),
}

# Ecuador no tiene horario de verano, pero la hora del servidor es UTC y sin
# convertir el resumen "de las 7" saldría a las 2 de la madrugada.
TZ_DEFECTO = "America/Guayaquil"


class ResPartnerAvisosLotes(models.Model):
    _inherit = "res.partner"

    emp_aviso_frecuencia = fields.Selection(
        [("off", "No quiero avisos"),
         ("diario", "Un resumen al día (7:00)"),
         ("dos_veces", "Dos resúmenes al día (7:00 y 15:00)")],
        string="Avisos de lotes nuevos",
        default="diario",
        help="Cada cuánto quieres el resumen de los lotes publicados que tu "
             "lista de precios vigente cotiza. Siempre es un correo con todos "
             "los lotes juntos, nunca uno por lote.")

    emp_aviso_min_cantidad = fields.Float(
        string="Cantidad mínima para avisarme (lb)",
        default=0.0,
        help="Por debajo de esta cantidad no te avisamos. Una empacadora que "
             "compra por contenedor no quiere enterarse de un lote de 300 lb.")

    # Por defecto en falso: el aviso sale del cruce con la lista de precios, y
    # la lista va dirigida a proveedores concretos. Avisar de lotes de
    # camaroneras a las que la empacadora todavía no le pasa lista es captar
    # proveedor nuevo, que es otra intención y no todas la tienen. Quien la
    # tenga lo enciende; no se le impone a nadie.
    emp_aviso_incluir_no_dirigidos = fields.Boolean(
        string="Incluir camaroneras a las que aún no paso lista",
        default=False,
        help="Además de tus proveedores, te avisamos de lotes de otras "
             "camaroneras cuya talla y presentación sí cotizas. Sirve para "
             "captar proveedor nuevo.")

    # Dos relojes distintos y hacen falta los dos: la revisión marca hasta
    # dónde se miró (y evita rehacer el cruce cada hora cuando no hay nada), y
    # el envío es lo que se le enseña a la empacadora en su perfil. Con un solo
    # campo, una pasada sin lotes borraría la fecha del último correo real.
    emp_aviso_ultima_revision = fields.Datetime(
        string="Última revisión de avisos", readonly=True, copy=False)
    emp_aviso_ultimo_envio = fields.Datetime(
        string="Último aviso enviado", readonly=True, copy=False)

    @api.constrains("emp_aviso_min_cantidad")
    def _check_emp_aviso_min_cantidad(self):
        for rec in self:
            if (rec.emp_aviso_min_cantidad or 0.0) < 0.0:
                raise ValidationError(_(
                    "La cantidad mínima para avisarte no puede ser negativa."))

    def _aviso_slot_pendiente(self, ahora):
        """La hora de envío ya pasada que todavía no se ha servido, o False.

        No basta con mirar si «son las siete»: el cron puede pasar a las 7:59,
        o no pasar a las siete porque el servidor estaba caído, y entonces la
        empacadora se quedaría sin su resumen del día sin que nadie se entere.
        Se busca la última hora de reparto que ya pasó y se compara con lo que
        se sirvió, así un cron retrasado reparte igual y uno que corre cada
        hora no manda dos veces lo mismo.
        """
        self.ensure_one()
        horas = HORAS_AVISO.get(self.emp_aviso_frecuencia or "off")
        if not horas:
            return False
        try:
            tz = pytz.timezone(self.tz or TZ_DEFECTO)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone(TZ_DEFECTO)
        local = pytz.utc.localize(ahora).astimezone(tz)

        # Se miran también las horas de ayer: a las 03:00 locales la última
        # hora de reparto que pasó es la de la tarde anterior.
        candidatas = []
        for dia in (local.date() - timedelta(days=1), local.date()):
            for hora in horas:
                candidatas.append(tz.localize(datetime.combine(dia, time(hora, 0))))
        pasadas = [c for c in candidatas if c <= local]
        if not pasadas:
            return False
        slot = max(pasadas)

        servido = self.emp_aviso_ultima_revision
        if servido and pytz.utc.localize(servido).astimezone(tz) >= slot:
            return False
        return slot

    def aviso_lotes_resumen(self):
        """Frase para el perfil: cómo está configurado el canal."""
        self.ensure_one()
        if (self.emp_aviso_frecuencia or "off") == "off":
            return _("Los avisos de lotes nuevos están apagados.")
        if not self.email:
            return _("No tienes correo en la ficha: no podemos avisarte.")
        if not self.emp_aviso_ultimo_envio:
            return _("Todavía no te hemos mandado ningún aviso.")
        return _("Último aviso enviado el %s.") % fields.Datetime.context_timestamp(
            self, self.emp_aviso_ultimo_envio).strftime("%d/%m/%Y a las %H:%M")
