"""19.0.1.13.0: permisos de los subusuarios y perfil activo POR USUARIO
(hueco CR-12, decisiones D-12 y D-37).

* Cada usuario de una cuenta (miembros y dueños de cuentas sin miembros)
  recibe como perfil activo propio (res_users.shrimp_active_profile) el que
  tenía su empresa (res_partner.shrimp_user_type): nadie cambia de perfil
  al actualizar.
* Columna nueva shrimp_account_member.can_manage_account: queda en False
  para los subusuarios existentes (desde ahora editar la ficha de la
  empresa, los certificados, las claves API y los webhooks exige
  «Gestionar cuenta»; el administrador siempre puede).
* La regla de shrimp_copacking «bandeja del maquilador» está en noupdate:
  se le cambia aquí el perfil de la empresa por el del usuario.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    # Miembros (administradores, subusuarios, técnicos): el perfil por defecto
    # de su empresa.
    cr.execute("""
        UPDATE res_users u
           SET shrimp_active_profile = p.shrimp_user_type
          FROM shrimp_account_member m
          JOIN res_partner p ON p.id = m.company_id
         WHERE m.user_id = u.id
           AND u.shrimp_active_profile IS NULL
           AND p.shrimp_user_type IS NOT NULL
    """)
    miembros = cr.rowcount
    # Dueños de cuentas anteriores a los miembros (su propio socio).
    cr.execute("""
        UPDATE res_users u
           SET shrimp_active_profile = p.shrimp_user_type
          FROM res_partner p
         WHERE p.id = u.partner_id
           AND u.share
           AND u.shrimp_active_profile IS NULL
           AND p.shrimp_user_type IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM shrimp_account_member m WHERE m.user_id = u.id)
    """)
    duenos = cr.rowcount
    _logger.warning("shrimp_user_registry 19.0.1.13.0: perfil activo copiado a %s miembros y "
                    "%s dueños de cuenta", miembros, duenos)

    # Por SQL: shrimp_copacking todavía no está cargado en el registro (el
    # ORM no podría validar el dominio de un modelo que aún no conoce).
    viejo = "user.shrimp_actor_partner_id.shrimp_user_type == 'maquilador'"
    cr.execute("""
        UPDATE ir_rule r
           SET domain_force = replace(r.domain_force, %s, %s)
          FROM ir_model_data d
         WHERE d.model = 'ir.rule' AND d.res_id = r.id
           AND d.module = 'shrimp_copacking' AND d.name = 'rule_copack_request_bandeja'
           AND r.domain_force LIKE %s
    """, (viejo, "user.shrimp_active_role == 'maquilador'", "%" + viejo + "%"))
    if cr.rowcount:
        _logger.warning("shrimp_user_registry 19.0.1.13.0: regla de la bandeja del maquilador "
                        "pasa al perfil del usuario")
