"""19.0.1.18.0 — la bandeja del maquilador tras unir con los cambios de Cristian.

Une las dos versiones de la regla: la de 19.0.1.9.0 (el maquilador ve
también las solicitudes que se le adjudicaron o en las que ofertó, en
cualquier estado) y la de Cristian (subusuarios: user.shrimp_actor_partner_id;
la bandeja de publicadas solo con el perfil activo maquilador, CR-12). Corre
después de las migraciones de los dos lados, en la demo y en producción.

La regla vive en un bloque ``noupdate="1"``: un ``-u`` no la toca en una base
ya instalada. Aquí se reescribe ``domain_force`` leyéndolo del propio XML, para
que el fichero siga siendo la única fuente del dominio. Idempotente.
"""
import logging
import os

from lxml import etree

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

FICHERO = os.path.join(os.path.dirname(__file__), "..", "..", "security",
                       "shrimp_copacking_rules.xml")
REGLAS = ("rule_copack_request_bandeja",)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    arbol = etree.parse(os.path.normpath(FICHERO))
    for xmlid in REGLAS:
        nodo = arbol.find(".//record[@id='%s']/field[@name='domain_force']" % xmlid)
        regla = env.ref("shrimp_copacking.%s" % xmlid, raise_if_not_found=False)
        if nodo is None or not regla:
            _logger.warning("Migración 19.0.1.18.0: no se encontró %s", xmlid)
            continue
        dominio = (nodo.text or "").strip()
        if regla.domain_force != dominio:
            regla.write({"domain_force": dominio})
            _logger.info("Regla %s actualizada", xmlid)
