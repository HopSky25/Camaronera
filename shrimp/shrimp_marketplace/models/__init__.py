from . import shrimp_selection
from . import shrimp_settings
from . import shrimp_notify_mixin
from . import shrimp_signoff_mixin
from . import shrimp_signoff_event
from . import shrimp_uom
from . import shrimp_size_grade
from . import shrimp_species
from . import shrimp_stage
from . import shrimp_genetics_line
from . import shrimp_partner_facility
from . import shrimp_partner_pond
from . import shrimp_lot_allocation
from . import shrimp_product_evolution
from . import shrimp_product
from . import shrimp_transaction
# Recepción por el comprador (con confirmación automática) y avisos de la
# fecha de entrega.
from . import shrimp_transaction_reception
# Aviso de retraso de entrega con negociación de una ronda y cancelación sin
# costo; strikes del vendedor (19.0.1.26.0).
from . import shrimp_transaction_delay
# Comprobante de compra y pantalla «¡Compra realizada!» (solo lectura).
from . import shrimp_purchase_receipt
from . import shrimp_stock_lot
from . import shrimp_stock_move
from . import shrimp_export
from . import res_partner
from . import shrimp_product_certificate_line
from . import shrimp_check_request
from . import shrimp_review
from . import shrimp_charge
from . import res_config_settings
# Ajustes de retrasos de entrega y strikes (campos nuevos en res.config.settings).
from . import res_config_settings_delay
from . import res_config_settings_lopdp
from . import shrimp_api_key
from . import website



# Fase 1 larva/nauplio: tipos de producto, ficha de calidad, «Avísame» y Mi panel.
from . import shrimp_larva
from . import shrimp_product_quality
from . import shrimp_larva_interest
from . import shrimp_dashboard
# Mi panel: próximas entregas, atrasadas y recepciones por confirmar.
from . import shrimp_dashboard_reception
# Mi panel: retrasos por responder, strikes y «Entregas a tiempo».
from . import shrimp_dashboard_delay
# Strikes (después de shrimp_larva: amplía su catálogo de destacados).
from . import shrimp_seller_strike
# Borradores legales con las reglas de entregas, retrasos y strikes.
from . import shrimp_delay_policy
# Barra superior: tabla de entradas por capacidad (Mi panel · Marketplace ·
# Vender · Comprar · Servicios · Mi cuenta).
from . import website_navbar
# Mover producto propio entre los perfiles de la misma cuenta (transferencia
# interna; depende de lotes, movimientos, siembras y tipos de larva).
from . import shrimp_profile_transfer
from . import ir_http
# «Estado real del lote»: observaciones del vendedor, divergencia con lo
# publicado y sus ajustes (19.0.1.30.0).
from . import shrimp_product_observation
from . import shrimp_evolution_settings
from . import shrimp_legal_site
# Archivar / eliminar / reactivar producto sin stock ni reservas sin dueño
# (auditoría de procesos, hueco #9).
from . import shrimp_product_retire
# Derechos del titular (LOPDP): copia de datos y anonimización (hueco #7).
from . import res_partner_lopdp
# Cancelación general de una compra (comprador, vendedor, mutuo acuerdo,
# plataforma) y desenlaces de la incidencia (19.0.1.34.0).
from . import shrimp_transaction_cancel
from . import res_config_settings_cancel
from . import shrimp_cancel_policy
from . import shrimp_seller_strike_appeal
# Reglas de la compra de la auditoría de procesos (§10–11): anulación de la
# comisión en toda cancelación (D-27), plazo, recordatorios y bloqueo de la
# factura del vendedor (D-43) y borradores legales de esas reglas.
from . import shrimp_purchase_rules
from . import res_config_settings_purchase_rules
from . import shrimp_purchase_rules_policy
# D-20: retirar publicaciones al suspender / rechazar
from . import res_partner_withdraw
# Decisiones de la auditoría de procesos (19.0.1.38.0): PCR positivo bloquea
# el lote (D-30), caducidad de publicaciones (D-31), cierre de la siembra al
# cosechar (D-49) y vencimiento de certificados (D-13).
from . import shrimp_product_pcr
from . import shrimp_product_expiry
from . import shrimp_sowing_close
from . import shrimp_certificate_expiry
from . import shrimp_decision_policy
# Fotos, certificados y cantidad de un lote: el núcleo que comparten el
# portal y la API (shrimp_api) para gestionar un lote desde la app.
from . import shrimp_product_media
# «Mis certificados» de la cuenta: núcleo compartido por el portal y la API.
from . import shrimp_account_certificates
