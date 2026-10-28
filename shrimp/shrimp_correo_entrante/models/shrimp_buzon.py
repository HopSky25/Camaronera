import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.mail import email_normalize

_logger = logging.getLogger(__name__)


class ShrimpBuzon(models.Model):
    """Una dirección @camaronmarket.com compartida (info@, facturacion@...).

    Quien está en `user_ids` lee lo que llega a esa dirección y puede escribir
    en su nombre. Los administradores del correo ven todos los buzones."""

    _name = "shrimp.buzon"
    _description = "Buzón de correo"
    _order = "sequence, name"

    name = fields.Char("Nombre", required=True)
    direccion = fields.Char("Dirección", required=True,
                            help="Correo completo del buzón, p. ej. info@camaronmarket.com")
    nombre_remitente = fields.Char(
        "Nombre del remitente", default=lambda self: self.env.company.name,
        help="Lo que ve quien recibe el correo junto a la dirección, en «De».")
    user_ids = fields.Many2many("res.users", "shrimp_buzon_res_users_rel", "buzon_id", "user_id",
                                string="Usuarios", domain=[("share", "=", False)],
                                help="Quienes leen este buzón y envían desde él.")
    firma = fields.Html("Firma", sanitize_style=True)
    color = fields.Integer("Color")
    sequence = fields.Integer("Secuencia", default=10)
    active = fields.Boolean("Activo", default=True)
    correo_ids = fields.One2many("shrimp.correo", "buzon_id", string="Correos")
    no_leidos = fields.Integer("No leídos", compute="_compute_no_leidos")
    total_entrada = fields.Integer("En la bandeja de entrada", compute="_compute_no_leidos")

    _direccion_uniq = models.Constraint("UNIQUE(direccion)", "Ya existe un buzón con esa dirección.")

    def _compute_no_leidos(self):
        Correo = self.env["shrimp.correo"]
        no_leidos = dict(Correo._read_group(
            [("buzon_id", "in", self.ids), ("carpeta", "=", "entrada"), ("leido", "=", False)],
            ["buzon_id"], ["__count"]))
        totales = dict(Correo._read_group(
            [("buzon_id", "in", self.ids), ("carpeta", "=", "entrada")], ["buzon_id"], ["__count"]))
        for buzon in self:
            buzon.no_leidos = no_leidos.get(buzon, 0)
            buzon.total_entrada = totales.get(buzon, 0)

    @api.constrains("direccion")
    def _check_direccion(self):
        for buzon in self:
            if not email_normalize(buzon.direccion or ""):
                raise ValidationError(self.env._("«%s» no es una dirección de correo válida.",
                                                 buzon.direccion))

    @api.model
    def _normalizar_vals(self, vals):
        if vals.get("direccion"):
            vals["direccion"] = (email_normalize(vals["direccion"]) or vals["direccion"]).strip().lower()
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        buzones = super().create([self._normalizar_vals(dict(v)) for v in vals_list])
        buzones._dar_grupo_a_usuarios()
        return buzones

    def write(self, vals):
        res = super().write(self._normalizar_vals(dict(vals)))
        if "user_ids" in vals:
            self._dar_grupo_a_usuarios()
        return res

    def _dar_grupo_a_usuarios(self):
        """Quien recibe un buzón necesita además el grupo para ver el menú."""
        grupo = self.env.ref("shrimp_correo_entrante.group_correo_usuario", raise_if_not_found=False)
        usuarios = self.sudo().user_ids
        if grupo and usuarios:
            faltan = usuarios.filtered(lambda u: grupo not in u.all_group_ids)
            if faltan:
                grupo.sudo().write({"user_ids": [(4, u.id) for u in faltan]})

    def _puede_usar(self, user=None):
        """True si `user` (el actual por defecto) lee y envía desde este buzón."""
        self.ensure_one()
        user = user or self.env.user
        if user.has_group("shrimp_correo_entrante.group_correo_admin"):
            return True
        return user in self.sudo().user_ids

    @api.model
    def _buzones_permitidos(self, user=None):
        """Buzones activos que el usuario puede usar para escribir."""
        user = user or self.env.user
        todos = self.sudo().search([])
        if user.has_group("shrimp_correo_entrante.group_correo_admin"):
            return todos
        return todos.filtered(lambda b: user in b.user_ids)

    def _buzon_predeterminado(self, user=None):
        """Buzón con el que el usuario escribe por defecto: el que eligió en su
        ficha si todavía puede usarlo; si no, el primero de los suyos."""
        user = user or self.env.user
        permitidos = self._buzones_permitidos(user)
        elegido = user.sudo().shrimp_buzon_predeterminado_id
        return elegido if elegido in permitidos else permitidos[:1]

    def action_ver_bandeja(self):
        """Abre la bandeja de entrada filtrada por este buzón."""
        self.ensure_one()
        self.check_access("read")
        accion = self.env["ir.actions.act_window"]._for_xml_id(
            "shrimp_correo_entrante.action_correo_entrada")
        accion["context"] = {
            "searchpanel_default_carpeta": "entrada",
            "searchpanel_default_buzon_id": self.id,
            "shrimp_correo_marcar_leido": True,
            "shrimp_accion": "shrimp_correo_entrante.action_correo_entrada",
        }
        return accion

    @api.model
    def _shrimp_asignar_usuarios_por_login(self, mapping, reemplazar=False):
        """Asigna usuarios a los buzones desde un shell de Odoo.

        mapping: {buzón: [login, ...]}, donde el buzón es la dirección completa
        («info@camaronmarket.com») o solo la parte de antes de la arroba
        («info»). Con reemplazar=True, el buzón queda solo con esos usuarios;
        si no, se agregan a los que ya tenga.

        Uso (en el servidor, nunca desde el navegador):
            env["shrimp.buzon"]._shrimp_asignar_usuarios_por_login({
                "gerencia_general": ["usuario1@empresa.com"],
                "info": ["usuario1@empresa.com", "usuario2@empresa.com"],
            })
            env.cr.commit()

        Devuelve {"asignados": {dirección: [logins]}, "faltan_logins": [...],
        "faltan_buzones": [...]} para revisar el resultado en la consola."""
        Buzon = self.sudo().with_context(active_test=False)
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        resultado = {"asignados": {}, "faltan_logins": [], "faltan_buzones": []}
        for clave, logins in (mapping or {}).items():
            clave = (clave or "").strip().lower()
            if "@" in clave:
                buzon = Buzon.search([("direccion", "=", clave)], limit=1)
            else:
                buzon = Buzon.search([("direccion", "=like", clave + "@%")], limit=1)
            if not buzon:
                resultado["faltan_buzones"].append(clave)
                continue
            if isinstance(logins, str):
                logins = [logins]
            usuarios = Users.browse()
            for login in logins:
                usuario = Users.search([("login", "=", login)], limit=1)
                if not usuario:
                    resultado["faltan_logins"].append(login)
                elif usuario.share:
                    raise UserError(self.env._("%s es un usuario del portal: no puede tener buzón.", login))
                else:
                    usuarios |= usuario
            if reemplazar:
                buzon.write({"user_ids": [(6, 0, usuarios.ids)]})
            elif usuarios:
                buzon.write({"user_ids": [(4, u.id) for u in usuarios]})
            resultado["asignados"][buzon.direccion] = buzon.user_ids.mapped("login")
        _logger.info("Buzones asignados: %s", resultado)
        return resultado
