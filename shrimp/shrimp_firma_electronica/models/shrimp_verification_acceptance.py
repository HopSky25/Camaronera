from odoo import _, models
from odoo.exceptions import UserError

MENSAJE_BLOQUEO = (
    "La plataforma exige que el informe esté firmado electrónicamente por la "
    "verificadora antes de aceptarlo. Todavía está pendiente de firma: en "
    "cuanto se firme podrás responder (el plazo empieza a correr desde la firma).")


class ShrimpVerificationAcceptance(models.Model):
    _inherit = "shrimp.verification.acceptance"

    def _firma_bloqueadas(self):
        return self.filtered(lambda a: a.verification_id.firma_bloquea_aceptacion())

    def action_accept(self, reason=None, auto=False, actor=None):
        bloqueadas = self._firma_bloqueadas()
        if bloqueadas:
            if not auto:
                raise UserError(_(MENSAJE_BLOQUEO))
            # El cron no da por aceptado por vencimiento un informe que las
            # partes no podían aceptar: espera a la firma (que reabre el plazo).
            resto = self - bloqueadas
            if not resto:
                return True
            return super(ShrimpVerificationAcceptance, resto).action_accept(
                reason=reason, auto=auto, actor=actor)
        return super().action_accept(reason=reason, auto=auto, actor=actor)

    def action_counter(self, price, reason=None, actor=None):
        # La contraoferta es una aceptación a otro precio: misma regla.
        if self._firma_bloqueadas():
            raise UserError(_(MENSAJE_BLOQUEO))
        return super().action_counter(price, reason=reason, actor=actor)
