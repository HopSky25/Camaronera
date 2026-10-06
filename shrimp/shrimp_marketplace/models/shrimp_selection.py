"""Valores de selección compartidos por todos los módulos shrimp_*.

«Presentación» estaba definida seis veces (producto, talla, verificación,
cosecha declarada, tarifa y solicitud de empaque) con etiquetas distintas y
una de ellas con un valor que las otras no tenían. Una lista que se repite se
desincroniza; aquí vive la única.

DECISIÓN: «valor agregado» NO es global. Un lote del marketplace se vende
entero o en cola (las tallas comerciales y las listas de precios solo existen
para esas dos), mientras que el valor agregado (pelado, desvenado, cocido...)
es algo que se le hace al camarón en la planta de empaque. Por eso hay dos
listas: la base y la ampliada que usa el servicio de empaque.
"""

PRESENTATIONS = [
    ("entero", "Entero"),
    ("cola", "Cola"),
]

PRESENTATIONS_WITH_VALUE_ADDED = PRESENTATIONS + [
    ("valor_agregado", "Valor agregado"),
]


def presentation_label(value, with_value_added=True):
    lista = PRESENTATIONS_WITH_VALUE_ADDED if with_value_added else PRESENTATIONS
    return dict(lista).get(value, value or "")
