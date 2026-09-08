from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ShrimpPriceList(models.Model):
    """Lista de precios que un comprador publica a sus proveedores.

    Es el documento que las empacadoras reparten cada semana a las camaroneras:
    "esto es lo que pago por talla desde tal fecha". No fija el precio de
    ninguna compra —eso se sigue pactando en la operación— sino que le dice al
    productor a cuánto le pagan antes de ofrecer.

    Por eso lleva ventana de despacho: un precio de camarón sin fecha no vale
    nada, se mueve semana a semana.
    """

    _name = "shrimp.price.list"
    _description = "Lista de precios de compra"
    _inherit = ["shrimp.uuid.mixin", "mail.thread"]
    _order = "issue_date desc, id desc"

    name = fields.Char(
        string="Referencia", required=True, tracking=True,
        help="Como la identifica el comprador: «Semana 32», «Lista del 5 de agosto»...")

    issuer_partner_id = fields.Many2one(
        "res.partner", string="Publica", required=True, index=True,
        ondelete="cascade", tracking=True,
        help="Quien compra y publica lo que va a pagar.")

    issue_date = fields.Date(
        string="Fecha de emisión", required=True, tracking=True,
        default=fields.Date.context_today)

    # La ventana de despacho es lo que distingue una lista viva de un papel
    # viejo. "Hasta segunda orden" es literal en las listas del sector: rige
    # hasta que el comprador publique otra.
    dispatch_from = fields.Date(string="Despacho desde", tracking=True)
    dispatch_to = fields.Date(string="Despacho hasta", tracking=True)
    open_ended = fields.Boolean(
        string="Hasta segunda orden", default=True, tracking=True,
        help="Rige sin fecha de fin, hasta que se publique otra lista.")

    state = fields.Selection(
        [("draft", "Borrador"), ("published", "Publicada"), ("archived", "Archivada")],
        string="Estado", default="draft", required=True, index=True, tracking=True)

    currency_id = fields.Many2one(
        "res.currency", string="Moneda", required=True,
        default=lambda self: self.env.company.currency_id)

    quality_conditions = fields.Text(
        string="Condiciones de calidad",
        help="Lo que el comprador no acepta: branquias sucias, sabores, "
             "picados, hongos, color amarillo o mezclado...")

    # Forma de pago en campos y no en un texto libre: es lo que el productor
    # compara entre compradores, y así se puede ordenar y filtrar.
    advance_pct = fields.Float(
        string="Anticipo (%)", digits=(5, 2),
        help="Qué parte del valor se paga por adelantado. En la lista de "
             "ECUAMARISCO es 50. Si pagas todo de una vez, pon 100 y deja "
             "vacío «días para el saldo».")
    advance_days = fields.Integer(
        string="Días para el anticipo",
        help="Días calendario para pagar el anticipo, contados desde que "
             "empieza el proceso. En la lista de ECUAMARISCO son 5.")
    balance_days = fields.Integer(
        string="Días para el saldo",
        help="Días calendario para pagar el resto, contados desde la "
             "recepción de la factura. En la lista de ECUAMARISCO son 14. "
             "No aplica si el anticipo es del 100 %.")
    payment_notes = fields.Char(
        string="Nota de pago",
        help="Cualquier condición que no entre en los campos anteriores. "
             "Es opcional.")

    # A quién va dirigida. Es lo que hace confidencial a la lista: en el sector
    # cada productor recibe su propio precio, y ver el del vecino sería un
    # problema comercial. Si el destinatario es la empresa madre de un grupo,
    # la ven también sus camaroneras.
    recipient_ids = fields.Many2many(
        "res.partner", "shrimp_price_list_recipient_rel", "list_id", "partner_id",
        string="Dirigida a",
        help="Los productores que pueden verla. Si eliges la empresa madre de "
             "un grupo, la ven todas sus camaroneras.")
    recipient_count = fields.Integer(
        compute="_compute_counts", string="Destinatarios")

    # copy=True a propósito: en Odoo los One2many NO se copian por defecto, y
    # duplicar una lista sin sus precios no sirve de nada. La gracia de copiar
    # es partir de los de la semana pasada y mover tres o cuatro.
    line_ids = fields.One2many(
        "shrimp.price.list.line", "price_list_id", string="Precios por talla",
        copy=True)
    bonus_ids = fields.One2many(
        "shrimp.price.list.bonus", "price_list_id", string="Bonificaciones",
        copy=True)

    line_count = fields.Integer(compute="_compute_counts", string="Renglones")
    is_current = fields.Boolean(
        compute="_compute_is_current", search="_search_is_current",
        string="Vigente")
    is_upcoming = fields.Boolean(
        compute="_compute_is_current", string="Próxima",
        help="Publicada, pero su ventana de despacho todavía no empieza.")

    @api.depends("line_ids", "recipient_ids")
    def _compute_counts(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)
            rec.recipient_count = len(rec.recipient_ids)

    def visible_para(self, partner):
        """¿Este partner puede ver la lista?

        La ve el que la emite, y la ve el destinatario —o cualquier camaronera
        que cuelgue de un destinatario, para cubrir el caso del grupo con
        varias razones sociales.
        """
        self.ensure_one()
        if not partner:
            return False
        if partner == self.issuer_partner_id:
            return True
        if self.state != "published":
            return False
        return bool(set(partner.shrimp_grupo_ids()) & set(self.recipient_ids.ids))

    @api.model
    def visibles_para(self, partner, incluir_futuras=False):
        """Las listas publicadas que le tocan a este partner.

        Por defecto solo las vigentes: el comparador y el reporte no pueden
        mezclar el precio de esta semana con el de la próxima, porque el
        productor los leería como si compitieran entre sí.

        Con incluir_futuras se añaden las que aún no arrancan, para la pantalla
        de consulta, donde sí interesa verlas —marcadas como próximas— para
        planificar la cosecha.
        """
        if not partner:
            return self.browse()
        listas = self.sudo().search([
            ("state", "=", "published"),
            ("recipient_ids", "in", partner.shrimp_grupo_ids()),
        ])
        if incluir_futuras:
            return listas.filtered(lambda l: l.is_current or l.is_upcoming)
        return listas.filtered("is_current")

    @api.depends("state", "dispatch_from", "dispatch_to", "open_ended")
    def _compute_is_current(self):
        hoy = fields.Date.context_today(self)
        for rec in self:
            rec.is_current = rec.is_upcoming = False
            if rec.state != "published":
                continue
            if rec.dispatch_from and rec.dispatch_from > hoy:
                # Publicada pero todavía no arranca. No es vigente, pero
                # esconderla sería un error: las listas se reparten con días de
                # antelación justamente para que el productor planifique la
                # cosecha. En las listas reales el encabezado dice "DESDE
                # miércoles 5 de agosto", y se manda antes de esa fecha.
                rec.is_upcoming = True
                continue
            if not rec.open_ended and rec.dispatch_to and rec.dispatch_to < hoy:
                continue
            rec.is_current = True

    def _search_is_current(self, operator, value):
        # Odoo 19 normaliza los dominios de booleano a operator 'in' con un
        # conjunto: dar por hecho '=' invierte el filtro en silencio.
        if operator in ("in", "not in"):
            valores = set(value) if not isinstance(value, bool) else {value}
            quiere = True in valores
            if operator == "not in":
                quiere = not quiere
        else:
            quiere = bool(value) if operator == "=" else not bool(value)
        hoy = fields.Date.context_today(self)
        vigentes = self.search([("state", "=", "published")]).filtered(
            lambda r: (not r.dispatch_from or r.dispatch_from <= hoy)
            and (r.open_ended or not r.dispatch_to or r.dispatch_to >= hoy))
        return [("id", "in" if quiere else "not in", vigentes.ids)]

    @api.constrains("issuer_partner_id", "recipient_ids")
    def _check_partes(self):
        """Quién publica y quién recibe.

        La lista es de camarón adulto —tallas de entero y cola—, así que la
        publica una empacadora y la reciben camaroneras. El laboratorio y el
        semillero venden larvas y nauplios, que se cotizan por millar y no por
        talla: una lista así no les dice nada.
        """
        for rec in self:
            if rec.issuer_partner_id.shrimp_user_type != "empacadora":
                raise ValidationError(_(
                    "Las listas de precios de compra las publica una "
                    "empacadora. «%s» no lo es.") % (rec.issuer_partner_id.name or ""))
            ajenos = rec.recipient_ids.filtered(
                lambda p: p.shrimp_user_type != "camaronera")
            if ajenos:
                raise ValidationError(_(
                    "Esta lista es de camarón adulto, así que va dirigida a "
                    "camaroneras. No lo son: %s.") % ", ".join(ajenos.mapped("name")))

    @api.constrains("advance_pct", "advance_days", "balance_days")
    def _check_pago(self):
        for rec in self:
            if rec.advance_pct and not (0 < rec.advance_pct <= 100):
                raise ValidationError(_(
                    "El anticipo es un porcentaje: tiene que estar entre 1 y "
                    "100. Pusiste %s.") % ("{:.0f}".format(rec.advance_pct)))
            if rec.advance_days and rec.advance_days < 0:
                raise ValidationError(_("Los días del anticipo no pueden ser negativos."))
            if rec.balance_days and rec.balance_days < 0:
                raise ValidationError(_("Los días del saldo no pueden ser negativos."))

    @api.constrains("dispatch_from", "dispatch_to", "open_ended")
    def _check_ventana(self):
        for rec in self:
            if (not rec.open_ended and rec.dispatch_from and rec.dispatch_to
                    and rec.dispatch_to < rec.dispatch_from):
                raise ValidationError(_(
                    "La fecha de fin del despacho es anterior a la de inicio."))

    # ==================================================================
    # Acciones
    # ==================================================================
    def action_publish(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_(
                    "Una lista sin precios no se puede publicar. Agrega al menos "
                    "un renglón de talla."))
            if not rec.recipient_ids:
                raise ValidationError(_(
                    "Indica a qué productores va dirigida. Una lista sin "
                    "destinatarios no la vería nadie."))
            # Solo una lista vigente por comprador: si no, el productor no sabe
            # cuál mirar. La anterior se archiva sola.
            # Solo se archivan las que compiten con esta: mismo emisor y algún
            # destinatario en común. Una empacadora tiene varias listas vivas a
            # la vez, una por cliente, y archivarlas todas sería un desastre.
            anteriores = self.search([
                ("issuer_partner_id", "=", rec.issuer_partner_id.id),
                ("state", "=", "published"),
                ("id", "!=", rec.id),
                ("recipient_ids", "in", rec.recipient_ids.ids),
            ])
            anteriores.write({"state": "archived"})
            rec.state = "published"
            if anteriores:
                rec.message_post(body=_(
                    "Publicada. Se archivaron %s lista(s) anterior(es).") % len(anteriores))
        return True

    def action_archive_list(self):
        self.write({"state": "archived"})
        return True

    def action_back_to_draft(self):
        self.write({"state": "draft"})
        return True

    def action_duplicate_for_next(self):
        """Copia la lista para la semana siguiente: es como se trabaja de verdad,
        se parte de la anterior y se mueven dos o tres renglones."""
        self.ensure_one()
        nueva = self.copy({
            "name": _("%s (copia)") % self.name,
            "state": "draft",
            "issue_date": fields.Date.context_today(self),
            "dispatch_from": False,
            "dispatch_to": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": "shrimp.price.list",
            "res_id": nueva.id,
            "view_mode": "form",
            "target": "current",
        }

    # ==================================================================
    # Lectura para el portal
    # ==================================================================
    def matriz(self, presentation):
        """Devuelve la matriz de una presentación lista para pintar.

        {"tallas": [...], "columnas": [(clave, etiqueta), ...],
         "precios": {talla: {clave: precio}}, "uom": "kg"|"lb"}

        Se arma aquí y no en la plantilla porque las dos presentaciones tienen
        columnas distintas: el entero se abre por calidad (A-B y C) y la cola
        además por canal (directa y sobrante).
        """
        self.ensure_one()
        lineas = self.line_ids.filtered(
            lambda l: l.size_grade_id.presentation == presentation)
        if not lineas:
            return {}

        columnas = []
        for linea in lineas.sorted(key=lambda l: (l.channel or "", l.quality)):
            clave = (linea.channel or "", linea.quality)
            if clave not in [c[0] for c in columnas]:
                columnas.append((clave, linea.etiqueta_columna()))

        precios = {}
        for linea in lineas:
            precios.setdefault(linea.size_grade_id.id, {})[
                (linea.channel or "", linea.quality)] = linea.price

        tallas = lineas.mapped("size_grade_id").sorted(
            key=lambda t: (t.sequence, t.name))
        return {
            "tallas": tallas,
            "columnas": columnas,
            "precios": precios,
            "uom": lineas[0].uom,
        }

    @api.model
    def combinaciones_disponibles(self, partner):
        """Las columnas que existen entre todas las listas que ve el productor.

        No se puede fijar un juego de columnas: cada empacadora cotiza lo suyo
        —una abre el sobrante en A y B, otra tiene una sola columna— así que
        las opciones del comparador salen de los datos.
        """
        listas = self.visibles_para(partner)
        vistas, salida = set(), []
        for linea in listas.mapped("line_ids").sorted(
                key=lambda l: (l.presentation, l.channel or "", l.quality)):
            clave = (linea.presentation, linea.channel or "", linea.quality)
            if clave in vistas:
                continue
            vistas.add(clave)
            pres = "Entero" if linea.presentation == "entero" else "Cola"
            salida.append({
                "clave": "%s|%s|%s" % clave,
                "presentation": linea.presentation,
                "channel": linea.channel or "",
                "quality": linea.quality,
                "etiqueta": "%s · %s" % (pres, linea.etiqueta_columna()),
                "uom": linea.uom,
            })
        return salida

    @api.model
    def empacadoras_con_lista(self, partner):
        """Las empacadoras que hoy le tienen una lista vigente a este partner.

        Sale de las listas y no del catálogo de empacadoras: en el filtro del
        comparador solo tiene sentido ofrecer a quien realmente le mandó
        precios. Ofrecer una empacadora sin lista sería una casilla que no
        cambia nada.
        """
        listas = self.visibles_para(partner)
        return listas.mapped("issuer_partner_id").sorted(key=lambda p: p.name or "")

    @api.model
    def comparativa(self, partner, presentation, channel, quality, cantidad=0.0,
                    emisores=None):
        """Qué paga cada empacadora por la misma talla.

        Es la cuenta que el camaronero hace hoy a mano con dos papeles sobre la
        mesa. Devuelve las filas por talla con el precio de cada lista, cuál es
        el mejor y cuánto se pierde eligiendo el segundo.
        """
        listas = self.visibles_para(partner)
        if emisores:
            # Filtro del usuario: comparar solo contra las empacadoras que él
            # eligió. Se aplica sobre lo que ya puede ver, nunca amplía.
            listas = listas.filtered(lambda l: l.issuer_partner_id.id in emisores)
        if not listas:
            return {}

        lineas = listas.mapped("line_ids").filtered(
            lambda l: l.presentation == presentation
            and (l.channel or "") == (channel or "")
            and l.quality == quality)
        if not lineas:
            return {}

        # Solo entran las empacadoras que cotizan esta combinación: una columna
        # entera vacía no aporta y estorba para leer.
        listas_con_datos = lineas.mapped("price_list_id")
        precios = {}
        for linea in lineas:
            precios.setdefault(linea.size_grade_id.id, {})[linea.price_list_id.id] = linea.price

        filas = []
        for talla in lineas.mapped("size_grade_id").sorted(key=lambda t: (t.sequence, t.name)):
            porlista = precios.get(talla.id, {})
            valores = sorted(porlista.values(), reverse=True)
            mejor = valores[0] if valores else 0.0
            segundo = valores[1] if len(valores) > 1 else None
            ganadores = [lid for lid, p in porlista.items() if p == mejor]
            filas.append({
                "talla": talla,
                "precios": porlista,
                "mejor": mejor,
                "ganadores": ganadores,
                # La ventaja solo tiene sentido si hay con quién comparar y no
                # hay empate: si empatan, elegir da igual.
                "ventaja": (mejor - segundo) if (segundo is not None and mejor > segundo) else 0.0,
                # Que solo una cotice no es un empate: no hay con quién comparar.
                # Decirle "empate" al camaronero lo llevaría a creer que da igual.
                "solo_uno": len(porlista) == 1,
                "diferencia_total": ((mejor - segundo) * cantidad)
                if (segundo is not None and cantidad) else 0.0,
                "total_mejor": mejor * cantidad if cantidad else 0.0,
            })

        uoms = set(lineas.mapped("uom"))
        return {
            "listas": listas_con_datos.sorted(key=lambda l: l.issuer_partner_id.name or ""),
            "filas": filas,
            "uom": list(uoms)[0] if len(uoms) == 1 else "",
            # Si dos empacadoras cotizan la misma talla en unidades distintas,
            # compararlas de frente sería un error de 2,2 veces. Se avisa.
            "uom_mixta": len(uoms) > 1,
            "cantidad": cantidad,
        }

    # ==================================================================
    # Carga desde Excel
    # ==================================================================
    # Es el camino de entrada de verdad: en el sector la lista se arma en
    # Excel y se manda por correo o WhatsApp. Pedirle a una empacadora que
    # teclee sesenta precios en un formulario web es pedirle que no lo use.
    #
    # La plantilla es una matriz por presentación, como el papel que ya
    # manejan. Las columnas están fijas y una celda vacía significa "no lo
    # cotizo": así entran tanto AQUAGOLD, que llena las cuatro columnas de
    # cola, como ECUAMARISCO, que deja sobrante B en blanco.
    COLUMNAS_ENTERO = [("", "ab", "A - B"), ("", "c", "C")]
    COLUMNAS_COLA = [
        ("directa", "a", "Directa A"), ("directa", "b", "Directa B"),
        ("sobrante", "a", "Sobrante A"), ("sobrante", "b", "Sobrante B"),
    ]

    def _columnas(self, presentation):
        return self.COLUMNAS_ENTERO if presentation == "entero" else self.COLUMNAS_COLA

    def plantilla_excel(self):
        """Genera la plantilla con las tallas y los precios que ya tenga cargados.

        Se rellena con lo actual a propósito: la forma real de trabajar es
        partir de la semana anterior y mover tres o cuatro renglones.
        """
        self.ensure_one()
        import io as _io
        import xlsxwriter

        buf = _io.BytesIO()
        libro = xlsxwriter.Workbook(buf, {"in_memory": True})
        titulo = libro.add_format({
            "bold": True, "font_color": "#FFFFFF", "bg_color": "#123E5C",
            "align": "center", "valign": "vcenter", "border": 1})
        talla_fmt = libro.add_format({"bold": True, "bg_color": "#F2F7F9", "border": 1})
        precio_fmt = libro.add_format({"num_format": "0.00", "border": 1})
        nota_fmt = libro.add_format({"italic": True, "font_color": "#5A6B74"})

        Talla = self.env["shrimp.size.grade"].sudo()
        for presentation, hoja_nombre, unidad in (
                ("entero", "ENTERO", "Kg"), ("cola", "COLA", "Lb")):
            hoja = libro.add_worksheet(hoja_nombre)
            columnas = self._columnas(presentation)
            hoja.write(0, 0, "Deja en blanco lo que no compres. No cambies "
                             "los títulos ni el orden de las columnas.", nota_fmt)
            hoja.write(1, 0, "Talla", titulo)
            for i, (_can, _cal, etiqueta) in enumerate(columnas):
                hoja.write(1, i + 1, "%s ($/%s)" % (etiqueta, unidad), titulo)
            hoja.set_column(0, 0, 14)
            hoja.set_column(1, len(columnas), 18)

            actuales = {}
            for linea in self.line_ids.filtered(lambda l: l.presentation == presentation):
                actuales[(linea.size_grade_id.id, linea.channel or "", linea.quality)] = linea.price

            tallas = Talla.search([("presentation", "=", presentation), ("active", "=", True)],
                                  order="sequence, name")
            for fila, talla in enumerate(tallas, start=2):
                hoja.write(fila, 0, talla.name, talla_fmt)
                for i, (canal, calidad, _e) in enumerate(columnas):
                    valor = actuales.get((talla.id, canal, calidad))
                    hoja.write(fila, i + 1, valor if valor else None, precio_fmt)

        # Bonificaciones: van aparte porque no dependen de la talla
        hoja = libro.add_worksheet("BONIFICACIONES")
        hoja.write(0, 0, "Concepto", titulo)
        hoja.write(0, 1, "Importe", titulo)
        hoja.set_column(0, 0, 20)
        hoja.set_column(1, 1, 14)
        for fila, bono in enumerate(self.bonus_ids, start=1):
            hoja.write(fila, 0, bono.name)
            hoja.write(fila, 1, bono.amount, precio_fmt)
        if not self.bonus_ids:
            for fila, ejemplo in enumerate(("SMALL", "MEDIUM", "LARGE"), start=1):
                hoja.write(fila, 0, ejemplo)
                hoja.write(fila, 1, None, precio_fmt)

        libro.close()
        return buf.getvalue()

    def cargar_excel(self, contenido):
        """Lee la plantilla y reemplaza los precios. Devuelve (resumen, errores).

        Si hay un solo error no se carga nada. Una lista a medias es peor que
        ninguna: el productor creería que está viendo el precio completo
        cuando le falta media tabla.
        """
        self.ensure_one()
        import io as _io
        import openpyxl

        try:
            libro = openpyxl.load_workbook(_io.BytesIO(contenido), data_only=True)
        except Exception:
            return None, [_("No se pudo leer el archivo. ¿Es un .xlsx sin proteger?")]

        Talla = self.env["shrimp.size.grade"].sudo()
        errores, nuevos, bonos = [], [], []

        for presentation, hoja_nombre, unidad in (
                ("entero", "ENTERO", "kg"), ("cola", "COLA", "lb")):
            if hoja_nombre not in libro.sheetnames:
                continue
            hoja = libro[hoja_nombre]
            columnas = self._columnas(presentation)
            for nfila, fila in enumerate(hoja.iter_rows(min_row=3, values_only=True), start=3):
                if not fila or not fila[0]:
                    continue
                nombre = str(fila[0]).strip()
                talla = Talla.search(
                    [("name", "=", nombre), ("presentation", "=", presentation)], limit=1)
                if not talla:
                    errores.append(_("%(hoja)s fila %(fila)s: la talla «%(t)s» no existe.") % {
                        "hoja": hoja_nombre, "fila": nfila, "t": nombre})
                    continue
                for i, (canal, calidad, etiqueta) in enumerate(columnas):
                    if i + 1 >= len(fila):
                        continue
                    bruto = fila[i + 1]
                    if bruto in (None, ""):
                        continue
                    try:
                        precio = float(str(bruto).replace(",", "."))
                    except (TypeError, ValueError):
                        errores.append(
                            _("%(hoja)s fila %(fila)s, %(col)s: «%(v)s» no es un número.") % {
                                "hoja": hoja_nombre, "fila": nfila,
                                "col": etiqueta, "v": bruto})
                        continue
                    if precio < 0:
                        errores.append(_("%(hoja)s fila %(fila)s, %(col)s: precio negativo.") % {
                            "hoja": hoja_nombre, "fila": nfila, "col": etiqueta})
                        continue
                    nuevos.append({
                        "price_list_id": self.id,
                        "size_grade_id": talla.id,
                        "channel": canal or False,
                        "quality": calidad,
                        "uom": unidad,
                        "price": precio,
                    })

        if "BONIFICACIONES" in libro.sheetnames:
            for nfila, fila in enumerate(
                    libro["BONIFICACIONES"].iter_rows(min_row=2, values_only=True), start=2):
                if not fila or not fila[0]:
                    continue
                try:
                    importe = float(str(fila[1] if len(fila) > 1 and fila[1] is not None else 0)
                                    .replace(",", "."))
                except (TypeError, ValueError):
                    errores.append(_("BONIFICACIONES fila %s: el importe no es un número.") % nfila)
                    continue
                bonos.append({"price_list_id": self.id,
                              "name": str(fila[0]).strip(), "amount": importe})

        if errores:
            return None, errores
        if not nuevos:
            return None, [_("El archivo no traía ningún precio. Revisa que hayas "
                            "llenado las hojas ENTERO o COLA.")]

        # Qué cambia respecto de lo que ya había: es lo que la empacadora
        # quiere revisar antes de publicar.
        antes = {(l.size_grade_id.id, l.channel or "", l.quality): l.price
                 for l in self.line_ids}
        despues = {(v["size_grade_id"], v["channel"] or "", v["quality"]): v["price"]
                   for v in nuevos}
        resumen = {
            "total": len(nuevos),
            "nuevos": len([k for k in despues if k not in antes]),
            "cambiados": len([k for k in despues if k in antes and antes[k] != despues[k]]),
            "quitados": len([k for k in antes if k not in despues]),
            "bonos": len(bonos),
        }

        self.line_ids.unlink()
        self.env["shrimp.price.list.line"].sudo().create(nuevos)
        if bonos:
            self.bonus_ids.unlink()
            self.env["shrimp.price.list.bonus"].sudo().create(bonos)
        return resumen, []

    @api.model
    def resumen_mejor_precio(self, partner):
        """Resumen para el reporte de la camaronera: quién paga mejor cada talla.

        Toma la combinación principal —la cola directa A, que es el producto que
        más se mueve— y devuelve el ganador de cada talla. Es de solo consultar:
        para cambiar de combinación o meter cantidades está el comparador.
        """
        combos = self.combinaciones_disponibles(partner)
        if not combos:
            return {}
        sel = combos[0]
        datos = self.comparativa(partner, sel["presentation"], sel["channel"],
                                 sel["quality"])
        if not datos.get("filas"):
            return {}

        # Cuántas tallas gana cada empacadora: dice de un vistazo con quién
        # conviene trabajar en general, más allá de talla por talla.
        marcador = {}
        for fila in datos["filas"]:
            if fila["solo_uno"]:
                continue
            for lid in fila["ganadores"]:
                marcador[lid] = marcador.get(lid, 0) + 1
        podio = sorted(
            ({"lista": l, "gana": marcador.get(l.id, 0)} for l in datos["listas"]),
            key=lambda d: -d["gana"])

        return {
            "sel": sel,
            "combos": len(combos),
            "listas": datos["listas"],
            "filas": datos["filas"],
            "uom": datos["uom"],
            "podio": podio,
            "comparables": len([f for f in datos["filas"] if not f["solo_uno"]]),
        }

    def texto_ventana(self):
        """La vigencia en una línea, como la escriben en las listas reales."""
        self.ensure_one()
        def f(d):
            return d.strftime("%d/%m/%Y") if d else ""
        if self.dispatch_from and not self.open_ended and self.dispatch_to:
            return _("Despacho del %(desde)s al %(hasta)s") % {
                "desde": f(self.dispatch_from), "hasta": f(self.dispatch_to)}
        if self.dispatch_from:
            return _("Despacho desde el %s, hasta segunda orden") % f(self.dispatch_from)
        return _("Vigente hasta segunda orden")

    def texto_pago(self):
        self.ensure_one()
        partes = []
        total = self.advance_pct and self.advance_pct >= 100
        if self.advance_pct:
            # Con el 100 % no es un "anticipo", es el pago completo: llamarlo
            # anticipo y anunciar un saldo que no existe confunde al productor.
            t = _("Pago del 100 % del valor") if total else (
                _("Anticipo del %s%%") % ("{:.0f}".format(self.advance_pct)))
            if self.advance_days:
                t += _(", a cancelar dentro de %s días calendarios") % self.advance_days
            partes.append(t)
        if self.balance_days and not total:
            partes.append(_(
                "Saldo pendiente a liquidar dentro de %s días calendarios desde "
                "la recepción de la factura") % self.balance_days)
        if self.payment_notes:
            partes.append(self.payment_notes)
        return partes


class ShrimpPriceListLine(models.Model):
    """Un renglón de la matriz: talla × canal × calidad = precio."""

    _name = "shrimp.price.list.line"
    _description = "Precio por talla"
    _order = "price_list_id, size_grade_id"

    price_list_id = fields.Many2one(
        "shrimp.price.list", string="Lista", required=True,
        ondelete="cascade", index=True)
    currency_id = fields.Many2one(
        related="price_list_id.currency_id", readonly=True)

    size_grade_id = fields.Many2one(
        "shrimp.size.grade", string="Talla", required=True,
        ondelete="restrict", index=True)
    presentation = fields.Selection(
        related="size_grade_id.presentation", string="Presentación",
        store=True, readonly=True)

    # El canal solo aplica a la cola. En las listas del sector la misma talla
    # vale muy distinto según entre directa o como sobrante de clasificar el
    # entero: en la lista de AQUAGOLD, la U/12 directa A son $3,00 y la
    # sobrante A $1,75.
    channel = fields.Selection(
        [("directa", "Directa"), ("sobrante", "Sobrante")],
        string="Canal",
        help="Solo para cola. Directa: el lote se procesa como cola desde el "
             "inicio. Sobrante: lo que queda tras clasificar el entero.")

    quality = fields.Selection(
        [("ab", "A - B"), ("a", "A"), ("b", "B"), ("c", "C")],
        string="Calidad", required=True, default="a")

    # El entero se cotiza por kilo y la cola por libra. Mezclarlas es un error
    # de 2,2 veces, así que la unidad viaja en cada renglón.
    uom = fields.Selection(
        [("kg", "$ / Kg"), ("lb", "$ / Lb")],
        string="Unidad", required=True, default="lb")

    price = fields.Monetary(string="Precio", required=True, currency_field="currency_id")

    _uniq_renglon = models.Constraint(
        "UNIQUE(price_list_id, size_grade_id, channel, quality)",
        "Esa combinación de talla, canal y calidad ya está en la lista.",
    )

    @api.constrains("price")
    def _check_price(self):
        for rec in self:
            if rec.price < 0:
                raise ValidationError(_("El precio no puede ser negativo."))

    @api.constrains("channel", "size_grade_id")
    def _check_channel(self):
        for rec in self:
            if rec.presentation == "entero" and rec.channel:
                raise ValidationError(_(
                    "El canal directa/sobrante solo tiene sentido en cola: el "
                    "entero no se clasifica así."))
            if rec.presentation == "cola" and not rec.channel:
                raise ValidationError(_(
                    "En cola hay que indicar si el precio es directa o sobrante."))

    @api.onchange("size_grade_id")
    def _onchange_size_grade(self):
        """Los valores por defecto de cada presentación, como en las listas
        reales: el entero por kilo y calidad A-B, la cola por libra y directa."""
        if self.presentation == "entero":
            self.uom = "kg"
            self.channel = False
            if self.quality not in ("ab", "c"):
                self.quality = "ab"
        elif self.presentation == "cola":
            self.uom = "lb"
            if not self.channel:
                self.channel = "directa"
            if self.quality not in ("a", "b"):
                self.quality = "a"

    def etiqueta_columna(self):
        self.ensure_one()
        cal = dict(self._fields["quality"]._description_selection(self.env))
        if self.channel:
            can = dict(self._fields["channel"]._description_selection(self.env))
            return "%s %s" % (can.get(self.channel, ""), cal.get(self.quality, ""))
        return cal.get(self.quality, "")


class ShrimpPriceListBonus(models.Model):
    """Bonificación que se suma al precio del renglón.

    En las listas del sector van en una tabla aparte al pie: SMALL, MEDIUM,
    LARGE, LOCAL, ROJO. No dependen de la talla sino de una condición del lote.
    """

    _name = "shrimp.price.list.bonus"
    _description = "Bonificación de la lista de precios"
    _order = "price_list_id, sequence, id"

    price_list_id = fields.Many2one(
        "shrimp.price.list", string="Lista", required=True,
        ondelete="cascade", index=True)
    currency_id = fields.Many2one(
        related="price_list_id.currency_id", readonly=True)

    name = fields.Char(string="Concepto", required=True)
    amount = fields.Monetary(string="Importe", currency_field="currency_id")
    note = fields.Char(string="Detalle")
    sequence = fields.Integer(default=10)
