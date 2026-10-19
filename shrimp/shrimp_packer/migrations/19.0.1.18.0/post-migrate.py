"""D-15 / D-48 (auditoría de procesos).

- Listas «hasta segunda orden» ya publicadas: arrancan su caducidad hoy (el
  plazo configurado, 30 días por defecto), para no archivar de golpe listas que
  nadie sabía que caducaban.
- Compromisos «a lista» ya aceptados sin foto de precios: se toma la foto ahora,
  de la lista que rige para su cosecha, para que una lista posterior ya no los
  altere.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {"shrimp_lista_sin_bloqueo": True})
    Lista = env["shrimp.price.list"]
    abiertas = Lista.search([("state", "=", "published"), ("open_ended", "=", True),
                             ("expires_on", "=", False)])
    for lista in abiertas:
        lista.expires_on = lista._caducidad_desde_hoy()
    Com = env["shrimp.harvest.commitment"]
    sin_foto = Com.search([("price_mode", "=", "lista"), ("state", "=", "accepted"),
                           ("price_snapshot_at", "=", False)])
    sin_foto._tomar_foto_precios()
    _logger.info("shrimp_packer D-15/D-48: %s lista(s) con caducidad, %s compromiso(s) con foto",
                 len(abiertas), len(sin_foto))
