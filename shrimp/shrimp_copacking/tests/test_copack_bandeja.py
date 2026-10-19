from datetime import date, timedelta

from odoo.tests import tagged

from .common import CopackCommon


@tagged("post_install", "-at_install")
class TestCopackBandeja(CopackCommon):
    """Lo que ve el maquilador por la regla rule_copack_request_bandeja.

    Defectos que encontró la revisión de la app móvil:
    - GET /copack/requests?state=assigned (o done) salía vacío aunque la
      solicitud se le hubiera adjudicado: la regla solo abría las publicadas.
    - Una cuenta con el perfil maquilador APROBADO pero navegando con otro
      perfil veía la bandeja vacía aunque mandara X-Shrimp-Role: maquilador,
      porque la regla miraba solo el perfil ACTIVO.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Socio = cls.env["res.partner"]
        portal = cls.env.ref("base.group_portal")

        def usuario(socio, login):
            return cls.env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "group_ids": [(6, 0, [portal.id])],
            })

        datos_planta = {
            "pack_ubicacion": "Durán", "pack_capacidad_lb_semana": 100000,
            "pack_lote_minimo_lb": 2000,
        }
        # Otra planta: no debe ver lo que se le adjudicó a la primera.
        cls.maq2 = Socio.create(dict(datos_planta, **{
            "name": "Otra maquiladora", "is_company": True,
            "email": "maq2@prueba.test", "vat_or_id": "0955500004001",
            "shrimp_user_type": "maquilador", "shrimp_account_state": "approved",
            "pack_razon_social": "Otra maquiladora S.A.",
            "pack_codigo_establecimiento": "99322",
        }))
        cls.u_maq2 = usuario(cls.maq2, "maq2@prueba.test")

        # Cuenta multiperfil: navega como camaronera y tiene el perfil
        # maquilador aprobado.
        cls.multi = Socio.create(dict(datos_planta, **{
            "name": "Grupo multiperfil", "is_company": True,
            "email": "multi@prueba.test", "vat_or_id": "0955500005001",
            "shrimp_user_type": "camaronera", "shrimp_account_state": "approved",
            "farm_razon_social": "Grupo multiperfil S.A.",
            "farm_representante": "Rep", "farm_telefono": "04-2222222",
            "farm_ubicacion": "El Oro",
            "pack_razon_social": "Grupo multiperfil S.A.",
            "pack_codigo_establecimiento": "99323",
        }))
        cls.rol_maq_multi = cls.env["shrimp.partner.role"].create({
            "partner_id": cls.multi.id, "role": "maquilador", "state": "approved"})
        cls.u_multi = usuario(cls.multi, "multi@prueba.test")

    # ------------------------------------------------------------------
    def visibles(self, user, state=None):
        dominio = [("state", "=", state)] if state else []
        return self.env["shrimp.copack.request"].with_user(user).search(dominio)

    def ofertar(self, sol, planta, tarifa=0.2):
        return self.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": planta.id,
            "rate_per_lb": tarifa, "capacity_lb": sol.quantity_lb,
            "available_from": date.today(),
            "available_to": date.today() + timedelta(days=6),
        })

    # ---------------- A: lo adjudicado sigue siendo visible ----------------
    def test_ve_la_solicitud_que_se_le_adjudico(self):
        sol = self.solicitud(dirigida=False)
        self.adjudicar(sol)
        self.assertEqual(sol.state, "assigned")
        self.assertIn(sol, self.visibles(self.u_maq, "assigned"))
        # Y cerrada.
        sol.write({"state": "done"})
        self.assertIn(sol, self.visibles(self.u_maq, "done"))
        # Leerla (lo que hace el serializador de la API) no da AccessError.
        self.assertTrue(sol.with_user(self.u_maq).read(["name", "state", "order_id"]))

    def test_ve_la_solicitud_en_la_que_oferto(self):
        sol = self.solicitud(dirigida=False)
        self.ofertar(sol, self.maq2)
        self.adjudicar(sol)  # gana self.maq
        # Ya no está publicada, pero maq2 ofertó en ella.
        self.assertIn(sol, self.visibles(self.u_maq2, "assigned"))

    def test_no_ve_lo_adjudicado_a_otra_planta(self):
        sol = self.solicitud(dirigida=False)
        self.adjudicar(sol)
        self.assertNotIn(sol, self.visibles(self.u_maq2))
        self.assertNotIn(sol, self.visibles(self.u_multi))
        # Ni el cliente ajeno.
        self.assertNotIn(sol, self.visibles(self.u_otro))

    def test_no_ve_las_publicadas_dirigidas_a_otra_planta(self):
        dirigida = self.solicitud(dirigida=True)  # a self.maq
        self.assertIn(dirigida, self.visibles(self.u_maq, "published"))
        self.assertNotIn(dirigida, self.visibles(self.u_maq2))

    # ------- B: la bandeja de publicadas va con el perfil ACTIVO (CR-12) -------
    def test_con_otro_perfil_activo_solo_ve_lo_suyo(self):
        """Cuenta multiperfil navegando como camaronera: no ve las publicadas
        de la bandeja, pero sí lo que se le adjudicó. Al activar el perfil
        maquilador (selector del portal o POST /me/active-role de la app),
        las ve."""
        self.assertEqual(self.multi.shrimp_user_type, "camaronera")
        abierta = self.solicitud(dirigida=False)
        self.assertNotIn(abierta, self.visibles(self.u_multi, "published"))
        oferta = self.ofertar(abierta, self.multi)
        oferta.action_accept(actor=abierta.client_partner_id)
        self.assertIn(abierta, self.visibles(self.u_multi, "assigned"))
        otra = self.solicitud(dirigida=False)
        self.u_multi.shrimp_active_role = "maquilador"
        self.assertEqual(self.u_multi.shrimp_active_role, "maquilador")
        self.assertIn(otra, self.visibles(self.u_multi, "published"))

    def test_perfil_de_la_peticion_abre_la_bandeja_sin_cambiar_el_activo(self):
        """La API pasa el perfil de la petición (X-Shrimp-Role) en el contexto
        (shrimp_acting_role): la app trabaja como maquilador sin mover el
        perfil activo del portal."""
        abierta = self.solicitud(dirigida=False)
        Sol = self.env["shrimp.copack.request"].with_user(self.u_multi)
        self.assertNotIn(abierta, Sol.search([("state", "=", "published")]))
        como_maq = Sol.with_context(shrimp_acting_role="maquilador")
        self.assertIn(abierta, como_maq.search([("state", "=", "published")]))
        self.assertEqual(self.u_multi.shrimp_active_role, "camaronera", "el activo no cambia")
        # Y la caché de reglas no mezcla una petición con la otra.
        self.assertNotIn(abierta, Sol.search([("state", "=", "published")]))

    def test_perfil_de_la_peticion_no_aprobado_no_abre_nada(self):
        """El contexto puede llegar de fuera (call_kw): solo vale un perfil
        aprobado de la cuenta."""
        abierta = self.solicitud(dirigida=False)
        Sol = self.env["shrimp.copack.request"].with_user(self.u_otro)
        self.assertNotIn(abierta, Sol.with_context(shrimp_acting_role="maquilador").search([]))
        self.rol_maq_multi.write({"state": "pending"})
        Sol = self.env["shrimp.copack.request"].with_user(self.u_multi)
        self.assertNotIn(abierta, Sol.with_context(shrimp_acting_role="maquilador").search([]))

    def test_perfil_maquilador_pendiente_no_abre_la_bandeja(self):
        self.rol_maq_multi.write({"state": "pending"})
        abierta = self.solicitud(dirigida=False)
        self.assertNotIn(abierta, self.visibles(self.u_multi))

    def test_cambiar_de_perfil_abre_la_bandeja_sin_vaciar_cache(self):
        """ir.rule cachea el dominio por usuario: la clave de caché incluye el
        perfil activo del usuario, si no seguiría viendo la bandeja vacía."""
        abierta = self.solicitud(dirigida=False)
        self.assertNotIn(abierta, self.visibles(self.u_multi))
        self.u_multi.shrimp_active_role = "maquilador"
        self.assertIn(abierta, self.visibles(self.u_multi))
        self.u_multi.shrimp_active_role = "camaronera"
        self.assertNotIn(abierta, self.visibles(self.u_multi))

    def test_sin_perfil_maquilador_no_ve_publicadas_ajenas(self):
        abierta = self.solicitud(dirigida=False)
        self.assertNotIn(abierta, self.visibles(self.u_otro))
        self.assertIn(abierta, self.visibles(self.u_cli))  # la suya

    def test_la_regla_sigue_siendo_de_solo_lectura(self):
        regla = self.env.ref("shrimp_copacking.rule_copack_request_bandeja")
        self.assertTrue(regla.perm_read)
        self.assertFalse(regla.perm_write or regla.perm_create or regla.perm_unlink)
