"""«Estado real del lote»: observaciones del vendedor separadas de la etapa
publicada (models/shrimp_product_observation.py, shrimp_product_evolution.py).

- alta validada (rangos, etapa del tipo de lote) sin tocar publicación ni stock;
- corrección/borrado solo dentro del plazo (Ajustes); después, fija;
- divergencia: aviso al vendedor y a los compradores en curso, sin duplicados;
- «Actualizar publicación a la etapa observada»: solo explícito;
- ficha pública, modal del dueño, permisos (otro socio / subusuario sin «Vender»);
- migración de orígenes.
"""
import importlib.util
import os
import re
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, tagged

from .common import CLAVE, ShrimpSecurityCommon, crear_socios, csrf_de, producto


def subusuario(env, empresa, login, permisos):
    contacto = env["res.partner"].sudo().create({
        "name": "Sub %s" % login, "email": "%s@prueba.test" % login,
        "parent_id": empresa.id, "type": "contact"})
    user = env["res.users"].sudo().create({
        "name": contacto.name, "login": login, "partner_id": contacto.id, "password": CLAVE,
        "group_ids": [(6, 0, [env.ref("base.group_portal").id])]})
    env["shrimp.account.member"].sudo()._shrimp_ensure(user, empresa, permissions=permisos)
    return user


class EvoCommon(ShrimpSecurityCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param("shrimp_marketplace.evolution_size_threshold_pct", "20")
        cls.ICP.set_param("shrimp_marketplace.evolution_edit_hours", "24")
        cls.pl10 = cls.env.ref("shrimp_marketplace.shrimp_stage_pl10")
        cls.pl12 = cls.env.ref("shrimp_marketplace.shrimp_stage_pl12")
        cls.pl15 = cls.env.ref("shrimp_marketplace.shrimp_stage_pl15")
        cls.juvenil = cls.env.ref("shrimp_marketplace.shrimp_stage_juvenil")
        cls.Evo = cls.env["shrimp.product.evolution"]

    def lote(self, **extra):
        vals = {"etapa": "shrimp_marketplace.shrimp_stage_pl10", "avg_size_mg": 2.0,
                "survival_rate": 90.0}
        vals.update(extra)
        etapa = vals.pop("etapa")
        return producto(self.env, self.s["sem"], nombre="Lote evo", etapa=etapa, **vals)

    def observar(self, prod, user=None, **data):
        datos = {"stage": self.pl10, "size": 2.0, "survival_rate": 88.0}
        datos.update(data)
        return prod.with_user(user or self.s["u_sem"]).shrimp_register_observation(datos)

    def avisos(self, record, texto):
        return record.sudo().message_ids.filtered(lambda m: texto in (m.subject or ""))


@tagged("post_install", "-at_install")
class TestObservaciones(EvoCommon):

    def test_alta_validada_sin_tocar_publicacion_ni_stock(self):
        prod = self.lote()
        disponible = prod.available_qty
        obs = self.observar(prod, stage=self.pl12, size=2.5, survival_rate=85.0,
                            water_temp_c=28.5, salinity_ppt=30, dissolved_oxygen_mgl=5.5,
                            water_ph=7.9, estimated_qty=40.0, note="Muestreo 100 animales")
        self.assertEqual(obs.origin, "observation")
        self.assertEqual(obs.published_stage_id, self.pl10, "guarda la etapa publicada de entonces")
        self.assertEqual(obs.user_id, self.s["u_sem"])
        self.assertEqual(obs.water_ph, 7.9)
        prod.invalidate_recordset()
        self.assertEqual(prod.stage_id, self.pl10, "la publicación NO cambia")
        self.assertEqual(prod.avg_size_mg, 2.0)
        self.assertEqual(prod.available_qty, disponible, "la cantidad estimada no toca el stock")
        self.assertTrue(obs.shrimp_stock_hint(), "estimada < publicada: sugiere ajustar")
        self.assertFalse(prod.shrimp_publication_changes(), "observar no deja foto de publicación")
        self.assertEqual(prod.shrimp_last_observation(), obs)

    def test_rangos(self):
        prod = self.lote()
        malos = [dict(survival_rate=101), dict(survival_rate=-1), dict(water_temp_c=41),
                 dict(salinity_ppt=61), dict(dissolved_oxygen_mgl=21), dict(water_ph=14.5),
                 dict(size=-1), dict(estimated_qty=-5), dict(stage=self.env["shrimp.stage"])]
        for datos in malos:
            with self.subTest(datos=datos), self.assertRaises(ValidationError):
                self.observar(prod, **datos)
        with self.assertRaises(ValidationError, msg="camarón no es etapa de un naupliero"):
            self.observar(prod, stage=self.juvenil)
        with self.assertRaises(ValidationError, msg="fecha futura"):
            self.observar(prod, date=fields.Datetime.now() + timedelta(days=2))
        # Bordes válidos
        self.observar(prod, survival_rate=100, water_temp_c=40, salinity_ppt=60,
                      dissolved_oxygen_mgl=20, water_ph=14, size=0)

    def test_unidad_gramos_en_camaron(self):
        prod = producto(self.env, self.s["cam"], nombre="Juvenil evo",
                        etapa="shrimp_marketplace.shrimp_stage_juvenil")
        self.assertEqual(prod.shrimp_observation_size_unit(), "g")
        obs = prod.with_user(self.s["u_cam"]).shrimp_register_observation(
            {"stage": self.juvenil, "size": 3.5, "survival_rate": 70})
        self.assertAlmostEqual(obs.avg_size_mg, 3500.0)
        self.assertEqual(obs.shrimp_size_display(), "3.5 g")

    def test_plazo_de_correccion_e_inmutabilidad(self):
        prod = self.lote()
        obs = self.observar(prod)
        self.assertTrue(obs.shrimp_is_editable())
        prod.with_user(self.s["u_sem"]).shrimp_update_observation(
            obs, {"stage": self.pl10, "size": 2.2, "survival_rate": 87, "note": "corregida"})
        self.assertEqual(obs.note, "corregida")
        # Pasado el plazo: fija para todos (también en sudo).
        self.env.cr.execute("UPDATE shrimp_product_evolution SET create_date = %s WHERE id = %s",
                            (fields.Datetime.now() - timedelta(hours=25), obs.id))
        obs.invalidate_recordset()
        self.assertFalse(obs.shrimp_is_editable())
        with self.assertRaises(ValidationError):
            prod.with_user(self.s["u_sem"]).shrimp_update_observation(
                obs, {"stage": self.pl10, "size": 9})
        with self.assertRaises(ValidationError):
            obs.sudo().write({"note": "tarde"})
        with self.assertRaises(ValidationError):
            obs.sudo().unlink()
        # Con plazo 48 h vuelve a ser corregible.
        self.ICP.set_param("shrimp_marketplace.evolution_edit_hours", "48")
        self.assertTrue(obs.shrimp_is_editable())
        prod.with_user(self.s["u_sem"]).shrimp_delete_observation(obs)
        self.assertFalse(obs.exists())

    def test_origenes_y_ficha_publica(self):
        prod = self.lote()
        prod.sudo().write({"avg_size_mg": 3.0})          # foto automática
        cambio = prod.shrimp_publication_changes()
        self.assertEqual(len(cambio), 1)
        self.assertEqual(cambio.origin, "publication")
        self.assertFalse(prod.shrimp_observations(), "un cambio de publicación no es observación")
        with self.assertRaises(ValidationError):
            cambio.write({"origin": "observation"})
        self.observar(prod, size=3.0)
        self.assertEqual(len(prod.shrimp_observations()), 1)

    def test_permisos_modelo(self):
        prod = self.lote()
        with self.assertRaises(AccessError):
            self.observar(prod, user=self.s["u_lab"])
        emp = self.s["sem"]
        self.env["shrimp.account.member"].sudo()._shrimp_ensure(self.s["u_sem"], emp, is_admin=True)
        solo_ver = subusuario(self.env, emp, "ver.evo", {"semillero": {"view"}})
        vende = subusuario(self.env, emp, "vende.evo", {"semillero": {"view", "sell"}})
        self.assertFalse(prod._shrimp_user_can_observe(solo_ver))
        with self.assertRaises(AccessError):
            self.observar(prod, user=solo_ver)
        self.assertTrue(prod._shrimp_user_can_observe(vende))
        self.observar(prod, user=vende)


@tagged("post_install", "-at_install")
class TestDivergencia(EvoCommon):

    def test_aviso_vendedor_y_compradores_sin_duplicados(self):
        prod = self.lote()
        # Dos compras en curso del MISMO comprador y una ya recibida de otro.
        tx1 = prod.execute_purchase_flow(self.s["lab"], 5.0)["transaction"]
        tx2 = prod.execute_purchase_flow(self.s["lab"], 3.0)["transaction"]
        self.assertEqual({tx1.state, tx2.state}, {"confirmed"})
        tx3 = prod.execute_purchase_flow(self.s["lab2"], 2.0)["transaction"]
        tx3.sudo().write({"state": "done"})
        # Reserva (solicitud de chequeo activa) de la camaronera.
        # (histórico: el flujo se retiró en D-28; se carga como importación)
        self.env["shrimp.check.request"].sudo().with_context(shrimp_legacy_check_import=True).create({
            "product_id": prod.id, "seller_partner_id": prod.seller_partner_id.id,
            "buyer_partner_id": self.s["cam"].id, "qty": 1.0})
        asunto_v = "no coincide con lo publicado"
        asunto_c = "Novedad en el lote"

        # Sin divergencia: nadie se entera.
        self.observar(prod, stage=self.pl10)
        self.assertFalse(self.avisos(prod, asunto_v))

        obs = self.observar(prod, stage=self.pl15)
        self.assertTrue(obs.shrimp_divergence()["stage"])
        aviso_v = self.avisos(prod, asunto_v)
        self.assertEqual(len(aviso_v), 1)
        self.assertIn(self.s["sem"], aviso_v.partner_ids)
        self.assertIn("Actualizar publicación a la etapa observada", aviso_v.body)
        avisos_tx = self.avisos(tx1 | tx2, asunto_c)
        self.assertEqual(len(avisos_tx), 1, "un aviso por comprador aunque tenga dos compras")
        self.assertIn(self.s["lab"], avisos_tx.partner_ids)
        self.assertFalse(self.avisos(tx3, asunto_c), "compra ya recibida: no se avisa")
        aviso_reserva = self.avisos(prod, asunto_c)
        self.assertEqual(len(aviso_reserva), 1)
        self.assertIn(self.s["cam"], aviso_reserva.partner_ids)
        self.assertNotIn(self.s["lab2"], aviso_reserva.partner_ids)

        # La misma divergencia otra vez: sin avisos nuevos.
        self.observar(prod, stage=self.pl15, size=2.1)
        self.assertEqual(len(self.avisos(prod, asunto_v)), 1)
        self.assertEqual(len(self.avisos(tx1 | tx2, asunto_c)), 1)
        # Con ventas, la etapa publicada no se cambia (el botón lo explica).
        self.assertTrue(prod.has_purchases())
        with self.assertRaises(ValidationError):
            prod.with_user(self.s["u_sem"]).shrimp_apply_observation(obs)
        self.assertEqual(prod.stage_id, self.pl10)

    def test_divergencia_de_talla_por_umbral(self):
        prod = self.lote()
        self.assertFalse(self.observar(prod, size=2.3).shrimp_divergence(), "15 % < 20 %")
        div = self.observar(prod, size=2.6).shrimp_divergence()
        self.assertTrue(div and div["size"] and not div["stage"])
        self.ICP.set_param("shrimp_marketplace.evolution_size_threshold_pct", "0")
        self.assertFalse(self.observar(prod, size=9.0).shrimp_divergence(), "0 = solo etapa")

    def test_actualizar_publicacion_solo_explicito(self):
        prod = self.lote()
        obs = self.observar(prod, stage=self.pl15)
        prod.invalidate_recordset()
        self.assertEqual(prod.stage_id, self.pl10, "observar nunca cambia la etapa publicada")
        with self.assertRaises(AccessError):
            prod.with_user(self.s["u_lab"]).shrimp_apply_observation(obs)
        prod.with_user(self.s["u_sem"]).shrimp_apply_observation(obs)
        self.assertEqual(prod.stage_id, self.pl15)
        self.assertFalse(obs.shrimp_divergence())
        self.assertEqual(prod.shrimp_publication_changes()[:1].stage_id, self.pl15,
                         "deja su «Cambio en la publicación»")
        with self.assertRaises(ValidationError):
            prod.with_user(self.s["u_sem"]).shrimp_apply_observation(obs)

    def test_migracion_de_origenes(self):
        prod = self.lote()
        Evo = self.Evo.sudo()
        auto = Evo.create({"product_id": prod.id, "note": "Actualización automática del producto."})
        self.assertEqual(auto.origin, "publication", "la foto automática nace como publicación")
        filas = Evo.with_context(shrimp_evolution_no_notify=True).create([
            {"product_id": prod.id, "note": "Muestreo registrado por el vendedor.", "origin": "observation"},
            {"product_id": prod.id, "note": "Muestreo del 2026-10-01. PCR: Negativo.", "origin": "observation"},
            {"product_id": prod.id, "note": "Medición a mano", "origin": "observation"},
        ])
        ficha = self.env["shrimp.product.quality"].sudo().create({
            "product_id": prod.id, "sample_date": fields.Date.today()})
        ficha.action_register_sample()
        self.assertEqual(ficha.evolution_id.origin, "quality")
        todas = auto | filas | ficha.evolution_id
        # Como estaban antes: todo con el valor por defecto de la columna nueva.
        self.env.flush_all()
        self.env.cr.execute("UPDATE shrimp_product_evolution SET origin = 'observation' WHERE id IN %s",
                            (tuple(todas.ids),))
        ruta = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "19.0.1.30.0",
                            "post-evolution_origin.py")
        spec = importlib.util.spec_from_file_location("evo_mig", ruta)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.migrate(self.env.cr, "19.0.1.26.0")
        todas.invalidate_recordset()
        self.assertEqual(auto.origin, "publication")
        self.assertEqual(filas.mapped("origin"), ["observation", "quality", "observation"])
        self.assertEqual(ficha.evolution_id.origin, "quality")


@tagged("post_install", "-at_install")
class TestEstadoRealPortal(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "ev")
        cls.pl10 = cls.env.ref("shrimp_marketplace.shrimp_stage_pl10")
        cls.pl15 = cls.env.ref("shrimp_marketplace.shrimp_stage_pl15")
        cls.prod = producto(cls.env, cls.s["sem"], nombre="Lote portal evo",
                            etapa="shrimp_marketplace.shrimp_stage_pl10", avg_size_mg=2.0)

    def url_open(self, *args, **kwargs):
        self.env.flush_all()
        resp = super().url_open(*args, **kwargs)
        self.env.invalidate_all()
        return resp

    def ficha(self):
        return "/marketplace/product/%s" % self.prod.uuid_ref

    def post_obs(self, datos, url=None):
        pagina = self.url_open(self.ficha()).text
        datos = dict(datos, csrf_token=csrf_de(pagina))
        return self.url_open(url or "/marketplace/products/%s/observations" % self.prod.uuid_ref,
                             data=datos, allow_redirects=False)

    def test_flujo_del_dueno_y_ficha_publica(self):
        self.authenticate(self.s["u_sem"].login, CLAVE)
        html = self.url_open(self.ficha()).text
        self.assertIn('id="evoObsModal"', html, "el dueño tiene el modal")
        self.assertIn("Registrar observación", html)
        r = self.post_obs({"date": "", "stage": self.pl15.uuid_ref, "size": "2,4",
                           "survival_rate": "86", "health_status": "Sin signos",
                           "water_temp_c": "28.4", "salinity_ppt": "31", "dissolved_oxygen_mgl": "5.6",
                           "water_ph": "7.9", "estimated_qty": "40", "note": "Nota visible",
                           "next": self.ficha()})
        self.assertEqual(r.status_code, 303)
        self.assertIn("obs=saved", r.headers["Location"])
        obs = self.prod.shrimp_last_observation()
        self.assertEqual(obs.stage_id, self.pl15)
        self.assertEqual(self.prod.stage_id, self.pl10)
        html = self.url_open(self.ficha() + "?obs=saved").text
        self.assertIn("Estado real del lote", html)
        self.assertIn('id="evo_div_owner"', html)
        self.assertIn("Actualizar publicación a la etapa observada", html)
        self.assertIn('id="evo_stock_hint"', html)
        self.assertIn("28.4 °C", html)
        # Rango inválido: vuelve con el mensaje.
        r = self.post_obs({"stage": self.pl10.uuid_ref, "survival_rate": "150", "next": self.ficha()})
        self.assertIn("obs_error=flash", r.headers["Location"])
        html = self.url_open(r.headers["Location"]).text
        self.assertIn("tiene que estar entre 0 y 100", html)
        # «Actualizar publicación», solo con su POST.
        r = self.post_obs({"next": self.ficha()}, url="/marketplace/products/%s/observations/%s/apply" % (
            self.prod.uuid_ref, obs.uuid_ref))
        self.assertIn("obs=applied", r.headers["Location"])
        self.assertEqual(self.prod.stage_id, self.pl15)

        # Público: ve el bloque y los datos, no la cantidad estimada ni botones.
        self.authenticate(None, None)
        html = self.url_open(self.ficha()).text
        self.assertIn("Estado real del lote", html)
        self.assertIn("Nota visible", html)
        self.assertIn("5.6 mg/L", html)
        self.assertNotIn('id="evoObsModal"', html)
        self.assertNotIn("Cantidad estimada", html)
        self.assertNotIn("evo_apply_btn", html)

    def test_comprador_ve_divergencia_informativa(self):
        self.prod.with_user(self.s["u_sem"]).shrimp_register_observation(
            {"stage": self.pl15, "size": 2.0})
        self.authenticate(self.s["u_lab"].login, CLAVE)
        html = self.url_open(self.ficha()).text
        self.assertIn("La última observación del vendedor no coincide con lo publicado", html)
        self.assertNotIn("evo_apply_btn", html)

    def test_permisos_http(self):
        obs = self.prod.with_user(self.s["u_sem"]).shrimp_register_observation({"stage": self.pl10})
        # Otro socio: 403 en todas las rutas.
        self.authenticate(self.s["u_lab"].login, CLAVE)
        pagina = self.url_open("/marketplace").text
        base = "/marketplace/products/%s/observations" % self.prod.uuid_ref
        for url in (base, "%s/%s/update" % (base, obs.uuid_ref), "%s/%s/delete" % (base, obs.uuid_ref),
                    "%s/%s/apply" % (base, obs.uuid_ref)):
            r = self.url_open(url, data={"csrf_token": csrf_de(pagina), "stage": self.pl10.uuid_ref},
                              allow_redirects=False)
            self.assertEqual(r.status_code, 403, url)
        # Subusuario sin «Vender»: 403; con «Vender»: puede.
        emp = self.s["sem"]
        self.env["shrimp.account.member"].sudo()._shrimp_ensure(self.s["u_sem"], emp, is_admin=True)
        subusuario(self.env, emp, "ver.evo.http", {"semillero": {"view"}})
        subusuario(self.env, emp, "vende.evo.http", {"semillero": {"view", "sell"}})
        self.authenticate("ver.evo.http", CLAVE)
        html = self.url_open(self.ficha()).text
        self.assertNotIn('id="evoObsModal"', html)
        r = self.url_open(base, data={"csrf_token": csrf_de(html), "stage": self.pl10.uuid_ref},
                          allow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.authenticate("vende.evo.http", CLAVE)
        html = self.url_open(self.ficha()).text
        r = self.url_open(base, data={"csrf_token": csrf_de(html), "stage": self.pl10.uuid_ref},
                          allow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("obs=saved", r.headers["Location"])

    def test_mis_productos_boton_y_modal(self):
        self.authenticate(self.s["u_sem"].login, CLAVE)
        html = self.url_open("/marketplace/products").text
        self.assertIn('id="evoObsModal"', html)
        self.assertTrue(re.search(r'data-evo-action="/marketplace/products/%s/observations"'
                                  % self.prod.uuid_ref, html))
