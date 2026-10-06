# -*- coding: utf-8 -*-
"""Piezas comunes del generador de demo masiva.

Todo lo que es azar pasa por ``RNG`` (semilla fija): dos ejecuciones dan el
mismo XML byte a byte, y el diff entre versiones se puede leer.

FECHAS. Ninguna fecha se escribe fija. Se escribe como desfase en días
respecto al día en que se CARGA la demo, igual que ya hacía la demo de
shrimp_packer:

    eval="(DateTime.today() + relativedelta(days=-120)).strftime('%Y-%m-%d')"

Hay restricciones que comparan contra "hoy" (vigencia de un compromiso de
reserva, llegada real de un camión que no puede estar en el futuro, listas
"vigentes"...). Con fechas fijas la demo se rompería sola al pasar los meses;
con desfases siempre cuenta "los últimos doce meses hasta hoy". El generador
razona con REF (el 2026-10-01) solo para ordenar y para los textos.
"""
import base64
import random
import unicodedata
import zlib
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

SEMILLA = 20261001
RNG = random.Random(SEMILLA)
REF = date(2026, 10, 1)

# Raíz de los addons (…/shrimp): scripts/demo_masivo/comun.py -> parents[3]
ADDONS = Path(__file__).resolve().parents[3]

MP = "shrimp_marketplace."
UR = "shrimp_user_registry."
SV = "shrimp_verification."
SP = "shrimp_packer."

PASSWORD_DEMO = "demo123"

# Cobros de la plataforma en la demo (shrimp.charge). Los datos de ejemplo
# NO se facturan: quedan "Por facturar" con los intentos automáticos ya
# agotados (el cron de reintento los salta) y el motivo a la vista.
CHG_DEMO = {
    "state": "pending",
    "invoice_attempts": 5,
    "invoice_error": "Datos de demostración: los cobros de ejemplo no se facturan "
                     "automáticamente.",
}


def cobro_demo(**vals):
    """Valores de un shrimp.charge de demostración."""
    out = dict(CHG_DEMO)
    out.update(vals)
    if "amount" in out and "unit_amount" not in out:
        out["unit_amount"] = round(out["amount"] / (out.get("invoice_qty") or 1.0), 6)
    return out
DOMINIO_LOGIN = "camaronera.test"


# ---------------------------------------------------------------------------
# Valores de campo
# ---------------------------------------------------------------------------
class R:
    """Referencia a un xml id (``ref="..."``)."""
    __slots__ = ("x",)

    def __init__(self, x):
        self.x = x


class E:
    """Expresión ``eval`` literal."""
    __slots__ = ("x",)

    def __init__(self, x):
        self.x = x


class D:
    """Fecha relativa al día de carga (en días)."""
    __slots__ = ("off",)

    def __init__(self, off):
        self.off = int(off)


class DT:
    """Fecha-hora relativa. ``hora`` es hora LOCAL de Ecuador (UTC-5): se
    guarda en UTC, como hace Odoo."""
    __slots__ = ("off", "h", "m")

    def __init__(self, off, h=8, m=0):
        self.off, self.h, self.m = int(off), int(h), int(m)


class M2M:
    __slots__ = ("ids",)

    def __init__(self, ids):
        self.ids = list(ids)


class SR:
    """Many2one resuelto por búsqueda al cargar (``model=... search=...``).

    En el eval de un <field> no existe ``obj`` (solo en los <function>), así
    que para enlazar un registro que depende de la fecha de carga —el aguaje
    de una lista de precios— se usa el atributo search del XML."""
    __slots__ = ("model", "domain")

    def __init__(self, model, domain):
        self.model, self.domain = model, domain


class F:
    """Fichero binario del propio addon (``type="base64" file="..."``)."""
    __slots__ = ("path",)

    def __init__(self, path):
        self.path = path


class B64:
    """Binario en línea, ya en base64."""
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


def fecha_txt(off):
    return (REF + timedelta(days=off)).strftime("%d/%m/%Y")


def _val(name, v):
    if isinstance(v, bool):
        return f'<field name="{name}" eval="{v}"/>'
    if isinstance(v, R):
        return f'<field name="{name}" ref="{v.x}"/>'
    if isinstance(v, E):
        return f'<field name="{name}" eval="{escape(v.x, {chr(34): "&quot;"})}"/>'
    if isinstance(v, D):
        return (f'<field name="{name}" eval="(DateTime.today() + relativedelta('
                f"days={v.off})).strftime('%Y-%m-%d')\"/>")
    if isinstance(v, DT):
        h = v.h + 5  # local Ecuador -> UTC
        return (f'<field name="{name}" eval="(DateTime.today().replace(hour=0, '
                f'minute=0, second=0, microsecond=0) + relativedelta(days={v.off}, '
                f"hours={h}, minutes={v.m})).strftime('%Y-%m-%d %H:%M:%S')\"/>")
    if isinstance(v, SR):
        return (f'<field name="{name}" model="{v.model}" '
                f'search="{escape(v.domain, {chr(34): "&quot;"})}"/>')
    if isinstance(v, M2M):
        refs = ", ".join(f"ref('{i}')" for i in v.ids)
        return f'<field name="{name}" eval="[(6, 0, [{refs}])]"/>'
    if isinstance(v, F):
        return f'<field name="{name}" type="base64" file="{v.path}"/>'
    if isinstance(v, B64):
        return f'<field name="{name}">{v.data}</field>'
    if isinstance(v, float):
        return f'<field name="{name}" eval="{round(v, 4)!r}"/>'
    if isinstance(v, int):
        return f'<field name="{name}" eval="{v}"/>'
    return f'<field name="{name}">{escape(str(v))}</field>'


def _py(v):
    """Valor como expresión Python para el eval de un <function>."""
    if isinstance(v, E):
        return v.x
    if isinstance(v, R):
        return f"ref('{v.x}')"
    if isinstance(v, D):
        return f"(DateTime.today() + relativedelta(days={v.off})).strftime('%Y-%m-%d')"
    if isinstance(v, DT):
        return (f"(DateTime.today().replace(hour=0, minute=0, second=0, microsecond=0) + "
                f"relativedelta(days={v.off}, hours={v.h + 5}, minutes={v.m})).strftime('%Y-%m-%d %H:%M:%S')")
    return repr(v)


class Archivo:
    """Un fichero XML de demo. Acumula registros en orden de escritura."""

    def __init__(self, modulo, nombre, comentario):
        self.modulo = modulo
        self.nombre = nombre
        self.comentario = comentario
        self.partes = []
        self.conteo = {}
        self.ids = []
        self.escrituras = []     # <function write> idempotentes (bloque noupdate="0")

    def seccion(self, texto):
        self.partes.append(f"\n        <!-- ===== {escape(texto)} ===== -->\n")

    def rec(self, xid, model, campos, context=None):
        ctx = f' context="{escape(context, {chr(34): "&quot;"})}"' if context else ""
        lineas = [f'        <record id="{xid}" model="{model}"{ctx}>']
        for k, v in campos.items():
            if v is None:
                continue
            lineas.append("            " + _val(k, v))
        lineas.append("        </record>")
        self.partes.append("\n".join(lineas) + "\n")
        self.conteo[model] = self.conteo.get(model, 0) + 1
        self.ids.append(xid)
        return xid

    def upd(self, xid, model, campos):
        """Actualiza un registro ya existente (write). No cuenta como nuevo."""
        lineas = [f'        <record id="{xid}" model="{model}">']
        for k, v in campos.items():
            if v is None:
                continue
            lineas.append("            " + _val(k, v))
        lineas.append("        </record>")
        self.partes.append("\n".join(lineas) + "\n")

    def funcion(self, model, name, eval_expr):
        self.partes.append(
            f'        <function model="{model}" name="{name}" '
            f'eval="{escape(eval_expr, {chr(34): "&quot;"})}"/>\n')

    def fn_write(self, model, xmlids, dominio, vals, context=None):
        """Escritura sobre registros YA existentes que también debe aplicarse en
        un -u de una base que ya tenía la demo.

        Odoo no reescribe registros noupdate en una actualización (ni siquiera
        los que acaba de crear en la misma pasada), así que estas escrituras
        van como <function name="write"> en un bloque noupdate="0" al final
        del archivo. ``dominio`` filtra los registros que todavía están en el
        estado de partida: la segunda vez no encuentra nada y no hace nada.
        Los xml ids que ya no existan se ignoran (no rompen la actualización).
        """
        vals = {k: v for k, v in vals.items() if v is not None}
        ids = ", ".join(repr(x) for x in xmlids)
        expr = (f"[obj().search([('id', 'in', [r.id for r in [obj().env.ref(x, False) for x in [{ids}]] if r])]"
                f" + {dominio!r}).ids, {{{', '.join(f'{k!r}: {_py(v)}' for k, v in vals.items())}}}]")
        self.escrituras.append(
            f'        <function model="{model}" name="write"'
            + (f' context="{escape(context, {chr(34): "&quot;"})}"' if context else "")
            + f' eval="{escape(expr, {chr(34): "&quot;"})}"/>\n')

    def texto(self):
        cab = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!-- " + self.comentario.replace("--", "-") + "\n\n"
            "     GENERADO por shrimp_marketplace/scripts/demo_masivo (semilla fija).\n"
            "     No editar a mano: cambiar el generador y volver a ejecutarlo.\n"
            "     Fechas relativas al día de carga de la demo. -->\n"
            "<odoo>\n    <data noupdate=\"1\">\n")
        cuerpo = cab + "".join(self.partes) + "    </data>\n"
        if self.escrituras:
            cuerpo += ('    <!-- Escrituras idempotentes: se aplican al instalar y también en -u. -->\n'
                       '    <data noupdate="0">\n' + "".join(self.escrituras) + "    </data>\n")
        return cuerpo + "</odoo>\n"

    def escribir(self):
        ruta = ADDONS / self.modulo / "demo" / self.nombre
        ruta.parent.mkdir(exist_ok=True)
        ruta.write_text(self.texto(), encoding="utf-8")
        return ruta


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def slug(texto, maximo=40):
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = "".join(c.lower() if c.isalnum() else "_" for c in t)
    while "__" in t:
        t = t.replace("__", "_")
    return t.strip("_")[:maximo].strip("_")


def dominio(texto):
    return slug(texto, 30).replace("_", "")


def r2(x):
    return round(float(x), 2)


def elegir_pesos(opciones):
    """opciones: [(valor, peso), ...]"""
    total = sum(p for _, p in opciones)
    x = RNG.uniform(0, total)
    acc = 0
    for v, p in opciones:
        acc += p
        if x <= acc:
            return v
    return opciones[-1][0]


# ---------------------------------------------------------------------------
# Identificaciones ecuatorianas (formato y dígito verificador reales)
# ---------------------------------------------------------------------------
_RUCS = set()


def ruc_sociedad(provincia="09"):
    """RUC de sociedad privada: PP 9 NNNNNN V 001, módulo 11."""
    coef = [4, 3, 2, 7, 6, 5, 4, 3, 2]
    while True:
        base = provincia + "9" + "".join(str(RNG.randint(0, 9)) for _ in range(6))
        s = sum(int(d) * c for d, c in zip(base, coef))
        v = 11 - (s % 11)
        if v == 11:
            v = 0
        if v == 10:
            continue
        r = f"{base}{v}001"
        if r not in _RUCS:
            _RUCS.add(r)
            return r


def cedula(provincia="09"):
    """Cédula: PP T NNNNNN V, módulo 10 (coef 2,1,2,1...)."""
    while True:
        base = provincia + str(RNG.randint(0, 5)) + "".join(
            str(RNG.randint(0, 9)) for _ in range(6))
        s = 0
        for i, d in enumerate(base):
            p = int(d) * (2 if i % 2 == 0 else 1)
            s += p - 9 if p > 9 else p
        v = (10 - s % 10) % 10
        c = f"{base}{v}"
        if c not in _RUCS:
            _RUCS.add(c)
            return c


def ruc_persona_natural(provincia="09"):
    return cedula(provincia) + "001"


# ---------------------------------------------------------------------------
# Geografía
# ---------------------------------------------------------------------------
# ciudad: (provincia, código provincia, state xmlid, lat, lon, prefijo fijo)
CIUDADES = {
    "Guayaquil": ("Guayas", "09", "base.state_ec_09", -2.19, -79.89, "04"),
    "Durán": ("Guayas", "09", "base.state_ec_09", -2.17, -79.83, "04"),
    "Naranjal": ("Guayas", "09", "base.state_ec_09", -2.67, -79.62, "04"),
    "Balao": ("Guayas", "09", "base.state_ec_09", -2.91, -79.81, "04"),
    "Taura": ("Guayas", "09", "base.state_ec_09", -2.32, -79.70, "04"),
    "Puerto El Morro": ("Guayas", "09", "base.state_ec_09", -2.64, -80.32, "04"),
    "Posorja": ("Guayas", "09", "base.state_ec_09", -2.70, -80.24, "04"),
    "General Villamil (Playas)": ("Guayas", "09", "base.state_ec_09", -2.63, -80.39, "04"),
    "Chongón": ("Guayas", "09", "base.state_ec_09", -2.23, -80.07, "04"),
    "Isla Puná": ("Guayas", "09", "base.state_ec_09", -2.75, -80.13, "04"),
    "Yaguachi": ("Guayas", "09", "base.state_ec_09", -2.11, -79.69, "04"),
    "Machala": ("El Oro", "07", "base.state_ec_07", -3.26, -79.96, "07"),
    "Santa Rosa": ("El Oro", "07", "base.state_ec_07", -3.45, -79.96, "07"),
    "Huaquillas": ("El Oro", "07", "base.state_ec_07", -3.48, -80.23, "07"),
    "Arenillas": ("El Oro", "07", "base.state_ec_07", -3.55, -80.06, "07"),
    "Pasaje": ("El Oro", "07", "base.state_ec_07", -3.33, -79.81, "07"),
    "El Guabo": ("El Oro", "07", "base.state_ec_07", -3.24, -79.83, "07"),
    "Puerto Bolívar": ("El Oro", "07", "base.state_ec_07", -3.27, -80.00, "07"),
    "Hualtaco": ("El Oro", "07", "base.state_ec_07", -3.45, -80.18, "07"),
    "Chone": ("Manabí", "13", "base.state_ec_13", -0.70, -80.09, "05"),
    "Bahía de Caráquez": ("Manabí", "13", "base.state_ec_13", -0.60, -80.42, "05"),
    "Pedernales": ("Manabí", "13", "base.state_ec_13", 0.07, -80.05, "05"),
    "Cojimíes": ("Manabí", "13", "base.state_ec_13", 0.36, -80.04, "05"),
    "Tosagua": ("Manabí", "13", "base.state_ec_13", -0.79, -80.23, "05"),
    "San Vicente": ("Manabí", "13", "base.state_ec_13", -0.59, -80.40, "05"),
    "Manta": ("Manabí", "13", "base.state_ec_13", -0.95, -80.73, "05"),
    "Jaramijó": ("Manabí", "13", "base.state_ec_13", -0.95, -80.63, "05"),
    "San Clemente": ("Manabí", "13", "base.state_ec_13", -0.77, -80.51, "05"),
    "Santa Elena": ("Santa Elena", "24", "base.state_ec_24", -2.23, -80.86, "04"),
    "La Libertad": ("Santa Elena", "24", "base.state_ec_24", -2.23, -80.91, "04"),
    "Salinas": ("Santa Elena", "24", "base.state_ec_24", -2.21, -80.96, "04"),
    "Ayangue": ("Santa Elena", "24", "base.state_ec_24", -1.98, -80.75, "04"),
    "San Pablo": ("Santa Elena", "24", "base.state_ec_24", -2.13, -80.77, "04"),
    "Mar Bravo": ("Santa Elena", "24", "base.state_ec_24", -2.24, -80.96, "04"),
    "Monteverde": ("Santa Elena", "24", "base.state_ec_24", -1.98, -80.74, "04"),
    "Punta Blanca": ("Santa Elena", "24", "base.state_ec_24", -2.11, -80.78, "04"),
    "Chanduy": ("Santa Elena", "24", "base.state_ec_24", -2.40, -80.69, "04"),
    "Anconcito": ("Santa Elena", "24", "base.state_ec_24", -2.33, -80.89, "04"),
    "Muisne": ("Esmeraldas", "08", "base.state_ec_08", 0.61, -80.02, "06"),
    "Atacames": ("Esmeraldas", "08", "base.state_ec_08", 0.87, -79.85, "06"),
}

CIUDADES_SEMILLERO = ["Ayangue", "San Pablo", "Monteverde", "Punta Blanca", "Mar Bravo",
                      "Salinas", "Anconcito", "San Clemente", "Chanduy", "Santa Elena"]
CIUDADES_LAB = ["San Pablo", "Ayangue", "Monteverde", "Punta Blanca", "Mar Bravo",
                "La Libertad", "San Clemente", "Jaramijó", "Atacames", "Chanduy",
                "General Villamil (Playas)"]
CIUDADES_CAMARONERA = ["Naranjal", "Balao", "Taura", "Puerto El Morro", "Posorja",
                       "Isla Puná", "Chongón", "Yaguachi", "Machala", "Santa Rosa",
                       "Huaquillas", "Arenillas", "Pasaje", "El Guabo", "Hualtaco",
                       "Chone", "Bahía de Caráquez", "Pedernales", "Cojimíes",
                       "Tosagua", "San Vicente", "Muisne"]
CIUDADES_PLANTA = ["Guayaquil", "Durán", "Machala", "Manta", "Posorja", "Santa Elena",
                   "Puerto Bolívar", "Jaramijó", "Taura", "Chongón"]


def geo(ciudad, jitter=0.06):
    prov, cod, st, lat, lon, pref = CIUDADES[ciudad]
    return (round(lat + RNG.uniform(-jitter, jitter), 6),
            round(lon + RNG.uniform(-jitter, jitter), 6))


def telefono_fijo(ciudad):
    pref = CIUDADES[ciudad][5]
    return f"{pref}-{RNG.randint(2, 6)}{RNG.randint(100000, 999999)}"


def celular():
    return f"+593 9{RNG.randint(5, 9)} {RNG.randint(100, 999)} {RNG.randint(1000, 9999)}"


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------
NOMBRES_H = ["José", "Luis", "Carlos", "Jorge", "Juan", "Miguel", "Fernando", "Andrés",
             "Ricardo", "Patricio", "Fabián", "Xavier", "Darwin", "Byron", "Wilson",
             "Édison", "Galo", "Holger", "Iván", "Marcelo", "Rubén", "Segundo",
             "Washington", "Jaime", "Diego", "Kléber", "Óscar", "Freddy", "Ángel",
             "Danny", "Bolívar", "Ramiro", "Hernán", "Gustavo", "Rolando", "Víctor"]
NOMBRES_M = ["María", "Ana", "Gabriela", "Verónica", "Karina", "Andrea", "Patricia",
             "Mónica", "Lorena", "Jessica", "Paola", "Cecilia", "Rocío", "Tatiana",
             "Silvia", "Glenda", "Johanna", "Mercedes", "Narcisa", "Elena", "Diana",
             "Katherine", "Maritza", "Ximena", "Yadira", "Fernanda", "Lucía", "Doris"]
APELLIDOS = ["Zambrano", "Mendoza", "Vera", "Cedeño", "Moreira", "Loor", "Intriago",
             "Macías", "Bravo", "Álava", "Pincay", "Tomalá", "Yagual", "Suárez", "Reyes",
             "Villamar", "Quimí", "Lindao", "Borbor", "Rodríguez", "Sánchez", "Torres",
             "Aguirre", "Chávez", "Castillo", "Espinoza", "Valarezo", "Romero", "Jaramillo",
             "Ordóñez", "Aguilar", "Peñafiel", "Ronquillo", "Mite", "Baque", "Holguín",
             "Coello", "Cabrera", "Salazar", "Burgos", "Alvarado", "León", "Muñoz",
             "Carvajal", "Benítez", "Palacios", "Quintero", "Andrade", "Murillo",
             "Ochoa", "Solórzano", "Delgado", "Anchundia", "Bajaña", "Parrales"]



def persona(con_titulo=False):
    mujer = RNG.random() < 0.42
    n = RNG.choice(NOMBRES_M if mujer else NOMBRES_H)
    a1, a2 = RNG.sample(APELLIDOS, 2)
    nombre = f"{n} {a1} {a2}"
    if con_titulo:
        t = RNG.choice(["Ing.", "Blga.", "Acuic.", "Econ.", "Lcda."] if mujer
                       else ["Ing.", "Blgo.", "Acuic.", "Econ.", "Lcdo."])
        nombre = f"{t} {nombre}"
    return nombre


# ---------------------------------------------------------------------------
# PDF mínimo (certificados de demo)
# ---------------------------------------------------------------------------
def pdf_minimo(titulo, lineas):
    """PDF de una página, válido, con texto Helvetica. Sin dependencias."""
    def esc_pdf(s):
        s = unicodedata.normalize("NFKD", s).encode("latin-1", "ignore").decode("latin-1")
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT /F1 16 Tf 60 760 Td (%s) Tj ET" % esc_pdf(titulo)]
    y = 730
    for l in lineas:
        ops.append("BT /F1 11 Tf 60 %d Td (%s) Tj ET" % (y, esc_pdf(l)))
        y -= 18
    ops.append("BT /F1 8 Tf 60 60 Td (Documento de DEMOSTRACION - sin validez legal) Tj ET")
    contenido = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(contenido) + contenido + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, xref)
    return base64.b64encode(bytes(out)).decode()


def crc(texto):
    return zlib.crc32(texto.encode()) & 0xFFFFFFFF
