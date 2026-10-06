"""Cajón de filtros reutilizable: piezas del lado del servidor.

Todas las páginas con filtros siguen el patrón aprobado en /marketplace:
barra superior (búsqueda · «Filtros (N)» · vista · orden), etiquetas de los
filtros activos que se quitan de una en una y un cajón lateral con solo los
filtros que aplican al perfil activo y a los datos que hay de verdad.

Las plantillas (shrimp_marketplace.s_filter_toolbar / s_filter_tags /
s_filter_drawer) leen un único diccionario `fd` que arma el controlador con
estas ayudas; el comportamiento lo pone static/src/js/shrimp_filter_drawer.js.
"""
import json
from urllib.parse import urlencode

from odoo.http import request


def fd_url(base, params, anchor=""):
    """URL de `base` con los parámetros no vacíos. `params` puede ser un dict o
    una lista de pares (para parámetros repetidos, p. ej. emp=a&emp=b)."""
    pares = params.items() if isinstance(params, dict) else params
    qs = []
    for clave, valor in pares:
        if isinstance(valor, (list, tuple)):
            qs += [(clave, v) for v in valor if v not in (None, "", False)]
        elif valor not in (None, "", False):
            qs.append((clave, valor))
    return base + ("?" + urlencode(qs) if qs else "") + (anchor or "")


def fd_tags(base, values, groups, label_fn, keep=None, anchor=""):
    """Etiquetas removibles de los filtros activos.

    values   dict parámetro → valor (los vacíos no cuentan)
    groups   tuplas de parámetros que forman UNA etiqueta (p. ej. desde/hasta)
    label_fn (grupo, values) → texto de la etiqueta, o None si no aplica
    keep     parámetros que no son filtros y se conservan siempre (búsqueda,
             orden, vista)

    Cada etiqueta enlaza a la misma página sin SU parámetro y con todos los
    demás. Devuelve (etiquetas, url de «Limpiar todo»)."""
    keep = {k: v for k, v in (keep or {}).items() if v}
    activos = {k: v for k, v in values.items() if v and k not in keep}
    tags = []
    for group in groups:
        valor = values.get(group[0])
        if len(group) == 1 and isinstance(valor, (list, tuple)):
            # Parámetro repetido (cert=bap&cert=asc): una etiqueta por valor;
            # quitarla deja los demás valores del mismo parámetro.
            for item in valor:
                label = label_fn(group, dict(values, **{group[0]: item}))
                if not label:
                    continue
                qs = [(k, v) for k, v in activos.items() if k != group[0]]
                qs.append((group[0], [x for x in valor if x != item]))
                qs += list(keep.items())
                tags.append({"key": "%s-%s" % (group[0], item), "label": label,
                             "url": fd_url(base, qs, anchor)})
            continue
        label = label_fn(group, values)
        if not label:
            continue
        qs = [(k, v) for k, v in activos.items() if k not in group]
        qs += list(keep.items())
        tags.append({"key": "-".join(group), "label": label, "url": fd_url(base, qs, anchor)})
    return tags, fd_url(base, keep, anchor)


def fd_json_count(count):
    """Respuesta del botón «Ver N …» del cajón."""
    return request.make_response(json.dumps({"count": int(count)}), headers=[
        ("Content-Type", "application/json; charset=utf-8"),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
    ])


def fd_date_label(value):
    """'2026-02-02' → '02/02/2026' (las etiquetas hablan como la pantalla)."""
    value = (value or "").strip()
    partes = value.split("-")
    if len(partes) == 3 and all(p.isdigit() for p in partes):
        return "%s/%s/%s" % (partes[2], partes[1], partes[0])
    return value


def fd_range_label(prefix, desde, hasta, fmt=fd_date_label):
    if desde and hasta:
        return "%s: %s – %s" % (prefix, fmt(desde), fmt(hasta))
    if desde:
        return "%s desde %s" % (prefix, fmt(desde))
    if hasta:
        return "%s hasta %s" % (prefix, fmt(hasta))
    return None


def fd_valid_date(value):
    """La fecha tal cual si es AAAA-MM-DD; si no, vacía (no revienta el dominio)."""
    value = (value or "").strip()
    from datetime import date
    try:
        date.fromisoformat(value)
    except ValueError:
        return ""
    return value


def fd_context(action, total, tags, clear_url, **extra):
    """Diccionario `fd` que leen las plantillas.

    Claves opcionales (extra):
      search       {"name", "value", "placeholder", "label"}
      hidden       [(nombre, valor)] que la barra superior conserva
      drawer_hidden[(nombre, valor)] que el cajón conserva (búsqueda, orden…)
      keep         nombres que «Limpiar» del cajón no vacía
      sort         {"name", "value", "options": [(valor, etiqueta)], "label"}
      view         {"key", "target", "path", "current", "lista_url", "grid_url"}
      count_url    ruta JSON del conteo en vivo (sin ella el botón dice «Aplicar»)
      noun         ("venta", "ventas")
      has_drawer   False si no hay ninguna sección que mostrar
      anchor       ancla de la lista (por defecto #listado)
    """
    fd = {
        "action": action,
        "total": total,
        "tags": tags,
        "clear_url": clear_url,
        "active": len(tags),
        "anchor": "#listado",
        "has_drawer": True,
        "keep": ["q", "sort", "order", "vista"],
    }
    fd.update(extra)
    noun = fd.get("noun") or ("resultado", "resultados")
    fd.setdefault("one", noun[0])
    fd.setdefault("many", noun[1])
    if fd.get("count_url"):
        fd.setdefault("apply_label", "Ver %s" % noun[1])
    else:
        fd.setdefault("apply_label", "Aplicar")
    return fd
