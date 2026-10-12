from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.shrimp_contabilidad.models import catalogo as C


@tagged("post_install", "-at_install")
class TestShrimpContabilidad(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ec = cls.env.ref("base.ec")
        usd = cls.env.ref("base.USD")
        usd.active = True
        # Compañía ecuatoriana propia del test: la BD de pruebas arranca sin
        # plan «ec» en la compañía principal.
        cls.company = cls.env["res.company"].create({
            "name": "CamaronMarket Pruebas", "country_id": ec.id, "currency_id": usd.id,
        })
        cls.env["account.chart.template"].try_loading("ec", company=cls.company,
                                                      install_demo=False)
        cls.Setup = cls.env["shrimp.contabilidad.setup"]
        cls.Setup._setup_company(cls.company)

        cls.contador = new_test_user(
            cls.env, login="contador_cm",
            groups="base.group_user,shrimp_contabilidad.group_contador_general",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])
        cls.asistente = new_test_user(
            cls.env, login="asistente_cm",
            groups="base.group_user,shrimp_contabilidad.group_asistente_contable",
            company_id=cls.company.id, company_ids=[Command.set(cls.company.ids)])

        cls.cliente = cls.env["res.partner"].create({
            "name": "Camaronera El Estero S.A.",
            "country_id": ec.id,
            "l10n_latam_identification_type_id": cls.env.ref("l10n_ec.ec_ruc").id,
            "vat": "0992339411001",
            "street": "Km 10 vía a la costa",
        })

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _account(self, code):
        return self.env["account.account"].with_company(self.company).search([
            ("company_ids", "parent_of", self.company.id), ("code", "=", code)])

    def _snapshot(self):
        company = self.company
        env = self.env
        Account = env["account.account"].with_company(company).with_context(active_test=False)
        plan = env["account.analytic.plan"].search([("name", "=", C.PLAN_ANALITICO)])
        codigos_producto = [p[2] for p in C.PRODUCTOS_INGRESO_PROPIOS.values()] + \
            [p[2] for p in C.PRODUCTOS_GASTO.values()]
        return {
            "cuentas_total": Account.search_count([("company_ids", "parent_of", company.id)]),
            "cuentas_nuevas": {s["code"]: Account.search_count([
                ("company_ids", "parent_of", company.id), ("code", "=", s["code"])])
                for s in C.CUENTAS.values()},
            "grupos": env["account.group"].search_count([("company_id", "=", company.id)]),
            "productos_servicio": env["product.product"].with_context(
                active_test=False).search_count([("type", "=", "service")]),
            "productos_propios": {c: env["product.product"].search_count(
                [("default_code", "=", c)]) for c in codigos_producto},
            "diarios": env["account.journal"].with_context(active_test=False).search_count(
                [("company_id", "=", company.id)]),
            "planes": len(plan),
            "cuentas_analiticas": env["account.analytic.account"].with_context(
                active_test=False).search_count([("plan_id", "in", plan.ids)]),
            "distribuciones": env["account.analytic.distribution.model"].search_count(
                [("company_id", "=", company.id)]),
            "terminos": env["account.payment.term"].with_context(
                active_test=False).search_count([]),
            "xmlids": env["ir.model.data"].search_count(
                [("module", "=", "shrimp_contabilidad")]),
        }

    # ------------------------------------------------------------------
    # Hook idempotente
    # ------------------------------------------------------------------
    def test_hook_idempotente(self):
        antes = self._snapshot()
        # Cada cuenta del catálogo existe exactamente una vez.
        for codigo, n in antes["cuentas_nuevas"].items():
            self.assertEqual(n, 1, "La cuenta %s debe existir una vez" % codigo)
        self.assertEqual(antes["planes"], 1)
        self.assertEqual(antes["cuentas_analiticas"], len(C.LINEAS))
        for codigo, n in antes["productos_propios"].items():
            self.assertEqual(n, 1, "El producto %s debe existir una vez" % codigo)

        self.Setup._setup_company(self.company)
        self.Setup._setup_all()
        self.Setup._setup_company(self.company)
        self.assertEqual(self._snapshot(), antes, "El setup no debe duplicar nada")

    def test_diarios_y_terminos(self):
        Journal = self.env["account.journal"]
        for spec in C.DIARIOS.values():
            diarios = Journal.search([("company_id", "=", self.company.id),
                                      ("type", "=", spec["type"]),
                                      ("name", "ilike", spec["name"])])
            self.assertEqual(len(diarios), 1, spec["name"])
        servicio = Journal.search([("company_id", "=", self.company.id),
                                   ("name", "ilike", "Facturas de servicio")])
        self.assertTrue(servicio.l10n_latam_use_documents)
        if "shrimp_service_journal_id" in self.company._fields:
            self.assertEqual(self.company.shrimp_service_journal_id, servicio)
        for xmlid in C.TERMINOS_PAGO.values():
            self.assertTrue(self.env.ref(xmlid).active)

    def test_productos_y_cuentas(self):
        comision = self.env.ref("shrimp_marketplace.product_marketplace_commission")
        tmpl = comision.product_tmpl_id.with_company(self.company)
        self.assertEqual(tmpl.property_account_income_id, self._account("41020101"))
        iva = self.env["account.chart.template"].with_company(self.company).ref(
            C.IVA_VENTA_SERVICIOS)
        self.assertIn(iva, tmpl.taxes_id)
        goods = self.env["account.chart.template"].with_company(self.company).ref(
            "tax_vat_15_411_goods")
        self.assertNotIn(goods, tmpl.taxes_id)
        # Compañía sin facturas: el IVA de venta por defecto pasa a servicios.
        self.assertEqual(self.company.account_sale_tax_id, iva)
        gasto = self.env.ref("shrimp_contabilidad.product_gasto_servidores")
        self.assertEqual(
            gasto.product_tmpl_id.with_company(self.company).property_account_expense_id,
            self._account("52022819"))
        # Reutilizadas del plan l10n_ec, no creadas.
        for clave in ("gas_publicidad", "gas_honorarios", "gas_comisiones_bancarias"):
            spec = C.CUENTAS[clave]
            plantilla = self.env["account.chart.template"].with_company(self.company).ref(
                spec["tmpl"])
            self.assertEqual(self._account(spec["code"]), plantilla)

    # ------------------------------------------------------------------
    # Contador general
    # ------------------------------------------------------------------
    def test_contador_ve_asientos_y_factura_servicio(self):
        contador = self.contador
        env = self.env(user=contador, context=dict(self.env.context,
                                                  allowed_company_ids=self.company.ids))
        # Ve asientos y apuntes.
        env["account.move"].search([])
        env["account.move.line"].search([])

        comision = self.env.ref("shrimp_marketplace.product_marketplace_commission")
        factura = env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.cliente.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [Command.create({
                "product_id": comision.id, "quantity": 1000, "price_unit": 0.02,
            })],
        })
        linea = factura.invoice_line_ids
        self.assertEqual(linea.account_id, self._account("41020101"))
        iva = self.env["account.chart.template"].with_company(self.company).ref(
            C.IVA_VENTA_SERVICIOS)
        self.assertEqual(linea.tax_ids, iva)
        self.assertAlmostEqual(factura.amount_untaxed, 20.0)
        self.assertAlmostEqual(factura.amount_tax, 3.0)
        self.assertIn("Facturas de servicio", factura.journal_id.name)
        marketplace = self.env["account.analytic.account"].search([
            ("plan_id.name", "=", C.PLAN_ANALITICO), ("code", "=", "MKT")])
        self.assertEqual(linea.analytic_distribution, {str(marketplace.id): 100.0})
        factura.action_post()
        self.assertEqual(factura.state, "posted")

    # ------------------------------------------------------------------
    # Asistente contable
    # ------------------------------------------------------------------
    def test_asistente_no_modifica_impuestos(self):
        tax = self.env["account.chart.template"].with_company(self.company).ref(
            C.IVA_VENTA_SERVICIOS)
        asistente = tax.with_user(self.asistente).with_context(
            allowed_company_ids=self.company.ids)
        asistente.read(["name", "amount"])  # puede verlos
        with self.assertRaises(AccessError):
            asistente.write({"amount": 12.0})
        with self.assertRaises(AccessError):
            self.env["account.tax"].with_user(self.asistente).with_context(
                allowed_company_ids=self.company.ids).create({
                    "name": "IVA inventado", "amount": 1.0, "company_id": self.company.id})
        # El contador sí administra impuestos.
        tax.with_user(self.contador).with_context(
            allowed_company_ids=self.company.ids).write({"description": "IVA 15%"})

    def test_cobros_solo_lectura(self):
        cobro = self.env["shrimp.charge"].create({
            "charge_type": "commission", "payer_partner_id": self.cliente.id,
            "company_id": self.company.id, "amount": 10.0, "invoice_qty": 500,
            "description": "Comisión de prueba",
        })
        for user in (self.asistente, self.contador):
            como = cobro.with_user(user).with_context(allowed_company_ids=self.company.ids)
            self.assertEqual(como.read(["name", "state", "amount"])[0]["amount"], 10.0)
            with self.assertRaises(AccessError):
                como.write({"amount": 1.0})
            with self.assertRaises(AccessError):
                self.env["shrimp.charge"].with_user(user).create({
                    "charge_type": "commission", "payer_partner_id": self.cliente.id})
        # ATS / 103 / 104: el asistente puede abrir el generador.
        self.env["ec.sri.report.wizard"].with_user(self.asistente).with_context(
            allowed_company_ids=self.company.ids).create({
                "company_id": self.company.id, "kind": "104"})

    def test_ninguno_es_administrador(self):
        prohibidos = ["base.group_system", "base.group_erp_manager",
                      "shrimp_marketplace.group_shrimp_manager"]
        for user in (self.asistente, self.contador):
            for xmlid in prohibidos:
                self.assertFalse(user.has_group(xmlid), "%s no debe tener %s" % (
                    user.login, xmlid))
        for xmlid in ("shrimp_contabilidad.group_asistente_contable",
                      "shrimp_contabilidad.group_contador_general"):
            implicados = self.env.ref(xmlid).all_implied_ids
            for prohibido in prohibidos:
                self.assertNotIn(self.env.ref(prohibido), implicados)
        self.assertTrue(self.contador.has_group("account.group_account_manager"))
        self.assertTrue(self.asistente.has_group("account.group_account_user"))
        self.assertFalse(self.asistente.has_group("account.group_account_manager"))

    # ------------------------------------------------------------------
    # Cobros -> producto
    # ------------------------------------------------------------------
    def test_comision_larva_usa_su_producto(self):
        Stage = self.env["shrimp.stage"]
        nauplio = Stage.search([("code", "=", "NAUPLIO")], limit=1)
        engorde = Stage.search([("code", "=", "ENGORDE")], limit=1)
        larva_prod = self.env["shrimp.product"].new({"stage_id": nauplio.id})
        cobro = self.env["shrimp.charge"].new({"charge_type": "commission",
                                               "product_id": larva_prod})
        producto = cobro._get_service_product("commission", self.company)
        self.assertEqual(producto, self.env.ref("shrimp_contabilidad.product_comision_larva"))
        self.assertEqual(
            producto.product_tmpl_id.with_company(self.company).property_account_income_id,
            self._account("41020105"))
        if engorde:
            adulto = self.env["shrimp.product"].new({"stage_id": engorde.id})
            cobro = self.env["shrimp.charge"].new({"charge_type": "commission",
                                                   "product_id": adulto})
            self.assertEqual(cobro._get_service_product("commission", self.company),
                             self.env.ref("shrimp_marketplace.product_marketplace_commission"))

    def test_cuenta_por_pagar_verificadora(self):
        cuenta = self._account("21030101")
        self.assertEqual(cuenta.account_type, "liability_payable")
        verificadora = self.env["res.partner"].create({"name": "Verificadora del Golfo"})
        self.assertTrue(self.Setup._set_payable_if_default(verificadora, self.company, cuenta))
        self.assertEqual(
            verificadora.with_company(self.company).property_account_payable_id, cuenta)
        # Una cuenta elegida a mano no se pisa.
        otro = self.env["res.partner"].create({"name": "Proveedor con cuenta propia"})
        propia = self.env["account.chart.template"].with_company(self.company).ref("ec210302")
        otro.with_company(self.company).property_account_payable_id = propia
        self.assertFalse(self.Setup._set_payable_if_default(otro, self.company, cuenta))

    def test_menu_informes_reutilizado(self):
        self.assertEqual(self.env.ref("account.menu_finance_reports").parent_id,
                         self.env.ref("shrimp_contabilidad.menu_contabilidad_root"))
