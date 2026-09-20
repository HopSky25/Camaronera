from pathlib import Path
from random import choice, randint, uniform

SEMILLEROS = 8
LABORATORIOS = 8
CAMARONERAS = 8

SEMILLERO_PRODUCTS = 135
LAB_PRODUCTS = 85

EVOLUTIONS_PER_PRODUCT = 10
SEM_TO_LAB_TX = 70
LAB_TO_FARM_TX = 60
CHECK_REQUESTS = 80

FACILITIES_PER_PARTNER = 2
PONDS_PER_PARTNER = 3
ALLOCATIONS = 120


species = [
    "shrimp_species_vannamei",
    "shrimp_species_monodon",
]

stages = [
    "shrimp_stage_nauplio",
    "shrimp_stage_zoea",
    "shrimp_stage_mysis",
    "shrimp_stage_pl8",
    "shrimp_stage_pl10",
    "shrimp_stage_pl12",
    "shrimp_stage_pl15",
    "shrimp_stage_juvenil",
]

genetics = [
    "shrimp_genetics_spr",
    "shrimp_genetics_spr_plus",
    "shrimp_genetics_fast_growth",
    "shrimp_genetics_high_survival",
    "shrimp_genetics_balanced_performance",
    "shrimp_genetics_monodon_classic",
]

locations = [
    "Guayas", "El Oro", "Manabí", "Santa Elena",
    "Esmeraldas", "Los Ríos", "Tumbes", "Piura"
]

out = []
out.append('<?xml version="1.0" encoding="utf-8"?>')
out.append("<odoo>")

def add(xml):
    out.append(xml)

# =====================================================
# PARTNERS + USUARIOS PORTAL
# =====================================================

for i in range(1, SEMILLEROS + 1):
    email = f"semillero.demo.{i}@demo.com"

    add(f"""
    <record id="demo_semillero_{i}" model="res.partner">
        <field name="name">Semillero Demo {i}</field>
        <field name="email">{email}</field>
        <field name="vat_or_id">SEMDEMO{i:010d}</field>
        <field name="shrimp_user_type">semillero</field>
    </record>

    <record id="demo_user_semillero_{i}" model="res.users">
        <field name="name">Semillero Demo {i}</field>
        <field name="login">{email}</field>
        <field name="email">{email}</field>
        <field name="partner_id" ref="demo_semillero_{i}"/>
        <field name="groups_id" eval="[(6, 0, [ref('base.group_portal')])]"/>
        <field name="company_id" ref="base.main_company"/>
        <field name="company_ids" eval="[(6, 0, [ref('base.main_company')])]"/>
        <field name="password">demo123</field>
    </record>
    """)

for i in range(1, LABORATORIOS + 1):
    email = f"laboratorio.demo.{i}@demo.com"
    location = choice(locations)

    add(f"""
    <record id="demo_laboratorio_{i}" model="res.partner">
        <field name="name">Laboratorio Demo {i}</field>
        <field name="email">{email}</field>
        <field name="vat_or_id">LABDEMO{i:010d}</field>
        <field name="shrimp_user_type">laboratorio</field>
        <field name="lab_razon_social">Laboratorio Demo {i} S.A.</field>
        <field name="lab_ubicacion">{location}</field>
        <field name="lab_global_gap">1</field>
        <field name="lab_social_ship_partner">1</field>
    </record>

    <record id="demo_user_laboratorio_{i}" model="res.users">
        <field name="name">Laboratorio Demo {i}</field>
        <field name="login">{email}</field>
        <field name="email">{email}</field>
        <field name="partner_id" ref="demo_laboratorio_{i}"/>
        <field name="groups_id" eval="[(6, 0, [ref('base.group_portal')])]"/>
        <field name="company_id" ref="base.main_company"/>
        <field name="company_ids" eval="[(6, 0, [ref('base.main_company')])]"/>
        <field name="password">demo123</field>
    </record>
    """)

for i in range(1, CAMARONERAS + 1):
    email = f"camaronera.demo.{i}@demo.com"
    location = choice(locations)

    add(f"""
    <record id="demo_camaronera_{i}" model="res.partner">
        <field name="name">Camaronera Demo {i}</field>
        <field name="email">{email}</field>
        <field name="vat_or_id">FARMDEMO{i:010d}</field>
        <field name="shrimp_user_type">camaronera</field>
        <field name="farm_razon_social">Camaronera Demo {i} S.A.</field>
        <field name="farm_representante">Representante Demo {i}</field>
        <field name="farm_telefono">099{i:07d}</field>
        <field name="farm_ubicacion">{location}</field>
        <field name="farm_capacidad">{randint(300, 5000)}</field>
        <field name="farm_area_ha">{round(uniform(20, 800), 2)}</field>
    </record>

    <record id="demo_user_camaronera_{i}" model="res.users">
        <field name="name">Camaronera Demo {i}</field>
        <field name="login">{email}</field>
        <field name="email">{email}</field>
        <field name="partner_id" ref="demo_camaronera_{i}"/>
        <field name="groups_id" eval="[(6, 0, [ref('base.group_portal')])]"/>
        <field name="company_id" ref="base.main_company"/>
        <field name="company_ids" eval="[(6, 0, [ref('base.main_company')])]"/>
        <field name="password">demo123</field>
    </record>
    """)

# =====================================================
# INSTALACIONES Y PISCINAS
# =====================================================

partner_refs = []
for i in range(1, SEMILLEROS + 1):
    partner_refs.append(("semillero", i, f"demo_semillero_{i}"))
for i in range(1, LABORATORIOS + 1):
    partner_refs.append(("laboratorio", i, f"demo_laboratorio_{i}"))
for i in range(1, CAMARONERAS + 1):
    partner_refs.append(("camaronera", i, f"demo_camaronera_{i}"))

pond_refs_by_partner = {}

for ptype, i, partner_ref in partner_refs:
    pond_refs_by_partner[partner_ref] = []

    for f in range(1, FACILITIES_PER_PARTNER + 1):
        facility_id = f"demo_facility_{ptype}_{i}_{f}"
        facility_type = "hatchery" if ptype == "semillero" else ("laboratory" if ptype == "laboratorio" else "farm")
        location = choice(locations)

        add(f"""
    <record id="{facility_id}" model="shrimp.partner.facility">
        <field name="partner_id" ref="{partner_ref}"/>
        <field name="name">Instalación {ptype.title()} {i}-{f}</field>
        <field name="code">{ptype[:3].upper()}-{i:02d}-{f:02d}</field>
        <field name="facility_type">{facility_type}</field>
        <field name="city">{location}</field>
        <field name="province">{location}</field>
        <field name="active">1</field>
        <field name="notes">Instalación demo generada automáticamente.</field>
    </record>
        """)

        for pond in range(1, PONDS_PER_PARTNER + 1):
            pond_id = f"demo_pond_{ptype}_{i}_{f}_{pond}"
            pond_refs_by_partner[partner_ref].append(pond_id)

            length = randint(20, 120)
            width = randint(10, 80)
            depth = round(uniform(0.8, 2.2), 2)

            add(f"""
    <record id="{pond_id}" model="shrimp.partner.pond">
        <field name="partner_id" ref="{partner_ref}"/>
        <field name="facility_id" ref="{facility_id}"/>
        <field name="name">Piscina {ptype.title()} {i}-{f}-{pond}</field>
        <field name="code">P-{ptype[:3].upper()}-{i:02d}-{f:02d}-{pond:02d}</field>
        <field name="pond_type">earth</field>
        <field name="location">{choice(locations)}</field>
        <field name="capacity_mode">dimensions</field>
        <field name="length_m">{length}</field>
        <field name="width_m">{width}</field>
        <field name="depth_m">{depth}</field>
        <field name="usable_volume_m3">{round(length * width * depth * 0.85, 2)}</field>
        <field name="max_stock_units">{randint(100, 3000)}</field>
        <field name="active">1</field>
    </record>
            """)

# =====================================================
# PRODUCTOS SEMILLERO + LOTES + EVOLUCIÓN
# =====================================================

sem_product_info = []

for i in range(1, SEMILLERO_PRODUCTS + 1):
    seller = randint(1, SEMILLEROS)
    seller_ref = f"demo_semillero_{seller}"
    product_id = f"demo_sem_product_{i}"
    lot_id = f"demo_sem_lot_{i}"

    stage = choice(stages)
    genetic = choice(genetics)
    specie = choice(species)
    qty = randint(500, 4000)
    price = round(uniform(10, 40), 2)
    survival = round(uniform(80, 98), 2)
    size = round(uniform(0.10, 1.50), 2)
    location = choice(locations)

    sem_product_info.append({
        "product_id": product_id,
        "lot_id": lot_id,
        "seller_ref": seller_ref,
        "seller_num": seller,
        "qty": qty,
    })

    add(f"""
    <record id="{product_id}" model="shrimp.product" context="{{'skip_initial_lot': True}}">
        <field name="name">Lote Semillero Demo {i} - {location}</field>
        <field name="seller_partner_id" ref="{seller_ref}"/>
        <field name="species_id" ref="{specie}"/>
        <field name="stage_id" ref="{stage}"/>
        <field name="genetics_line_id" ref="{genetic}"/>
        <field name="initial_qty">{qty}</field>
        <field name="uom">millar</field>
        <field name="price">{price}</field>
        <field name="survival_rate">{survival}</field>
        <field name="avg_size_mg">{size}</field>
        <field name="location">{location}</field>
        <field name="state">published</field>
        <field name="seller_role">semillero</field>
        <field name="health_status">Condición sanitaria estable en lote de semillero.</field>
    </record>

    <record id="{lot_id}" model="shrimp.stock.lot">
        <field name="product_id" ref="{product_id}"/>
        <field name="owner_id" ref="{seller_ref}"/>
        <field name="initial_qty">{qty}</field>
        <field name="available_qty">{qty}</field>
        <field name="uom">millar</field>
        <field name="state">available</field>
    </record>
    """)

    current_survival = 99.0
    current_qty = qty

    for j in range(1, EVOLUTIONS_PER_PRODUCT + 1):
        current_survival -= round(uniform(0.3, 2.5), 2)
        current_qty -= randint(5, 40)
        evo_stage = stages[min(j, len(stages) - 1)]
        evo_size = round(size + (j * uniform(0.05, 0.20)), 2)

        add(f"""
    <record id="{product_id}_evolution_{j}" model="shrimp.product.evolution">
        <field name="product_id" ref="{product_id}"/>
        <field name="date">2026-03-{j:02d} 08:00:00</field>
        <field name="stage_id" ref="{evo_stage}"/>
        <field name="avg_size_mg">{evo_size}</field>
        <field name="survival_rate">{round(current_survival, 2)}</field>
        <field name="available_qty">{max(current_qty, 0)}</field>
        <field name="note">Muestreo demo #{j} del producto de semillero.</field>
        <field name="health_status">Control sanitario demo sin novedades críticas.</field>
    </record>
        """)

# =====================================================
# PRODUCTOS LABORATORIO + LOTES + EVOLUCIÓN
# =====================================================

lab_product_info = []

for i in range(1, LAB_PRODUCTS + 1):
    seller = randint(1, LABORATORIOS)
    seller_ref = f"demo_laboratorio_{seller}"
    product_id = f"demo_lab_product_{i}"
    lot_id = f"demo_lab_lot_{i}"

    stage = choice(["shrimp_stage_pl10", "shrimp_stage_pl12", "shrimp_stage_pl15", "shrimp_stage_juvenil"])
    genetic = choice(genetics)
    specie = choice(species)
    qty = randint(300, 3000)
    price = round(uniform(18, 55), 2)
    survival = round(uniform(75, 96), 2)
    size = round(uniform(0.8, 3.5), 2)
    location = choice(locations)

    lab_product_info.append({
        "product_id": product_id,
        "lot_id": lot_id,
        "seller_ref": seller_ref,
        "seller_num": seller,
        "qty": qty,
    })

    add(f"""
    <record id="{product_id}" model="shrimp.product" context="{{'skip_initial_lot': True}}">
        <field name="name">Lote Laboratorio Demo {i} - {location}</field>
        <field name="seller_partner_id" ref="{seller_ref}"/>
        <field name="species_id" ref="{specie}"/>
        <field name="stage_id" ref="{stage}"/>
        <field name="genetics_line_id" ref="{genetic}"/>
        <field name="initial_qty">{qty}</field>
        <field name="uom">millar</field>
        <field name="price">{price}</field>
        <field name="survival_rate">{survival}</field>
        <field name="avg_size_mg">{size}</field>
        <field name="location">{location}</field>
        <field name="state">published</field>
        <field name="seller_role">laboratorio</field>
        <field name="health_status">Condición sanitaria estable en lote de laboratorio.</field>
    </record>

    <record id="{lot_id}" model="shrimp.stock.lot">
        <field name="product_id" ref="{product_id}"/>
        <field name="owner_id" ref="{seller_ref}"/>
        <field name="initial_qty">{qty}</field>
        <field name="available_qty">{qty}</field>
        <field name="uom">millar</field>
        <field name="state">available</field>
    </record>
    """)

    current_survival = 97.0
    current_qty = qty

    for j in range(1, EVOLUTIONS_PER_PRODUCT + 1):
        current_survival -= round(uniform(0.5, 3.0), 2)
        current_qty -= randint(5, 45)
        evo_size = round(size + (j * uniform(0.10, 0.35)), 2)

        add(f"""
    <record id="{product_id}_evolution_{j}" model="shrimp.product.evolution">
        <field name="product_id" ref="{product_id}"/>
        <field name="date">2026-04-{j:02d} 08:00:00</field>
        <field name="stage_id" ref="shrimp_stage_juvenil"/>
        <field name="avg_size_mg">{evo_size}</field>
        <field name="survival_rate">{round(current_survival, 2)}</field>
        <field name="available_qty">{max(current_qty, 0)}</field>
        <field name="note">Muestreo demo #{j} del producto de laboratorio.</field>
        <field name="health_status">Control sanitario demo sin novedades críticas.</field>
    </record>
        """)

# =====================================================
# SOLICITUDES DE CHEQUEO
# =====================================================

for i in range(1, CHECK_REQUESTS + 1):
    if i % 2:
        prod = choice(sem_product_info)
        buyer_ref = f"demo_laboratorio_{randint(1, LABORATORIOS)}"
    else:
        prod = choice(lab_product_info)
        buyer_ref = f"demo_camaronera_{randint(1, CAMARONERAS)}"

    qty = randint(10, 180)
    state = choice(["requested", "under_review", "approved", "rejected", "cancelled"])

    add(f"""
    <record id="demo_check_request_{i}" model="shrimp.check.request">
        <field name="product_id" ref="{prod['product_id']}"/>
        <field name="seller_partner_id" ref="{prod['seller_ref']}"/>
        <field name="buyer_partner_id" ref="{buyer_ref}"/>
        <field name="qty">{qty}</field>
        <field name="state">{state}</field>
        <field name="note">Solicitud demo generada para pruebas de flujo.</field>
    </record>
    """)

# =====================================================
# TRANSACCIONES SEMILLERO -> LABORATORIO
# MOVIMIENTOS + LOTES COMPRADOR
# =====================================================

buyer_lab_lots = []

for i in range(1, SEM_TO_LAB_TX + 1):
    prod = choice(sem_product_info)
    buyer = randint(1, LABORATORIOS)
    qty = randint(10, 180)
    tx_id = f"demo_sem_lab_tx_{i}"
    move_id = f"demo_sem_lab_move_{i}"
    buyer_lot_id = f"demo_sem_lab_buyer_lot_{i}"

    buyer_lab_lots.append({
        "lot_id": buyer_lot_id,
        "product_id": prod["product_id"],
        "owner_ref": f"demo_laboratorio_{buyer}",
        "qty": qty,
    })

    add(f"""
    <record id="{tx_id}" model="shrimp.transaction">
        <field name="name">TX-SEM-LAB-{i:05d}</field>
        <field name="transaction_type">semillero_to_laboratorio</field>
        <field name="product_id" ref="{prod['product_id']}"/>
        <field name="seller_partner_id" ref="{prod['seller_ref']}"/>
        <field name="buyer_partner_id" ref="demo_laboratorio_{buyer}"/>
        <field name="location">{choice(locations)}</field>
        <field name="transaction_qty">{qty}</field>
        <field name="sold_qty">{qty}</field>
        <field name="sold_date">2026-04-{randint(1, 25):02d}</field>
        <field name="state">confirmed</field>
    </record>

    <record id="{move_id}" model="shrimp.stock.move">
        <field name="product_id" ref="{prod['product_id']}"/>
        <field name="source_partner_id" ref="{prod['seller_ref']}"/>
        <field name="dest_partner_id" ref="demo_laboratorio_{buyer}"/>
        <field name="qty">{qty}</field>
        <field name="transaction_id" ref="{tx_id}"/>
        <field name="date">2026-04-{randint(1, 25):02d} 10:00:00</field>
    </record>

    <record id="{buyer_lot_id}" model="shrimp.stock.lot">
        <field name="product_id" ref="{prod['product_id']}"/>
        <field name="owner_id" ref="demo_laboratorio_{buyer}"/>
        <field name="origin_move_id" ref="{move_id}"/>
        <field name="initial_qty">{qty}</field>
        <field name="available_qty">{qty}</field>
        <field name="uom">millar</field>
        <field name="state">available</field>
    </record>
    """)

# =====================================================
# TRANSACCIONES LABORATORIO -> CAMARONERA
# MOVIMIENTOS + LOTES COMPRADOR
# =====================================================

farm_lots = []

for i in range(1, LAB_TO_FARM_TX + 1):
    prod = choice(lab_product_info)
    buyer = randint(1, CAMARONERAS)
    qty = randint(10, 150)
    tx_id = f"demo_lab_farm_tx_{i}"
    move_id = f"demo_lab_farm_move_{i}"
    farm_lot_id = f"demo_lab_farm_buyer_lot_{i}"

    farm_lots.append({
        "lot_id": farm_lot_id,
        "product_id": prod["product_id"],
        "owner_ref": f"demo_camaronera_{buyer}",
        "owner_num": buyer,
        "qty": qty,
    })

    add(f"""
    <record id="{tx_id}" model="shrimp.transaction">
        <field name="name">TX-LAB-FARM-{i:05d}</field>
        <field name="transaction_type">laboratorio_to_camaronera</field>
        <field name="product_id" ref="{prod['product_id']}"/>
        <field name="seller_partner_id" ref="{prod['seller_ref']}"/>
        <field name="buyer_partner_id" ref="demo_camaronera_{buyer}"/>
        <field name="location">{choice(locations)}</field>
        <field name="transaction_qty">{qty}</field>
        <field name="desired_qty">{qty}</field>
        <field name="desired_date">2026-05-{randint(1, 25):02d}</field>
        <field name="state">confirmed</field>
    </record>

    <record id="{move_id}" model="shrimp.stock.move">
        <field name="product_id" ref="{prod['product_id']}"/>
        <field name="source_partner_id" ref="{prod['seller_ref']}"/>
        <field name="dest_partner_id" ref="demo_camaronera_{buyer}"/>
        <field name="qty">{qty}</field>
        <field name="transaction_id" ref="{tx_id}"/>
        <field name="date">2026-05-{randint(1, 25):02d} 10:00:00</field>
    </record>

    <record id="{farm_lot_id}" model="shrimp.stock.lot">
        <field name="product_id" ref="{prod['product_id']}"/>
        <field name="owner_id" ref="demo_camaronera_{buyer}"/>
        <field name="origin_move_id" ref="{move_id}"/>
        <field name="initial_qty">{qty}</field>
        <field name="available_qty">{qty}</field>
        <field name="uom">millar</field>
        <field name="state">available</field>
    </record>
    """)

# =====================================================
# ASIGNACIONES DE LOTES A PISCINAS
# =====================================================

all_assignable_lots = []

for lot in buyer_lab_lots:
    all_assignable_lots.append(lot)

for lot in farm_lots:
    all_assignable_lots.append(lot)

for i in range(1, min(ALLOCATIONS, len(all_assignable_lots)) + 1):
    lot = all_assignable_lots[i - 1]
    pond_options = pond_refs_by_partner.get(lot["owner_ref"], [])
    if not pond_options:
        continue

    pond_ref = choice(pond_options)
    allocated_qty = max(1, round(lot["qty"] * uniform(0.25, 0.85), 2))

    add(f"""
    <record id="demo_lot_allocation_{i}" model="shrimp.lot.allocation">
        <field name="stock_lot_id" ref="{lot['lot_id']}"/>
        <field name="pond_id" ref="{pond_ref}"/>
        <field name="allocated_qty">{allocated_qty}</field>
        <field name="allocation_date">2026-05-{randint(1, 25):02d}</field>
        <field name="state">allocated</field>
        <field name="notes">Asignación demo de lote a piscina.</field>
    </record>
    """)

out.append("</odoo>")

Path("demo").mkdir(exist_ok=True)
Path("demo/shrimp_demo_aggressive_data.xml").write_text(
    "\n".join(out),
    encoding="utf-8"
)

print("Archivo generado: demo/shrimp_demo_aggressive_data.xml")
print(f"Partners: {SEMILLEROS + LABORATORIOS + CAMARONERAS}")
print(f"Productos semillero: {SEMILLERO_PRODUCTS}")
print(f"Productos laboratorio: {LAB_PRODUCTS}")
print(f"Evoluciones: {(SEMILLERO_PRODUCTS + LAB_PRODUCTS) * EVOLUTIONS_PER_PRODUCT}")
print(f"Solicitudes: {CHECK_REQUESTS}")
print(f"Transacciones semillero-laboratorio: {SEM_TO_LAB_TX}")
print(f"Transacciones laboratorio-camaronera: {LAB_TO_FARM_TX}")
print(f"Asignaciones: {min(ALLOCATIONS, SEM_TO_LAB_TX + LAB_TO_FARM_TX)}")