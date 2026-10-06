import base64
from urllib.parse import quote

from dateutil.relativedelta import relativedelta

from odoo import http, fields
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import ustr
from werkzeug.exceptions import NotFound, Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import (
    binary_headers, current_partner, flash_message, read_upload, to_number)

from .filter_drawer import (
    fd_context, fd_json_count, fd_range_label, fd_tags, fd_valid_date)

# Nombres de mes en espanol para las graficas del portal.
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun",
                "jul", "ago", "sep", "oct", "nov", "dic"]


class ShrimpTransactionPortalController(http.Controller):

    # Estados en que una compra se puede calificar: confirmada (pagada, con
    # el stock ya descontado) o completada (recibida). Se incluye
    # "confirmada" porque la pantalla de agradecimiento ofrece calificar justo
    # después de pagar, y esperar a la recepción —que en larvas puede ser
    # semanas después— dejaría sin reseña la mayoría de las compras. Lo que
    # no se puede calificar es una compra que nunca llegó a cerrarse
    # (borrador, pendiente de verificación/aceptación o cancelada).
    _RATEABLE_STATES = ("confirmed", "done")

    def _get_current_partner(self):
        return current_partner()

    def _get_product_for_purchase(self, product_ref):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()

        if product.state != "published" or product.available_qty <= 0:
            raise NotFound()

        return product

    def _check_buyer_can_buy_product(self, buyer_partner, product):
        """Corta con 403 a quien no puede comprar este lote.

        La regla es la del modelo (matriz de capacidades vía
        motivo_no_comprable): la misma que pinta o no el botón «Comprar» y la
        que aplica la restricción de la transacción.
        """
        if product.motivo_no_comprable(buyer_partner):
            raise Forbidden()
        return True

    # ------------------------------------------------------------------
    # Listas de transacciones (ventas, planificación, historial de compras
    # y «Mis compras»): filtros, cajón y conteo en vivo
    # ------------------------------------------------------------------
    TX_ORDERS = [
        ("", "Más recientes"),
        ("oldest", "Más antiguos"),
        ("qty_desc", "Mayor cantidad"),
        ("price_desc", "Mayor precio"),
        ("price_asc", "Menor precio"),
        ("delivery_asc", "Entrega más próxima"),
        ("delivery_desc", "Entrega más lejana"),
    ]
    # Estados que la planificación puede listar (lo impone su dominio).
    _PLANNING_STATES = ("confirmed", "done")

    @staticmethod
    def _tx_role_field(mode):
        """Con qué perfil participó la cuenta: vendió (sales) o compró."""
        return "seller_role" if mode == "sales" else "buyer_role"

    def _tx_role_labels(self, mode):
        Tx = request.env["shrimp.transaction"]
        return dict(Tx._fields[self._tx_role_field(mode)]._description_selection(request.env))

    def _tx_filters(self, kw, mode):
        """Parámetros de la lista, normalizados (un valor imposible se descarta
        en vez de reventar el dominio). Mismos nombres de siempre: los enlaces
        guardados siguen funcionando."""
        f = {k: (kw.get(k) or "").strip() for k in (
            "q", "tx_state", "stage", "location", "date_from", "date_to",
            "delivery_from", "delivery_to", "order", "role")}
        Tx = request.env["shrimp.transaction"]
        if f["tx_state"] not in dict(Tx._fields["state"].selection):
            f["tx_state"] = ""
        if f["stage"] and not f["stage"].isdigit():
            f["stage"] = ""
        for k in ("date_from", "date_to", "delivery_from", "delivery_to"):
            f[k] = fd_valid_date(f[k])
        if f["order"] not in dict(self.TX_ORDERS):
            f["order"] = ""
        if f["role"] not in self._tx_role_labels(mode):
            f["role"] = ""
        return f

    def _tx_base_domain(self, mode, partner):
        domain = [("product_id", "!=", False)]
        if mode == "purchase":
            domain += [("buyer_partner_id", "=", partner.id)]
        elif mode == "sales":
            domain += [("seller_partner_id", "=", partner.id)]
        elif mode == "planning":
            domain += [
                ("buyer_partner_id", "=", partner.id),
                ("state", "in", list(self._PLANNING_STATES)),
                ("product_id.expected_delivery_date", "!=", False),
            ]
        return domain

    def _build_tx_domain(self, mode, partner, kw):
        f = self._tx_filters(kw, mode)
        domain = self._tx_base_domain(mode, partner)

        q = f["q"]
        if q:
            domain += [
                "|", "|", "|", "|",
                ("name", "ilike", q),
                ("product_id.name", "ilike", q),
                ("seller_partner_id.name", "ilike", q),
                ("buyer_partner_id.name", "ilike", q),
                ("location", "ilike", q),
            ]
        if f["tx_state"]:
            domain += [("state", "=", f["tx_state"])]
        if f["stage"]:
            domain += [("product_id.stage_id", "=", int(f["stage"]))]
        if f["location"]:
            domain += ["|", ("location", "ilike", f["location"]),
                       ("product_id.location", "ilike", f["location"])]
        if f["date_from"]:
            domain += [("create_date", ">=", f"{f['date_from']} 00:00:00")]
        if f["date_to"]:
            domain += [("create_date", "<=", f"{f['date_to']} 23:59:59")]
        if f["delivery_from"]:
            domain += [("delivery_date", ">=", f["delivery_from"])]
        if f["delivery_to"]:
            domain += [("delivery_date", "<=", f["delivery_to"])]
        if f["role"]:
            domain += [(self._tx_role_field(mode), "=", f["role"])]
        return domain

    def _get_tx_order(self, kw):
        order = (kw.get("order") or "").strip()
        order_map = {
            "recent": "create_date desc",
            "oldest": "create_date asc",
            "delivery_asc": "delivery_date asc, create_date desc",
            "delivery_desc": "delivery_date desc, create_date desc",
            "qty_desc": "transaction_qty desc, create_date desc",
            "price_desc": "product_id.price desc, create_date desc",
            "price_asc": "product_id.price asc, create_date desc",
        }
        return order_map.get(order, "create_date desc")

    def _tx_filter_options(self, base, mode, f):
        """Opciones del cajón a partir de las transacciones que la cuenta
        TIENE en esta lista (no del catálogo entero): un semillero solo ve
        sus estadíos de nauplio, una cuenta con un solo perfil no ve
        «Perfil», un único estado o una única ubicación no se ofrecen."""
        Tx = request.env["shrimp.transaction"]
        estados = set(base.mapped("state"))
        if f["tx_state"]:
            estados.add(f["tx_state"])
        state_opts = [(v, l) for v, l in Tx._fields["state"].selection if v in estados]
        stages = base.mapped("product_id.stage_id").sorted(lambda s: (s.sequence, s.name or ""))
        locations = sorted({(t.product_id.location or t.location or "").strip() for t in base}
                           - {""})
        role_field = self._tx_role_field(mode)
        role_labels = self._tx_role_labels(mode)
        roles = set(base.mapped(role_field)) | ({f["role"]} if f["role"] else set())
        role_opts = [(v, l) for v, l in role_labels.items() if v in roles]
        show = {
            "state": len(state_opts) > 1,
            "stage": len(stages) > 1,
            "location": len(locations) > 1,
            "role": len(role_opts) > 1,
            "dates": len(base) > 1,
            "delivery": mode == "planning" and len(base) > 1,
        }
        return {"state_opts": state_opts, "stage_opts": stages, "location_opts": locations,
                "role_opts": role_opts, "fd_show": show}

    def _tx_fd(self, url, mode, f, total, opts, count_url, noun, sort=True):
        """Barra, etiquetas y cajón de una lista de transacciones."""
        Tx = request.env["shrimp.transaction"]
        estados = dict(Tx._fields["state"].selection)
        roles = self._tx_role_labels(mode)
        fecha = "Venta" if mode == "sales" else "Compra"

        def label(group, vals):
            key = group[0]
            if group == ("date_from", "date_to"):
                return fd_range_label(fecha, vals.get("date_from"), vals.get("date_to"))
            if group == ("delivery_from", "delivery_to"):
                return fd_range_label("Entrega", vals.get("delivery_from"), vals.get("delivery_to"))
            val = vals.get(key) or ""
            if not val:
                return None
            if key == "tx_state":
                return "Estado: %s" % estados.get(val, val)
            if key == "stage":
                st = request.env["shrimp.stage"].sudo().browse(int(val)).exists()
                return ("Estadío: %s" % st.name) if st else None
            if key == "location":
                return "Ubicación: %s" % val
            if key == "role":
                return "Perfil: %s" % roles.get(val, val)
            return None

        groups = [("tx_state",), ("stage",), ("role",), ("location",),
                  ("date_from", "date_to"), ("delivery_from", "delivery_to")]
        keep = {"q": f["q"], "order": f["order"] if sort else ""}
        tags, clear_url = fd_tags(url, f, groups, label, keep=keep, anchor="#listado")
        filtros = ("tx_state", "stage", "role", "location", "date_from", "date_to",
                   "delivery_from", "delivery_to")
        return fd_context(
            url, total, tags, clear_url,
            search={"name": "q", "value": f["q"],
                    "placeholder": "Referencia, producto, cliente…" if mode == "sales"
                    else "Referencia, producto, vendedor…",
                    "label": "Buscar"},
            toolbar_label="Buscar y ordenar",
            hidden=[(k, f[k]) for k in filtros],
            sort={"name": "order", "value": f["order"], "options": self.TX_ORDERS}
            if sort else None,
            count_url=count_url,
            noun=noun,
            drawer_hidden=[("q", f["q"]), ("order", f["order"])],
            keep=["q", "order"],
            has_drawer=any(opts["fd_show"].values()),
        )

    def _render_tx_list(self, mode, title, subtitle, base_url, **kw):
        partner = self._get_current_partner()
        Tx = request.env["shrimp.transaction"].sudo()
        f = self._tx_filters(kw, mode)
        txs = Tx.search(self._build_tx_domain(mode, partner, kw), order=self._get_tx_order(kw))
        base = Tx.search(self._tx_base_domain(mode, partner))

        # En modo ventas: reseñas que este vendedor ya dejó a sus compradores.
        buyer_reviews_by_tx = {}
        charges_by_tx = {}
        if mode == "sales" and txs:
            my_buyer_reviews = request.env["shrimp.review"].sudo().search([
                ("reviewer_partner_id", "=", partner.id),
                ("direction", "=", "to_buyer"),
                ("transaction_id", "in", txs.ids),
            ])
            buyer_reviews_by_tx = {
                r.transaction_id.id: r for r in my_buyer_reviews if r.transaction_id}
            # La comisión de cada venta (una consulta para toda la página, no
            # una por fila y por vista).
            for cobro in request.env["shrimp.charge"].sudo().search(
                    [("transaction_id", "in", txs.ids)]):
                charges_by_tx.setdefault(cobro.transaction_id.id, cobro)

        opts = self._tx_filter_options(base, mode, f)
        noun = {"sales": ("venta", "ventas")}.get(mode, ("compra", "compras"))
        values = {
            "page_name": "marketplace_history",
            "page_title": title,
            "page_subtitle": subtitle,
            "base_url": base_url,
            "mode": mode,
            "transactions": txs,
            "buyer_reviews_by_tx": buyer_reviews_by_tx,
            "charges_by_tx": charges_by_tx,
            "saved": kw.get("saved"),
            "filters": f,
            "stage_options": request.env["shrimp.stage"].sudo().search([]),
            "state_options": request.env["shrimp.transaction"]._fields["state"].selection,
            "currency": request.env.company.currency_id,
            "fd": self._tx_fd(base_url, mode, f, len(txs), opts, base_url + "/count", noun),
        }
        values.update(opts)
        return request.render("shrimp_marketplace.marketplace_transaction_history", values)

    def _tx_count(self, mode, kw):
        partner = self._get_current_partner()
        return fd_json_count(request.env["shrimp.transaction"].sudo().search_count(
            self._build_tx_domain(mode, partner, kw)))

    @http.route("/marketplace/buy/<product_ref>", type="http", auth="user", website=True)
    def buy_product(self, product_ref, **kw):
        product = self._get_product_for_purchase(product_ref)
        buyer_partner = self._get_current_partner()

        self._check_buyer_can_buy_product(buyer_partner, product)

        seller = product.seller_partner_id
        reviews = seller.sudo()._shrimp_seller_reviews(limit=20)

        check_fee = float(request.env["ir.config_parameter"].sudo().get_param(
            "shrimp_marketplace.check_fee") or 0.0)

        unit_price = product.price_for_partner(buyer_partner)
        has_custom_price = unit_price != product.price

        return request.render("shrimp_marketplace.buy_product", {
            "product": product,
            "seller": seller,
            "reviews": reviews,
            "check_fee": check_fee,
            "unit_price": unit_price,
            "has_custom_price": has_custom_price,
        })

    @http.route("/marketplace/buy/<product_ref>/confirm", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def buy_product_confirm(self, product_ref, **post):
        product = self._get_product_for_purchase(product_ref)

        buyer_partner = self._get_current_partner()
        self._check_buyer_can_buy_product(buyer_partner, product)

        qty = to_number(post.get("qty"))

        if qty <= 0:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=qty")

        if qty > product.available_qty:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=stock")

        try:
            # La compra (y su comisión, que registra la propia transacción al
            # confirmarse) va en un savepoint: si el modelo la rechaza no queda
            # nada a medias. Un lote con verificación obligatoria lo rechaza
            # el modelo aunque el POST se haya fabricado a mano.
            with request.env.cr.savepoint():
                result = product.sudo().execute_purchase_flow(buyer_partner, qty)
        except ValidationError as e:
            msg = flash_message(e.args[0] if e.args else ustr(e))
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=validation&message={msg}")

        tx = result["transaction"]
        return request.redirect(f"/marketplace/thanks/{tx.uuid_ref}?paid=1")

    @http.route("/marketplace/buy/<product_ref>/request-check", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def request_check(self, product_ref, **post):
        """RETIRADA. La solicitud de chequeo se sustituyó por la compra con
        verificación en campo (shrimp_verification), que cubre lo mismo con
        técnico, informe y firma de las partes. La ruta se conserva para que
        un formulario viejo no termine en un 404: devuelve a la ficha con la
        explicación y no crea nada (ni cobra nada)."""
        product = self._get_product_for_purchase(product_ref)
        msg = flash_message(
            "La solicitud de chequeo ya no está disponible: compra con "
            "verificación en campo para que un verificador acreditado inspeccione "
            "el producto antes de concretar la compra.")
        return request.redirect(
            f"/marketplace/buy/{product.uuid_ref}?error=validation&message={msg}")

    @http.route("/marketplace/check-request/thanks/<check_request_ref>", type="http", auth="user", website=True, methods=["GET"])
    def check_request_thanks(self, check_request_ref, **kwargs):
        check_request = request.env["shrimp.check.request"].sudo().resolve_ref(check_request_ref)

        if not check_request:
            raise NotFound()

        current_partner = self._get_current_partner()
        if current_partner not in (check_request.buyer_partner_id, check_request.seller_partner_id):
            raise NotFound()

        return request.render("shrimp_marketplace.check_request_thanks", {
            "check_request": check_request,
        })

    @http.route("/marketplace/thanks/<tx_ref>", type="http", auth="user", website=True)
    def marketplace_thanks(self, tx_ref, **kw):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()

        partner = self._get_current_partner()
        if tx.buyer_partner_id.id != partner.id and tx.seller_partner_id.id != partner.id:
            raise Forbidden()

        my_review = request.env["shrimp.review"].sudo().search([
            ("reviewer_partner_id", "=", partner.id),
            ("transaction_id", "=", tx.id),
        ], limit=1)
        can_rate = bool(
            tx.buyer_partner_id.id == partner.id
            and not my_review
            and tx.state in self._RATEABLE_STATES)

        return request.render("shrimp_marketplace.buy_thanks", {
            "tx": tx,
            "my_review": my_review,
            "can_rate": can_rate,
        })

    def _purchases_domain(self, partner, kw):
        """Dominio de «Mis compras» (/marketplace/purchases): lo usan la página
        y el conteo del cajón."""
        f = self._tx_filters(kw, "purchase")
        domain = [("buyer_partner_id", "=", partner.id)]
        if f["q"]:
            domain += ["|", "|",
                ("name", "ilike", f["q"]),
                ("product_id.name", "ilike", f["q"]),
                ("seller_partner_id.name", "ilike", f["q"]),
            ]
        if f["tx_state"]:
            domain.append(("state", "=", f["tx_state"]))
        if f["stage"]:
            domain.append(("product_id.stage_id", "=", int(f["stage"])))
        if f["location"]:
            domain.append(("product_id.location", "ilike", f["location"]))
        if f["date_from"]:
            domain.append(("create_date", ">=", f"{f['date_from']} 00:00:00"))
        if f["date_to"]:
            domain.append(("create_date", "<=", f"{f['date_to']} 23:59:59"))
        if f["role"]:
            domain.append(("buyer_role", "=", f["role"]))
        return domain, f

    @http.route("/marketplace/purchases", type="http", auth="user", website=True)
    def marketplace_purchased_products(self, **kw):
        partner = self._get_current_partner()
        Tx = request.env["shrimp.transaction"].sudo()
        domain, f = self._purchases_domain(partner, kw)
        # Esta lista no filtra por entrega ni tiene orden.
        f.update(delivery_from="", delivery_to="", order="")

        transactions = Tx.search(domain, order="create_date desc")
        base = Tx.search([("buyer_partner_id", "=", partner.id)])
        opts = self._tx_filter_options(base, "purchase", f)

        # Transacciones que este comprador ya calificó (para mostrar la reseña)
        my_reviews = request.env["shrimp.review"].sudo().search(
            [("reviewer_partner_id", "=", partner.id)])
        reviewed_tx_ids = set(my_reviews.mapped("transaction_id").ids)
        reviews_by_tx = {r.transaction_id.id: r for r in my_reviews if r.transaction_id}

        values = {
            "page_name": "marketplace_purchased_products",
            "mode": "purchase",
            "base_url": "/marketplace/purchases",
            "page_title": "Productos comprados",
            "page_subtitle": "Consulta todos los productos que has comprado y descarga su trazabilidad.",
            "transactions": transactions,
            "reviewed_tx_ids": reviewed_tx_ids,
            "reviews_by_tx": reviews_by_tx,
            "today": fields.Date.context_today(request.env.user),
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "error_message": kw.get("message"),
            "filters": f,
            "stage_options": request.env["shrimp.stage"].sudo().search([]),
            "state_options": request.env["shrimp.transaction"]._fields["state"].selection,
            "currency": request.env.company.currency_id,
            "fd": self._tx_fd("/marketplace/purchases", "purchase", f, len(transactions), opts,
                              "/marketplace/purchases/count", ("compra", "compras"), sort=False),
        }
        values.update(opts)
        # «Entrega» es solo de la planificación.
        values["fd_show"] = dict(values["fd_show"], delivery=False)
        return request.render("shrimp_marketplace.marketplace_purchased_products", values)

    @http.route("/marketplace/purchases/count", type="http", auth="user", website=True,
                methods=["GET"], sitemap=False)
    def marketplace_purchased_products_count(self, **kw):
        """«Ver N compras» del cajón: mismo dominio que la lista."""
        domain, _f = self._purchases_domain(self._get_current_partner(), kw)
        return fd_json_count(request.env["shrimp.transaction"].sudo().search_count(domain))

    @http.route("/marketplace/purchases/<tx_ref>/rate", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_rate_seller(self, tx_ref, **post):
        partner = self._get_current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        # Solo el comprador de esa transacción puede calificar
        if not tx or tx.buyer_partner_id.id != partner.id:
            raise Forbidden()
        # Solo compras reales: una pendiente de verificación, cancelada o en
        # borrador no es una operación que se pueda reseñar.
        if tx.state not in self._RATEABLE_STATES:
            return request.redirect("/marketplace/purchases?error=rating_state")
        try:
            rating = int(post.get("rating") or 0)
        except ValueError:
            rating = 0
        if not (1 <= rating <= 5):
            return request.redirect("/marketplace/purchases?error=rating")

        comment = (post.get("comment") or "").strip()
        Review = request.env["shrimp.review"].sudo()
        existing = Review.search([
            ("reviewer_partner_id", "=", partner.id),
            ("transaction_id", "=", tx.id),
            ("direction", "=", "to_seller"),
        ], limit=1)
        if existing:
            existing.write({"rating": rating, "comment": comment})
        else:
            Review.create({
                "direction": "to_seller",
                "seller_partner_id": tx.seller_partner_id.id,
                "reviewer_partner_id": partner.id,
                "transaction_id": tx.id,
                "rating": rating,
                "comment": comment,
            })
        return request.redirect("/marketplace/purchases?saved=review")

    @http.route("/marketplace/purchases/<tx_ref>/receive", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_receive_purchase(self, tx_ref, **post):
        """El comprador confirma la recepción de la compra: sube su inventario
        (producto en borrador), respetando la fecha de entrega."""
        partner = self._get_current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx or tx.buyer_partner_id.id != partner.id:
            raise Forbidden()
        try:
            tx.action_receive()
        except ValidationError as e:
            msg = flash_message(e.args[0] if e.args else ustr(e))
            return request.redirect(f"/marketplace/purchases?error=receive&message={msg}")
        return request.redirect("/marketplace/purchases?saved=received")

    @http.route("/marketplace/sales/<tx_ref>/rate-buyer", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_rate_buyer(self, tx_ref, **post):
        """El vendedor de la transacción califica al comprador."""
        partner = self._get_current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        # Solo el vendedor de esa transacción puede calificar al comprador
        if not tx or tx.seller_partner_id.id != partner.id:
            raise Forbidden()
        if tx.state not in self._RATEABLE_STATES:
            return request.redirect("/marketplace/sales?error=rating_state")
        try:
            rating = int(post.get("rating") or 0)
        except ValueError:
            rating = 0
        if not (1 <= rating <= 5):
            return request.redirect("/marketplace/sales?error=rating")

        comment = (post.get("comment") or "").strip()
        Review = request.env["shrimp.review"].sudo()
        existing = Review.search([
            ("reviewer_partner_id", "=", partner.id),
            ("transaction_id", "=", tx.id),
            ("direction", "=", "to_buyer"),
        ], limit=1)
        if existing:
            existing.write({"rating": rating, "comment": comment})
        else:
            Review.create({
                "direction": "to_buyer",
                "seller_partner_id": tx.buyer_partner_id.id,   # calificado = comprador
                "reviewer_partner_id": partner.id,             # autor = vendedor
                "transaction_id": tx.id,
                "rating": rating,
                "comment": comment,
            })
        return request.redirect("/marketplace/sales?saved=review")

    @http.route("/marketplace/reports", type="http", auth="user", website=True)
    def marketplace_my_reports(self, **kw):
        """#12 — Panel de reportes personales del usuario en el portal:
        resumen de sus ventas, compras y comisiones, con series mensuales."""
        partner = self._get_current_partner()
        Tx = request.env["shrimp.transaction"].sudo()
        Charge = request.env["shrimp.charge"].sudo()

        # Solo transacciones "reales" (confirmadas o completadas) para los totales
        real_states = ["confirmed", "done"]
        sales = Tx.search([
            ("seller_partner_id", "=", partner.id),
            ("state", "in", real_states),
        ])
        purchases = Tx.search([
            ("buyer_partner_id", "=", partner.id),
            ("state", "in", real_states),
        ])
        # Cobros de la plataforma a este socio (comisiones, verificaciones,
        # empaque), sin los anulados ni los acreditados.
        charges = Charge.search([("payer_partner_id", "=", partner.id),
                                 ("state", "not in", ("cancelled", "credited", "to_credit"))])

        def _sum(recs, field):
            return sum(recs.mapped(field) or [0.0])

        sales_total = _sum(sales, "amount_total")
        purchases_total = _sum(purchases, "amount_total")
        # «Comisiones pagadas» son SOLO las comisiones por venta. Antes se
        # sumaban todos los cobros (honorarios de verificación que paga el
        # comprador, cuotas de empaque que paga el maquilador) y el «ingreso
        # neto» de las ventas restaba gastos que no son de las ventas.
        por_tipo = lambda tipo: charges.filtered(lambda c: c.charge_type == tipo)
        commission_total = _sum(por_tipo("commission"), "amount")
        verification_fees_total = _sum(por_tipo("verification_fee"), "amount")
        copack_fees_total = _sum(por_tipo("copack_platform"), "amount")
        check_fees_total = _sum(por_tipo("check_fee"), "amount")
        net_sales = sales_total - commission_total
        sales_count = len(sales)
        purchases_count = len(purchases)
        avg_sale = sales_total / sales_count if sales_count else 0.0
        avg_purchase = purchases_total / purchases_count if purchases_count else 0.0

        # --- Serie mensual (últimos 6 meses) ---
        today = fields.Date.context_today(request.env.user)
        start = today.replace(day=1) - relativedelta(months=5)
        month_keys = []
        months = []
        for i in range(6):
            m = start + relativedelta(months=i)
            key = m.strftime("%Y-%m")
            month_keys.append(key)
            months.append({
                "key": key,
                # strftime("%b") depende del locale del proceso y sale en
                # ingles ("Apr 26") dentro de una interfaz en espanol.
                "label": "%s %02d" % (MESES_CORTOS[m.month - 1], m.year % 100),
                "sales": 0.0,
                "purchases": 0.0,
            })
        month_index = {m["key"]: m for m in months}

        def _bucket(recs, field):
            for rec in recs:
                if not rec.create_date:
                    continue
                key = rec.create_date.strftime("%Y-%m")
                slot = month_index.get(key)
                if slot:
                    slot[field] += rec.amount_total

        _bucket(sales, "sales")
        _bucket(purchases, "purchases")
        month_max = max([m["sales"] for m in months] + [m["purchases"] for m in months] + [1.0])

        # --- Top productos vendidos / comprados (por monto) ---
        def _top_products(recs, limit=5):
            agg = {}
            for rec in recs:
                prod = rec.product_id
                if not prod:
                    continue
                data = agg.setdefault(prod.id, {"name": prod.name, "amount": 0.0, "qty": 0.0})
                data["amount"] += rec.amount_total
                data["qty"] += rec.transaction_qty
            rows = sorted(agg.values(), key=lambda r: r["amount"], reverse=True)[:limit]
            top_max = max([r["amount"] for r in rows] + [1.0])
            for r in rows:
                r["pct"] = round(100.0 * r["amount"] / top_max, 1)
            return rows

        top_sold = _top_products(sales)
        top_bought = _top_products(purchases)

        currency = request.env.company.currency_id

        return request.render("shrimp_marketplace.marketplace_my_reports", {
            "page_name": "marketplace_my_reports",
            "page_title": "Mis reportes",
            "page_subtitle": "Resumen de tu actividad: ventas, compras y comisiones.",
            "partner": partner,
            "currency": currency,
            "kpi": {
                "sales_total": sales_total,
                "purchases_total": purchases_total,
                "commission_total": commission_total,
                "verification_fees_total": verification_fees_total,
                "copack_fees_total": copack_fees_total,
                "check_fees_total": check_fees_total,
                "net_sales": net_sales,
                "sales_count": sales_count,
                "purchases_count": purchases_count,
                "avg_sale": avg_sale,
                "avg_purchase": avg_purchase,
            },
            "months": months,
            "month_max": month_max,
            "top_sold": top_sold,
            "top_bought": top_bought,
        })

    @http.route(["/marketplace/purchase-history"], type="http", auth="user", website=True)
    def marketplace_purchase_history(self, **kw):
        return self._render_tx_list(
            mode="purchase",
            title="Historial de compras",
            subtitle="Consulta todos los productos que has comprado a lo largo del tiempo.",
            # Antes los filtros de esta página llevaban a /marketplace/purchases
            # (otra pantalla): ahora se quedan en la propia lista.
            base_url="/marketplace/purchase-history",
            **kw,
        )

    @http.route("/marketplace/purchase-history/count", type="http", auth="user",
                website=True, methods=["GET"], sitemap=False)
    def marketplace_purchase_history_count(self, **kw):
        return self._tx_count("purchase", kw)

    @http.route("/marketplace/purchases/<tx_ref>/traceability/pdf", type="http", auth="user", website=True)
    def marketplace_purchase_traceability_pdf(self, tx_ref, **kw):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)

        if not tx:
            raise NotFound()

        partner = self._get_current_partner()
        is_internal = request.env.user.has_group("base.group_user")

        if not is_internal and tx.buyer_partner_id.id != partner.id:
            raise Forbidden()

        # No hay trazabilidad que certificar mientras la compra no se cerró
        # (pendiente de verificación o de las firmas, borrador o cancelada).
        if not is_internal and tx.state not in self._RATEABLE_STATES:
            raise NotFound()

        report = request.env["ir.actions.report"].sudo()._get_report_from_name(
            "shrimp_marketplace.report_shrimp_full_traceability"
        )

        if not report:
            raise NotFound()

        pdf_content, _ = report.sudo()._render_qweb_pdf(
            report.report_name,
            res_ids=[tx.id],
        )

        safe_tx_name = (tx.name or "trazabilidad").replace("/", "-").replace("\\", "-")
        filename = f"Certificado-Trazabilidad-{safe_tx_name}.pdf"

        # Por defecto se visualiza en el navegador; con ?download=1 se descarga.
        disposition = "attachment" if kw.get("download") else "inline"
        pdfhttpheaders = [
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(pdf_content))),
            ("Content-Disposition", f'{disposition}; filename="{filename}"'),
            ("X-Content-Type-Options", "nosniff"),
        ]

        return request.make_response(pdf_content, headers=pdfhttpheaders)

    @http.route("/marketplace/purchases/<tx_ref>/invoice/pdf", type="http", auth="user", website=True)
    def marketplace_purchase_invoice_pdf(self, tx_ref, **kw):
        """Comprobante de la compra en PDF, SIN efectos secundarios.

        La plataforma no factura mercadería: la factura es del VENDEDOR, que la
        registra en la compra (ver /marketplace/purchases/<ref>/seller-invoice).
        Aquí se sirve el comprobante no fiscal de la operación («sin valor
        tributario»), o la factura histórica si la compra es de cuando la
        plataforma todavía la emitía.
        """
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()
        partner = self._get_current_partner()
        if partner.id not in (tx.buyer_partner_id.id, tx.seller_partner_id.id):
            raise NotFound()
        if tx.state not in self._RATEABLE_STATES:
            raise NotFound()

        invoice = tx.invoice_id
        if invoice and invoice.state == "posted":
            report = request.env.ref("account.account_invoices", raise_if_not_found=False)
            report = report.sudo() if report else request.env["ir.actions.report"].sudo()._get_report_from_name(
                "account.report_invoice")
            if not report:
                raise NotFound()
            pdf_content, _ = report._render_qweb_pdf(report.report_name, res_ids=[invoice.id])
            prefijo = "Factura"
        else:
            report = request.env.ref(
                "shrimp_marketplace.action_report_shrimp_purchase_receipt").sudo()
            pdf_content, _ = report._render_qweb_pdf(report.report_name, res_ids=[tx.id])
            prefijo = "Comprobante"
        safe = (tx.name or "compra").replace("/", "-").replace("\\", "-")
        filename = f"{prefijo}-{safe}.pdf"
        disposition = "attachment" if kw.get("download") else "inline"
        return request.make_response(pdf_content, headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Length", str(len(pdf_content))),
            ("Content-Disposition", f'{disposition}; filename="{filename}"'),
            ("X-Content-Type-Options", "nosniff"),
            ("Cache-Control", "private, max-age=0"),
        ])

    # ------------------------------------------------------------------
    # Factura del vendedor (paso 2 de la facturación)
    # ------------------------------------------------------------------
    # El vendedor emite la factura de la mercadería en su propio sistema y
    # aquí deja constancia (opcional): número, clave de acceso, fecha y el
    # XML/PDF. La ven el comprador y el vendedor; no sale en la trazabilidad
    # pública (/t/<token>).
    _SELLER_INVOICE_MIMETYPES = {"application/pdf", "application/xml", "text/xml",
                                 "text/plain", "image/png", "image/jpeg"}

    def _tx_de_parte(self, tx_ref, solo_vendedor=False):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()
        partner = self._get_current_partner()
        if solo_vendedor:
            if tx.seller_partner_id != partner:
                raise NotFound()
        elif partner not in (tx.buyer_partner_id, tx.seller_partner_id):
            raise NotFound()
        return tx

    @http.route("/marketplace/sales/<tx_ref>/invoice", type="http", auth="user",
                website=True, methods=["GET"])
    def seller_invoice_form(self, tx_ref, **kw):
        tx = self._tx_de_parte(tx_ref, solo_vendedor=True)
        if tx.state not in self._RATEABLE_STATES:
            raise NotFound()
        return request.render("shrimp_marketplace.seller_invoice_form", {
            "tx": tx,
            "error": kw.get("error"),
            "page_name": "marketplace_history",
        })

    @http.route("/marketplace/sales/<tx_ref>/invoice", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def seller_invoice_save(self, tx_ref, **post):
        tx = self._tx_de_parte(tx_ref, solo_vendedor=True)
        destino = f"/marketplace/sales/{tx.uuid_ref}/invoice"
        try:
            with request.env.cr.savepoint():
                vals = {
                    "number": post.get("seller_invoice_number"),
                    "access_key": post.get("seller_invoice_access_key"),
                    "date": post.get("seller_invoice_date") or False,
                }
                archivo = request.httprequest.files.get("seller_invoice_file")
                if archivo:
                    contenido, mime, nombre = read_upload(
                        archivo, allowed=self._SELLER_INVOICE_MIMETYPES)
                    if contenido:
                        adjunto = request.env["ir.attachment"].sudo().create({
                            "name": nombre, "datas": base64.b64encode(contenido),
                            "mimetype": mime, "res_model": "shrimp.transaction",
                            "res_id": tx.id,
                        })
                        vals["attachment_id"] = adjunto.id
                tx.action_register_seller_invoice(vals, actor=self._get_current_partner())
        except (ValidationError, AccessError) as e:
            return request.redirect(f"{destino}?error={flash_message(e.args[0] if e.args else ustr(e))}")
        return request.redirect("/marketplace/sales?saved=invoice")

    @http.route("/marketplace/purchases/<tx_ref>/seller-invoice", type="http", auth="user",
                website=True)
    def seller_invoice_file(self, tx_ref, **kw):
        """El archivo de la factura del vendedor, solo para las dos partes."""
        tx = self._tx_de_parte(tx_ref)
        adjunto = tx.seller_invoice_attachment_id.sudo()
        if not adjunto or not adjunto.raw:
            raise NotFound()
        return request.make_response(adjunto.raw, headers=binary_headers(
            adjunto.raw, adjunto.mimetype, adjunto.name, download=bool(kw.get("download"))))

    @http.route("/marketplace/sales", type="http", auth="user", website=True)
    def marketplace_sales_history(self, **kw):
        return self._render_tx_list(
            mode="sales",
            title="Ventas realizadas",
            subtitle="Consulta los productos que otros usuarios te han comprado.",
            base_url="/marketplace/sales",
            **kw,
        )

    @http.route("/marketplace/sales/count", type="http", auth="user", website=True,
                methods=["GET"], sitemap=False)
    def marketplace_sales_history_count(self, **kw):
        """«Ver N ventas» del cajón: mismo dominio que la lista."""
        return self._tx_count("sales", kw)

    @http.route("/marketplace/planning", type="http", auth="user", website=True)
    def marketplace_planning(self, **kw):
        return self._render_tx_list(
            mode="planning",
            title="Planificación",
            subtitle="Consulta productos comprados que ya tienen entrega planificada.",
            base_url="/marketplace/planning",
            **kw,
        )

    @http.route("/marketplace/planning/count", type="http", auth="user", website=True,
                methods=["GET"], sitemap=False)
    def marketplace_planning_count(self, **kw):
        return self._tx_count("planning", kw)
