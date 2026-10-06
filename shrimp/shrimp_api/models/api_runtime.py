"""Estado de ejecución de la API: límites de peticiones e idempotencia."""

import logging
import math

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ShrimpApiRateBucket(models.Model):
    """Contadores por minuto, compartidos por todos los workers.

    Es una tabla y no memoria del proceso porque Odoo corre con varios workers:
    un contador en memoria dejaría pasar N veces el límite. Se actualiza con
    INSERT … ON CONFLICT en un cursor aparte, que se confirma enseguida: la
    fila se bloquea microsegundos y nunca durante toda la petición.

    De paso sirve para contar llamadas: el cron suma los minutos ya cerrados
    al contador de la clave y borra las filas.
    """

    _name = "shrimp.api.rate.bucket"
    _description = "Contador de peticiones de la API por minuto"
    _auto = False
    _table = "shrimp_api_rate_bucket"
    _log_access = False

    subject = fields.Char(readonly=True)
    bucket = fields.Datetime(readonly=True)
    kind = fields.Char(readonly=True)
    hits = fields.Integer(readonly=True)

    def init(self):
        self.env.cr.execute("""
            CREATE TABLE IF NOT EXISTS shrimp_api_rate_bucket (
                subject varchar(64) NOT NULL,
                bucket timestamp without time zone NOT NULL,
                kind varchar(1) NOT NULL,
                hits integer NOT NULL DEFAULT 0,
                PRIMARY KEY (subject, bucket, kind)
            )
        """)

    @api.model
    def _hit(self, cr, subject, kind, limit):
        """Suma una petición y dice si cabe. Devuelve (permitida, usadas,
        segundos_hasta_el_próximo_minuto)."""
        cr.execute("""
            INSERT INTO shrimp_api_rate_bucket (subject, bucket, kind, hits)
            VALUES (%s, date_trunc('minute', now() at time zone 'utc'), %s, 1)
            ON CONFLICT (subject, bucket, kind)
            DO UPDATE SET hits = shrimp_api_rate_bucket.hits + 1
            RETURNING hits,
                      extract(epoch from (date_trunc('minute', now() at time zone 'utc')
                                          + interval '1 minute'
                                          - (now() at time zone 'utc')))
        """, (subject[:64], kind))
        hits, remaining = cr.fetchone()
        retry = max(1, int(math.ceil(float(remaining or 1))))
        return (not limit or hits <= limit), hits, retry

    @api.model
    def _cron_rollup(self):
        """Pasa los minutos cerrados al contador de cada clave y limpia."""
        self.env.cr.execute("""
            WITH old AS (
                DELETE FROM shrimp_api_rate_bucket
                 WHERE bucket < (now() at time zone 'utc') - interval '5 minutes'
             RETURNING subject, hits
            ), per_key AS (
                SELECT substr(subject, 3)::int AS key_id, sum(hits) AS total
                  FROM old
                 WHERE subject ~ '^k:[0-9]+$'
                 GROUP BY 1
            )
            UPDATE shrimp_api_key k
               SET call_count = coalesce(k.call_count, 0) + per_key.total
              FROM per_key
             WHERE k.id = per_key.key_id
        """)
        return True


class ShrimpApiIdempotency(models.Model):
    """Respuestas guardadas por Idempotency-Key durante 24 h.

    Un POST repetido con la misma clave y el mismo cuerpo devuelve la misma
    respuesta sin volver a ejecutar nada; con otro cuerpo, 422. Es lo que hace
    seguro reintentar tras un timeout: sin esto, un ERP que reintenta una
    creación de producto dejaba dos productos.
    """

    _name = "shrimp.api.idempotency"
    _description = "Idempotencia de la API externa"
    _order = "id desc"

    api_key_id = fields.Many2one("shrimp.api.key", required=True, ondelete="cascade", index=True)
    key = fields.Char(required=True)
    method = fields.Char()
    path = fields.Char()
    request_hash = fields.Char()
    state = fields.Selection([("in_progress", "En curso"), ("done", "Hecha")],
                             default="in_progress", required=True)
    status_code = fields.Integer()
    response_body = fields.Text()
    response_type = fields.Char()

    _uniq_key = models.Constraint("unique(api_key_id, key)",
                                  "Esa Idempotency-Key ya se usó con esta clave.")

    @api.model
    def _cron_cleanup(self):
        limit = fields.Datetime.subtract(fields.Datetime.now(), hours=24)
        self.sudo().search([("create_date", "<", limit)]).unlink()
        return True
