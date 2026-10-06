"""Ayudas para los scripts de migración de los módulos shrimp_*.

Viven en un módulo importable (y no copiadas en cada script) porque las
migraciones de varios módulos hacen lo mismo: pasar al perfil común los datos
que cada rol guardaba en sus propios campos.
"""


def columnas(cr, tabla):
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s",
               (tabla,))
    return {r[0] for r in cr.fetchall()}


def copiar_perfil(cr, mapa, tipo=None, unidad_capacidad=None, columna_capacidad=None):
    """Copia columnas viejas al perfil común sin pisar lo ya escrito.

    `mapa`: {campo_nuevo: [columnas_viejas en orden de preferencia]}.
    """
    cols = columnas(cr, "res_partner")
    filtro = "shrimp_user_type = %s" if tipo else "shrimp_user_type IS NOT NULL"
    params = [tipo] if tipo else []
    for nuevo, viejos in mapa.items():
        viejos = [v for v in viejos if v in cols]
        if nuevo not in cols or not viejos:
            continue
        origen = "COALESCE(%s)" % ", ".join("NULLIF(%s, '')" % v for v in viejos)
        cr.execute(
            "UPDATE res_partner SET %s = %s WHERE (%s IS NULL OR %s = '') AND %s IS NOT NULL AND %s"
            % (nuevo, origen, nuevo, nuevo, origen, filtro), params)
    if columna_capacidad and columna_capacidad in cols and "shrimp_capacity_value" in cols:
        cr.execute(
            "UPDATE res_partner SET shrimp_capacity_value = %s, shrimp_capacity_unit = %%s "
            "WHERE COALESCE(shrimp_capacity_value, 0) = 0 AND COALESCE(%s, 0) <> 0 AND %s"
            % (columna_capacidad, columna_capacidad, filtro), [unidad_capacidad] + params)




def crear_perfiles_desde_tipo(cr):
    """Varios perfiles por cuenta: una línea shrimp.partner.role por cada
    socio con shrimp_user_type, con el estado de aprobación de la cuenta.

    Idempotente (solo crea las que faltan) y en SQL para no disparar
    restricciones ni correos sobre miles de contactos.
    """
    cols = columnas(cr, "shrimp_partner_role")
    if not cols:
        return 0
    cr.execute("""
        INSERT INTO shrimp_partner_role
               (partner_id, role, state, sequence, request_date, decision_date,
                create_uid, create_date, write_uid, write_date)
        SELECT p.id, p.shrimp_user_type, COALESCE(p.shrimp_account_state, 'approved'), 10,
               COALESCE(p.create_date, now() at time zone 'UTC'),
               CASE WHEN COALESCE(p.shrimp_account_state, 'approved') <> 'pending'
                    THEN now() at time zone 'UTC' END,
               1, now() at time zone 'UTC', 1, now() at time zone 'UTC'
          FROM res_partner p
         WHERE p.shrimp_user_type IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM shrimp_partner_role r
                            WHERE r.partner_id = p.id AND r.role = p.shrimp_user_type)
    """)
    creadas = cr.rowcount
    if "shrimp_role_count" in columnas(cr, "res_partner"):
        cr.execute("""
            UPDATE res_partner p
               SET shrimp_role_count = sub.n
              FROM (SELECT partner_id, count(*) AS n FROM shrimp_partner_role
                     WHERE state = 'approved' GROUP BY partner_id) sub
             WHERE sub.partner_id = p.id
               AND COALESCE(p.shrimp_role_count, 0) <> sub.n
        """)
    return creadas
