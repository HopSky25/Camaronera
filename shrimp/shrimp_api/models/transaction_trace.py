"""Trazabilidad pública por token revocable.

El QR del certificado apuntaba a /marketplace/compras/<id>/trazabilidad: con
el id entero (que la ruta ya no resolvía, solo acepta uuid) y detrás de un
login. Quien escanea el QR —un importador, un inspector, un consumidor— no
tiene cuenta, así que el QR no servía para nada.

Ahora apunta a /t/<token>: una página pública con lo que se puede enseñar
(cadena de custodia, especie, genética, fechas, evolución, certificados
vigentes, veredicto, planta) y nada de lo que no (precios, comisiones, GPS,
teléfonos, técnicos, archivos). El token es aleatorio, no deriva de ningún
id y se puede rotar: rotarlo invalida los QR ya impresos.
"""

import secrets

from odoo import _, api, fields, models

VERDICT_STATES = ("approved", "approved_obs", "rejected")


class ShrimpTransaction(models.Model):
    _inherit = "shrimp.transaction"

    trace_token = fields.Char(
        string="Token de trazabilidad pública", copy=False, readonly=True, index=True,
        help="Va en el QR del certificado (/t/<token>). Rotarlo invalida los QR impresos.")

    _trace_token_unique = models.Constraint(
        "unique(trace_token)", "El token de trazabilidad debe ser único.")

    @api.model
    def _new_trace_token(self):
        return secrets.token_urlsafe(18)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("trace_token", self._new_trace_token())
        return super().create(vals_list)

    def _ensure_trace_token(self):
        self.ensure_one()
        if not self.sudo().trace_token:
            self.sudo().trace_token = self._new_trace_token()
        return self.sudo().trace_token

    def action_rotate_trace_token(self):
        for rec in self.sudo():
            rec.trace_token = self._new_trace_token()
        return True

    def traceability_url(self):
        """URL pública que va en el QR del certificado."""
        self.ensure_one()
        return "%s/t/%s" % (self.get_base_url(), self._ensure_trace_token())

    # ------------------------------------------------------------------
    # Lo que se publica
    # ------------------------------------------------------------------
    @api.model
    def _public_party(self, partner, role_label=None, role_code=None):
        """Empresa, rol, provincia y país de un eslabón (sin datos personales).

        La provincia y el país salen de la ficha del socio y, si no los tiene,
        de su primera instalación con esos datos."""
        p = partner.sudo()
        roles = dict(p._fields["shrimp_user_type"]._description_selection(p.env))
        province = p.state_id.name or None
        country = p.country_id.name or None
        if not province or not country:
            facs = self.env["shrimp.partner.facility"].sudo().search(
                [("partner_id", "=", p.commercial_partner_id.id)], order="id asc")
            fac = facs.filtered(lambda f: f.province or f.country_id)[:1]
            province = province or (fac.province if fac else None) or None
            country = country or (fac.country_id.name if fac and fac.country_id else None)
        if not country and province:
            # Una provincia sin país es de la plataforma (Ecuador). No se usa
            # el país de la compañía de Odoo: en una base compartida puede ser
            # otro (la de demo sale con Estados Unidos).
            country = self._shrimp_platform_country_name()
        return {
            "company": p.commercial_partner_id.name or p.name,
            "role": role_label or roles.get(role_code or p.shrimp_user_type) or _("Productor"),
            "role_code": role_code or p.shrimp_user_type or None,
            "province": province,
            "country": country,
        }

    @api.model
    def _shrimp_platform_country_name(self):
        pais = self.env.ref("base.ec", raise_if_not_found=False)
        return pais.name if pais else None

    @api.model
    def _approved_cert_lines(self, lines):
        today = fields.Date.context_today(self)
        lines = lines.filtered(lambda l: l.active and (not l.expiry_date or l.expiry_date >= today))
        # Si el modelo trae revisión de certificados (estado aprobado), solo
        # cuentan los aprobados.
        for fname in ("review_state", "state", "status"):
            field = lines._fields.get(fname)
            if field and field.type == "selection":
                values = [v[0] for v in field._description_selection(self.env)]
                if "approved" in values:
                    lines = lines.filtered(lambda l, f=fname: l[f] == "approved")
                break
        return lines

    def _public_traceability_data(self):
        self.ensure_one()
        # Página/API públicas: anónimas, así que las horas van en la zona de
        # la plataforma, no en la del usuario que consulte (ver _shrimp_tz).
        tx = self.sudo().with_context(shrimp_public_tz=True)
        data = tx.get_full_traceability_data()
        product = tx.product_id
        roles = dict(self.env["res.partner"]._fields["shrimp_user_type"]._description_selection(self.env))
        partner_tz = tx.buyer_partner_id

        def ldate(value):
            d = tx.shrimp_local_date(value, partner_tz)
            return d.isoformat() if d else None

        def ldt(value):
            return tx.shrimp_iso_local(value, partner_tz)

        # Cadena: el mismo cálculo que el certificado (rol de cada eslabón,
        # la planta de empaque como servicio, sin repetir al dueño).
        chain = []
        for p, rol in tx._shrimp_chain_parties(data):
            chain.append(self._public_party(p, role_label=roles.get(rol), role_code=rol))
        if not chain:
            for p in (tx.seller_partner_id, tx.buyer_partner_id):
                if p:
                    chain.append(self._public_party(p))

        packing = []
        for order in getattr(tx, "copack_order_ids", tx.browse()):
            plant = order.copacker_partner_id
            packing.append({
                "plant": plant.name,
                # Empaque propio: la empresa dueña empacó en su propia planta
                # (perfil Maquilador); sin acta de dos partes ni comisión.
                "self_packing": bool(order.self_packing),
                "label": order.shrimp_plant_label() if order.self_packing else None,
                "establishment_code": plant.pack_codigo_establecimiento or None,
                "province": plant.state_id.name or None,
                "received_on": ldate(order.received_date),
                "packed_on": ldate(order.packed_date),
                "presentation": order.packed_presentation or None,
                "received_lb": round(order.received_lb or 0.0, 2),
                "packed_lb": round(order.packed_lb or 0.0, 2),
                "boxes": order.boxes or None,
            })

        # La empacadora COMPRADORA no empacó nada en esta compra: es el
        # destino. Antes salía como «planta» de empaque, duplicada con la
        # cadena y confundida con el maquilador.
        destination = None
        buyer = tx.buyer_partner_id
        buyer_role = tx.buyer_role or buyer._shrimp_effective_type()
        if buyer_role == "empacadora":
            destination = {
                "label": _("Destino / planta compradora"),
                "plant": buyer.emp_planta_nombre or buyer.commercial_partner_id.name or buyer.name,
                "establishment_code": buyer.emp_aprobacion_sanitaria or None,
                "province": self._public_party(buyer)["province"],
                "received_on": ldate(tx.received_date) if tx.received_date else None,
            }

        evolutions = data["evolutions"].sorted(lambda e: (e.date or fields.Datetime.now(), e.id))
        evo = None
        if evolutions:
            first, last = evolutions[0], evolutions[-1]
            evo = {
                "records": len(evolutions),
                "first_date": ldate(first.date),
                "last_date": ldate(last.date),
                "survival_rate_first": first.survival_rate or None,
                "survival_rate_last": last.survival_rate or None,
                "avg_size_mg_first": first.avg_size_mg or None,
                "avg_size_mg_last": last.avg_size_mg or None,
                "stages": list(dict.fromkeys(s for s in evolutions.mapped("stage_id.name") if s)),
            }

        # Certificados de toda la cadena, vigentes y SIN repetir (tipo + número).
        certificates = [{
            "name": line.certificate_id.name,
            "issuer": line.issuer or line.certificate_id.issuer or None,
            "number": line.number or None,
            "issue_date": line.issue_date.isoformat() if line.issue_date else None,
            "expiry_date": line.expiry_date.isoformat() if line.expiry_date else None,
        } for line in self._approved_cert_lines(tx.shrimp_trace_certificates(data))]

        verification = None
        v = tx.verification_ids[:1] if "verification_ids" in tx._fields else tx.browse()
        # Declarada: se publica con el informe ya presentado, y SIEMPRE
        # rotulada como declarada por las partes (no como verificación
        # acreditada): mode/label permiten a la página pública distinguirlas.
        if v and (v.state in VERDICT_STATES or v.state == "declared"):
            declarada = v.verification_mode == "declared"
            verification = {
                "result": v.state,
                "mode": v.verification_mode,
                "label": v.verification_label(),
                "accredited": bool(not declarada and v.verifier_partner_id.verifier_is_accredited),
                "confirmed_by_both": v.acceptance_state == "closed",
                "verdict_date": ldate(v.verified_date),
                "verifier": v.verifier_partner_id.name if not declarada else None,
                "declared_by": (v.declarant_partner_id.commercial_partner_id.name
                                or v.declarant_partner_id.name) if declarada else None,
                "external_verifier": (v.external_verifier_name or None) if declarada else None,
                "external_verifier_vat": (v.external_verifier_vat or None) if declarada else None,
                "self_verified": bool(declarada and v.declared_source == "self"),
                "scope": v.scope or None,
                "presentation": v.presentation or None,
            }
            if v.scope == "larvae":
                verification.update({
                    "survival_rate": v.larvae_survival_rate or None,
                    "avg_size_mg": v.larvae_avg_size_mg or None,
                    "health_status": v.larvae_health_status or None,
                })
            else:
                verification.update({
                    "yield_pct": round(v.yield_pct, 2) if v.yield_pct else None,
                    "classes_pct": {"a": round(v.yield_class_a_pct, 2),
                                    "b": round(v.yield_class_b_pct, 2),
                                    "c": round(v.yield_class_c_pct, 2)} if v.total_processed_lb else None,
                    "metabisulfite": v.metabisulfite_result or None,
                    "taste": v.taste_result or None,
                })

        # Despacho: horas y puntualidad; sin transportista, placa ni teléfono.
        dispatch = None
        d = tx.dispatch_id if "dispatch_id" in tx._fields else False
        if d and (d.harvest_date or d.farm_departure or d.eta or d.actual_arrival):
            dispatch = {
                "harvest_date": d.harvest_date.isoformat() if d.harvest_date else None,
                "farm_departure": ldt(d.farm_departure),
                "eta": ldt(d.eta),
                "actual_arrival": ldt(d.actual_arrival),
                "on_time": bool(d.on_time) if d.actual_arrival and d.eta else None,
                "delay_minutes": int(round(d.delay_minutes)) if d.actual_arrival and d.eta else None,
            }

        harvest = None
        if v and v.harvest_date:
            harvest = v.harvest_date.isoformat()
        elif d and d.harvest_date:
            harvest = d.harvest_date.isoformat()

        # Siembra de origen (de la cosecha): piscina, fecha y cantidad.
        sowings = [{
            "date": a.allocation_date.isoformat() if a.allocation_date else None,
            "farm": a.partner_id.commercial_partner_id.name or None,
            "pond": a.pond_id.name or None,
            "qty": round(a.allocated_qty, 2),
            "uom": a.stock_lot_id.uom_id.name or None,
            "larvae": a.product_id.name or None,
        } for a in data["allocations"]]

        exits = [e.public_dict() for e in data["exports"].filtered(lambda e: e.state == "registered")]
        for e in data["exports"].filtered(lambda e: e.state == "registered"):
            chain.append({"company": e.chain_label(), "role": _("Salida / Exportación"),
                          "role_code": "export", "province": None,
                          "country": e.destination_country_id.name or None})

        facility = product.origin_facility_id
        return {
            "status": tx.state,
            "timezone": tx._shrimp_tz(partner_tz).zone,
            "product": {
                "name": product.name,
                "species": product.species_id.name or None,
                "scientific_name": product.species_id.scientific_name or None,
                "genetics_line": product.genetics_line_id.name or None,
                "genetics_brand": product.genetics_line_id.brand or None,
                "stage": product.stage_id.name or None,
                "presentation": product.presentation or None,
                "size_grade": product.size_grade_id.name or None,
                "batch_code": product.batch_code or None,
                "origin": {
                    "facility": facility.name or None,
                    "city": facility.city or None,
                    "province": facility.province or None,
                    "country": facility.country_id.name or (
                        self._shrimp_platform_country_name() if facility.province else None),
                } if facility else None,
            },
            "chain": chain,
            "dates": {
                "production": product.production_date.isoformat() if product.production_date else None,
                # La fecha real de la operación (confirmación), la misma que
                # imprime el certificado PDF que enlaza a esta página.
                "purchase": ldate(tx.shrimp_operation_datetime()),
                "harvest": harvest,
                "process": v.process_date.isoformat() if v and v.process_date else None,
                # La fecha REAL de recepción si ya se recibió; si no, la comprometida.
                "delivery": (ldate(tx.received_date) if tx.received_date
                             else (tx.delivery_date.isoformat() if tx.delivery_date else None)),
                "delivery_is_actual": bool(tx.received_date),
            },
            "evolution": evo,
            "sowings": sowings,
            "certificates": certificates,
            "verification": verification,
            "dispatch": dispatch,
            "packing": packing,
            "destination": destination,
            "exports": exits,
        }
