# -*- coding: utf-8 -*-
"""19.0.1.2.1: cifra el certificado que quedó en claro al crear compañías.

Hasta esta versión create() no se interceptaba, así que una compañía creada con
ec_sri_cert_file / ec_sri_cert_password (importación, XML-RPC, script) dejaba el
.p12 como adjunto y la contraseña en su columna, en claro. write() nunca los
guarda, así que cualquier valor en esos dos campos es texto en claro: se cifra
con el mismo helper de la subida y luego se borra. Idempotente: tras correr, esos
campos quedan vacíos y una segunda pasada no encuentra nada.

Si el servidor no tiene la llave maestra no se toca nada (no se puede cifrar) y
se deja un aviso en el log.
"""
import base64
import logging
from odoo import api, SUPERUSER_ID
from odoo.addons.l10n_ec_sri_community.services import crypto

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("SELECT id FROM res_company WHERE ec_sri_cert_password IS NOT NULL AND ec_sri_cert_password != ''")
    ids = {row[0] for row in cr.fetchall()}
    cr.execute("SELECT DISTINCT res_id FROM ir_attachment WHERE res_model = 'res.company' "
               "AND res_field = 'ec_sri_cert_file' AND res_id IS NOT NULL")
    ids |= {row[0] for row in cr.fetchall()}
    if not ids:
        return
    try:
        has_key = crypto.has_master_key()
    except ValueError:
        has_key = False
    if not has_key:
        _logger.warning('SRI 19.0.1.2.1: %s compañía(s) con certificado o contraseña EN CLARO (ids %s), pero '
                        'falta EC_SRI_MASTER_KEY: no se cifraron. Vuelva a subir el certificado.', len(ids), sorted(ids))
        return
    encrypted = cleared = 0
    for company in env['res.company'].browse(sorted(ids)).exists():
        cert_b64 = company.ec_sri_cert_file
        cr.execute('SELECT ec_sri_cert_password FROM res_company WHERE id = %s', (company.id,))
        password = cr.fetchone()[0]
        if cert_b64:
            try:
                with cr.savepoint():
                    company._ec_sri_store_certificate(base64.b64decode(cert_b64), password)
                encrypted += 1
            except Exception as exc:  # noqa: BLE001
                # El certificado en claro no es utilizable (contraseña errada, vencido...):
                # no se cifra, pero tampoco se deja el secreto en claro.
                _logger.warning('SRI 19.0.1.2.1: no se pudo cifrar el certificado de la compañía %s (%s); '
                                'se borra la copia en claro, vuelva a subirlo.', company.id, type(exc).__name__)
        # Los campos en claro no los lee la firma (usa los cifrados): se vacían siempre.
        cr.execute('UPDATE res_company SET ec_sri_cert_password = NULL WHERE id = %s', (company.id,))
        env['ir.attachment'].search([('res_model', '=', 'res.company'), ('res_id', '=', company.id),
                                     ('res_field', '=', 'ec_sri_cert_file')]).unlink()
        cleared += 1
    env.invalidate_all()
    _logger.info('SRI 19.0.1.2.1: %s certificado(s) cifrado(s); copia en claro borrada en %s compañía(s).',
                 encrypted, cleared)
