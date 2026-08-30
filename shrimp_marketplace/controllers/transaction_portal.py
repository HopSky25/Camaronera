from datetime import date
from urllib.parse import quote

from dateutil.relativedelta import relativedelta

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.tools import ustr
from werkzeug.exceptions import NotFound, Forbidden


class ShrimpTransactionPortalController(http.Controller):

    def _get_current_partner(self):
        return request.env.user.partner_id

    def _get_product_for_purchase(self, product_ref):
        product = request.env["shrimp.product"].sudo().resolve_ref(product_ref)
        if not product or not product.active:
            raise NotFound()

        if product.state != "published" or product.available_qty <= 0:
            raise NotFound()

        return product

    def _check_buyer_can_buy_product(self, buyer_partner, product):
        seller_type = product.seller_partner_id.shrimp_user_type
        buyer_type = buyer_partner.shrimp_user_type

        if seller_type == "semillero" and buyer_type != "laboratorio":
            raise Forbidden()

        if seller_type == "laboratorio" and buyer_type != "camaronera":
            raise Forbidden()

        if buyer_partner.id == product.seller_partner_id.id:
            raise Forbidden()

        return True

    def _build_tx_domain(self, mode, partner, kw):
        domain = [("product_id", "!=", False)]

        if mode == "purchase":
            domain += [("buyer_partner_id", "=", partner.id)]
        elif mode == "sales":
            domain += [("seller_partner_id", "=", partner.id)]
        elif mode == "planning":
            domain += [
                ("buyer_partner_id", "=", partner.id),
                ("state", "in", ["confirmed", "done"]),
                ("product_id.expected_delivery_date", "!=", False),
            ]

        q = (kw.get("q") or "").strip()
        tx_state = (kw.get("tx_state") or "").strip()
        stage = (kw.get("stage") or "").strip()
        location = (kw.get("location") or "").strip()
        date_from = (kw.get("date_from") or "").strip()
        date_to = (kw.get("date_to") or "").strip()
        delivery_from = (kw.get("delivery_from") or "").strip()
        delivery_to = (kw.get("delivery_to") or "").strip()

        if q:
            domain += [
                "|", "|", "|", "|",
                ("name", "ilike", q),
                ("product_id.name", "ilike", q),
                ("seller_partner_id.name", "ilike", q),
                ("buyer_partner_id.name", "ilike", q),
                ("location", "ilike", q),
            ]

        if tx_state:
            domain += [("state", "=", tx_state)]

        if stage:
            try:
                domain += [("product_id.stage_id", "=", int(stage))]
            except (TypeError, ValueError):
                pass

        if location:
            domain += ["|", ("location", "ilike", location), ("product_id.location", "ilike", location)]

        if date_from:
            domain += [("create_date", ">=", f"{date_from} 00:00:00")]

        if date_to:
            domain += [("create_date", "<=", f"{date_to} 23:59:59")]

        if delivery_from:
            domain += [("product_id.expected_delivery_date", ">=", delivery_from)]

        if delivery_to:
            domain += [("product_id.expected_delivery_date", "<=", delivery_to)]

        return domain

    def _get_tx_order(self, kw):
        order = (kw.get("order") or "").strip()
        order_map = {
            "recent": "create_date desc",
            "oldest": "create_date asc",
            "delivery_asc": "product_id.expected_delivery_date asc, create_date desc",
            "delivery_desc": "product_id.expected_delivery_date desc, create_date desc",
            "qty_desc": "transaction_qty desc, create_date desc",
            "price_desc": "product_id.price desc, create_date desc",
            "price_asc": "product_id.price asc, create_date desc",
        }
        return order_map.get(order, "create_date desc")

    def _render_tx_list(self, mode, title, subtitle, base_url, **kw):
        partner = self._get_current_partner()
        txs = request.env["shrimp.transaction"].sudo().search(
            self._build_tx_domain(mode, partner, kw),
            order=self._get_tx_order(kw),
        )

        return request.render("shrimp_marketplace.marketplace_transaction_history", {
            "page_name": "marketplace_history",
            "page_title": title,
            "page_subtitle": subtitle,
            "base_url": base_url,
            "mode": mode,
            "transactions": txs,
            "filters": {
                "q": kw.get("q") or "",
                "tx_state": kw.get("tx_state") or "",
                "stage": kw.get("stage") or "",
                "location": kw.get("location") or "",
                "date_from": kw.get("date_from") or "",
                "date_to": kw.get("date_to") or "",
                "delivery_from": kw.get("delivery_from") or "",
                "delivery_to": kw.get("delivery_to") or "",
                "order": kw.get("order") or "",
            },
            "stage_options": request.env["shrimp.stage"].sudo().search([]),
            "state_options": request.env["shrimp.transaction"]._fields["state"].selection,
        })

    @http.route("/marketplace/buy/<product_ref>", type="http", auth="user", website=True)
    def buy_product(self, product_ref, **kw):
        product = self._get_product_for_purchase(product_ref)
        buyer_partner = self._get_current_partner()

        self._check_buyer_can_buy_product(buyer_partner, product)

        seller = product.seller_partner_id
        reviews = request.env["shrimp.review"].sudo().search(
            [("seller_partner_id", "=", seller.id)],
            order="create_date desc", limit=20)

        check_fee = float(request.env["ir.config_parameter"].sudo().get_param(
            "shrimp_marketplace.check_fee") or 0.0)

        return request.render("shrimp_marketplace.buy_product", {
            "product": product,
            "seller": seller,
            "reviews": reviews,
            "check_fee": check_fee,
        })

    @http.route("/marketplace/buy/<product_ref>/confirm", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def buy_product_confirm(self, product_ref, **post):
        product = self._get_product_for_purchase(product_ref)

        buyer_partner = self._get_current_partner()
        self._check_buyer_can_buy_product(buyer_partner, product)

        try:
            qty = float(post.get("qty") or 0.0)
        except Exception:
            qty = 0.0

        if qty <= 0:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=qty")

        if qty > product.available_qty:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=stock")

        try:
            result = product.sudo().execute_purchase_flow(buyer_partner, qty)
        except ValidationError as e:
            msg = quote(ustr(e))
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=validation&message={msg}")

        tx = result["transaction"]
        # #10 — registrar el cobro de comisión del marketplace por esta venta
        request.env["shrimp.charge"].sudo().register_for_transaction(tx, qty)
        return request.redirect(f"/marketplace/thanks/{tx.uuid_ref}?paid=1")

    @http.route("/marketplace/buy/<product_ref>/request-check", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def request_check(self, product_ref, **post):
        product = self._get_product_for_purchase(product_ref)

        buyer_partner = self._get_current_partner()
        seller_partner = product.seller_partner_id

        self._check_buyer_can_buy_product(buyer_partner, product)

        try:
            qty = float(post.get("qty") or 0.0)
        except Exception:
            qty = 0.0

        if qty <= 0:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=qty")

        if qty > product.available_qty:
            return request.redirect(f"/marketplace/buy/{product.uuid_ref}?error=stock")

        check_fee = float(request.env["ir.config_parameter"].sudo().get_param(
            "shrimp_marketplace.check_fee") or 0.0)

        check_request = request.env["shrimp.check.request"].sudo().create({
            "product_id": product.id,
            "seller_partner_id": seller_partner.id,
            "buyer_partner_id": buyer_partner.id,
            "qty": qty,
            "state": "requested",
            "check_fee": check_fee,
        })

        # Cobra el chequeo al comprador y lo lleva a Ventas (pedido + factura).
        check_request._create_check_sale_documents()

        template = request.env.ref("shrimp_marketplace.mail_template_shrimp_check_request", raise_if_not_found=False)
        if template:
            if seller_partner.email:
                template.sudo().send_mail(check_request.id, force_send=True, email_values={
                    "email_to": seller_partner.email,
                })
            if buyer_partner.email:
                template.sudo().send_mail(check_request.id, force_send=True, email_values={
                    "email_to": buyer_partner.email,
                })

        return request.redirect(f"/marketplace/check-request/thanks/{check_request.uuid_ref}")

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
            and tx.state in ("confirmed", "done"))

        return request.render("shrimp_marketplace.buy_thanks", {
            "tx": tx,
            "my_review": my_review,
            "can_rate": can_rate,
        })

    @http.route("/marketplace/compras", type="http", auth="user", website=True)
    def marketplace_purchased_products(self, **kw):
        partner = self._get_current_partner()

        domain = [
            ("buyer_partner_id", "=", partner.id),
        ]

        q = (kw.get("q") or "").strip()
        tx_state = (kw.get("tx_state") or "").strip()
        stage = (kw.get("stage") or "").strip()
        location = (kw.get("location") or "").strip()
        date_from = (kw.get("date_from") or "").strip()
        date_to = (kw.get("date_to") or "").strip()

        if q:
            domain += ["|", "|",
                ("name", "ilike", q),
                ("product_id.name", "ilike", q),
                ("seller_partner_id.name", "ilike", q),
            ]

        if tx_state:
            domain.append(("state", "=", tx_state))

        if stage:
            try:
                domain.append(("product_id.stage_id", "=", int(stage)))
            except (TypeError, ValueError):
                pass

        if location:
            domain.append(("product_id.location", "ilike", location))

        if date_from:
            domain.append(("create_date", ">=", f"{date_from} 00:00:00"))

        if date_to:
            domain.append(("create_date", "<=", f"{date_to} 23:59:59"))

        transactions = request.env["shrimp.transaction"].sudo().search(
            domain,
            order="create_date desc",
        )

        # Transacciones que este comprador ya calificó (para mostrar la reseña)
        my_reviews = request.env["shrimp.review"].sudo().search(
            [("reviewer_partner_id", "=", partner.id)])
        reviewed_tx_ids = set(my_reviews.mapped("transaction_id").ids)
        reviews_by_tx = {r.transaction_id.id: r for r in my_reviews if r.transaction_id}

        return request.render("shrimp_marketplace.marketplace_purchased_products", {
            "page_name": "marketplace_purchased_products",
            "page_title": "Productos comprados",
            "page_subtitle": "Consulta todos los productos que has comprado y descarga su trazabilidad.",
            "transactions": transactions,
            "reviewed_tx_ids": reviewed_tx_ids,
            "reviews_by_tx": reviews_by_tx,
            "filters": {
                "q": q,
                "tx_state": tx_state,
                "stage": stage,
                "location": location,
                "date_from": date_from,
                "date_to": date_to,
            },
            "stage_options": request.env["shrimp.stage"].sudo().search([]),
            "state_options": request.env["shrimp.transaction"]._fields["state"].selection,
        })

    @http.route("/marketplace/compras/<tx_ref>/calificar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def marketplace_rate_seller(self, tx_ref, **post):
        partner = self._get_current_partner()
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        # Solo el comprador de esa transacción puede calificar
        if not tx or tx.buyer_partner_id.id != partner.id:
            raise Forbidden()
        try:
            rating = int(post.get("rating") or 0)
        except ValueError:
            rating = 0
        if not (1 <= rating <= 5):
            return request.redirect("/marketplace/compras?error=rating")

        comment = (post.get("comment") or "").strip()
        Review = request.env["shrimp.review"].sudo()
        existing = Review.search([
            ("reviewer_partner_id", "=", partner.id),
            ("transaction_id", "=", tx.id),
        ], limit=1)
        if existing:
            existing.write({"rating": rating, "comment": comment})
        else:
            Review.create({
                "seller_partner_id": tx.seller_partner_id.id,
                "reviewer_partner_id": partner.id,
                "transaction_id": tx.id,
                "rating": rating,
                "comment": comment,
            })
        return request.redirect("/marketplace/compras?saved=review")

    @http.route("/marketplace/reportes", type="http", auth="user", website=True)
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
        charges = Charge.search([("seller_partner_id", "=", partner.id)])

        def _sum(recs, field):
            return sum(recs.mapped(field) or [0.0])

        sales_total = _sum(sales, "amount_total")
        purchases_total = _sum(purchases, "amount_total")
        commission_total = _sum(charges, "amount")
        net_sales = sales_total - commission_total
        sales_count = len(sales)
        purchases_count = len(purchases)
        avg_sale = sales_total / sales_count if sales_count else 0.0
        avg_purchase = purchases_total / purchases_count if purchases_count else 0.0

        # --- Serie mensual (últimos 6 meses) ---
        today = date.today()
        start = today.replace(day=1) - relativedelta(months=5)
        month_keys = []
        months = []
        for i in range(6):
            m = start + relativedelta(months=i)
            key = m.strftime("%Y-%m")
            month_keys.append(key)
            months.append({
                "key": key,
                "label": m.strftime("%b %y"),
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

    @http.route(["/marketplace/productos-comprados"], type="http", auth="user", website=True)
    def marketplace_purchase_history(self, **kw):
        return self._render_tx_list(
            mode="purchase",
            title="Historial de compras",
            subtitle="Consulta todos los productos que has comprado a lo largo del tiempo.",
            base_url="/marketplace/compras",
            **kw,
        )

    @http.route("/marketplace/compras/<tx_ref>/trazabilidad/pdf", type="http", auth="user", website=True)
    def marketplace_purchase_traceability_pdf(self, tx_ref, **kw):
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)

        if not tx:
            raise NotFound()

        partner = self._get_current_partner()
        is_internal = request.env.user.has_group("base.group_user")

        if not is_internal and tx.buyer_partner_id.id != partner.id:
            raise Forbidden()

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
        ]

        return request.make_response(pdf_content, headers=pdfhttpheaders)

    @http.route("/marketplace/ventas", type="http", auth="user", website=True)
    def marketplace_sales_history(self, **kw):
        return self._render_tx_list(
            mode="sales",
            title="Ventas realizadas",
            subtitle="Consulta los productos que otros usuarios te han comprado.",
            base_url="/marketplace/ventas",
            **kw,
        )

    @http.route("/marketplace/planificacion", type="http", auth="user", website=True)
    def marketplace_planning(self, **kw):
        return self._render_tx_list(
            mode="planning",
            title="Planificación",
            subtitle="Consulta productos comprados que ya tienen entrega planificada.",
            base_url="/marketplace/planificacion",
            **kw,
        )