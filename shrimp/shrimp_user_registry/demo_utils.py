# -*- coding: utf-8 -*-
"""Demo de los módulos shrimp en bases reinstaladas.

PROBLEMA. Odoo solo carga la demo de un módulo si TODAS sus dependencias
tienen demo (ModuleNode.demo_installable). Si una dependencia estándar se
quedó sin demo (p. ej. sale_management, cuya demo falló en una base vieja),
Odoo instala el módulo SIN su demo y sin decir nada, pero deja en
ir_module_module.demo el valor que ya tenía la fila (True en una base con
demo, también tras desinstalar y reinstalar). Los módulos de más abajo ven
ese True, intentan cargar su demo y fallan al primer ref() a un registro de
demo que no existe (shrimp_marketplace.demo_cert_doc_1...). Fue lo que pasó
en «Pruebas» al reinstalar la plataforma.

SOLUCIÓN. Antes de instalar un módulo shrimp, `cargar_demo_pendiente`
revisa sus dependencias shrimp ya instaladas: si una figura con demo pero su
demo no está (falta el último registro de su último archivo de demo), la
carga en ese momento, con el mismo modo y la misma semántica que Odoo (init,
noupdate) y dentro de un savepoint. Si aun así falla, pone su demo=False
para que lo de abajo no lo intente sobre datos que no existen.
"""
import logging
import sys

from lxml import etree

from odoo.modules.module import get_manifest
from odoo.tools import file_path
from odoo.tools.convert import convert_file

_logger = logging.getLogger(__name__)


def _shrimp_deps(nombre, vistos=None):
    """Dependencias shrimp_* (transitivas) de `nombre`, en orden de carga."""
    vistos = vistos if vistos is not None else []
    for dep in get_manifest(nombre).get("depends", []):
        if dep.startswith("shrimp_") and dep not in vistos:
            _shrimp_deps(dep, vistos)
            vistos.append(dep)
    return vistos


def _centinela(nombre):
    """xml id calificado del ÚLTIMO registro del último archivo de demo, o
    None si el módulo no tiene demo. Si existe, la demo se cargó entera."""
    demo = [f for f in get_manifest(nombre).get("demo", []) if f.endswith(".xml")]
    if not demo:
        return None
    raiz = etree.parse(file_path("%s/%s" % (nombre, demo[-1]))).getroot()
    ids = [n.get("id") for n in raiz.iter("record", "template", "menuitem") if n.get("id")]
    if not ids:
        return None
    return ids[-1] if "." in ids[-1] else "%s.%s" % (nombre, ids[-1])


def _columnas_que_faltan(env):
    """Modelos con algún campo almacenado cuya columna no existe todavía.

    Pasa cuando el código de un módulo ya instalado trae campos nuevos y no
    se actualizó (-u) antes de instalar otro: Odoo no actualiza las
    dependencias ya instaladas. Cargar demo sobre esas tablas fallaría en el
    primer SELECT."""
    env.cr.execute("SELECT table_name, column_name FROM information_schema.columns "
                   "WHERE table_schema = current_schema()")
    columnas = set(env.cr.fetchall())
    tablas = {t for t, _c in columnas}
    faltan = []
    for nombre, modelo in env.registry.items():
        if modelo._abstract or not modelo._auto or modelo._table not in tablas:
            continue
        if any(f.store and f.column_type and (modelo._table, f.name) not in columnas
               for f in modelo._fields.values()):
            faltan.append(nombre)
    return faltan


def cargar_demo_pendiente(env, modulo):
    """Carga la demo que falta de las dependencias shrimp de `modulo`.

    Pensado para el pre_init_hook de cada módulo shrimp. No hace nada en una
    base sin demo (las filas tienen demo=False) ni cuando la demo ya está.
    """
    Module = env["ir.module.module"].sudo()
    pendientes = []
    for dep in _shrimp_deps(modulo):
        fila = Module.search([("name", "=", dep), ("state", "=", "installed")], limit=1)
        if not fila or not fila.demo:
            continue
        centinela = _centinela(dep)
        if not centinela or env.ref(centinela, raise_if_not_found=False):
            continue
        pendientes.append((dep, fila, centinela))
    if pendientes:
        faltan = _columnas_que_faltan(env)
        if faltan:
            _logger.warning(
                "Columnas sin crear en %s (código nuevo de un módulo instalado sin -u): se "
                "crean ahora para poder cargar la demo. Actualiza los módulos shrimp "
                "instalados (-u) junto con esta instalación.", ", ".join(sorted(faltan)))
            env.registry.init_models(env.cr, faltan, {}, install=False)
    for dep, fila, centinela in pendientes:
        _logger.warning(
            "%s figura instalado con demo pero su demo no está cargada (falta %s): "
            "se carga ahora, antes de instalar %s.", dep, centinela, modulo)
        demo_env = env(su=True, context=dict(env.context, install_demo=True))
        idref = {}
        try:
            # flush=True: la caché queda limpia si algo falla (ver
            # _shrimp_set_bank_account en shrimp_verification).
            with env.cr.savepoint():
                for fichero in get_manifest(dep).get("demo", []):
                    _logger.info("loading %s/%s", dep, fichero)
                    convert_file(demo_env, dep, fichero, idref, mode="init", noupdate=True)
                gancho = getattr(sys.modules.get("odoo.addons.%s" % dep), "_shrimp_post_demo", None)
                if gancho:
                    gancho(env)
        except Exception:  # noqa: BLE001 - se instala igual, sin la demo de abajo
            _logger.warning("No se pudo cargar la demo pendiente de %s: los módulos que "
                            "dependen de él se instalarán sin demo.", dep, exc_info=True)
            env.invalidate_all()
            env.cr.execute("UPDATE ir_module_module SET demo = FALSE WHERE id = %s", (fila.id,))
            fila.invalidate_recordset(["demo"])
            return False
    return True
