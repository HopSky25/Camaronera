"""Fase 1 larva/nauplio: Mi panel por rol, filtros de larva en el catálogo,
ficha de calidad del lote y secciones de la landing.

Todo se monta aquí (sin depender de la demo). Las búsquedas del catálogo
llevan q=F1 para no depender de cuántos lotes de demo haya en la base.
"""
import base64
import json
from datetime import date, timedelta
from urllib.parse import urlencode

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, PDF_MIN, PNG_1PX, crear_socios, csrf_de, producto

MP = "shrimp_marketplace."
UR = "shrimp_user_registry."


@tagged("post_install", "-at_install")
class TestFase1Larva(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "f1")
        cls.att = env["ir.attachment"].create({
            "name": "pcr.pdf", "datas": base64.b64encode(PDF_MIN), "mimetype": "application/pdf"})
        hoy = date.today()
        spr, spr_plus = env.ref(MP + "shrimp_genetics_spr"), env.ref(MP + "shrimp_genetics_spr_plus")
        fast = env.ref(MP + "shrimp_genetics_fast_growth")
        cls.spr_plus = spr_plus

        # Semillero: nauplio publicado, una corrida en borrador y un certificado por vencer.
        cls.nauplio = producto(env, cls.s["sem"], nombre="Nauplio F1 SPR",
                               etapa=MP + "shrimp_stage_nauplio", initial_qty=1000.0,
                               genetics_line_id=spr.id, survival_rate=92.0,
                               expected_delivery_date=hoy)
        cls.borrador = producto(env, cls.s["sem"], nombre="Corrida borrador F1",
                                etapa=MP + "shrimp_stage_nauplio", publicado=False)
        env["shrimp.user.certificate.line"].create({
            "partner_id": cls.s["sem"].id, "certificate_id": env.ref(UR + "shrimp_certificate_registro_larvas").id,
            "certificate_number": "AGRO-F1", "issue_date": hoy - timedelta(days=300),
            "expiry_date": hoy + timedelta(days=10), "file_attachment_id": cls.att.id, "status": "approved"})

        # El laboratorio compra nauplio: nace su lote (borrador) en el inventario.
        cls.tx_nauplio = cls.nauplio.execute_purchase_flow(cls.s["lab"], 100.0)["transaction"]
        cls.tx_nauplio.action_receive()
        cls.resultado = cls.tx_nauplio.result_product_id

        # Larva publicada: con PCR vigente, con PCR vencido y de otro laboratorio.
        cls.pl_ok = producto(env, cls.s["lab"], nombre="PL12 F1 PCR", etapa=MP + "shrimp_stage_pl12",
                             genetics_line_id=spr_plus.id, survival_rate=85.0,
                             expected_delivery_date=hoy)
        cls.pl_vencido = producto(env, cls.s["lab"], nombre="PL10 F1 vencido", etapa=MP + "shrimp_stage_pl10",
                                  genetics_line_id=spr_plus.id, survival_rate=80.0)
        cls.pl_ajeno = producto(env, cls.s["lab2"], nombre="PL12 F1 ajeno", etapa=MP + "shrimp_stage_pl12",
                                genetics_line_id=fast.id, survival_rate=62.0)
        Line = env["shrimp.product.certificate.line"].with_context(shrimp_keep_cert_status=True)
        pcr = env.ref(UR + "shrimp_certificate_pcr_wssv")
        Line.create({"product_id": cls.pl_ok.id, "certificate_id": pcr.id, "number": "PCR-F1-OK",
                     "status": "approved", "attachment_id": cls.att.id,
                     "issue_date": hoy - timedelta(days=20), "expiry_date": hoy + timedelta(days=60)})
        Line.create({"product_id": cls.pl_vencido.id, "certificate_id": pcr.id, "number": "PCR-F1-OLD",
                     "status": "approved", "attachment_id": cls.att.id,
                     "issue_date": hoy - timedelta(days=200), "expiry_date": hoy - timedelta(days=5)})

        # Camarón adulto (para la pestaña Camarón).
        cls.camaron = producto(env, cls.s["cam"], nombre="Camaron F1 engorde", etapa=MP + "shrimp_stage_engorde",
                               uom_id=env.ref(MP + "uom_libra").id, presentation="entero",
                               size_grade_id=env["shrimp.size.grade"].search([("presentation", "=", "entero")], limit=1).id)

        # Ficha de calidad del lote con PCR negativo.
        cls.ficha = env["shrimp.product.quality"].create({
            "product_id": cls.pl_ok.id, "sample_date": hoy - timedelta(days=2),
            "pcr_wssv": "negativo", "pcr_ihhnv": "negativo", "pcr_ahpnd": "negativo", "pcr_ehp": "negativo",
            "stress_test_survival": 88.0, "uniformity": 91.0, "tank": "T-04",
            "analysis_lab": "Lab Diagnóstico F1", "attachment_id": cls.att.id})

    def _login(self, clave):
        self.authenticate(self.s["u_" + clave].login, CLAVE)

    def _cards(self, **params):
        params.setdefault("q", "F1")
        r = self.url_open("/marketplace/cards", params=dict(params, offset=0))
        self.assertEqual(r.status_code, 200)
        return json.loads(r.text)

    # ================================================================ Mi panel
    def test_panel_requiere_sesion(self):
        r = self.url_open("/my/dashboard", allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        self.assertIn("/web/login", r.headers.get("Location", ""))

    def test_panel_semillero(self):
        data = self.env["shrimp.dashboard"]._panel_data(self.s["sem"])
        kpis = {k["key"]: k for k in data["kpis"]}
        self.assertEqual(data["role"], "semillero")
        self.assertIn("900", kpis["available"]["value"], "1000 publicados - 100 vendidos")
        self.assertIn("100", kpis["sold_month"]["value"])
        alertas = {a["key"] for a in data["alerts"]}
        self.assertIn("drafts", alertas, "la corrida en borrador aparece como pendiente de publicar")
        self.assertTrue(any(k.startswith("cert_") for k in alertas), "certificado por vencer")

        self._login("sem")
        r = self.url_open("/my/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Mi panel · Semillero", r.text)
        self.assertIn("Disponible publicado", r.text)
        self.assertIn("Nauplio F1 SPR", r.text)
        self.assertIn("vence en", r.text)
        self.assertIn("Últimas ventas", r.text)
        # Nada de otros socios.
        self.assertNotIn("PL12 F1 PCR", r.text)
        self.assertNotIn("PL12 F1 ajeno", r.text)

    def test_panel_laboratorio(self):
        self._login("lab")
        r = self.url_open("/my/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Mi panel · Laboratorio", r.text)
        self.assertIn("Nauplio en inventario", r.text)
        self.assertIn("Inventario de nauplio comprado", r.text)
        self.assertIn("Publicar larva de este lote", r.text)
        self.assertIn("/marketplace/products/%s/edit" % self.resultado.uuid_ref, r.text)
        self.assertIn("Supervivencia publicada", r.text)
        self.assertIn("PL12 F1 PCR", r.text)
        self.assertNotIn("PL12 F1 ajeno", r.text, "lotes de otro laboratorio")
        self.assertNotIn("Corrida borrador F1", r.text, "lotes del semillero")
        r = self.url_open("/marketplace/my-lots")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Publicar larva de este lote", r.text)

    def test_panel_aislado_entre_socios(self):
        data = self.env["shrimp.dashboard"]._panel_data(self.s["lab2"])
        texto = json.dumps([s["rows"] for s in data["sections"]], default=str)
        self.assertNotIn("PL12 F1 PCR", texto)
        self.assertIn("PL12 F1 ajeno", texto)
        inv = {k["key"]: k for k in data["kpis"]}["nauplio_stock"]
        self.assertTrue(inv["value"].startswith("0"), "lab2 no compró nauplio")
        self._login("lab2")
        r = self.url_open("/my/dashboard")
        self.assertNotIn(self.resultado.uuid_ref, r.text)

    def test_panel_camaronera_y_tarjeta_en_my(self):
        self._login("cam")
        r = self.url_open("/my/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Larva en inventario", r.text)
        self.assertIn("Piscinas activas", r.text)
        r = self.url_open("/my")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sp_mypanel_card"', r.text)
        self.assertIn('href="/my/dashboard"', r.text)

    # ======================================================= Filtros de larva
    def test_filtro_tipo(self):
        r = self.url_open("/marketplace?tipo=larva&q=F1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("PL12 F1 PCR", r.text)
        self.assertNotIn("Nauplio F1 SPR", r.text)
        self.assertNotIn("Camaron F1 engorde", r.text)
        self.assertIn("Larva para tu próxima siembra", r.text)
        self.assertIn('id="sp_tipo_tabs"', r.text)
        r = self.url_open("/marketplace?tipo=nauplio&q=F1")
        self.assertIn("Nauplio F1 SPR", r.text)
        self.assertNotIn("PL12 F1 PCR", r.text)
        r = self.url_open("/marketplace?tipo=camaron&q=F1")
        self.assertIn("Camaron F1 engorde", r.text)
        self.assertNotIn("PL12 F1 PCR", r.text)

    def test_filtro_genetica_supervivencia_pcr_en_pagina(self):
        base = "/marketplace?q=F1&tipo=larva&genetics=%s" % self.spr_plus.id
        r = self.url_open(base)
        self.assertIn("PL12 F1 PCR", r.text)
        self.assertIn("PL10 F1 vencido", r.text)
        self.assertNotIn("PL12 F1 ajeno", r.text)
        r = self.url_open("/marketplace?q=F1&tipo=larva&survival_min=80")
        self.assertIn("PL12 F1 PCR", r.text)
        self.assertNotIn("PL12 F1 ajeno", r.text, "62 % < 80 %")
        r = self.url_open(base + "&pcr=1")
        self.assertIn("PL12 F1 PCR", r.text)
        self.assertNotIn("PL10 F1 vencido", r.text, "PCR vencido no cuenta")
        # Insignias y vista de lista.
        r = self.url_open(base + "&pcr=1&vista=lista")
        self.assertIn("PCR vigente", r.text)
        self.assertIn("Superv. 85 %", r.text)
        self.assertIn("s-products--list", r.text)
        self.assertIn('data-pcr="1"', r.text, "«Cargar más» recibe el filtro")

    def test_filtros_en_cargar_mas(self):
        data = self._cards(tipo="larva", genetics=self.spr_plus.id, pcr=1)
        self.assertIn("PL12 F1 PCR", data["html"])
        self.assertNotIn("PL10 F1 vencido", data["html"])
        self.assertEqual(data["total"], 1)
        data = self._cards(tipo="larva", survival_min=80)
        self.assertNotIn("PL12 F1 ajeno", data["html"])
        self.assertIn("PL12 F1 PCR", data["html"])
        data = self._cards(tipo="larva", sort="survival")
        self.assertLess(data["html"].index("PL12 F1 PCR"), data["html"].index("PL12 F1 ajeno"))

    # ============================================ Cajón de filtros (Opción C)
    def test_cajon_filtros_y_etiquetas(self):
        r = self.url_open("/marketplace?q=F1&tipo=larva&survival_min=80&pcr=1&sort=price_asc&vista=lista")
        self.assertEqual(r.status_code, 200)
        html = r.text
        # Sin barra lateral: el catálogo ocupa todo el ancho.
        self.assertNotIn("s-market-side", html)
        # Cajón accesible con todas las secciones.
        self.assertIn('id="sFilterDrawer"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-labelledby="sFilterDrawerTitle"', html)
        for sec in ("sp_filter_tipo", "sf_sec_species", "sf_sec_presentation", "sf_sec_size",
                    "sf_sec_location", "sf_sec_price", "sp_filter_larva", "sp_filter_survival",
                    "sp_filter_certs", "sf_sec_seller"):
            self.assertIn('id="%s"' % sec, html)
        # Botón «Filtros» con 3 activos (tipo, supervivencia, PCR; no cuenta q ni sort).
        self.assertIn('aria-label="Filtros, 3 activos"', html)
        # Etiquetas removibles: conservan búsqueda, orden y vista.
        self.assertIn("Larva (PL)", html)
        self.assertIn("Supervivencia 80 %+", html)
        self.assertIn('data-filter="pcr"', html)
        self.assertIn("/marketplace?q=F1&amp;tipo=larva&amp;survival_min=80&amp;sort=price_asc&amp;vista=lista#listado", html)
        self.assertIn("Limpiar todo", html)
        self.assertIn("/marketplace?q=F1&amp;sort=price_asc&amp;vista=lista#listado", html)

    def test_conteo_del_cajon(self):
        r = self.url_open("/marketplace/count?" + urlencode(
            {"q": "F1", "tipo": "larva", "genetics": self.spr_plus.id, "pcr": 1}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.text)["count"], 1)
        total = json.loads(self.url_open("/marketplace/count?q=F1&tipo=larva").text)["count"]
        self.assertEqual(total, self._cards(tipo="larva")["total"])

    # ======================================= Catálogo según el perfil activo
    def _tipos_comprables(self, rol):
        Partner = self.env["res.partner"]
        rol_vendedor = {"nauplio": "semillero", "larva": "laboratorio", "camaron": "camaronera"}
        return [t for t in ("nauplio", "larva", "camaron")
                if Partner._shrimp_type_can(rol, "buy_from_%s" % rol_vendedor[t])]

    def _semillero_vendedor(self):
        """Otro semillero que publica nauplio y también postlarva (el lote
        es de rol semillero pero de TIPO larva por su estadío)."""
        sem2 = self.env["res.partner"].create({
            "name": "Semillero otro F1", "is_company": True, "email": "sem2.f1@prueba.test",
            "vat_or_id": "0911100009001", "shrimp_user_type": "semillero"})
        producto(self.env, sem2, nombre="Nauplio F1 otro", etapa=MP + "shrimp_stage_nauplio")
        producto(self.env, sem2, nombre="PL12 F1 de semillero", etapa=MP + "shrimp_stage_pl12")
        return sem2

    def test_semillero_solo_ve_lo_que_puede_comprar(self):
        self._semillero_vendedor()
        permitidos = self._tipos_comprables("semillero")
        self.assertIn("nauplio", permitidos)
        self.assertNotIn("larva", permitidos)
        self._login("sem")
        pl12 = self.env.ref(MP + "shrimp_stage_pl12")
        for url in ("/marketplace?q=F1", "/marketplace?q=F1&tipo=larva",
                    "/marketplace?q=F1&stage=%s" % pl12.id, "/marketplace?q=F1&tipo=larva&stage=%s" % pl12.id):
            html = self.url_open(url).text
            self.assertNotIn("PL12 F1 de semillero", html, url)
            self.assertNotIn("PL12 F1 PCR", html, url)
            self.assertNotIn("Larva para tu próxima siembra", html, url)
            self.assertNotIn('data-tipo="larva"', html, "sin pestaña Larva: %s" % url)
            if "stage=" not in url:
                self.assertIn("Nauplio F1 otro", html, url)
        # Pestañas / «Tipo» del cajón: solo los tipos comprables; con uno solo, ninguna.
        html = self.url_open("/marketplace?q=F1").text
        if len(permitidos) == 1:
            self.assertNotIn('id="sp_tipo_tabs"', html)
            self.assertNotIn('id="sp_filter_tipo"', html)
        # «Cargar más» y el conteo del cajón aplican la misma regla.
        data = self._cards(tipo="larva")
        self.assertNotIn("PL12 F1", data["html"])
        self.assertIn("Nauplio F1 otro", data["html"])
        conteo = json.loads(self.url_open("/marketplace/count?q=F1&tipo=larva").text)["count"]
        self.assertEqual(conteo, data["total"])
        self.assertEqual(json.loads(self.url_open(
            "/marketplace/count?q=F1&stage=%s" % pl12.id).text)["count"], 0)

    def test_pestanas_por_perfil(self):
        self._semillero_vendedor()
        for clave, rol in (("lab", "laboratorio"), ("cam", "camaronera"), ("sem", "semillero")):
            permitidos = self._tipos_comprables(rol)
            self._login(clave)
            html = self.url_open("/marketplace?q=F1").text
            for tipo in ("nauplio", "larva", "camaron"):
                marca = 'data-tipo="%s"' % tipo
                if tipo in permitidos and len(permitidos) > 1:
                    self.assertIn(marca, html, "%s debe ver la pestaña %s" % (rol, tipo))
                else:
                    self.assertNotIn(marca, html, "%s no debe ver la pestaña %s" % (rol, tipo))
        # El laboratorio sí ve la postlarva del semillero (rol y tipo comprables).
        self._login("lab")
        self.assertIn("PL12 F1 de semillero", self.url_open("/marketplace?q=F1&tipo=larva").text)
        # El visitante ve el catálogo público entero, con todas las pestañas.
        self.authenticate(None, None)
        html = self.url_open("/marketplace?q=F1").text
        for tipo in ("nauplio", "larva", "camaron"):
            self.assertIn('data-tipo="%s"' % tipo, html)
        self.assertIn("PL12 F1 de semillero", html)

    def test_destacados_segun_perfil(self):
        self._min_resenas(0)
        # El semillero no ve «Laboratorios destacados» ni forzando ?tipo=larva.
        self._login("sem")
        html = self.url_open("/marketplace?tipo=larva").text
        self.assertNotIn("Laboratorios destacados", html)
        # La camaronera y el visitante sí.
        self._login("cam")
        self.assertIn("Laboratorios destacados", self.url_open("/marketplace?tipo=larva").text)
        self.authenticate(None, None)
        self.assertIn("Laboratorios destacados", self.url_open("/marketplace?tipo=larva").text)

    def _min_resenas(self, n):
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_marketplace.featured_min_reviews", str(n))

    def test_destacados_y_avisame(self):
        # Sin mínimo de reseñas (el de fábrica, 10, se prueba aparte).
        self._min_resenas(0)
        r = self.url_open("/marketplace?tipo=larva")
        self.assertIn("Laboratorios destacados", r.text)
        # Con la demo cargada el laboratorio de prueba puede no estar entre los 6
        # primeros: se comprueba la lista completa.
        destacados = self.env["shrimp.product"]._shrimp_featured_sellers("larva", limit=1000)
        self.assertIn(self.s["lab"], [d["partner"] for d in destacados])
        self.assertNotIn(self.s["sem"], [d["partner"] for d in destacados], "solo laboratorios")
        # Estado vacío: al público se le ofrece iniciar sesión.
        r = self.url_open("/marketplace?tipo=larva&q=NOEXISTE-F1")
        self.assertIn('id="sp_avisame"', r.text)
        self.assertIn("/web/login?redirect=", r.text)
        # Con sesión: «Avísame» guarda el interés (una sola vez).
        self._login("cam")
        r = self.url_open("/marketplace?tipo=larva&q=NOEXISTE-F1&survival_min=90")
        token = csrf_de(r.text)
        datos = {"csrf_token": token, "tipo": "larva", "survival_min": "90", "pcr": "1"}
        r = self.url_open("/marketplace/notify-me", data=datos)
        self.assertEqual(r.status_code, 200)
        self.url_open("/marketplace/notify-me", data=datos)
        intereses = self.env["shrimp.larva.interest"].search([("partner_id", "=", self.s["cam"].id)])
        self.assertEqual(len(intereses), 1)
        self.assertEqual(intereses.survival_min, 90)
        self.assertTrue(intereses.pcr_required)

    # ======================================================= Ficha de calidad
    def test_ficha_visible_para_comprador_y_publico(self):
        r = self.url_open("/marketplace/product/%s" % self.pl_ok.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="shrimp_product_quality"', r.text)
        self.assertIn("Ficha de calidad del lote", r.text)
        self.assertIn("Lab Diagnóstico F1", r.text)
        self.assertNotIn("Gestionar fichas", r.text, "el público no edita")
        url_pdf = "/marketplace/product/%s/quality/%s/report" % (self.pl_ok.uuid_ref, self.ficha.uuid_ref)
        self.assertIn(url_pdf, r.text)
        self.assertEqual(self.url_open(url_pdf).status_code, 200)
        # Lote sin ficha: el público no ve el bloque vacío.
        r = self.url_open("/marketplace/product/%s" % self.pl_ajeno.uuid_ref)
        self.assertNotIn('id="shrimp_product_quality"', r.text)

    def test_ficha_crud_solo_dueno(self):
        Quality = self.env["shrimp.product.quality"]
        url = "/marketplace/products/%s/quality" % self.pl_vencido.uuid_ref
        # Otro laboratorio: 403 al ver y al guardar.
        self._login("lab2")
        self.assertEqual(self.url_open(url).status_code, 403)
        r = self.url_open("/marketplace/products/%s/quality" % self.pl_ajeno.uuid_ref)
        token = csrf_de(r.text)
        r = self.url_open(url + "/save", data={"csrf_token": token, "pcr_wssv": "negativo"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Quality.search([("product_id", "=", self.pl_vencido.id)]))

        # Dueño: crea con PDF y muestreo.
        self._login("lab")
        r = self.url_open(url)
        self.assertEqual(r.status_code, 200)
        token = csrf_de(r.text)
        evol_antes = self.env["shrimp.product.evolution"].search_count([("product_id", "=", self.pl_vencido.id)])
        r = self.url_open(url + "/save", data={
            "csrf_token": token, "sample_date": str(date.today()), "pcr_wssv": "negativo",
            "pcr_ihhnv": "negativo", "pcr_ahpnd": "positivo", "pcr_ehp": "no_realizado",
            "stress_test_survival": "77,5", "uniformity": "88", "tank": "T-09",
            "analysis_lab": "Lab F1", "register_sample": "1",
        }, files={"report_file": ("informe.pdf", PDF_MIN, "application/pdf")})
        self.assertEqual(r.status_code, 200)
        ficha = Quality.search([("product_id", "=", self.pl_vencido.id)])
        self.assertEqual(len(ficha), 1)
        self.assertEqual(ficha.pcr_status, "positivo")
        self.assertAlmostEqual(ficha.stress_test_survival, 77.5)
        self.assertEqual(ficha.attachment_id.mimetype, "application/pdf")
        self.assertTrue(ficha.evolution_id)
        self.assertEqual(self.env["shrimp.product.evolution"].search_count(
            [("product_id", "=", self.pl_vencido.id)]), evol_antes + 1)

        # Edita.
        r = self.url_open(url + "?ficha=%s" % ficha.uuid_ref)
        token = csrf_de(r.text)
        self.url_open(url + "/save", data={"csrf_token": token, "ficha": ficha.uuid_ref,
                                             "pcr_ahpnd": "negativo", "pcr_wssv": "negativo",
                                             "pcr_ihhnv": "negativo", "pcr_ehp": "negativo",
                                             "sample_date": str(date.today())})
        ficha.invalidate_recordset()
        self.assertEqual(ficha.pcr_status, "negativo")

        # Archivo que no es PDF: se rechaza y no crea nada.
        self.url_open(url + "/save", data={"csrf_token": token, "pcr_wssv": "negativo"},
                      files={"report_file": ("foto.png", PNG_1PX, "image/png")})
        self.assertEqual(Quality.search_count([("product_id", "=", self.pl_vencido.id)]), 1)

        # Otro laboratorio no la borra; el dueño sí.
        self._login("lab2")
        r = self.url_open("/marketplace/products/%s/quality" % self.pl_ajeno.uuid_ref)
        r = self.url_open(url + "/%s/delete" % ficha.uuid_ref, data={"csrf_token": csrf_de(r.text)})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(ficha.exists())
        self._login("lab")
        r = self.url_open(url)
        self.url_open(url + "/%s/delete" % ficha.uuid_ref, data={"csrf_token": csrf_de(r.text)})
        self.assertFalse(ficha.exists())

    def test_registrar_muestreo_rapido(self):
        self._login("lab")
        url = "/marketplace/products/%s" % self.pl_ok.uuid_ref
        r = self.url_open(url + "/quality")
        antes = self.env["shrimp.product.evolution"].search_count([("product_id", "=", self.pl_ok.id)])
        self.url_open(url + "/sampling", data={
            "csrf_token": csrf_de(r.text), "survival_rate": "83", "avg_size_mg": "2.5",
            "stage": self.env.ref(MP + "shrimp_stage_pl12").uuid_ref, "note": "Muestreo F1"})
        evol = self.env["shrimp.product.evolution"].search(
            [("product_id", "=", self.pl_ok.id)], order="id desc", limit=1)
        self.assertEqual(self.env["shrimp.product.evolution"].search_count(
            [("product_id", "=", self.pl_ok.id)]), antes + 1)
        self.assertAlmostEqual(evol.survival_rate, 83.0)
        self.assertEqual(evol.note, "Muestreo F1")

    def test_ficha_validaciones_de_modelo(self):
        Quality = self.env["shrimp.product.quality"]
        with self.assertRaises(ValidationError):
            Quality.create({"product_id": self.camaron.id})
        with self.assertRaises(ValidationError):
            Quality.create({"product_id": self.pl_ok.id, "uniformity": 120.0})
        lote_ajeno = self.env["shrimp.stock.lot"].search([("owner_id", "=", self.s["lab"].id)], limit=1)
        with self.assertRaises(ValidationError):
            Quality.create({"product_id": self.pl_ajeno.id, "origin_lot_id": lote_ajeno.id})

    def test_ficha_en_trazabilidad(self):
        tx = self.pl_ok.execute_purchase_flow(self.s["cam"], 10.0)["transaction"]
        tx.action_receive()
        self._login("cam")
        r = self.url_open("/marketplace/purchases/%s/traceability" % tx.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="shrimp_trace_quality"', r.text)
        self.assertIn("Lab Diagnóstico F1", r.text)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "shrimp_marketplace.report_shrimp_full_traceability", [tx.id])[0]
        self.assertIn(b"Ficha de calidad de los lotes", html)
        self.assertIn(b"Lab Diagn", html)

    # ================================================================ Landing
    def test_destacados_minimo_resenas(self):
        """Solo salen los vendedores con el mínimo de reseñas configurado; sin
        rellenar con otros y sin sección si no cumple ninguno."""
        Product = self.env["shrimp.product"]
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_param("shrimp_marketplace.featured_min_reviews"),
            "10", "valor de fábrica")
        self.assertEqual(Product._shrimp_featured_min_reviews(), 10)
        # Las reseñas se fijan directo en el contador almacenado.
        self.s["lab"].sudo().write({"shrimp_rating_count": 12, "shrimp_rating_avg": 4.0})
        self.s["lab2"].sudo().write({"shrimp_rating_count": 3, "shrimp_rating_avg": 5.0})

        def socios():
            return [d["partner"] for d in Product._shrimp_featured_sellers("larva", limit=1000)]

        self.assertIn(self.s["lab"], socios())
        self.assertNotIn(self.s["lab2"], socios(), "3 reseñas < 10: no se rellena con él")
        for d in Product._shrimp_featured_sellers("larva", limit=1000):
            self.assertGreaterEqual(d["rating_count"], 10)
        # Bajar el mínimo en Ajustes cambia la lista.
        self.env["res.config.settings"].create({"shrimp_featured_min_reviews": 3}).execute()
        self.assertEqual(Product._shrimp_featured_min_reviews(), 3)
        self.assertIn(self.s["lab2"], socios())
        # Mínimo inalcanzable: lista vacía y sin sección en catálogo ni landing.
        self._min_resenas(100000)
        self.assertEqual(Product._shrimp_featured_sellers("larva"), [])
        self.assertNotIn('id="sp_featured"', self.url_open("/marketplace?tipo=larva").text)
        self.assertNotIn('id="sp_labs_destacados"', self.url_open("/").text)
        # Valor no numérico: se usa el de fábrica.
        self._min_resenas("abc")
        self.assertEqual(Product._shrimp_featured_min_reviews(), 10)

    def test_landing_secciones_larva(self):
        self._min_resenas(0)
        r = self.url_open("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn('id="sp_larva_lista"', r.text)
        self.assertIn("Larva lista para sembrar", r.text)
        self.assertIn("Reserva tu larva para la siembra", r.text)
        self.assertIn("/marketplace?tipo=larva", r.text)
        self.assertIn('id="sp_labs_destacados"', r.text)

    # ============================================================ Permisos
    def test_permisos_portal(self):
        """Portal: solo lectura, de sus lotes o de lotes públicos; el
        «Avísame» de cada cuenta no lo ve otra."""
        borrador = self.env["shrimp.product.quality"].create({
            "product_id": self.resultado.id, "pcr_wssv": "negativo"})
        Q = self.env["shrimp.product.quality"].with_user(self.s["u_lab2"])
        visibles = Q.search([("id", "in", [self.ficha.id, borrador.id])])
        self.assertIn(self.ficha, visibles, "lote publicado: visible")
        self.assertNotIn(borrador, visibles, "lote en borrador de otro: oculto")
        self.assertIn(borrador, self.env["shrimp.product.quality"].with_user(self.s["u_lab"]).search(
            [("id", "=", borrador.id)]), "el dueño ve la suya")
        from odoo.exceptions import AccessError
        with self.assertRaises(AccessError):
            Q.browse(self.ficha.id).write({"tank": "X"})
        with self.assertRaises(AccessError):
            Q.create({"product_id": self.pl_ajeno.id})
        interes = self.env["shrimp.larva.interest"].create({
            "partner_id": self.s["cam"].id, "search_url": "/marketplace?tipo=larva&x=f1"})
        self.assertFalse(self.env["shrimp.larva.interest"].with_user(self.s["u_lab2"]).search(
            [("id", "=", interes.id)]))
        self.assertTrue(self.env["shrimp.larva.interest"].with_user(self.s["u_cam"]).search(
            [("id", "=", interes.id)]))

    def test_portada_del_lote(self):
        foto = self.env["ir.attachment"].create({
            "name": "f.png", "datas": base64.b64encode(PNG_1PX), "mimetype": "image/png",
            "res_model": "shrimp.product", "res_id": self.pl_ok.id})
        self.pl_ok.photo_attachment_ids = [(6, 0, foto.ids)]
        self.resultado.photo_attachment_ids = [(6, 0, foto.ids)]
        r = self.url_open("/marketplace/product/%s/cover" % self.pl_ok.uuid_ref)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("Content-Type"), "image/png")
        # Lote en borrador: el público no lo ve.
        r = self.url_open("/marketplace/product/%s/cover" % self.resultado.uuid_ref)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.url_open("/marketplace/product/%d/cover" % self.pl_ok.id).status_code, 404)
