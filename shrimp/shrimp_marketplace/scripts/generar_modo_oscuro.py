#!/usr/bin/env python3
"""Genera las hojas *_dark_auto.css del MODO OSCURO a partir de las hojas claras.

Las hojas de estilo de los módulos shrimp tienen cientos de colores escritos a
mano (#fff, #f7fafb, #123E5C...). En vez de reescribirlas, este script las lee
y emite, para cada regla que lleva un color «claro de fondo» o «oscuro de
texto», la MISMA regla prefijada con [data-bs-theme="dark"] y el color
traducido:

* fondos claros (blanco, grises, tintes pastel)  -> superficies oscuras
  (blanco -> var(--dk-surface); tintes -> el mismo tono, oscuro y apagado);
* textos oscuros o de poco contraste            -> el mismo tono, aclarado
  hasta >= 4.8:1 sobre la superficie oscura (WCAG AA);
* bordes claros                                 -> bordes oscuros;
* color: var(--shrimp-brand) y similares         -> la variante «de texto»
  del token (var(--dk-brand-text)...): el mismo token sirve de fondo de botón
  con texto blanco y de color de texto, y en oscuro no hay un color que valga
  para las dos cosas.

Los fondos saturados u oscuros (botones de marca, héroes, bandas en degradado,
pie de página) no se tocan: ya funcionan en oscuro. Las sombras tampoco (el
diseño las quita todas). Lo que el script no puede adivinar se corrige a mano
en las hojas *_dark.css, que se cargan después.

Uso (desde cualquier carpeta):

    python3 shrimp_marketplace/scripts/generar_modo_oscuro.py

Vuelve a ejecutarlo cuando cambie una hoja clara: sobrescribe los *_auto.css.
No necesita Odoo ni dependencias.
"""
import colorsys
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# destino -> hojas de origen (rutas relativas a la carpeta de addons shrimp)
OBJETIVOS = {
    "shrimp_marketplace/static/src/css/shrimp_dark_auto.css": [
        "shrimp_user_registry/static/src/css/shrimp_base.css",
        "shrimp_user_registry/static/src/css/shrimp_signup.css",
        "shrimp_marketplace/static/src/css/shrimp_marketplace.css",
        "shrimp_marketplace/static/src/css/shrimp_panel.css",
        "shrimp_marketplace/static/src/css/shrimp_product_form.css",
        "shrimp_marketplace/static/src/css/shrimp_product_detail.css",
        "shrimp_marketplace/static/src/css/shrimp_buy.css",
    ],
    "shrimp_verification/static/src/css/verification_dark_auto.css": [
        "shrimp_verification/static/src/css/verification.css",
    ],
    "shrimp_packer/static/src/css/packer_dark_auto.css": [
        "shrimp_packer/static/src/css/packer.css",
    ],
    "shrimp_copacking/static/src/css/copacking_dark_auto.css": [
        "shrimp_copacking/static/src/css/copacking_landing.css",
        "shrimp_copacking/static/src/css/copacking_portal.css",
    ],
}

# Reglas que se resuelven a mano en *_dark.css (o que deben quedar como están):
# la cabecera flotante, el pie (ya es oscuro) y los botones blancos sobre los
# héroes en degradado, que deben seguir siendo blancos.
EXCLUIR = (
    "header#top .navbar",
    "footer#bottom",
    ".o_footer_copyright",
    ".sl-btn--primary",
    ".cl-btn--light",
)

# Selectores exactos que se resuelven a mano (p. ej. el color genérico de los
# enlaces del sitio de verificadores, que pisaría el texto blanco de sus botones).
EXCLUIR_EXACTO = {
    "#wrapwrap.o_shrimp_verifier a",
}

# Superficie oscura de referencia para el contraste del texto (= --dk-surface).
SUPERFICIE = (0x16, 0x21, 0x2B)
CONTRASTE_MIN = 4.8

# Tokens que en oscuro tienen una variante distinta cuando se usan como TEXTO.
VAR_TEXTO = {
    "--shrimp-brand": "--dk-brand-text",
    "--shrimp-brand-ink": "--dk-brand-text",
    "--shrimp-teal": "--dk-teal-text",
    "--shrimp-success": "--dk-success-text",
    "--shrimp-warning": "--dk-warning-text",
    "--shrimp-danger": "--dk-danger-text",
    "--shrimp-state-pending": "--dk-warning-text",
    "--shrimp-state-progress": "--dk-teal-text",
    "--shrimp-state-done": "--dk-success-text",
    "--shrimp-navy": "--dk-brand-text",
    "--c-blue": "--dk-brand-text",
    "--wiz-brand": "--dk-brand-text",
    "--sp-navy": "--dk-brand-text",
    "--sp-teal": "--dk-teal-text",
    "--sp-green": "--dk-success-text",
    "--sp-amber": "--dk-warning-text",
    "--sp-coral": "--dk-coral-text",
    "--sl-navy-2": "--dk-brand-text",
    "--sl-blue": "--dk-brand-text",
    "--sl-teal": "--dk-teal-text",
    "--sl-green": "--dk-success-text",
    "--vw-teal": "--dk-teal-text",
    "--vw-teal-dark": "--dk-teal-text",
    "--cp-brand": "--dk-brand-text",
    "--cp-brand-ink": "--dk-brand-text",
    "--cp-ok": "--dk-success-text",
}
PROPS_TEXTO = {"color", "outline", "outline-color", "fill", "stroke", "caret-color",
               "text-decoration-color", "accent-color"}

RE_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\bwhite\b")
RE_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(,[^()]*(?:\([^()]*\)[^()]*)*)?\)")


# ---------------------------------------------------------------- colores
def parse(c):
    c = c.strip()
    if c == "white":
        return (255, 255, 255, 1.0)
    if c.startswith("#"):
        h = c[1:]
        if len(h) in (3, 4):
            h = "".join(x * 2 for x in h)
        if len(h) not in (6, 8):
            return None
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (r, g, b, a)
    m = re.match(r"rgba?\(([^)]*)\)", c)
    if not m:
        return None
    parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
    try:
        r, g, b = (float(p) for p in parts[:3])
        a = float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1) if len(parts) > 3 else 1.0
    except ValueError:
        return None
    return (r, g, b, a)


def lum(rgb):
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contraste(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def hls(rgb):
    return colorsys.rgb_to_hls(*(x / 255 for x in rgb[:3]))


def rgb_de(h, l, s):
    return tuple(round(x * 255) for x in colorsys.hls_to_rgb(h, l, s))


def fmt(rgb, a=1.0):
    if a >= 0.999:
        return "#%02x%02x%02x" % tuple(int(x) for x in rgb[:3])
    return "rgba(%d, %d, %d, %s)" % (rgb[0], rgb[1], rgb[2], ("%.3f" % a).rstrip("0").rstrip("."))


def fondo(col):
    r, g, b, a = col
    h, l, s = hls(col)
    if a < 0.999:
        if l >= 0.9 and s < 0.3:                 # blanco translúcido
            return fmt(SUPERFICIE, a) if a >= 0.6 else None   # (el «vidrio» de los héroes se queda)
        if a <= 0.4 and l < 0.8:                # velo de color: se aclara el tono
            return fmt(rgb_de(h, 0.72, min(s, 0.6)), min(a * 1.6, 0.28))
        if a <= 0.4:
            return fmt(rgb_de(h, 0.72, min(s, 0.6)), min(a * 1.2, 0.2))
        return None
    if l >= 0.97:
        return "var(--dk-surface)"
    if l >= 0.80:
        if s < 0.12:
            return "var(--dk-surface-2)"
        return fmt(rgb_de(h, 0.17, min(0.45, s * 0.55)))
    if l >= 0.62:
        return fmt(rgb_de(h, 0.26, min(0.4, s * 0.6)))
    return None


def borde(col):
    r, g, b, a = col
    if (r, g, b) == (255, 255, 255):
        return None       # borde blanco puro: va sobre un fondo oscuro (héroes)
    h, l, s = hls(col)
    if a < 0.999:
        if l < 0.5 and a <= 0.4:               # rgba(0,0,0,.08) y familia
            return fmt((255, 255, 255), min(max(a * 1.4, 0.08), 0.22))
        return None
    if l >= 0.75:
        if s < 0.3:
            return "var(--dk-border)"
        return fmt(rgb_de(h, 0.30, min(0.4, s * 0.6)))
    return None


def texto(col):
    r, g, b, a = col
    if contraste(col, SUPERFICIE) >= CONTRASTE_MIN or a < 0.5:
        return None
    h, l, s = hls(col)
    # Tinta casi negra (títulos, texto principal) -> casi blanca; grises y
    # colores medios -> el mismo tono con contraste holgado (>= 6:1).
    meta = 11.0 if l < 0.3 else 6.0
    s = min(s, 0.35 if l < 0.3 else 0.7)
    while l < 0.97:
        l += 0.02
        c = rgb_de(h, l, s)
        if contraste(c, SUPERFICIE) >= meta:
            return fmt(c, a)
    return "#ffffff"


def rol(prop):
    if prop.startswith("background"):
        return fondo
    if prop in PROPS_TEXTO:
        return texto
    if prop.startswith("border") or prop == "column-rule":
        return borde
    return None


def traducir(prop, valor):
    """Devuelve el valor en oscuro o None si no cambia."""
    fn = rol(prop)
    if not fn:
        return None
    nuevo = valor
    if fn is texto:
        def sv(m):
            alt = VAR_TEXTO.get(m.group(1))
            return "var(%s)" % alt if alt else m.group(0)
        nuevo = RE_VAR.sub(sv, nuevo)

    if fn is fondo:
        # Un degradado con paradas oscuras (héroes, bandas, banners) ya
        # funciona en oscuro: se deja entero, velos incluidos.
        for m in RE_COLOR.finditer(valor):
            col = parse(m.group(0))
            if col and col[3] >= 0.999 and hls(col)[1] < 0.4:
                return None

    def sc(m):
        col = parse(m.group(0))
        if not col:
            return m.group(0)
        out = fn(col)
        return out if out else m.group(0)
    # no tocar los colores de reserva dentro de var(--x, #fff)
    trozos = re.split(r"(var\([^()]*(?:\([^()]*\)[^()]*)*\))", nuevo)
    nuevo = "".join(t if t.startswith("var(") else RE_COLOR.sub(sc, t) for t in trozos)
    return nuevo if nuevo != valor else None


# ------------------------------------------------------------- parser CSS
def bloques(src):
    """Itera (prelude, cuerpo) al primer nivel, con llaves anidadas."""
    i, n = 0, len(src)
    while i < n:
        j = src.find("{", i)
        if j < 0:
            return
        prelude = src[i:j].strip()
        prof, k = 1, j + 1
        while k < n and prof:
            if src[k] == "{":
                prof += 1
            elif src[k] == "}":
                prof -= 1
            k += 1
        yield prelude, src[j + 1:k - 1]
        i = k


def selector_oscuro(sel):
    sel = sel.strip()
    if sel.startswith(":root"):
        return ':root[data-bs-theme="dark"]' + sel[5:]
    if re.match(r"html\b", sel):
        return 'html[data-bs-theme="dark"]' + sel[4:]
    return '[data-bs-theme="dark"] ' + sel


def declaraciones(cuerpo):
    out = []
    # Texto con degradado (background-clip: text): el degradado ES el color
    # del texto y va sobre héroes oscuros; no se toca.
    texto_degradado = re.search(r"background-clip\s*:\s*text", cuerpo) is not None
    for d in re.split(r";(?![^(]*\))", cuerpo):
        if ":" not in d:
            continue
        p, v = d.split(":", 1)
        p, v = p.strip().lower(), v.strip()
        if not p or p.startswith("--"):
            continue
        imp = ""
        if v.endswith("!important"):
            v, imp = v[: -len("!important")].strip(), " !important"
        if texto_degradado and p.startswith("background"):
            continue
        nv = traducir(p, v)
        if nv:
            out.append("%s: %s%s" % (p, nv, imp))
    return out


def procesar(src, sangria=""):
    res = []
    for prelude, cuerpo in bloques(src):
        if prelude.startswith("@"):
            if prelude.startswith(("@media", "@supports")):
                dentro = procesar(cuerpo, sangria + "  ")
                if dentro:
                    res.append("%s%s {\n%s\n%s}" % (sangria, prelude, "\n".join(dentro), sangria))
            continue   # @keyframes, @font-face...
        sels = [s.strip() for s in prelude.split(",") if s.strip()]
        sels = [s for s in sels if s not in EXCLUIR_EXACTO and not any(x in s for x in EXCLUIR)]
        if not sels:
            continue
        decl = declaraciones(cuerpo)
        if decl:
            res.append("%s%s { %s; }" % (sangria, ", ".join(selector_oscuro(s) for s in sels), "; ".join(decl)))
    return res


def main():
    for destino, origenes in OBJETIVOS.items():
        partes = [
            "/* GENERADO por shrimp_marketplace/scripts/generar_modo_oscuro.py — NO EDITAR A MANO.\n"
            "   Modo oscuro automático de: %s.\n"
            "   Los retoques a mano van en la hoja *_dark.css del mismo módulo. */" % ", ".join(
                os.path.basename(o) for o in origenes)
        ]
        for o in origenes:
            ruta = os.path.join(RAIZ, o)
            if not os.path.exists(ruta):
                continue
            src = re.sub(r"/\*.*?\*/", "", open(ruta, encoding="utf-8").read(), flags=re.S)
            reglas = procesar(src)
            if reglas:
                partes.append("\n/* ---- %s ---- */" % o)
                partes.extend(reglas)
        with open(os.path.join(RAIZ, destino), "w", encoding="utf-8") as fh:
            fh.write("\n".join(partes) + "\n")
        print("escrito", destino, sum(1 for p in partes if "{" in p), "reglas")


if __name__ == "__main__":
    main()
