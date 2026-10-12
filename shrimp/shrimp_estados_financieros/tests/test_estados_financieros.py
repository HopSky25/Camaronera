"""Estados financieros sobre una compañía de prueba con el plan ``ec``.

Escenario (fechas fijas para que el resultado no dependa del día):

* 2024-06-30  Ingreso de un ejercicio anterior SIN asiento de cierre:
              Bancos 500 / Ingresos 500.
* 2025-01-02  Aporte de capital: Bancos 10.000 / Capital 10.000.
* 2025-03-01  Factura de cliente 1.000 + IVA, vence el 2025-05-16.
* 2025-04-10  Factura de proveedor 400 + IVA, vence el 2025-07-10.
* 2025-05-31  Gasto pagado por banco: Gasto 100 / Bancos 100.
* 2025-06-01  Asiento en BORRADOR: Gasto 999 / Bancos 999 (no debe contar).
* 2025-07-15  Cobro parcial de 150 de la factura de cliente.

Resultado 2025 = 1.000 − 400 − 100 = 500; resultado anterior no cerrado = 500.
"""
import base64
from datetime import date

from odoo import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestEstadosFinancieros(AccountTestInvoicingCommon):

    @classmethod
    @AccountTestInvoicingCommon.setup_country("ec")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        # Los documentos LATAM (número de comprobante del SRI) no son lo que
        # se prueba aquí.
        (cls.company_data["default_journal_sale"] + cls.company_data["default_journal_purchase"]).write(
            {"l10n_latam_use_documents": False})
        Account = cls.env["account.account"].with_company(cls.company)
        dom = Account._check_company_domain(cls.company)
        cls.banco_diario = cls.company_data["default_journal_bank"]
        cls.cta_banco = cls.banco_diario.default_account_id
        cls.cta_capital = Account.search(dom + [("code", "=like", "3101%")], limit=1)
        cls.cta_ingreso = cls.company_data["default_account_revenue"]
        # El primer "expense" del plan ec por código es 110310 (variación de
        # inventarios, clase 1): se busca uno de la clase 5.
        cls.cta_gasto = Account.search(dom + [("account_type", "=", "expense"), ("code", "=like", "5%")], limit=1)
        cls.misc = cls.company_data["default_journal_misc"]

        cls.cliente = cls.env["res.partner"].create({"name": "Cliente Antigüedad", "vat": "0992345678001"})
        cls.proveedor = cls.env["res.partner"].create({"name": "Proveedor Larvas"})

        cls._asiento(date(2024, 6, 30), cls.cta_banco, cls.cta_ingreso, 500.0)
        cls._asiento(date(2025, 1, 2), cls.cta_banco, cls.cta_capital, 10000.0)
        cls.factura = cls._factura("out_invoice", cls.cliente, date(2025, 3, 1), date(2025, 5, 16),
                                   1000.0, cls.cta_ingreso, cls.company_data["default_tax_sale"])
        cls.compra = cls._factura("in_invoice", cls.proveedor, date(2025, 4, 10), date(2025, 7, 10),
                                  400.0, cls.cta_gasto, cls.company_data["default_tax_purchase"])
        cls._asiento(date(2025, 5, 31), cls.cta_gasto, cls.cta_banco, 100.0)
        cls._asiento(date(2025, 6, 1), cls.cta_gasto, cls.cta_banco, 999.0, publicar=False)
        cls.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=cls.factura.ids).create({
                "payment_date": date(2025, 7, 15), "amount": 150.0,
                "journal_id": cls.banco_diario.id})._create_payments()

    @classmethod
    def _asiento(cls, fecha, cuenta_debe, cuenta_haber, importe, publicar=True):
        asiento = cls.env["account.move"].create({
            "move_type": "entry", "date": fecha, "journal_id": cls.misc.id, "ref": "Prueba EF",
            "line_ids": [
                Command.create({"account_id": cuenta_debe.id, "debit": importe, "name": "Debe"}),
                Command.create({"account_id": cuenta_haber.id, "credit": importe, "name": "Haber"}),
            ]})
        if publicar:
            asiento.action_post()
        return asiento

    @classmethod
    def _factura(cls, tipo, partner, fecha, vence, precio, cuenta, impuesto):
        factura = cls.env["account.move"].create({
            "move_type": tipo, "partner_id": partner.id, "invoice_date": fecha, "date": fecha,
            "invoice_date_due": vence, "invoice_payment_term_id": False,
            "invoice_line_ids": [Command.create({
                "name": "Servicio", "quantity": 1, "price_unit": precio, "account_id": cuenta.id,
                "tax_ids": [Command.set(impuesto.ids)]})]})
        factura.action_post()
        return factura

    def _wizard(self, informe, **valores):
        return self.env["shrimp.estados.financieros.wizard"].with_company(self.company).create({
            "informe": informe, "date_from": date(2025, 1, 1), "date_to": date(2025, 12, 31),
            **valores})

    def _fila(self, datos, codigo):
        return next(f for f in datos["filas"] if f["celdas"] and f["celdas"][0] == codigo)

    # ------------------------------------------------------------------
    def test_escenario(self):
        """Las cuentas elegidas son de la clase que el escenario supone."""
        self.assertEqual(self.cta_banco.code[:1], "1")
        self.assertEqual(self.cta_capital.code[:4], "3101")
        self.assertEqual(self.cta_ingreso.code[:1], "4")
        self.assertEqual(self.cta_gasto.code[:1], "5")
        self.assertEqual(self.factura.payment_state, "partial")

    def test_balance_comprobacion_cuadra(self):
        datos = self._wizard("comprobacion")._datos_informe()
        res = datos["resumen"]
        self.assertAlmostEqual(res["debitos"], res["creditos"], places=2)
        self.assertGreater(res["debitos"], 0)
        self.assertAlmostEqual(res["saldo_inicial"], 0.0, places=2)
        self.assertAlmostEqual(res["saldo_final"], 0.0, places=2)
        self.assertAlmostEqual(res["diferencia"], 0.0, places=2)
        # Bancos: 500 de 2024 como saldo inicial; 10.000 − 100 en el período.
        banco = self._fila(datos, self.cta_banco.code)
        self.assertEqual(banco["celdas"][2:], [500.0, 10000.0, 100.0, 10400.0])
        # El borrador no cuenta; si se incluye, sí.
        borradores = self._wizard("comprobacion", solo_publicados=False)._datos_informe()
        self.assertAlmostEqual(borradores["resumen"]["debitos"] - res["debitos"], 999.0, places=2)
        # Agregado a 1 dígito: una fila por clase, mismos totales.
        por_clase = self._wizard("comprobacion", nivel="1")._datos_informe()
        codigos = [f["celdas"][0] for f in por_clase["filas"] if f["estilo"] == "normal"]
        self.assertEqual(codigos, sorted(codigos))
        self.assertTrue(all(len(c) == 1 for c in codigos))
        self.assertAlmostEqual(por_clase["resumen"]["debitos"], res["debitos"], places=2)

    def test_situacion_financiera_cuadra(self):
        datos = self._wizard("situacion", comparar=True)._datos_informe()
        res = datos["resumen"]
        for i in range(2):
            self.assertAlmostEqual(res["activo"][i], res["pasivo"][i] + res["patrimonio"][i], places=2)
            self.assertAlmostEqual(res["diferencia"][i], 0.0, places=2)
        # 2025: resultado del ejercicio 500 y 500 de 2024 sin cerrar.
        self.assertEqual(res["resultado_ejercicio"], [500.0, 500.0])
        self.assertEqual(res["resultados_anteriores"], [500.0, 0.0])
        self.assertAlmostEqual(res["patrimonio"][0], 10000.0 + 500.0 + 500.0, places=2)
        # Al 31/12/2024 solo existía el ingreso de 500 en bancos.
        self.assertEqual([res["activo"][1], res["pasivo"][1], res["patrimonio"][1]], [500.0, 0.0, 500.0])
        self.assertTrue(any(f["celdas"][1] == "Resultado del ejercicio" for f in datos["filas"]))
        self.assertFalse(any(f["estilo"] == "aviso" for f in datos["filas"]))

    def test_situacion_niveles(self):
        """Los nodos de primer nivel bajo cada clase suman el total de la clase."""
        wizard = self._wizard("situacion")
        cortes = wizard._estructura_plan(self.company)["cortes"]
        self.assertEqual(cortes[:3], [1, 2, 4])  # plan ec: 1 / 11 / 1101 / 110101 ...
        wizard.nivel = "2"
        datos = wizard._datos_informe()
        activo = [f for f in datos["filas"] if f["estilo"] in ("grupo", "normal")
                  and f["celdas"][0] and f["celdas"][0][:1] == "1"]
        self.assertTrue(activo)
        self.assertTrue(all(len(f["celdas"][0]) <= 2 for f in activo))
        self.assertAlmostEqual(sum(f["celdas"][2] for f in activo), datos["resumen"]["activo"][0], places=2)
        # A nivel 1 solo quedan encabezados y totales.
        wizard.nivel = "1"
        datos = wizard._datos_informe()
        self.assertFalse([f for f in datos["filas"] if f["estilo"] == "normal"])

    def test_estado_resultados(self):
        datos = self._wizard("resultados", comparar=True)._datos_informe()
        res = datos["resumen"]
        self.assertEqual(res["ingresos"], [1000.0, 500.0])
        self.assertEqual(res["costos_gastos"], [500.0, 0.0])
        self.assertEqual(res["utilidad"], [500.0, 500.0])
        con_borrador = self._wizard("resultados", solo_publicados=False)._datos_informe()
        self.assertEqual(con_borrador["resumen"]["utilidad"], [500.0 - 999.0])

    def test_cartera_antiguedad(self):
        datos = self._wizard("cartera", date_to=date(2025, 6, 30))._datos_informe()
        cobrar = datos["resumen"]["cobrar"][self.cliente.id]
        # Vencida el 16/05: 45 días al 30/06 -> tramo 31-60.
        self.assertEqual(cobrar["d31_60"], self.factura.amount_total)
        self.assertEqual(cobrar["total"], self.factura.amount_total)
        pagar = datos["resumen"]["pagar"][self.proveedor.id]
        self.assertEqual(pagar["no_vencido"], self.compra.amount_total)
        # El cobro del 15/07 no existía al 30/06; al 31/07 sí (76 días -> 61-90).
        datos = self._wizard("cartera", date_to=date(2025, 7, 31))._datos_informe()
        cobrar = datos["resumen"]["cobrar"][self.cliente.id]
        self.assertAlmostEqual(cobrar["d61_90"], self.factura.amount_total - 150.0, places=2)
        self.assertEqual(self.factura.amount_residual, self.factura.amount_total - 150.0)
        self.assertEqual(datos["resumen"]["pagar"][self.proveedor.id]["d1_30"], self.compra.amount_total)

    def test_libro_mayor_y_diario(self):
        mayor = self._wizard("mayor", account_ids=[Command.set(self.cta_banco.ids)])._datos_informe()
        filas = mayor["filas"]
        inicial = next(f for f in filas if f["celdas"][4:5] == ["Saldo inicial"])
        self.assertEqual(inicial["celdas"][7], 500.0)
        total = next(f for f in filas if f["estilo"] == "total")
        self.assertEqual(total["celdas"][5:], [10000.0, 100.0, 10400.0])
        self.assertEqual(mayor["resumen"]["cuentas"], 1)
        por_contacto = self._wizard("mayor", partner_id=self.cliente.id)._datos_informe()
        self.assertTrue(any(f["celdas"][3] == self.cliente.name for f in por_contacto["filas"]
                            if not f["span"]))
        diario = self._wizard("diario")._datos_informe()
        # 2025 publicados: capital, factura, compra, gasto y cobro.
        self.assertEqual(diario["resumen"]["asientos"], 5)
        self.assertAlmostEqual(diario["resumen"]["debitos"], diario["resumen"]["creditos"], places=2)

    def test_lector_contable(self):
        """Un usuario solo con lectura contable puede generar los informes."""
        lector = new_test_user(
            self.env, login="lector_ef", groups="base.group_user,account.group_account_readonly",
            company_id=self.company.id, company_ids=[Command.set(self.company.ids)])
        wizard = self._wizard("comprobacion").with_user(lector)
        self.assertAlmostEqual(wizard._datos_informe()["resumen"]["diferencia"], 0.0, places=2)

    def test_pdf_y_excel(self):
        Report = self.env["ir.actions.report"]
        for informe, _nombre in self.env["shrimp.estados.financieros.wizard"]._fields["informe"].selection:
            wizard = self._wizard(informe, comparar=True)
            accion = wizard.action_ver_pdf()
            self.assertEqual(accion["type"], "ir.actions.report")
            contenido, _tipo = Report._render_qweb_pdf(accion["report_name"], wizard.ids)
            self.assertTrue(contenido)
            self.assertIn(self.company.name.encode(), contenido)
            self.assertIn(b"de <span class=\"topage\"", contenido.replace(b"\n", b""))
            accion = wizard.action_descargar_excel()
            self.assertEqual(accion["type"], "ir.actions.act_url")
            self.assertTrue(wizard.excel_filename.endswith(".xlsx"))
            xlsx = base64.b64decode(wizard.excel_file)
            self.assertTrue(xlsx.startswith(b"PK"), informe)
            self.assertGreater(len(xlsx), 2000)
