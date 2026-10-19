from odoo import api, fields, models, _
from odoo.exceptions import UserError

class AccountJournal(models.Model):
    _inherit='account.journal'
    # La entidad y el punto de emisión nativos de l10n_ec (l10n_ec_entity / l10n_ec_emission)
    # se toman del punto de emisión SRI: se eligen de las tablas, no se escriben a mano.
    ec_sri_point_id=fields.Many2one('ec.sri.point',string='Punto de emisión SRI',check_company=True,
        domain="[('company_id','=',company_id)]",
        help='Las facturas de este diario proponen este punto. Su establecimiento y punto de emisión '
             'llenan la entidad y el punto de emisión del diario.')
    ec_sri_establishment_id=fields.Many2one(related='ec_sri_point_id.establishment_id',string='Sucursal SRI')

    def _ec_sri_point_codes(self,vals):
        point=self.env['ec.sri.point'].browse(vals['ec_sri_point_id']) if 'ec_sri_point_id' in vals else self.ec_sri_point_id
        return dict(l10n_ec_entity=point.establishment,l10n_ec_emission=point.emission) if point else {}

    @api.onchange('ec_sri_point_id')
    def _onchange_ec_sri_point_id(self):
        if self.ec_sri_point_id:
            self.l10n_ec_entity=self.ec_sri_point_id.establishment
            self.l10n_ec_emission=self.ec_sri_point_id.emission

    @api.model_create_multi
    def create(self,vals_list):
        for vals in vals_list:
            if vals.get('ec_sri_point_id'): vals.update(self._ec_sri_point_codes(vals))
        return super().create(vals_list)

    def write(self,vals):
        if 'ec_sri_point_id' not in vals and {'l10n_ec_entity','l10n_ec_emission'}&set(vals):
            for journal in self.filtered('ec_sri_point_id'):
                if (vals.get('l10n_ec_entity',journal.l10n_ec_entity),vals.get('l10n_ec_emission',journal.l10n_ec_emission)) != \
                        (journal.ec_sri_point_id.establishment,journal.ec_sri_point_id.emission):
                    raise UserError(_('El diario %s toma la entidad y el punto de emisión de su punto de emisión SRI. '
                        'Cambie el punto de emisión SRI en lugar de escribirlos.',journal.display_name))
        if vals.get('ec_sri_point_id'):
            vals=dict(vals,**self._ec_sri_point_codes(vals))
        return super().write(vals)
