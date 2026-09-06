import base64

from odoo import api, fields, models
from odoo.tools import file_open


class Website(models.Model):
    _inherit = "website"

    # Marcar el sitio con un campo, y no por id o por nombre, permite moverlo o
    # renombrarlo sin romper nada.
    shrimp_is_verifier_site = fields.Boolean(
        string="Sitio de verificadores",
        help="Marca este sitio como la plataforma de los verificadores: su "
             "portada es la de verificación y las rutas del marketplace "
             "redirigen al sitio principal.",
    )

    def _shrimp_verifier_site(self):
        return self.sudo().search([("shrimp_is_verifier_site", "=", True)], limit=1)

    def _shrimp_main_site(self):
        return self.sudo().search([("shrimp_is_verifier_site", "=", False)], limit=1)

    # ------------------------------------------------------------------
    # Autoconfiguración de las dos plataformas (para "solo instalar").
    # Se llama desde data/site_config.xml en cada install/update. Es
    # IDEMPOTENTE y NO DESTRUCTIVA: solo crea lo que falta y no pisa la
    # configuración del cliente (dominios ya puestos, menús ya armados).
    # ------------------------------------------------------------------
    # Dominios por defecto para una instalación nueva. En otro entorno se
    # cambian una sola vez desde Sitio web › Configuración; el hook no los
    # vuelve a tocar si ya tienen valor.
    _SHRIMP_DOMINIO_PRINCIPAL = "http://localhost:8069"
    _SHRIMP_DOMINIO_VERIFICADORES = "http://verificadores.localhost:8069"

    # Árbol de menús del sitio de verificadores. Los nombres 'Verificaciones' y
    # 'Mi empresa' deben existir con hijos para que los dropdowns con estilo
    # (navbar_dropdown_inherit) los reconozcan y reemplacen.
    _SHRIMP_MENU_VERIFICADOR = [
        ("Mi bandeja", "/verificador/bandeja", 10, []),
        ("Verificaciones", "#", 20, [
            ("Verificaciones abiertas", "/verificador/bandeja?state=open"),
            ("En campo", "/verificador/bandeja?state=in_field"),
            ("Por dictaminar", "/verificador/bandeja?state=done"),
            ("Ya verificadas", "/verificador/bandeja?state=approved"),
            ("Todas", "/verificador/bandeja"),
        ]),
        ("Mi empresa", "#", 30, [
            ("Perfil y cuenta bancaria", "/verificador/perfil"),
            ("Mi equipo", "/verificador/tecnicos"),
            ("Mi acreditación", "/marketplace/mis-certificados"),
            ("Mis reportes", "/verificador/reportes"),
        ]),
    ]

    @api.model
    def _shrimp_ensure_config(self):
        W = self.env["website"].sudo()
        sitios = W.search([], order="id")
        if not sitios:
            return False

        # 1) Identificar (o crear) el sitio de verificadores.
        verif = W.search([("shrimp_is_verifier_site", "=", True)], limit=1)
        if not verif:
            verif = W.search([("name", "ilike", "verificador")], limit=1)
        if not verif:
            if len(sitios) == 1:
                verif = W.create({"name": "Trazul Verificadores"})
            else:
                verif = sitios[-1]

        # 2) Marcar el flag (uno y solo uno).
        W.search([("id", "!=", verif.id)]).write({"shrimp_is_verifier_site": False})
        if not verif.shrimp_is_verifier_site:
            verif.shrimp_is_verifier_site = True

        # 3) Dominios: solo si faltan (no pisar la config del cliente).
        principal = W.search([("id", "!=", verif.id)], order="id", limit=1)
        if principal and not principal.domain:
            principal.domain = self._SHRIMP_DOMINIO_PRINCIPAL
        if not verif.domain:
            verif.domain = self._SHRIMP_DOMINIO_VERIFICADORES

        # 4) Menú propio del verificador.
        self._shrimp_build_verifier_menu(verif)

        # 5) Un tema a medio instalar tumba el bundle CSS del sitio: quitarlo.
        for s in W.search([]):
            if s.theme_id and s.theme_id.state != "installed":
                s.theme_id = False
        return True

    def _shrimp_build_verifier_menu(self, verif):
        M = self.env["website.menu"].sudo()
        # Si ya está armado (existe 'Verificaciones'), no se toca: así se
        # respetan personalizaciones posteriores y no se rehace en cada update.
        if M.search_count([("website_id", "=", verif.id), ("name", "=", "Verificaciones")]):
            return
        raiz = M.search(
            [("website_id", "=", verif.id), ("parent_id", "=", False)], limit=1)
        if not raiz:
            return
        # Limpiar los menús por defecto que Odoo copió al crear el sitio.
        M.search([("id", "child_of", raiz.id), ("id", "!=", raiz.id)]).unlink()

        def crear(nombre, url, parent, seq):
            return M.create({
                "name": nombre, "url": url, "parent_id": parent,
                "sequence": seq, "website_id": verif.id,
            })

        for nombre, url, seq, hijos in self._SHRIMP_MENU_VERIFICADOR:
            padre = crear(nombre, url, raiz.id, seq)
            for i, (hn, hu) in enumerate(hijos):
                crear(hn, hu, padre.id, 10 + i * 10)

    # ------------------------------------------------------------------
    # Marca Trazul (logo y favicon)
    # ------------------------------------------------------------------
    # Los archivos viven en el módulo, no sueltos en el disco de un equipo:
    # así viajan con el código y una instalación nueva arranca con la marca
    # puesta en vez de con el "Your Logo" de Odoo.
    _SHRIMP_LOGO = "shrimp_verification/static/src/img/logo.png"
    _SHRIMP_FAVICON = "shrimp_verification/static/src/img/favicon.png"

    @api.model
    def _shrimp_ensure_brand(self):
        """Pone el logo y el favicon de Trazul donde todavía esté el de Odoo.

        NO DESTRUCTIVO: si el sitio o la compañía ya tienen una imagen propia
        —porque el cliente subió la suya— no se toca. Solo se reemplaza la
        que Odoo trae por defecto.
        """
        def _leer(ruta):
            try:
                with file_open(ruta, "rb") as f:
                    return base64.b64encode(f.read())
            except Exception:
                return False

        logo = _leer(self._SHRIMP_LOGO)
        favicon = _leer(self._SHRIMP_FAVICON)
        if not logo and not favicon:
            return False

        W = self.env["website"].sudo()
        for sitio in W.search([]):
            vals = {}
            # El logo por defecto de Odoo es un archivo concreto: comparando
            # contra él se distingue "nunca lo cambiaron" de "subieron el suyo".
            # Un campo vacío cuenta igual que el de Odoo: tampoco es del cliente.
            if logo and sitio.logo in (False, sitio._default_logo()):
                vals["logo"] = logo
            if favicon and sitio.favicon in (False, sitio._default_favicon()):
                vals["favicon"] = favicon
            if vals:
                sitio.write(vals)

        # La compañía lleva el logo a las facturas y a los informes PDF.
        if logo:
            for empresa in self.env["res.company"].sudo().search([]):
                if empresa.uses_default_logo:
                    empresa.logo = logo
        return True
