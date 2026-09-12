from odoo import api, models


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
