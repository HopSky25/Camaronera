"""Entradas de la barra superior que aporta el mundo empacadora.

Antes eran <li> inyectados por xpath en el desplegable «Operaciones»
(views/navbar_inherit.xml y views/reserva_navbar_inherit.xml). Ahora se
declaran aquí, con su capacidad, en la tabla única de
shrimp_marketplace/models/website_navbar.py:

* Camaronera (declare_harvest) — en «Vender»: Reservas de cosecha, Listas de
  precios recibidas, Empacadoras, Simulador de cosecha.
* Empacadora (issue_price_lists / commit_harvest) — en «Comprar»: Oferta
  disponible, Mis listas de precios, Cosechas ofrecidas, Rendimiento de
  proveedores. En «Mi cuenta»: Mi ficha pública.

«Mi historial verificado» de la camaronera pasa a Mi panel (tarjeta).
"""
from odoo import api, models


class Website(models.Model):
    _inherit = "website"

    @api.model
    def _shrimp_nav_entries(self, partner):
        entradas = super()._shrimp_nav_entries(partner)
        entradas += [
            # ===== Vender (camaronera) =====
            {"section": "sell", "key": "harvest_reservations", "sequence": 50,
             "label": "Reservas de cosecha",
             "url": "/marketplace/reservations", "icon": "fa-calendar-check-o", "tone": "green",
             "caps": ("declare_harvest",),
             "desc": "Declara tu próxima cosecha y mira quién se comprometió."},
            {"section": "sell", "key": "price_lists_received", "sequence": 60,
             "label": "Listas de precios recibidas",
             "url": "/marketplace/price-lists", "icon": "fa-list-alt", "tone": "blue",
             "caps": ("declare_harvest",),
             "desc": "Lo que paga cada empacadora por talla."},
            {"section": "sell", "key": "packers", "sequence": 70,
             "label": "Empacadoras",
             "url": "/marketplace/packers", "icon": "fa-industry", "tone": "amber",
             "caps": ("declare_harvest",),
             "desc": "El directorio de plantas a las que puedes vender."},
            {"section": "sell", "key": "harvest_simulator", "sequence": 80,
             "label": "Simulador de cosecha",
             "url": "/marketplace/simulator", "icon": "fa-sliders", "tone": "blue",
             "caps": ("declare_harvest",),
             "desc": "¿Cosechar ahora o esperar? Con los precios de hoy."},

            # ===== Comprar (empacadora) =====
            {"section": "buy", "key": "offer", "sequence": 5,
             "label": "Oferta disponible",
             "url": "/marketplace/supply", "icon": "fa-cubes", "tone": "green",
             "caps": ("issue_price_lists",),
             "desc": "Los lotes a la venta cruzados con tus precios."},
            {"section": "buy", "key": "price_lists", "sequence": 20,
             "label": {"empacadora": "Mis listas de precios", None: "Listas de precios"},
             "url": "/marketplace/price-lists", "icon": "fa-list-alt", "tone": "blue",
             "caps": ("issue_price_lists",), "untyped": True,
             "desc": "Publica y compara tus precios por talla."},
            {"section": "buy", "key": "harvests_offered", "sequence": 40,
             "label": "Cosechas ofrecidas",
             "url": "/packer/reservations", "icon": "fa-handshake-o", "tone": "teal",
             "caps": ("commit_harvest",),
             "desc": "Las cosechas que te ofrecen por adelantado."},
            {"section": "buy", "key": "supplier_ranking", "sequence": 80,
             "label": "Rendimiento de proveedores",
             "url": "/marketplace/suppliers", "icon": "fa-trophy", "tone": "amber",
             "caps": ("issue_price_lists",),
             "desc": "El rendimiento verificado de quienes te venden."},
        ]
        if partner and partner.sudo().uuid_ref:
            entradas.append(
                {"section": "account", "key": "packer_profile", "sequence": 30,
                 "label": "Mi ficha pública",
                 # La ficha pública de la empacadora es /marketplace/packers/<ref>
                 # (shrimp_packer/controllers/main.py: packer_profile). Antes
                 # apuntaba a la vitrina de vendedor, que responde 404 a quien
                 # no vende (la empacadora pura no tiene «sell_products»).
                 "url": "/marketplace/packers/%s" % partner.sudo()._shrimp_role_holder().uuid_ref,
                 "icon": "fa-eye", "tone": "teal",
                 "caps": ("issue_price_lists",)})
        return entradas
