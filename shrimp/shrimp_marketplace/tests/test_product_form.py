"""Formulario de publicar/editar producto (rediseño 2026-10).

Cubre el contrato del POST que usa la página nueva: etapas por perfil,
presentación y talla solo en camarón (exigidas en engorde), «Publicar» desde
el formulario (y su vuelta a borrador con los motivos), certificados que
llegan del modal (prod_cert_<n>_* con archivo), editar y quitar
certificados, fotos (alta, baja, orden y portada), el selector de vendedor
del usuario interno y los campos bloqueados por compras.
"""
import re

from odoo.tests import HttpCase, tagged

from .common import CLAVE, PDF_MIN, PNG_1PX, crear_socios, csrf_de, producto


@tagged("post_install", "-at_install")
class TestFormularioProducto(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.s = crear_socios(cls.env, "pf")
        ref = cls.env.ref
        cls.st_nauplio = ref("shrimp_marketplace.shrimp_stage_nauplio")
        cls.st_pl12 = ref("shrimp_marketplace.shrimp_stage_pl12")
        cls.st_engorde = cls.env["shrimp.stage"].search([("code", "=", "ENGORDE")], limit=1)
        cls.st_juvenil = cls.env["shrimp.stage"].search([("code", "=", "JUVENIL")], limit=1)
        cls.millar = ref("shrimp_marketplace.uom_millar")
        cls.libra = ref("shrimp_marketplace.uom_libra")
        cls.t3040 = ref("shrimp_marketplace.size_entero_3040")
        cls.t1620 = ref("shrimp_marketplace.size_cola_1620")
        cls.cert = cls.env["shrimp.certificate"].create({
            "name": "PCR WSSV prueba pf", "issuer": "Lab. Prueba", "role": "all", "code": "PCR-PF",
            "expires_required": False})
        cls.interno = cls.env["res.users"].create({
            "name": "Interno pf", "login": "interno.pf", "password": CLAVE,
            "group_ids": [(6, 0, [ref("base.group_user").id])]})

    # ------------------------------------------------------------ utilidades
    def _login(self, clave):
        self.authenticate(self.s["u_" + clave].login, CLAVE)
        self.token = csrf_de(self.url_open("/marketplace/products/new").text)

    def _crear(self, nombre, **extra):
        datos = {"csrf_token": self.token, "name": nombre, "initial_qty": "100",
                 "price": "2.5"}
        files = extra.pop("files", None)
        datos.update(extra)
        return self.url_open("/marketplace/products/create", data=datos, files=files)

    def _buscar(self, nombre):
        return self.env["shrimp.product"].search([("name", "=", nombre)])

    # ------------------------------------------------------------ la página
    def test_pagina_nueva_estructura(self):
        self._login("sem")
        r = self.url_open("/marketplace/products/new")
        self.assertEqual(r.status_code, 200)
        html = r.text
        # Diseño nuevo: 4 pasos, vista previa, checklist, modal de certificado.
        for marca in ('id="pfSec1"', 'id="pfSec4"', 'id="pfPrevCard"', "Checklist para publicar",
                      'id="pfCertModal"', 'name="publish"', 'name="photo_order"'):
            self.assertIn(marca, html)
        self.assertNotIn("Guía rápida", html)
        # El semillero no ve las etapas de camarón (grupo apagado y deshabilitado).
        grupo = re.search(r'<div class="pf-cgrp off" data-tipo="camaron">', html)
        self.assertTrue(grupo, "el grupo Camarón debe salir apagado para un semillero")
        self.assertRegex(html, r'name="stage_id" value="%d"[^>]*disabled' % self.st_engorde.id)
        # Unidad por defecto del eslabón: millares.
        self.assertRegex(html, r'name="uom_id" value="%d"[^>]*checked' % self.millar.id)

        self._login("cam")
        html = self.url_open("/marketplace/products/new").text
        self.assertIn('<div class="pf-cgrp off" data-tipo="larva">', html)
        self.assertIn('<div class="pf-cgrp off" data-tipo="nauplio">', html)
        self.assertRegex(html, r'name="uom_id" value="%d"[^>]*checked' % self.libra.id)
        # shrimp_verification: aviso de verificación obligatoria para la camaronera.
        if "requires_verification" in self.env["shrimp.product"]._fields:
            self.assertIn('data-verif-roles="camaronera"', html)
            self.assertIn("Este lote requiere verificación en campo", html)

    # ------------------------------------------------- 1. alta por perfil
    def test_crear_semillero_nauplio(self):
        self._login("sem")
        r = self._crear("PF nauplio", stage_id=str(self.st_nauplio.id), uom_id=str(self.millar.id),
                        species_id="", survival_rate="85", location="Santa Elena",
                        # Presentación/talla no aplican a nauplio: se descartan.
                        presentation="entero", size_grade_id=str(self.t3040.id))
        self.assertEqual(r.status_code, 200)
        p = self._buscar("PF nauplio")
        self.assertEqual(len(p), 1)
        self.assertEqual(p.state, "draft")
        self.assertEqual(p.seller_partner_id, self.s["sem"])
        self.assertEqual(p.seller_role, "semillero")
        self.assertEqual(p.stage_id, self.st_nauplio)
        self.assertEqual(p.uom_id, self.millar)
        self.assertFalse(p.presentation)
        self.assertFalse(p.size_grade_id)
        self.assertEqual(p.location, "Santa Elena")

    def test_crear_sin_etapa_rechazado(self):
        self._login("sem")
        r = self._crear("PF sin etapa")
        self.assertFalse(self._buscar("PF sin etapa"))
        self.assertIn("error=stage_required", r.url)
        self.assertIn('id="pfServerError"', r.text)

    def test_crear_camaronera_engorde_exige_presentacion_y_talla(self):
        self._login("cam")
        r = self._crear("PF engorde incompleto", stage_id=str(self.st_engorde.id))
        self.assertFalse(self._buscar("PF engorde incompleto"))
        self.assertIn("El camarón de engorde necesita presentación y talla", r.text)
        self._crear("PF engorde sin talla", stage_id=str(self.st_engorde.id), presentation="entero")
        self.assertFalse(self._buscar("PF engorde sin talla"))
        # Talla de otra presentación: la restricción del modelo la rechaza.
        r = self._crear("PF engorde talla cruzada", stage_id=str(self.st_engorde.id),
                        presentation="entero", size_grade_id=str(self.t1620.id))
        self.assertFalse(self._buscar("PF engorde talla cruzada"))
        self.assertIn("no corresponde a la presentación", r.text)
        self._crear("PF engorde ok", stage_id=str(self.st_engorde.id), presentation="entero",
                    size_grade_id=str(self.t3040.id), uom_id=str(self.libra.id))
        p = self._buscar("PF engorde ok")
        self.assertEqual(len(p), 1)
        self.assertEqual(p.seller_role, "camaronera")
        self.assertEqual((p.presentation, p.size_grade_id), ("entero", self.t3040))
        # Juvenil: presentación y talla opcionales.
        self._crear("PF juvenil", stage_id=str(self.st_juvenil.id))
        self.assertEqual(len(self._buscar("PF juvenil")), 1)

    # --------------------------------- 2. etapa no permitida para el perfil
    def test_etapa_no_permitida_para_el_perfil(self):
        self._login("sem")
        r = self._crear("PF sem engorde", stage_id=str(self.st_engorde.id), presentation="entero",
                        size_grade_id=str(self.t3040.id))
        self.assertFalse(self._buscar("PF sem engorde"))
        self.assertIn("no la puede publicar un perfil semillero", r.text)
        self._login("cam")
        r = self._crear("PF cam pl12", stage_id=str(self.st_pl12.id))
        self.assertFalse(self._buscar("PF cam pl12"))
        self.assertIn("no la puede publicar un perfil camaronera", r.text)
        # En edición tampoco se puede pasar a una etapa ajena al perfil...
        self._login("sem")
        p = producto(self.env, self.s["sem"], nombre="PF sem edit", publicado=False)
        r = self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "initial_qty": "100", "price": "10",
            "stage_id": str(self.st_engorde.id), "presentation": "entero",
            "size_grade_id": str(self.t3040.id)})
        p.invalidate_recordset()
        self.assertEqual(p.stage_id, self.st_pl12)
        self.assertIn("no la puede publicar", r.text)
        # ...pero sí cambiar dentro de las suyas.
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "10",
            "stage_id": str(self.st_nauplio.id)})
        p.invalidate_recordset()
        self.assertEqual(p.stage_id, self.st_nauplio)

    # ------------------------------------------------- 3. Publicar
    def test_publicar_desde_crear(self):
        self._login("sem")
        r = self._crear("PF publicar", stage_id=str(self.st_nauplio.id), publish="1")
        p = self._buscar("PF publicar")
        self.assertEqual(p.state, "published")
        self.assertTrue(p.published_date)
        self.assertIn("/marketplace/product/%s" % p.uuid_ref, r.url)
        self.assertIn("published=1", r.url)

    def test_publicar_falla_y_queda_en_borrador_con_motivos(self):
        p = producto(self.env, self.s["sem"], nombre="PF sin stock")
        p.execute_purchase_flow(self.s["lab"], 100.0)
        p.sudo().write({"state": "draft"})
        self.assertEqual(p.available_qty, 0.0)
        self._login("sem")
        r = self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": "PF sin stock (editado)",
            "health_status": "Sin novedades", "publish": "1"})
        p.invalidate_recordset()
        self.assertEqual(p.state, "draft")
        # Lo que sí se podía guardar, se guardó.
        self.assertEqual(p.name, "PF sin stock (editado)")
        self.assertEqual(p.health_status, "Sin novedades")
        self.assertIn("/marketplace/products/%s/edit" % p.uuid_ref, r.url)
        self.assertIn("error=publish", r.url)
        self.assertIn("no se pudo publicar", r.text)
        self.assertIn("no tiene stock disponible", r.text)

    def test_publicar_en_edicion(self):
        p = producto(self.env, self.s["sem"], nombre="PF borrador", publicado=False)
        self._login("sem")
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "10",
            "stage_id": str(self.st_pl12.id), "publish": "1"})
        p.invalidate_recordset()
        self.assertEqual(p.state, "published")

    # ------------------------------------------------- 4. certificados
    def test_certificado_del_modal_se_guarda_con_archivo(self):
        self._login("sem")
        self._crear("PF cert", stage_id=str(self.st_nauplio.id),
                    prod_cert_5000_id=self.cert.uuid_ref, prod_cert_5000_number="PCR-0932",
                    prod_cert_5000_issue_date="2026-09-28", prod_cert_5000_expiry_date="2026-12-20",
                    files={"prod_cert_5000_file": ("pcr.pdf", PDF_MIN, "application/pdf")})
        p = self._buscar("PF cert")
        linea = p.certificate_line_ids
        self.assertEqual(len(linea), 1)
        self.assertEqual(linea.certificate_id, self.cert)
        self.assertEqual(linea.number, "PCR-0932")
        self.assertEqual(str(linea.expiry_date), "2026-12-20")
        self.assertEqual(linea.status, "pending")
        self.assertEqual(linea.attachment_id.mimetype, "application/pdf")
        self.assertIn(linea.attachment_id, p.cert_attachment_ids)
        # Archivo de más de 5 MB: el validador del servidor lo rechaza con mensaje.
        grande = b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024 + 10)
        r = self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "2.5",
            "prod_cert_5001_id": self.cert.uuid_ref},
            files={"prod_cert_5001_file": ("grande.pdf", grande, "application/pdf")}, timeout=60)
        self.assertIn("excede el tamaño máximo", r.text)
        p.invalidate_recordset()
        self.assertEqual(len(p.certificate_line_ids), 1)

    def test_editar_y_quitar_certificado(self):
        p = producto(self.env, self.s["sem"], nombre="PF cert edit", publicado=False)
        att = self.env["ir.attachment"].create({
            "name": "c.pdf", "raw": PDF_MIN, "mimetype": "application/pdf",
            "res_model": "shrimp.product", "res_id": p.id})
        linea = self.env["shrimp.product.certificate.line"].create({
            "product_id": p.id, "certificate_id": self.cert.id, "number": "A-1",
            "issue_date": "2026-01-01", "expiry_date": "2026-06-01", "attachment_id": att.id})
        linea.sudo().write({"status": "approved"})
        self._login("sem")
        html = self.url_open("/marketplace/products/%s/edit" % p.uuid_ref).text
        self.assertIn('name="edit_cert_%s_number"' % linea.uuid_ref, html)
        self.assertIn('name="remove_cert_%s"' % linea.uuid_ref, html)
        # Editar (desde el modal): datos nuevos + archivo nuevo -> vuelve a revisión.
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "10",
            "edit_cert_%s_number" % linea.uuid_ref: "A-2",
            "edit_cert_%s_issue_date" % linea.uuid_ref: "2026-02-01",
            "edit_cert_%s_expiry_date" % linea.uuid_ref: "2027-02-01"},
            files={"edit_cert_%s_file" % linea.uuid_ref: ("nuevo.pdf", PDF_MIN, "application/pdf")})
        linea.invalidate_recordset()
        self.assertEqual(linea.number, "A-2")
        self.assertEqual(str(linea.expiry_date), "2027-02-01")
        self.assertEqual(linea.status, "pending")
        self.assertNotEqual(linea.attachment_id, att)
        self.assertTrue(linea.attachment_id.name.endswith("nuevo.pdf"))
        # Quitar (tras la confirmación del modal, el formulario marca remove_cert_<uuid>).
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "10",
            "remove_cert_%s" % linea.uuid_ref: "1"})
        self.assertFalse(linea.exists())

    # ------------------------------------------------- 5. fotos
    def test_fotos_alta_orden_portada_y_baja(self):
        self._login("sem")
        fotos = [("photo_files", ("a.png", PNG_1PX, "image/png")),
                 ("photo_files", ("b.png", PNG_1PX, "image/png")),
                 ("photo_files", ("c.png", PNG_1PX, "image/png"))]
        # La tercera elegida como portada.
        self._crear("PF fotos", stage_id=str(self.st_nauplio.id), photo_order="n:2,n:0,n:1", files=fotos)
        p = self._buscar("PF fotos")
        self.assertEqual(p.photo_attachment_ids.mapped("name"), ["c.png", "a.png", "b.png"])
        self.assertEqual(p.photo_attachment_ids[:1].name, "c.png")  # portada de las tarjetas
        a, b, c = (p.photo_attachment_ids.filtered(lambda x, n=n: x.name == n) for n in ("a.png", "b.png", "c.png"))
        # Edición: quitar «c», hacer portada a «b» y agregar «d» al final.
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "2.5",
            "remove_photo_%s" % c.access_token: "1",
            "photo_order": "e:%s,e:%s,n:0" % (b.access_token, a.access_token)},
            files=[("photo_files", ("d.png", PNG_1PX, "image/png"))])
        p.invalidate_recordset()
        self.assertEqual(p.photo_attachment_ids.mapped("name"), ["b.png", "a.png", "d.png"])
        self.assertFalse(c.exists(), "la foto quitada se borra si nadie más la usa")
        # Sin cambios: no se recrea nada.
        antes = p.photo_attachment_ids.ids
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "2.5",
            "photo_order": ",".join("e:%s" % x.access_token for x in p.photo_attachment_ids)})
        p.invalidate_recordset()
        self.assertEqual(p.photo_attachment_ids.ids, antes)
        # Una imagen falsa se rechaza con mensaje y no cambia nada.
        r = self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "2.5"},
            files=[("photo_files", ("x.png", b"<html>no</html>", "image/png"))])
        self.assertIn("no es una imagen válida", r.text)
        p.invalidate_recordset()
        self.assertEqual(p.photo_attachment_ids.ids, antes)

    def test_foto_compartida_no_se_borra(self):
        """Mover la portada copia la foto; la original sigue viva si otro
        producto (el lote republicado por el comprador) la usa."""
        p = producto(self.env, self.s["sem"], nombre="PF compartida", publicado=False)
        f1, f2 = (self.env["ir.attachment"].create({
            "name": n, "raw": PNG_1PX, "mimetype": "image/png",
            "res_model": "shrimp.product", "res_id": p.id}) for n in ("1.png", "2.png"))
        (f1 | f2).generate_access_token()
        p.photo_attachment_ids = [(6, 0, (f1 | f2).ids)]
        otro = producto(self.env, self.s["lab"], nombre="PF otro", publicado=False)
        otro.photo_attachment_ids = [(6, 0, f2.ids)]
        p.invalidate_recordset()  # orden real de la base (ir.attachment: id desc)
        self.assertEqual(p.photo_attachment_ids[:1], f2)
        self._login("sem")
        self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": p.name, "price": "10",
            "photo_order": "e:%s,e:%s" % (f1.access_token, f2.access_token)})
        p.invalidate_recordset()
        self.assertEqual(p.photo_attachment_ids.mapped("name"), ["1.png", "2.png"])
        self.assertTrue(f2.exists())
        self.assertIn(f2, otro.photo_attachment_ids)

    # ------------------------------------------------- 6. usuario interno
    def test_usuario_interno_elige_vendedor(self):
        self.authenticate("interno.pf", CLAVE)
        html = self.url_open("/marketplace/products/new").text
        self.token = csrf_de(html)
        self.assertIn('name="seller_partner_ref"', html)
        self.assertRegex(html, r'value="%s" data-role="semillero"' % self.s["sem"].uuid_ref)
        # Sin vendedor: no se crea.
        r = self._crear("PF interno sin vendedor", stage_id=str(self.st_nauplio.id))
        self.assertFalse(self._buscar("PF interno sin vendedor"))
        self.assertIn("error=seller", r.url)
        # La etapa se valida contra el perfil del vendedor elegido.
        self._crear("PF interno engorde a semillero", seller_partner_ref=self.s["sem"].uuid_ref,
                    stage_id=str(self.st_engorde.id), presentation="entero", size_grade_id=str(self.t3040.id))
        self.assertFalse(self._buscar("PF interno engorde a semillero"))
        self._crear("PF interno ok", seller_partner_ref=self.s["sem"].uuid_ref,
                    stage_id=str(self.st_nauplio.id))
        p = self._buscar("PF interno ok")
        self.assertEqual(p.seller_partner_id, self.s["sem"])
        self.assertEqual(p.seller_role, "semillero")
        self.assertEqual(p.uom_id, self.millar)  # unidad del eslabón del vendedor
        self._crear("PF interno camaronera", seller_partner_ref=self.s["cam"].uuid_ref,
                    stage_id=str(self.st_engorde.id), presentation="entero", size_grade_id=str(self.t3040.id))
        p = self._buscar("PF interno camaronera")
        self.assertEqual((p.seller_partner_id, p.seller_role), (self.s["cam"], "camaronera"))
        # Un vendedor de portal no puede publicar a nombre de otro.
        self._login("sem")
        self._crear("PF suplantado", seller_partner_ref=self.s["lab"].uuid_ref, stage_id=str(self.st_pl12.id))
        self.assertEqual(self._buscar("PF suplantado").seller_partner_id, self.s["sem"])

    # ------------------------------------------------- 7. bloqueo por compras
    def test_campos_bloqueados_con_compras(self):
        p = producto(self.env, self.s["sem"], nombre="PF con compras")
        p.execute_purchase_flow(self.s["lab"], 5.0)
        self.assertTrue(p.has_purchases())
        self._login("sem")
        html = self.url_open("/marketplace/products/%s/edit" % p.uuid_ref).text
        self.assertIn("Este producto ya tiene compras registradas", html)
        self.assertRegex(html, r'name="price"[^>]*disabled')
        self.assertRegex(html, r'name="initial_qty"[^>]*disabled')
        self.assertIn('<input type="hidden" name="uom_id" value="%d"' % self.millar.id, html)
        r = self.url_open("/marketplace/products/%s/update" % p.uuid_ref, data={
            "csrf_token": self.token, "name": "PF con compras (nuevo nombre)",
            "price": "99", "initial_qty": "999", "stage_id": str(self.st_nauplio.id),
            "uom_id": str(self.libra.id), "health_status": "Revisado"})
        self.assertEqual(r.status_code, 200)
        p.invalidate_recordset()
        self.assertEqual(p.name, "PF con compras (nuevo nombre)")
        self.assertEqual(p.health_status, "Revisado")
        self.assertEqual(p.price, 10.0)
        self.assertEqual(p.initial_qty, 100.0)
        self.assertEqual(p.stage_id, self.st_pl12)
        self.assertEqual(p.uom_id, self.millar)
