from odoo import fields, models

# Única fuente de la lista de scopes. La usan los datos XML (que solo dan
# nombre bonito a cada código), la validación de las claves, el portal y el
# generador de OpenAPI. Si un scope no está aquí, no existe.
SCOPES = [
    ("catalog:read", "Catálogos (lectura)"),
    ("profile:write", "Perfil propio (edición)"),
    ("facilities:read", "Instalaciones y piscinas (lectura)"),
    ("facilities:write", "Instalaciones y piscinas (escritura)"),
    ("products:read", "Productos / lotes publicados (lectura)"),
    ("products:write", "Productos (escritura) y solicitudes de chequeo"),
    ("lots:read", "Inventario por lote y movimientos (lectura)"),
    ("lots:write", "Asignación de lotes a piscinas"),
    ("transactions:read", "Compras, ventas, cobros y trazabilidad (lectura)"),
    ("transactions:write", "Acciones sobre compras y solicitudes de chequeo"),
    ("verifications:read", "Verificaciones en campo (lectura)"),
    ("verifications:write", "Informe de campo, veredicto y aceptación"),
    ("dispatch:write", "Seguimiento del despacho (cita y llegada)"),
    ("pricelists:read", "Listas de precios y avisos de lotes (lectura)"),
    ("pricelists:write", "Listas de precios (escritura y publicación)"),
    ("harvest:read", "Reserva anticipada de cosecha (lectura)"),
    ("harvest:write", "Reserva anticipada de cosecha (escritura)"),
    ("copack:read", "Servicio de empaque / maquila (lectura)"),
    ("copack:write", "Servicio de empaque / maquila (escritura)"),
    ("exports:read", "Salidas / exportaciones (lectura)"),
    ("exports:write", "Salidas / exportaciones (registro y anulación)"),
    ("invoices:read", "Comprobantes electrónicos SRI (lectura)"),
    ("webhooks:manage", "Gestión de webhooks"),
]
SCOPE_CODES = [code for code, _label in SCOPES]


class ShrimpApiScope(models.Model):
    _name = "shrimp.api.scope"
    _description = "Permiso (scope) de la API externa"
    _order = "sequence, code"
    _rec_name = "name"

    code = fields.Char(string="Código", required=True, index=True)
    name = fields.Char(string="Descripción", required=True)
    sequence = fields.Integer(default=10)

    _code_unique = models.Constraint("unique(code)", "El código del scope debe ser único.")
