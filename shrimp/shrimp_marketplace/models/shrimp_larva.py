# -*- coding: utf-8 -*-
"""Larva y nauplio en el marketplace (Fase 1 de la propuesta de menús).

Agrupa los estadíos existentes en tres «tipos» de producto que entiende el
comprador (Nauplio · Larva PL · Camarón), calcula qué lotes tienen PCR / SPF
vigente (certificados aprobados y no vencidos, del lote o de su vendedor) y
arma los bloques de «Laboratorios destacados» y «Larva lista para sembrar».

Nada de esto agrega campos almacenados: todo se calcula sobre los datos que ya
existen (stage_id, genetics_line_id, survival_rate, certificados).
"""
from odoo import api, fields, models

# Tipos de producto para las pestañas del catálogo.
SHRIMP_TIPOS = [
    ("nauplio", "Nauplio"),
    ("larva", "Larva (PL)"),
    ("camaron", "Camarón"),
]
# Rol de vendedor que, sin estadío, define el tipo del lote.
SHRIMP_TIPO_ROL = {"nauplio": "semillero", "larva": "laboratorio", "camaron": "camaronera"}
# Para los «destacados»: quién vende cada tipo.
SHRIMP_TIPO_VENDEDOR = {"nauplio": "semillero", "larva": "laboratorio"}

# Supervivencias mínimas que ofrece el filtro (en %).
SHRIMP_SUPERVIVENCIAS = (60, 70, 80, 90)


class ShrimpStage(models.Model):
    _inherit = "shrimp.stage"

    def _shrimp_tipo(self):
        """'nauplio', 'larva' o 'camaron' según el código del estadío."""
        self.ensure_one()
        code = (self.code or "").strip().upper()
        if code == "NAUPLIO":
            return "nauplio"
        if code in ("ZOEA", "MYSIS") or code.startswith("PL"):
            return "larva"
        return "camaron"

    @api.model
    def _shrimp_stages_of_tipo(self, tipo):
        stages = self.sudo().search([])
        return stages.filtered(lambda s: s._shrimp_tipo() == tipo)


class ShrimpProductLarva(models.Model):
    _inherit = "shrimp.product"

    quality_ids = fields.One2many(
        "shrimp.product.quality", "product_id", string="Fichas de calidad", readonly=True)

    # ------------------------------------------------------------------
    # Tipo de producto (pestañas)
    # ------------------------------------------------------------------
    def _shrimp_tipo(self):
        self.ensure_one()
        if self.stage_id:
            return self.stage_id._shrimp_tipo()
        return {"semillero": "nauplio", "laboratorio": "larva"}.get(self.seller_role, "camaron")

    @api.model
    def _shrimp_tipo_domain(self, tipo):
        if tipo not in SHRIMP_TIPO_ROL:
            return []
        ids = self.env["shrimp.stage"]._shrimp_stages_of_tipo(tipo).ids
        return ["|", ("stage_id", "in", ids),
                "&", ("stage_id", "=", False), ("seller_role", "=", SHRIMP_TIPO_ROL[tipo])]

    # ------------------------------------------------------------------
    # Certificados sanitarios vigentes (PCR / SPF)
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_cert_catalog(self, kind):
        """Certificados del catálogo de un tipo: 'pcr' (PCR-WSSV, PCR-AHPND…)
        o 'spf'."""
        Cert = self.env["shrimp.certificate"].sudo().with_context(active_test=False)
        if kind == "pcr":
            return Cert.search([("code", "=ilike", "PCR%")])
        if kind == "spf":
            return Cert.search([("code", "=ilike", "SPF")])
        return Cert.browse()

    @api.model
    def _shrimp_cert_valid_domain(self, kind):
        """Dominio de productos con un certificado `kind` APROBADO y no
        vencido: en el propio lote o entre los certificados del vendedor."""
        certs = self._shrimp_cert_catalog(kind)
        if not certs:
            return [("id", "=", 0)]
        hoy = fields.Date.context_today(self)
        vigencia = ["|", ("expiry_date", "=", False), ("expiry_date", ">=", hoy)]
        prod_ids = self.env["shrimp.product.certificate.line"].sudo().search(
            [("certificate_id", "in", certs.ids), ("status", "=", "approved")] + vigencia
        ).mapped("product_id").ids
        socio_ids = self.env["shrimp.user.certificate.line"].sudo().search(
            [("certificate_id", "in", certs.ids), ("status", "=", "approved")] + vigencia
        ).mapped("partner_id").ids
        return ["|", ("id", "in", prod_ids), ("seller_partner_id", "in", socio_ids)]

    def _shrimp_cert_valid_ids(self, kind):
        """Ids de `self` con certificado `kind` vigente (para las insignias)."""
        if not self:
            return set()
        return set(self.sudo().search(
            [("id", "in", self.ids)] + self._shrimp_cert_valid_domain(kind)).ids)

    def _shrimp_pcr_valid_ids(self):
        return self._shrimp_cert_valid_ids("pcr")

    # ------------------------------------------------------------------
    # Ficha de calidad
    # ------------------------------------------------------------------
    def _shrimp_latest_quality(self):
        self.ensure_one()
        return self.env["shrimp.product.quality"].sudo().search(
            [("product_id", "=", self.id)], order="sample_date desc, id desc", limit=1)

    def _shrimp_accepts_quality(self):
        """La ficha de calidad es de nauplio y larva (no del camarón adulto)."""
        self.ensure_one()
        return self._shrimp_tipo() in ("nauplio", "larva")

    # ------------------------------------------------------------------
    # Bloques de la landing y del catálogo
    # ------------------------------------------------------------------
    @api.model
    def _shrimp_published_domain(self):
        return [("active", "=", True), ("state", "=", "published"), ("available_qty", ">", 0)]

    @api.model
    def _shrimp_featured_min_reviews(self):
        """Mínimo de reseñas como vendedor para salir en «destacados»
        (parámetro shrimp_marketplace.featured_min_reviews, 10 por defecto)."""
        valor = self.env["ir.config_parameter"].sudo().get_param(
            "shrimp_marketplace.featured_min_reviews", "10")
        try:
            return max(int(float(valor)), 0)
        except (TypeError, ValueError):
            return 10

    @api.model
    def _shrimp_featured_sellers(self, tipo="larva", limit=6):
        """Vendedores destacados de un tipo (laboratorios para larva,
        semilleros para nauplio): mejor calificación y, a igualdad, más
        ventas cerradas. Solo cuentan los que tienen al menos
        _shrimp_featured_min_reviews() reseñas como vendedor; si no llegan a
        ``limit`` no se rellena con otros (y si no hay ninguno, la lista va
        vacía y la sección no se pinta). Devuelve dicts listos para pintar."""
        role = SHRIMP_TIPO_VENDEDOR.get(tipo, "laboratorio")
        Product = self.sudo()
        lotes = Product.search(self._shrimp_published_domain() + [
            ("seller_role", "=", role),
            ("seller_partner_id.shrimp_rating_count", ">=", self._shrimp_featured_min_reviews()),
        ])
        if not lotes:
            return []
        por_vendedor = {}
        for p in lotes:
            por_vendedor.setdefault(p.seller_partner_id.id, Product.browse())
            por_vendedor[p.seller_partner_id.id] |= p
        vendedores = self.env["res.partner"].sudo().browse(list(por_vendedor))
        ventas = {}
        grupos = self.env["shrimp.transaction"].sudo()._read_group(
            [("seller_partner_id", "in", vendedores.ids), ("state", "in", ["confirmed", "done"])],
            ["seller_partner_id"], ["__count"])
        for socio, n in grupos:
            ventas[socio.id] = n
        pcr_ids = lotes._shrimp_pcr_valid_ids()
        filas = []
        for v in vendedores:
            suyos = por_vendedor[v.id]
            supervivencias = [s for s in suyos.mapped("survival_rate") if s]
            filas.append({
                "partner": v,
                "rating": v.shrimp_rating_avg or 0.0,
                "rating_count": v.shrimp_rating_count or 0,
                "sales": ventas.get(v.id, 0),
                "lots": len(suyos),
                "qty": sum(suyos.mapped("available_qty")),
                "uom": suyos[:1].uom_id.name or "",
                "genetics": sorted({g for g in suyos.mapped("genetics_line_id.name") if g})[:3],
                # Total de líneas genéticas (la tarjeta compacta muestra una y «+N»).
                "genetics_count": len({g for g in suyos.mapped("genetics_line_id.name") if g}),
                "survival": (sum(supervivencias) / len(supervivencias)) if supervivencias else 0.0,
                "location": next((l for l in suyos.mapped("location") if l), ""),
                "pcr": any(p.id in pcr_ids for p in suyos),
                "price_from": min(suyos.mapped("price")) if suyos else 0.0,
            })
        filas.sort(key=lambda f: (-f["rating"], -f["sales"], -f["lots"], f["partner"].name or ""))
        return filas[:limit]

    @api.model
    def _shrimp_landing_larva(self, limit=6):
        """«Larva lista para sembrar»: lotes PL publicados más recientes y un
        resumen por estadío (millares disponibles y precio desde)."""
        Product = self.sudo()
        pl_stages = self.env["shrimp.stage"]._shrimp_stages_of_tipo("larva").filtered(
            lambda s: (s.code or "").upper().startswith("PL"))
        dominio = self._shrimp_published_domain() + [("stage_id", "in", pl_stages.ids)]
        lotes = Product.search(dominio, order="published_date desc, id desc", limit=limit)
        resumen = []
        for st in pl_stages.sorted(lambda s: (s.sequence, s.name)):
            del_estadio = Product.search(dominio + [("stage_id", "=", st.id)])
            if not del_estadio:
                continue
            resumen.append({
                "stage": st,
                "qty": sum(del_estadio.mapped("available_qty")),
                "price_from": min(del_estadio.mapped("price")),
                "count": len(del_estadio),
                "uom": del_estadio[:1].uom_id.name or "",
            })
        return {"lots": lotes, "stages": resumen, "pcr_ids": lotes._shrimp_pcr_valid_ids()}
