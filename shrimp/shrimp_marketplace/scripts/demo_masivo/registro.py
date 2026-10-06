# -*- coding: utf-8 -*-
"""shrimp_user_registry: catálogo de certificados, socios de los tres roles
productivos (semillero, laboratorio, camaronera), sus cuentas de acceso y sus
certificados con el PDF adjunto."""
from .comun import (RNG, UR, Archivo, B64, CIUDADES, CIUDADES_CAMARONERA,
                    CIUDADES_LAB, CIUDADES_SEMILLERO, D, DOMINIO_LOGIN, M2M,
                    PASSWORD_DEMO, R, celular, dominio, geo, pdf_minimo,
                    persona, r2, ruc_persona_natural, ruc_sociedad, slug,
                    telefono_fijo)

# Certificados del catálogo base (data/shrimp_certificate_data.xml) por rol.
CERT_BASE = {
    "semillero": ["shrimp_certificate_registro_larvas", "shrimp_certificate_spf",
                  "shrimp_certificate_bap_hatchery", "shrimp_certificate_pcr_wssv",
                  "shrimp_certificate_pcr_ahpnd", "shrimp_certificate_movilizacion_larvas",
                  "shrimp_certificate_acuerdo_acuicultura"],
    "laboratorio": ["shrimp_certificate_haccp", "shrimp_certificate_iso_22000",
                    "shrimp_certificate_arcsa", "shrimp_certificate_bpm",
                    "shrimp_certificate_brc", "shrimp_certificate_microbiologico",
                    "shrimp_certificate_residuos"],
    "camaronera": ["shrimp_certificate_bap", "shrimp_certificate_asc",
                   "shrimp_certificate_exportacion", "shrimp_certificate_fda",
                   "shrimp_certificate_organic"],
    "all": ["shrimp_certificate_globalgap", "shrimp_certificate_agrocalidad"],
}
CERT_EXPIRA = {  # años de vigencia (None = sin vencimiento obligatorio)
    "shrimp_certificate_registro_larvas": 2, "shrimp_certificate_spf": 1,
    "shrimp_certificate_bap_hatchery": 1, "shrimp_certificate_pcr_wssv": None,
    "shrimp_certificate_pcr_ahpnd": None, "shrimp_certificate_movilizacion_larvas": None,
    "shrimp_certificate_acuerdo_acuicultura": None, "shrimp_certificate_haccp": 2,
    "shrimp_certificate_iso_22000": 3, "shrimp_certificate_arcsa": 5,
    "shrimp_certificate_bpm": 2, "shrimp_certificate_brc": 1,
    "shrimp_certificate_microbiologico": None, "shrimp_certificate_residuos": None,
    "shrimp_certificate_bap": 1, "shrimp_certificate_asc": 3,
    "shrimp_certificate_exportacion": None, "shrimp_certificate_fda": None,
    "shrimp_certificate_organic": 1, "shrimp_certificate_globalgap": 1,
    "shrimp_certificate_agrocalidad": 1,
}

# Certificados que el sector usa y el catálogo base no tenía.
# (xmlid, nombre, emisor, rol, tipo, código, expira, duración, período, descripción)
CERT_NUEVOS = [
    ("demo_cert_permiso_ambiental", "Permiso Ambiental (MAATE)",
     "Ministerio del Ambiente, Agua y Transición Ecológica", "camaronera",
     "sustainability", "MAATE-PA", True, 5, "years",
     "Licencia o registro ambiental de la actividad acuícola."),
    ("demo_cert_concesion_playa", "Acuerdo de concesión de zona de playa y bahía",
     "Subsecretaría de Acuacultura", "camaronera", "other", "SUBACUA-CON", True, 10,
     "years", "Concesión para ocupar zona de playa y bahía con piscinas camaroneras."),
    ("demo_cert_calidad_agua", "Análisis de calidad de agua", "Laboratorio acreditado SAE",
     "camaronera", "quality", "AGUA-FQ", False, 0, "years",
     "Oxígeno disuelto, amonio, nitritos, salinidad y pH de las piscinas."),
    ("demo_cert_naturland", "Naturland (acuicultura orgánica)", "Naturland e.V.",
     "camaronera", "sustainability", "NATURLAND", True, 1, "years",
     "Estándar orgánico para camarón de cultivo con baja densidad."),
    ("demo_cert_fair_trade", "Fair Trade USA - Seafood", "Fair Trade USA", "camaronera",
     "social", "FT-SEAFOOD", True, 3, "years",
     "Condiciones laborales y prima social para trabajadores de la finca."),
    ("demo_cert_carbono_neutro", "Carbono Neutro Ecuador", "MAATE - Programa Ecuador Carbono Cero",
     "camaronera", "sustainability", "ECC0", True, 1, "years",
     "Medición y compensación de la huella de carbono de la finca."),
    ("demo_cert_pcr_ihhnv", "Análisis PCR de IHHNV", "Laboratorio de diagnóstico acreditado",
     "semillero", "biosecurity", "PCR-IHHNV", False, 0, "years",
     "Necrosis hipodérmica y hematopoyética infecciosa."),
    ("demo_cert_pcr_ehp", "Análisis PCR de EHP", "Laboratorio de diagnóstico acreditado",
     "semillero", "biosecurity", "PCR-EHP", False, 0, "years",
     "Enterocytozoon hepatopenaei en reproductores y nauplios."),
    ("demo_cert_reproductores_spf", "Certificado de reproductores SPF importados",
     "Agrocalidad - Cuarentena", "semillero", "biosecurity", "AGRO-REPRO", True, 6,
     "months", "Ingreso de reproductores libres de patógenos específicos."),
    ("demo_cert_libre_venta", "Certificado de Libre Venta", "ARCSA", "laboratorio",
     "quality", "ARCSA-CLV", True, 2, "years",
     "Permite comercializar la larva fuera de la provincia y exportarla."),
    ("demo_cert_iso_9001", "ISO 9001", "Certificadoras ISO acreditadas", "laboratorio",
     "quality", "ISO 9001", True, 3, "years", "Sistema de gestión de la calidad."),
    ("demo_cert_bioseguridad_lab", "Certificado de bioseguridad de laboratorio de larvas",
     "Agrocalidad", "laboratorio", "biosecurity", "AGRO-BIO-LAB", True, 1, "years",
     "Inspección anual de bioseguridad de la planta de larvicultura."),
    ("demo_cert_iso_14001", "ISO 14001", "Certificadoras ISO acreditadas", "all",
     "sustainability", "ISO 14001", True, 3, "years", "Sistema de gestión ambiental."),
    ("demo_cert_smeta", "Auditoría social SMETA", "Sedex", "all", "social", "SMETA",
     True, 2, "years", "Auditoría ética de condiciones laborales (4 pilares)."),
    ("demo_cert_origen", "Certificado de Origen", "MPCEIP", "all", "other", "MPCEIP-CO",
     False, 0, "years", "Origen ecuatoriano del producto para preferencias arancelarias."),
    ("demo_cert_registro_acuicola", "Inscripción en el Registro Acuícola",
     "Subsecretaría de Acuacultura", "camaronera", "other", "RA-GR", False, 0, "years",
     "Código GR- de la camaronera en el registro nacional."),
]

# -------------------------------------------------------------- nombres
SEM_NOMBRES = [
    "Maduración Punta Carnero S.A.", "Nauplios del Pacífico Sur Cía. Ltda.",
    "Reproductores Ayangue S.A.", "Maduración Monteverde S.A.",
    "Genética Marina San Pablo S.A.", "Nauplios Mar Bravo Cía. Ltda.",
    "Maduración Costa Dorada S.A.", "Reproductores del Golfo S.A.",
    "Semillas Marinas Chanduy Cía. Ltda.", "Maduración Punta Blanca S.A.",
    "Nauplios Valdivia S.A.", "Biogenética Anconcito Cía. Ltda.",
    "Maduración Brisas del Mar S.A.", "Reproductores Libertador Bolívar S.A.",
    "Nauplios San Clemente Cía. Ltda.",
]
LAB_NOMBRES = [
    "Laboratorio de Larvas Ayangue S.A.", "Larvicultura Monteverde Cía. Ltda.",
    "Biolarvas San Pablo S.A.", "Hatchery Punta Blanca S.A.",
    "Laboratorio Mar Bravo Larvas Cía. Ltda.", "Larvas Selectas de la Península S.A.",
    "Laboratorio Bahía Azul S.A.", "Postlarvas del Litoral Cía. Ltda.",
    "Larvicultura San Clemente S.A.", "Laboratorio Jaramijó Larvas S.A.",
    "Hatchery Atacames Cía. Ltda.", "Larvas del Pacífico Chanduy S.A.",
    "Laboratorio Aguas Claras S.A.", "Larvicultura Playas del Sol Cía. Ltda.",
    "Biolarvas Santa Elena S.A.",
]
CAM_BASE = [
    ("Camaronera Estero Salado", "Chongón"), ("Acuícola Naranjal", "Naranjal"),
    ("Camarones del Churute", "Naranjal"), ("Langostinos Balao Chico", "Balao"),
    ("Camaronera Las Garzas", "Taura"), ("Fincas Acuícolas El Morro", "Puerto El Morro"),
    ("Camaronera Data de Posorja", "Posorja"), ("Acuícola Isla Puná", "Isla Puná"),
    ("Camaronera Río Yaguachi", "Yaguachi"), ("Acuícola Puerto Jelí", "Santa Rosa"),
    ("Camaronera Hualtaco", "Hualtaco"), ("Camarones La Tembladera", "Santa Rosa"),
    ("Acuícola Jambelí", "Puerto Bolívar"), ("Camaronera El Guabo", "El Guabo"),
    ("Camaronera Arenillas", "Arenillas"), ("Langostinos Huaquillas", "Huaquillas"),
    ("Camaronera Tendales", "El Guabo"), ("Acuícola Pasaje", "Pasaje"),
    ("Camaronera Estuario del Chone", "Chone"), ("Acuícola Bahía", "Bahía de Caráquez"),
    ("Camaronera Cojimíes", "Cojimíes"), ("Langostinos de Pedernales", "Pedernales"),
    ("Acuícola Tosagua", "Tosagua"), ("Camaronera Briceño", "San Vicente"),
    ("Camaronera Muisne", "Muisne"), ("Acuícola Manglares de Taura", "Taura"),
    ("Camaronera Puerto Roma", "Chongón"), ("Acuícola San Jacinto", "Bahía de Caráquez"),
    ("Camaronera La Puntilla", "Machala"), ("Acuícola Bajo Alto", "El Guabo"),
]
SUFIJOS = [" S.A.", " Cía. Ltda.", " S.A.", " S.A.S."]
# Grupos camaroneros: empresa madre + filiales (shrimp_grupo_ids).
GRUPOS = [
    ("Grupo Acuícola Del Río", "Naranjal", 3),
    ("Corporación Camaronera Orense", "Santa Rosa", 3),
    ("Holding Acuícola Manabita", "Chone", 2),
]


def construir(M):
    """Rellena M con los socios y escribe los tres XML del módulo."""
    M.setdefault("partners", {})
    M["sem"], M["lab"], M["cam"] = [], [], []
    M["usuarios"] = []          # (login, xmlid_user, partner_xmlid, rol, modulo)
    M["cert_lines"] = {}        # partner -> [(xid, cert_xmlid, status, vence_off)]

    f1 = Archivo("shrimp_user_registry", "demo_masivo_01_catalogo.xml",
                 "Catálogo de certificados adicional y usuarios internos de back-office.")
    f2 = Archivo("shrimp_user_registry", "demo_masivo_02_socios.xml",
                 "Semilleros, laboratorios y camaroneras con su cuenta de portal.")
    f3 = Archivo("shrimp_user_registry", "demo_masivo_03_certificados.xml",
                 "Expedientes PDF y certificados de cada socio (aprobados, pendientes,\n"
                 "     rechazados y vencidos), tal como llegan a la bandeja de aprobación.")

    # ---------------------------------------------------------- catálogo
    f1.seccion("Certificados del catálogo que faltaban")
    for i, (xid, nom, emisor, rol, tipo, cod, exp, dur, per, desc) in enumerate(CERT_NUEVOS):
        f1.rec(xid, "shrimp.certificate", {
            "sequence": 300 + i * 10, "name": nom, "issuer": emisor, "role": rol,
            "certificate_type": tipo, "code": cod, "description": desc,
            "expires_required": exp, "duration_value": dur if exp else 0,
            "duration_period": per, "active": True,
        })
        CERT_BASE.setdefault(rol, []).append(xid)
        CERT_EXPIRA[xid] = dur if exp else None
    # Uno archivado: el catálogo también tiene historia.
    f1.rec("demo_cert_obsoleto_inp", "shrimp.certificate", {
        "sequence": 900, "name": "Certificado sanitario INP (formato anterior)",
        "issuer": "Instituto Nacional de Pesca", "role": "camaronera",
        "certificate_type": "quality", "code": "INP-OLD", "expires_required": False,
        "duration_value": 0, "description": "Sustituido por el certificado de Agrocalidad.",
        "active": False,
    })

    f1.seccion("Usuarios internos (back-office): aprueban certificados y socios")
    for login, nombre, grupos in [
        ("aprobador.certificados", "Aprobador de certificados", ["base.group_user"]),
        ("soporte.socios", "Soporte a socios del marketplace", ["base.group_user"]),
    ]:
        pid = f"demo_ms_int_{slug(login)}_partner"
        f1.rec(pid, "res.partner", {"name": nombre, "email": f"{login}@{DOMINIO_LOGIN}",
                                   "company_id": R("base.main_company")})
        uid = f"demo_ms_user_int_{slug(login)}"
        f1.rec(uid, "res.users", {
            "partner_id": R(pid), "login": f"{login}@{DOMINIO_LOGIN}",
            "password": PASSWORD_DEMO, "company_id": R("base.main_company"),
            "company_ids": M2M(["base.main_company"]), "group_ids": M2M(grupos),
            "tz": "America/Guayaquil",
        })
        M["usuarios"].append((f"{login}@{DOMINIO_LOGIN}", uid, pid, "interno", "shrimp_user_registry"))

    # ---------------------------------------------------------- socios
    def alta(rol, nombre, ciudad, xid, padre=None, persona_natural=False):
        prov_cod = CIUDADES[ciudad][1]
        st = CIUDADES[ciudad][2]
        dom = dominio(nombre.replace(" S.A.S.", "").replace(" S.A.", "").replace(" Cía. Ltda.", ""))
        email = f"{'ventas' if rol != 'camaronera' else 'gerencia'}@{dom}.test"
        vat = ruc_persona_natural(prov_cod) if persona_natural else ruc_sociedad(prov_cod)
        rep = persona(con_titulo=True)
        lat, lon = geo(ciudad)
        tel = telefono_fijo(ciudad)
        vals = {
            "name": nombre, "is_company": True, "shrimp_user_type": rol,
            "vat_or_id": vat, "email": email, "phone": tel,
            "street": f"{RNG.choice(['Km', 'Vía'])} {RNG.randint(2, 28)} "
                      f"{RNG.choice(['vía a la Costa', 'vía Data', 'vía Puerto Bolívar', 'Ruta del Spondylus', 'vía Naranjal', 'vía Puerto Inca', 'sector El Estero', 'Recinto La Playita'])}",
            "city": ciudad, "state_id": R(st), "country_id": R("base.ec"),
            "partner_latitude": lat, "partner_longitude": lon,
            "website": f"https://www.{dom}.test", "parent_id": R(padre) if padre else None,
            "comment": f"Socio de demostración ({rol}). Expediente completo en la plataforma.",
        }
        if rol == "laboratorio":
            vals.update({
                "shrimp_razon_social": nombre, "shrimp_ubicacion": f"{ciudad}, {CIUDADES[ciudad][0]}",
                "lab_global_gap": RNG.random() < 0.55,
                "lab_social_ship_partner": RNG.random() < 0.35,
            })
        elif rol == "camaronera":
            area = r2(RNG.choice([RNG.uniform(25, 120), RNG.uniform(120, 450), RNG.uniform(450, 1600)]))
            vals.update({
                "shrimp_razon_social": nombre, "shrimp_representante": rep,
                "shrimp_telefono": tel, "shrimp_ubicacion": f"{ciudad}, {CIUDADES[ciudad][0]}",
                "farm_area_ha": area,
                # ~2,2 t/ha/año en semi-intensivo ecuatoriano
                "shrimp_capacity_value": r2(area * RNG.uniform(1.6, 3.2)),
                "shrimp_capacity_unit": "ton_year",
            })
        f2.rec(xid, "res.partner", vals)
        M["partners"][xid] = {"rol": rol, "nombre": nombre, "ciudad": ciudad,
                              "provincia": CIUDADES[ciudad][0], "email": email,
                              "vat": vat, "rep": rep, "lat": lat, "lon": lon,
                              "modulo": "shrimp_user_registry", "padre": padre}
        return xid

    f2.seccion("Semilleros (maduración y producción de nauplios)")
    for i, nombre in enumerate(SEM_NOMBRES):
        ciudad = CIUDADES_SEMILLERO[i % len(CIUDADES_SEMILLERO)]
        M["sem"].append(alta("semillero", nombre, ciudad, f"demo_ms_sem_{i + 1:02d}"))

    f2.seccion("Laboratorios (larvicultura: de nauplio a postlarva)")
    for i, nombre in enumerate(LAB_NOMBRES):
        ciudad = CIUDADES_LAB[i % len(CIUDADES_LAB)]
        M["lab"].append(alta("laboratorio", nombre, ciudad, f"demo_ms_lab_{i + 1:02d}"))

    f2.seccion("Grupos camaroneros (empresa madre) y camaroneras")
    M["grupos_cam"] = {}
    n = 0
    for gnombre, gciudad, hijos in GRUPOS:
        n += 1
        gx = alta("camaronera", gnombre + " S.A.", gciudad, f"demo_ms_cam_grupo_{n}")
        M["cam"].append(gx)
        M["grupos_cam"][gx] = []
    grupos_x = list(M["grupos_cam"].keys())
    for i, (base, ciudad) in enumerate(CAM_BASE):
        nombre = base + SUFIJOS[i % len(SUFIJOS)]
        padre = None
        # las primeras filiales cuelgan de un grupo
        if i < sum(g[2] for g in GRUPOS):
            acum = 0
            for gx, (_, _, h) in zip(grupos_x, GRUPOS):
                if i < acum + h:
                    padre = gx
                    break
                acum += h
        natural = (i % 7 == 6)
        if natural:
            nombre = f"{base} de {persona().split()[1]}"
        x = alta("camaronera", nombre, ciudad, f"demo_ms_cam_{i + 1:02d}", padre=padre,
                 persona_natural=natural)
        M["cam"].append(x)
        if padre:
            M["grupos_cam"][padre].append(x)

    # ---------------------------------------------------------- usuarios portal
    f2.seccion("Cuentas de portal (una por socio). Contraseña: " + PASSWORD_DEMO)
    for rol, lista in (("semillero", M["sem"]), ("laboratorio", M["lab"]), ("camaronera", M["cam"])):
        base_n = {"semillero": 5, "laboratorio": 5, "camaronera": 5}[rol]  # 01-05: demo antigua
        for k, px in enumerate(lista, start=base_n + 1):
            login = f"{rol}.{k:02d}@{DOMINIO_LOGIN}"
            ux = f"demo_ms_user_{px[8:]}"
            f2.rec(ux, "res.users", {
                "partner_id": R(px), "login": login, "password": PASSWORD_DEMO,
                "company_id": R("base.main_company"),
                "company_ids": M2M(["base.main_company"]),
                "group_ids": M2M(["base.group_portal"]), "tz": "America/Guayaquil",
            })
            M["usuarios"].append((login, ux, px, rol, "shrimp_user_registry"))
            M["partners"][px]["user"] = UR + ux

    # ---------------------------------------------------------- certificados
    f3.seccion("Expedientes PDF (uno por socio)")
    for px in M["sem"] + M["lab"] + M["cam"]:
        p = M["partners"][px]
        ax = f"{px}_expediente"
        f3.rec(ax, "ir.attachment", {
            "name": f"Expediente_{slug(p['nombre'], 30)}.pdf", "type": "binary",
            "mimetype": "application/pdf",
            "datas": B64(pdf_minimo(f"Expediente de certificados - {p['nombre']}", [
                f"RUC: {p['vat']}", f"Ubicación: {p['ciudad']}, {p['provincia']}",
                f"Representante: {p['rep']}", "Contiene copia de los certificados vigentes",
                "declarados en la plataforma de trazabilidad."])),
        })
        p["adjunto"] = ax

    f3.seccion("Certificados de cada socio")
    for px in M["sem"] + M["lab"] + M["cam"]:
        p = M["partners"][px]
        rol = p["rol"]
        disponibles = list(dict.fromkeys(CERT_BASE[rol] + CERT_BASE["all"]))
        elegidos = RNG.sample(disponibles, RNG.randint(3, 5))
        M["cert_lines"][px] = []
        for j, cx in enumerate(elegidos, 1):
            ref_cert = UR + cx
            anios = CERT_EXPIRA.get(cx)
            emision = -RNG.randint(20, 700)
            # Estado: la mayoría aprobados; algunos pendientes de revisión,
            # rechazados (papel ilegible) o ya vencidos.
            estado = RNG.choices(["approved", "pending", "rejected"], [78, 14, 8])[0]
            if anios:
                dur_dias = int(anios * 365) if anios >= 1 else 180
                vence = emision + dur_dias
            else:
                vence = emision + RNG.choice([180, 365, 540])
            if j == 1 and estado == "approved" and vence < 30:
                vence = RNG.randint(60, 500)  # al menos uno vigente
            lx = f"{px}_cert_{j}"
            codigo = (cx.replace("shrimp_certificate_", "").replace("demo_cert_", "")[:6]).upper()
            f3.rec(lx, "shrimp.user.certificate.line", {
                "partner_id": R(px), "certificate_id": R(ref_cert),
                "certificate_number": f"{codigo}-{p['vat'][:4]}-{RNG.randint(1000, 99999)}",
                "issue_date": D(emision), "expiry_date": D(vence),
                "file_attachment_id": R(p["adjunto"]), "status": estado,
            })
            M["cert_lines"][px].append((lx, cx, estado, vence))

    for f in (f1, f2, f3):
        M["archivos"].append(f)
