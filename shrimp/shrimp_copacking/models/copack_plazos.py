"""Plazos del servicio de empaque (Ajustes › CamaronMarket › Empaque).

Decisiones del usuario sobre la auditoría de procesos (D-14, D-34, D-47):

* Acta: el cliente tiene un plazo para firmar, con recordatorios antes de que
  venza; si calla, el acta se da por aceptada (como el informe de
  verificación). La disputa la media la plataforma.
* Solicitudes y ofertas vencen solas (cron), con aviso.
* La orden firmada se cierra sola.
"""

ACCEPTANCE_HOURS_PARAM = "shrimp_copacking.acceptance_hours"
ACCEPTANCE_HOURS = 48
REMINDER_1_PARAM = "shrimp_copacking.acceptance_reminder_1_hours"
REMINDER_1 = 24
REMINDER_2_PARAM = "shrimp_copacking.acceptance_reminder_2_hours"
REMINDER_2 = 4
MEDIATION_DAYS_PARAM = "shrimp_copacking.mediation_days"
MEDIATION_DAYS = 3
REQUEST_DAYS_PARAM = "shrimp_copacking.request_expiry_days"
REQUEST_DAYS = 15
OFFER_DAYS_PARAM = "shrimp_copacking.offer_expiry_days"
OFFER_DAYS = 7
AUTOCLOSE_HOURS_PARAM = "shrimp_copacking.autoclose_hours"
AUTOCLOSE_HOURS = 24


def get_int(env, key, default, minimum=0, maximum=None):
    return env["shrimp.settings"].get_int(key, default, minimum=minimum, maximum=maximum)


def acceptance_hours(env):
    return get_int(env, ACCEPTANCE_HOURS_PARAM, ACCEPTANCE_HOURS, minimum=1, maximum=24 * 60)


def reminder_hours(env):
    """Horas antes del vencimiento del acta en que se recuerda (sin 0 ni
    repetidas, de mayor a menor)."""
    horas = {get_int(env, REMINDER_1_PARAM, REMINDER_1, maximum=24 * 60),
             get_int(env, REMINDER_2_PARAM, REMINDER_2, maximum=24 * 60)}
    return sorted((h for h in horas if h > 0), reverse=True)


def mediation_days(env):
    return get_int(env, MEDIATION_DAYS_PARAM, MEDIATION_DAYS, minimum=1, maximum=90)


def request_days(env):
    return get_int(env, REQUEST_DAYS_PARAM, REQUEST_DAYS, minimum=1, maximum=365)


def offer_days(env):
    return get_int(env, OFFER_DAYS_PARAM, OFFER_DAYS, minimum=1, maximum=365)


def autoclose_hours(env):
    return get_int(env, AUTOCLOSE_HOURS_PARAM, AUTOCLOSE_HOURS, minimum=0, maximum=24 * 90)
