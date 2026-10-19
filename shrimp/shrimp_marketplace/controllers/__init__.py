from . import product_portal
from . import marketplace
from . import transaction_portal
from . import account_portal
from . import export_portal
# La API externa (/api/v1) vive en el módulo shrimp_api. El antiguo
# controllers/api.py se eliminó (D-32, auditoría de procesos): no se cargaba,
# sus rutas estáticas habrían tapado a las nuevas, comparaba claves en claro y
# asignaba seller_role «laboratorio» a las camaroneras.
# Fase 1 larva/nauplio: Mi panel, filtros de larva y ficha de calidad.
from . import dashboard
from . import larva_marketplace
from . import quality_portal
# «Mover a otro perfil» (transferencia interna entre perfiles de la cuenta).
from . import profile_transfer_portal
# Paso «Instalaciones» del asistente de alta (19.0.1.21.0).
from . import signup_facilities
# Retraso de entrega: aviso del vendedor, respuesta del comprador, cancelación
# sin costo; strikes en el catálogo (19.0.1.26.0).
from . import delay_portal
# «Instalaciones y piscinas» en Mi cuenta: permisos, archivar y vuelta a
# Mi cuenta para las rutas de /marketplace/my-facilities (19.0.1.28.0).
from . import my_account_facilities
# «Estado real del lote»: observaciones del vendedor (19.0.1.30.0).
from . import evolution_portal
from . import cancel_portal
