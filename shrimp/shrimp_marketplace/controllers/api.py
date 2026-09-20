import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Campos del producto que la API expone / acepta
_READABLE_FIELDS = [
    "id", "name", "price", "uom_id", "location", "state",
    "initial_qty", "available_qty", "seller_role",
    "species_id", "stage_id", "genetics_line_id",
    "avg_size_mg", "survival_rate", "expected_delivery_date",
]
_WRITABLE_FIELDS = [
    "name", "price", "uom_id", "location",
    "initial_qty", "species_id", "stage_id", "genetics_line_id",
    "avg_size_mg", "survival_rate", "health_status",
    "location", "expected_delivery_date",
]


class ShrimpMarketplaceAPI(http.Controller):
    """API REST/JSON para el CRUD de productos, autenticada por API key.

    Autenticación: cabecera `X-API-Key: <clave>`.
    Niveles de seguridad (scope de la clave):
      - read  → solo GET
      - write → GET + POST/PUT/DELETE sobre los productos propios
      - admin → todo, sobre productos de cualquier vendedor
    """

    # ---------- utilidades ----------
    def _json(self, payload, status=200):
        return request.make_response(
            json.dumps(payload, default=str),
            headers=[("Content-Type", "application/json; charset=utf-8")],
            status=status,
        )

    def _error(self, status, code, message):
        return self._json({"error": {"code": code, "message": message}}, status=status)

    def _get_api_key(self):
        raw = request.httprequest.headers.get("X-API-Key")
        if not raw:
            # también aceptamos Authorization: Bearer <clave>
            auth = request.httprequest.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                raw = auth[7:].strip()
        return request.env["shrimp.api.key"].sudo()._authenticate(raw)

    def _serialize(self, product):
        data = {}
        for f in _READABLE_FIELDS:
            if f == "id":
                # El identificador público es el código alfanumérico (no el id entero).
                data["id"] = product.uuid_ref
                continue
            val = product[f]
            if hasattr(val, "id"):  # Many2one
                data[f] = {"id": getattr(val, "uuid_ref", val.id), "name": val.display_name} if val else None
            else:
                data[f] = val
        # El vendedor es un partner nativo de Odoo (sin código): se mantiene su id.
        data["seller"] = {
            "id": product.seller_partner_id.id,
            "name": product.seller_partner_id.name,
        }
        return data

    # Campos Many2one escribibles que apuntan a modelos con código propio.
    _M2O_MODEL_BY_FIELD = {
        "uom_id": "shrimp.uom",
        "species_id": "shrimp.species",
        "stage_id": "shrimp.stage",
        "genetics_line_id": "shrimp.genetics.line",
    }

    def _resolve_m2o_vals(self, env, vals):
        """Convierte valores de campos Many2one que vengan como código
        alfanumérico (o id) al id entero que espera el ORM."""
        for field, model_name in self._M2O_MODEL_BY_FIELD.items():
            if field in vals and vals[field]:
                rec = env[model_name].sudo().resolve_ref(vals[field])
                vals[field] = rec.id if rec else False
        return vals

    def _read_body(self):
        try:
            raw = request.httprequest.get_data(as_text=True) or "{}"
            return json.loads(raw), None
        except (ValueError, TypeError):
            return None, self._error(400, "bad_json", "El cuerpo no es JSON válido.")

    def _clean_write_vals(self, body):
        vals = {}
        for f in _WRITABLE_FIELDS:
            if f in body:
                vals[f] = body[f]
        return vals

    # ---------- despachador ----------
    @http.route(
        ["/api/v1/products", "/api/v1/products/<product_id>"],
        type="http", auth="none", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        csrf=False, save_session=False,
    )
    def products(self, product_id=None, **kw):
        method = request.httprequest.method

        if method == "OPTIONS":
            return self._json({"ok": True})

        api_key = self._get_api_key()
        if not api_key:
            return self._error(401, "unauthorized", "Clave de API ausente o inválida (cabecera X-API-Key).")

        api_key._touch()
        # Ejecutamos como el usuario propietario de la clave
        env = request.env(user=api_key.user_id.id)
        Product = env["shrimp.product"]
        partner = api_key.partner_id

        try:
            if method == "GET":
                if not api_key._can("read"):
                    return self._error(403, "forbidden", "La clave no tiene permiso de lectura.")
                return self._handle_get(Product, api_key, partner, product_id, kw)

            if method == "POST":
                if not api_key._can("write"):
                    return self._error(403, "forbidden", "La clave requiere nivel 'write' para crear.")
                return self._handle_create(Product, api_key, partner)

            if method in ("PUT",):
                if not api_key._can("write"):
                    return self._error(403, "forbidden", "La clave requiere nivel 'write' para editar.")
                return self._handle_update(Product, api_key, partner, product_id)

            if method == "DELETE":
                if not api_key._can("write"):
                    return self._error(403, "forbidden", "La clave requiere nivel 'write' para borrar.")
                return self._handle_delete(Product, api_key, partner, product_id)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Error en la API del marketplace")
            return self._error(500, "server_error", str(exc))

        return self._error(405, "method_not_allowed", "Método no soportado.")

    # ---------- operaciones ----------
    def _scope_domain(self, api_key, partner):
        """admin ve todo; el resto solo sus propios productos."""
        if api_key._can("admin"):
            return []
        return [("seller_partner_id", "=", partner.id)]

    def _handle_get(self, Product, api_key, partner, product_id, kw):
        if product_id:
            product = Product.sudo().resolve_ref(product_id)
            if not product or (not product.active and not api_key._can("admin")):
                return self._error(404, "not_found", "Producto no encontrado.")
            if not api_key._can("admin") and product.seller_partner_id.id != partner.id:
                return self._error(403, "forbidden", "No puedes ver este producto.")
            return self._json({"product": self._serialize(product)})

        domain = self._scope_domain(api_key, partner)
        # filtros opcionales
        if kw.get("state"):
            domain.append(("state", "=", kw["state"]))
        if kw.get("q"):
            domain.append(("name", "ilike", kw["q"]))
        try:
            limit = min(int(kw.get("limit", 50)), 200)
            offset = int(kw.get("offset", 0))
        except (TypeError, ValueError):
            limit, offset = 50, 0
        products = Product.sudo().search(domain, limit=limit, offset=offset, order="create_date desc")
        total = Product.sudo().search_count(domain)
        return self._json({
            "count": len(products),
            "total": total,
            "limit": limit,
            "offset": offset,
            "products": [self._serialize(p) for p in products],
        })

    def _handle_create(self, Product, api_key, partner):
        body, err = self._read_body()
        if err:
            return err
        vals = self._clean_write_vals(body)
        if not vals.get("name"):
            return self._error(422, "validation", "El campo 'name' es obligatorio.")
        if "price" not in vals:
            return self._error(422, "validation", "El campo 'price' es obligatorio.")

        # El vendedor es el dueño de la clave (o el indicado si es admin)
        seller = partner
        if api_key._can("admin") and body.get("seller_partner_id"):
            seller = request.env["res.partner"].sudo().browse(int(body["seller_partner_id"]))
            if not seller.exists():
                return self._error(422, "validation", "seller_partner_id no existe.")
        vals["seller_partner_id"] = seller.id

        # seller_role derivado del tipo del vendedor
        utype = seller.shrimp_user_type
        vals["seller_role"] = "semillero" if utype == "semillero" else "laboratorio"

        # Los Many2one pueden venir como código alfanumérico o id.
        self._resolve_m2o_vals(Product.env, vals)

        try:
            product = Product.sudo().create(vals)
        except Exception as exc:  # noqa: BLE001
            return self._error(422, "validation", str(exc))

        # Correo: producto creado (vía API).
        self._notify_product_created(product)

        return self._json({"product": self._serialize(product)}, status=201)

    def _notify_product_created(self, product):
        """Envía el correo de 'producto creado' al vendedor. No rompe la respuesta de la API."""
        try:
            template = request.env.ref(
                "shrimp_marketplace.mail_template_shrimp_product_created",
                raise_if_not_found=False,
            )
            email_to = product.seller_partner_id.email
            if template and email_to:
                template.sudo().send_mail(
                    product.id, force_send=True,
                    email_values={"email_to": email_to},
                )
        except Exception:  # noqa: BLE001
            pass

    def _handle_update(self, Product, api_key, partner, product_id):
        if not product_id:
            return self._error(400, "bad_request", "Falta el código del producto en la URL.")
        product = Product.sudo().resolve_ref(product_id)
        if not product:
            return self._error(404, "not_found", "Producto no encontrado.")
        if not api_key._can("admin") and product.seller_partner_id.id != partner.id:
            return self._error(403, "forbidden", "No puedes editar este producto.")
        body, err = self._read_body()
        if err:
            return err
        vals = self._clean_write_vals(body)
        if not vals:
            return self._error(422, "validation", "No hay campos válidos para actualizar.")
        self._resolve_m2o_vals(Product.env, vals)
        try:
            product.sudo().write(vals)
        except Exception as exc:  # noqa: BLE001
            return self._error(422, "validation", str(exc))
        return self._json({"product": self._serialize(product)})

    def _handle_delete(self, Product, api_key, partner, product_id):
        if not product_id:
            return self._error(400, "bad_request", "Falta el código del producto en la URL.")
        product = Product.sudo().resolve_ref(product_id)
        if not product:
            return self._error(404, "not_found", "Producto no encontrado.")
        if not api_key._can("admin") and product.seller_partner_id.id != partner.id:
            return self._error(403, "forbidden", "No puedes borrar este producto.")
        # Archivamos en lugar de borrar para preservar trazabilidad
        code = product.uuid_ref
        product.sudo().write({"active": False, "state": "cancel"})
        return self._json({"deleted": True, "id": code})

    # ======================================================================
    # INSTALACIONES (shrimp.partner.facility) — CRUD por API key
    # ======================================================================
    _FAC_WRITABLE = ["name", "code", "facility_type", "city", "province", "address"]

    def _serialize_facility(self, f):
        return {
            "id": f.uuid_ref, "name": f.name, "code": f.code or None,
            "facility_type": f.facility_type, "city": f.city or None,
            "province": f.province or None, "address": f.address or None,
            "partner": {"id": f.partner_id.id, "name": f.partner_id.name},
        }

    @http.route(
        ["/api/v1/facilities", "/api/v1/facilities/<facility_id>"],
        type="http", auth="none", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        csrf=False, save_session=False,
    )
    def facilities(self, facility_id=None, **kw):
        method = request.httprequest.method
        if method == "OPTIONS":
            return self._json({"ok": True})
        api_key = self._get_api_key()
        if not api_key:
            return self._error(401, "unauthorized", "Clave de API ausente o inválida (X-API-Key).")
        api_key._touch()
        env = request.env(user=api_key.user_id.id)
        Fac = env["shrimp.partner.facility"]
        partner = api_key.partner_id

        def _owned(rec):
            return api_key._can("admin") or rec.partner_id.id == partner.id

        try:
            if method == "GET":
                if not api_key._can("read"):
                    return self._error(403, "forbidden", "La clave no tiene permiso de lectura.")
                if facility_id:
                    f = Fac.sudo().resolve_ref(facility_id)
                    if not f:
                        return self._error(404, "not_found", "Instalación no encontrada.")
                    if not _owned(f):
                        return self._error(403, "forbidden", "No puedes ver esta instalación.")
                    return self._json({"facility": self._serialize_facility(f)})
                domain = [] if api_key._can("admin") else [("partner_id", "=", partner.id)]
                recs = Fac.sudo().search(domain, order="name")
                return self._json({"count": len(recs), "facilities": [self._serialize_facility(f) for f in recs]})

            if not api_key._can("write"):
                return self._error(403, "forbidden", "La clave requiere nivel 'write'.")

            if method == "POST":
                body, err = self._read_body()
                if err:
                    return err
                if not body.get("name"):
                    return self._error(422, "validation", "El campo 'name' es obligatorio.")
                vals = {k: body[k] for k in self._FAC_WRITABLE if k in body}
                vals["partner_id"] = partner.id
                try:
                    f = Fac.sudo().create(vals)
                except Exception as exc:  # noqa: BLE001
                    return self._error(422, "validation", str(exc))
                return self._json({"facility": self._serialize_facility(f)}, status=201)

            if method in ("PUT", "DELETE"):
                if not facility_id:
                    return self._error(400, "bad_request", "Falta el código en la URL.")
                f = Fac.sudo().resolve_ref(facility_id)
                if not f:
                    return self._error(404, "not_found", "Instalación no encontrada.")
                if not _owned(f):
                    return self._error(403, "forbidden", "No puedes modificar esta instalación.")
                if method == "DELETE":
                    code = f.uuid_ref
                    try:
                        f.sudo().unlink()
                    except Exception as exc:  # noqa: BLE001
                        return self._error(409, "conflict", str(exc))
                    return self._json({"deleted": True, "id": code})
                body, err = self._read_body()
                if err:
                    return err
                vals = {k: body[k] for k in self._FAC_WRITABLE if k in body}
                if not vals:
                    return self._error(422, "validation", "No hay campos válidos para actualizar.")
                try:
                    f.sudo().write(vals)
                except Exception as exc:  # noqa: BLE001
                    return self._error(422, "validation", str(exc))
                return self._json({"facility": self._serialize_facility(f)})
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Error en API de instalaciones")
            return self._error(500, "server_error", str(exc))
        return self._error(405, "method_not_allowed", "Método no soportado.")

    # ======================================================================
    # PISCINAS (shrimp.partner.pond) — CRUD por API key
    # ======================================================================
    _POND_WRITABLE = ["name", "code", "pond_type", "capacity_mode",
                      "length_m", "width_m", "depth_m", "manual_volume_m3", "location"]

    def _serialize_pond(self, p):
        return {
            "id": p.uuid_ref, "name": p.name, "code": p.code or None, "pond_type": p.pond_type,
            "facility": {"id": p.facility_id.uuid_ref, "name": p.facility_id.name} if p.facility_id else None,
            "area_m2": p.area_m2, "volume_m3": p.volume_m3,
            "length_m": p.length_m, "width_m": p.width_m, "depth_m": p.depth_m,
            "location": p.location or None,
            "partner": {"id": p.partner_id.id, "name": p.partner_id.name},
        }

    @http.route(
        ["/api/v1/ponds", "/api/v1/ponds/<pond_id>"],
        type="http", auth="none", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        csrf=False, save_session=False,
    )
    def ponds(self, pond_id=None, **kw):
        method = request.httprequest.method
        if method == "OPTIONS":
            return self._json({"ok": True})
        api_key = self._get_api_key()
        if not api_key:
            return self._error(401, "unauthorized", "Clave de API ausente o inválida (X-API-Key).")
        api_key._touch()
        env = request.env(user=api_key.user_id.id)
        Pond = env["shrimp.partner.pond"]
        Fac = env["shrimp.partner.facility"]
        partner = api_key.partner_id

        def _owned(rec):
            return api_key._can("admin") or rec.partner_id.id == partner.id

        def _resolve_facility(body, vals):
            """Valida y coloca facility_id si viene en el body (código o id, del mismo dueño)."""
            if "facility_id" in body:
                fid = body.get("facility_id")
                if not fid:
                    vals["facility_id"] = False
                    return None
                fac = Fac.sudo().resolve_ref(fid)
                if not fac or not _owned(fac):
                    return "facility_id no existe o no te pertenece."
                vals["facility_id"] = fac.id
            return None

        try:
            if method == "GET":
                if not api_key._can("read"):
                    return self._error(403, "forbidden", "La clave no tiene permiso de lectura.")
                if pond_id:
                    p = Pond.sudo().resolve_ref(pond_id)
                    if not p:
                        return self._error(404, "not_found", "Piscina no encontrada.")
                    if not _owned(p):
                        return self._error(403, "forbidden", "No puedes ver esta piscina.")
                    return self._json({"pond": self._serialize_pond(p)})
                domain = [] if api_key._can("admin") else [("partner_id", "=", partner.id)]
                if kw.get("facility_id"):
                    fac = Fac.sudo().resolve_ref(kw["facility_id"])
                    if fac:
                        domain.append(("facility_id", "=", fac.id))
                recs = Pond.sudo().search(domain, order="name")
                return self._json({"count": len(recs), "ponds": [self._serialize_pond(p) for p in recs]})

            if not api_key._can("write"):
                return self._error(403, "forbidden", "La clave requiere nivel 'write'.")

            if method == "POST":
                body, err = self._read_body()
                if err:
                    return err
                if not body.get("name"):
                    return self._error(422, "validation", "El campo 'name' es obligatorio.")
                vals = {k: body[k] for k in self._POND_WRITABLE if k in body}
                vals["partner_id"] = partner.id
                ferr = _resolve_facility(body, vals)
                if ferr:
                    return self._error(422, "validation", ferr)
                try:
                    p = Pond.sudo().create(vals)
                except Exception as exc:  # noqa: BLE001
                    return self._error(422, "validation", str(exc))
                return self._json({"pond": self._serialize_pond(p)}, status=201)

            if method in ("PUT", "DELETE"):
                if not pond_id:
                    return self._error(400, "bad_request", "Falta el código en la URL.")
                p = Pond.sudo().resolve_ref(pond_id)
                if not p:
                    return self._error(404, "not_found", "Piscina no encontrada.")
                if not _owned(p):
                    return self._error(403, "forbidden", "No puedes modificar esta piscina.")
                if method == "DELETE":
                    code = p.uuid_ref
                    try:
                        p.sudo().unlink()
                    except Exception as exc:  # noqa: BLE001
                        return self._error(409, "conflict", str(exc))
                    return self._json({"deleted": True, "id": code})
                body, err = self._read_body()
                if err:
                    return err
                vals = {k: body[k] for k in self._POND_WRITABLE if k in body}
                ferr = _resolve_facility(body, vals)
                if ferr:
                    return self._error(422, "validation", ferr)
                if not vals:
                    return self._error(422, "validation", "No hay campos válidos para actualizar.")
                try:
                    p.sudo().write(vals)
                except Exception as exc:  # noqa: BLE001
                    return self._error(422, "validation", str(exc))
                return self._json({"pond": self._serialize_pond(p)})
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Error en API de piscinas")
            return self._error(500, "server_error", str(exc))
        return self._error(405, "method_not_allowed", "Método no soportado.")
