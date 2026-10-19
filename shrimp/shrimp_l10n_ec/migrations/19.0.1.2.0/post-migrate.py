"""19.0.1.2.0: unión con la cola SRI de Cristian.

Las bases que tuvieron el puente anterior de dev_carballo (19.0.1.1.0, solo
TrazulDemo) conservan su cron «firmar y encolar comprobantes SRI de
servicios»: estaba en noupdate y llama a un método que ya no existe. Se borra.
Idempotente; en las bases que nunca lo tuvieron no hace nada.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'shrimp_l10n_ec' AND name = 'ir_cron_shrimp_sri_queue'
           AND model = 'ir.cron'
    """)
    row = cr.fetchone()
    if row:
        cr.execute("DELETE FROM ir_cron WHERE id = %s", (row[0],))
        cr.execute("""
            DELETE FROM ir_model_data
             WHERE module = 'shrimp_l10n_ec' AND name = 'ir_cron_shrimp_sri_queue'
        """)
