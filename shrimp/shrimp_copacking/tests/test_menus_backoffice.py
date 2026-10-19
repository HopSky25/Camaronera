"""Backoffice en tres aplicaciones (CamaronMarket, Verificador, Copacking).

- Cada app es una raíz propia, con icono, declarada por su módulo.
- Cada app la ve su grupo: un usuario del Verificador no ve las otras dos, y un
  empleado interno sin roles de la plataforma no ve ninguna.
- Las tablas internas no salen en ninguna de las tres apps: solo en
  Ajustes › Técnico › CamaronMarket, en modo desarrollador y para el
  administrador del sistema.
"""
from odoo.tests import TransactionCase, new_test_user, tagged

RAICES = {
    "mk": "shrimp_marketplace.menu_shrimp_root",
    "ver": "shrimp_verification.menu_shrimp_verification_root",
    "cop": "shrimp_copacking.menu_copack_root",
}

# Modelos internos: los llena el código, se ven dentro de su documento o son
# colas/bitácoras. No deben tener menú en ninguna de las tres apps.
INTERNOS = {
    "shrimp.rate.limit", "shrimp.api.rate.bucket", "shrimp.api.idempotency",
    "shrimp.webhook.delivery", "shrimp.api.scope", "shrimp.stock.move",
    "shrimp.lot.allocation", "shrimp.profile.transfer", "shrimp.signoff.event",
    "shrimp.product.evolution", "shrimp.legal.acceptance", "shrimp.check.request",
    "shrimp.verification.line", "shrimp.verification.count",
    "shrimp.verification.acceptance", "shrimp.copack.acceptance",
    "shrimp.copack.offer", "shrimp.copack.tariff.line", "shrimp.price.list.line",
    "shrimp.price.list.bonus", "shrimp.lot.alert", "shrimp.harvest.confirmation",
    "shrimp.export.line", "shrimp.account.member.permission",
}


@tagged("post_install", "-at_install")
class TestMenusBackoffice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raices = {k: cls.env.ref(x) for k, x in RAICES.items()}
        cls.tecnico = cls.env.ref("shrimp_user_registry.menu_shrimp_technical_root")

    def _visibles(self, user, debug=False):
        Menu = self.env["ir.ui.menu"].with_user(user)
        return Menu.browse(Menu._visible_menu_ids(debug=debug))

    def _raices_visibles(self, user, debug=False):
        vis = self._visibles(user, debug)
        return {k for k, m in self.raices.items() if m in vis}

    def test_tres_raices_con_icono(self):
        for clave, menu in self.raices.items():
            self.assertFalse(menu.parent_id, clave)
            self.assertTrue(menu.web_icon, clave)
        self.assertEqual(
            {m.name for m in self.raices.values()},
            {"CamaronMarket", "Verificador", "Copacking"})

    def test_empleado_interno_no_ve_ninguna(self):
        empleado = new_test_user(self.env, login="empleado.menus", groups="base.group_user")
        self.assertFalse(self._raices_visibles(empleado))
        self.assertFalse(self._raices_visibles(empleado, debug=True))

    def test_cada_app_su_grupo(self):
        casos = {
            "mk": "shrimp_marketplace.group_marketplace_user",
            "ver": "shrimp_verification.group_verification_user",
            "cop": "shrimp_copacking.group_copacking_user",
        }
        for clave, grupo in casos.items():
            usuario = new_test_user(self.env, login="menus.%s" % clave,
                                    groups="base.group_user,%s" % grupo)
            self.assertEqual(self._raices_visibles(usuario), {clave}, grupo)
            # La configuración de la app es solo para su administrador.
            vis = self._visibles(usuario)
            config = vis.filtered(lambda m: m.name == "Configuración"
                                  and m.parent_id == self.raices[clave])
            self.assertFalse(config, grupo)

    def test_admin_ve_las_tres_y_tecnico_solo_en_debug(self):
        admin = self.env.ref("base.user_admin")
        self.assertEqual(self._raices_visibles(admin), {"mk", "ver", "cop"})
        self.assertNotIn(self.tecnico, self._visibles(admin))
        self.assertIn(self.tecnico, self._visibles(admin, debug=True))

    def test_operador_no_llega_al_menu_tecnico(self):
        operador = new_test_user(
            self.env, login="menus.operador",
            groups="base.group_user,shrimp_marketplace.group_marketplace_manager,"
                   "shrimp_verification.group_verification_manager,"
                   "shrimp_copacking.group_copacking_manager")
        vis = self._visibles(operador, debug=True)
        hijos = self.env["ir.ui.menu"].search([("parent_id", "=", self.tecnico.id)])
        self.assertTrue(hijos)
        self.assertFalse(hijos & vis)

    def test_tablas_internas_fuera_de_las_apps(self):
        Menu = self.env["ir.ui.menu"]
        # Excepción deliberada: Reportes › Movimientos es el análisis agregado
        # (pivote/gráfico) de los movimientos, para el administrador; la tabla
        # en bruto está en el menú técnico.
        informe = self.env.ref("shrimp_marketplace.menu_report_moves")
        for clave, raiz in self.raices.items():
            menus = Menu.search([("id", "child_of", raiz.id)]) - informe
            modelos = {
                m.action.res_model for m in menus
                if m.action and m.action._name == "ir.actions.act_window"
            }
            self.assertFalse(modelos & INTERNOS, clave)
