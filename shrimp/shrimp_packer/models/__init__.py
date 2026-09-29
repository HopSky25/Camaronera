from . import res_partner
from . import shrimp_price_list
from . import shrimp_transaction
from . import shrimp_product
from . import shrimp_verification
from . import shrimp_proveedores
from . import shrimp_simulador
from . import res_partner_avisos
from . import shrimp_lot_alert
# La reserva anticipada. Va despues de shrimp_simulador porque reutiliza su
# parser de rangos de talla para construir la escalera de escalones.
from . import shrimp_harvest_forecast
from . import shrimp_harvest_commitment
from . import shrimp_harvest_confirmation
from . import res_partner_reserva
