"""19.0.1.3.0 — el aguaje se elige del calendario de la plataforma.

* «Rige para» (aguaje_txt, almacenado) pasa a fechas dd/mm/aaaa: se recalcula
  en las listas que ya tienen aguaje.
* El generador lunar cubría 15 lunaciones y en algunos años perdía la última
  fase de diciembre (2027 se quedaba sin el aguaje del 27 de diciembre). Se
  completan los huecos de los años ya sembrados desde el actual, solo con
  aguajes calculados que no pisen ninguno existente: lo ajustado a mano o
  traído del INOCAR no se toca y no se duplica nada.
* Se asegura que haya aguajes próximos (lo mismo que hace el cron diario).

Las listas históricas no se tocan: siguen con su aguaje (aunque haya pasado) o
sin él. Idempotente.
"""
from odoo import SUPERUSER_ID, api, fields


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Lista = env["shrimp.price.list"]
    listas = Lista.search([("aguaje_id", "!=", False)])
    if listas:
        env.add_to_compute(Lista._fields["aguaje_txt"], listas)
    Aguaje = env["shrimp.aguaje"]
    hoy = fields.Date.context_today(Aguaje)
    for anio in sorted(set(Aguaje.search([("year", ">=", hoy.year)]).mapped("year"))):
        Aguaje.sembrar_anio(anio, completar=True)
    Aguaje._cron_asegurar_calendario()
    env.flush_all()
