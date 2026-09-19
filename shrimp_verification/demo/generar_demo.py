"""Genera los datos demo de shrimp_verification.

Determinista (random con semilla fija) para que dos ejecuciones den el mismo
resultado y el diff de git sea estable.
"""
import random
from lxml import etree
from pathlib import Path

random.seed(20260919)

BASE = Path("/home/ccristhian/odoo19/custom_addons/Camaronera")
DEMO = BASE / "shrimp_verification" / "demo"
DEMO.mkdir(exist_ok=True)
MP = "shrimp_marketplace."

# ---------------------------------------------------------------- transacciones
tx = {}
tree = etree.parse(str(BASE / "shrimp_marketplace/demo/demo_10_transactions.xml"))
for rec in tree.iter("record"):
    d = {}
    for f in rec.findall("field"):
        d[f.get("name")] = f.get("ref") or (f.text or "").strip()
    tx[rec.get("id")] = d

ADULT = [f"demo_tx_{i}" for i in range(61, 81)]
LARVAE = [f"demo_tx_{i}" for i in range(1, 61)]

# ---------------------------------------------------------------- empresas
EMPRESAS = [
    ("pacifico", "Inspecciones Acuícolas del Pacífico S.A.", "990002192001",
     "Blgo. Marco Zambrano Loor", "Guayaquil", "Guayas", "+593 99 412 7788",
     "Guayaquil, Guayas", "Guayas, Los Ríos, El Oro", 180, "Banco Pichincha",
     "corriente", "2100548837", "24 horas", 12),
    ("certimar", "Certimar Ecuador Cía. Ltda.", "990002329001",
     "Ing. Lucía Bermeo Andrade", "Machala", "El Oro", "+593 98 233 4109",
     "Machala, El Oro", "El Oro, Guayas", 140, "Banco del Pacífico",
     "ahorros", "1044729903", "12 horas", 9),
    ("aquacontrol", "AquaControl Verificadores S.A.", "990002466001",
     "Ing. Rubén Castillo Mera", "Durán", "Guayas", "+593 96 770 5521",
     "Durán, Guayas", "Guayas, Santa Elena", 200, "Produbanco",
     "corriente", "3300911274", "8 horas", 16),
    ("verimar", "Grupo Verimar del Litoral", "990002603001",
     "Blga. Daniela Cedeño Vera", "Manta", "Manabí", "+593 99 018 6634",
     "Manta, Manabí", "Manabí, Esmeraldas", 220, "Banco Bolivariano",
     "ahorros", "5012384470", "24 horas", 8),
    ("guayas", "Inspectorate Camaronero del Guayas", "990002740001",
     "Ing. Fabián Ordóñez Tapia", "Naranjal", "Guayas", "+593 97 559 2280",
     "Naranjal, Guayas", "Guayas, Cañar", 120, "Banco Internacional",
     "corriente", "7700432165", "36 horas", 7),
    ("bioaudit", "BioAudit Acuícola Santa Elena", "990002877001",
     "Blgo. Andrés Figueroa Lindao", "La Libertad", "Santa Elena", "+593 98 641 3392",
     "La Libertad, Santa Elena", "Santa Elena, Guayas", 160, "Banco Guayaquil",
     "ahorros", "0029947716", "18 horas", 10),
]

NOMBRES = [
    "Jorge Villamar Solís", "María Fernanda Quintero", "Luis Alberto Mendoza",
    "Karina Salazar Bravo", "Diego Armando Palacios", "Silvia Moreira Cando",
    "Byron Espinoza Vera", "Gabriela Intriago Loor", "Christian Yagual Pozo",
    "Verónica Delgado Mite", "Freddy Sánchez Baque", "Paola Cevallos Ronquillo",
    "Wilson Arreaga Chávez", "Andrea Zambrano Pincay", "Marco Tulio Reyes",
    "Jessica Anchundia Vélez", "Rómulo Bajaña Torres", "Cinthya Parrales Soto",
    "Édison Cabrera Muñoz", "Tatiana Holguín Rizzo", "Nelson Farías Aguirre",
    "Michelle Vinueza Coello", "Óscar Peñafiel Jaramillo", "Doris Alvarado León",
    "Iván Macías Zamora", "Lorena Bustamante Gil", "Héctor Suárez Plaza",
    "Mónica Carvajal Ortiz", "Álvaro Benítez Tomalá", "Rocío Guerrero Lucas",
]
ROLES = ["tech_role_campo", "tech_role_supervisor", "tech_role_inspector",
         "tech_role_lab", "tech_role_coord"]

SIZES_A = ["U15", "16/20", "21/25"]
SIZES_B = ["26/30", "31/35"]
SIZES_C = ["36/40", "41/50"]


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def head(comment):
    return f'<?xml version="1.0" encoding="UTF-8" ?>\n<odoo>\n  <!-- {comment} -->\n  <data noupdate="1">\n'


TAIL = "  </data>\n</odoo>\n"

# ================================================================ 01 empresas
out = [head("Empresas verificadoras: la cuenta principal de cada una.")]
for (slug, nombre, ruc, resp, city, prov, tel, ubic, cobertura, radio,
     banco, tipo_cta, cta, resp_time, cap) in EMPRESAS:
    out.append(f"""    <record id="demo_ver_{slug}" model="res.partner">
      <field name="name">{esc(nombre)}</field>
      <field name="is_company" eval="True"/>
      <field name="shrimp_user_type">verificador</field>
      <field name="vat_or_id">{ruc}</field>
      <field name="email">verificaciones@{slug}.ec</field>
      <field name="phone">{tel}</field>
      <field name="city">{esc(city)}</field>
      <field name="street">Km {random.randint(2, 14)} vía a {esc(city)}</field>
      <field name="ver_razon_social">{esc(nombre)}</field>
      <field name="ver_representante">{esc(resp)}</field>
      <field name="ver_telefono">{tel}</field>
      <field name="ver_ubicacion">{esc(ubic)}</field>
      <field name="ver_cobertura">{esc(cobertura)}</field>
      <field name="ver_provincias">{esc(cobertura)}</field>
      <field name="ver_radio_km">{radio}</field>
      <field name="ver_registro_num">SAE-VER-{random.randint(1000, 9999)}</field>
      <field name="ver_entidad_acredita">Servicio de Acreditación Ecuatoriano (SAE)</field>
      <field name="ver_acred_vigencia">2027-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}</field>
      <field name="ver_bank_name">{esc(banco)}</field>
      <field name="ver_bank_account_type">{tipo_cta}</field>
      <field name="ver_bank_account_number">{cta}</field>
      <field name="ver_bank_holder">{esc(nombre)}</field>
      <field name="ver_bank_holder_id">{ruc}</field>
      <field name="ver_email_avisos">avisos@{slug}.ec</field>
      <field name="ver_whatsapp">{tel}</field>
      <field name="ver_horario">Lunes a sábado, 06:00 a 20:00</field>
      <field name="ver_tiempo_respuesta">{resp_time}</field>
      <field name="ver_equipo_propio" eval="True"/>
      <field name="ver_capacidad_lotes_dia">{cap}</field>
      <field name="ver_analisis_tipos">Peso y merma, presentación (cuerpo/cola), metabisulfito, clasificación por talla, sabor</field>
      <field name="ver_equipos">Balanza de precisión 0,1 g, kit colorimétrico de metabisulfito, calibrador de tallas, cámara térmica, GPS</field>
      <field name="ver_ruc">{ruc}</field>
      <field name="ver_razon_fiscal">{esc(nombre)}</field>
      <field name="ver_dir_fiscal">{esc(ubic)}</field>
      <field name="ver_fee_base">{random.choice([220, 250, 280, 300])}.0</field>
      <field name="ver_fee_por_lb">{random.choice([1.5, 2.0, 2.5, 3.0])}</field>
    </record>
""")
out.append(TAIL)
(DEMO / "demo_01_verifier_partners.xml").write_text("".join(out), encoding="utf-8")

# ================================================================ 02 técnicos
tecnicos = {}
out = [head("Subcuentas: los técnicos de campo de cada empresa verificadora.\n"
            "       Un técnico es un contacto hijo con shrimp_is_field_tech,\n"
            "       sin shrimp_user_type propio.")]
idx = 0
for slug, nombre, *_ in EMPRESAS:
    tecnicos[slug] = []
    for n in range(1, random.randint(4, 6) + 1):
        persona = NOMBRES[idx % len(NOMBRES)]
        idx += 1
        tid = f"demo_vtec_{slug}_{n}"
        tecnicos[slug].append(tid)
        rol = ROLES[0] if n <= 2 else ROLES[(n - 1) % len(ROLES)]
        out.append(f"""    <record id="{tid}" model="res.partner">
      <field name="name">{esc(persona)}</field>
      <field name="parent_id" ref="demo_ver_{slug}"/>
      <field name="function">{esc(persona.split()[0])} · técnico de campo</field>
      <field name="shrimp_is_field_tech" eval="True"/>
      <field name="tech_role_id" ref="{rol}"/>
      <field name="email">{persona.split()[0].lower()}.{persona.split()[-1].lower()}@{slug}.ec</field>
      <field name="phone">+593 9{random.randint(10, 99)} {random.randint(100, 999)} {random.randint(1000, 9999)}</field>
    </record>
""")
out.append(TAIL)
(DEMO / "demo_02_verifier_technicians.xml").write_text("".join(out), encoding="utf-8")

# ================================================================ 03 acreditaciones
out = [head("Acreditaciones: sin una línea aprobada y vigente la empresa no\n"
            "       aparece como verificador elegible (verifier_is_accredited).")]
for i, (slug, nombre, *_rest) in enumerate(EMPRESAS):
    doc = f"{MP}demo_cert_doc_{(i % 3) + 1}"
    out.append(f"""    <record id="demo_vacred_{slug}" model="shrimp.user.certificate.line">
      <field name="partner_id" ref="demo_ver_{slug}"/>
      <field name="certificate_id" ref="cert_acreditacion_verificador"/>
      <field name="certificate_number">ACRED-VER-{2024 + (i % 2)}-{100 + i}</field>
      <field name="issue_date">{2024 + (i % 2)}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}</field>
      <field name="expiry_date">2027-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}</field>
      <field name="file_attachment_id" ref="{doc}"/>
      <field name="status">approved</field>
    </record>
""")
    # la mitad suma la habilitación de laboratorio
    if i % 2 == 0:
        out.append(f"""    <record id="demo_vacred_lab_{slug}" model="shrimp.user.certificate.line">
      <field name="partner_id" ref="demo_ver_{slug}"/>
      <field name="certificate_id" ref="cert_habilitacion_laboratorio_analisis"/>
      <field name="certificate_number">HAB-LAB-{2025}-{200 + i}</field>
      <field name="issue_date">2025-{random.randint(1, 10):02d}-{random.randint(1, 28):02d}</field>
      <field name="expiry_date">2026-12-{random.randint(10, 28)}</field>
      <field name="file_attachment_id" ref="{MP}demo_cert_doc_{((i + 1) % 3) + 1}"/>
      <field name="status">approved</field>
    </record>
""")
out.append(TAIL)
(DEMO / "demo_03_verifier_accreditations.xml").write_text("".join(out), encoding="utf-8")

# ================================================================ 04 verificaciones
# Reparto de estados: realista, con cola larga de casos cerrados.
def estado_para(i, total):
    r = i / total
    if r < 0.60:
        return "approved"
    if r < 0.72:
        return "approved_obs"
    if r < 0.80:
        return "rejected"
    if r < 0.87:
        return "done"
    if r < 0.93:
        return "in_field"
    if r < 0.98:
        return "assigned"
    return "received"


FINALES = {"approved", "approved_obs", "rejected", "cancelled"}
verifs = []          # (xmlid, tx, estado, scope, empresa_slug, buyer)

out = [head("Verificaciones de camarón adulto: los cinco análisis completos\n"
            "       (peso, presentación, metabisulfito, clasificación y sabor),\n"
            "       con sus líneas por talla y los conteos de campo.")]
for i, txid in enumerate(ADULT):
    slug = EMPRESAS[i % len(EMPRESAS)][0]
    est = estado_para(i, len(ADULT))
    d = tx[txid]
    vid = f"demo_verif_a_{i + 1}"
    verifs.append((vid, txid, est, "adult", slug, d["buyer_partner_id"]))
    tec = random.choice(tecnicos[slug])

    enviado = round(random.uniform(800, 4200), 2)
    merma = round(enviado * random.uniform(0.02, 0.09), 2)
    planta = round(enviado * random.uniform(0.94, 1.03), 2)
    merma = min(merma, round(planta * 0.5, 2))
    neto = planta - merma
    limite = 100.0
    ppm = round(random.uniform(38, 96), 2) if est != "rejected" else round(random.uniform(104, 150), 2)
    pres = random.choice(["entero", "cola"])
    sabor = ("rejected" if est == "rejected"
             else random.choice(["excellent", "good", "good", "acceptable"]))
    cosecha = f"2026-0{random.randint(4, 8)}-{random.randint(1, 28):02d}"
    proceso = f"2026-0{random.randint(4, 9)}-{random.randint(1, 28):02d}"
    fee = round(random.uniform(300, 780), 2)
    gramaje = round(random.uniform(1.6, 3.4), 2)

    # Líneas por talla. El reparto entre clases cambia en cada lote y lo
    # clasificado no cuadra al 100% con el neto: en campo siempre se pierde
    # algo entre el pesaje y la mesa de clasificación, así que el rendimiento
    # cae a la banda 92-99% en vez de salir clavado a 100.
    fa = random.uniform(0.44, 0.72)
    fb = random.uniform(0.18, min(0.36, 0.95 - fa))
    fc = max(0.03, 1.0 - fa - fb)
    clasificado = neto * random.uniform(0.92, 0.995)
    reparto = []
    for cls, sizes, frac in (("a", SIZES_A, fa), ("b", SIZES_B, fb), ("c", SIZES_C, fc)):
        cuota = clasificado * frac
        elegidas = random.sample(sizes, random.randint(1, len(sizes)))
        pesos = [random.uniform(0.6, 1.4) for _ in elegidas]
        suma = sum(pesos)
        for j, sz in enumerate(elegidas):
            w = round(cuota * pesos[j] / suma, 2)
            if w > 0:
                reparto.append((cls, sz, w, (j + 1) * 10))
    lineas = "".join(
        f"""        <record id="{vid}_l{k}" model="shrimp.verification.line">
          <field name="verification_id" ref="{vid}"/>
          <field name="quality_class">{cls}</field>
          <field name="size_code">{sz}</field>
          <field name="weight_lb">{w}</field>
          <field name="sequence">{seq}</field>
        </record>
""" for k, (cls, sz, w, seq) in enumerate(reparto, 1))

    conteos = "".join(
        f"""        <record id="{vid}_c{k}" model="shrimp.verification.count">
          <field name="verification_id" ref="{vid}"/>
          <field name="value">{round(random.uniform(neto * 0.95, neto * 1.05), 2)}</field>
          <field name="note">Conteo {k} en muelle</field>
          <field name="sequence">{k * 10}</field>
        </record>
""" for k in range(1, random.randint(2, 4) + 1))

    crit = "" if est == "rejected" else (
        '      <field name="taste_criteria_ok_ids" '
        'eval="[(6, 0, [ref(\'taste_crit_olor\'), ref(\'taste_crit_color\')])]"/>\n')

    fechas = f"""      <field name="assigned_date">2026-0{random.randint(4, 8)}-{random.randint(1, 28):02d} {random.randint(7, 11):02d}:{random.randint(0, 59):02d}:00</field>
"""
    if est not in ("received", "assigned"):
        fechas += f"""      <field name="field_start_date">2026-0{random.randint(5, 8)}-{random.randint(1, 28):02d} {random.randint(6, 10):02d}:{random.randint(0, 59):02d}:00</field>
"""
    if est in FINALES:
        fechas += f"""      <field name="verified_date">2026-0{random.randint(6, 9)}-{random.randint(1, 28):02d} {random.randint(11, 18):02d}:{random.randint(0, 59):02d}:00</field>
"""

    acc_state = "closed" if est in ("approved", "approved_obs") else "na"
    veredicto = {
        "approved": "Lote conforme. Peso, presentación y sabor dentro de lo pactado.",
        "approved_obs": "Se aprueba con observación: la merma supera lo habitual, se recomienda revisar el manejo en cosecha.",
        "rejected": "Se rechaza el lote: el metabisulfito excede el límite y el sabor no es apto.",
    }.get(est, "")

    out.append(f"""    <record id="{vid}" model="shrimp.verification">
      <field name="transaction_id" ref="{MP}{txid}"/>
      <field name="verifier_partner_id" ref="demo_ver_{slug}"/>
      <field name="technician_partner_id" ref="{tec}"/>
      <field name="state">{est}</field>
      <field name="batch_code">LT-2026-{1000 + i}</field>
      <field name="pond_label">Piscina {random.randint(1, 18)}</field>
      <field name="plant_name">{random.choice(['Total Seafood', 'Empacadora Santa Priscila', 'Omarsa', 'Songa', 'Expalsa'])}</field>
      <field name="harvest_date">{cosecha}</field>
      <field name="process_date">{proceso}</field>
      <field name="weight_sent_lb">{enviado}</field>
      <field name="weight_plant_lb">{planta}</field>
      <field name="trash_lb">{merma}</field>
      <field name="presentation">{pres}</field>
      <field name="metabisulfite_ppm">{ppm}</field>
      <field name="metabisulfite_limit_ppm">{limite}</field>
      <field name="metabisulfite_notes">Medición con kit colorimétrico, tres tomas sobre el mismo lote.</field>
      <field name="taste_result">{sabor}</field>
      <field name="taste_notes">{'Sabor característico, sin notas extrañas.' if sabor != 'rejected' else 'Se detecta sabor a fango; no apto.'}</field>
{crit}      <field name="grams_farm">{gramaje}</field>
      <field name="grams_plant_1">{round(gramaje + random.uniform(-0.15, 0.15), 2)}</field>
      <field name="grams_plant_2">{round(gramaje + random.uniform(-0.15, 0.15), 2)}</field>
      <field name="gps_latitude">{round(random.uniform(-3.3, -0.9), 6)}</field>
      <field name="gps_longitude">{round(random.uniform(-80.4, -79.3), 6)}</field>
      <field name="incident_notes">{random.choice(['Sin novedades.', 'Retraso de 40 minutos por lluvia.', 'Acceso a piscina en mal estado.', 'Sin novedades.'])}</field>
      <field name="verdict_notes">{esc(veredicto)}</field>
      <field name="fee">{fee}</field>
      <field name="margin_pct">15.0</field>
      <field name="acceptance_state">{acc_state}</field>
      <field name="buyer_notified" eval="{est in FINALES}"/>
{fechas}    </record>
{lineas}{conteos}""")
out.append(TAIL)
(DEMO / "demo_04_verifications_adult.xml").write_text("".join(out), encoding="utf-8")

# ================================================================ 05 larva
out = [head("Verificaciones de larva: la ruta alternativa del módulo, con\n"
            "       cantidad, supervivencia, talla media y estado sanitario.")]
for i, txid in enumerate(LARVAE):
    slug = EMPRESAS[i % len(EMPRESAS)][0]
    est = estado_para(i, len(LARVAE))
    d = tx[txid]
    vid = f"demo_verif_l_{i + 1}"
    verifs.append((vid, txid, est, "larvae", slug, d["buyer_partner_id"]))
    tec = random.choice(tecnicos[slug])

    salud = ("rejected" if est == "rejected"
             else random.choice(["excellent", "good", "good", "acceptable"]))
    surv = round(random.uniform(62, 94), 2) if est != "rejected" else round(random.uniform(31, 55), 2)
    qty = round(random.uniform(200000, 2400000), 2)
    fee = round(random.uniform(300, 620), 2)

    fechas = f"""      <field name="assigned_date">2026-0{random.randint(3, 8)}-{random.randint(1, 28):02d} {random.randint(7, 11):02d}:{random.randint(0, 59):02d}:00</field>
"""
    if est not in ("received", "assigned"):
        fechas += f"""      <field name="field_start_date">2026-0{random.randint(4, 8)}-{random.randint(1, 28):02d} {random.randint(6, 10):02d}:{random.randint(0, 59):02d}:00</field>
"""
    if est in FINALES:
        fechas += f"""      <field name="verified_date">2026-0{random.randint(5, 9)}-{random.randint(1, 28):02d} {random.randint(11, 18):02d}:{random.randint(0, 59):02d}:00</field>
"""

    veredicto = {
        "approved": "Siembra conforme: supervivencia y talla dentro de lo ofertado.",
        "approved_obs": "Conforme con observación: la talla media queda por debajo de lo ofertado.",
        "rejected": "Se rechaza: la supervivencia está muy por debajo de lo declarado.",
    }.get(est, "")

    out.append(f"""    <record id="{vid}" model="shrimp.verification">
      <field name="transaction_id" ref="{MP}{txid}"/>
      <field name="verifier_partner_id" ref="demo_ver_{slug}"/>
      <field name="technician_partner_id" ref="{tec}"/>
      <field name="state">{est}</field>
      <field name="batch_code">LV-2026-{2000 + i}</field>
      <field name="larvae_qty_verified">{qty}</field>
      <field name="larvae_survival_rate">{surv}</field>
      <field name="larvae_avg_size_mg">{round(random.uniform(1.2, 9.5), 3)}</field>
      <field name="larvae_health_status">{salud}</field>
      <field name="larvae_health_notes">{'Nauplios activos, sin necrosis visible.' if salud != 'rejected' else 'Alta mortalidad y presencia de necrosis.'}</field>
      <field name="gps_latitude">{round(random.uniform(-3.3, -0.9), 6)}</field>
      <field name="gps_longitude">{round(random.uniform(-80.4, -79.3), 6)}</field>
      <field name="incident_notes">{random.choice(['Sin novedades.', 'Muestreo repetido por discrepancia en el primer conteo.', 'Sin novedades.'])}</field>
      <field name="verdict_notes">{esc(veredicto)}</field>
      <field name="fee">{fee}</field>
      <field name="margin_pct">15.0</field>
      <field name="acceptance_state">{'closed' if est in ('approved', 'approved_obs') else 'na'}</field>
      <field name="buyer_notified" eval="{est in FINALES}"/>
{fechas}    </record>
""")
out.append(TAIL)
(DEMO / "demo_05_verifications_larvae.xml").write_text("".join(out), encoding="utf-8")

# ================================================================ 06 aceptaciones
out = [head("Rondas de aceptación: la postura del comprador y la del vendedor.\n"
            "       Exactamente una por parte y verificación.")]
n_acc = 0
for vid, txid, est, scope, slug, buyer in verifs:
    if est not in ("approved", "approved_obs", "rejected"):
        continue
    d = tx[txid]
    seller = d["seller_partner_id"]
    if est == "rejected":
        dec_b, dec_s = "rejected", "pending"
    else:
        dec_b = random.choices(["accepted", "accepted", "accepted", "counter"], k=1)[0]
        dec_s = "accepted" if dec_b == "accepted" else random.choice(["accepted", "rejected"])
    for role, partner, dec in (("buyer", buyer, dec_b), ("seller", seller, dec_s)):
        n_acc += 1
        extra = ""
        if dec == "counter":
            extra = f'      <field name="counter_price">{round(random.uniform(1.8, 4.2), 2)}</field>\n'
        razon = {
            "accepted": "Conforme con el informe del verificador.",
            "counter": "El rendimiento quedó por debajo de lo publicado; se propone ajustar el precio.",
            "rejected": "No se acepta: el informe no cumple lo ofertado.",
            "pending": "",
        }[dec]
        decided = ("" if dec == "pending" else
                   f'      <field name="decided_at">2026-09-{random.randint(1, 28):02d} '
                   f'{random.randint(8, 19):02d}:{random.randint(0, 59):02d}:00</field>\n')
        out.append(f"""    <record id="{vid}_acc_{role}" model="shrimp.verification.acceptance">
      <field name="verification_id" ref="{vid}"/>
      <field name="role">{role}</field>
      <field name="partner_id" ref="{MP}{partner}"/>
      <field name="decision">{dec}</field>
      <field name="reason">{esc(razon)}</field>
{extra}{decided}    </record>
""")
out.append(TAIL)
(DEMO / "demo_06_acceptances.xml").write_text("".join(out), encoding="utf-8")

# ================================================================ 07 calificaciones
out = [head("Calificaciones del verificador. Una sola por verificación, y quien\n"
            "       califica tiene que ser el comprador de esa compra.")]
COMENTARIOS = [
    "Llegó a la hora acordada y explicó cada medición. Muy correcto.",
    "Buen trabajo en campo, aunque el informe tardó en llegar.",
    "Muy detallado con el metabisulfito. Repetiría.",
    "Cumplió, sin más. La comunicación por WhatsApp fue lenta.",
    "Excelente criterio técnico, nos ahorró una compra mala.",
    "Puntual y ordenado. Las fotos del lote fueron muy útiles.",
    "El técnico fue amable pero le faltó rigor en el conteo.",
    "Informe impecable y a tiempo. Recomendado.",
    "Se coordinó bien con la empacadora. Sin quejas.",
    "Tuvo que repetir el muestreo, pero resolvió bien.",
]
n_rev = 0
for vid, txid, est, scope, slug, buyer in verifs:
    if est not in ("approved", "approved_obs", "rejected"):
        continue
    if random.random() > 0.72:      # no todo el mundo califica
        continue
    n_rev += 1
    base = 5 if est == "approved" else (4 if est == "approved_obs" else random.randint(2, 3))
    rating = max(1, min(5, base - random.choice([0, 0, 0, 1])))
    out.append(f"""    <record id="{vid}_rev" model="shrimp.verifier.review">
      <field name="verification_id" ref="{vid}"/>
      <field name="reviewer_partner_id" ref="{MP}{buyer}"/>
      <field name="rating">{rating}</field>
      <field name="punctuality">{max(1, min(5, rating + random.choice([-1, 0, 0, 1])))}</field>
      <field name="thoroughness">{max(1, min(5, rating + random.choice([-1, 0, 0, 1])))}</field>
      <field name="communication">{max(1, min(5, rating + random.choice([-1, 0, 1])))}</field>
      <field name="comment">{esc(random.choice(COMENTARIOS))}</field>
    </record>
""")
out.append(TAIL)
(DEMO / "demo_07_verifier_reviews.xml").write_text("".join(out), encoding="utf-8")

print(f"empresas={len(EMPRESAS)}  tecnicos={sum(len(v) for v in tecnicos.values())}")
print(f"verificaciones={len(verifs)} (adulto={len(ADULT)}, larva={len(LARVAE)})")
print(f"aceptaciones={n_acc}  calificaciones={n_rev}")
