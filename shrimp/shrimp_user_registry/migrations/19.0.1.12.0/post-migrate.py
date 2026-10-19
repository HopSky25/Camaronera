"""19.0.1.12.0: derechos LOPDP y retención (hueco #7).

Crea (o completa) un BORRADOR de la Política de privacidad que describe lo
que el sistema hace ahora (anonimización a los 12 meses, registros técnicos,
descarga de la copia portable). La versión publicada no se toca."""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    borrador = env["shrimp.legal.document"]._shrimp_lopdp_privacy_draft()
    _logger.warning("shrimp_user_registry 19.0.1.12.0: borrador de privacidad %s",
                    borrador.display_label if borrador else "(no hacía falta)")
