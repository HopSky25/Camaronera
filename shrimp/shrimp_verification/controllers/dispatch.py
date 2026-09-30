"""Pantallas del seguimiento del despacho.

Una sola página, ``/marketplace/despacho/<compra>``, que ven las cuatro partes
y que enseña un formulario distinto según quién entre:

  * el VENDEDOR la llena y la corrige (fecha de pesca, salida, cita, transporte);
  * el COMPRADOR la ve entera, sin editar nada —el cliente insistió en que «el
    comprador debe conocer eso»—;
  * el TÉCNICO y su empresa la ven y son los únicos que pueden estampar la
    llegada real.

Sobre los permisos: el portal NO tiene escritura sobre ``shrimp.dispatch`` (ni
en ``ir.model.access.csv`` ni en las reglas). Todo pasa por ``sudo()`` DESPUÉS
de comprobar quién es el usuario, que es el patrón del proyecto; darle
escritura al portal «por si acaso» cuando el controlador ya trabaja en sudo es
lo que abrió el agujero de las líneas de verificación.
"""

from urllib.parse import quote

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import UserError, ValidationError
from werkzeug.exceptions import NotFound, Forbidden


class ShrimpDispatchPortal(http.Controller):

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _partner(self):
        return request.env.user.partner_id

    def _despacho(self, tx_ref):
        """(despacho, papel) del usuario, o 403.

        El papel es lo único que decide qué puede hacer en la pantalla, así que
        se resuelve una sola vez y aquí: repartirlo por la plantilla es como se
        terminan colando formularios que el navegador enseña a quien no debe.
        """
        tx = request.env["shrimp.transaction"].sudo().resolve_ref(tx_ref)
        if not tx:
            raise NotFound()
        yo = self._partner()

        # El papel se resuelve ANTES de tocar nada. Si el despacho se creara
        # primero, una petición de un tercero —que acaba en 403— habría dejado
        # un registro creado por el camino: un GET no autorizado no puede
        # escribir en la base.
        if yo == tx.seller_partner_id:
            papel = "vendedor"
        elif yo == tx.buyer_partner_id:
            papel = "comprador"
        else:
            verificacion = tx.verification_id[:1]
            if verificacion and yo in (verificacion.technician_partner_id,
                                       verificacion.verifier_partner_id):
                papel = "verificador"
            else:
                # Un tercero no tiene nada que hacer aquí: la cita dice dónde y
                # cuándo va a estar un camión cargado de camarón.
                raise Forbidden()

        return tx._ensure_dispatch(), papel

    def _volver(self, despacho, **kw):
        partes = "&".join(
            "%s=%s" % (clave, quote(str(valor)))
            for clave, valor in kw.items() if valor)
        destino = "/marketplace/despacho/%s" % despacho.transaction_id.uuid_ref
        return request.redirect(destino + ("?" + partes if partes else ""))

    # ==================================================================
    # La página
    # ==================================================================
    @http.route("/marketplace/despacho/<tx_ref>", type="http", auth="user",
                website=True)
    def dispatch_page(self, tx_ref, **kw):
        despacho, papel = self._despacho(tx_ref)
        return request.render("shrimp_verification.dispatch_page", {
            "page_name": "dispatch_page",
            "d": despacho,
            "tx": despacho.transaction_id,
            "papel": papel,
            "puede_editar": papel == "vendedor" and not despacho._bloqueado(),
            "puede_llegada": papel == "verificador" and not despacho.actual_arrival,
            "resumen": despacho.seller_partner_id.despacho_resumen(),
            "saved": kw.get("saved"),
            "error": kw.get("error"),
            "message": kw.get("message"),
        })

    # ==================================================================
    # El vendedor llena o corrige el despacho
    # ==================================================================
    @http.route("/marketplace/despacho/<tx_ref>/guardar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def dispatch_save(self, tx_ref, **post):
        despacho, papel = self._despacho(tx_ref)
        if papel != "vendedor":
            raise Forbidden()

        Dispatch = request.env["shrimp.dispatch"].sudo()
        vals = {
            "harvest_date": (post.get("harvest_date") or "").strip() or False,
            "farm_departure": Dispatch.desde_local(post.get("farm_departure")),
            "eta": Dispatch.desde_local(post.get("eta")),
            "carrier_name": (post.get("carrier_name") or "").strip() or False,
            "vehicle_plate": (post.get("vehicle_plate") or "").strip().upper() or False,
            "carrier_phone": (post.get("carrier_phone") or "").strip() or False,
            "notes": (post.get("notes") or "").strip() or False,
        }

        try:
            # savepoint obligatorio: Odoo valida DESPUÉS de escribir, así que
            # una ValidationError salta con la fila ya modificada y la
            # transacción envenenada. Sin esto, el "vuelve y corrígelo" que se
            # le enseña al vendedor aborta al siguiente SELECT.
            with request.env.cr.savepoint():
                aviso = despacho.registrar_plan(self._partner(), vals)
        except (UserError, ValidationError) as e:
            msg = e.args[0] if e.args else _("No se pudo guardar el despacho.")
            return self._volver(despacho, error="1", message=msg)

        return self._volver(despacho, saved="avisado" if aviso else "1")

    # ==================================================================
    # El técnico estampa la llegada real
    # ==================================================================
    @http.route("/marketplace/despacho/<tx_ref>/llegada", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def dispatch_arrival(self, tx_ref, **post):
        despacho, papel = self._despacho(tx_ref)
        if papel != "verificador":
            # El corte que pidió el cliente: la hora de llegada la pone un
            # tercero justamente para que no la pueda discutir ninguna de las
            # dos partes. Si la pudiera poner el vendedor, no valdría nada.
            raise Forbidden()

        Dispatch = request.env["shrimp.dispatch"].sudo()
        # "Ahora" es el caso normal —el técnico está delante del camión— pero
        # se admite corregir la hora hacia atrás: puede haber estado pesando y
        # registrarlo veinte minutos después.
        cuando = Dispatch.desde_local(post.get("actual_arrival")) or fields.Datetime.now()

        try:
            with request.env.cr.savepoint():
                despacho.registrar_llegada(self._partner(), cuando)
        except (UserError, ValidationError) as e:
            msg = e.args[0] if e.args else _("No se pudo registrar la llegada.")
            return self._volver(despacho, error="1", message=msg)

        return self._volver(despacho, saved="llegada")
