"""Mi cuenta: sección «Instalaciones y piscinas» y rediseño (19.0.1.28.0).

El laboratorio no encontraba dónde agregar instalaciones ni tanques. Ahora
/marketplace/my-account#instalaciones las gestiona con modales que envían a
las rutas de /marketplace/my-facilities (next=account), con permiso de
subusuario («Vender»), validación previa y «Archivar»."""
import base64

from odoo.tests import HttpCase, tagged

from .common import CLAVE, PDF_MIN, crear_socios, csrf_de
from .test_subcuentas import subusuario


@tagged("post_install", "-at_install")
class TestMiCuentaInstalaciones(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "mci")
        cls.lab = cls.s["lab"]
        cls.Fac = cls.env["shrimp.partner.facility"].sudo().with_context(active_test=False)
        cls.Pond = cls.env["shrimp.partner.pond"].sudo().with_context(active_test=False)

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def _pagina(self, login):
        self.authenticate(login, CLAVE)
        r = self.url_open("/marketplace/my-account")
        self.assertEqual(r.status_code, 200)
        return r.text

    def _post(self, ruta, token, **datos):
        datos.setdefault("next", "account")
        return self.url_open(ruta, data=dict(datos, csrf_token=token), allow_redirects=False)

    # ------------------------------------------------------------------
    def test_laboratorio_agrega_instalacion_y_dos_tanques(self):
        html = self._pagina(self.s["u_lab"].login)
        # Sección y navegación con el vocabulario del laboratorio.
        self.assertIn('id="instalaciones"', html)
        self.assertIn('href="#instalaciones"', html)
        self.assertIn("Agregar instalación", html)
        self.assertIn("Aún no registras laboratorios", html)
        # Tipo por defecto (laboratorio) y orden: primero los del perfil.
        self.assertIn('<option value="laboratory" selected="selected">Laboratorio</option>', html)
        self.assertLess(html.index('name="pond_type"'), html.index('<option value="tank" selected="selected">Tanque</option>'))
        self.assertLess(html.index('<option value="tank" selected'), html.index('<option value="earth">'))
        self.assertIn("Centro de maduración (naupliero)", html)
        token = csrf_de(html)

        r = self._post("/marketplace/my-facilities/facility/create", token,
                       name="Lab Norte", code="LN-1", facility_type="laboratory",
                       province="Santa Elena", city="Salinas", address="Km 5")
        self.assertEqual(r.status_code, 303)
        loc = r.headers["Location"]
        self.assertIn("/marketplace/my-account?", loc)
        self.assertIn("saved=fac_facility", loc)
        self.assertTrue(loc.endswith("#instalaciones"))
        fac = self.Fac.search([("partner_id", "=", self.lab.id), ("name", "=", "Lab Norte")])
        self.assertEqual((fac.facility_type, fac.province, fac.city), ("laboratory", "Santa Elena", "Salinas"))
        self.assertIn("fac=%s" % fac.uuid_ref, loc)

        for nombre, vol in (("T-01", "12,5"), ("T-02", "20")):
            r = self._post("/marketplace/my-facilities/pond/create", token, facility_ref=fac.uuid_ref,
                           name=nombre, pond_type="tank", capacity_mode="volume", manual_volume_m3=vol)
            self.assertEqual(r.status_code, 303)
            self.assertIn("saved=fac_pond", r.headers["Location"])
        tanques = self.Pond.search([("facility_id", "=", fac.id)], order="name")
        self.assertEqual(tanques.mapped("name"), ["T-01", "T-02"])
        self.assertEqual(tanques.mapped("volume_m3"), [12.5, 20.0])

        html = self.url_open("/marketplace/my-account?saved=fac_pond").text
        self.assertIn("Lab Norte", html)
        self.assertIn("Agregar tanque", html)
        self.assertIn("2 tanques", html)
        self.assertIn("32.5 m³", html)
        self.assertIn("Tanque guardado.", html)

        # Editar instalación (provincia incluida) y tanque.
        r = self._post("/marketplace/my-facilities/facility/%s/update" % fac.uuid_ref, token,
                       name="Lab Norte 2", code="LN-1", facility_type="laboratory",
                       province="Guayas", city="Playas", address="")
        self.assertEqual(r.status_code, 303)
        fac.invalidate_recordset()
        self.assertEqual((fac.name, fac.province, fac.city), ("Lab Norte 2", "Guayas", "Playas"))
        t1 = tanques[0]
        r = self._post("/marketplace/my-facilities/pond/%s/update" % t1.uuid_ref, token,
                       facility_ref=fac.uuid_ref, name="T-01b", pond_type="raceway",
                       capacity_mode="dimensions", length_m="10", width_m="2", depth_m="1")
        self.assertEqual(r.status_code, 303)
        t1.invalidate_recordset()
        self.assertEqual((t1.name, t1.pond_type, t1.volume_m3), ("T-01b", "raceway", 20.0))

        # Archivar: el tanque, y después la instalación con lo que le queda.
        r = self._post("/marketplace/my-facilities/pond/%s/archive" % t1.uuid_ref, token)
        self.assertIn("saved=fac_pond_arch", r.headers["Location"])
        t1.invalidate_recordset()
        self.assertFalse(t1.active)
        r = self._post("/marketplace/my-facilities/facility/%s/archive" % fac.uuid_ref, token)
        self.assertIn("saved=fac_facility_arch", r.headers["Location"])
        self.env.invalidate_all()
        self.assertFalse(fac.active)
        self.assertFalse(tanques[1].active)
        html = self.url_open("/marketplace/my-account").text
        self.assertNotIn("Lab Norte 2", html)

    def test_validacion_previa_sin_error_500(self):
        html = self._pagina(self.s["u_lab"].login)
        token = csrf_de(html)
        r = self._post("/marketplace/my-facilities/facility/create", token, name="X", facility_type="castillo")
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=validation", r.headers["Location"])
        self.assertFalse(self.Fac.search([("partner_id", "=", self.lab.id), ("name", "=", "X")]))
        self._post("/marketplace/my-facilities/facility/create", token, name="A", code="DUP")
        r = self._post("/marketplace/my-facilities/facility/create", token, name="B", code="DUP")
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=validation", r.headers["Location"])
        fac = self.Fac.search([("partner_id", "=", self.lab.id), ("code", "=", "DUP")])
        self.assertEqual(len(fac), 1)
        r = self._post("/marketplace/my-facilities/pond/create", token, facility_ref=fac.uuid_ref,
                       name="P", pond_type="tank", depth_m="999")
        self.assertIn("error=validation", r.headers["Location"])
        r = self._post("/marketplace/my-facilities/pond/create", token, facility_ref=fac.uuid_ref,
                       name="P", pond_type="submarino")
        self.assertIn("error=validation", r.headers["Location"])
        self.assertFalse(self.Pond.search([("facility_id", "=", fac.id)]))
        # El mensaje se ve en Mi cuenta (texto del servidor, no de la URL).
        self.assertIn("no es válido", self.url_open(r.headers["Location"]).text + self.url_open(
            self._post("/marketplace/my-facilities/pond/create", token, facility_ref=fac.uuid_ref,
                       name="P", depth_m="-1").headers["Location"]).text)
        # Nombre vacío: aviso en Mi cuenta.
        r = self._post("/marketplace/my-facilities/facility/create", token, name="  ")
        self.assertIn("error=validation", r.headers["Location"])
        self.assertIn("El nombre es obligatorio.", self.url_open(r.headers["Location"]).text)

    def test_subcuenta_sin_vender_solo_lectura(self):
        fac = self.Fac.create({"partner_id": self.lab.id, "name": "Lab Sur", "facility_type": "laboratory"})
        pond = self.Pond.create({"partner_id": self.lab.id, "facility_id": fac.id, "name": "TQ-9",
                                 "pond_type": "tank", "capacity_mode": "volume", "manual_volume_m3": 9})
        lector = subusuario(self.env, self.lab, "lector.mci", {"laboratorio": {"view", "buy"}})
        html = self._pagina(lector.login)
        self.assertIn("Lab Sur", html)
        self.assertIn("TQ-9", html)
        self.assertIn("Solo lectura", html)
        self.assertNotIn('id="maFacModal"', html)
        self.assertNotIn("ma-arch-btn", html)
        token = csrf_de(html)
        for ruta, datos in (
                ("/marketplace/my-facilities/facility/create", {"name": "Nueva"}),
                ("/marketplace/my-facilities/facility/%s/update" % fac.uuid_ref, {"name": "Cambio"}),
                ("/marketplace/my-facilities/facility/%s/archive" % fac.uuid_ref, {}),
                ("/marketplace/my-facilities/pond/create", {"name": "P", "facility_ref": fac.uuid_ref}),
                ("/marketplace/my-facilities/pond/%s/update" % pond.uuid_ref, {"name": "X"}),
                ("/marketplace/my-facilities/pond/%s/archive" % pond.uuid_ref, {}),
                ("/marketplace/my-facilities/pond/%s/delete" % pond.uuid_ref, {})):
            r = self._post(ruta, token, **datos)
            self.assertEqual(r.status_code, 403, ruta)
        self.env.invalidate_all()
        self.assertTrue(fac.active and pond.active)
        self.assertEqual((fac.name, pond.name), ("Lab Sur", "TQ-9"))
        # Con «Vender» sí puede.
        vendedor = subusuario(self.env, self.lab, "vende.mci", {"laboratorio": {"view", "sell"}})
        html = self._pagina(vendedor.login)
        self.assertNotIn("Solo lectura", html)
        self.assertIn('id="maFacModal"', html)
        r = self._post("/marketplace/my-facilities/pond/create", csrf_de(html), facility_ref=fac.uuid_ref,
                       name="TQ-10", pond_type="tank")
        self.assertEqual(r.status_code, 303)
        nuevo = self.Pond.search([("name", "=", "TQ-10")])
        self.assertEqual(nuevo.partner_id, self.lab, "a nombre de la empresa")

    def test_camaronera_vocabulario_y_pagina_detallada(self):
        html = self._pagina(self.s["u_cam"].login)
        self.assertIn("Aún no registras fincas", html)
        self.assertIn('<option value="farm" selected="selected">Finca</option>', html)
        self.assertIn('<option value="earth" selected="selected">Piscina de tierra</option>', html)
        token = csrf_de(html)
        # Sin next: la página detallada sigue funcionando como antes.
        r = self.url_open("/marketplace/my-facilities/facility/create", allow_redirects=False,
                          data={"csrf_token": token, "name": "Finca Uno", "facility_type": "farm"})
        self.assertIn("/marketplace/my-facilities?saved=facility", r.headers["Location"])
        fac = self.Fac.search([("name", "=", "Finca Uno")])
        r = self.url_open("/marketplace/my-facilities/pond/create", allow_redirects=False, data={
            "csrf_token": token, "facility_ref": fac.uuid_ref, "name": "P1", "pond_type": "earth",
            "capacity_mode": "dimensions", "length_m": "100", "width_m": "50", "depth_m": "1.2"})
        self.assertIn("/marketplace/my-facilities?saved=pond", r.headers["Location"])
        self.assertEqual(self.url_open("/marketplace/my-facilities?facility=%s" % fac.uuid_ref).status_code, 200)
        html = self.url_open("/marketplace/my-account").text
        self.assertIn("0.50 ha", html)
        self.assertIn("Agregar piscina", html)
        self.assertIn("Según tus piscinas registradas", html)

    def test_formularios_de_mi_cuenta_siguen_enviando(self):
        html = self._pagina(self.s["u_lab"].login)
        token = csrf_de(html)
        # Navegación y anclas que usan la barra y otros módulos.
        for ancla in ('id="empresa"', 'id="perfiles"', 'id="certificados"', 'id="mpAccountActions"',
                      'id="contratos"', 'id="seguridad"', 'href="/my/security"', 'href="/my/data"'):
            self.assertIn(ancla, html)
        for campo in ('name="email"', 'name="phone"', 'name="city"', 'name="lab_razon_social"',
                      'name="lab_ubicacion"', 'name="lab_global_gap"', 'name="lab_social_ship_partner"',
                      'action="/marketplace/my-account/save"', 'action="/marketplace/my-certificates/add"'):
            self.assertIn(campo, html)
        r = self.url_open("/marketplace/my-account/save", allow_redirects=False, data={
            "csrf_token": token, "email": "lab.nuevo.mci@prueba.test", "phone": "0999",
            "city": "Salinas", "lab_razon_social": "Lab Nuevo S.A.", "lab_ubicacion": "Salinas",
            "lab_social_ship_partner": "on"})
        if "saved=1" not in r.headers["Location"]:
            import re as _re
            txt = self.url_open(r.headers["Location"]).text
            self.fail(_re.findall(r'pf-a-bad[^>]*>.*?</div>', txt, _re.S))
        self.lab.invalidate_recordset()
        self.assertEqual((self.lab.email, self.lab.city, self.lab.shrimp_razon_social),
                         ("lab.nuevo.mci@prueba.test", "Salinas", "Lab Nuevo S.A."))
        self.assertTrue(self.lab.lab_social_ship_partner)
        cert = self.env["shrimp.certificate"].sudo().search(
            [("active", "=", True), ("role", "in", ["laboratorio", "all"])], limit=1)
        if cert:
            r = self.url_open("/marketplace/my-certificates/add", allow_redirects=False,
                              data={"csrf_token": token, "certificate_id": cert.uuid_ref,
                                    "certificate_number": "MCI-1", "issue_date": "2026-01-10",
                                    "expiry_date": "2028-01-10"},
                              files={"file": ("c.pdf", PDF_MIN, "application/pdf")})
            if "saved=1" not in r.headers["Location"]:
                import re as _re
                txt = self.url_open(r.headers["Location"]).text
                self.fail(_re.findall(r'pf-a-bad[^>]*>.*?</div>', txt, _re.S))
            html = self.url_open("/marketplace/my-account").text
            self.assertIn("MCI-1", html)

    def test_etiqueta_centro_de_maduracion(self):
        sel = dict(self.env["shrimp.partner.facility"]._fields["facility_type"].selection)
        self.assertEqual(sel["hatchery"], "Centro de maduración (naupliero)")
        html = self._pagina(self.s["u_sem"].login)
        self.assertIn("Aún no registras centros de maduración", html)
        self.assertIn('<option value="hatchery" selected="selected">Centro de maduración (naupliero)</option>', html)
