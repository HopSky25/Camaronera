from odoo import api, models
from odoo.http import request


class ShrimpLegalDocument(models.Model):
    _inherit = "shrimp.legal.document"

    @api.model
    def _shrimp_pending(self, user, roles=None):
        """Perfiles por sitio: en cada sitio solo se piden los anexos de los
        perfiles que se usan en él (el de la verificadora se acepta en
        CamaronMarket Verificadores; el del maquilador, en el de empaque). Los
        documentos generales se piden en todos. Sin sitio (API, cron) no se
        filtra nada."""
        if roles is None and user and not user._is_public() and not user._is_internal():
            web = getattr(request, "website", None) if request else None
            if web and "shrimp_platform" in web._fields:
                company = user.sudo()._shrimp_account_company()
                if company and company.shrimp_user_type:
                    roles = web.sudo()._shrimp_site_roles(company._shrimp_roles(include_pending=True))
        return super()._shrimp_pending(user, roles=roles)
