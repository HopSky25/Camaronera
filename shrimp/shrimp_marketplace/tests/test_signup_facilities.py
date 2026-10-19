"""Paso «Instalaciones» del asistente de alta (19.0.1.22.0): fincas y
piscinas creadas a nombre de la empresa, «Lo haré después» y validación."""
import re

from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_user_registry.tests.legal_utils import signup_post


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


@tagged("post_install", "-at_install")
class TestSignupFacilities(HttpCase):

    def _post(self, roles, correo, **extra):
        html = self.url_open("/register").text
        datos = signup_post(self.env, roles, correo, csrf_token=_csrf(html))
        datos.update(extra)
        return self.url_open("/register/submit", data=datos, allow_redirects=False)

    def _socio(self, correo):
        return self.env["res.partner"].sudo().search([("email", "=", correo)], limit=1)

    def _finca(self, **extra):
        estado = self.env["res.country.state"].sudo().search([("country_id.code", "=", "EC")], limit=1)
        datos = {
            "fac_0_role": "camaronera", "fac_0_name": "Finca El Porvenir", "fac_0_code": "F-01",
            "fac_0_state_id": str(estado.id), "fac_0_city": "Machala", "fac_0_address": "Puerto Jelí",
            "fac_0_lat": "-3.25", "fac_0_lng": "-79.95",
            "pond_0_0_name": "P1", "pond_0_0_code": "P-1", "pond_0_0_type": "earth",
            "pond_0_0_length": "80", "pond_0_0_width": "60", "pond_0_0_depth": "1.2",
            "pond_0_1_name": "P2", "pond_0_1_type": "geomembrane", "pond_0_1_length": "100", "pond_0_1_width": "50",
        }
        datos.update(extra)
        return datos, estado

    def test_paso_en_el_asistente(self):
        html = self.url_open("/register").text
        self.assertIn('id="shrimp_reg_step_facilities"', html)
        self.assertIn('data-step-key="facilities"', html)
        self.assertIn('href="/marketplace/my-facilities"', html)

    def test_finca_con_dos_piscinas(self):
        datos, estado = self._finca()
        r = self._post(["camaronera"], "finca.alta@prueba.test", **datos)
        self.assertIn(r.status_code, (302, 303), r.text[:400])
        socio = self._socio("finca.alta@prueba.test")
        fac = self.env["shrimp.partner.facility"].sudo().search([("partner_id", "=", socio.id)])
        self.assertEqual(len(fac), 1)
        self.assertEqual((fac.name, fac.code, fac.facility_type, fac.city, fac.province),
                         ("Finca El Porvenir", "F-01", "farm", "Machala", estado.name))
        self.assertAlmostEqual(fac.latitude, -3.25)
        ponds = fac.pond_ids.sorted("name")
        self.assertEqual(ponds.mapped("name"), ["P1", "P2"])
        self.assertEqual(ponds.mapped("partner_id"), socio)
        self.assertAlmostEqual(ponds[0].area_m2, 4800.0)
        self.assertEqual(ponds[1].pond_type, "geomembrane")

    def test_lo_hare_despues(self):
        datos, _e = self._finca(facilities_later="1")
        r = self._post(["camaronera"], "despues.alta@prueba.test", **datos)
        self.assertIn(r.status_code, (302, 303))
        socio = self._socio("despues.alta@prueba.test")
        self.assertTrue(socio)
        self.assertFalse(self.env["shrimp.partner.facility"].sudo().search([("partner_id", "=", socio.id)]))

    def test_sin_instalaciones_igual_se_registra(self):
        r = self._post(["camaronera"], "sinfinca.alta@prueba.test")
        self.assertIn(r.status_code, (302, 303))
        self.assertTrue(self._socio("sinfinca.alta@prueba.test"))

    def test_piscina_sin_nombre_rechazada(self):
        datos, _e = self._finca(pond_0_1_name="")
        r = self._post(["camaronera"], "piscina.mal@prueba.test", **datos)
        self.assertEqual(r.status_code, 200)
        self.assertIn("escribe el nombre de cada piscina", r.text)
        self.assertFalse(self._socio("piscina.mal@prueba.test"))
        # Lo escrito vuelve al formulario para no perderlo.
        self.assertIn("Finca El Porvenir", r.text)

    def test_medida_negativa_rechazada(self):
        datos, _e = self._finca(pond_0_0_length="-5")
        r = self._post(["camaronera"], "negativa@prueba.test", **datos)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self._socio("negativa@prueba.test"))

    def test_instalacion_de_perfil_no_elegido_rechazada(self):
        # 19.0.1.22.4: antes se ignoraba en silencio; ahora vuelve con el aviso.
        datos, _e = self._finca(fac_0_role="laboratorio", pond_0_0_type="tank", pond_0_1_type="tank")
        r = self._post(["camaronera"], "ajena.fac@prueba.test", **datos)
        self.assertEqual(r.status_code, 200)
        self.assertIn("no corresponde a los perfiles que elegiste", r.text)
        self.assertFalse(self._socio("ajena.fac@prueba.test"))

    def test_laboratorio_con_tanques(self):
        r = self._post(["laboratorio"], "lab.fac@prueba.test", **{
            "fac_0_role": "laboratorio", "fac_0_name": "Lab Norte",
            "pond_0_0_name": "T1", "pond_0_0_type": "tank"})
        self.assertIn(r.status_code, (302, 303))
        socio = self._socio("lab.fac@prueba.test")
        fac = self.env["shrimp.partner.facility"].sudo().search([("partner_id", "=", socio.id)])
        self.assertEqual(fac.facility_type, "laboratory")
        self.assertEqual(fac.pond_ids.pond_type, "tank")

    # ---- 19.0.1.22.3: área y piscinas de la finca sin pedirlas al registrarse ----
    def test_area_y_piscinas_salen_de_las_instalaciones(self):
        datos, _estado = self._finca()
        r = self._post(["camaronera"], "finca.area@prueba.test", **datos)
        self.assertIn(r.status_code, (302, 303), r.text[:400])
        socio = self._socio("finca.area@prueba.test")
        self.assertFalse(socio.farm_area_ha)
        # 80x60 (tierra) + 100x50 (geomembrana) = 9800 m2 = 0.98 ha.
        self.assertAlmostEqual(socio.shrimp_farm_area_effective_ha, 0.98)
        self.assertEqual(socio.shrimp_pond_count_effective, 2)
        self.assertEqual(socio.shrimp_farm_area_source, "ponds")
        # Mi cuenta abre y muestra el área calculada aunque no haya hectáreas declaradas.
        self.authenticate("finca.area@prueba.test", "Clave-segura-123")
        r = self.url_open("/marketplace/my-account")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Según tus piscinas registradas", r.text)
        self.assertIn('name="farm_area_ha"', r.text)

    def test_area_sin_piscinas_usa_el_dato_manual_o_nada(self):
        r = self._post(["camaronera"], "finca.sinpisc@prueba.test")
        self.assertIn(r.status_code, (302, 303), r.text[:400])
        socio = self._socio("finca.sinpisc@prueba.test")
        self.assertEqual((socio.shrimp_farm_area_effective_ha, socio.shrimp_pond_count_effective,
                          socio.shrimp_farm_area_source), (0.0, 0, "none"))
        socio.sudo().write({"farm_area_ha": 25.0, "shrimp_num_piscinas": 4})
        self.assertEqual((socio.shrimp_farm_area_effective_ha, socio.shrimp_pond_count_effective,
                          socio.shrimp_farm_area_source), (25.0, 4, "manual"))
        # Los tanques no son área de finca.
        self.env["shrimp.partner.pond"].sudo().create({
            "partner_id": socio.id, "name": "T1", "pond_type": "tank", "length_m": 10, "width_m": 10})
        self.assertEqual(socio.shrimp_farm_area_source, "manual")

    # ---- 19.0.1.22.4: una sola lista de instalaciones con su tipo ----
    def _mixta(self, **extra):
        estado = self.env["res.country.state"].sudo().search([("country_id.code", "=", "EC")], limit=1)
        datos = {
            "fac_0_type": "farm", "fac_0_name": "Finca Mixta", "fac_0_code": "F-M",
            "fac_0_state_id": str(estado.id), "fac_0_city": "Machala",
            "fac_1_type": "laboratory", "fac_1_name": "Lab Mixto", "fac_1_code": "L-M",
            "pond_0_0_name": "P1", "pond_0_0_type": "earth", "pond_0_0_mode": "dimensions",
            "pond_0_0_length": "100", "pond_0_0_width": "50", "pond_0_0_depth": "1.5",
            "pond_0_1_name": "P2", "pond_0_1_type": "geomembrane", "pond_0_1_mode": "dimensions",
            "pond_0_1_length": "40", "pond_0_1_width": "25",
            "pond_1_0_name": "T1", "pond_1_0_code": "T-1", "pond_1_0_type": "tank",
            "pond_1_0_mode": "volume", "pond_1_0_volume": "12.5",
        }
        datos.update(extra)
        return datos

    def test_tabla_unica_con_tipos_mezclados(self):
        r = self._post(["camaronera", "laboratorio"], "mixta.fac@prueba.test", **self._mixta())
        self.assertIn(r.status_code, (302, 303), r.text[:400])
        socio = self._socio("mixta.fac@prueba.test")
        facs = self.env["shrimp.partner.facility"].sudo().search([("partner_id", "=", socio.id)])
        # Una sola vez cada una (el gancho corre por perfil, se crean una vez).
        self.assertEqual(sorted(facs.mapped("facility_type")), ["farm", "laboratory"])
        finca = facs.filtered(lambda f: f.facility_type == "farm")
        lab = facs.filtered(lambda f: f.facility_type == "laboratory")
        self.assertEqual(sorted(finca.pond_ids.mapped("name")), ["P1", "P2"])
        self.assertEqual(lab.pond_ids.mapped("name"), ["T1"])
        tanque = lab.pond_ids
        self.assertEqual((tanque.capacity_mode, tanque.manual_volume_m3, tanque.volume_m3), ("volume", 12.5, 12.5))
        p1 = finca.pond_ids.filtered(lambda p: p.name == "P1")
        self.assertAlmostEqual(p1.area_m2, 5000.0)
        self.assertAlmostEqual(p1.volume_m3, 7500.0)
        self.assertEqual(set((facs.pond_ids).mapped("partner_id")), {socio})

    def test_paso_ofrece_los_tipos_de_los_perfiles(self):
        html = self.url_open("/register").text
        self.assertIn('id="shrimpFacModal"', html)
        self.assertIn('id="shrimpPondModal"', html)
        self.assertIn("Para agregar una piscina primero debes agregar una instalación", html)
        self.assertIn("hatchery", html)  # tipos de instalación (data-kinds)

    def test_piscina_sin_instalacion_rechazada(self):
        datos = self._mixta()
        datos.update({"pond_7_0_name": "Huérfana", "pond_7_0_type": "earth", "pond_7_0_length": "10",
                      "pond_7_0_width": "10"})
        r = self._post(["camaronera", "laboratorio"], "huerfana.fac@prueba.test", **datos)
        self.assertEqual(r.status_code, 200)
        self.assertIn("debe pertenecer a una de las instalaciones", r.text)
        self.assertFalse(self._socio("huerfana.fac@prueba.test"))
        # Solo piscinas, sin ninguna instalación.
        r = self._post(["camaronera"], "solo.pisc@prueba.test", **{
            "pond_0_0_name": "P1", "pond_0_0_type": "earth"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self._socio("solo.pisc@prueba.test"))

    def test_tipo_de_piscina_no_admitido_rechazado(self):
        r = self._post(["camaronera", "laboratorio"], "tipo.mal@prueba.test",
                       **self._mixta(pond_0_1_type="tank"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("no corresponde a una finca", r.text)
        self.assertFalse(self._socio("tipo.mal@prueba.test"))
        # Lo escrito vuelve (las dos instalaciones y sus piscinas).
        self.assertIn("Lab Mixto", r.text)
        self.assertIn("Finca Mixta", r.text)

    def test_tipo_de_instalacion_de_otro_perfil_rechazado(self):
        r = self._post(["camaronera"], "hatch.mal@prueba.test", **{
            "fac_0_type": "hatchery", "fac_0_name": "Centro X"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("no corresponde a los perfiles que elegiste", r.text)

    def test_rangos_y_codigos_repetidos(self):
        for extra, correo in (({"pond_0_0_depth": "80"}, "prof.mal@prueba.test"),
                              ({"pond_1_0_volume": "abc"}, "vol.mal@prueba.test"),
                              ({"fac_0_lat": "-95"}, "lat.mal@prueba.test"),
                              ({"fac_1_code": "f-m"}, "codfac.rep@prueba.test"),
                              ({"pond_0_1_code": "t-1"}, "codpis.rep@prueba.test"),
                              ({"pond_0_0_mode": "otro"}, "modo.mal@prueba.test")):
            r = self._post(["camaronera", "laboratorio"], correo, **self._mixta(**extra))
            self.assertEqual(r.status_code, 200, extra)
            self.assertFalse(self._socio(correo), extra)

    def test_editar_y_eliminar_se_reflejan_en_el_envio(self):
        # El navegador renumera tras editar y eliminar: lo enviado es el estado final
        # (se eliminó P2 y se editó T1, que pasó a «Canal» por dimensiones).
        datos = self._mixta()
        for k in [k for k in datos if k.startswith("pond_0_1_")]:
            datos.pop(k)
        datos.update({"pond_1_0_name": "Canal 1", "pond_1_0_type": "raceway", "pond_1_0_mode": "dimensions",
                      "pond_1_0_length": "20", "pond_1_0_width": "2", "pond_1_0_depth": "1",
                      "pond_1_0_volume": "999"})
        r = self._post(["camaronera", "laboratorio"], "edit.fac@prueba.test", **datos)
        self.assertIn(r.status_code, (302, 303), r.text[:400])
        socio = self._socio("edit.fac@prueba.test")
        ponds = self.env["shrimp.partner.pond"].sudo().search([("partner_id", "=", socio.id)])
        self.assertEqual(sorted(ponds.mapped("name")), ["Canal 1", "P1"])
        canal = ponds.filtered(lambda p: p.name == "Canal 1")
        self.assertEqual((canal.pond_type, canal.capacity_mode, canal.manual_volume_m3), ("raceway", "dimensions", 0.0))
        self.assertAlmostEqual(canal.volume_m3, 40.0)
        # Eliminar una instalación elimina sus piscinas: no llegan en el envío.
        datos = {k: v for k, v in self._mixta().items() if not k.startswith(("fac_1_", "pond_1_"))}
        r = self._post(["camaronera", "laboratorio"], "del.fac@prueba.test", **datos)
        self.assertIn(r.status_code, (302, 303), r.text[:400])
        socio = self._socio("del.fac@prueba.test")
        facs = self.env["shrimp.partner.facility"].sudo().search([("partner_id", "=", socio.id)])
        self.assertEqual(facs.mapped("name"), ["Finca Mixta"])
        self.assertEqual(len(facs.pond_ids), 2)

    def test_lo_hare_despues_con_tabla_unica(self):
        r = self._post(["camaronera", "laboratorio"], "despues.mixta@prueba.test",
                       **self._mixta(facilities_later="1", pond_9_0_name="X"))
        self.assertIn(r.status_code, (302, 303), r.text[:400])
        socio = self._socio("despues.mixta@prueba.test")
        self.assertFalse(self.env["shrimp.partner.facility"].sudo().search([("partner_id", "=", socio.id)]))

    def test_etiquetas_del_tipo_de_instalacion(self):
        sel = dict(self.env["shrimp.partner.facility"]._fields["facility_type"].selection)
        self.assertEqual(sel["hatchery"], "Centro de maduración (naupliero)")
        self.assertEqual(sel["farm"], "Finca")
