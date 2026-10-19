# -*- coding: utf-8 -*-
"""«Mi panel» por rol (/my/dashboard): indicadores, alertas y accesos directos.

El panel se arma con datos y la plantilla solo los pinta, así que cada módulo
agrega lo suyo heredando este modelo (shrimp_verification: supervivencia
verificada; shrimp_packer: listas de precios, reservas y oferta; …) sin tocar
la plantilla. Estructura que devuelve _panel_data():

    {
      "role": "laboratorio", "role_label": "Laboratorio", "supported": True,
      "actions":   [{"label", "url", "icon", "primary"}],          # botones de la cabecera
                   # (los de la tabla única con «headline» + los que agregue un módulo)
      "alerts":    [{"level": warning|danger|info, "text", "url", "link_label"}],
      "kpis":      [{"key", "label", "value", "sub", "icon", "tone", "url"}],
      "sections":  [{"key", "title", "icon", "columns", "rows", "empty", "more_url"}],
      "shortcuts": [{"key", "label", "url", "icon", "tone", "section", "section_label"}],
                   # «Accesos»: SOLO de la tabla única de la barra
                   # (website._shrimp_nav_actions, models/website_navbar.py).
                   # No se agregan a mano: así barra y panel no se separan.
      "other_roles": [{"role", "label", "alerts"}],                 # multi-perfil
    }

Todo se calcula para el socio que pide el panel (nunca por parámetro), con
sudo y dominios filtrados por ese socio.
"""
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.http import request

PANEL_ROLES = ("semillero", "laboratorio", "camaronera", "empacadora")
# Estados de una compra que ya no están «en curso».
TX_CERRADAS = ("draft", "done", "cancel")
CERT_ALERT_DAYS = 30


def fmt_num(valor, dec=0):
    valor = valor or 0.0
    if dec == 0 or float(valor).is_integer():
        return "{:,.0f}".format(valor)
    return ("{:,.%df}" % dec).format(valor)


def fmt_money(valor):
    return "$ {:,.2f}".format(valor or 0.0)


class ShrimpDashboard(models.AbstractModel):
    _name = "shrimp.dashboard"
    _description = "Mi panel por rol"

    # ------------------------------------------------------------------
    # Perfil
    # ------------------------------------------------------------------
    @api.model
    def _panel_active_role(self, partner):
        partner = partner.sudo()
        if hasattr(partner, "_shrimp_active_role"):
            return partner._shrimp_active_role()
        if hasattr(partner, "_shrimp_effective_type"):
            return partner._shrimp_effective_type()
        return partner.shrimp_user_type

    @api.model
    def _panel_roles(self, partner):
        partner = partner.sudo()
        if hasattr(partner, "_shrimp_roles"):
            return list(partner._shrimp_roles())
        rol = self._panel_active_role(partner)
        return [rol] if rol else []

    @api.model
    def _panel_role_label(self, role):
        Partner = self.env["res.partner"]
        if hasattr(Partner, "_shrimp_type_label"):
            return Partner._shrimp_type_label(role)
        return dict(Partner._fields["shrimp_user_type"]._description_selection(self.env)).get(role, role)

    @api.model
    def _panel_supported(self, role):
        return role in PANEL_ROLES

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------
    @api.model
    def _panel_data(self, partner, role=None, with_others=True):
        partner = partner.sudo()
        role = role or self._panel_active_role(partner)
        data = {
            "partner": partner,
            "role": role,
            "role_label": self._panel_role_label(role) if role else _("sin perfil"),
            "supported": self._panel_supported(role),
            "actions": [], "alerts": [], "kpis": [], "sections": [], "shortcuts": [],
            "shortcut_groups": [],
            "other_roles": [],
        }
        if data["supported"]:
            builder = getattr(self, "_panel_%s" % role, None)
            if builder:
                builder(partner, data)
            self._panel_common(partner, role, data)
            # Cabecera y «Accesos» salen de la tabla única de la barra, con
            # el perfil ACTIVO (los resúmenes de otros perfiles no los usan).
            if role == self._panel_active_role(partner):
                data["actions"] = self._panel_headline_actions(partner) + data["actions"]
                data["shortcuts"] = self._panel_shortcuts(partner)
                grupos = []
                for sc in data["shortcuts"]:
                    if not grupos or grupos[-1]["section"] != sc["section"]:
                        grupos.append({"section": sc["section"], "label": sc["section_label"], "items": []})
                    grupos[-1]["items"].append(sc)
                data["shortcut_groups"] = grupos
        if with_others:
            for otro in self._panel_roles(partner):
                if otro == role or not self._panel_supported(otro):
                    continue
                resumen = self._panel_data(partner, otro, with_others=False)
                data["other_roles"].append({
                    "role": otro, "label": resumen["role_label"],
                    "alerts": resumen["alerts"][:3],
                })
        return data

    # ------------------------------------------------------------------
    # Bloques reutilizables
    # ------------------------------------------------------------------
    @api.model
    def _today(self):
        return fields.Date.context_today(self)

    @api.model
    def _month_start(self):
        hoy = self._today()
        return fields.Datetime.to_datetime(hoy.replace(day=1))

    @api.model
    def _month_domain(self):
        """Del mes en curso: por la fecha de venta (sold_date) y, si no la
        tiene, por la de creación."""
        inicio = self._month_start()
        return ["|", ("sold_date", ">=", inicio.date()),
                "&", ("sold_date", "=", False), ("create_date", ">=", inicio)]

    @api.model
    def _role_domain(self, field, role):
        return ["|", (field, "=", role), (field, "=", False)]

    @api.model
    def _seller_products(self, partner, role):
        return self.env["shrimp.product"].sudo().search([
            ("seller_partner_id", "=", partner.id), ("seller_role", "=", role),
            ("active", "=", True)], order="create_date desc")

    @api.model
    def _sales_domain(self, partner, role):
        return [("seller_partner_id", "=", partner.id)] + self._role_domain("seller_role", role)

    @api.model
    def _purchases_domain(self, partner, role):
        return [("buyer_partner_id", "=", partner.id)] + self._role_domain("buyer_role", role)

    @api.model
    def _uom_label(self, products, default="millares"):
        nombre = products[:1].uom_id.name
        return nombre or default

    @api.model
    def _seller_kpis(self, partner, role, data, unit_default="millares"):
        """Disponible publicado, reservado/por entregar, vendido en el mes y
        calificación. Devuelve los productos del rol (para otros bloques)."""
        Tx = self.env["shrimp.transaction"].sudo()
        productos = self._seller_products(partner, role)
        publicados = productos.filtered(lambda p: p.state == "published")
        con_stock = publicados.filtered(lambda p: p.available_qty > 0)
        uom = self._uom_label(con_stock or productos, unit_default)

        en_curso = Tx.search(self._sales_domain(partner, role) + [("state", "not in", TX_CERRADAS)])
        del_mes = Tx.search(self._sales_domain(partner, role) + [
            ("state", "in", ["confirmed", "done"])] + self._month_domain())

        data["kpis"] += [
            {"key": "available", "label": _("Disponible publicado"),
             "value": "%s %s" % (fmt_num(sum(con_stock.mapped("available_qty"))), uom),
             "sub": _("%s lotes publicados") % len(con_stock), "icon": "fa-cubes", "tone": "teal",
             "url": "/marketplace/products"},
            {"key": "reserved", "label": _("Reservado / por entregar"),
             "value": "%s %s" % (fmt_num(sum(en_curso.mapped("transaction_qty"))), uom),
             "sub": _("%s compras en curso") % len(en_curso), "icon": "fa-clock-o", "tone": "amber",
             "url": "/marketplace/sales"},
            {"key": "sold_month", "label": _("Vendido este mes"),
             "value": "%s %s" % (fmt_num(sum(del_mes.mapped("transaction_qty"))), uom),
             "sub": fmt_money(sum(del_mes.mapped("amount_total"))), "icon": "fa-line-chart",
             "tone": "green", "url": "/marketplace/sales"},
            {"key": "rating", "label": _("Calificación de clientes"),
             "value": ("★ %.1f" % partner.shrimp_rating_avg) if partner.shrimp_rating_count else "—",
             "sub": _("%s reseñas") % (partner.shrimp_rating_count or 0), "icon": "fa-star",
             "tone": "navy", "url": "/marketplace/sellers/%s" % partner.uuid_ref},
        ]
        borradores = productos.filtered(lambda p: p.state == "draft")
        if borradores:
            data["alerts"].append({
                "level": "warning", "key": "drafts",
                "text": _("%s lotes o corridas sin publicar (en borrador).") % len(borradores),
                "url": "/marketplace/products", "link_label": _("Revisar y publicar"),
            })
        return productos

    @api.model
    def _cert_alert_days(self):
        """Días de antelación del aviso de certificados por vencer (Ajustes ›
        CamaronMarket; shrimp_marketplace.cert_alert_days, 30 por defecto)."""
        return self.env["shrimp.settings"].get_int(
            "shrimp_marketplace.cert_alert_days", CERT_ALERT_DAYS, minimum=1)

    @api.model
    def _cert_alerts(self, partner, data, productos=None):
        """Certificados del socio y de sus lotes publicados que vencen en
        los próximos 30 días (o ya vencieron sin reemplazo)."""
        hoy = self._today()
        tope = hoy + timedelta(days=self._cert_alert_days())
        UserLine = self.env["shrimp.user.certificate.line"].sudo()
        lineas = UserLine.search([
            ("partner_id", "=", partner.id), ("status", "=", "approved"),
            ("expiry_date", "!=", False), ("expiry_date", "<=", tope)], order="expiry_date asc")
        vigentes = UserLine.search([
            ("partner_id", "=", partner.id), ("status", "=", "approved"),
            "|", ("expiry_date", "=", False), ("expiry_date", ">", tope)])
        renovados = set(vigentes.mapped("certificate_id").ids)
        avisados = set()
        for l in lineas:
            if l.certificate_id.id in renovados or l.certificate_id.id in avisados:
                continue
            avisados.add(l.certificate_id.id)
            dias = (l.expiry_date - hoy).days
            if dias < 0:
                texto = _("Tu certificado %(c)s venció el %(f)s.")
                nivel = "danger"
            else:
                texto = _("Tu certificado %(c)s vence en %(d)s días (%(f)s).")
                nivel = "warning"
            data["alerts"].append({
                "level": nivel, "key": "cert_%s" % l.id,
                "text": texto % {"c": l.certificate_id.name, "d": dias,
                                 "f": l.expiry_date.strftime("%d/%m/%Y")},
                "url": "/marketplace/my-account", "link_label": _("Renovar"),
            })
        n_lotes = 0
        if productos:
            publicados = productos.filtered(lambda p: p.state == "published")
            if publicados:
                por_vencer = self.env["shrimp.product.certificate.line"].sudo().search([
                    ("product_id", "in", publicados.ids), ("status", "=", "approved"),
                    ("expiry_date", "!=", False), ("expiry_date", "<=", tope)])
                n_lotes = len(por_vencer.mapped("product_id"))
                if n_lotes:
                    data["alerts"].append({
                        "level": "warning", "key": "lot_certs",
                        "text": _("%(n)s lotes publicados tienen certificados (PCR, Agrocalidad…) "
                                  "vencidos o por vencer en %(d)s días.") % {
                                      "n": n_lotes, "d": self._cert_alert_days()},
                        "url": "/marketplace/products", "link_label": _("Ver lotes"),
                    })
        return n_lotes

    @api.model
    def _active_lots_section(self, partner, productos, title=None):
        publicados = productos.filtered(lambda p: p.state == "published" and p.available_qty > 0)[:8]
        pcr_ids = publicados._shrimp_pcr_valid_ids() if publicados else set()
        filas = []
        for p in publicados:
            ficha = p._shrimp_latest_quality()
            filas.append({
                "cells": [
                    p.name, p.stage_id.name or "—", p.genetics_line_id.name or "—",
                    "%s %s" % (fmt_num(p.available_qty), p.uom_id.name or ""),
                    "$%.2f / %s" % (p.price, p.uom_id.name or ""),
                    _("Vigente") if p.id in pcr_ids else "—",
                    ficha.sample_date.strftime("%d/%m/%Y") if ficha else _("Sin ficha"),
                ],
                "url": "/marketplace/product/%s" % p.uuid_ref,
                "button": {"label": _("Ficha de calidad"),
                           "url": "/marketplace/products/%s/quality" % p.uuid_ref}
                if p._shrimp_accepts_quality() else None,
            })
        return {
            "key": "active_lots", "title": title or _("Lotes activos"), "icon": "fa-list",
            "columns": [_("Lote"), _("Estadío"), _("Línea genética"), _("Disponible"),
                        _("Precio"), _("PCR"), _("Ficha de calidad")],
            "rows": filas, "empty": _("No tienes lotes publicados con stock."),
            "more_url": "/marketplace/products", "more_label": _("Ver todos mis lotes"),
        }

    @api.model
    def _last_sales_section(self, partner, role):
        Tx = self.env["shrimp.transaction"].sudo()
        ventas = Tx.search(self._sales_domain(partner, role) + [("state", "not in", ["draft", "cancel"])],
                           order="create_date desc", limit=500)
        ventas = ventas.sorted(lambda t: (t.sold_date or t.create_date.date(), t.id), reverse=True)[:5]
        estados = dict(Tx._fields["state"]._description_selection(self.env))
        filas = [{
            "cells": [
                (tx.sold_date or tx.create_date).strftime("%d/%m/%Y") if (tx.sold_date or tx.create_date) else "",
                tx.buyer_partner_id.name or "—", tx.product_id.name or "—",
                "%s %s" % (fmt_num(tx.transaction_qty), tx.product_id.uom_id.name or ""),
                fmt_money(tx.amount_total), estados.get(tx.state, tx.state or ""),
            ],
            "url": "/marketplace/purchases/%s/traceability" % tx.uuid_ref,
        } for tx in ventas]
        return {
            "key": "last_sales", "title": _("Últimas ventas"), "icon": "fa-handshake-o",
            "columns": [_("Fecha"), _("Comprador"), _("Lote"), _("Cantidad"), _("Total"), _("Estado")],
            "rows": filas, "empty": _("Todavía no tienes ventas."),
            "more_url": "/marketplace/sales", "more_label": _("Ver todas las ventas"),
        }

    @api.model
    def _quality_survival_kpi(self, productos, data):
        """Supervivencia publicada (lo que dice el lote) frente a la de la
        prueba de estrés de su última ficha de calidad, si hay fichas."""
        publicados = productos.filtered(lambda p: p.state == "published" and p.survival_rate)
        if not publicados:
            return
        publicada = sum(publicados.mapped("survival_rate")) / len(publicados)
        fichas = [p._shrimp_latest_quality() for p in publicados]
        estres = [f.stress_test_survival for f in fichas if f and f.stress_test_survival]
        sub = (_("prueba de estrés: %.0f %% (%s fichas)") % (sum(estres) / len(estres), len(estres))
               if estres else _("sin fichas de calidad todavía"))
        data["kpis"].append({
            "key": "survival_published", "label": _("Supervivencia publicada"),
            "value": "%.0f %%" % publicada, "sub": sub, "icon": "fa-heartbeat", "tone": "coral",
            "url": "/marketplace/products",
        })

    # ------------------------------------------------------------------
    # Accesos comunes a todos los perfiles
    # ------------------------------------------------------------------
    @api.model
    def _panel_nav_partner(self, partner):
        """El socio con el que la BARRA se calcula para este panel: el del
        usuario de la petición si es la misma cuenta (un subusuario navega
        con su contacto, el panel con la empresa); si no, `partner`."""
        partner = partner.sudo()
        if request:
            propio = self.env["website"]._shrimp_nav_partner()
            if propio and propio._shrimp_role_holder() == partner._shrimp_role_holder():
                return propio
        return partner

    @api.model
    def _panel_shortcuts(self, partner):
        """«Accesos» de Mi panel: la tabla única de la barra (surface
        "dashboard"), filtrada con la matriz de capacidades, el perfil activo
        y los permisos del subusuario. Agrupados Vender · Comprar ·
        Servicios · Más herramientas · Mi cuenta."""
        return self.env["website"]._shrimp_nav_actions(
            self._panel_nav_partner(partner), surface="dashboard")

    @api.model
    def _panel_headline_actions(self, partner):
        return [{"key": a["key"], "label": a["label"], "url": a["url"],
                 "icon": "fa-plus" if a["key"] == "publish" else "fa-shopping-cart",
                 "primary": a["primary"]}
                for a in self.env["website"]._shrimp_nav_headline(self._panel_nav_partner(partner))]

    # ------------------------------------------------------------------
    # Común a todos los perfiles
    # ------------------------------------------------------------------
    @api.model
    def _panel_common(self, partner, role, data):
        """Gancho para lo común a todos los perfiles. Los accesos ya no se
        declaran aquí: vienen de la tabla única (ver _panel_shortcuts)."""
        return data

    # ------------------------------------------------------------------
    # Semillero
    # ------------------------------------------------------------------
    @api.model
    def _panel_semillero(self, partner, data):
        productos = self._seller_kpis(partner, "semillero", data)
        n_lotes = self._cert_alerts(partner, data, productos)
        data["kpis"].append({
            "key": "cert_lots", "label": _("Lotes con certificado por vencer"),
            "value": str(n_lotes), "sub": _("PCR, Agrocalidad… en %s días") % self._cert_alert_days(),
            "icon": "fa-certificate", "tone": "amber" if n_lotes else "navy",
            "url": "/marketplace/products"})
        self._semillero_pl_survival(partner, data)
        data["sections"] += [
            self._active_lots_section(partner, productos),
            self._last_sales_section(partner, "semillero"),
        ]

    @api.model
    def _semillero_pl_survival(self, partner, data):
        """Supervivencia a PL que publican los laboratorios con el nauplio de
        este semillero (lotes que nacieron de sus ventas)."""
        Tx = self.env["shrimp.transaction"].sudo()
        ventas = Tx.search(self._sales_domain(partner, "semillero") + [
            ("state", "in", ["confirmed", "done"]), ("result_product_id", "!=", False)])
        resultados = ventas.mapped("result_product_id").filtered(
            lambda p: p.survival_rate and p.stage_id and p.stage_id._shrimp_tipo() == "larva")
        if not resultados:
            return
        prom = sum(resultados.mapped("survival_rate")) / len(resultados)
        data["kpis"].append({
            "key": "pl_survival", "label": _("Supervivencia a PL (labs)"),
            "value": "%.0f %%" % prom, "sub": _("%s lotes de tus clientes") % len(resultados),
            "icon": "fa-heartbeat", "tone": "coral", "url": False,
        })

    # ------------------------------------------------------------------
    # Laboratorio
    # ------------------------------------------------------------------
    @api.model
    def _panel_laboratorio(self, partner, data):
        productos = self._seller_kpis(partner, "laboratorio", data)
        self._quality_survival_kpi(productos, data)
        inventario = self._nauplio_inventory(partner)
        uom = self._uom_label(inventario.mapped("product_id"))
        data["kpis"].append({
            "key": "nauplio_stock", "label": _("Nauplio en inventario"),
            "value": "%s %s" % (fmt_num(sum(inventario.mapped("available_qty"))), uom),
            "sub": _("%s lotes comprados") % len(inventario), "icon": "fa-flask", "tone": "navy",
            "url": "/marketplace/my-lots"})
        n_lotes = self._cert_alerts(partner, data, productos)
        if n_lotes:
            data["kpis"].append({
                "key": "cert_lots", "label": _("Lotes con certificado por vencer"),
                "value": str(n_lotes), "sub": _("PCR, Agrocalidad… en %s días") % self._cert_alert_days(),
                "icon": "fa-certificate", "tone": "amber", "url": "/marketplace/products"})
        sin_publicar = inventario.filtered(
            lambda l: l.product_id.seller_partner_id == partner and l.product_id.state == "draft")
        if sin_publicar:
            data["alerts"].append({
                "level": "info", "key": "nauplio_unpublished",
                "text": _("Tienes %s lotes de nauplio comprados cuya larva aún no publicas.") % len(sin_publicar),
                "url": "/my/dashboard#nauplio_inventory", "link_label": _("Publicar larva"),
            })
        data["sections"] += [
            self._nauplio_section(partner, inventario),
            self._active_lots_section(partner, productos, title=_("Lotes y corridas publicados")),
            self._last_sales_section(partner, "laboratorio"),
        ]

    @api.model
    def _nauplio_inventory(self, partner):
        """Lotes de nauplio que el laboratorio compró y todavía tiene."""
        lotes = self.env["shrimp.stock.lot"].sudo().search([
            ("owner_id", "=", partner.id), ("available_qty", ">", 0),
            ("origin_move_id", "!=", False)], order="id desc")

        def es_nauplio(lote):
            tx = lote.origin_move_id.transaction_id
            if tx and tx.transaction_type == "semillero_to_laboratorio":
                return True
            return lote.product_id._shrimp_tipo() == "nauplio"
        return lotes.filtered(es_nauplio)

    @api.model
    def _nauplio_section(self, partner, inventario):
        filas = []
        # Primero los que aún no se publican: son los que piden acción.
        inventario = inventario.sorted(lambda l: (l.product_id.state != "draft", -l.id))
        for lote in inventario[:10]:
            prod = lote.product_id
            origen = lote.origin_move_id.source_partner_id or lote.origin_move_id.transaction_id.seller_partner_id
            propio = prod.seller_partner_id == partner
            if propio and prod.state == "draft":
                boton = {"label": _("Publicar larva de este lote"),
                         "url": "/marketplace/products/%s/edit" % prod.uuid_ref, "primary": True}
            elif propio:
                boton = {"label": _("Ver lote publicado"), "url": "/marketplace/product/%s" % prod.uuid_ref}
            else:
                boton = {"label": _("Publicar larva"), "url": "/marketplace/products/new"}
            filas.append({
                "cells": [
                    prod.name, origen.name or "—", prod.genetics_line_id.name or "—",
                    "%s %s" % (fmt_num(lote.available_qty), lote.uom_id.name or prod.uom_id.name or ""),
                    (lote.origin_move_id.date or lote.create_date).strftime("%d/%m/%Y")
                    if (lote.origin_move_id.date or lote.create_date) else "",
                ],
                "button": boton,
            })
        return {
            "key": "nauplio_inventory", "title": _("Inventario de nauplio comprado"), "icon": "fa-flask",
            "columns": [_("Lote"), _("Naupliero de origen"), _("Línea genética"), _("Disponible"), _("Recibido")],
            "rows": filas, "empty": _("No tienes nauplio comprado en inventario."),
            "more_url": "/marketplace/my-lots", "more_label": _("Ver mi inventario"),
        }

    # ------------------------------------------------------------------
    # Camaronera
    # ------------------------------------------------------------------
    @api.model
    def _panel_camaronera(self, partner, data):
        Lot = self.env["shrimp.stock.lot"].sudo()
        larva = Lot.search([("owner_id", "=", partner.id), ("available_qty", ">", 0)]).filtered(
            lambda l: l.product_id._shrimp_tipo() == "larva")
        Pond = self.env["shrimp.partner.pond"].sudo()
        piscinas = Pond.search_count([("partner_id", "=", partner.id), ("active", "=", True)])
        siembras = self.env["shrimp.lot.allocation"].sudo().search_count(
            [("partner_id", "=", partner.id), ("state", "=", "allocated")])
        data["kpis"] += [
            {"key": "larva_stock", "label": _("Larva en inventario"),
             "value": "%s %s" % (fmt_num(sum(larva.mapped("available_qty"))),
                                 self._uom_label(larva.mapped("product_id"))),
             "sub": _("%s lotes por sembrar") % len(larva), "icon": "fa-flask", "tone": "teal",
             "url": "/marketplace/my-lots"},
            {"key": "ponds", "label": _("Piscinas activas"), "value": str(piscinas),
             "sub": _("%s siembras en curso") % siembras, "icon": "fa-map-o", "tone": "navy",
             "url": "/marketplace/my-facilities"},
        ]
        productos = self._seller_kpis(partner, "camaronera", data, unit_default="lb")
        self._cert_alerts(partner, data, productos)
        # «Compras que esperan que confirmes la recepción»: ahora para todos los
        # perfiles, con el plazo de la confirmación automática
        # (shrimp_dashboard_reception.py).
        data["sections"] += [
            self._active_lots_section(partner, productos, title=_("Cosechas publicadas")),
            self._last_sales_section(partner, "camaronera"),
        ]

    # ------------------------------------------------------------------
    # Empacadora (shrimp_packer agrega listas, reservas y oferta)
    # ------------------------------------------------------------------
    @api.model
    def _panel_empacadora(self, partner, data):
        Tx = self.env["shrimp.transaction"].sudo()
        compras = self._purchases_domain(partner, "empacadora")
        en_curso = Tx.search(compras + [("state", "not in", TX_CERRADAS)])
        del_mes = Tx.search(compras + [("state", "in", ["confirmed", "done"]),
                                       ] + self._month_domain())
        cerradas = Tx.search(compras + [("state", "in", ["confirmed", "done"])])
        data["kpis"] += [
            {"key": "purchases_open", "label": _("Compras en curso"), "value": str(len(en_curso)),
             "sub": _("verificación, firmas o entrega"), "icon": "fa-clock-o", "tone": "amber",
             "url": "/marketplace/purchases"},
            {"key": "bought_month", "label": _("Comprado este mes"),
             "value": "%s lb" % fmt_num(sum(del_mes.mapped("transaction_qty"))),
             "sub": fmt_money(sum(del_mes.mapped("amount_total"))), "icon": "fa-line-chart",
             "tone": "green", "url": "/marketplace/purchases"},
            {"key": "suppliers", "label": _("Proveedores"),
             "value": str(len(cerradas.mapped("seller_partner_id"))),
             "sub": _("camaroneras con compras cerradas"), "icon": "fa-users", "tone": "navy",
             "url": False},
        ]
        self._cert_alerts(partner, data)
        top = {}
        for tx in cerradas:
            fila = top.setdefault(tx.seller_partner_id, [0.0, 0.0, 0])
            fila[0] += tx.transaction_qty or 0.0
            fila[1] += tx.amount_total or 0.0
            fila[2] += 1
        orden = sorted(top.items(), key=lambda kv: -kv[1][0])[:5]
        data["sections"].append({
            "key": "top_suppliers", "title": _("Mis 5 mejores proveedores"), "icon": "fa-trophy",
            "columns": [_("Camaronera"), _("Compras"), _("Cantidad"), _("Total")],
            "rows": [{"cells": [s.name, str(v[2]), "%s lb" % fmt_num(v[0]), fmt_money(v[1])],
                      "url": "/marketplace/sellers/%s" % s.uuid_ref} for s, v in orden],
            "empty": _("Todavía no tienes compras cerradas."),
            "more_url": "/marketplace/purchases", "more_label": _("Ver mis compras"),
        })
