# -*- coding: utf-8 -*-
import base64
import hashlib
import io

from PIL import Image

from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.webmanifest import WebManifest


class WebManifestBranded(WebManifest):
    """Personaliza el manifest de la PWA (Aplicación Web Progresiva) del backend.

    De forma nativa Odoo instala la app con el nombre "Odoo" y el icono de Odoo.
    Aquí se usan los parámetros configurables del módulo (Ajustes > Tema):
      - Nombre del aplicativo (o el nombre de la empresa si se deja vacío).
      - Icono del aplicativo (o el logo de la empresa si no se sube ninguno).
      - Color de la barra: color principal del módulo, o un color propio.
    """

    def _get_webmanifest(self):
        manifest = super()._get_webmanifest()
        company = request.env.company
        icp = request.env["ir.config_parameter"].sudo()

        # --- Nombre ---
        pwa_name = icp.get_param("web_theme_launcher.pwa_name")
        # web.web_app_name (nativo) tiene prioridad si está definido.
        if not icp.get_param("web.web_app_name"):
            name = pwa_name or company.name
            manifest["name"] = name
            manifest["short_name"] = name

        # --- Color de la barra / tema ---
        use_primary = icp.get_param(
            "web_theme_launcher.pwa_use_primary_color", "True"
        )
        # Los parámetros del sistema se guardan como texto ("True"/"False").
        use_primary = str(use_primary).lower() not in ("false", "0", "")
        if use_primary:
            bar_color = (
                icp.get_param("web_theme_launcher.primary_color") or "#123e5c"
            )
        else:
            bar_color = icp.get_param("web_theme_launcher.pwa_bar_color")
        if bar_color:
            manifest["background_color"] = bar_color
            manifest["theme_color"] = bar_color

        # --- Icono ---
        icon_data = icp.get_param("web_theme_launcher.pwa_icon")
        if icon_data:
            # Versión derivada del contenido del icono: al cambiar el icono
            # cambia la URL y el navegador no reusa el cacheado.
            version = hashlib.sha1(icon_data.encode()).hexdigest()[:8]
            manifest["icons"] = [
                {
                    "src": "/web_theme_launcher/pwa_icon/%s?v=%s" % (size, version),
                    "sizes": size,
                    "type": "image/png",
                }
                for size in ("192x192", "512x512")
            ]
        elif company.logo:
            # Sin icono propio: se usa el logo de la empresa.
            manifest["icons"] = [
                {
                    "src": "/web/image/res.company/%s/logo/%s" % (company.id, size),
                    "sizes": size,
                    "type": "image/png",
                }
                for size in ("192x192", "512x512")
            ]
        return manifest

    @http.route(
        [
            "/web_theme_launcher/pwa_icon",
            "/web_theme_launcher/pwa_icon/<int:width>x<int:height>",
        ],
        type="http",
        auth="public",
        methods=["GET"],
    )
    def wtl_pwa_icon(self, width=None, height=None, **kw):
        """Sirve el icono de la PWA subido en Ajustes.

        Chrome exige que el icono de instalación sea CUADRADO. Como el PNG
        subido puede no serlo, se centra sobre un lienzo cuadrado transparente
        y se escala al tamaño exacto pedido (p. ej. 192x192, 512x512).
        """
        icp = request.env["ir.config_parameter"].sudo()
        data = icp.get_param("web_theme_launcher.pwa_icon")
        if not data:
            return request.not_found()

        img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
        # Lienzo cuadrado del lado mayor, con la imagen centrada.
        side = max(img.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
        # Escalar al tamaño solicitado (cuadrado garantizado).
        if width and height:
            resample = getattr(
                getattr(Image, "Resampling", Image), "LANCZOS", 1
            )
            canvas = canvas.resize((width, height), resample)

        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return request.make_response(
            out.getvalue(),
            [
                ("Content-Type", "image/png"),
                ("Cache-Control", "public, max-age=3600"),
            ],
        )
