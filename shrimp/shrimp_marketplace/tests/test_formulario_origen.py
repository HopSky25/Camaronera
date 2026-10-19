"""Formulario de producto: instalación → piscina/tanque → siembras de origen.

- Sin instalación no se guardan piscina ni siembras (aunque lleguen en el POST).
- La piscina tiene que ser de la instalación elegida; las bodegas y oficinas
  no son origen de un lote.
- Las siembras candidatas dependen del tipo de producto: la larva del
  laboratorio sale del NAUPLIO sembrado en su tanque (no de su propio stock de
  postlarva ubicado allí); el camarón de la camaronera, de la LARVA sembrada.
- La trazabilidad aguas arriba sigue la siembra hasta el proveedor.
"""
from datetime import date, timedelta

from odoo.tests import HttpCase, tagged

from .common import CLAVE, crear_socios, csrf_de, producto


@tagged("post_install", "-at_install")
class TestFormularioOrigen(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.s = crear_socios(env, "fo")
        ref = env.ref
        cls.st_pl12 = ref("shrimp_marketplace.shrimp_stage_pl12")
        cls.st_engorde = env["shrimp.stage"].search([("code", "=", "ENGORDE")], limit=1)
        cls.libra = ref("shrimp_marketplace.uom_libra")
        cls.t3040 = ref("shrimp_marketplace.size_entero_3040")
        Fac, Pond, Alloc = env["shrimp.partner.facility"], env["shrimp.partner.pond"], env["shrimp.lot.allocation"]
        ayer = date.today() - timedelta(days=1)

        # Naupliero -> laboratorio: el nauplio llega como lote del producto
        # republicado del laboratorio y se siembra en su tanque.
        cls.nauplio = producto(env, cls.s["sem"], nombre="Nauplio fo",
                               etapa="shrimp_marketplace.shrimp_stage_nauplio", initial_qty=1000.0,
                               expected_delivery_date=date.today())
        cls.tx_n = cls.nauplio.execute_purchase_flow(cls.s["lab"], 600.0)["transaction"]
        cls.tx_n.action_receive()
        cls.lab_res = cls.tx_n.result_product_id
        # El laboratorio la avanza a PL12 (el nombre del lote ya no dice «nauplio»).
        cls.lab_res.write({"name": "Postlarva PL12 Corrida fo", "stage_id": cls.st_pl12.id})
        lote_n = cls.lab_res.stock_lot_ids.filtered(lambda l: l.owner_id == cls.s["lab"])
        cls.lab_fac = Fac.create({"partner_id": cls.s["lab"].id, "name": "Planta fo",
                                  "facility_type": "laboratory"})
        cls.lab_fac2 = Fac.create({"partner_id": cls.s["lab"].id, "name": "Planta 2 fo",
                                   "facility_type": "laboratory"})
        cls.lab_bodega = Fac.create({"partner_id": cls.s["lab"].id, "name": "Bodega alimento fo",
                                     "facility_type": "warehouse"})
        cls.tanque = Pond.create({"partner_id": cls.s["lab"].id, "facility_id": cls.lab_fac.id,
                                  "name": "Tanque fo 01", "pond_type": "tank"})
        cls.tanque2 = Pond.create({"partner_id": cls.s["lab"].id, "facility_id": cls.lab_fac2.id,
                                   "name": "Tanque fo 02", "pond_type": "tank"})
        cls.alloc_nauplio = Alloc.create({"stock_lot_id": lote_n.id, "pond_id": cls.tanque.id,
                                          "allocated_qty": 200.0, "allocation_date": ayer})
        # Stock PROPIO del laboratorio (PL sin compra) ubicado en el mismo tanque:
        # no es origen de nada.
        cls.pl_propia = producto(env, cls.s["lab"], nombre="PL propia fo", initial_qty=500.0)
        lote_pl = cls.pl_propia.stock_lot_ids[:1]
        cls.alloc_propia = Alloc.create({"stock_lot_id": lote_pl.id, "pond_id": cls.tanque.id,
                                         "allocated_qty": 100.0, "allocation_date": ayer})

        # Laboratorio -> camaronera: la larva se siembra en la piscina.
        cls.larva = producto(env, cls.s["lab2"], nombre="Larva fo", initial_qty=1000.0,
                             expected_delivery_date=date.today())
        tx_l = cls.larva.execute_purchase_flow(cls.s["cam"], 300.0)["transaction"]
        tx_l.action_receive()
        cls.tx_l = tx_l
        lote_l = env["shrimp.stock.lot"].search([
            ("origin_move_id", "in", tx_l.stock_move_ids.ids), ("owner_id", "=", cls.s["cam"].id)])
        cls.finca = Fac.create({"partner_id": cls.s["cam"].id, "name": "Finca fo", "facility_type": "farm"})
        cls.piscina = Pond.create({"partner_id": cls.s["cam"].id, "facility_id": cls.finca.id,
                                   "name": "Piscina fo 01"})
        cls.alloc_larva = Alloc.create({"stock_lot_id": lote_l.id, "pond_id": cls.piscina.id,
                                        "allocated_qty": 150.0, "allocation_date": ayer})

    # ------------------------------------------------------------ utilidades
    def _login(self, clave):
        self.authenticate(self.s["u_" + clave].login, CLAVE)
        r = self.url_open("/marketplace/products/new")
        self.token = csrf_de(r.text)
        return r.text

    def _crear_larva(self, nombre, **extra):
        datos = {"csrf_token": self.token, "name": nombre, "initial_qty": "100", "price": "2.5",
                 "stage_id": str(self.st_pl12.id), "origin_allocation_present": "1"}
        datos.update(extra)
        return self.url_open("/marketplace/products/create", data=datos)

    def _buscar(self, nombre):
        return self.env["shrimp.product"].search([("name", "=", nombre)])

    # ------------------------------------------------ candidatas por perfil
    def test_lo_sembrado_es_el_insumo_comprado(self):
        self.assertEqual(self.alloc_nauplio._shrimp_seeded_tipo(), "nauplio")
        self.assertEqual(self.alloc_nauplio._shrimp_seeded_product(), self.nauplio)
        self.assertTrue(self.alloc_nauplio._shrimp_is_upstream_input())
        self.assertFalse(self.alloc_propia._shrimp_is_upstream_input())
        self.assertEqual(self.alloc_larva._shrimp_seeded_tipo(), "larva")
        self.assertIn(self.s["sem"].name, self.alloc_nauplio._shrimp_seeded_label())

    def test_candidatas_laboratorio_son_nauplio(self):
        p = producto(self.env, self.s["lab"], nombre="Larva lab fo", publicado=False,
                     origin_facility_id=self.lab_fac.id, origin_pond_id=self.tanque.id,
                     production_date=date.today())
        self.assertEqual(p.origin_allocation_ids, self.alloc_nauplio)
        opciones = self.env["shrimp.product"]._shrimp_origin_allocation_options(self.s["lab"])
        self.assertIn(self.alloc_nauplio, opciones)
        self.assertNotIn(self.alloc_propia, opciones)

    def test_candidatas_camaronera_son_larva(self):
        p = producto(self.env, self.s["cam"], nombre="Camarón fo", publicado=False,
                     etapa="shrimp_marketplace.shrimp_stage_engorde", uom_id=self.libra.id,
                     presentation="entero", size_grade_id=self.t3040.id,
                     origin_facility_id=self.finca.id, origin_pond_id=self.piscina.id,
                     production_date=date.today())
        self.assertEqual(p.origin_allocation_ids, self.alloc_larva)

    def test_nauplio_no_tiene_siembras_de_origen(self):
        # Un nauplio (sale de reproductores) en el tanque no toma la siembra de nauplio.
        p = producto(self.env, self.s["lab"], nombre="Nauplio lab fo", publicado=False,
                     etapa="shrimp_marketplace.shrimp_stage_nauplio",
                     origin_facility_id=self.lab_fac.id, origin_pond_id=self.tanque.id)
        self.assertFalse(p.origin_allocation_ids)

    def test_siembra_anterior_a_la_produccion(self):
        p = producto(self.env, self.s["lab"], nombre="Larva vieja fo", publicado=False,
                     origin_facility_id=self.lab_fac.id, origin_pond_id=self.tanque.id,
                     production_date=date.today() - timedelta(days=10))
        self.assertFalse(p.origin_allocation_ids)

    # --------------------------------------------------------------- POST
    def test_sin_instalacion_no_guarda_piscina_ni_siembras(self):
        self._login("lab")
        self._crear_larva("Larva sin fac fo", origin_facility_ref="",
                          origin_pond_ref=self.tanque.uuid_ref,
                          origin_allocation_ref=self.alloc_nauplio.uuid_ref)
        p = self._buscar("Larva sin fac fo")
        self.assertEqual(len(p), 1)
        self.assertFalse(p.origin_facility_id)
        self.assertFalse(p.origin_pond_id)
        self.assertFalse(p.origin_allocation_ids)

    def test_con_instalacion_y_tanque(self):
        self._login("lab")
        self._crear_larva("Larva con tanque fo", origin_facility_ref=self.lab_fac.uuid_ref,
                          origin_pond_ref=self.tanque.uuid_ref)
        p = self._buscar("Larva con tanque fo")
        self.assertEqual(p.origin_facility_id, self.lab_fac)
        self.assertEqual(p.origin_pond_id, self.tanque)
        self.assertEqual(p.origin_allocation_ids, self.alloc_nauplio)

    def test_marcar_a_mano_solo_vale_lo_del_tanque_y_tipo(self):
        self._login("lab")
        # El stock propio ubicado en el tanque no es origen: se ignora y se
        # proponen las del tanque.
        self._crear_larva("Larva mano fo", origin_facility_ref=self.lab_fac.uuid_ref,
                          origin_pond_ref=self.tanque.uuid_ref,
                          origin_allocation_ref=self.alloc_propia.uuid_ref)
        p = self._buscar("Larva mano fo")
        self.assertEqual(p.origin_allocation_ids, self.alloc_nauplio)

    def test_piscina_de_otra_instalacion_y_bodega(self):
        self._login("lab")
        self._crear_larva("Larva cruzada fo", origin_facility_ref=self.lab_fac.uuid_ref,
                          origin_pond_ref=self.tanque2.uuid_ref)
        p = self._buscar("Larva cruzada fo")
        self.assertEqual(p.origin_facility_id, self.lab_fac)
        self.assertFalse(p.origin_pond_id)
        self.assertFalse(p.origin_allocation_ids)
        self._crear_larva("Larva bodega fo", origin_facility_ref=self.lab_bodega.uuid_ref)
        self.assertFalse(self._buscar("Larva bodega fo").origin_facility_id)

    def test_editar_quitando_instalacion_limpia_todo(self):
        self._login("lab")
        self._crear_larva("Larva editar fo", origin_facility_ref=self.lab_fac.uuid_ref,
                          origin_pond_ref=self.tanque.uuid_ref)
        p = self._buscar("Larva editar fo")
        self.assertTrue(p.origin_allocation_ids)
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "2.5",
            "stage_id": str(self.st_pl12.id), "origin_facility_ref": "",
            "origin_pond_ref": self.tanque.uuid_ref, "origin_allocation_present": "1",
            "origin_allocation_ref": self.alloc_nauplio.uuid_ref})
        p.invalidate_recordset()
        self.assertFalse(p.origin_facility_id)
        self.assertFalse(p.origin_pond_id)
        self.assertFalse(p.origin_allocation_ids)

    # ------------------------------------------------------------- página
    def test_pagina_laboratorio(self):
        html = self._login("lab")
        self.assertIn("Tanque de origen", html)
        self.assertIn('id="pfPondBlk"', html)
        # La bodega no se ofrece como origen (solo en el aviso).
        self.assertNotIn('value="%s"' % self.lab_bodega.uuid_ref, html)
        self.assertIn("Bodega alimento fo", html)
        # Candidata: el nauplio, con su proveedor; nunca el stock propio.
        self.assertIn('value="%s"' % self.alloc_nauplio.uuid_ref, html)
        self.assertNotIn('value="%s"' % self.alloc_propia.uuid_ref, html)
        self.assertIn('data-tipo="nauplio"', html)
        self.assertIn("Nauplio fo", html)
        self.assertIn("¿Qué es esto?", html)
        self.assertNotIn("Automáticas", html)

    def test_pagina_camaronera(self):
        html = self._login("cam")
        self.assertIn("Piscina de origen", html)
        self.assertIn('value="%s"' % self.alloc_larva.uuid_ref, html)
        self.assertIn('data-tipo="larva"', html)

    # ------------------------------------------------------ trazabilidad
    def test_trazabilidad_aguas_arriba_laboratorio(self):
        p = producto(self.env, self.s["lab"], nombre="Larva traza fo", publicado=False,
                     origin_facility_id=self.lab_fac.id, origin_pond_id=self.tanque.id,
                     production_date=date.today())
        moves = p._shrimp_origin_moves()
        # La compra al naupliero aparece aguas arriba.
        self.assertTrue(moves & self.tx_n.stock_move_ids)
        self.assertEqual(moves.mapped("product_id"), self.nauplio)

    def test_trazabilidad_aguas_arriba_camaronera(self):
        p = producto(self.env, self.s["cam"], nombre="Camarón traza fo", publicado=False,
                     etapa="shrimp_marketplace.shrimp_stage_engorde", uom_id=self.libra.id,
                     presentation="entero", size_grade_id=self.t3040.id,
                     origin_facility_id=self.finca.id, origin_pond_id=self.piscina.id,
                     production_date=date.today())
        moves = p._shrimp_origin_moves()
        self.assertTrue(moves)
        self.assertEqual(moves.mapped("product_id"), self.larva)
