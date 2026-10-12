"""Crea el usuario interno del área contable de CamaronMarket.

Uso (con odoo-bin shell; no se ejecuta solo):

    cd /home/ccarballo/odoo19
    venv/bin/python src/odoo-bin shell -c odoo.conf -d <BASE> --no-http \\
        < custom_addons/Camaronera/shrimp/shrimp_contabilidad/scripts/crear_usuario_contable.py

Qué hace (idempotente):
* Si no existe, crea el usuario interno «Contabilidad CamaronMarket»
  (login contabilidad@camaronmarket.com) con el grupo «Contador general».
  Si ya existe, solo le asegura ese grupo.
* NO le pone contraseña y NO envía invitación por correo: la contraseña la
  pone el responsable con un comando aparte (se imprime al final).
* Comprueba que NO quede como administrador del sistema ni de la plataforma.
"""
LOGIN = "contabilidad@camaronmarket.com"
NOMBRE = "Contabilidad CamaronMarket"

# `env` lo inyecta odoo-bin shell.
env = env  # noqa: F821,PLW0127

Users = env["res.users"].with_context(active_test=False, no_reset_password=True)
contador = env.ref("shrimp_contabilidad.group_contador_general")
interno = env.ref("base.group_user")

companias = env["res.company"].search([]).filtered(lambda c: c.chart_template == "ec") \
    or env.company
principal = companias[:1]

usuario = Users.search([("login", "=", LOGIN)], limit=1)
if usuario:
    usuario.write({"group_ids": [(4, contador.id)], "active": True})
    accion = "ya existía: se le aseguró el grupo «Contador general»"
else:
    usuario = Users.create({
        "name": NOMBRE,
        "login": LOGIN,
        "email": LOGIN,
        "company_id": principal.id,
        "company_ids": [(6, 0, companias.ids)],
        # Lista explícita: no hereda los grupos por defecto de usuario nuevo.
        "group_ids": [(6, 0, [interno.id, contador.id])],
    })
    accion = "creado"

prohibidos = ["base.group_system", "base.group_erp_manager",
              "shrimp_marketplace.group_shrimp_manager"]
problemas = [x for x in prohibidos if usuario.has_group(x)]
if problemas:
    env.cr.rollback()
    raise SystemExit("ABORTADO: el usuario quedaría con %s. No se guardó nada." % problemas)

env.cr.commit()

print("=" * 72)
print("Usuario %s (%s): %s." % (usuario.name, usuario.login, accion))
print("Compañías: %s" % ", ".join(usuario.company_ids.mapped("name")))
print("Grupo: Contabilidad CamaronMarket / Contador general. "
      "Administrador del sistema: NO.")
print("-" * 72)
print("El usuario NO tiene contraseña y no puede entrar todavía. El responsable")
print("debe ponerla con un comando aparte, por ejemplo:")
print()
print("  read -rs CLAVE && export CLAVE   # se teclea sin que se vea")
print("  venv/bin/python src/odoo-bin shell -c odoo.conf -d <BASE> --no-http <<'PY'")
print("  import os")
print("  u = env['res.users'].search([('login', '=', '%s')])" % LOGIN)
print("  u.password = os.environ['CLAVE']")
print("  env.cr.commit()")
print("  PY")
print("  unset CLAVE")
print()
print("o desde Ajustes > Usuarios > %s > Acción > Cambiar contraseña." % NOMBRE)
print("=" * 72)
