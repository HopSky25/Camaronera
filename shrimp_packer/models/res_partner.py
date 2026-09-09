from odoo import api, fields, models, _


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
