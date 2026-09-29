"""Guardado de las preferencias de aviso de lotes.

Va en un controlador propio y no dentro de main.py por la misma razón por la
que los campos van en un modelo aparte: es el interruptor de un canal que se
puede apagar, y tiene que poderse leer entero de una sentada.

Odoo compone en una sola clase todos los controladores que heredan del mismo
padre, así que este _guardar_extra() encadena con el de la empacadora sin
tocarlo.
"""

from odoo.addons.shrimp_marketplace.controllers.account_portal import (
    ShrimpAccountPortalController)

from ..models.res_partner_avisos import HORAS_AVISO


class ShrimpPackerAlertsAccount(ShrimpAccountPortalController):

    def _guardar_extra(self, partner, post):
        res = super()._guardar_extra(partner, post)
        if partner.shrimp_user_type != "empacadora":
            return res

        # La frecuencia se valida contra las que existen de verdad. Si llegara
        # un valor inventado por el formulario, escribirlo dejaría a la
        # empacadora en un estado en el que no le llega nada y el perfil le
        # diría que sí le llega.
        frecuencia = post.get("emp_aviso_frecuencia")
        if frecuencia not in set(HORAS_AVISO) | {"off"}:
            frecuencia = partner.emp_aviso_frecuencia or "diario"

        try:
            minimo = float((post.get("emp_aviso_min_cantidad") or "0").replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            minimo = 0.0

        res.update({
            "emp_aviso_frecuencia": frecuencia,
            "emp_aviso_min_cantidad": max(minimo, 0.0),
            # Booleano de casilla: hay que escribir también el False, porque si
            # solo se escribieran las marcadas no habría forma de volver a
            # apagarla.
            "emp_aviso_incluir_no_dirigidos": bool(
                post.get("emp_aviso_incluir_no_dirigidos")),
        })
        return res
