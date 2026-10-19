"""Reglas del SRI sin dependencia de Odoo.

Fuentes: Ficha técnica de comprobantes electrónicos esquema offline 2.34 (27/07/2026),
Reglamento de Comprobantes de Venta, Retención y Documentos Complementarios, y las
resoluciones NAC-DGERCGC25-00000014 / NAC-DGERCGC25-00000017.
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_TEXT = 300            # razón social, direcciones, descripción (Anexo 3)
MAX_CODE = 25             # codigoPrincipal / codigoAuxiliar (Anexo 3)
MAX_ADDITIONAL = 15       # infoAdicional: máximo quince campos de hasta 300 caracteres (9.11)
MAX_XML_BYTES = 320*1024  # tamaño máximo de un comprobante individual (sección 7)
FINAL_CONSUMER = '9999999999999'
FINAL_CONSUMER_LIMIT = 50  # Reglamento art. 19 núm. 1 (D.E. 586) y ficha 9.10
FINAL_CONSUMER_RULES_FROM = date(2026, 1, 1)  # Res. 25-00000014 art. 3 último inciso, vigencia por la 25-00000017
RETENTION_BUSINESS_DAYS = 5  # Reglamento art. 8
# Tabla 17: codigoPorcentaje del IVA -> tarifa. El 8 (IVA diferenciado) lo fija un decreto.
IVA_RATES = {'0': 0, '2': 12, '3': 14, '4': 15, '5': 5, '6': 0, '7': 0, '10': 13}
IVA_DIFFERENTIATED = '8'
ECUADOR_TZ = ZoneInfo('America/Guayaquil')

def today_ec():
    """Fecha actual en Ecuador: la fecha de emisión se compara con este día."""
    return datetime.now(ECUADOR_TZ).date()

def text(value, label, limit=MAX_TEXT):
    value = (value or '').strip()
    if len(value) > limit:
        raise ValueError('%s supera %s caracteres (tiene %s).' % (label, limit, len(value)))
    return value

def iva_rate_matches(percentage_code, rate):
    """El código de la tabla 17 debe corresponder a la tarifa del impuesto."""
    if percentage_code == IVA_DIFFERENTIATED:
        return True
    return percentage_code in IVA_RATES and abs(float(rate) - IVA_RATES[percentage_code]) < 0.001

def _mod11(digits, coefficients):
    check = 11 - sum(int(d) * c for d, c in zip(digits, coefficients)) % 11
    return 0 if check == 11 else check

def ec_identification_ok(vat):
    """Dígito verificador de cédula o RUC con los algoritmos de uso común.

    El SRI no los publica en la ficha y solo emite la advertencia 62 ("identificación
    incorrecta"), así que el resultado sirve para advertir, nunca para bloquear."""
    if not re.fullmatch(r'\d{10}|\d{13}', vat or ''):
        return False
    province, third = int(vat[:2]), int(vat[2])
    if not (1 <= province <= 24 or province == 30):
        return False
    if third < 6:  # persona natural: módulo 10
        total = 0
        for digit, coefficient in zip(vat[:9], (2, 1, 2, 1, 2, 1, 2, 1, 2)):
            n = int(digit) * coefficient
            total += n - 9 if n > 9 else n
        return (10 - total % 10) % 10 == int(vat[9]) and (len(vat) == 10 or vat[10:] != '000')
    if len(vat) != 13:
        return False
    if third == 9:  # sociedad privada: módulo 11, verificador en la posición 10
        return _mod11(vat[:9], (4, 3, 2, 7, 6, 5, 4, 3, 2)) == int(vat[9])
    if third == 6:  # sector público: módulo 11, verificador en la posición 9
        return _mod11(vat[:8], (3, 2, 7, 6, 5, 4, 3, 2)) == int(vat[8])
    return False

def add_business_days(start, days):
    """Días hábiles de lunes a viernes; los feriados no se conocen aquí."""
    current = start
    while days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days -= 1
    return current

def cancellation_deadline(issue_date):
    """Anulación en línea hasta el día 7 del mes siguiente a la emisión (Res. 25-00000017),
    corrido al lunes si cae en fin de semana. Los feriados pueden extenderlo un poco más."""
    year, month = (issue_date.year + 1, 1) if issue_date.month == 12 else (issue_date.year, issue_date.month + 1)
    deadline = date(year, month, 7)
    while deadline.weekday() >= 5:
        deadline += timedelta(days=1)
    return deadline
