from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

# Costo anual del dinero que se supone cuando la camaronera no ha dicho el
# suyo. La referencia es la tasa activa efectiva del Banco Central del Ecuador
# para el segmento productivo —que ronda el 10-11 % anual— redondeada hacia
# arriba: el productor rara vez se financia al precio del mejor cliente
# corporativo, y el anticipo del comisionista, que es su financiación real
# cuando el banco no llega, cuesta bastante más. Es un punto de partida
# conservador, no una verdad: por eso hay una casilla en el perfil y por eso
# la pantalla marca esta cifra como supuesto mientras no la cambie.
TASA_DESCUENTO_DEFECTO = 12.0

# Tope de credibilidad. No es un límite de negocio sino un cazador de errores
# de tecleo: la confusión clásica es escribir la tasa MENSUAL en la casilla
# anual, y un 12 % mensual metido aquí como 12 anual pasaría inadvertido,
# mientras que un 150 se cuestiona.
TASA_DESCUENTO_MAX = 100.0


class ResPartner(models.Model):
    _inherit = "res.partner"

    # La empacadora es el último eslabón de la cadena: compra el camarón adulto
    # a las camaroneras, lo procesa y lo exporta. No publica productos en el
    # marketplace, solo compra, y su herramienta es la lista de precios que
    # reparte cada semana a sus proveedores.
    shrimp_user_type = fields.Selection(
        selection_add=[("empacadora", "Empacadora")],
        ondelete={"empacadora": "set null"},
    )

    # ------------------------------------------------------------------
    # Perfil de la empacadora
    # ------------------------------------------------------------------
    emp_razon_social = fields.Char(string="Razón social (Empacadora)")
    emp_representante = fields.Char(string="Representante legal")
    emp_contacto_comercial = fields.Char(
        string="Contacto comercial",
        help="Quien negocia y manda las listas de precios a los productores.")
    emp_telefono = fields.Char(string="Teléfono (Empacadora)")

    emp_codigo_exportador = fields.Char(
        string="Código de exportador",
        help="Código con el que exporta. Es lo que la identifica ante la "
             "autoridad sanitaria y en la declaración de exportación.")
    emp_planta_nombre = fields.Char(string="Planta de proceso")
    emp_planta_ubicacion = fields.Char(
        string="Ubicación de la planta",
        help="Dónde entrega el productor. Pesa tanto como el precio: llevar "
             "camarón dos horas más lejos se come la diferencia.")
    emp_capacidad_lb_dia = fields.Float(
        string="Capacidad (lb/día)",
        help="Cuánto puede procesar al día. Le dice al productor si puede "
             "recibirle una cosecha grande de una sola vez.")

    # Certificaciones: es lo primero que mira un productor serio, porque
    # determinan a qué mercados puede ir su camarón y, por tanto, cuánto vale.
    emp_cert_bap = fields.Boolean(string="BAP (Best Aquaculture Practices)")
    emp_cert_asc = fields.Boolean(string="ASC")
    emp_cert_haccp = fields.Boolean(string="HACCP")
    emp_cert_otras = fields.Char(string="Otras certificaciones")
    emp_aprobacion_sanitaria = fields.Char(
        string="N.º de aprobación sanitaria",
        help="Registro sanitario de la planta ante la autoridad competente.")

    # A qué mercados exporta. Explica su estructura de precios: quien va a Asia
    # y Europa paga mejor el entero; quien va a Norteamérica, la cola.
    emp_mercado_asia = fields.Boolean(string="Asia")
    emp_mercado_europa = fields.Boolean(string="Europa")
    emp_mercado_norteamerica = fields.Boolean(string="Norteamérica")
    emp_mercado_local = fields.Boolean(string="Mercado local")

    emp_price_list_ids = fields.One2many(
        "shrimp.price.list", "issuer_partner_id", string="Listas de precios")
    emp_price_list_count = fields.Integer(
        compute="_compute_emp_price_list_count", string="Listas publicadas")

    # ------------------------------------------------------------------
    # Código del registro acuícola
    # ------------------------------------------------------------------
    # El "GR-####" del registro de camaroneras del Ministerio de Producción.
    # Es la matrícula de la finca ante la autoridad: identifica el predio, no
    # a la empresa, y es lo que se cita en los trámites y en las guías.
    shrimp_registro_acuicola = fields.Char(
        string="Registro acuícola (GR-)",
        help="Código del registro de camaroneras, p. ej. GR-1104.")

    def _compute_emp_price_list_count(self):
        for rec in self:
            rec.emp_price_list_count = len(
                rec.emp_price_list_ids.filtered(lambda l: l.state == "published"))

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------
    def shrimp_is_empacadora(self):
        self.ensure_one()
        return self.shrimp_user_type == "empacadora"

    def shrimp_grupo_ids(self):
        """El partner y su empresa madre, si la tiene.

        Los grupos camaroneros son varias razones sociales bajo una misma
        gerencia —Grupo Burgos son al menos tres camaroneras con el mismo
        teléfono— y la empacadora les manda una sola lista al grupo. Para
        resolver la visibilidad hay que mirar hacia arriba en la jerarquía.
        """
        self.ensure_one()
        ids = [self.id]
        padre = self.parent_id
        while padre and padre.id not in ids:
            ids.append(padre.id)
            padre = padre.parent_id
        return ids

    @api.model
    def empacadoras_activas(self):
        return self.sudo().search(
            [("shrimp_user_type", "=", "empacadora"), ("active", "=", True)],
            order="name")

    # El historial verificado es de la camaronera, no del sistema. Publicarlo
    # sin preguntarle convierte una herramienta de venta en una amenaza: la que
    # rinde bien quiere presumirlo, pero a la que rinde flojo le estariamos
    # publicando su peor numero delante de todos sus compradores, y lo racional
    # entonces es no usar CamaronMkt. Por defecto apagado: que lo encienda quien
    # quiera usarlo para vender.
    farm_publicar_historial = fields.Boolean(
        string="Publicar mi rendimiento verificado",
        help="Muestra tu rendimiento medio y tu clase A en la ficha de los "
             "lotes que publicas, para que el comprador lo vea antes de "
             "preguntarte. El detalle lote a lote nunca se publica.")

    # ------------------------------------------------------------------
    # El costo del dinero de la camaronera
    # ------------------------------------------------------------------
    # Comparar dos listas solo por el precio por libra es comparar mal: una
    # empacadora que paga 3,20 con 60 % de anticipo a 2 días puede convenir
    # más que otra que paga 3,28 a 21 días, si hay que sembrar la próxima
    # corrida. Para traer esos pagos a valor de hoy hace falta una tasa, y esa
    # tasa NO es un dato del sistema: es lo que a ELLA le cuesta el dinero
    # —su crédito bancario, el anticipo del comisionista o la siembra que deja
    # de hacer—. Por eso se le pregunta y no se calcula.
    #
    # Cero significa "no la ha fijado": entonces se usa el supuesto de
    # TASA_DESCUENTO_DEFECTO y la pantalla lo dice con todas sus letras. Un
    # número que parece exacto y descansa en una suposición escondida es peor
    # que no dar el número.
    farm_tasa_descuento_anual = fields.Float(
        string="Costo anual de tu dinero (%)", digits=(5, 2),
        help="A qué tasa anual te cuesta el dinero: el interés de tu crédito, "
             "o lo que dejas de ganar por cobrar tarde. Sirve para comparar "
             "listas con formas de pago distintas —quién paga más en el papel "
             "no siempre es quien más te deja en caja—. Si la dejas en blanco "
             "se usa un supuesto del sector y la pantalla te lo avisa.")

    @api.constrains("farm_tasa_descuento_anual")
    def _check_farm_tasa_descuento(self):
        for rec in self:
            tasa = rec.farm_tasa_descuento_anual or 0.0
            if tasa < 0:
                raise ValidationError(_(
                    "El costo de tu dinero no puede ser negativo."))
            if tasa > TASA_DESCUENTO_MAX:
                raise ValidationError(_(
                    "Una tasa anual del %(tasa)s %% no es creíble ni para el "
                    "crédito de campo. Revisa el dato: se escribe en por "
                    "ciento anual, no mensual (12 %% anual, no 12 %% al mes).",
                    tasa="{:.2f}".format(tasa)))

    @api.model
    def tasa_descuento_supuesta(self):
        """El supuesto del sector, para que la pantalla pueda nombrarlo.

        Existe para que la plantilla no tenga que repetir el número: si
        mañana cambia la referencia del Banco Central, cambia en un sitio.
        """
        return TASA_DESCUENTO_DEFECTO

    def tasa_descuento(self):
        """(tasa anual en %, si es un supuesto nuestro) de este productor.

        Se devuelve también de dónde sale la cifra porque la pantalla tiene
        que poder distinguir "tu tasa" de "la que supusimos por ti". Quien no
        la ha fijado merece ver el aviso; quien la fijó no merece que le
        pongan un aviso encima de su propio dato.
        """
        self.ensure_one()
        tasa = self.farm_tasa_descuento_anual or 0.0
        if tasa > 0:
            return tasa, False
        return TASA_DESCUENTO_DEFECTO, True
