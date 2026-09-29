"""Avisos de lotes nuevos a las empacadoras.

El marketplace era un escaparate: la camaronera publicaba el lote y esperaba a
que alguien entrara a buscarlo. Pero la fecha de cosecha no espera —un lote que
no se coloca en 48 horas vale menos— así que el sistema es el que tiene que ir
a buscar al comprador.

El cruce es exactamente el mismo que hace shrimp.product.mejor_precio_hoy()
—talla, presentación y canal contra los renglones de las listas vigentes— pero
leído al revés: allí se pregunta «¿quién me paga mejor este lote?» y aquí
«¿a qué empacadora le sirve este lote?».

DE DÓNDE SALE LA RELEVANCIA, que es lo único que mantiene vivo el canal:

  1. La empacadora tiene que tener lista de precios VIGENTE, y esa lista tiene
     que cotizar esa talla en esa presentación. Si no la cotiza, el lote no le
     interesa: no es un filtro de cortesía, es la definición de su demanda,
     escrita por ella misma y actualizada cada semana.
  2. Un correo por tanda con todos los lotes dentro, nunca uno por lote.
  3. Se apaga desde el perfil, y el correo lleva el enlace para hacerlo.
  4. De cada lote se avisa UNA vez a cada empacadora. Lo garantiza una
     restricción en la base, no la buena memoria del cron: si mañana el cron
     corre dos veces, o si alguien lo lanza a mano, el segundo aviso no existe.

Sin las cuatro cosas el canal se quema, y quemarlo es definitivo: cuando una
empacadora marca el remitente como correo no deseado deja de recibir también
lo que sí le servía.
"""

import logging
from datetime import timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

# Cuánto hacia atrás se miran los lotes publicados. No es la frecuencia del
# aviso: es la ventana de rescate. Si una empacadora publica hoy una lista que
# cotiza 41/50 por primera vez, los lotes de 41/50 de los últimos tres días
# siguen en cámara y todavía se pueden colocar; dejarlos fuera por haberse
# publicado ayer sería perder ventas por una tecnicismo del reloj.
VENTANA_HORAS = 72

# En la primera pasada de una empacadora (recién registrada, o recién encendido
# el aviso) se recorta a 24 horas. Estrenar el canal con tres días de lotes de
# golpe es la peor primera impresión posible.
VENTANA_PRIMERA_HORAS = 24

# Tope de lotes detallados en un correo. Por encima de esto ya no se lee: se
# enseñan los que más valen y los demás se cuentan con un enlace. Los que no se
# detallan NO se dan por avisados, así que vuelven a competir en la siguiente
# tanda en vez de perderse.
MAX_LOTES_CORREO = 20

# Un lote cuya entrega es pasado mañana se cotiza distinto: la empacadora tiene
# que mover camión y turno de planta. Se marca en el correo.
DIAS_URGENTE = 3

COLOR_URGENTE = "#b3261e"
COLOR_NORMAL = "#5A6B74"


class ShrimpLotAlert(models.Model):
    """El registro de que a esta empacadora ya se le avisó de este lote.

    Es a la vez el antiduplicados y el historial con el que se podrá medir si
    el canal sirve: cuántos avisos acabaron en compra.
    """

    _name = "shrimp.lot.alert"
    _description = "Aviso de lote nuevo a una empacadora"
    _order = "sent_date desc, id desc"

    packer_partner_id = fields.Many2one(
        "res.partner", string="Empacadora", required=True, index=True,
        ondelete="cascade")
    product_id = fields.Many2one(
        "shrimp.product", string="Lote", required=True, index=True,
        ondelete="cascade")
    seller_partner_id = fields.Many2one(
        "res.partner", string="Camaronera", index=True)
    price_list_id = fields.Many2one(
        "shrimp.price.list", string="Lista que lo cotiza", ondelete="set null")

    price = fields.Float(string="Precio cotizado", digits=(16, 2))
    uom = fields.Selection([("kg", "$/Kg"), ("lb", "$/Lb")], string="Unidad")
    qty = fields.Float(string="Cantidad del lote")
    amount = fields.Float(
        string="Valor a ese precio", digits=(16, 2),
        help="Lo que costaría el lote entero al precio que la empacadora "
             "cotiza hoy. Es lo que hace que el aviso se abra.")
    sent_date = fields.Datetime(
        string="Avisado el", default=fields.Datetime.now, index=True)

    # Odoo 19 ignora _sql_constraints en silencio: la restricción no llegaría
    # nunca a PostgreSQL y el antiduplicados sería decorativo.
    _aviso_unico = models.Constraint(
        "unique(packer_partner_id, product_id)",
        "A cada empacadora se le avisa de un lote una sola vez.",
    )

    # ------------------------------------------------------------------
    # El cruce
    # ------------------------------------------------------------------
    @api.model
    def _lotes_candidatos(self, ahora):
        """Los lotes publicados que podrían interesarle a alguna empacadora.

        Se recorta aquí lo que no es mercadería de empacadora, con el mismo
        criterio que mejor_precio_hoy(): solo camarón adulto en ENGORDE. Un
        juvenil de 3 g no se vende a planta, se vende a otra finca para seguir
        engordando, y colarlo en el aviso enseñaría a la empacadora que este
        correo trae ruido.
        """
        desde = ahora - timedelta(hours=VENTANA_HORAS)
        lotes = self.env["shrimp.product"].sudo().search([
            ("state", "=", "published"),
            ("active", "=", True),
            ("available_qty", ">", 0),
            ("verification_scope", "=", "adult"),
            ("size_grade_id", "!=", False),
            ("presentation", "!=", False),
            # published_date solo se rellena al publicar desde el portal; los
            # lotes que nacen publicados por otra vía lo tienen vacío y sin
            # este segundo tramo no se avisaría de ellos jamás.
            "|", ("published_date", ">=", desde),
            "&", ("published_date", "=", False), ("create_date", ">=", desde),
        ])
        return lotes.filtered(
            lambda l: (l.stage_id.code or "").strip().upper() == "ENGORDE")

    @api.model
    def _mejor_linea(self, listas, lote):
        """El renglón que mejor paga este lote entre esas listas.

        Es el cruce de mejor_precio_hoy() al revés. Se respeta lo mismo: misma
        presentación (entero contra entero, cola contra cola, porque convertir
        exige aplicar rendimiento y daría una cifra inventada) y en cola solo
        el canal directa, que la sobrante es lo que queda de clasificar y no un
        precio al que se pueda comprar un lote entero.
        """
        candidatas = listas.mapped("line_ids").filtered(
            lambda l: l.size_grade_id == lote.size_grade_id
            and l.presentation == lote.presentation
            and (lote.presentation != "cola" or l.channel == "directa"))
        if not candidatas:
            return False
        return max(candidatas, key=lambda l: l.price)

    @api.model
    def _fila_aviso(self, lote, linea, competencia, hoy):
        cantidad = lote.cantidad_en(linea.uom)
        entrega = lote.expected_delivery_date
        urgente = bool(entrega and entrega <= hoy + timedelta(days=DIAS_URGENTE))
        return {
            "lote_id": lote.id,
            "linea_id": linea.id,
            "lista_id": linea.price_list_id.id,
            "vendedor_id": lote.seller_partner_id.id,
            "nombre": lote.name or "",
            "camaronera": lote.seller_partner_id.name or "",
            "talla": lote.size_grade_id.name or "",
            "presentacion": _("Entero") if lote.presentation == "entero" else _("Cola"),
            "calidad": linea.etiqueta_columna(),
            "cantidad": cantidad,
            "uom": "Kg" if linea.uom == "kg" else "Lb",
            "precio": linea.price,
            "valor": linea.price * cantidad,
            "ubicacion": lote.location or "",
            # Las fechas se formatean aquí y no en la plantilla: el QWeb de
            # Odoo 19 revienta con "incomplete format" en cuanto se le mete un
            # formato con % dentro de un t-out.
            "entrega": entrega.strftime("%d/%m/%Y") if entrega else "",
            "urgente": urgente,
            "color": COLOR_URGENTE if urgente else COLOR_NORMAL,
            # Cuántas empacadoras cotizan hoy esa talla para ese productor. Es
            # un número, nunca el precio del vecino: la lista de cada
            # empacadora es confidencial y filtrarla sería un problema
            # comercial, no un detalle de pantalla.
            "competencia": competencia,
            "url": "/marketplace/product/%s" % (lote.uuid_ref or ""),
        }

    @api.model
    def _digest_para(self, packer, lotes, listas_por_vendedor, competencia_por_lote,
                     ahora, hoy):
        """Las filas del resumen de esta empacadora, ya ordenadas."""
        PL = self.env["shrimp.price.list"].sudo()
        propias = PL.search([
            ("issuer_partner_id", "=", packer.id),
            ("state", "=", "published"),
        ]).filtered("is_current")
        if not propias:
            # Sin lista vigente no hay demanda declarada, y sin demanda
            # declarada cualquier aviso sería un disparo a ciegas.
            return []

        ya_avisados = set(self.sudo().search([
            ("packer_partner_id", "=", packer.id),
            ("product_id", "in", lotes.ids),
        ]).mapped("product_id").ids)

        minimo = packer.emp_aviso_min_cantidad or 0.0
        corte = None
        if not packer.emp_aviso_ultima_revision:
            corte = ahora - timedelta(hours=VENTANA_PRIMERA_HORAS)

        filas = []
        for lote in lotes:
            if lote.id in ya_avisados:
                continue
            if lote.seller_partner_id == packer:
                continue
            if corte and (lote.published_date or lote.create_date) < corte:
                continue
            if minimo and lote.cantidad_en("lb") < minimo:
                continue

            if packer.emp_aviso_incluir_no_dirigidos:
                listas = propias
            else:
                # Solo las listas MÍAS que además van dirigidas al dueño del
                # lote. visibles_para() ya resuelve los grupos con varias
                # razones sociales, que es donde esto se equivoca solo.
                listas = listas_por_vendedor[lote.seller_partner_id.id].filtered(
                    lambda l: l.issuer_partner_id == packer)
            if not listas:
                continue

            linea = self._mejor_linea(listas, lote)
            if not linea:
                continue
            filas.append(self._fila_aviso(
                lote, linea, competencia_por_lote.get(lote.id, 0), hoy))

        # Lo urgente primero y, dentro, lo que más vale. El orden es la mitad
        # del trabajo: si lo primero que se lee no vale la pena, el resto del
        # correo ya no se lee nunca más, ni este ni el del mes que viene.
        filas.sort(key=lambda f: (0 if f["urgente"] else 1, -f["valor"]))
        return filas

    # ------------------------------------------------------------------
    # El reparto
    # ------------------------------------------------------------------
    @api.model
    def _cron_avisar_lotes(self):
        """Reparte los resúmenes a las empacadoras a las que les toca.

        El cron corre cada hora y casi siempre no hace nada: quien decide si
        toca es la preferencia de cada empacadora. Correr cada hora y no cada
        día es lo que permite las dos tandas y lo que hace que un cron caído a
        las siete reparta a las ocho en vez de saltarse el día.
        """
        ahora = fields.Datetime.now()
        hoy = fields.Date.context_today(self)
        Partner = self.env["res.partner"].sudo()

        empacadoras = Partner.search([
            ("shrimp_user_type", "=", "empacadora"),
            ("active", "=", True),
            ("emp_aviso_frecuencia", "!=", "off"),
        ])
        pendientes = empacadoras.filtered(
            lambda p: p.email and p._aviso_slot_pendiente(ahora))
        if not pendientes:
            return 0

        lotes = self._lotes_candidatos(ahora)
        if not lotes:
            pendientes.write({"emp_aviso_ultima_revision": ahora})
            return 0

        # El cruce por vendedor se resuelve una sola vez y no una por
        # empacadora: son las mismas listas vigentes para todas.
        PL = self.env["shrimp.price.list"].sudo()
        listas_por_vendedor = {}
        competencia_por_lote = {}
        for lote in lotes:
            vendedor = lote.seller_partner_id
            if vendedor.id not in listas_por_vendedor:
                listas_por_vendedor[vendedor.id] = PL.visibles_para(vendedor)
            # Se reutiliza el cálculo que ya existe del lado de la camaronera.
            # Del resultado solo se toma el recuento de empacadoras: los
            # precios de ahí son de listas ajenas.
            competencia_por_lote[lote.id] = (
                lote.mejor_precio_hoy().get("empacadoras") or 0)

        enviados = 0
        for packer in pendientes:
            # Cada empacadora va en su propio savepoint: un dato roto en una
            # —una lista sin renglones, un lote sin unidad— no puede dejar sin
            # resumen a las demás ni tumbar el cron entero.
            try:
                with self.env.cr.savepoint():
                    filas = self._digest_para(
                        packer, lotes, listas_por_vendedor, competencia_por_lote,
                        ahora, hoy)
                    packer.emp_aviso_ultima_revision = ahora
                    if filas and self._enviar_digest(packer, filas):
                        enviados += 1
            except Exception:  # noqa: BLE001 - el cron sigue con la siguiente
                _logger.exception(
                    "Avisos de lotes: falló el resumen de %s", packer.name)
        if enviados:
            _logger.info("Avisos de lotes: %s resumen(es) enviado(s).", enviados)
        return enviados

    @api.model
    def _enviar_digest(self, packer, filas):
        """Manda un único correo con todos los lotes y deja el rastro."""
        plantilla = self.env.ref(
            "shrimp_packer.mail_template_aviso_lotes_nuevos", raise_if_not_found=False)
        if not plantilla:
            return False
        if not self.env["ir.mail_server"].sudo().search_count([]):
            _logger.warning(
                "Avisos de lotes: no hay servidor de correo saliente; "
                "%s se queda sin su resumen de %s lote(s).",
                packer.name, len(filas))
            return False

        mostradas = filas[:MAX_LOTES_CORREO]
        resto = len(filas) - len(mostradas)
        ctx = {
            "destinatario": packer.emp_contacto_comercial or packer.name,
            "filas": mostradas,
            "resto": resto,
            "total_lotes": len(mostradas),
            "total_valor": sum(f["valor"] for f in mostradas),
            "urgentes": sum(1 for f in mostradas if f["urgente"]),
        }
        try:
            # El savepoint no es adorno: si el fallo del envío es de base de
            # datos, tragarse la excepción sin él deja el cursor abortado y la
            # siguiente empacadora revienta sin motivo aparente.
            with self.env.cr.savepoint():
                plantilla.sudo().with_context(**ctx).send_mail(
                    packer.id, force_send=False,
                    email_values={"email_to": packer.email})
        except Exception:  # noqa: BLE001 - un correo caído no puede parar el cron
            _logger.exception(
                "Avisos de lotes: falló el envío a %s", packer.name)
            return False

        # El rastro se escribe DESPUÉS del envío y solo de los lotes que
        # realmente salieron detallados. Los que se quedaron fuera del tope no
        # están avisados, así que vuelven a competir en la siguiente tanda en
        # vez de desaparecer sin que nadie los haya visto.
        self.sudo().create([{
            "packer_partner_id": packer.id,
            "product_id": f["lote_id"],
            "seller_partner_id": f["vendedor_id"],
            "price_list_id": f["lista_id"],
            "price": f["precio"],
            "uom": "kg" if f["uom"] == "Kg" else "lb",
            "qty": f["cantidad"],
            "amount": f["valor"],
        } for f in mostradas])
        packer.emp_aviso_ultimo_envio = fields.Datetime.now()
        return True
