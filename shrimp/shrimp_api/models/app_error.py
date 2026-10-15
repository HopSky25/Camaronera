"""Errores que reporta la app móvil (docs/app/CONTRATO-MEJORAS-APP.md, C2).

Sin terceros (ni Sentry ni Crashlytics) y sin datos personales: la app manda
el texto del error, su pila y datos del aparato (modelo, versión del sistema,
pantalla), nunca usuario, correo, clave ni contenido de formularios. Se
agrupan por huella (sha256 del error + primera línea útil de la pila) para
que mil teléfonos con el mismo fallo sean una fila con «veces = 1000».
"""

import hashlib
import logging
import re

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

RETENTION_DAYS = 90
MAX_VERSIONS = 30

# Marcos de la pila que no dicen dónde está el fallo: el SDK de Dart y el
# propio Flutter. La «primera línea útil» es la primera que no es de ellos.
_FRAMEWORK_FRAME = re.compile(r"\(?(dart:|package:flutter/|<asynchronous suspension>)")
_FRAME_INDEX = re.compile(r"^#\d+\s+")


def first_useful_line(stack):
    """Primera línea de la pila que apunta al código de la app."""
    lines = [l.strip() for l in (stack or "").splitlines() if l.strip()]
    for line in lines:
        if not _FRAMEWORK_FRAME.search(line):
            return _FRAME_INDEX.sub("", line)[:500]
    return _FRAME_INDEX.sub("", lines[0])[:500] if lines else ""


def fingerprint(error, stack):
    base = (error or "").strip() + "\n" + first_useful_line(stack)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


class ShrimpAppError(models.Model):
    _name = "shrimp.app.error"
    _description = "Error reportado por la app móvil"
    _order = "last_seen desc, id desc"
    _rec_name = "error_head"

    fingerprint = fields.Char(string="Huella", required=True, readonly=True, index=True)
    error = fields.Text(string="Error", readonly=True)
    error_head = fields.Char(string="Error (resumen)", compute="_compute_error_head", store=True)
    stack_line = fields.Char(string="Primera línea útil de la pila", readonly=True)
    stack = fields.Text(string="Pila (último reporte)", readonly=True)
    platform = fields.Char(string="Plataforma", readonly=True)
    os_version = fields.Char(string="Sistema (último)", readonly=True)
    device_model = fields.Char(string="Modelo (último)", readonly=True)
    screen = fields.Char(string="Pantalla (última)", readonly=True)
    app_versions = fields.Char(string="Versiones de la app", readonly=True,
                               help="Versiones que reportaron este error, separadas por coma.")
    last_app_version = fields.Char(string="Última versión que lo reportó", readonly=True)
    first_seen = fields.Datetime(string="Primera vez", readonly=True)
    last_seen = fields.Datetime(string="Última vez", readonly=True, index=True)
    last_occurred_at = fields.Datetime(string="Ocurrió (según el teléfono)", readonly=True)
    count = fields.Integer(string="Veces", readonly=True, default=0)
    resolved = fields.Boolean(
        string="Resuelto", help="Marcado a mano. Si vuelve a llegar, se desmarca solo.")

    _fingerprint_unique = models.Constraint(
        "unique(fingerprint)", "Ya hay un error con esa huella.")

    @api.depends("error")
    def _compute_error_head(self):
        for rec in self:
            first = (rec.error or "").strip().splitlines()[:1]
            rec.error_head = (first[0][:200] if first else "") or "(sin texto)"

    @api.model
    def _record(self, vals):
        """Suma un reporte (vals ya validados) a su grupo, o lo crea."""
        error = vals.get("error") or ""
        stack = vals.get("stack") or ""
        fp = fingerprint(error, stack)
        now = fields.Datetime.now()
        version = (vals.get("app_version") or "").strip()[:32]
        common = {
            "stack": stack or False,
            "platform": vals.get("platform") or False,
            "os_version": vals.get("os_version") or False,
            "device_model": vals.get("device_model") or False,
            "screen": vals.get("screen") or False,
            "last_seen": now,
            "last_occurred_at": vals.get("occurred_at") or False,
            "resolved": False,
        }
        if version:
            common["last_app_version"] = version
        Error = self.sudo()
        # FOR UPDATE: dos reportes simultáneos del mismo fallo no pierden la cuenta.
        self.env.cr.execute("SELECT id FROM shrimp_app_error WHERE fingerprint = %s FOR UPDATE", (fp,))
        row = self.env.cr.fetchone()
        if row:
            rec = Error.browse(row[0])
            versions = [v for v in (rec.app_versions or "").split(",") if v]
            if version and version not in versions:
                versions = (versions + [version])[-MAX_VERSIONS:]
            common.update(count=rec.count + 1, app_versions=",".join(versions) or False)
            rec.write(common)
            return rec
        try:
            with self.env.cr.savepoint():
                return Error.create(dict(common, fingerprint=fp, error=error,
                                         stack_line=first_useful_line(stack) or False,
                                         first_seen=now, count=1,
                                         app_versions=version or False))
        except Exception:  # noqa: BLE001 - otro worker creó el grupo a la vez
            self.env.cr.execute("SELECT id FROM shrimp_app_error WHERE fingerprint = %s FOR UPDATE", (fp,))
            row = self.env.cr.fetchone()
            if not row:
                raise
            rec = Error.browse(row[0])
            rec.write(dict(common, count=rec.count + 1))
            return rec

    @api.model
    def _cron_cleanup(self):
        """Borra los grupos que no se repiten desde hace RETENTION_DAYS días."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=RETENTION_DAYS)
        old = self.sudo().search([("last_seen", "<", limit)])
        if old:
            _logger.info("Errores de la app: se borran %s grupos sin reportes desde %s",
                         len(old), limit)
            old.unlink()
        return True
