from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import ApiCase, FORBIDDEN_KEYS, int_ids, walk_keys


@tagged("post_install", "-at_install", "shrimp_api")
class TestApiIsolation(ApiCase):

    def test_facility_isolation_and_archive(self):
        resp = self.api("POST", "/facilities", raw=self.raw_a,
                        body={"name": "Granja A1", "facility_type": "farm", "province": "Guayas"})
        self.assertEqual(resp.status_code, 201, resp.text)
        fid = resp.json()["id"]
        self.assertEqual(self.api("GET", "/facilities/%s" % fid, raw=self.raw_a).status_code, 200)
        # B no la ve, ni la puede tocar ni archivar: 404, no 403.
        self.assertProblem(self.api("GET", "/facilities/%s" % fid, raw=self.raw_b), 404)
        self.assertProblem(self.api("PATCH", "/facilities/%s" % fid, raw=self.raw_b,
                                    body={"name": "robada"}), 404)
        self.assertProblem(self.api("DELETE", "/facilities/%s" % fid, raw=self.raw_b), 404)
        listing = self.api("GET", "/facilities", raw=self.raw_b).json()
        self.assertNotIn(fid, [f["id"] for f in listing["data"]])
        # A la archiva: no se borra.
        resp = self.api("DELETE", "/facilities/%s" % fid, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["active"])
        fac = self.env["shrimp.partner.facility"].with_context(active_test=False).search(
            [("uuid_ref", "=", fid)])
        self.assertTrue(fac)
        self.assertEqual(fac.partner_id, self.farm_a)

    def test_pond_cannot_use_foreign_facility(self):
        fac_b = self.env["shrimp.partner.facility"].create({"partner_id": self.farm_b.id, "name": "FB"})
        body = self.assertProblem(self.api("POST", "/ponds", raw=self.raw_a,
                                           body={"name": "P", "facility": fac_b.uuid_ref}), 422)
        self.assertEqual(body["errors"][0]["field"], "facility")

    def test_product_isolation(self):
        ref = self.product.uuid_ref
        self.assertEqual(self.api("GET", "/products/%s" % ref, raw=self.raw_lab).status_code, 200)
        self.assertProblem(self.api("GET", "/products/%s" % ref, raw=self.raw_a), 404)
        self.assertProblem(self.api("PATCH", "/products/%s" % ref, raw=self.raw_a, body={"price": 0.1}), 404)
        self.assertProblem(self.api("POST", "/products/%s:archive" % ref, raw=self.raw_a), 404)
        self.assertEqual(self.product.price, 2.5)

    def test_product_create_forces_owner_and_role(self):
        resp = self.api("POST", "/products", raw=self.raw_lab, body={
            "name": "Nauplio API", "price": 1.2, "initial_qty": 500,
            "species": self.species.uuid_ref, "stage": self.stage_pl12.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        data = resp.json()
        self.assertEqual(data["seller"]["id"], self.lab.uuid_ref)
        self.assertEqual(data["seller_type"], "laboratorio")
        self.assertEqual(data["state"], "draft")
        self.assertEqual(data["uom"]["id"], self.uom_millar.uuid_ref)
        # No se puede elegir otro vendedor.
        self.assertProblem(self.api("POST", "/products", raw=self.raw_lab, body={
            "name": "x", "price": 1, "initial_qty": 1, "seller_partner_id": self.farm_a.id}), 422)
        # Una camaronera queda como camaronera (la API vieja la marcaba laboratorio).
        resp = self.api("POST", "/products", raw=self.raw_a, body={
            "name": "Juvenil", "price": 3, "initial_qty": 10, "stage": self.stage_pl12.uuid_ref})
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["seller_type"], "camaronera")
        # La empacadora no publica productos.
        self.assertProblem(self.api("POST", "/products", raw=self.raw_packer, body={
            "name": "x", "price": 1, "initial_qty": 1}), 403)

    def test_product_publish_and_locked_fields(self):
        resp = self.api("POST", "/products", raw=self.raw_lab, body={
            "name": "Para publicar", "price": 1, "initial_qty": 100, "stage": self.stage_pl12.uuid_ref})
        pid = resp.json()["id"]
        resp = self.api("POST", "/products/%s:publish" % pid, raw=self.raw_lab)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "published")
        # Alias con barra.
        self.assertProblem(self.api("POST", "/products/%s/publish" % pid, raw=self.raw_lab), 409)
        # Con compras, el precio queda bloqueado: 422 explícito.
        self.product.execute_purchase_flow(self.farm_a, 10)
        body = self.assertProblem(self.api("PATCH", "/products/%s" % self.product.uuid_ref,
                                           raw=self.raw_lab, body={"price": 9.9}), 422, "business-rule")
        self.assertIn("compras", body["detail"])

    def test_transactions_visibility(self):
        tx = self.product.execute_purchase_flow(self.farm_a, 5)["transaction"]
        ref = tx.uuid_ref
        resp = self.api("GET", "/transactions/%s" % ref, raw=self.raw_a)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["my_role"], "buyer")
        self.assertEqual(self.api("GET", "/transactions/%s" % ref, raw=self.raw_lab).json()["my_role"], "seller")
        self.assertProblem(self.api("GET", "/transactions/%s" % ref, raw=self.raw_b), 404)
        self.assertProblem(self.api("GET", "/transactions/%s/traceability" % ref, raw=self.raw_b), 404)
        buys = self.api("GET", "/transactions?role=buyer", raw=self.raw_a).json()["data"]
        self.assertIn(ref, [t["id"] for t in buys])
        sales = self.api("GET", "/transactions?role=seller", raw=self.raw_a).json()["data"]
        self.assertNotIn(ref, [t["id"] for t in sales])
        trace = self.api("GET", "/transactions/%s/traceability" % ref, raw=self.raw_a).json()
        self.assertIn("/t/", trace["public_url"])
        self.assertFalse(int_ids(trace))

    def test_lots_and_allocation(self):
        self.product.execute_purchase_flow(self.farm_a, 20)["transaction"].write(
            {"desired_date": fields.Date.today()})
        tx = self.env["shrimp.transaction"].search([("buyer_partner_id", "=", self.farm_a.id)], limit=1)
        tx.action_receive()
        lots = self.api("GET", "/lots", raw=self.raw_a).json()["data"]
        self.assertTrue(lots)
        lot = lots[0]
        self.assertEqual(lot["owner"]["id"], self.farm_a.uuid_ref)
        # B no ve el lote de A.
        self.assertProblem(self.api("GET", "/lots/%s" % lot["id"], raw=self.raw_b), 404)
        moves = self.api("GET", "/lots/%s/moves" % lot["id"], raw=self.raw_a).json()
        self.assertTrue(moves["provenance"])
        pond = self.env["shrimp.partner.pond"].create({"partner_id": self.farm_a.id, "name": "PA"})
        resp = self.api("POST", "/lots/%s/allocations" % lot["id"], raw=self.raw_a,
                        body={"pond": pond.uuid_ref, "allocated_qty": 5})
        self.assertEqual(resp.status_code, 201, resp.text)
        # Una piscina ajena no se acepta.
        pond_b = self.env["shrimp.partner.pond"].create({"partner_id": self.farm_b.id, "name": "PB"})
        self.assertProblem(self.api("POST", "/lots/%s/allocations" % lot["id"], raw=self.raw_a,
                                    body={"pond": pond_b.uuid_ref, "allocated_qty": 1}), 422)

    def test_pagination_cursor_and_updated_since(self):
        Pond = self.env["shrimp.partner.pond"]
        created = Pond.create([{"partner_id": self.farm_b.id, "name": "Pag %s" % i} for i in range(5)])
        seen, cursor, pages = [], None, 0
        while True:
            path = "/ponds?limit=2" + ("&cursor=%s" % cursor if cursor else "")
            body = self.api("GET", path, raw=self.raw_b).json()
            pages += 1
            seen += [p["id"] for p in body["data"]]
            self.assertLessEqual(body["meta"]["count"], 2)
            cursor = body["meta"]["next_cursor"]
            if not cursor:
                break
            self.assertLess(pages, 10)
        self.assertEqual(sorted(seen), sorted(created.mapped("uuid_ref")))
        self.assertEqual(len(seen), len(set(seen)))
        future = (fields.Datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = self.api("GET", "/ponds?updated_since=%s" % future, raw=self.raw_b).json()
        self.assertEqual(body["data"], [])
        past = (fields.Datetime.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = self.api("GET", "/ponds?limit=200&updated_since=%s" % past, raw=self.raw_b).json()
        self.assertEqual(len(body["data"]), 5)
        self.assertProblem(self.api("GET", "/ponds?cursor=basura", raw=self.raw_b), 400, "invalid-cursor")
        self.assertProblem(self.api("GET", "/ponds?limit=500", raw=self.raw_b), 400)

    def test_no_numeric_ids_and_no_forbidden_fields(self):
        self.product.execute_purchase_flow(self.farm_a, 3)
        for path in ("/me", "/transactions", "/products", "/charges", "/lots"):
            for raw in (self.raw_a, self.raw_lab):
                body = self.api("GET", path, raw=raw).json()
                self.assertFalse(int_ids(body), "%s: %s" % (path, int_ids(body)))
                self.assertFalse(FORBIDDEN_KEYS & walk_keys(body), path)

    def test_price_list_confidentiality(self):
        grade = self.env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1)
        aguaje = self.env["shrimp.aguaje"].seleccionables()[:1]
        resp = self.api("POST", "/price-lists", raw=self.raw_packer, body={
            "name": "Semana API", "recipients": [self.farm_a.uuid_ref], "aguaje": aguaje.uuid_ref,
            "lines": [{"size_grade": grade.uuid_ref, "price": 2.75}]})
        self.assertEqual(resp.status_code, 201, resp.text)
        lid = resp.json()["id"]
        self.assertEqual(resp.json()["lines"][0]["uom"], "kg")
        # Borrador: ni la destinataria la ve todavía.
        self.assertProblem(self.api("GET", "/price-lists/%s" % lid, raw=self.raw_a), 404)
        self.assertEqual(self.api("POST", "/price-lists/%s:publish" % lid, raw=self.raw_packer).status_code, 200)
        seen = self.api("GET", "/price-lists/%s" % lid, raw=self.raw_a)
        self.assertEqual(seen.status_code, 200, seen.text)
        self.assertNotIn("recipients", seen.json())   # no sabe a quién más se la mandaron
        self.assertProblem(self.api("GET", "/price-lists/%s" % lid, raw=self.raw_b), 404)
        # La destinataria no puede modificarla ni publicarla.
        self.assertProblem(self.api("PUT", "/price-lists/%s" % lid, raw=self.raw_a,
                                    body={"name": "hack"}), 403)
        # Una camaronera no emite listas.
        self.assertProblem(self.api("POST", "/price-lists", raw=self.raw_a, body={"name": "x"}), 403)

    def test_me_patch_preferences(self):
        resp = self.api("PATCH", "/me", raw=self.raw_packer,
                        body={"preferences": {"accepts_harvest_reservations": True}, "city": "Durán"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(self.packer.reserva_acepta)
        self.assertEqual(self.packer.city, "Durán")
        self.assertProblem(self.api("PATCH", "/me", raw=self.raw_a,
                                    body={"preferences": {"accepts_harvest_reservations": True}}), 422)
        self.assertProblem(self.api("PATCH", "/me", raw=self.raw_a, body={"vat": "123"}), 422)

    def test_check_request_only_seller_resolves(self):
        cr = self.env["shrimp.check.request"].create({
            "product_id": self.product.id, "seller_partner_id": self.lab.id,
            "buyer_partner_id": self.farm_a.id, "qty": 5})
        # El comprador no puede aprobarse su propia solicitud.
        self.assertProblem(self.api("POST", "/check-requests/%s:approve" % cr.uuid_ref, raw=self.raw_a), 403)
        self.assertProblem(self.api("POST", "/check-requests/%s:approve" % cr.uuid_ref, raw=self.raw_b), 404)
        resp = self.api("POST", "/check-requests/%s:approve" % cr.uuid_ref, raw=self.raw_lab)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["state"], "approved")
        self.assertTrue(resp.json()["transaction"])
