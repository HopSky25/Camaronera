from . import product_portal
from . import marketplace
from . import transaction_portal
from . import account_portal
from . import export_portal
# La API externa (/api/v1) vive ahora en el módulo shrimp_api. controllers/api.py
# ya no se carga: sus rutas chocarían con las nuevas y comparaba claves en claro.
# from . import api
# Fase 1 larva/nauplio: Mi panel, filtros de larva y ficha de calidad.
from . import dashboard
from . import larva_marketplace
from . import quality_portal
# «Mover a otro perfil» (transferencia interna entre perfiles de la cuenta).
from . import profile_transfer_portal
