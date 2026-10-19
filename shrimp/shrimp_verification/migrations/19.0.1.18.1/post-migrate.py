"""19.0.1.18.1 — menú del sitio de verificadores = la lista única.

Los hijos guardados de «Verificaciones» y «Mi empresa» (sitio de
verificadores) decían «Verificaciones abiertas» (?state=open), «Todas» y
«Mi acreditación» → /marketplace/my-certificates (que solo redirige),
mientras la barra pintaba «Por iniciar», «Todas mis verificaciones»... Se
alinean con SHRIMP_VERIFIER_JOBS (models/website_navbar.py). Solo se tocan
los que conservan el texto/URL por defecto anterior (lo que el cliente
cambió desde el editor se respeta); falta «Rechazadas»: se crea. Idempotente.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

VIEJOS = {
    # (nombre viejo, url vieja): (nombre nuevo, url nueva)
    ("Verificaciones abiertas", "/verifier/inbox?state=open"): ("Por iniciar", "/verifier/inbox?state=pending"),
    ("Todas", "/verifier/inbox"): ("Todas mis verificaciones", "/verifier/inbox"),
    ("Mi acreditación", "/marketplace/my-certificates"): ("Mi acreditación", "/marketplace/my-account#certificados"),
}


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.shrimp_verification.models.website_navbar import SHRIMP_VERIFIER_JOBS
    M = env["website.menu"].sudo()
    cambios = 0
    for sitio in env["website"].sudo().search([("shrimp_platform", "=", "verifier")]):
        for padre in M.search([("website_id", "=", sitio.id),
                               ("shrimp_key", "in", ("verifier_jobs", "verifier_company"))]):
            for hijo in padre.child_id:
                nuevo = VIEJOS.get((hijo.name, hijo.url))
                if nuevo:
                    hijo.write({"name": nuevo[0], "url": nuevo[1]})
                    cambios += 1
            if padre.shrimp_key != "verifier_jobs":
                continue
            urls = set(padre.child_id.mapped("url"))
            for i, (_k, label, url, *_r) in enumerate(SHRIMP_VERIFIER_JOBS):
                if url not in urls:
                    M.create({"name": label, "url": url, "parent_id": padre.id,
                              "website_id": sitio.id, "sequence": 10 + i * 10})
                    cambios += 1
            orden = {url: 10 + i * 10 for i, (_k, _l, url, *_r) in enumerate(SHRIMP_VERIFIER_JOBS)}
            for hijo in padre.child_id:
                if hijo.url in orden and hijo.sequence != orden[hijo.url]:
                    hijo.sequence = orden[hijo.url]
    _logger.info("Menú del sitio de verificadores alineado con la lista única: %s cambios", cambios)
