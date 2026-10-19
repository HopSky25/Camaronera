"""Paso 2 del asistente según el tipo de persona (19.0.1.8.0).

Persona natural: cédula o RUC de persona natural, nombres y apellidos (por
defecto los del paso 1), nombre comercial opcional, sin representante legal.
Persona jurídica: RUC de sociedad, razón social, nombre comercial y
representante legal obligatorios; cédula del representante opcional.
El servidor valida por tipo e ignora lo que no aplica.
"""
import re

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from ..controllers.main import validate_ec_identification
from .legal_utils import (cedula_valida, ruc_natural_valido, ruc_sociedad_valido,
                          signup_post, signup_post_juridica)


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto)
    return m.group(1) if m else None


@tagged("post_install", "-at_install")
class TestIdentificacionPorTipo(TransactionCase):

    def test_juridica_solo_ruc_de_sociedad(self):
        ruc = ruc_sociedad_valido()
        self.assertEqual(validate_ec_identification(ruc, "juridica"), ruc)
        with self.assertRaisesRegex(ValidationError, "no con una cédula"):
            validate_ec_identification(cedula_valida(), "juridica")
        with self.assertRaisesRegex(ValidationError, "persona natural"):
            validate_ec_identification(ruc_natural_valido(), "juridica")
        malo = ruc[:9] + str((int(ruc[9]) + 1) % 10) + ruc[10:]
        with self.assertRaisesRegex(ValidationError, "RUC no es válido"):
            validate_ec_identification(malo, "juridica")
        # Sin establecimiento (000) tampoco.
        with self.assertRaises(ValidationError):
            validate_ec_identification(ruc[:10] + "000", "juridica")

    def test_natural_cedula_o_ruc_natural(self):
        ced = cedula_valida()
        self.assertEqual(validate_ec_identification(ced, "natural"), ced)
        self.assertEqual(validate_ec_identification(ced + "001", "natural"), ced + "001")
        with self.assertRaisesRegex(ValidationError, "es de una sociedad"):
            validate_ec_identification(ruc_sociedad_valido(), "natural")
        with self.assertRaisesRegex(ValidationError, "no son válidos"):
            validate_ec_identification(ced[:-1] + str((int(ced[-1]) + 1) % 10), "natural")
        with self.assertRaises(ValidationError):
            validate_ec_identification(ced + "000", "natural")
        with self.assertRaisesRegex(ValidationError, "solo llevan números"):
            validate_ec_identification("09ABC", "natural")


@tagged("post_install", "-at_install")
class TestRegistroTipoPersona(HttpCase):

    def setUp(self):
        super().setUp()
        # Varias altas por test desde la misma IP: sin límite.
        self.env["ir.config_parameter"].sudo().set_param(
            "shrimp_user_registry.signup_rate_limit", "0")

    def _token(self):
        token = _csrf(self.url_open("/register").text)
        self.assertTrue(token)
        return token

    def _socio(self, correo):
        return self.env["res.partner"].sudo().search([("email", "=", correo)])

    def _post(self, datos):
        return self.url_open("/register/submit", data=datos, allow_redirects=False)

    # ------------------------------------------------------------ natural
    def test_alta_persona_natural(self):
        correo = "nat.tipo@prueba.test"
        datos = signup_post(self.env, ["camaronera"], correo, csrf_token=self._token(),
                            shrimp_contact_name="María  Pérez Loor",
                            shrimp_nombre_comercial="Camaronera Doña María",
                            # Sin JS llegan también los campos de la jurídica:
                            # se ignoran.
                            shrimp_razon_social="No aplica S.A.",
                            shrimp_representante="Otra persona",
                            shrimp_representante_cedula="0000000000")
        datos.pop("name")
        r = self._post(datos)
        self.assertIn(r.status_code, (302, 303), r.text[:2000] if r.status_code == 200 else "")
        socio = self._socio(correo)
        self.assertTrue(socio)
        self.assertEqual(socio.shrimp_person_type, "natural")
        self.assertFalse(socio.is_company)
        self.assertEqual(socio.company_type, "person")
        self.assertEqual(socio.name, "María Pérez Loor", "nombre legal = nombres y apellidos")
        self.assertEqual(socio.shrimp_razon_social, "María Pérez Loor")
        self.assertEqual(socio.shrimp_nombre_comercial, "Camaronera Doña María")
        # Se representa a sí misma; lo que llegó para la jurídica no se usa.
        self.assertEqual(socio.sudo().shrimp_representante, "María Pérez Loor")
        self.assertFalse(socio.sudo().shrimp_representante_cedula)
        self.assertEqual(socio.vat, datos["vat_or_id"])

    def test_natural_nombres_editados_y_comercial_opcional(self):
        correo = "nat.edit@prueba.test"
        datos = signup_post(self.env, ["semillero"], correo, csrf_token=self._token(),
                            shrimp_nombres_apellidos="José Luis Andrade Vera",
                            shrimp_nombre_comercial="", vat_or_id=cedula_valida())
        datos.pop("name")
        self._post(datos)
        socio = self._socio(correo)
        self.assertEqual(socio.name, "José Luis Andrade Vera")
        self.assertFalse(socio.shrimp_nombre_comercial)

    def test_dos_personas_naturales_con_el_mismo_nombre(self):
        for i, correo in enumerate(("homonimo1@prueba.test", "homonimo2@prueba.test")):
            datos = signup_post(self.env, ["semillero"], correo, csrf_token=self._token(),
                                shrimp_nombres_apellidos="Juan Carlos Pérez")
            datos.pop("name")
            self._post(datos)
            self.assertEqual(self._socio(correo).name, "Juan Carlos Pérez", i)

    def test_natural_rechaza_ruc_de_sociedad(self):
        correo = "nat.malo@prueba.test"
        r = self._post(signup_post(self.env, ["semillero"], correo, csrf_token=self._token(),
                                   vat=ruc_sociedad_valido()))
        self.assertEqual(r.status_code, 200)
        self.assertIn("es de una sociedad", r.text)
        self.assertFalse(self._socio(correo))

    # ------------------------------------------------------------ jurídica
    def test_alta_persona_juridica(self):
        correo = "jur.tipo@prueba.test"
        ced_rep = cedula_valida()
        datos = signup_post_juridica(self.env, ["camaronera"], correo, csrf_token=self._token(),
                                     shrimp_representante_cedula=ced_rep,
                                     shrimp_nombres_apellidos="No aplica")
        r = self._post(datos)
        self.assertIn(r.status_code, (302, 303), r.text[:2000] if r.status_code == 200 else "")
        socio = self._socio(correo)
        self.assertEqual(socio.shrimp_person_type, "juridica")
        self.assertTrue(socio.is_company)
        self.assertEqual(socio.company_type, "company")
        self.assertEqual(socio.name, "Sociedad jur.tipo S.A.", "nombre legal = razón social")
        self.assertEqual(socio.shrimp_razon_social, "Sociedad jur.tipo S.A.")
        self.assertEqual(socio.shrimp_nombre_comercial, "Comercial jur.tipo")
        self.assertEqual(socio.sudo().shrimp_representante, "Representante jur.tipo")
        self.assertEqual(socio.sudo().shrimp_representante_cedula, ced_rep)
        self.assertEqual(socio.vat, datos["vat_or_id"])
        self.assertEqual(socio.shrimp_contact_name, "Persona jur.tipo")

    def test_juridica_rechaza_cedula_y_ruc_natural(self):
        for i, vat in enumerate((cedula_valida(), ruc_natural_valido())):
            correo = "jur.vat%s@prueba.test" % i
            r = self._post(signup_post_juridica(self.env, ["semillero"], correo,
                                                csrf_token=self._token(), vat_or_id=vat))
            self.assertEqual(r.status_code, 200)
            self.assertRegex(r.text, "no con una cédula|es de una persona natural")
            self.assertFalse(self._socio(correo))

    def test_juridica_exige_sus_campos(self):
        casos = {
            "shrimp_razon_social": "razón social es obligatoria",
            "shrimp_nombre_comercial": "nombre comercial es obligatorio",
            "shrimp_representante": "representante legal es obligatorio",
        }
        for i, (campo, mensaje) in enumerate(casos.items()):
            with self.subTest(campo=campo):
                correo = "jur.falta%s@prueba.test" % i
                r = self._post(signup_post_juridica(self.env, ["semillero"], correo,
                                                    csrf_token=self._token(), **{campo: ""}))
                self.assertIn(mensaje, r.text)
                self.assertFalse(self._socio(correo))
        correo = "jur.cedrep@prueba.test"
        ced = cedula_valida()
        mala = ced[:-1] + str((int(ced[-1]) + 1) % 10)
        r = self._post(signup_post_juridica(self.env, ["semillero"], correo, csrf_token=self._token(),
                                            shrimp_representante_cedula=mala))
        self.assertIn("cédula del representante legal no es válida", r.text)
        self.assertFalse(self._socio(correo))

    def test_juridica_razon_social_repetida(self):
        token = self._token()
        self._post(signup_post_juridica(self.env, ["semillero"], "jur.rep1@prueba.test",
                                        csrf_token=token, shrimp_razon_social="Repetida S.A."))
        self.assertTrue(self._socio("jur.rep1@prueba.test"))
        r = self._post(signup_post_juridica(self.env, ["semillero"], "jur.rep2@prueba.test",
                                            csrf_token=token, shrimp_razon_social="Repetida S.A."))
        self.assertIn("no se pueden usar para el registro", r.text)

    def test_sin_tipo_de_persona(self):
        correo = "sin.tipo@prueba.test"
        datos = signup_post(self.env, ["semillero"], correo, csrf_token=self._token())
        datos.pop("shrimp_person_type")
        r = self._post(datos)
        self.assertIn("persona natural o jurídica", r.text)
        self.assertFalse(self._socio(correo))

    # ------------------------------------------------------------ formulario
    def test_formulario_paso2_por_tipo(self):
        html = self.url_open("/register").text
        # Sin tipo elegido por defecto.
        self.assertNotRegex(html, r'name="shrimp_person_type"[^>]*checked')
        self.assertRegex(html, r'data-pt="natural"[\s\S]{0,400}name="shrimp_nombres_apellidos"')
        self.assertRegex(html, r'data-pt="juridica"[\s\S]{0,400}name="shrimp_razon_social"')
        self.assertRegex(html, r'data-pt="juridica"[\s\S]{0,400}name="shrimp_representante"')
        self.assertIn('name="shrimp_representante_cedula"', html)
        self.assertIn('name="shrimp_nombre_comercial"', html)

    def test_paso3_sin_certificados_como_casillas(self):
        """Los certificados (GlobalG.A.P., Social Ship Partner, BAP...) se
        adjuntan en el paso 4; los bloques del paso 3 no los traen como
        casillas."""
        html = self.url_open("/register").text
        m = re.search(r'id="shrimpProfileSections"([\s\S]*?)id="shrimp_prof_sections_end"', html)
        self.assertTrue(m)
        bloques = m.group(1)
        for nombre in ("lab_global_gap", "lab_social_ship_partner", "emp_cert_bap",
                       "emp_cert_asc", "emp_cert_haccp", "GlobalG.A.P.", "Social Ship"):
            self.assertNotIn(nombre, bloques)
        lab = re.search(r'id="section_laboratorio"([\s\S]*?)</div>\s*</div>\s*</div>', html)
        if lab:
            self.assertNotIn('type="checkbox"', lab.group(1))

    def test_globalgap_no_se_ofrece_al_laboratorio(self):
        r = self.url_open("/register/certificates", data='{"jsonrpc": "2.0", "method": "call", '
                          '"params": {"role": "laboratorio"}, "id": 1}',
                          headers={"Content-Type": "application/json"})
        nombres = [c["name"] for c in (r.json().get("result") or [])]
        self.assertTrue(nombres)
        self.assertNotIn("GlobalG.A.P.", nombres)
        self.assertFalse([n for n in nombres if "social ship" in n.lower()])
