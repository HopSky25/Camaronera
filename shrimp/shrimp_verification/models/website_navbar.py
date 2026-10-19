"""El verificador en la barra del marketplace (sitio principal).

Antes, para el verificador, el desplegable «Productos» se sustituía por uno
propio (plantilla submenu_mi_panel_verificador, por xpath). Con la barra
nueva sus accesos van en «Servicios ▾ > Verificación» y su cuenta en
«Mi cuenta ▾». El sitio de verificadores tiene su propio menú
(verifier_jobs / verifier_company) y no cambia.
"""
from odoo import api, models

# Trabajo del verificador: UNA lista para el desplegable «Verificaciones»
# del sitio de verificadores (views/navbar_dropdown_inherit.xml), las
# tarjetas de su panel (/my, views/portal_home_inherit.xml) y «Servicios ▾ >
# Verificación» del marketplace. Antes eran tres listas distintas («Por
# iniciar» / «Compras por verificar» ?state=assigned / «Verificaciones
# abiertas» ?state=open). Los filtros son los de /verifier/inbox
# (controllers/main.py: estados del modelo + los atajos open / pending).
# (clave, texto, url, icono, tono, estados que cuenta, descripción)
SHRIMP_VERIFIER_JOBS = [
    ("pending", "Por iniciar", "/verifier/inbox?state=pending", "fa-hourglass-start", "teal",
     ("received", "assigned"),
     "Órdenes recibidas o asignadas que todavía no has empezado. Ábrelas para "
     "iniciar el trabajo de campo."),
    ("in_field", "En campo", "/verifier/inbox?state=in_field", "fa-map-marker", "amber",
     ("in_field",),
     "Inspecciones en curso. Aquí llenas el informe: peso, cuerpo o cola, "
     "metabisulfito, clasificación y sabor."),
    ("done", "Por dictaminar", "/verifier/inbox?state=done", "fa-gavel", "navy",
     ("done",),
     "Informes completos a la espera de tu veredicto: aprobar, aprobar con "
     "observaciones o rechazar."),
    ("approved", "Ya verificadas", "/verifier/inbox?state=approved", "fa-check-circle", "green",
     ("approved",),
     "Verificaciones aprobadas. Consulta el informe y copia el parte para WhatsApp."),
    ("rejected", "Rechazadas", "/verifier/inbox?state=rejected", "fa-times-circle", "coral",
     ("rejected",),
     "Lotes que no pasaron la inspección y cuya compra se canceló."),
    ("all", "Todas mis verificaciones", "/verifier/inbox", "fa-list", "blue",
     (),
     "La bandeja completa, con buscador por referencia, lote o producto."),
]


class Website(models.Model):
    _inherit = "website"

    @api.model
    def _shrimp_verifier_jobs(self):
        """[{key, label, url, icon, tone, states, desc}] — ver
        SHRIMP_VERIFIER_JOBS."""
        return [{"key": k, "label": l, "url": u, "icon": i, "tone": t, "states": st, "desc": d}
                for k, l, u, i, t, st, d in SHRIMP_VERIFIER_JOBS]

    @api.model
    def _shrimp_verifier_company_links(self, partner=None):
        """«Mi empresa» del verificador (desplegable del sitio de
        verificadores y tarjetas de su panel): el técnico (no admin) ve el
        perfil en solo lectura y no ve «Mi equipo» (esa ruta le da 403).
        «Mi acreditación» va directo a la sección de certificados de «Mi
        cuenta» (/marketplace/my-certificates solo redirige allí)."""
        if partner is None:
            partner = self.env.user.shrimp_actor_partner_id
        partner = partner.sudo() if partner else partner
        admin = bool(partner) and partner.shrimp_is_verifier_admin()
        res = [
            {"key": "verifier_profile", "url": "/verifier/profile", "icon": "fa-id-badge", "tone": "teal",
             "label": "Perfil y cuenta bancaria" if admin else "Perfil de la empresa",
             "desc": "Los datos de tu empresa y la cuenta donde cobras."},
        ]
        if admin:
            res.append({"key": "verifier_team", "url": "/verifier/technicians", "icon": "fa-users",
                        "tone": "blue", "label": "Mi equipo",
                        "desc": "Da de alta a los técnicos que van a campo y reparte el trabajo entre ellos."})
        res += [
            {"key": "verifier_accreditation", "url": "/marketplace/my-account#certificados", "icon": "fa-certificate",
             "tone": "coral", "label": "Mi acreditación",
             "desc": "Consulta o renueva el documento que te autoriza a verificar."},
            {"key": "verifier_reports", "url": "/verifier/reports", "icon": "fa-bar-chart",
             "tone": "green", "label": "Mis reportes",
             "desc": "Tus verificaciones y lo que cobraste, en gráficas."},
        ]
        return res

    @api.model
    def _shrimp_nav_entries(self, partner):
        entradas = super()._shrimp_nav_entries(partner)
        for i, job in enumerate(self._shrimp_verifier_jobs()):
            entradas.append(
                {"section": "services", "key": "verify_%s" % job["key"], "sequence": 100 + i,
                 "label": job["label"], "url": job["url"], "icon": job["icon"], "tone": job["tone"],
                 "caps": ("verify",), "group": "Verificación", "desc": job["desc"]})
        return entradas
