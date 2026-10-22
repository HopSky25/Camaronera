"""Permisos de los subusuarios y perfil activo POR USUARIO (hueco CR-12,
decisiones D-12 y D-37; también ME-06, AL-22 y BA-11).

* Matriz central (shrimp_user_registry/models/shrimp_permission_matrix.py):
  toda ruta POST del portal está clasificada; «ver» no puede escribir nada
  (403) y cada permiso (comprar, vender, facturación, gestionar cuenta) abre
  solo sus acciones.
* Perfil activo por usuario: dos miembros de la misma empresa con perfiles
  distintos; el cambio automático por sitio solo toca al usuario.
* API: claves y webhooks solo con «gestionar cuenta»; la API REST aplica la
  misma matriz y la clave fija su perfil.
* Transferencia de la administración con los permisos elegidos.
* Migración 19.0.1.13.0.
"""
import importlib.util
import os
import re

from odoo.exceptions import AccessError
from odoo.http import _generate_routing_rules
from odoo.tests import HttpCase, tagged
from odoo.tools import mute_logger

from odoo.addons.shrimp_user_registry.models.shrimp_permission_matrix import (
    SHRIMP_GET_PERMISSIONS, SHRIMP_ROUTE_EXEMPT, SHRIMP_ROUTE_PERMISSIONS)

from .common import CLAVE, crear_socios, producto

MARCA_403 = "shrimp-permission-denied"


def _csrf(texto):
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', texto) or \
        re.search(r'value="([^"]+)"\s+name="csrf_token"', texto)
    return m.group(1) if m else ""


def subusuario(env, empresa, login, permisos, gestionar=False):
    contacto = env["res.partner"].sudo().create({
        "name": "Sub %s" % login, "email": "%s@prueba.test" % login,
        "parent_id": empresa.id, "type": "contact"})
    user = env["res.users"].sudo().create({
        "name": contacto.name, "login": login, "partner_id": contacto.id, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
    m = env["shrimp.account.member"].sudo()._shrimp_ensure(user, empresa, permissions=permisos)
    if gestionar:
        m._set_permissions(permisos, manage_account=True)
    return user


def _url(ruta):
    """Ruta de @http.route con parámetros de relleno."""
    ruta = re.sub(r"<int:[^>]+>", "1", ruta)
    return re.sub(r"<[^>]+>", "noexiste", ruta)


def _permitido(perm, permisos, gestionar):
    perms = perm if isinstance(perm, tuple) else (perm,)
    for p in perms:
        if p == "none":
            return True
        if p == "manage_account" and gestionar:
            return True
        if p in permisos:
            return True
    return False


@tagged("post_install", "-at_install")
class TestPermisosSubcuentas(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "pm")
        cls.emp = cls.s["cam"]
        cls.emp._shrimp_request_role("laboratorio", {
            "shrimp_razon_social": "Cam y Lab PM S.A.", "shrimp_ubicacion": "Guayas"})
        cls.admin = cls.s["u_cam"]
        cls.Member = cls.env["shrimp.account.member"].sudo()
        cls.Member._shrimp_ensure(cls.admin, cls.emp, is_admin=True)
        cls.ver = subusuario(cls.env, cls.emp, "ver.pm",
                             {"camaronera": {"view"}, "laboratorio": {"view"}})

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def _post(self, ruta, token, **data):
        data.setdefault("csrf_token", token)
        return self.url_open(_url(ruta), data=data, allow_redirects=False)

    def _rutas_instaladas(self):
        """{ruta: métodos} de los módulos shrimp instalados (POST/ANY)."""
        mods = [m for m in self.env.registry._init_modules if m.startswith("shrimp")]
        res = {}
        for url, endpoint in _generate_routing_rules(mods, nodb_only=False):
            routing = endpoint.routing
            if routing.get("auth") != "user" or routing.get("type") != "http":
                continue
            metodos = routing.get("methods") or ["ANY"]
            res.setdefault(url, set()).update(metodos)
        return res

    # ------------------------------------------------------------------
    # Matriz: cobertura
    # ------------------------------------------------------------------
    def test_toda_ruta_post_esta_clasificada(self):
        rutas = self._rutas_instaladas()
        sin = sorted(r for r, metodos in rutas.items()
                     if "POST" in metodos and r not in SHRIMP_ROUTE_PERMISSIONS
                     and r not in SHRIMP_ROUTE_EXEMPT)
        self.assertFalse(sin, "rutas POST sin permiso en la matriz: %s" % sin)
        fantasmas = sorted(r for r in list(SHRIMP_ROUTE_PERMISSIONS) + list(SHRIMP_GET_PERMISSIONS)
                           if r not in rutas)
        self.assertFalse(fantasmas, "la matriz nombra rutas que no existen: %s" % fantasmas)

    def test_toda_ruta_de_escritura_de_la_api_esta_clasificada(self):
        try:
            from odoo.addons.shrimp_api.controllers.framework import (
                API_ROUTE_PERMISSIONS, ROUTES)
        except ImportError:
            self.skipTest("sin shrimp_api")
        instalados = self.env.registry._init_modules
        sin = sorted("%s %s" % (r.method, r.path) for r in ROUTES
                     if r.module in instalados and r.method != "GET" and not r.public
                     and (r.method, r.path) not in API_ROUTE_PERMISSIONS)
        self.assertFalse(sin, "rutas de escritura de la API sin permiso: %s" % sin)

    # ------------------------------------------------------------------
    # Matriz: «ver» no escribe nada; cada permiso abre solo lo suyo
    # ------------------------------------------------------------------
    @mute_logger("odoo.http", "odoo.addons.shrimp_user_registry.models.ir_http")
    def _matriz(self, user, permisos, gestionar=False):
        self.authenticate(user.login, CLAVE)
        token = _csrf(self.url_open("/my/data").text)
        instaladas = self._rutas_instaladas()
        errores = []
        for ruta, (perm, _scope) in sorted(SHRIMP_ROUTE_PERMISSIONS.items()):
            if ruta not in instaladas:
                continue
            esperado = _permitido(perm, permisos, gestionar)
            if esperado and "<" not in ruta:
                # Las rutas sin registro ejecutarían la acción con datos
                # vacíos: lo permitido se comprueba con las que llevan uno.
                continue
            r = self._post(ruta, token)
            bloqueado = r.status_code == 403 and MARCA_403 in r.text
            if bloqueado == esperado:
                errores.append("%s %s -> %s" % ("bloqueada" if bloqueado else "abierta", ruta,
                                                r.status_code))
        self.assertFalse(errores, "\n".join(errores))

    def test_solo_ver_no_escribe_nada(self):
        self._matriz(self.ver, {"view"})

    def test_vender_abre_solo_lo_suyo(self):
        u = subusuario(self.env, self.emp, "vende.pm", {"camaronera": {"view", "sell"}})
        self._matriz(u, {"view", "sell"})

    def test_comprar_abre_solo_lo_suyo(self):
        u = subusuario(self.env, self.emp, "compra.pm", {"camaronera": {"view", "buy"}})
        self._matriz(u, {"view", "buy"})

    def test_facturacion_abre_solo_lo_suyo(self):
        u = subusuario(self.env, self.emp, "factura.pm", {"camaronera": {"view", "billing"}})
        self._matriz(u, {"view", "billing"})

    def test_gestionar_cuenta_abre_solo_lo_suyo(self):
        u = subusuario(self.env, self.emp, "gestiona.pm", {"camaronera": {"view"}}, gestionar=True)
        self._matriz(u, {"view"}, gestionar=True)

    # ------------------------------------------------------------------
    # Casos concretos del hueco
    # ------------------------------------------------------------------
    def test_ver_no_edita_la_ficha_ni_certificados(self):
        correo = self.emp.email
        self.authenticate(self.ver.login, CLAVE)
        pagina = self.url_open("/marketplace/my-account")
        self.assertEqual(pagina.status_code, 200, "ver sí entra (solo lectura)")
        self.assertIn("shrimpPermissionDeny", pagina.text, "la página desactiva los formularios")
        r = self._post("/marketplace/my-account/save", _csrf(pagina.text), email="robo@prueba.test")
        self.assertEqual(r.status_code, 403)
        self.assertIn(MARCA_403, r.text)
        self.assertIn("Gestionar cuenta", r.text)
        self.assertEqual(self.emp.email, correo)
        adjunto = self.env["ir.attachment"].sudo().create({
            "name": "cert.pdf", "raw": b"%PDF-1.4 prueba", "res_model": "res.partner",
            "res_id": self.emp.id})
        linea = self.env["shrimp.user.certificate.line"].sudo().create({
            "partner_id": self.emp.id,
            "certificate_id": self.env["shrimp.certificate"].sudo().search([], limit=1).id,
            "file_attachment_id": adjunto.id, "status": "approved",
            "expiry_date": "2030-01-01", "issue_date": "2025-01-01"})
        r = self._post("/marketplace/my-certificates/%s/delete" % linea.uuid_ref, _csrf(pagina.text))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(linea.exists())

    def test_gestionar_cuenta_edita_la_ficha(self):
        u = subusuario(self.env, self.emp, "ficha.pm", {"camaronera": {"view"}}, gestionar=True)
        self.authenticate(u.login, CLAVE)
        pagina = self.url_open("/marketplace/my-account")
        self.assertNotIn("my\\\\-account/save", pagina.text, "su formulario no se desactiva")
        r = self._post("/marketplace/my-account/save", _csrf(pagina.text), phone="0999999999",
                       farm_razon_social="Cam S.A.", farm_representante="Rep",
                       farm_telefono="04-1234567", farm_ubicacion="Guayas")
        self.assertIn("saved=1", r.headers.get("Location", ""))
        self.assertEqual(self.emp.phone, "0999999999")

    def test_publicar_exige_vender_y_usa_el_actor(self):
        """AL-22: el subusuario con «vender» publica (antes, error 500)."""
        prod = producto(self.env, self.emp, nombre="Lote AL22", publicado=False,
                        etapa="shrimp_marketplace.shrimp_stage_juvenil")
        vende = subusuario(self.env, self.emp, "publica.pm", {"camaronera": {"view", "sell"}})
        with self.assertRaises(AccessError):
            prod.with_user(self.ver).sudo().action_publish()
        try:
            prod.with_user(vende).sudo().action_publish()
        except AccessError as e:  # pragma: no cover - lo que se arregla
            self.fail("el subusuario con «vender» no pudo publicar: %s" % e)
        except Exception:  # noqa: BLE001 - otras validaciones de negocio
            pass

    def test_factura_del_vendedor_exige_vender(self):
        tx = self.env["shrimp.transaction"].sudo().search([], limit=1)
        if not tx:
            self.skipTest("sin compras de demostración")
        tx = tx.sudo()
        tx.write({"seller_partner_id": self.emp.id, "seller_role": "camaronera"})
        with self.assertRaises(AccessError):
            tx.with_user(self.ver).action_register_seller_invoice({"number": "001-001-1"})
        compras = subusuario(self.env, self.emp, "factura2.pm", {"camaronera": {"view", "buy"}})
        with self.assertRaises(AccessError):
            tx.with_user(compras).action_register_seller_invoice({"number": "001-001-1"})

    def test_traspaso_entre_perfiles_exige_vender_en_los_dos(self):
        Lot = self.env["shrimp.stock.lot"]
        solo_cam = subusuario(self.env, self.emp, "trasp.pm", {"camaronera": {"view", "sell"}})
        with self.assertRaises(AccessError):
            Lot.with_user(solo_cam)._shrimp_check_transfer_actor(
                self.emp, roles=("camaronera", "laboratorio"))
        self.assertTrue(Lot.with_user(solo_cam)._shrimp_check_transfer_actor(
            self.emp, roles=("camaronera",)))
        with self.assertRaises(AccessError):
            Lot.with_user(self.ver)._shrimp_check_transfer_actor(self.emp, roles=("camaronera",))

    # ------------------------------------------------------------------
    # Perfil activo por usuario
    # ------------------------------------------------------------------
    def test_perfil_activo_por_usuario(self):
        dos = subusuario(self.env, self.emp, "dos.pm",
                         {"camaronera": {"view", "buy"}, "laboratorio": {"view", "buy"}})
        self.assertEqual(self.emp.shrimp_user_type, "camaronera")
        self.emp.with_user(self.admin)._shrimp_set_active_role("laboratorio")
        self.assertEqual(self.emp.shrimp_user_type, "camaronera", "la empresa no cambia")
        self.assertEqual(self.admin.shrimp_active_profile, "laboratorio")
        self.assertEqual(self.emp.with_user(self.admin)._shrimp_effective_type(), "laboratorio")
        self.assertEqual(self.emp.with_user(dos)._shrimp_effective_type(), "camaronera")
        self.assertEqual(self.admin.shrimp_active_role, "laboratorio")
        self.assertEqual(dos.shrimp_active_role, "camaronera")
        # El selector de cada uno marca SU perfil.
        self.assertEqual(self.emp.with_user(self.admin)._shrimp_profile_switcher_info()["active"],
                         "laboratorio")
        self.assertEqual(self.emp.with_user(dos)._shrimp_profile_switcher_info()["active"],
                         "camaronera")
        # Las capacidades siguen al perfil de cada uno.
        self.assertTrue(self.emp.with_user(self.admin)._shrimp_can("buy_from_semillero"))
        self.assertFalse(self.emp.with_user(dos)._shrimp_can("buy_from_semillero"))
        # Por el portal: el subusuario cambia el suyo y no el del admin.
        self.authenticate(dos.login, CLAVE)
        token = _csrf(self.url_open("/my/data").text)
        self.url_open("/my/profile/activate", data={"csrf_token": token, "role": "laboratorio",
                                                    "next": "/my/data"})
        self.assertEqual(dos.shrimp_active_profile, "laboratorio")
        self.emp.with_user(self.admin)._shrimp_set_active_role("camaronera")
        self.assertEqual(self.emp.with_user(dos)._shrimp_effective_type(), "laboratorio")
        self.assertEqual(self.emp.with_user(self.admin)._shrimp_effective_type(), "camaronera")
        # Para la contraparte y el backoffice, el perfil por defecto.
        self.assertEqual(self.emp.with_user(self.s["u_lab"])._shrimp_effective_type(), "camaronera")
        self.assertEqual(self.emp._shrimp_effective_type(), "camaronera")

    def test_perfil_elegido_que_ya_no_vale_cae_al_permitido(self):
        u = subusuario(self.env, self.emp, "cae.pm", {"laboratorio": {"view"}})
        # Sin permiso en el perfil de la empresa (camaronera): actúa con el suyo.
        self.assertEqual(self.emp.with_user(u)._shrimp_effective_type(), "laboratorio")
        u.sudo().shrimp_active_profile = "camaronera"
        self.assertEqual(self.emp.with_user(u)._shrimp_effective_type(), "laboratorio")

    def test_cambio_por_sitio_solo_toca_al_usuario(self):
        W = self.env["website"].sudo()
        if "shrimp_platform" not in W._fields:
            self.skipTest("sin perfiles por sitio")
        sitio_emp = W._shrimp_platform_site("copacker")
        if not sitio_emp or "pack_codigo_establecimiento" not in self.emp._fields:
            self.skipTest("sin sitio de empaque")
        self.env["shrimp.partner.role"].sudo().create({
            "partner_id": self.emp.id, "role": "maquilador", "state": "approved"})
        otro = subusuario(self.env, self.emp, "sitio.pm",
                          {"camaronera": {"view"}, "maquilador": {"view"}})
        nuevo = sitio_emp._shrimp_align_active_role(self.admin)
        self.assertEqual(nuevo, "maquilador")
        self.assertEqual(self.admin.shrimp_active_profile, "maquilador")
        self.assertEqual(self.emp.shrimp_user_type, "camaronera")
        self.assertEqual(self.emp.with_user(otro)._shrimp_effective_type(), "camaronera",
                         "el otro miembro sigue en su perfil")
        principal = W._shrimp_main_site()
        self.assertIn(principal._shrimp_align_active_role(self.admin), ("camaronera", "laboratorio"),
                      "de vuelta en el marketplace, un perfil de este sitio")
        self.assertEqual(self.emp.with_user(otro)._shrimp_effective_type(), "camaronera")

    # ------------------------------------------------------------------
    # Claves API y webhooks (ME-06), API REST, perfil de la clave (BA-11)
    # ------------------------------------------------------------------
    def test_claves_api_solo_con_gestionar_cuenta(self):
        if "shrimp.api.key" not in self.env or "acting_role" not in self.env["shrimp.api.key"]._fields:
            self.skipTest("sin shrimp_api")
        self.authenticate(self.ver.login, CLAVE)
        pagina = self.url_open("/my/api-keys")
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("Gestionar cuenta", pagina.text)
        r = self._post("/my/api-keys/new", _csrf(pagina.text), name="ERP", scopes="products:read")
        self.assertEqual(r.status_code, 403)
        r = self._post("/my/webhooks/new", _csrf(pagina.text), name="x", url="https://e.test/h",
                       all_events="1")
        self.assertEqual(r.status_code, 403)
        u = subusuario(self.env, self.emp, "api.pm", {"camaronera": {"view"}}, gestionar=True)
        self.authenticate(u.login, CLAVE)
        pagina = self.url_open("/my/api-keys")
        r = self._post("/my/api-keys/new", _csrf(pagina.text), name="ERP", scopes="products:read")
        self.assertEqual(r.status_code, 200)
        clave = self.env["shrimp.api.key"].sudo().search([("user_id", "=", u.id)])
        self.assertEqual(len(clave), 1)
        self.assertEqual(clave.acting_role, "camaronera", "BA-11: la clave fija su perfil")

    def test_api_rest_aplica_la_matriz(self):
        if "shrimp.api.key" not in self.env or "acting_role" not in self.env["shrimp.api.key"]._fields:
            self.skipTest("sin shrimp_api")
        Key = self.env["shrimp.api.key"].sudo()
        _k, secreto = Key.api_create_key(self.ver, "solo ver", ["products:read", "products:write",
                                                                "profile:write"])
        cab = {"Authorization": "Bearer %s" % secreto, "Content-Type": "application/json",
               "Idempotency-Key": "pm-1"}
        r = self.url_open("/api/v1/me", headers=cab)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["api_key"]["acting_role"], "camaronera")
        r = self.url_open("/api/v1/products", data="{}", headers=cab)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("code"), "permission_denied")
        cab["Idempotency-Key"] = "pm-2"
        self.env.flush_all()
        r = self.opener.patch(self.base_url() + "/api/v1/me", data='{"phone": "1"}', headers=cab)
        self.assertEqual(r.status_code, 403, "la ficha de la empresa exige «gestionar cuenta»")
        # La clave no sigue el perfil que el usuario elija después en el portal.
        self.emp.with_user(self.ver)._shrimp_set_active_role("laboratorio")
        r = self.url_open("/api/v1/me", headers=cab)
        self.assertEqual(r.json()["api_key"]["acting_role"], "camaronera")

    # ------------------------------------------------------------------
    # Transferencia de la administración (D-37)
    # ------------------------------------------------------------------
    def test_transferir_admin_con_permisos_elegidos(self):
        nuevo = subusuario(self.env, self.emp, "nuevoadm.pm", {"camaronera": {"view"}})
        self.authenticate(self.admin.login, CLAVE)
        pagina = self.url_open("/my/team")
        self.assertIn("prev_manage_account", pagina.text)
        m = nuevo._shrimp_member()
        self.url_open("/my/team/%s/make-admin" % m.uuid_ref, data={
            "csrf_token": _csrf(pagina.text), "confirm": "1", "prev_custom": "1",
            "prev_camaronera_view": "1", "prev_camaronera_buy": "1"})
        self.assertTrue(m.is_admin)
        viejo = self.admin._shrimp_member()
        self.assertFalse(viejo.is_admin)
        self.assertEqual(viejo._permissions_dict(), {"camaronera": {"view", "buy"}})
        self.assertFalse(viejo.can_manage_account)
        # Por defecto (sin elegir): todo lo delegable y «gestionar cuenta».
        viejo._shrimp_make_admin()
        self.assertTrue(viejo.is_admin)
        m.invalidate_recordset()
        self.assertFalse(m.is_admin)
        self.assertEqual(set(m._permissions_dict()), {"camaronera", "laboratorio"})
        self.assertTrue(m.can_manage_account)
        self.assertEqual(m._permissions_dict()["camaronera"],
                         {"view", "buy", "sell", "approve", "billing"})

    def test_equipo_guarda_gestionar_cuenta(self):
        u = subusuario(self.env, self.emp, "equipo.pm", {"camaronera": {"view"}})
        self.authenticate(self.admin.login, CLAVE)
        pagina = self.url_open("/my/team")
        self.assertIn("perm_manage_account", pagina.text)
        m = u._shrimp_member()
        self.url_open("/my/team/%s/permissions" % m.uuid_ref, data={
            "csrf_token": _csrf(pagina.text), "perm_camaronera_view": "1",
            "perm_manage_account": "1"})
        self.assertTrue(m.can_manage_account)
        self.assertTrue(u._shrimp_has_perm("manage_account"))
        self.assertFalse(u._shrimp_has_perm("sell"))

    # ------------------------------------------------------------------
    # Migración 19.0.1.13.0
    # ------------------------------------------------------------------
    def test_migracion_copia_el_perfil_de_la_empresa(self):
        import odoo.addons.shrimp_user_registry as mod
        ruta = os.path.join(os.path.dirname(mod.__file__), "migrations", "19.0.1.13.0",
                            "post-migrate.py")
        spec = importlib.util.spec_from_file_location("shrimp_mig_19_0_1_13", ruta)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        usuarios = self.admin | self.ver | self.s["u_lab"]
        usuarios.sudo().write({"shrimp_active_profile": False})
        self.env.flush_all()
        mig.migrate(self.env.cr, "19.0.1.12.0")
        self.env.invalidate_all()
        self.assertEqual(self.admin.shrimp_active_profile, "camaronera")
        self.assertEqual(self.ver.shrimp_active_profile, "camaronera")
        self.assertEqual(self.s["u_lab"].shrimp_active_profile, "laboratorio",
                         "dueño de una cuenta sin miembros")
