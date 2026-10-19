# -*- coding: utf-8 -*-
from odoo import models

# Rutas web en inglés (shrimp_user_registry.tests.test_rutas_legacy). Las
# viejas en español redirigen con 301: los PDF ya firmados llevan impresa la
# dirección de comprobación /verificacion/firma/<token> y tiene que seguir
# funcionando.
_LEGACY_ROUTES = [
    ("/verificacion/firma/<token>", "/signature/<token>"),
    ("/verificacion/firma/<token>/pdf", "/signature/<token>/pdf"),
    ("/verifier/verifications/<ref>/firmar", "/verifier/verifications/<ref>/sign"),
    ("/verifier/verifications/<ref>/informe.pdf", "/verifier/verifications/<ref>/report/pdf"),
]


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _shrimp_legacy_routes(cls):
        return super()._shrimp_legacy_routes() + _LEGACY_ROUTES
