from . import res_partner
from . import shrimp_aguaje
from . import shrimp_price_list
from . import shrimp_transaction
from . import shrimp_product
from . import shrimp_verification
from . import shrimp_proveedores
from . import res_config_settings
from . import shrimp_simulador
from . import res_partner_avisos
from . import shrimp_lot_alert
# La reserva anticipada. Va despues de shrimp_simulador porque reutiliza su
# parser de rangos de talla para construir la escalera de escalones.
from . import shrimp_harvest_forecast
from . import shrimp_harvest_commitment
from . import shrimp_harvest_confirmation
from . import res_partner_reserva
from . import shrimp_certificate
# Mi panel (/my/dashboard): listas, reservas y oferta de la empacadora y la camaronera.
from . import shrimp_dashboard_packer
# Entradas de la barra superior (Vender / Comprar) de la empacadora y la camaronera.
from . import website_navbar
from . import ir_http
# La reserva cumplida queda reservada a la empacadora comprometida, con
# plazo de compra e incumplimientos (auditoría de procesos, hueco #8).
from . import shrimp_harvest_reservation
# D-20: retirar listas de precios al suspender / rechazar
from . import res_partner_withdraw
# Lote atado a una lista archivada: pasa a la nueva lista del emisor (D-49).
from . import shrimp_price_list_relink
