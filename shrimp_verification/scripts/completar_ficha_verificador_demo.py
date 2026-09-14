# -*- coding: utf-8 -*-
"""Completa la ficha del verificador de demostración (partner 59).

POR QUÉ: cuando el comprador elige verificador, el "ojito" de cada tarjeta
abre una ficha (sv-info-modal, en views/portal_templates.xml) que muestra
responsable técnico, registro, base de operaciones, zonas que cubre y
teléfono. En "Inspecciones Acuícolas del Pacífico" —el verificador que usan
casi todas las demos, con 13 verificaciones y 2 técnicos de campo— todos esos
campos están en NULL, así que la ficha sale vacía justo en la pantalla donde
el comprador decide a quién le confía la inspección.

ESTO ES DATO DE DEMOSTRACIÓN. La empresa no existe: el nombre, el responsable
técnico, los teléfonos, los equipos y la cobertura son inventados y solo
sirven para que la pantalla se vea completa. Lo que NO se inventa:

  * El número de registro y la vigencia se COPIAN de la acreditación que el
    partner ya tiene en la base (shrimp.user.certificate.line), no se escriben
    a mano: así la ficha y el certificado que abre el comprador dicen lo mismo.
  * El honorario se deja igual que la tarifa de la plataforma
    (ir.config_parameter shrimp_verification.fee_base / fee_cents), para que el
    perfil no contradiga al importe que se le cobra al comprador.
  * NO se rellenan RUC, razón fiscal, dirección fiscal ni cuenta bancaria. Un
    RUC de 13 dígitos inventado puede coincidir con el de una empresa real, y
    ninguno de esos campos aparece en la pantalla del comprador. Si hacen falta
    para la demo, que los ponga una persona.

Por qué no va en data/ ni en demo/: toca un partner concreto de ESTA base,
creado por la demo con un id que no es estable entre instalaciones.

Uso:

    cd /home/ccarballo/odoo19
    ./venv/bin/python src/odoo-bin shell -c odoo.conf -d odoo19 \
        < custom_addons/Camaronera/shrimp_verification/scripts/completar_ficha_verificador_demo.py

Es idempotente y NO pisa nada: solo escribe en los campos que estén vacíos.
Si alguien ya puso un dato a mano, se respeta y se avisa por consola.
"""

# El partner se busca por el correo de la cuenta, no por el id 59: el id lo
# asignó la demo y cambia si la base se regenera.
CORREO = "verificador.demo@camaronera.test"

NOMBRE_SIN_TILDES = "Inspecciones Acuicolas del Pacifico"
NOMBRE_CON_TILDES = "Inspecciones Acuícolas del Pacífico"

FICHA = {
    "ver_razon_social": "Inspecciones Acuícolas del Pacífico S.A.",
    "ver_representante": "Blgo. Marco Zambrano Loor",
    "ver_ubicacion": "Guayaquil, Guayas",
    "ver_cobertura": "Guayas, El Oro, Santa Elena y Los Ríos",
    "ver_provincias": "Guayas, El Oro, Santa Elena, Los Ríos",
    "ver_telefono": "042601180",
    "ver_whatsapp": "0993741208",
    "ver_email_avisos": CORREO,
    "ver_horario": "Lunes a sábado, 07:00 a 18:00",
    "ver_tiempo_respuesta": "24 a 48 horas",
    "ver_radio_km": 150,
    "ver_capacidad_lotes_dia": 6,
    "ver_equipo_propio": True,
    # Los cinco análisis son los que el propio flujo de verificación registra
    # en campo; no se inventa un catálogo distinto al que hace el sistema.
    "ver_analisis_tipos": (
        "Peso y conteo de piezas por libra.\n"
        "Rendimiento cuerpo/cola.\n"
        "Residual de metabisulfito.\n"
        "Clasificación por talla comercial.\n"
        "Prueba de sabor y olor."
    ),
    "ver_equipos": (
        "Balanza digital de precisión (0,1 g).\n"
        "Juego de tamices de clasificación por talla.\n"
        "Kit colorimétrico para metabisulfito.\n"
        "Termómetro de punción y cámara térmica portátil."
    ),
    # El SAE (Servicio de Acreditación Ecuatoriano) sí existe y es el organismo
    # nacional de acreditación; no se inventa el número, que se toma abajo de
    # la acreditación real del partner.
    "ver_entidad_acredita": "Servicio de Acreditación Ecuatoriano (SAE)",
}


P = env["res.partner"].sudo()
Cfg = env["ir.config_parameter"].sudo()

empresa = P.search([("email", "=", CORREO),
                    ("shrimp_user_type", "=", "verificador")], limit=1)
if not empresa:
    raise SystemExit(
        "No se encontró el verificador de demo (%s). Si la base se regeneró "
        "con otros correos, ajusta CORREO en la cabecera de este script."
        % CORREO)

valores = dict(FICHA)

# --- Registro y vigencia: se copian de la acreditación que ya tiene ---------
# Si la ficha dijera un número y el certificado que abre el comprador dijera
# otro, la pantalla se desmiente sola.
acred = empresa.verifier_accreditation_line_id
if acred:
    if acred.certificate_number:
        valores["ver_registro_num"] = acred.certificate_number
    if acred.expiry_date:
        valores["ver_acred_vigencia"] = acred.expiry_date
else:
    print("AVISO: el partner no tiene acreditación aprobada y vigente; se deja "
          "el número de registro vacío en vez de inventarlo.")

# --- Honorario: el mismo que cobra la plataforma ---------------------------
try:
    valores["ver_fee_base"] = float(Cfg.get_param("shrimp_verification.fee_base") or 0)
    # fee_cents son centavos por libra; el campo del partner está en dólares.
    valores["ver_fee_por_lb"] = float(Cfg.get_param("shrimp_verification.fee_cents") or 0) / 100.0
except (TypeError, ValueError):
    print("AVISO: la tarifa de la plataforma no es numérica; no se toca el honorario.")

# --- Escritura: solo lo que esté vacío -------------------------------------
a_escribir, respetados = {}, []
for campo, valor in valores.items():
    if campo not in empresa._fields:
        continue
    actual = empresa[campo]
    if actual:
        if actual != valor:
            respetados.append((campo, actual))
        continue
    a_escribir[campo] = valor

# El nombre que se ve en el selector de compra va sin tildes ("Acuicolas del
# Pacifico"). Se corrige solo si sigue siendo exactamente el de la demo, para
# no pisar un nombre que alguien haya cambiado a propósito.
if empresa.name == NOMBRE_SIN_TILDES:
    a_escribir["name"] = NOMBRE_CON_TILDES

# La ciudad ya dice Guayaquil; solo se rellena si falta, porque la ficha cae a
# ver_ubicacion or city.
if not empresa.city:
    a_escribir["city"] = "Guayaquil"

if a_escribir:
    empresa.write(a_escribir)
    env.cr.commit()

print("Ficha de [%s] %s" % (empresa.id, empresa.name))
if a_escribir:
    print("  rellenados %s campos:" % len(a_escribir))
    for campo in sorted(a_escribir):
        print("    %-24s %s" % (campo, a_escribir[campo]))
else:
    print("  nada que hacer: la ficha ya estaba completa.")
if respetados:
    print("  se respetaron %s campos que ya tenían dato propio:" % len(respetados))
    for campo, actual in sorted(respetados):
        print("    %-24s %s" % (campo, actual))
