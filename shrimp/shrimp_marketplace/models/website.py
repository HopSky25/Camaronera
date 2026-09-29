import base64

from odoo import api, models
from odoo.tools import file_open
from odoo.tools.image import image_process


class Website(models.Model):
    _inherit = "website"

    # ------------------------------------------------------------------
    # Alineación de los menús del portal
    # ------------------------------------------------------------------
    # La barra superior convierte tres menús —"Productos", "Operaciones" y
    # "Mi cuenta"— en desplegables con estilo, y los reconoce por el NOMBRE
    # literal del menú (views/navbar_dropdown.xml). Ese acoplamiento es
    # frágil por un motivo concreto de Odoo:
    #
    # los website.menu de cada sitio NO son los registros del módulo. Odoo
    # saca una copia al crear el sitio, y desde ese momento la copia deja de
    # seguir al XML: cambiar views/website_menu.xml no la actualiza.
    #
    # Cuando el antiguo menú único "Mi panel" se dividió en los tres actuales,
    # el registro del módulo se renombró pero la copia del sitio se quedó con
    # el nombre viejo apuntando a /my. Resultado: el desplegable "Productos"
    # no coincidía y no se renderizaba PARA NINGÚN ROL, y con él desaparecían
    # de la barra sus seis entradas —Publicar producto, Mis productos,
    # Marketplace, Mi inventario, Instalaciones y piscinas y Solicitudes de
    # chequeo—, o sea todo el trabajo del vendedor. Solo se llegaba
    # escribiendo la URL a mano.
    #
    # Esto se arreglaba con un script que había que ejecutar a mano, así que
    # el arreglo no viajaba con el código: quien tomara los cambios seguía sin
    # ver las opciones. Ahora corre solo en cada instalación y actualización.
    _SHRIMP_MENU_PORTAL = {
        "/marketplace": "Marketplace",
        "/marketplace/products": "Productos",
        "/marketplace/compras": "Operaciones",
        "/marketplace/mi-cuenta": "Mi cuenta",
    }

    # Nombres viejos y la URL a la que quedaron apuntando, para reconocer una
    # copia desalineada sin depender de su id, que cambia en cada base.
    _SHRIMP_MENU_RENOMBRADOS = {
        "Mi panel": ("/my", "/marketplace/products"),
    }

    @api.model
    def _shrimp_alinear_menus_portal(self):
        """Devuelve los menús de cada sitio de portal a su nombre y URL.

        Es idempotente: se puede ejecutar las veces que haga falta y no toca
        nada si ya están bien. No borra ni crea menús —solo corrige los que
        reconoce— para no pisar los que haya añadido el cliente.
        """
        M = self.env["website.menu"].sudo()
        arreglados = 0
        for sitio in self.env["website"].sudo().search([]):
            # El sitio de verificadores tiene su propio árbol, construido por
            # shrimp_verification: no se toca.
            if getattr(sitio, "shrimp_is_verifier_site", False):
                continue
            for menu in M.search([("website_id", "=", sitio.id),
                                  ("parent_id", "!=", False)]):
                esperado = self._SHRIMP_MENU_PORTAL.get(menu.url)
                if esperado and menu.name != esperado:
                    menu.name = esperado
                    arreglados += 1
                    continue
                arreglo = self._SHRIMP_MENU_RENOMBRADOS.get(menu.name)
                if arreglo and menu.url == arreglo[0]:
                    destino = arreglo[1]
                    menu.write({"name": self._SHRIMP_MENU_PORTAL[destino],
                                "url": destino})
                    arreglados += 1
        return arreglados

    # ------------------------------------------------------------------
    # Marca del sitio principal
    # ------------------------------------------------------------------
    # El logo del marketplace lo sembraba shrimp_verification, que es el
    # módulo de la otra plataforma: la marca del sitio principal dependía de
    # que estuviera instalado el de verificadores. Cada módulo lleva ahora la
    # suya, en su propia carpeta static/description:
    #
    #   icon.png         icono del módulo en Aplicaciones (lo coge Odoo solo)
    #   logo.png         el de la barra de título del sitio
    #   icon_circle.png  el favicon de la pestaña del navegador
    #   favicon.png      la marca sin fondo (no la usa Odoo directamente)
    #
    # Los ficheros del módulo son la FUENTE DE LA VERDAD: al actualizar, el
    # sitio se resincroniza con ellos. Antes solo se escribía si el sitio
    # tenía todavía el logo por defecto de Odoo, y el efecto era que sustituir
    # el PNG no cambiaba nada y había que tocar la base a mano.
    _SHRIMP_MP_LOGO = "shrimp_marketplace/static/description/logo.png"
    _SHRIMP_MP_FAVICON = "shrimp_marketplace/static/description/icon_circle.png"

    @api.model
    def _shrimp_leer_imagen(self, ruta):
        try:
            with file_open(ruta, "rb") as f:
                return base64.b64encode(f.read())
        except Exception:
            return False

    @api.model
    def _shrimp_favicon_procesado(self, datos):
        """El favicon tal y como quedará guardado.

        website._handle_favicon() lo recorta a un ICO de 256x256 al escribir,
        así que comparar contra el PNG original daría siempre distinto y se
        reescribiría en cada actualización.
        """
        try:
            return base64.b64encode(image_process(
                base64.b64decode(datos), size=(256, 256),
                crop="center", output_format="ICO"))
        except Exception:
            return False

    # Banderas con las que las OTRAS plataformas marcan su sitio. Las definen
    # módulos que dependen de este (shrimp_verification, shrimp_copacking), así
    # que aquí no se puede dar por hecho que existan: se comprueban una a una.
    # Esta tupla es además la lista blanca de nombres que se interpolan en el
    # SQL de abajo; no se consulta ningún nombre que no esté aquí.
    _SHRIMP_FLAGS_OTRAS_PLATAFORMAS = (
        "shrimp_is_verifier_site",
        "shrimp_is_copacker_site",
    )

    def _shrimp_sitios_marcados(self, campo):
        """Ids de los sitios con el flag `campo` activo, exista o no el campo.

        Devuelve un set vacío si el módulo que define el flag no está
        instalado.
        """
        if campo not in self._SHRIMP_FLAGS_OTRAS_PLATAFORMAS:
            raise ValueError("Flag de plataforma desconocido: %s" % campo)
        W = self.env["website"].sudo()
        if campo in W._fields:
            return set(W.search([(campo, "=", True)]).ids)

        # El campo no está en el registro, pero la columna puede existir ya en
        # la base: se consulta directamente. Sin esto, al actualizar, TODOS los
        # sitios parecían el principal y el logo del marketplace pisaba también
        # el de la otra plataforma.
        self.env.cr.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_name = 'website'
               AND column_name = %s
        """, (campo,))
        if not self.env.cr.fetchone():
            return set()
        self.env.cr.execute(
            'SELECT id FROM website WHERE "%s" IS TRUE' % campo)
        return {fila[0] for fila in self.env.cr.fetchall()}

    def _shrimp_sitios_principales(self):
        """Los sitios del marketplace: ni el de verificadores ni el de empaque.

        Cada plataforma lleva su propia marca. Cuando aquí solo se descartaba
        el sitio de verificadores, el de empaque contaba como principal y en
        cada actualización recibía el logo y el favicon del marketplace, o sea
        que el maquilador entraba a un sitio con la marca de otra cosa.
        """
        W = self.env["website"].sudo()
        todos = W.search([], order="id")
        excluidos = set()
        for campo in self._SHRIMP_FLAGS_OTRAS_PLATAFORMAS:
            excluidos |= self._shrimp_sitios_marcados(campo)
        if not excluidos:
            return todos
        return todos.filtered(lambda s: s.id not in excluidos)

    @api.model
    def _shrimp_ensure_marketplace_brand(self):
        """Sincroniza logo y favicon del sitio principal con los del módulo.

        Es idempotente: solo escribe cuando el sitio difiere del fichero, así
        que actualizar el módulo dos veces seguidas no toca nada la segunda.
        """
        logo = self._shrimp_leer_imagen(self._SHRIMP_MP_LOGO)
        favicon = self._shrimp_leer_imagen(self._SHRIMP_MP_FAVICON)
        favicon_final = self._shrimp_favicon_procesado(favicon) if favicon else False

        cambiados = 0
        for sitio in self._shrimp_sitios_principales():
            vals = {}
            if logo and sitio.logo != logo:
                vals["logo"] = logo
            if favicon_final and sitio.favicon != favicon_final:
                vals["favicon"] = favicon
            if vals:
                sitio.write(vals)
                cambiados += 1
        return cambiados
