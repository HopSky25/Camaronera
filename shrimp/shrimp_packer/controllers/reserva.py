"""Las pantallas de la reserva anticipada.

Dos mundos: el de la camaronera, que declara la cosecha y elige con quién se
compromete, y el de la empacadora, que recibe la declaración y responde con un
precio.

Todas las mutaciones van en sudo DESPUÉS de comprobar la propiedad del
registro, así que el portal no necesita ni un permiso de escritura sobre estos
modelos. Es deliberado: en este proyecto ya hubo un agujero grave por darle al
portal escritura que el controlador no usaba, y la regla de lectura acabó
haciendo también de regla de escritura.
"""

from urllib.parse import quote, urlencode

from odoo import http, fields, _
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from werkzeug.exceptions import NotFound, Forbidden


# Lo que llega por la barra de direcciones o por un formulario es texto libre, y
# un int()/float() desnudo sobre eso es un error 500 esperando a que alguien
# escriba «hola». Estas dos convierten sin reventar y dejan que rechace el
# valor la validación del modelo, que sí sabe explicarse.
def _entero(valor, por_defecto=0):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return por_defecto


def _decimal(valor, por_defecto=0.0):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return por_defecto


def _mensaje(error):
    return error.args[0] if getattr(error, "args", None) else str(error)


class ShrimpReservaBase(http.Controller):
    """Ayudas comunes a los dos lados."""

    def _partner(self):
        return request.env.user.partner_id

    def _tallas(self, presentation=None):
        dominio = [("active", "=", True)]
        if presentation:
            dominio.append(("presentation", "=", presentation))
        return request.env["shrimp.size.grade"].sudo().search(
            dominio, order="presentation, sequence, name")


class ShrimpReservaCamaronera(ShrimpReservaBase):
    """Lo que ve y hace quien va a cosechar."""

    # Se lista una sola vez porque lo usan el formulario que lo pinta y el
    # guardado que lo devuelve cuando la validación falla: perder lo tecleado
    # por una fecha mal puesta es la forma más rápida de que alguien abandone.
    _CAMPOS = ("expected_date", "expected_lb", "presentation", "size_grade_id",
               "pond_id", "facility_id", "tolerance_lb_pct",
               "tolerance_size_steps", "date_tolerance_days", "notes")

    def _solo_camaronera(self):
        """Corta el paso a quien no cosecha.

        El modelo ya lo impide al guardar, pero sin este corte se le abriría el
        formulario entero a una empacadora para reventar al final. Una pantalla
        que no lleva a ningún sitio es peor que no tenerla.
        """
        if self._partner().shrimp_user_type != "camaronera":
            raise Forbidden()

    def _mia(self, ref):
        declaracion = request.env["shrimp.harvest.forecast"].sudo().resolve_ref(ref)
        if not declaracion:
            raise NotFound()
        if declaracion.farmer_partner_id != self._partner():
            raise Forbidden()
        return declaracion

    def _mi_compromiso(self, ref):
        compromiso = request.env["shrimp.harvest.commitment"].sudo().resolve_ref(ref)
        if not compromiso:
            raise NotFound()
        if compromiso.farmer_partner_id != self._partner():
            raise Forbidden()
        return compromiso

    # ==================================================================
    # Mis cosechas declaradas
    # ==================================================================
    @http.route("/marketplace/reservas", type="http", auth="user", website=True)
    def reservas_index(self, **kw):
        self._solo_camaronera()
        declaraciones = request.env["shrimp.harvest.forecast"].sudo().search(
            [("farmer_partner_id", "=", self._partner().id)])
        return request.render("shrimp_packer.reserva_index", {
            "declaraciones": declaraciones,
            "hoy": fields.Date.context_today(self._partner()),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/reservas/nueva", type="http", auth="user",
                website=True, methods=["GET"])
    def reserva_form(self, **kw):
        self._solo_camaronera()
        socio = self._partner()
        return request.render("shrimp_packer.reserva_form", {
            "piscinas": request.env["shrimp.partner.pond"].sudo().search(
                [("partner_id", "=", socio.id), ("active", "=", True)]),
            "instalaciones": request.env["shrimp.partner.facility"].sudo().search(
                [("partner_id", "=", socio.id)]),
            "tallas": self._tallas(),
            "empacadoras": request.env["res.partner"].sudo().search(
                [("shrimp_user_type", "=", "empacadora"), ("active", "=", True)],
                order="name"),
            "datos": dict(
                {c: (kw.get(c) or "") for c in self._CAMPOS},
                # Se convierten aquí y no en la plantilla, que es donde menos
                # se puede depurar: el desplegable compara con el id.
                size_grade_id=_entero(kw.get("size_grade_id"), 0),
                pond_id=_entero(kw.get("pond_id"), 0),
                facility_id=_entero(kw.get("facility_id"), 0)),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/reservas/nueva", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def reserva_guardar(self, **post):
        self._solo_camaronera()
        socio = self._partner()
        destinatarios = [
            _entero(v) for v in request.httprequest.form.getlist("recipient_ids")
            if _entero(v)
        ]
        vals = {
            "farmer_partner_id": socio.id,
            "expected_date": post.get("expected_date") or False,
            "expected_lb": _decimal(post.get("expected_lb")),
            "presentation": post.get("presentation") or "entero",
            "size_grade_id": _entero(post.get("size_grade_id")) or False,
            "pond_id": _entero(post.get("pond_id")) or False,
            "facility_id": _entero(post.get("facility_id")) or False,
            "tolerance_lb_pct": _decimal(post.get("tolerance_lb_pct"), 20.0),
            "tolerance_size_steps": _entero(post.get("tolerance_size_steps"), 1),
            "date_tolerance_days": _entero(post.get("date_tolerance_days"), 7),
            "notes": (post.get("notes") or "").strip() or False,
            "open_call": bool(post.get("open_call")),
            "recipient_ids": [(6, 0, destinatarios)],
        }
        # En un savepoint: Odoo valida DESPUÉS de escribir, así que sin esto lo
        # que el modelo rechaza se queda igual en la base y deja declaraciones
        # fantasma que no se pueden ni publicar ni borrar.
        try:
            with request.env.cr.savepoint():
                # En sudo a propósito: la camaronera se fuerza arriba al socio
                # del usuario, y así el portal no necesita permiso de creación.
                # Ese permiso dejaría declarar cosechas a nombre de terceros.
                declaracion = request.env["shrimp.harvest.forecast"].sudo().create(vals)
                declaracion.action_publish(actor=socio)
        except (AccessError, ValidationError, ValueError) as e:
            params = {c: (post.get(c) or "") for c in self._CAMPOS}
            params["error"] = str(_mensaje(e))
            return request.redirect(
                "/marketplace/reservas/nueva?%s" % urlencode(params))
        return request.redirect("/marketplace/reservas/%s?mensaje=publicada"
                                % declaracion.uuid_ref)

    @http.route("/marketplace/reservas/<ref>", type="http", auth="user",
                website=True)
    def reserva_detalle(self, ref, **kw):
        self._solo_camaronera()
        declaracion = self._mia(ref)
        aceptado = declaracion.commitment_ids.filtered(
            lambda c: c.state in ("accepted", "to_confirm", "honored"))[:1]
        return request.render("shrimp_packer.reserva_detalle", {
            "d": declaracion,
            "compromisos": declaracion.commitment_ids.filtered(
                lambda c: c.state not in ("withdrawn", "lapsed")),
            "aceptado": aceptado,
            "mi_firma": aceptado.confirmation_ids.filtered(
                lambda c: c.active and c.role == "farmer")[:1] if aceptado else None,
            "tallas": self._tallas(declaracion.presentation),
            "hoy": fields.Date.context_today(self._partner()),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/marketplace/reservas/<ref>/cancelar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def reserva_cancelar(self, ref, **post):
        self._solo_camaronera()
        declaracion = self._mia(ref)
        try:
            with request.env.cr.savepoint():
                declaracion.action_cancel(
                    motivo=post.get("motivo"), actor=self._partner())
        except (AccessError, ValidationError) as e:
            return request.redirect("/marketplace/reservas/%s?error=%s"
                                    % (declaracion.uuid_ref, quote(str(_mensaje(e)))))
        return request.redirect("/marketplace/reservas/%s?mensaje=cancelada"
                                % declaracion.uuid_ref)

    @http.route("/marketplace/reservas/<ref>/cosecha", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def reserva_cosecha(self, ref, **post):
        self._solo_camaronera()
        declaracion = self._mia(ref)
        try:
            with request.env.cr.savepoint():
                declaracion.action_registrar_cosecha(
                    actual_lb=_decimal(post.get("actual_lb")),
                    actual_size_grade_id=_entero(post.get("actual_size_grade_id")),
                    actual_date=post.get("actual_date") or None,
                    actor=self._partner())
        except (AccessError, ValidationError) as e:
            return request.redirect("/marketplace/reservas/%s?error=%s"
                                    % (declaracion.uuid_ref, quote(str(_mensaje(e)))))
        return request.redirect("/marketplace/reservas/%s?mensaje=cosechada"
                                % declaracion.uuid_ref)

    # ==================================================================
    # Sobre un compromiso concreto
    # ==================================================================
    def _accion_compromiso(self, ref, metodo, **kwargs):
        compromiso = self._mi_compromiso(ref)
        destino = "/marketplace/reservas/%s" % compromiso.forecast_id.uuid_ref
        try:
            with request.env.cr.savepoint():
                getattr(compromiso, metodo)(actor=self._partner(), **kwargs)
        except (AccessError, ValidationError) as e:
            return request.redirect("%s?error=%s" % (destino, quote(str(_mensaje(e)))))
        return request.redirect("%s?mensaje=ok" % destino)

    @http.route("/marketplace/reservas/compromiso/<ref>/aceptar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def compromiso_aceptar(self, ref, **post):
        self._solo_camaronera()
        return self._accion_compromiso(ref, "action_accept")

    @http.route("/marketplace/reservas/compromiso/<ref>/descartar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def compromiso_descartar(self, ref, **post):
        self._solo_camaronera()
        return self._accion_compromiso(
            ref, "action_reject", motivo=post.get("motivo"))

    @http.route("/marketplace/reservas/compromiso/<ref>/desistir", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def compromiso_desistir(self, ref, **post):
        self._solo_camaronera()
        return self._accion_compromiso(
            ref, "action_desistir", motivo=post.get("motivo"))


class ShrimpReservaEmpacadora(ShrimpReservaBase):
    """La bandeja de quien compra a ciegas."""

    def _solo_empacadora(self):
        if self._partner().shrimp_user_type != "empacadora":
            raise Forbidden()

    def _dirigida_a_mi(self, ref):
        """La declaración tiene que estar dirigida a mí. Se comprueba aquí.

        Se resuelve en sudo para poder dar 404 y 403 distintos, y la propiedad
        se comprueba con `visible_para`, que es la misma regla que usa la
        ir.rule: dos copias de la misma regla se separan siempre.
        """
        declaracion = request.env["shrimp.harvest.forecast"].sudo().resolve_ref(ref)
        if not declaracion:
            raise NotFound()
        if not declaracion.visible_para(self._partner()):
            raise Forbidden()
        return declaracion

    def _mi_compromiso(self, ref):
        compromiso = request.env["shrimp.harvest.commitment"].sudo().resolve_ref(ref)
        if not compromiso:
            raise NotFound()
        if compromiso.packer_partner_id != self._partner():
            raise Forbidden()
        return compromiso

    @http.route("/empacadora/reservas", type="http", auth="user", website=True)
    def bandeja(self, **kw):
        self._solo_empacadora()
        socio = self._partner()
        Declaracion = request.env["shrimp.harvest.forecast"]
        todas = Declaracion.sudo().visibles_para(socio, solo_vivas=False)
        mias = request.env["shrimp.harvest.commitment"].sudo().search(
            [("packer_partner_id", "=", socio.id)])
        ya_respondidas = mias.mapped("forecast_id")
        return request.render("shrimp_packer.reserva_bandeja", {
            "abiertas": todas.filtered(
                lambda d: d.admite_compromisos and d not in ya_respondidas),
            "respondidas": mias,
            "acepta": socio.reserva_acepta,
            "hoy": fields.Date.context_today(socio),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/empacadora/reservas/preferencia", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def preferencia(self, **post):
        """El interruptor de recibir declaraciones abiertas.

        Vive aquí y no en el formulario de «Mi cuenta» a propósito: es una
        preferencia de esta bandeja y se decide mirándola, no en otra pantalla.
        Se escribe en sudo sobre el PROPIO socio del usuario —nunca sobre uno
        que venga por parámetro—, así que el portal sigue sin necesitar
        permiso de escritura sobre res.partner.
        """
        self._solo_empacadora()
        self._partner().sudo().write({
            "reserva_acepta": bool(post.get("reserva_acepta"))})
        return request.redirect("/empacadora/reservas?mensaje=preferencia")

    @http.route("/empacadora/reserva/<ref>", type="http", auth="user", website=True)
    def declaracion(self, ref, **kw):
        self._solo_empacadora()
        declaracion = self._dirigida_a_mi(ref)
        socio = self._partner()
        mio = declaracion.commitment_ids.filtered(
            lambda c: c.packer_partner_id == socio)[:1]
        return request.render("shrimp_packer.reserva_declaracion", {
            "d": declaracion,
            "mio": mio,
            "mi_firma": mio.confirmation_ids.filtered(
                lambda c: c.active and c.role == "packer")[:1] if mio else None,
            # Lo único que la empacadora puede mirar antes de comprometerse a
            # ciegas: cómo rindió en planta este proveedor y con cuánta
            # puntería estima sus cosechas.
            "rendimiento": request.env["shrimp.proveedor.ranking"].sudo()
                .resumen_publico(declaracion.farmer_partner_id),
            "reservas": declaracion.farmer_partner_id.sudo().reserva_resumen(),
            "hoy": fields.Date.context_today(socio),
            "datos": dict(kw),
            "mensaje": kw.get("mensaje"),
            "error": kw.get("error"),
        })

    @http.route("/empacadora/reserva/<ref>/comprometer", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def comprometer(self, ref, **post):
        self._solo_empacadora()
        declaracion = self._dirigida_a_mi(ref)
        socio = self._partner()
        destino = "/empacadora/reserva/%s" % declaracion.uuid_ref
        vals = {
            "forecast_id": declaracion.id,
            "packer_partner_id": socio.id,
            "committed_lb": _decimal(post.get("committed_lb")),
            "price_mode": post.get("price_mode") or "fijo",
            "price_per_lb": _decimal(post.get("price_per_lb")),
            "step_delta_per_lb": _decimal(post.get("step_delta_per_lb")),
            "price_floor_per_lb": _decimal(post.get("price_floor_per_lb")),
            "valid_until": post.get("valid_until") or False,
            "notes": (post.get("notes") or "").strip() or False,
        }
        existente = declaracion.commitment_ids.filtered(
            lambda c: c.packer_partner_id == socio)[:1]
        try:
            with request.env.cr.savepoint():
                if existente:
                    if existente.state not in ("sent", "withdrawn"):
                        raise ValidationError(_(
                            "Tu compromiso sobre esta cosecha ya no se puede "
                            "cambiar."))
                    existente.write({k: v for k, v in vals.items()
                                     if k not in ("forecast_id", "packer_partner_id")})
                else:
                    # En sudo tras comprobar que la declaración me fue dirigida:
                    # la empacadora se fuerza arriba al socio del usuario, así
                    # que el portal no necesita permiso de creación.
                    request.env["shrimp.harvest.commitment"].sudo().create(vals)
        except (AccessError, ValidationError, ValueError) as e:
            params = dict({k: (post.get(k) or "") for k in post}, error=str(_mensaje(e)))
            params.pop("csrf_token", None)
            return request.redirect("%s?%s" % (destino, urlencode(params)))
        return request.redirect("%s?mensaje=comprometida" % destino)

    def _accion(self, ref, metodo, **kwargs):
        compromiso = self._mi_compromiso(ref)
        destino = "/empacadora/reserva/%s" % compromiso.forecast_id.uuid_ref
        try:
            with request.env.cr.savepoint():
                getattr(compromiso, metodo)(actor=self._partner(), **kwargs)
        except (AccessError, ValidationError) as e:
            return request.redirect("%s?error=%s" % (destino, quote(str(_mensaje(e)))))
        return request.redirect("%s?mensaje=ok" % destino)

    @http.route("/empacadora/compromiso/<ref>/retirar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def retirar(self, ref, **post):
        self._solo_empacadora()
        return self._accion(ref, "action_withdraw")

    @http.route("/empacadora/compromiso/<ref>/desistir", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def desistir(self, ref, **post):
        self._solo_empacadora()
        return self._accion(ref, "action_desistir", motivo=post.get("motivo"))

    @http.route("/empacadora/compromiso/<ref>/comprar", type="http", auth="user",
                website=True, methods=["POST"], csrf=True)
    def comprar(self, ref, **post):
        self._solo_empacadora()
        return self._accion(ref, "action_comprar")


class ShrimpReservaConfirmacion(ShrimpReservaBase):
    """Firmar la cosecha que se salió de la banda. Lo usan las dos partes.

    Va en su propio controlador y no duplicado en los dos de arriba porque la
    firma es la misma operación para ambos lados: quien firma se comprueba
    contra `partner_id` de la propia fila, que es la única forma de que una
    parte no pueda firmar por la otra.
    """

    def _mi_firma(self, ref):
        firma = request.env["shrimp.harvest.confirmation"].sudo().resolve_ref(ref)
        if not firma:
            raise NotFound()
        socio = request.env.user.partner_id
        compromiso = firma.commitment_id
        if socio not in (compromiso.farmer_partner_id, compromiso.packer_partner_id):
            raise Forbidden()
        return firma

    @http.route("/marketplace/reservas/confirmacion/<ref>/firmar", type="http",
                auth="user", website=True, methods=["POST"], csrf=True)
    def firmar(self, ref, **post):
        firma = self._mi_firma(ref)
        socio = request.env.user.partner_id
        compromiso = firma.commitment_id
        if socio == compromiso.farmer_partner_id:
            destino = "/marketplace/reservas/%s" % compromiso.forecast_id.uuid_ref
        else:
            destino = "/empacadora/reserva/%s" % compromiso.forecast_id.uuid_ref
        acepta = post.get("decision") == "accepted"
        try:
            with request.env.cr.savepoint():
                if acepta:
                    firma.action_accept(actor=socio)
                else:
                    firma.action_reject(motivo=post.get("motivo"), actor=socio)
        except (AccessError, ValidationError) as e:
            return request.redirect("%s?error=%s" % (destino, quote(str(_mensaje(e)))))
        return request.redirect("%s?mensaje=firmada" % destino)
