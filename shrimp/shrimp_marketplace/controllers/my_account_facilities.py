"""«Instalaciones y piscinas» dentro de Mi cuenta (19.0.1.28.0).

Un laboratorio no encontraba dónde agregar sus instalaciones ni sus tanques:
la única pantalla era /marketplace/my-facilities y su entrada del menú exige la
capacidad «manage_ponds» (siembra), que solo tiene la camaronera. Ahora Mi
cuenta tiene la sección #instalaciones para los perfiles con instalaciones
(camaronera, laboratorio y naupliero), con el vocabulario de cada uno.

No hay rutas de escritura nuevas para crear o editar: los modales de la
sección envían a las MISMAS rutas de /marketplace/my-facilities (mismos
nombres de campo). Aquí se les agrega, para las dos pantallas:

* permiso: crear, editar y archivar exige el permiso «Vender» del perfil
  activo (la capacidad manage_ponds se mapea a «sell») o ser administrador
  de la cuenta; un subusuario sin él recibe 403 y ve la sección en lectura;
* validación previa (tipos de la lista, números finitos dentro de rango,
  código no repetido): antes un tipo inventado o un código duplicado daban
  un error 500;
* `next=account`: vuelve a Mi cuenta#instalaciones (con el aviso) en vez de
  a /marketplace/my-facilities;
* «Archivar» (active = False) para instalaciones y piscinas: conserva el
  historial (siembras, origen de productos), a diferencia de «Eliminar».
"""
import math
from urllib.parse import parse_qsl, urlencode, urlsplit

from odoo import _, http
from odoo.http import request
from werkzeug.exceptions import Forbidden

from odoo.addons.shrimp_user_registry.controllers.main import flash_message, to_number

from .account_portal import ShrimpAccountPortalController

# Límites (los mismos del paso «Instalaciones» del alta).
MAX_METERS = 100000.0
MAX_DEPTH = 50.0
MAX_VOLUME = 10000000.0

FACILITY_TYPES = ("hatchery", "laboratory", "farm", "warehouse", "office", "other")
POND_TYPES = ("earth", "geomembrane", "tank", "raceway", "other")
POND_MODES = ("dimensions", "volume")

# Perfil activo -> vocabulario, tipo por defecto y orden de los tipos (los
# naturales del perfil primero; NO se restringe ninguno).
FACILITY_PROFILES = {
    "camaronera": {
        "fac_plural": "Fincas", "fac_singular": "finca",
        "pond_plural": "Piscinas", "pond_singular": "piscina", "pond_article": "una",
        "metric": "area", "icon": "fa-map-o",
        "fac_default": "farm",
        "fac_order": ("farm", "warehouse", "office", "laboratory", "hatchery", "other"),
        "pond_default": "earth",
        "pond_order": ("earth", "geomembrane", "raceway", "tank", "other"),
        "mode_default": "dimensions",
        "desc": "Tus fincas y sus piscinas. El área se suma sola y se usa como "
                "origen al publicar un producto.",
    },
    "laboratorio": {
        "fac_plural": "Laboratorios", "fac_singular": "laboratorio",
        "pond_plural": "Tanques", "pond_singular": "tanque", "pond_article": "un",
        "metric": "volume", "icon": "fa-flask",
        "fac_default": "laboratory",
        "fac_order": ("laboratory", "hatchery", "farm", "warehouse", "office", "other"),
        "pond_default": "tank",
        "pond_order": ("tank", "raceway", "geomembrane", "earth", "other"),
        "mode_default": "volume",
        "desc": "Tus laboratorios y sus tanques de larvicultura, con su volumen en m³.",
    },
    "semillero": {
        "fac_plural": "Centros de maduración", "fac_singular": "centro de maduración",
        "pond_plural": "Tanques", "pond_singular": "tanque", "pond_article": "un",
        "metric": "volume", "icon": "fa-leaf",
        "fac_default": "hatchery",
        "fac_order": ("hatchery", "laboratory", "farm", "warehouse", "office", "other"),
        "pond_default": "tank",
        "pond_order": ("tank", "raceway", "geomembrane", "earth", "other"),
        "mode_default": "volume",
        "desc": "Tus centros de maduración (naupliero) y sus tanques, con su volumen en m³.",
    },
}

ACCOUNT_URL = "/marketplace/my-account"
ACCOUNT_ANCHOR = "instalaciones"


def _num_or_error(post, key, label, maximo):
    """None si el campo es válido (o está vacío); si no, el mensaje."""
    raw = post.get(key)
    if raw in (None, "", False):
        return None
    valor = to_number(raw, None)
    if valor is None or not math.isfinite(valor) or valor < 0 or valor > maximo:
        return _("%s: el valor «%s» no es válido.") % (label, raw)
    return None


class ShrimpMyAccountFacilities(ShrimpAccountPortalController):

    # ------------------------------------------------------------------
    # Permisos
    # ------------------------------------------------------------------
    def _ma_fac_role(self, partner):
        return partner.sudo()._shrimp_effective_type()

    def _ma_fac_can_manage(self, partner):
        """Crear/editar/archivar: el administrador de la cuenta o un
        subusuario con «Vender» en el perfil activo."""
        return bool(partner) and partner.sudo()._shrimp_user_allows(
            "manage_ponds", self._ma_fac_role(partner))

    def _ma_fac_guard(self):
        partner = self._partner()
        if not self._ma_fac_can_manage(partner):
            raise Forbidden()
        return partner

    # ------------------------------------------------------------------
    # Vuelta a Mi cuenta
    # ------------------------------------------------------------------
    def _ma_back(self, res, post, facility_ref=None):
        """Si el formulario vino de Mi cuenta (next=account), traduce la
        redirección de /marketplace/my-facilities a Mi cuenta#instalaciones
        conservando el aviso (saved / error / message)."""
        if (post or {}).get("next") != "account":
            return res
        loc = self._ma_location(res)
        if not loc:
            return res
        q = dict(parse_qsl(urlsplit(loc).query))
        params = {}
        if q.get("error"):
            if q["error"] == "name":
                params.update(error="validation", message=flash_message(_("El nombre es obligatorio.")))
            else:
                params["error"] = q["error"]
                if q.get("message"):
                    params["message"] = q["message"]
        elif q.get("saved"):
            params["saved"] = "fac_" + q["saved"]
        ref = facility_ref or q.get("facility")
        if ref:
            params["fac"] = ref
        return request.redirect("%s?%s#%s" % (ACCOUNT_URL, urlencode(params), ACCOUNT_ANCHOR))

    @staticmethod
    def _ma_location(res):
        headers = getattr(res, "headers", None)
        return headers.get("Location") if headers is not None else None

    @staticmethod
    def _ma_norm_numbers(post):
        """Coma decimal -> punto (la ruta base usa float())."""
        for key in ("length_m", "width_m", "depth_m", "manual_volume_m3"):
            if isinstance(post.get(key), str):
                post[key] = post[key].strip().replace(",", ".")

    def _ma_error(self, post, message, fallback="/marketplace/my-facilities"):
        code = flash_message(message)
        if (post or {}).get("next") == "account":
            return request.redirect("%s?%s#%s" % (ACCOUNT_URL, urlencode(
                {"error": "validation", "message": code}), ACCOUNT_ANCHOR))
        sep = "&" if "?" in fallback else "?"
        return request.redirect("%s%serror=validation&message=%s" % (fallback, sep, code))

    # ------------------------------------------------------------------
    # Validación previa (compartida por las dos pantallas)
    # ------------------------------------------------------------------
    def _ma_check_facility(self, partner, post, current=None):
        tipo = post.get("facility_type")
        if tipo and tipo not in FACILITY_TYPES:
            return _("Elige un tipo de instalación de la lista.")
        code = (post.get("code") or "").strip()
        if code:
            dom = [("partner_id", "=", partner.id), ("code", "=", code)]
            if current:
                dom.append(("id", "!=", current.id))
            if request.env["shrimp.partner.facility"].sudo().with_context(
                    active_test=False).search_count(dom):
                return _("Ya tienes una instalación con el código «%s».") % code
        return None

    def _ma_check_pond(self, partner, post, current=None):
        if post.get("pond_type") and post.get("pond_type") not in POND_TYPES:
            return _("Elige un tipo de piscina o tanque de la lista.")
        if post.get("capacity_mode") and post.get("capacity_mode") not in POND_MODES:
            return _("Elige cómo indicas la capacidad.")
        for key, label, maximo in (
                ("length_m", _("Largo"), MAX_METERS), ("width_m", _("Ancho"), MAX_METERS),
                ("depth_m", _("Profundidad"), MAX_DEPTH), ("manual_volume_m3", _("Volumen"), MAX_VOLUME)):
            err = _num_or_error(post, key, label, maximo)
            if err:
                return err
        code = (post.get("code") or "").strip()
        if code:
            dom = [("partner_id", "=", partner.id), ("code", "=", code)]
            if current:
                dom.append(("id", "!=", current.id))
            if request.env["shrimp.partner.pond"].sudo().with_context(
                    active_test=False).search_count(dom):
                return _("Ya tienes una piscina o tanque con el código «%s».") % code
        return None

    # ------------------------------------------------------------------
    # Rutas existentes: permiso + validación + vuelta a Mi cuenta
    # ------------------------------------------------------------------
    @http.route()
    def facility_create(self, **post):
        partner = self._ma_fac_guard()
        err = self._ma_check_facility(partner, post)
        if err:
            return self._ma_error(post, err, "/marketplace/my-facilities?new=1")
        return self._ma_back(super().facility_create(**post), post)

    @http.route()
    def facility_update(self, facility_ref, **post):
        partner = self._ma_fac_guard()
        fac = self._owned("shrimp.partner.facility", facility_ref)
        err = self._ma_check_facility(partner, post, current=fac)
        if err:
            return self._ma_error(post, err, "/marketplace/my-facilities?facility=%s" % fac.uuid_ref)
        res = super().facility_update(facility_ref, **post)
        # La ruta base no escribe la provincia; el modal de Mi cuenta la envía.
        if "province" in post and "error=" not in (self._ma_location(res) or ""):
            fac.write({"province": (post.get("province") or "").strip() or False})
        return self._ma_back(res, post, facility_ref=fac.uuid_ref)

    @http.route()
    def facility_delete(self, facility_ref, **post):
        self._ma_fac_guard()
        return self._ma_back(super().facility_delete(facility_ref, **post), post)

    @http.route()
    def pond_create(self, **post):
        partner = self._ma_fac_guard()
        err = self._ma_check_pond(partner, post)
        if err:
            return self._ma_error(post, err)
        self._ma_norm_numbers(post)
        return self._ma_back(super().pond_create(**post), post,
                             facility_ref=post.get("facility_ref") or None)

    @http.route()
    def pond_update(self, pond_ref, **post):
        partner = self._ma_fac_guard()
        pond = self._owned("shrimp.partner.pond", pond_ref)
        err = self._ma_check_pond(partner, post, current=pond)
        if err:
            return self._ma_error(post, err)
        self._ma_norm_numbers(post)
        res = super().pond_update(pond_ref, **post)
        # Mi cuenta permite mover la piscina a otra instalación propia
        # (p. ej. las que quedaron sin instalación).
        if post.get("facility_ref") and "error=" not in (self._ma_location(res) or ""):
            fac = request.env["shrimp.partner.facility"].sudo().resolve_ref(post.get("facility_ref"))
            if fac and fac.partner_id.id == partner.id and fac != pond.facility_id:
                pond.write({"facility_id": fac.id})
        return self._ma_back(res, post, facility_ref=pond.facility_id.uuid_ref or None)

    @http.route()
    def pond_delete(self, pond_ref, **post):
        self._ma_fac_guard()
        return self._ma_back(super().pond_delete(pond_ref, **post), post)

    # ------------------------------------------------------------------
    # Archivar (nuevo)
    # ------------------------------------------------------------------
    @http.route("/marketplace/my-facilities/facility/<facility_ref>/archive",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def facility_archive(self, facility_ref, **post):
        self._ma_fac_guard()
        fac = self._owned("shrimp.partner.facility", facility_ref)
        # Sus piscinas o tanques se archivan con ella (siguen en el historial).
        fac.pond_ids.write({"active": False})
        fac.write({"active": False})
        return self._ma_back(request.redirect("/marketplace/my-facilities?saved=facility_arch"),
                             post)

    @http.route("/marketplace/my-facilities/pond/<pond_ref>/archive",
                type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def pond_archive(self, pond_ref, **post):
        self._ma_fac_guard()
        pond = self._owned("shrimp.partner.pond", pond_ref)
        ref = pond.facility_id.uuid_ref or ""
        pond.write({"active": False})
        return self._ma_back(request.redirect(
            "/marketplace/my-facilities?saved=pond_arch" + ("&facility=%s" % ref if ref else "")),
            post)

    # ------------------------------------------------------------------
    # Mi cuenta: datos de la sección y de la navegación
    # ------------------------------------------------------------------
    @http.route()
    def my_account(self, **kw):
        res = super().my_account(**kw)
        if getattr(res, "qcontext", None) is not None:
            partner = self._partner()
            res.qcontext.update(self._ma_values(partner, kw))
        return res

    def _ma_values(self, partner, kw):
        saved = kw.get("saved") if isinstance(kw.get("saved"), str) else None
        return {
            "ma_fac": self._ma_facilities(partner, kw),
            "ma_fac_saved": saved[4:] if saved and saved.startswith("fac_") else None,
            "ma_links": self._ma_links(partner),
        }

    def _ma_links(self, partner):
        """Secciones de enlace (otras páginas de la cuenta) que aplican."""
        user = request.env.user
        links = []
        if user.sudo()._shrimp_is_account_admin():
            links.append({"key": "equipo", "label": _("Equipo"), "icon": "fa-users",
                          "title": _("Equipo y subcuentas"), "url": "/my/team",
                          "cta": _("Gestionar mi equipo"),
                          "desc": _("Invita usuarios de tu empresa y dales permisos por perfil "
                                    "(ver, comprar, vender, aprobar, facturación).")})
        links.append({"key": "contratos", "label": _("Contratos"), "icon": "fa-file-text-o",
                      "title": _("Contratos y datos personales"), "url": "/my/data",
                      "cta": _("Ver contratos y mis datos"),
                      "desc": _("Los contratos y políticas que aceptaste (con su comprobante) y "
                                "tus derechos sobre tus datos personales.")})
        Module = request.env["ir.module.module"].sudo()
        if user.share and Module.search_count([("name", "=", "shrimp_api"), ("state", "=", "installed")]):
            links.append({"key": "api", "label": _("Integraciones API"), "icon": "fa-plug",
                          "title": _("Integraciones (API)"), "url": "/my/api-keys",
                          "cta": _("Gestionar claves de API"),
                          "desc": _("Claves para conectar tu sistema con el marketplace "
                                    "(publicar productos, consultar pedidos).")})
        links.append({"key": "seguridad", "label": _("Seguridad"), "icon": "fa-lock",
                      "title": _("Contraseña y seguridad"), "url": "/my/security",
                      "cta": _("Cambiar contraseña"),
                      "desc": _("Cambia tu contraseña y revisa la seguridad de tu acceso.")})
        return links

    def _ma_facilities(self, partner, kw):
        role = self._ma_fac_role(partner)
        cfg = FACILITY_PROFILES.get(role)
        if not cfg:
            return None
        Facility = request.env["shrimp.partner.facility"].sudo()
        Pond = request.env["shrimp.partner.pond"].sudo()
        fac_labels = dict(Facility._fields["facility_type"]._description_selection(request.env))
        pond_labels = dict(Pond._fields["pond_type"]._description_selection(request.env))
        orden = {t: i for i, t in enumerate(cfg["fac_order"])}
        facilities = Facility.search([("partner_id", "=", partner.id)]).sorted(
            lambda f: (orden.get(f.facility_type, 99), (f.name or "").lower()))
        ponds = Pond.search([("partner_id", "=", partner.id)])

        def pond_row(p):
            dims = ""
            if p.capacity_mode == "dimensions" and (p.length_m or p.width_m):
                partes = [p.length_m, p.width_m] + ([p.depth_m] if p.depth_m else [])
                dims = " × ".join(("%g" % round(v, 2)) for v in partes) + " m"
            return {
                "rec": p,
                "type_label": pond_labels.get(p.pond_type, p.pond_type or "—"),
                "dims": dims,
                "area_ha": (p.area_m2 or 0.0) / 10000.0,
                "volume": p.volume_m3 or 0.0,
            }

        def bloque(fac, recs):
            filas = [pond_row(p) for p in recs]
            ubic = ", ".join(x for x in (fac.city, fac.province) if x) if fac else ""
            return {
                "rec": fac,
                "type_label": fac_labels.get(fac.facility_type, "") if fac else "",
                "location": ubic,
                "ponds": filas,
                "area_ha": sum(f["area_ha"] for f in filas),
                "volume": sum(f["volume"] for f in filas),
            }

        bloques = [bloque(f, ponds.filtered(lambda p, f=f: p.facility_id == f)) for f in facilities]
        sueltas = ponds.filtered(lambda p: not p.facility_id or p.facility_id not in facilities)
        estados = request.env["res.country.state"].sudo().search(
            [("country_id.code", "=", "EC")], order="name")
        return {
            "role": role,
            "cfg": cfg,
            "can_manage": self._ma_fac_can_manage(partner),
            "facilities": bloques,
            "orphans": bloque(None, sueltas) if sueltas else None,
            "fac_count": len(facilities),
            "pond_count": len(ponds),
            "area_ha": sum(b["area_ha"] for b in bloques) + (sum(
                (p.area_m2 or 0.0) for p in sueltas) / 10000.0),
            "volume": sum(b["volume"] for b in bloques) + sum((p.volume_m3 or 0.0) for p in sueltas),
            "fac_types": [(t, fac_labels.get(t, t)) for t in cfg["fac_order"]],
            "pond_types": [(t, pond_labels.get(t, t)) for t in cfg["pond_order"]],
            "provinces": estados.mapped("name"),
            "focus": kw.get("fac") if isinstance(kw.get("fac"), str) else None,
            "can_sow": request.env["shrimp.lot.allocation"]._shrimp_can_sow(partner),
        }
