# -*- coding: utf-8 -*-
"""Matriz de permisos de los subusuarios (hueco CR-12, decisión D-12).

Cada ruta del portal que ESCRIBE algo exige un permiso del usuario de la
cuenta de empresa (shrimp.account.member). Esta tabla es la fuente única: la
aplica ir.http antes de despachar (403 si falta el permiso), cada página la
usa para desactivar los formularios que el usuario no puede enviar y un test
comprueba que ninguna ruta POST de los módulos shrimp quede sin clasificar.
Las rutas se escriben como en @http.route; un módulo no instalado simplemente
no tiene esas rutas.

Permisos
    view            solo lectura (basta con entrar con el perfil)
    buy             comprar: compras, recepciones, cancelaciones y
                    contraofertas del comprador, compromisos de cosecha de la
                    empacadora, pedir empaque
    sell            vender: productos (precios, stock, publicar, archivar,
                    eliminar), traspasos entre perfiles, factura del vendedor,
                    despacho, avisos de retraso, cancelaciones del vendedor,
                    observaciones, calidad, listas de precios, declaraciones
                    de cosecha, ofertas y tarifas de empaque, salidas,
                    instalaciones y piscinas
    approve         emitir veredictos de verificación (técnicos)
    billing         facturas y cobros de la plataforma
    manage_account  ficha de la empresa, certificados, claves API, webhooks,
                    técnicos de la verificadora
    admin           solo el administrador (equipo, agregar perfiles)
    none            cualquiera de la cuenta (cambiar SU perfil, documentos
                    legales y derechos LOPDP propios...)

Alcance
    "active"  el permiso en el perfil ACTIVO del usuario (la acción crea algo
              con ese perfil)
    "any"     el permiso en alguno de sus perfiles: la acción es sobre un
              registro que trae su propio perfil (una compra, un producto) y
              el modelo / controlador vuelve a comprobar el permiso en ESE
              perfil (shrimp.transaction._shrimp_check_party,
              _shrimp_check_transfer_actor, _get_owned_product...)

Una tupla de permisos = cualquiera de ellos (acciones que hace la parte que
corresponda, compradora o vendedora; el modelo decide cuál).
"""
import re

SHRIMP_ROUTE_PERMISSIONS = {
    # ------------------------------------------------------------------
    # shrimp_user_registry
    # ------------------------------------------------------------------
    "/my/profile/activate": ("none", "active"),
    "/my/profile/add": ("admin", "active"),
    "/my/data/export": ("none", "active"),
    "/my/data/request": ("none", "active"),
    "/my/legal/accept": ("none", "active"),
    "/my/privacy/accept": ("none", "active"),
    "/my/legal/dismiss": ("none", "active"),
    "/my/team/invite": ("admin", "active"),
    "/my/team/<string:ref>/permissions": ("admin", "active"),
    "/my/team/<string:ref>/toggle": ("admin", "active"),
    "/my/team/<string:ref>/make-admin": ("admin", "active"),
    "/my/team/<string:ref>/resend": ("admin", "active"),
    # ------------------------------------------------------------------
    # shrimp_marketplace: Mi cuenta, certificados, instalaciones
    # ------------------------------------------------------------------
    "/marketplace/my-account/save": ("manage_account", "active"),
    "/marketplace/my-certificates/add": ("manage_account", "active"),
    "/marketplace/my-certificates/<line_ref>/renew": ("manage_account", "active"),
    "/marketplace/my-certificates/<line_ref>/delete": ("manage_account", "active"),
    "/marketplace/my-facilities/facility/create": ("sell", "active"),
    "/marketplace/my-facilities/facility/<facility_ref>/update": ("sell", "active"),
    "/marketplace/my-facilities/facility/<facility_ref>/delete": ("sell", "active"),
    "/marketplace/my-facilities/facility/<facility_ref>/archive": ("sell", "active"),
    "/marketplace/my-facilities/pond/create": ("sell", "active"),
    "/marketplace/my-facilities/pond/<pond_ref>/update": ("sell", "active"),
    "/marketplace/my-facilities/pond/<pond_ref>/delete": ("sell", "active"),
    "/marketplace/my-facilities/pond/<pond_ref>/archive": ("sell", "active"),
    "/marketplace/my-facilities/allocate": ("sell", "active"),
    "/marketplace/sowings/<alloc_ref>/update": ("sell", "active"),
    "/marketplace/sowings/<alloc_ref>/cancel": ("sell", "active"),
    # Solicitudes de chequeo: las aprueba / rechaza el vendedor.
    "/marketplace/check-requests/<cr_ref>/approve": ("sell", "any"),
    "/marketplace/check-requests/<cr_ref>/reject": ("sell", "any"),
    # Productos (el controlador comprueba además el perfil del producto)
    "/marketplace/products/create": ("sell", "active"),
    "/marketplace/products/<product_ref>/update": ("sell", "any"),
    "/marketplace/products/<product_ref>/publish": ("sell", "any"),
    "/marketplace/products/<product_ref>/deactivate": ("sell", "any"),
    "/marketplace/products/<product_ref>/delete": ("sell", "any"),
    "/marketplace/products/<product_ref>/reactivate": ("sell", "any"),
    "/marketplace/products/<product_ref>/quality/save": ("sell", "any"),
    "/marketplace/products/<product_ref>/quality/<quality_ref>/delete": ("sell", "any"),
    "/marketplace/products/<product_ref>/quality/<quality_ref>/sampling": ("sell", "any"),
    "/marketplace/products/<product_ref>/sampling": ("sell", "any"),
    "/marketplace/products/<product_ref>/observations": ("sell", "any"),
    "/marketplace/products/<product_ref>/observations/<obs_ref>/update": ("sell", "any"),
    "/marketplace/products/<product_ref>/observations/<obs_ref>/delete": ("sell", "any"),
    "/marketplace/products/<product_ref>/observations/<obs_ref>/apply": ("sell", "any"),
    # Traspaso entre perfiles (el modelo exige «vender» en los dos perfiles)
    "/marketplace/my-lots/<lot_ref>/move-profile": ("sell", "any"),
    "/marketplace/products/<product_ref>/move-profile": ("sell", "any"),
    # Salidas / producción
    "/marketplace/exports/register": ("sell", "active"),
    "/marketplace/exports/<export_ref>/cancel": ("sell", "any"),
    "/marketplace/my-lots/<lot_ref>/production": ("sell", "any"),
    # Compras (comprador)
    "/marketplace/buy/<product_ref>/confirm": ("buy", "active"),
    "/marketplace/buy/<product_ref>/request-check": ("buy", "active"),
    "/marketplace/purchases/<tx_ref>/rate": ("buy", "any"),
    "/marketplace/purchases/<tx_ref>/receive": ("buy", "any"),
    "/marketplace/purchases/<tx_ref>/report": ("buy", "any"),
    "/marketplace/purchases/<tx_ref>/delay/accept": ("buy", "any"),
    "/marketplace/purchases/<tx_ref>/delay/counter-offer": ("buy", "any"),
    "/marketplace/purchases/<tx_ref>/cancel": ("buy", "any"),
    "/marketplace/notify-me": ("buy", "active"),
    # Ventas (vendedor)
    "/marketplace/sales/<tx_ref>/dispatch": ("sell", "any"),
    "/marketplace/sales/<tx_ref>/rate-buyer": ("sell", "any"),
    # Factura del vendedor: «Vender» o «Facturación» (regla D-43 de la compra).
    "/marketplace/sales/<tx_ref>/invoice": (("sell", "billing"), "any"),
    "/marketplace/sales/<tx_ref>/delay": ("sell", "any"),
    "/marketplace/sales/<tx_ref>/delay/accept": ("sell", "any"),
    "/marketplace/sales/<tx_ref>/delay/reject": ("sell", "any"),
    "/marketplace/sales/<tx_ref>/cancel": ("sell", "any"),
    "/marketplace/sales/<tx_ref>/strike/<int:strike_id>/appeal": ("sell", "any"),
    # Solicitud de cancelación: la pide / responde la parte que toque
    # (shrimp.transaction._shrimp_check_party decide cuál).
    "/marketplace/purchases/<tx_ref>/cancel/request": (("buy", "sell"), "any"),
    "/marketplace/purchases/<tx_ref>/cancel/request/accept": (("buy", "sell"), "any"),
    "/marketplace/purchases/<tx_ref>/cancel/request/reject": (("buy", "sell"), "any"),
    "/marketplace/purchases/<tx_ref>/cancel/request/withdraw": (("buy", "sell"), "any"),
    # ------------------------------------------------------------------
    # shrimp_packer
    # ------------------------------------------------------------------
    "/marketplace/price-lists/save": ("sell", "active"),
    "/marketplace/price-lists/<ref>/lines": ("sell", "any"),
    "/marketplace/price-lists/<ref>/lines/<line_ref>/delete": ("sell", "any"),
    "/marketplace/price-lists/<ref>/bonuses": ("sell", "any"),
    "/marketplace/price-lists/<ref>/bonuses/<bonus_ref>/delete": ("sell", "any"),
    "/marketplace/price-lists/<ref>/upload": ("sell", "any"),
    "/marketplace/price-lists/<ref>/publish": ("sell", "any"),
    "/marketplace/price-lists/<ref>/duplicate": ("sell", "any"),
    "/marketplace/price-lists/<ref>/archive": ("sell", "any"),
    "/marketplace/price-lists/<ref>/unpublish": ("sell", "any"),
    "/marketplace/price-lists/<ref>/delete": ("sell", "any"),
    "/marketplace/price-lists/<ref>/correct": ("sell", "any"),
    "/marketplace/price-lists/<ref>/renew": ("sell", "any"),
    # Declaraciones de cosecha (camaronera = vende)
    "/marketplace/reservations/new": ("sell", "active"),
    "/marketplace/reservations/<ref>/cancel": ("sell", "any"),
    "/marketplace/reservations/<ref>/harvest": ("sell", "any"),
    "/marketplace/reservations/commitments/<ref>/accept": ("sell", "any"),
    "/marketplace/reservations/commitments/<ref>/discard": ("sell", "any"),
    "/marketplace/reservations/commitments/<ref>/desist": ("sell", "any"),
    # Compromisos de la empacadora (= compra)
    "/packer/reservations/preference": ("buy", "active"),
    "/packer/reservations/<ref>/commit": ("buy", "active"),
    "/packer/commitments/<ref>/withdraw": ("buy", "any"),
    "/packer/commitments/<ref>/desist": ("buy", "any"),
    "/packer/commitments/<ref>/buy": ("buy", "any"),
    # Acta de confirmación: firma cada parte la suya.
    "/marketplace/reservations/confirmations/<ref>/sign": (("buy", "sell"), "any"),
    "/marketplace/reservations/confirmations/<ref>/undo": (("buy", "sell"), "any"),
    # ------------------------------------------------------------------
    # shrimp_copacking
    # ------------------------------------------------------------------
    "/marketplace/copacking/requests/new": ("buy", "active"),
    "/marketplace/copacking/requests/<ref>/cancel": ("buy", "any"),
    "/marketplace/copacking/offers/<ref>/accept": ("buy", "any"),
    # Rechazar una oferta concreta (auditoría D-47)
    "/marketplace/copacking/offers/<ref>/reject": ("buy", "any"),
    # Empaque propio (la planta empaca su producto para venderlo/exportarlo)
    "/marketplace/copacking/self/new": ("sell", "active"),
    "/marketplace/copacking/self/<ref>/reception": ("sell", "any"),
    "/marketplace/copacking/self/<ref>/packing": ("sell", "any"),
    "/marketplace/copacking/self/<ref>/correct": ("sell", "any"),
    "/marketplace/copacking/self/<ref>/close": ("sell", "any"),
    # Orden: actúa el cliente (comprar) o el maquilador (vender)
    "/marketplace/copacking/orders/<ref>/cancel": (("buy", "sell"), "any"),
    "/marketplace/copacking/orders/<ref>/sign": (("buy", "sell"), "any"),
    "/marketplace/copacking/orders/<ref>/undo-signature": (("buy", "sell"), "any"),
    "/marketplace/copacking/orders/<ref>/reopen": (("buy", "sell"), "any"),
    "/marketplace/copacking/orders/<ref>/close": (("buy", "sell"), "any"),
    # Acta de devolución: la firma el cliente (D-34)
    "/marketplace/copacking/orders/<ref>/cancel/sign": ("buy", "any"),
    "/copacker/requests/<ref>/offer": ("sell", "active"),
    "/copacker/requests/<ref>/withdraw": ("sell", "any"),
    "/copacker/orders/<ref>/reception": ("sell", "any"),
    "/copacker/orders/<ref>/packing": ("sell", "any"),
    # Con el camarón en planta: acta de devolución o aprobación de la plataforma (D-34)
    "/copacker/orders/<ref>/cancel/submit": ("sell", "any"),
    "/copacker/orders/<ref>/cancel/withdraw": ("sell", "any"),
    "/copacker/tariffs/new": ("sell", "active"),
    "/copacker/tariffs/<ref>/save": ("sell", "any"),
    "/copacker/tariffs/<ref>/archive": ("sell", "any"),
    "/copacker/profile/save": ("manage_account", "active"),
    # ------------------------------------------------------------------
    # shrimp_verification
    # ------------------------------------------------------------------
    "/verifier/profile/save": ("manage_account", "active"),
    "/verifier/verifications/<ref>/start": ("approve", "any"),
    "/verifier/verifications/<ref>/save": ("approve", "any"),
    "/verifier/verifications/<ref>/photos/<token>/delete": ("approve", "any"),
    "/verifier/verifications/<ref>/verdict": ("approve", "any"),
    "/verifier/verifications/<ref>/assign": ("approve", "any"),
    # Decisiones de la verificadora (aceptar / declinar el encargo, «no se
    # presentó») y segunda opinión que pide el vendedor.
    "/verifier/verifications/<ref>/respond": ("approve", "any"),
    "/verifier/verifications/<ref>/not-presented": ("approve", "any"),
    "/marketplace/sales/<tx_ref>/second-opinion": ("sell", "any"),
    # Equipo de campo de una verificación (técnico responsable y de apoyo)
    "/verifier/verifications/<ref>/team/add": ("approve", "any"),
    "/verifier/verifications/<ref>/team/delete": ("approve", "any"),
    "/verifier/verifications/<ref>/team/assign": ("approve", "any"),
    "/verifier/technicians/add": ("manage_account", "active"),
    "/verifier/technicians/<tech_ref>/toggle": ("manage_account", "active"),
    "/verifier/technicians/<tech_ref>/edit": ("manage_account", "active"),
    "/marketplace/buy/<product_ref>/verify": ("buy", "active"),
    "/marketplace/purchases/<tx_ref>/complete": ("buy", "any"),
    "/marketplace/purchases/<tx_ref>/verifier/assign": ("buy", "any"),
    "/marketplace/verifications/<ref>/accept": ("buy", "any"),
    "/marketplace/verifications/<ref>/reject": ("buy", "any"),
    "/marketplace/verifications/<ref>/counter-offer": ("buy", "any"),
    "/marketplace/verifications/<ref>/undo": ("buy", "any"),
    "/marketplace/verifications/<ref>/rate": ("buy", "any"),
    # Verificación declarada: la llena la parte que la declara.
    "/marketplace/verifications/<ref>/declare/save": (("buy", "sell"), "any"),
    "/marketplace/verifications/<ref>/declare/cancel": (("buy", "sell"), "any"),
    "/marketplace/verifications/<ref>/declare/photos/<token>/delete": (("buy", "sell"), "any"),
    "/marketplace/dispatch/<tx_ref>/save": ("sell", "any"),
    "/marketplace/dispatch/<tx_ref>/arrival": ("approve", "any"),
    # ------------------------------------------------------------------
    # shrimp_api (páginas del portal; la API REST tiene su propia tabla)
    # ------------------------------------------------------------------
    "/my/api-keys/new": ("manage_account", "active"),
    "/my/api-keys/<string:ref>/revoke": ("manage_account", "active"),
    "/my/api-keys/<string:ref>/renew": ("manage_account", "active"),
    # Cada quien revoca sus propios teléfonos de la app (shrimp_api).
    "/my/api-keys/devices/<string:ref>/revoke": ("none", "active"),
    "/my/webhooks/new": ("manage_account", "active"),
    # Página pública de trazabilidad (D-29): la retira o renueva cualquiera de las partes.
    "/marketplace/purchases/<tx_ref>/traceability/withdraw": (("buy", "sell"), "any"),
    "/marketplace/purchases/<tx_ref>/traceability/renew": (("buy", "sell"), "any"),
    "/my/webhooks/<string:ref>/delete": ("manage_account", "active"),
    "/my/webhooks/<string:ref>/toggle": ("manage_account", "active"),
    "/my/webhooks/<string:ref>/ping": ("manage_account", "active"),
    "/my/webhooks/<string:ref>/deliveries/<string:dref>/resend": ("manage_account", "active"),
}

# Páginas GET que solo sirven para escribir (formularios): sin el permiso se
# responde 403 en vez de dejar llenar un formulario que no se podrá enviar.
SHRIMP_GET_PERMISSIONS = {
    "/marketplace/products/new": ("sell", "active"),
    "/marketplace/sales/<tx_ref>/invoice": (("sell", "billing"), "any"),
    "/marketplace/price-lists/new": ("sell", "active"),
    "/marketplace/reservations/new": ("sell", "active"),
    "/marketplace/exports/new": ("sell", "active"),
    "/marketplace/sowings/new": ("sell", "active"),
    "/marketplace/sowings/<alloc_ref>/edit": ("sell", "active"),
    "/marketplace/copacking/requests/new": ("buy", "active"),
    "/marketplace/copacking/self/new": ("sell", "active"),
    "/marketplace/purchases/<tx_ref>/invoice/pdf": ("billing", "any"),
}

# Rutas POST de los módulos shrimp que no son del portal de un socio (alta
# pública): el test de cobertura las salta.
SHRIMP_ROUTE_EXEMPT = {
    "/register/submit",
}

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def shrimp_route_rule(routes, method):
    """(permiso, alcance) de la petición, o None si no está en la tabla."""
    tabla = SHRIMP_ROUTE_PERMISSIONS if method in WRITE_METHODS else SHRIMP_GET_PERMISSIONS
    for ruta in routes or ():
        regla = tabla.get(ruta)
        if regla:
            return regla
    return None


def shrimp_route_regex(route):
    """'/a/<x>/b' -> '^/a/[^/]+/b/?$' (la página desactiva esos formularios)."""
    partes = re.split(r"<[^>]+>", route)
    return "^" + "[^/]+".join(re.escape(p) for p in partes) + "/?$"
