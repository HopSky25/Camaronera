"""Configuración contable de CamaronMarket, idempotente.

Lo llaman:
* el post_init_hook (instalación), para todas las compañías con plan «ec»;
* la carga del plan «ec» en una compañía nueva (account.chart.template);
* los cobros de la plataforma, de forma perezosa, cuando un producto de
  servicio se crea después de instalar este módulo (p. ej. al instalar
  shrimp_verification o shrimp_copacking más tarde).

Regla general: lo que ya existe —por xmlid, por código de cuenta o por
nombre— se reutiliza; nunca se duplica y nunca se pisa una decisión de la
empresa (solo se completan campos vacíos o con el valor genérico de fábrica).
"""
import logging

from odoo import Command, api, models

from . import catalogo as C

_logger = logging.getLogger(__name__)

MODULE = "shrimp_contabilidad"

# ACL de solo lectura sobre modelos de módulos opcionales (no son dependencia
# de este módulo): se crean por código si el modelo existe.
ACCESOS_OPCIONALES = [
    ("shrimp.verification", "access_shrimp_verification_contable"),
    ("shrimp.copack.order", "access_shrimp_copack_order_contable"),
]


class ShrimpContabilidadSetup(models.AbstractModel):
    _name = "shrimp.contabilidad.setup"
    _description = "Configuración contable de CamaronMarket (idempotente)"

    # ------------------------------------------------------------------
    # Entradas
    # ------------------------------------------------------------------
    @api.model
    def _companias_ec(self):
        """Compañías con el plan contable de Ecuador (estructura Supercias)."""
        return self.env["res.company"].sudo().search([]).filtered(
            lambda c: c.chart_template == "ec")

    @api.model
    def _setup_all(self):
        """Puntos 2 a 5 para todas las compañías con plan «ec». Devuelve el
        resumen por compañía y lo escribe en el log."""
        self._ensure_optional_access()
        companias = self._companias_ec()
        resumen = {}
        if not companias:
            _logger.warning(
                "shrimp_contabilidad: ninguna compañía tiene el plan contable «ec» "
                "(l10n_ec). No se creó nada; se configurará solo al cargar el plan "
                "«ec» en una compañía.")
            return resumen
        for company in companias:
            resumen[company.id] = self._setup_company(company)
        return resumen

    @api.model
    def _setup_company(self, company):
        """Cuentas, productos, analítica, diarios y términos de pago de una
        compañía. Idempotente."""
        company = company.sudo()
        R = {"company": company.name, "cuentas": [], "grupos": [], "productos": [],
             "analitica": [], "diarios": [], "terminos": [], "socios": [],
             "impuesto_defecto": "-"}
        cuentas = self._ensure_accounts(company, R)
        self._ensure_account_groups(company, R)
        impuestos = self._taxes(company)
        self._ensure_default_sale_tax(company, impuestos, R)
        plan, analiticas = self._ensure_analytic(R)
        productos = self._ensure_products(company, cuentas, impuestos, R)
        self._ensure_distribution_models(company, productos, analiticas, R)
        self._ensure_journals(company, R)
        self._ensure_payment_terms(R)
        self._ensure_verifier_payables(company, cuentas, R)
        self._log_resumen(R)
        return R

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @api.model
    def _bind_xmlid(self, xmlid, record):
        module, name = xmlid.split(".", 1)
        self.env["ir.model.data"].sudo().create({
            "module": module, "name": name, "model": record._name,
            "res_id": record.id, "noupdate": True,
        })

    @api.model
    def _rename(self, record, new_name):
        """Renombra un campo traducible en inglés y en todos los idiomas
        instalados (si no, el usuario en español seguiría viendo el viejo)."""
        record = record.sudo()
        record.with_context(lang="en_US").name = new_name
        for code, _label in self.env["res.lang"].get_installed():
            record.with_context(lang=code).name = new_name

    @api.model
    def _chart_ref(self, company, xmlid):
        return self.env["account.chart.template"].with_company(company).ref(
            xmlid, raise_if_not_found=False)

    # ------------------------------------------------------------------
    # Cuentas
    # ------------------------------------------------------------------
    @api.model
    def _find_account(self, company, spec):
        Account = self.env["account.account"].sudo().with_company(company).with_context(
            active_test=False)
        dominio = Account._check_company_domain(company)
        if spec.get("tmpl"):
            cuenta = self._chart_ref(company, spec["tmpl"])
            if cuenta:
                return cuenta
        cuenta = Account.search(dominio + [("code", "=", spec["code"])], limit=1)
        if cuenta:
            return cuenta
        return Account.search(dominio + [("name", "=ilike", spec["name"]),
                                         ("account_type", "=", spec["type"])], limit=1)

    @api.model
    def _ensure_account(self, company, clave, R=None):
        spec = C.CUENTAS[clave]
        cuenta = self._find_account(company, spec)
        if cuenta:
            if R is not None:
                R["cuentas"].append(("reutilizada", cuenta.with_company(company).code,
                                     cuenta.name))
            return cuenta
        cuenta = self.env["account.account"].sudo().with_company(company).create({
            "code": spec["code"],
            "name": spec["name"],
            "account_type": spec["type"],
            "reconcile": spec.get("reconcile", False),
            "company_ids": [Command.set(company.ids)],
        })
        if R is not None:
            R["cuentas"].append(("creada", spec["code"], spec["name"]))
        return cuenta

    @api.model
    def _ensure_accounts(self, company, R=None):
        return {clave: self._ensure_account(company, clave, R) for clave in C.CUENTAS}

    @api.model
    def _ensure_account_groups(self, company, R):
        Group = self.env["account.group"].sudo()
        for prefijo, nombre in C.GRUPOS_CUENTAS:
            existe = Group.search([("company_id", "=", company.id),
                                   ("code_prefix_start", "=", prefijo)], limit=1)
            if existe:
                R["grupos"].append(("reutilizado", prefijo, existe.name))
                continue
            Group.create({"name": nombre, "code_prefix_start": prefijo,
                          "code_prefix_end": prefijo, "company_id": company.id})
            R["grupos"].append(("creado", prefijo, nombre))

    # ------------------------------------------------------------------
    # Impuestos
    # ------------------------------------------------------------------
    @api.model
    def _taxes(self, company):
        """IVA de la plantilla l10n_ec; si la empresa los borró o renombró,
        se cae al impuesto por defecto de la compañía."""
        Tax = self.env["account.tax"].sudo()

        def buscar(xmlid, uso, tarifa, defecto):
            tax = self._chart_ref(company, xmlid)
            if tax and tax.active:
                return tax
            tax = Tax.search([("company_id", "=", company.id), ("type_tax_use", "=", uso),
                              ("amount", "=", tarifa), ("amount_type", "=", "percent")],
                             order="sequence, id", limit=1)
            return tax or defecto

        return {
            C.IVA_VENTA_SERVICIOS: buscar(C.IVA_VENTA_SERVICIOS, "sale", 15.0,
                                          company.account_sale_tax_id),
            C.IVA_COMPRA_15: buscar(C.IVA_COMPRA_15, "purchase", 15.0,
                                    company.account_purchase_tax_id),
            C.IVA_COMPRA_0: buscar(C.IVA_COMPRA_0, "purchase", 0.0,
                                   company.account_purchase_tax_id),
        }

    @api.model
    def _ensure_default_sale_tax(self, company, impuestos, R):
        """IVA 15 % de SERVICIOS como impuesto de venta por defecto de la
        compañía (l10n_ec deja el de BIENES), pero solo si la compañía aún no
        tiene ninguna factura ni nota de crédito de cliente: con historial,
        el cambio lo decide el contador."""
        iva = impuestos[C.IVA_VENTA_SERVICIOS]
        if not iva or company.account_sale_tax_id == iva:
            R["impuesto_defecto"] = "ya era %s" % (iva.name if iva else "-")
            return
        tiene_facturas = self.env["account.move"].sudo().search_count([
            ("company_id", "=", company.id),
            ("move_type", "in", ("out_invoice", "out_refund")),
        ], limit=1)
        if tiene_facturas:
            R["impuesto_defecto"] = "sin cambio (la compañía ya tiene facturas): %s" % (
                company.account_sale_tax_id.name or "-")
            return
        antes = company.account_sale_tax_id.name or "-"
        company.account_sale_tax_id = iva
        R["impuesto_defecto"] = "cambiado de «%s» a «%s»" % (antes, iva.name)

    @api.model
    def _set_company_taxes(self, tmpl, field, company, tax, default_tax):
        """Pone `tax` como impuesto de la compañía en el producto solo si no
        tiene ninguno o tiene el genérico por defecto (p. ej. el IVA 15 % de
        BIENES que asigna shrimp_marketplace al crear el producto). Conserva
        los impuestos de otras compañías. Devuelve True si cambió algo."""
        if not tax:
            return False
        actuales = tmpl.sudo()[field].filtered(lambda t: t.company_id == company)
        if actuales == tax:
            return False
        if actuales and actuales != default_tax and actuales != company.account_sale_tax_id \
                and not (field == "taxes_id" and actuales == self._chart_ref(
                    company, "tax_vat_15_411_goods")):
            return False  # decisión de la empresa: no se toca
        tmpl.sudo().write({field: [Command.unlink(t.id) for t in actuales]
                           + [Command.link(tax.id)]})
        return True

    # ------------------------------------------------------------------
    # Productos
    # ------------------------------------------------------------------
    @api.model
    def _ensure_product(self, xmlid, nombre, codigo, sale_ok, purchase_ok):
        """Producto de servicio por xmlid; si no, por código interno o nombre
        (y se le ata el xmlid); si no, se crea. Devuelve (producto, creado)."""
        product = self.env.ref(xmlid, raise_if_not_found=False)
        if product:
            return product, False
        Product = self.env["product.product"].sudo()
        product = (Product.search([("default_code", "=", codigo), ("type", "=", "service")],
                                  limit=1)
                   or Product.search([("name", "=", nombre), ("type", "=", "service")],
                                     limit=1))
        creado = not product
        if not product:
            vals = {
                "name": nombre,
                "type": "service",
                "default_code": codigo,
                "list_price": 0.0,
                "sale_ok": sale_ok,
                "purchase_ok": purchase_ok,
                "taxes_id": [Command.clear()],
                "supplier_taxes_id": [Command.clear()],
            }
            if "invoice_policy" in self.env["product.template"]._fields and sale_ok:
                vals["invoice_policy"] = "order"
            product = self.env["product.template"].sudo().create(vals).product_variant_id
        self._bind_xmlid(xmlid, product)
        return product, creado

    @api.model
    def _ensure_own_income_product(self, clave):
        xmlid, nombre, codigo, _linea = C.PRODUCTOS_INGRESO_PROPIOS[clave]
        product, _creado = self._ensure_product(xmlid, nombre, codigo, True, False)
        return product

    @api.model
    def _configure_income_product(self, product, linea, company, cuentas, impuestos):
        """Cuenta de ingreso de la línea e IVA 15 % de servicios. En la
        verificación, además, cuenta de costo e IVA de compras (con ese mismo
        producto se registra la factura de la verificadora). Devuelve la
        lista de cambios hechos."""
        tmpl = product.product_tmpl_id.sudo().with_company(company)
        cambios = []
        cuenta = cuentas[C.LINEA_A_CUENTA[linea]]
        actual = tmpl.property_account_income_id
        if actual != cuenta and (not actual or actual == company.income_account_id):
            tmpl.property_account_income_id = cuenta
            cambios.append("ingreso %s" % cuenta.with_company(company).code)
        if self._set_company_taxes(tmpl, "taxes_id", company,
                                   impuestos[C.IVA_VENTA_SERVICIOS],
                                   company.account_sale_tax_id):
            cambios.append("IVA venta 15 % servicios")
        if linea == "verificacion":
            costo = cuentas["cos_verificadoras"]
            actual = tmpl.property_account_expense_id
            if actual != costo and (not actual or actual == company.expense_account_id):
                tmpl.property_account_expense_id = costo
                cambios.append("costo %s" % costo.with_company(company).code)
            if tmpl.purchase_ok and self._set_company_taxes(
                    tmpl, "supplier_taxes_id", company, impuestos[C.IVA_COMPRA_15],
                    company.account_purchase_tax_id):
                cambios.append("IVA compra 15 %")
        return cambios

    @api.model
    def _configure_expense_product(self, product, clave_cuenta, iva, company, cuentas,
                                   impuestos):
        tmpl = product.product_tmpl_id.sudo().with_company(company)
        cambios = []
        cuenta = cuentas[clave_cuenta]
        actual = tmpl.property_account_expense_id
        if actual != cuenta and (not actual or actual == company.expense_account_id):
            tmpl.property_account_expense_id = cuenta
            cambios.append("gasto %s" % cuenta.with_company(company).code)
        if self._set_company_taxes(tmpl, "supplier_taxes_id", company, impuestos[iva],
                                   company.account_purchase_tax_id):
            cambios.append("IVA compra")
        return cambios

    @api.model
    def _charge_products(self):
        """Productos de servicio de los cobros de la plataforma que existen
        en esta instalación: {tipo de cobro: producto}. Se obtienen con el
        propio shrimp.charge._get_service_product (mismo xmlid que usa la
        facturación), así que nunca se crea un producto paralelo."""
        Charge = self.env["shrimp.charge"].sudo().with_context(shrimp_cont_no_lazy=True)
        specs = Charge._service_product_specs()
        productos = {}
        for tipo in specs:
            if tipo not in C.COBRO_A_LINEA:
                continue
            xmlid = specs[tipo][0]
            if tipo == "check_fee":
                # Legado: solo se configura si ya existe; no se crea.
                product = self.env.ref(xmlid, raise_if_not_found=False)
                if product:
                    productos[tipo] = (product, False)
                continue
            existia = bool(self.env.ref(xmlid, raise_if_not_found=False))
            productos[tipo] = (Charge._get_service_product(tipo), not existia)
        return productos

    @api.model
    def _ensure_products(self, company, cuentas, impuestos, R):
        """Devuelve {producto: línea} de los productos de ingreso."""
        ingresos = {}
        for tipo, (product, creado) in self._charge_products().items():
            linea = C.COBRO_A_LINEA[tipo]
            cambios = self._configure_income_product(product, linea, company, cuentas,
                                                     impuestos)
            R["productos"].append(("creado (por shrimp.charge)" if creado else "reutilizado",
                                   product.default_code or "", product.name, cambios))
            ingresos[product] = linea
        for clave, (xmlid, nombre, codigo, linea) in C.PRODUCTOS_INGRESO_PROPIOS.items():
            product, creado = self._ensure_product(xmlid, nombre, codigo, True, False)
            cambios = self._configure_income_product(product, linea, company, cuentas,
                                                     impuestos)
            R["productos"].append(("creado" if creado else "reutilizado", codigo, nombre,
                                   cambios))
            ingresos[product] = linea
        for clave, (xmlid, nombre, codigo, clave_cuenta, iva) in C.PRODUCTOS_GASTO.items():
            product, creado = self._ensure_product(xmlid, nombre, codigo, False, True)
            cambios = self._configure_expense_product(product, clave_cuenta, iva, company,
                                                      cuentas, impuestos)
            R["productos"].append(("creado" if creado else "reutilizado", codigo, nombre,
                                   cambios))
        return ingresos

    # ------------------------------------------------------------------
    # Analítica
    # ------------------------------------------------------------------
    @api.model
    def _ensure_analytic(self, R=None):
        """Plan «Línea de negocio» (global) y sus cuentas. Devuelve
        (plan, {línea: cuenta analítica})."""
        Plan = self.env["account.analytic.plan"].sudo()
        plan = Plan.search([("name", "=", C.PLAN_ANALITICO), ("parent_id", "=", False)],
                           limit=1)
        if not plan:
            plan = Plan.create({"name": C.PLAN_ANALITICO,
                                "default_applicability": "optional",
                                "description": "Líneas de negocio de CamaronMarket para "
                                               "ver el estado de resultados por servicio."})
            if R is not None:
                R["analitica"].append(("creado", "plan", plan.name))
        elif R is not None:
            R["analitica"].append(("reutilizado", "plan", plan.name))
        Account = self.env["account.analytic.account"].sudo().with_context(active_test=False)
        cuentas = {}
        for linea, (nombre, codigo) in C.LINEAS.items():
            cuenta = (Account.search([("plan_id", "=", plan.id), ("code", "=", codigo)], limit=1)
                      or Account.search([("plan_id", "=", plan.id), ("name", "=", nombre)],
                                        limit=1))
            if not cuenta:
                cuenta = Account.create({"name": nombre, "code": codigo, "plan_id": plan.id,
                                         "company_id": False})
                if R is not None:
                    R["analitica"].append(("creada", codigo, nombre))
            elif R is not None:
                R["analitica"].append(("reutilizada", codigo, nombre))
            cuentas[linea] = cuenta
        return plan, cuentas

    @api.model
    def _ensure_distribution_model(self, company, product, cuenta_analitica):
        Model = self.env["account.analytic.distribution.model"].sudo()
        existe = Model.search([("product_id", "=", product.id),
                               ("company_id", "=", company.id)], limit=1)
        if existe:
            return existe, False
        return Model.create({
            "product_id": product.id,
            "company_id": company.id,
            "analytic_distribution": {str(cuenta_analitica.id): 100.0},
        }), True

    @api.model
    def _ensure_distribution_models(self, company, productos, analiticas, R):
        for product, linea in productos.items():
            _model, creado = self._ensure_distribution_model(company, product,
                                                             analiticas[linea])
            R["analitica"].append(("creado" if creado else "reutilizado",
                                   "distribución", "%s -> %s" % (product.name,
                                                                 analiticas[linea].name)))

    # ------------------------------------------------------------------
    # Diarios y términos de pago
    # ------------------------------------------------------------------
    @api.model
    def _free_journal_code(self, company, code):
        Journal = self.env["account.journal"].sudo().with_context(active_test=False)
        candidato, n = code, 1
        while Journal.search_count([("company_id", "=", company.id),
                                    ("code", "=", candidato)]):
            n += 1
            candidato = "%s%d" % (code[:4], n)
        return candidato

    @api.model
    def _create_journal(self, company, spec):
        vals = {"name": spec["name"], "type": spec["type"], "company_id": company.id,
                "code": self._free_journal_code(company, spec["code"])}
        if spec["type"] == "sale" and company.country_code == "EC" \
                and "l10n_ec_entity" in self.env["account.journal"]._fields:
            usados = set(self.env["account.journal"].sudo().search([
                ("company_id", "=", company.id), ("type", "=", "sale")]).mapped(
                    lambda j: (j.l10n_ec_entity, j.l10n_ec_emission)))
            # Sugerencia documentada: 001-003 (establecimiento 001, punto 003).
            # Solo se usa si la compañía no tiene NINGÚN diario de ventas; el
            # punto real lo confirma la empresa con el SRI.
            emision = 3
            while ("001", "%03d" % emision) in usados:
                emision += 1
            vals.update({"l10n_latam_use_documents": True, "l10n_ec_entity": "001",
                         "l10n_ec_emission": "%03d" % emision,
                         "l10n_ec_emission_address_id": company.partner_id.id})
        return self.env["account.journal"].sudo().with_context(lang="en_US").create(vals)

    @api.model
    def _ensure_journal(self, company, clave, R):
        spec = C.DIARIOS[clave]
        Journal = self.env["account.journal"].sudo()
        dominio = [("company_id", "=", company.id), ("type", "=", spec["type"])]
        journal = Journal.browse()
        campo_servicio = "shrimp_service_journal_id" in company._fields
        if clave == "servicio" and campo_servicio:
            journal = company.shrimp_service_journal_id
        if not journal:
            journal = Journal.search(dominio + [("name", "ilike", spec["name"])], limit=1)
        if not journal and clave == "servicio":
            # El diario de ventas de l10n_ec (001-001, documentos del SRI): la
            # plataforma solo factura servicios, así que es ESTE diario.
            journal = (Journal.search(dominio + [("l10n_latam_use_documents", "=", True)],
                                      order="sequence, id", limit=1)
                       or Journal.search(dominio, order="sequence, id", limit=1))
        if not journal and spec["renombrables"]:
            journal = Journal.search(dominio, order="sequence, id").filtered(
                lambda j: j.name in spec["renombrables"])[:1]
        accion = "reutilizado"
        if not journal:
            journal = self._create_journal(company, spec)
            accion = "creado"
        nuevo_nombre = spec["renombrables"].get(journal.name)
        if nuevo_nombre:
            antes = journal.name
            self._rename(journal, nuevo_nombre)
            accion += " (renombrado desde «%s»)" % antes
        if clave == "servicio" and campo_servicio and not company.shrimp_service_journal_id:
            company.shrimp_service_journal_id = journal
            accion += " y fijado como diario de servicio de la plataforma"
        R["diarios"].append((accion, journal.code, journal.name))
        return journal

    @api.model
    def _traducir_diarios_fabrica(self):
        """En cada actualización (-u): diarios de fábrica que siguen en inglés,
        al español, en todas las empresas. Nombres puestos a mano no se tocan."""
        for journal in self.env["account.journal"].sudo().with_context(active_test=False).search([]):
            nuevo = C.DIARIOS_FABRICA_ES.get(journal.with_context(lang="en_US").name)
            if nuevo and journal.name != nuevo:
                self._rename(journal, nuevo)

    @api.model
    def _ensure_journals(self, company, R):
        diarios = {clave: self._ensure_journal(company, clave, R) for clave in C.DIARIOS}
        # Los diarios de fábrica que quedaron en inglés, al español.
        for journal in self.env["account.journal"].sudo().search([("company_id", "=", company.id)]):
            nuevo = C.DIARIOS_FABRICA_ES.get(journal.name)
            if nuevo:
                antes = journal.name
                self._rename(journal, nuevo)
                R["diarios"].append(("renombrado desde «%s»" % antes, journal.code, journal.name))
        return diarios

    @api.model
    def _ensure_payment_terms(self, R):
        Term = self.env["account.payment.term"].sudo()
        dias = {"contado": (0, "Contado"), "15_dias": (15, "15 días"),
                "30_dias": (30, "30 días")}
        for clave, xmlid in C.TERMINOS_PAGO.items():
            term = self.env.ref(xmlid, raise_if_not_found=False)
            if term:
                if not term.active:
                    term.active = True
                R["terminos"].append(("reutilizado", clave, term.name))
                continue
            nb, nombre = dias[clave]
            term = Term.search([("name", "=", nombre)], limit=1) or Term.create({
                "name": nombre,
                "line_ids": [Command.create({"value": "percent", "value_amount": 100.0,
                                             "nb_days": nb})],
            })
            R["terminos"].append(("creado", clave, term.name))

    # ------------------------------------------------------------------
    # Verificadoras: cuenta por pagar propia
    # ------------------------------------------------------------------
    @api.model
    def _set_payable_if_default(self, partner, company, cuenta):
        """Pone la cuenta por pagar a verificadoras al socio en la compañía
        solo si tiene la cuenta por pagar por defecto (no pisa una elegida)."""
        partner = partner.sudo().with_company(company).commercial_partner_id
        actual = partner.property_account_payable_id
        if actual == cuenta:
            return False
        defectos = {self.env["ir.default"].sudo()._get(
            "res.partner", "property_account_payable_id", company_id=company.id)}
        plantilla = self._chart_ref(company, "ec210301")
        if plantilla:
            defectos.add(plantilla.id)
        if actual and actual.id not in defectos:
            return False
        partner.property_account_payable_id = cuenta
        return True

    @api.model
    def _ensure_verifier_payables(self, company, cuentas, R):
        Partner = self.env["res.partner"].sudo()
        campo = Partner._fields.get("shrimp_user_type")
        if not campo:
            return
        valores = [v for v, _l in campo._description_selection(self.env)]
        if "verificador" not in valores:
            return
        for partner in Partner.search([("shrimp_user_type", "=", "verificador")]):
            if self._set_payable_if_default(partner, company, cuentas["pas_verificadoras"]):
                R["socios"].append(partner.display_name)

    @api.model
    def _payable_verificadora(self, partner, product, company):
        """Antes de la factura de proveedor de una verificación: la deuda con
        la verificadora va a 21030101 (si la compañía tiene plan «ec»)."""
        if not partner or company.chart_template != "ec":
            return
        verificacion = self.env.ref("shrimp_verification.product_field_verification",
                                    raise_if_not_found=False)
        if not verificacion or product != verificacion:
            return
        cuenta = self._ensure_account(company, "pas_verificadoras")
        self._set_payable_if_default(partner, company, cuenta)

    # ------------------------------------------------------------------
    # Configuración perezosa (productos creados después de instalar)
    # ------------------------------------------------------------------
    @api.model
    def _configure_lazy(self, product, linea, company):
        """Si el producto de servicio no tiene la cuenta de su línea en esta
        compañía, lo configura (cuenta, IVA, distribución analítica). Nunca
        lanza: un fallo aquí no puede impedir facturar."""
        if not product or company.chart_template != "ec":
            return
        tmpl = product.product_tmpl_id.sudo().with_company(company)
        actual = tmpl.property_account_income_id
        if actual and actual != company.income_account_id:
            return
        try:
            with self.env.cr.savepoint():
                cuentas = {clave: self._ensure_account(company, clave)
                           for clave in (C.LINEA_A_CUENTA[linea], "cos_verificadoras")}
                cambios = self._configure_income_product(product, linea, company, cuentas,
                                                         self._taxes(company))
                _plan, analiticas = self._ensure_analytic()
                self._ensure_distribution_model(company, product, analiticas[linea])
            _logger.info("shrimp_contabilidad: producto %s configurado para %s (%s)",
                         product.display_name, company.name, ", ".join(cambios))
        except Exception:  # noqa: BLE001 - nunca debe tumbar la facturación
            _logger.exception("shrimp_contabilidad: no se pudo configurar el producto %s "
                              "para %s", product.display_name, company.name)

    # ------------------------------------------------------------------
    # Accesos opcionales
    # ------------------------------------------------------------------
    @api.model
    def _ensure_optional_access(self):
        """Lectura para el área contable de las verificaciones y las órdenes
        de empaque, si esos módulos están instalados (no son dependencia)."""
        grupo = self.env.ref("%s.group_asistente_contable" % MODULE, raise_if_not_found=False)
        if not grupo:
            return
        for modelo, nombre in ACCESOS_OPCIONALES:
            ir_model = self.env["ir.model"].sudo()._get(modelo)
            if not ir_model or self.env.ref("%s.%s" % (MODULE, nombre),
                                            raise_if_not_found=False):
                continue
            acl = self.env["ir.model.access"].sudo().create({
                "name": "%s contabilidad (lectura)" % modelo,
                "model_id": ir_model.id, "group_id": grupo.id,
                "perm_read": True, "perm_write": False, "perm_create": False,
                "perm_unlink": False,
            })
            self._bind_xmlid("%s.%s" % (MODULE, nombre), acl)
            _logger.info("shrimp_contabilidad: lectura de %s para el área contable", modelo)

    # ------------------------------------------------------------------
    # Resumen
    # ------------------------------------------------------------------
    @api.model
    def _log_resumen(self, R):
        def fmt(items):
            return "; ".join(" ".join(str(x) for x in item if x not in (None, "", []))
                             if not isinstance(item, str) else item for item in items) or "-"
        lineas = [
            "shrimp_contabilidad: configuración contable de «%s»" % R["company"],
            "  cuentas: %s" % fmt(R["cuentas"]),
            "  grupos de cuentas: %s" % fmt(R["grupos"]),
            "  productos: %s" % fmt(R["productos"]),
            "  analítica: %s" % fmt(R["analitica"]),
            "  diarios: %s" % fmt(R["diarios"]),
            "  impuesto de venta por defecto: %s" % R["impuesto_defecto"],
            "  términos de pago: %s" % fmt(R["terminos"]),
            "  verificadoras con cuenta 21030101: %s" % (", ".join(R["socios"]) or "-"),
        ]
        _logger.info("\n".join(lineas))
