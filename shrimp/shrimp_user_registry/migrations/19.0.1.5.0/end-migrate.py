"""19.0.1.5.0 — rutas de protección de datos en inglés.

/privacidad -> /privacy, /terminos -> /legal/terms, /my/datos -> /my/data,
/my/datos/solicitar -> /my/data/request (las viejas siguen como 301/308).
Las columnas nuevas (res.partner.shrimp_privacy_ip) las crea el ORM; aquí
solo se reescriben las URL guardadas (menús, redirecciones) con el mismo
procedimiento idempotente de 19.0.1.4.0, que usa la tabla completa de rutas
viejas.
"""
import importlib.util
import os


def migrate(cr, version):
    if not version:
        return
    ruta = os.path.join(os.path.dirname(os.path.dirname(__file__)), "19.0.1.4.0", "end-migrate.py")
    spec = importlib.util.spec_from_file_location("shrimp_ur_end_migrate_1940_reuse", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.migrate(cr, version)
