"""Matriz de verificación (shrimp.verification.rule).

Reglas del cliente (2026-10), sembradas en data/verification_rule_data.xml:
  - naupliero → laboratorio ......... No aplica
  - laboratorio → camaronera ........ No aplica
  - naupliero → naupliero ........... No aplica (mismo nivel)
  - laboratorio → laboratorio ....... No aplica (mismo nivel)
  - camaronera → camaronera ......... Obligatoria (como antes)
  (camaronera → empacadora, Obligatoria, la siembra shrimp_packer.)

Se comprueba la página de compra, el POST manipulado a mano, el modelo,
la precedencia de reglas y que el cambio del administrador surte efecto.
"""
from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, tagged

from odoo.addons.shrimp_marketplace.tests.common import CLAVE, csrf_de, producto

from .common_seguridad import montar_verificacion


def reactivar_matriz(env):
    env["shrimp.verification.rule"].sudo().with_context(active_test=False).search(
        []).write({"active": True})


@tagged("post_install", "-at_install")
class TestMatrizVerificacion(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # montar_verificacion archiva la matriz (para sus propios tests); aquí
        # se vuelve a activar: lo que se prueba es justamente la matriz.
        cls.s, _v = montar_verificacion(cls.env, "mtz")
        reactivar_matriz(cls.env)
        s = cls.s
        Socio = cls.env["res.partner"]
        portal = cls.env.ref("base.group_portal")

        def socio(clave, nombre, tipo, ruc, **extra):
            s[clave] = Socio.create(dict({
                "name": nombre, "is_company": True, "email": "%s.mtz@prueba.test" % clave,
                "vat_or_id": ruc, "shrimp_user_type": tipo}, **extra))
            s["u_" + clave] = cls.env["res.users"].create({
                "name": nombre, "login": "%s.mtz" % clave, "partner_id": s[clave].id,
                "password": CLAVE, "group_ids": [(6, 0, [portal.id])]})

        socio("sem2", "Naupliero dos mtz", "semillero", "0911199001001")
        socio("cam2", "Camaronera dos mtz", "camaronera", "0911199002001",
              farm_razon_social="Cam2 S.A.", farm_representante="Rep",
              farm_telefono="04-1234567", farm_ubicacion="Guayas")

        cls.nauplio = producto(cls.env, s["sem"], nombre="Nauplio mtz",
                               etapa="shrimp_marketplace.shrimp_stage_nauplio")
        cls.larva = producto(cls.env, s["lab"], nombre="Larva mtz")
        cls.larva_lab2 = producto(cls.env, s["lab2"], nombre="Larva lab2 mtz")
        cls.juvenil = producto(cls.env, s["cam"], nombre="Juvenil mtz",
                               etapa="shrimp_marketplace.shrimp_stage_juvenil")
        cls.Rule = cls.env["shrimp.verification.rule"]

    # ------------------------------------------------------------------
    def _combos_no_aplica(self):
        s = self.s
        return [
            # (producto, comprador, usuario)
            (self.nauplio, s["lab"], s["u_lab"]),      # naupliero → laboratorio
            (self.larva, s["cam"], s["u_cam"]),        # laboratorio → camaronera
            (self.nauplio, s["sem2"], s["u_sem2"]),    # naupliero → naupliero
            (self.larva_lab2, s["lab"], s["u_lab"]),   # laboratorio → laboratorio
        ]

    def test_politicas_sembradas(self):
        for prod, comprador, _u in self._combos_no_aplica():
            self.assertEqual(prod._shrimp_verification_policy(comprador), "none",
                             "%s → %s" % (prod.name, comprador.name))
            self.assertFalse(prod._shrimp_requires_verification(comprador))
        self.assertEqual(self.juvenil._shrimp_verification_policy(self.s["cam2"]), "mandatory")
        self.assertTrue(self.juvenil._shrimp_requires_verification(self.s["cam2"]))
        # Sin comprador: la general del lote (larva no exige; adulto sí).
        self.assertFalse(self.larva.requires_verification)
        self.assertTrue(self.juvenil.requires_verification)

    def test_no_aplica_pagina_y_post_manipulado(self):
        V = self.env["shrimp.verification"]
        for prod, comprador, usuario in self._combos_no_aplica():
            self.authenticate(usuario.login, CLAVE)
            pagina = self.url_open("/marketplace/buy/%s" % prod.uuid_ref).text
            # Sin sección de verificación: ni modos ni formulario /verify.
            self.assertNotIn('name="verification_mode"', pagina)
            self.assertNotIn("/verify\"", pagina)
            self.assertIn('id="buyForm"', pagina)
            self.assertIn("no lleva verificación en campo", pagina)
            token = csrf_de(pagina)
            antes = V.search_count([("product_id", "=", prod.id)])
            for datos in ({"verification_mode": "declared", "declared_source": "self"},
                          {"verification_mode": "platform",
                           "verifier_ref": self.s["verif"].uuid_ref}):
                r = self.url_open("/marketplace/buy/%s/verify" % prod.uuid_ref,
                                  data=dict(datos, csrf_token=token, qty="2"))
                self.assertIn("error=validation", r.url)
            self.assertEqual(V.search_count([("product_id", "=", prod.id)]), antes,
                             "no debe abrirse ninguna verificación: %s" % prod.name)
            # El modelo también lo rechaza (API, reservas, scripts).
            with self.assertRaises(ValidationError):
                prod.with_context(shrimp_buyer_role=comprador.shrimp_user_type) \
                    .start_verified_purchase(comprador, 1.0, None, mode="declared")
            # La compra directa sí funciona y no lleva verificación.
            r = self.url_open("/marketplace/buy/%s/confirm" % prod.uuid_ref,
                              data={"csrf_token": token, "qty": "1"})
            self.assertIn("/marketplace/thanks/", r.url, prod.name)
            tx = self.env["shrimp.transaction"].search(
                [("product_id", "=", prod.id), ("buyer_partner_id", "=", comprador.id)],
                order="id desc", limit=1)
            self.assertEqual(tx.state, "confirmed")
            self.assertFalse(tx.needs_verification)
            self.assertFalse(tx.verification_ids)

    def test_obligatoria_sigue_forzando(self):
        self.authenticate(self.s["u_cam2"].login, CLAVE)
        pagina = self.url_open("/marketplace/buy/%s" % self.juvenil.uuid_ref).text
        self.assertNotIn('id="buyForm"', pagina)
        self.assertIn('name="verification_mode" value="platform"', pagina)
        self.assertIn('name="verification_mode" value="declared"', pagina)
        # Sin la tarjeta de compra directa (valor vacío) cuando es obligatoria.
        self.assertNotIn('name="verification_mode" value=""', pagina)
        token = csrf_de(pagina)
        r = self.url_open("/marketplace/buy/%s/confirm" % self.juvenil.uuid_ref,
                          data={"csrf_token": token, "qty": "1"})
        self.assertIn("error=validation", r.url)
        with self.assertRaises(ValidationError):
            self.juvenil.execute_purchase_flow(self.s["cam2"], 1.0)

    def test_admin_cambia_la_matriz(self):
        regla = self.env.ref("shrimp_verification.verification_rule_laboratorio_camaronera")
        # Opcional: vuelven las dos formas de compra, con la tarjeta directa.
        regla.policy = "optional"
        self.assertEqual(self.larva._shrimp_verification_policy(self.s["cam"]), "optional")
        self.authenticate(self.s["u_cam"].login, CLAVE)
        pagina = self.url_open("/marketplace/buy/%s" % self.larva.uuid_ref).text
        self.assertIn('id="buyForm"', pagina)
        self.assertIn('name="verification_mode" value="declared"', pagina)
        self.assertIn('name="verification_mode" value=""', pagina)
        res = self.larva.start_verified_purchase(self.s["cam"], 2.0, None, mode="declared")
        self.assertEqual(res["transaction"].state, "pending_verification")
        # Obligatoria: la compra directa se rechaza.
        regla.policy = "mandatory"
        with self.assertRaises(ValidationError):
            self.larva.execute_purchase_flow(self.s["cam"], 1.0)
        pagina = self.url_open("/marketplace/buy/%s" % self.larva.uuid_ref).text
        self.assertNotIn('id="buyForm"', pagina)
        # Una regla general (cualquier comprador) mueve el campo almacenado.
        self.assertFalse(self.larva.requires_verification)
        general = self.Rule.create({"seller_role": "laboratorio", "policy": "mandatory"})
        self.assertTrue(self.larva.requires_verification)
        general.unlink()
        self.assertFalse(self.larva.requires_verification)
        # Sin ninguna regla: la lógica de siempre.
        self.Rule.search([]).write({"active": False})
        self.assertEqual(self.larva._shrimp_verification_policy(self.s["cam"]), "optional")
        self.assertEqual(self.juvenil._shrimp_verification_policy(self.s["cam2"]), "mandatory")

    def test_precedencia_mas_especifica(self):
        # General «obligatoria» para el laboratorio, pero la pareja concreta
        # laboratorio → camaronera sigue «no aplica».
        self.Rule.create({"seller_role": "laboratorio", "policy": "mandatory"})
        self.assertEqual(self.larva._shrimp_verification_policy(self.s["cam"]), "none")
        # Y una por tipo de producto gana a la de la pareja.
        self.Rule.create({"seller_role": "laboratorio", "buyer_role": "camaronera",
                          "product_tipo": "larva", "policy": "optional"})
        self.assertEqual(self.larva._shrimp_verification_policy(self.s["cam"]), "optional")
        # No se repite la misma combinación (comprador vacío incluido).
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.Rule.create({"seller_role": "laboratorio", "policy": "none"})

    def test_ficha_lee_la_misma_matriz(self):
        regla = self.env.ref("shrimp_verification.verification_rule_laboratorio_camaronera")
        regla.policy = "mandatory"
        self.authenticate(self.s["u_cam"].login, CLAVE)
        ficha = self.url_open("/marketplace/product/%s" % self.larva.uuid_ref).text
        self.assertIn("verificación en campo</b> obligatoria", ficha)
        regla.policy = "none"
        ficha = self.url_open("/marketplace/product/%s" % self.larva.uuid_ref).text
        self.assertNotIn("verificación en campo</b> obligatoria", ficha)
