"""Configura el servidor de correo saliente de Trazul.

Sin esto el sistema calcula y registra todo bien, pero ningún aviso sale del
servidor: los correos se quedan en cola con estado "excepción" y nadie se
entera de nada.

Uso:
    cd /home/ccarballo/odoo19
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d odoo19 --no-http \
        < custom_addons/Camaronera/shrimp_verification/scripts/configurar_correo.py

Antes de ejecutarlo, edita los valores de CONFIG de abajo.

Para Gmail / Google Workspace hay que generar una "contraseña de aplicación"
(https://myaccount.google.com/apppasswords); la clave normal de la cuenta no
funciona con SMTP desde 2022.
"""

CONFIG = {
    # --- Servidor SMTP ---
    "nombre": "Correo saliente Trazul",
    "host": "smtp.gmail.com",       # Gmail: smtp.gmail.com | Outlook: smtp.office365.com
    "puerto": 587,
    "cifrado": "starttls",          # "starttls" (587) | "ssl" (465) | "none"
    "usuario": "avisos@trazul.ec",  # cuenta desde la que salen los correos
    "clave": "PON-AQUI-LA-CLAVE",   # contraseña de aplicación, no la del correo

    # --- Identidad del remitente ---
    # El dominio debe ser uno que controles: si mandas "desde" un dominio ajeno,
    # Gmail y Outlook mandan tus avisos directo a spam.
    "dominio": "trazul.ec",
    "remitente": "avisos",          # resultado: avisos@trazul.ec

    # --- URL pública ---
    # Los botones de los correos se arman con esto. Si queda en localhost,
    # los enlaces no le sirven a nadie fuera de este servidor.
    "url_publica": "http://localhost:8069",
}


def configurar(env, cfg):
    servidor = env["ir.mail_server"].sudo()
    reg = servidor.search([("name", "=", cfg["nombre"])], limit=1)
    vals = {
        "name": cfg["nombre"],
        "smtp_host": cfg["host"],
        "smtp_port": cfg["puerto"],
        "smtp_encryption": cfg["cifrado"],
        "smtp_user": cfg["usuario"],
        "smtp_pass": cfg["clave"],
        "sequence": 10,
    }
    reg.write(vals) if reg else servidor.create(vals)
    reg = reg or servidor.search([("name", "=", cfg["nombre"])], limit=1)

    par = env["ir.config_parameter"].sudo()
    par.set_param("mail.default.from", cfg["remitente"])
    par.set_param("mail.catchall.domain", cfg["dominio"])
    par.set_param("web.base.url", cfg["url_publica"])
    # Si no se congela, Odoo reescribe la URL base con la del primer login y
    # los enlaces de los correos terminan apuntando a donde no deben.
    par.set_param("web.base.url.freeze", "True")

    empresa = env.company
    if not empresa.email:
        empresa.email = "%s@%s" % (cfg["remitente"], cfg["dominio"])

    print("Servidor configurado:", reg.name, "->", reg.smtp_host, reg.smtp_port)
    print("Remitente:", par.get_param("mail.default.from"), "@",
          par.get_param("mail.catchall.domain"))
    print("URL de los enlaces:", par.get_param("web.base.url"))
    try:
        reg.test_smtp_connection()
        print("Conexión SMTP: OK")
    except Exception as e:
        print("Conexión SMTP FALLÓ:", str(e)[:200])
    return reg


configurar(env, CONFIG)   # noqa: F821  (env lo inyecta el shell de Odoo)
env.cr.commit()           # noqa: F821
