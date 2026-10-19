"""Asistente de alta: instalaciones y piscinas (19.0.1.22.0; 19.0.1.22.4).

El paso «Instalaciones» del asistente común (shrimp_user_registry) lo agrega
este módulo porque los modelos son suyos (shrimp.partner.facility y
shrimp.partner.pond). Es opcional: quien marca «Lo haré después» (o no agrega
nada) se registra igual y gestiona lo mismo después en /marketplace/my-facilities.

19.0.1.22.4: UNA sola lista de instalaciones con su tipo (finca, laboratorio,
centro de producción), sin separar por perfil, y una sola lista de piscinas y
tanques que apuntan a su instalación. Lo que se envía:

    fac_<i>_type   farm | laboratory | hatchery   (fac_<i>_role, el nombre
                   anterior por perfil, se sigue aceptando)
    fac_<i>_name, _code, _state_id, _city, _address, _lat, _lng
    pond_<i>_<j>_name, _code, _type, _mode (dimensions | volume),
                 _length, _width, _depth, _volume
                   (i = índice de SU instalación; j = 0, 1, 2…)

Se valida TODO antes de crear la cuenta (_extra_partner_vals corre antes del
create y un ValidationError vuelve al formulario con el mensaje y lo escrito)
y se crea dentro de la misma transacción del alta (_post_registration), a
nombre de la empresa (el partner comercial que crea el alta).
"""
import json
import math
import re

from odoo import _
from odoo.http import request
from odoo.exceptions import ValidationError

from odoo.addons.shrimp_user_registry.controllers.main import (
    ShrimpRegistryController, _normalize_text, to_int, to_number)

MAX_FACILITIES = 20
MAX_PONDS = 60
MAX_METERS = 100000.0
MAX_DEPTH = 50.0
MAX_VOLUME = 10000000.0

POND_TYPE_LABELS = {
    "earth": "Piscina de tierra", "geomembrane": "Geomembrana",
    "tank": "Tanque", "raceway": "Canal", "other": "Otro",
}
POND_TYPES = set(POND_TYPE_LABELS)
POND_MODES = {"dimensions", "volume"}

# Tipo de instalación (clave de shrimp.partner.facility.facility_type) → qué
# perfil la ofrece y qué piscinas o tanques admite (el primero es el de por
# defecto). Empacadora y maquilador declaran su planta en su bloque del paso
# «Perfil» y el verificador no tiene instalaciones.
FACILITY_KINDS = {
    "farm": {
        "label": "Finca", "profile": "camaronera", "icon": "fa-map-o", "pond": "piscina",
        "pond_types": ["earth", "geomembrane", "other"],
    },
    "laboratory": {
        "label": "Laboratorio", "profile": "laboratorio", "icon": "fa-flask", "pond": "tanque o canal",
        "pond_types": ["tank", "raceway", "other"],
    },
    "hatchery": {
        "label": "Centro de producción", "profile": "semillero", "icon": "fa-leaf", "pond": "tanque",
        "pond_types": ["tank", "other"],
    },
}
# Perfil → tipo de instalación (también traduce el fac_<i>_role anterior).
FACILITY_ROLES = {cfg["profile"]: code for code, cfg in FACILITY_KINDS.items()}


def _kinds_for(tipos):
    """Tipos de instalación que corresponden a los perfiles `tipos`."""
    return [code for code, cfg in FACILITY_KINDS.items() if cfg["profile"] in set(tipos or [])]


class ShrimpRegistryFacilities(ShrimpRegistryController):

    # ------------------------------------------------------------ contexto
    def _signup_context(self, values, preselect=None):
        ctx = super()._signup_context(values, preselect=preselect)
        perfiles = {p["code"]: p for p in ctx.get("signup_profiles") or []}
        # Perfiles del sitio con instalaciones (el paso solo se ve con ellos).
        ctx["signup_facility_roles"] = [r for r in FACILITY_ROLES if r in perfiles]
        ctx["signup_fac_kinds_json"] = json.dumps([{
            "code": code, "label": cfg["label"], "profile": cfg["profile"], "icon": cfg["icon"],
            "pondTypes": [{"code": t, "label": POND_TYPE_LABELS[t]} for t in cfg["pond_types"]],
        } for code, cfg in FACILITY_KINDS.items() if cfg["profile"] in perfiles])
        # Al volver con un error: lo que se había escrito (sin validar).
        try:
            prefill = self._signup_facilities(values or {}, list(perfiles), strict=False)
        except ValidationError:
            prefill = []
        ctx["signup_fac_prefill_json"] = json.dumps(prefill)
        return ctx

    # ------------------------------------------------------------ lectura
    @staticmethod
    def _num(post, key, label, minimo=0.0, maximo=MAX_METERS, strict=True):
        raw = (post.get(key) or "").strip() if isinstance(post.get(key), str) else post.get(key)
        if raw in (None, "", False):
            return 0.0
        valor = to_number(raw, None)
        if valor is None or not math.isfinite(valor) or valor < minimo or valor > maximo:
            if strict:
                raise ValidationError(_("%s: el valor «%s» no es válido.") % (label, raw))
            return 0.0
        return valor

    @staticmethod
    def _fac_kind(post, i):
        """Tipo de la instalación i: fac_<i>_type o, en el formato anterior,
        el perfil de fac_<i>_role."""
        tipo = (post.get("fac_%s_type" % i) or "").strip()
        if not tipo:
            tipo = FACILITY_ROLES.get((post.get("fac_%s_role" % i) or "").strip(), "")
        return tipo

    def _signup_facilities(self, post, tipos, strict=True):
        """Instalaciones del formulario: [{type, name, code, state_id, city,
        address, lat, lng, ponds: [{name, code, type, mode, length, width,
        depth, volume}]}]. Ignora las tarjetas vacías. Con strict lanza
        ValidationError con un mensaje para el usuario; sin strict (para
        volver a pintar el formulario) descarta lo que no se puede mostrar."""
        if post.get("facilities_later"):
            return []
        indices = sorted({int(m.group(1)) for k in post
                          for m in [re.match(r"^fac_(\d+)_(?:type|role|name)$", k)] if m})
        pond_keys = {}
        for k in post:
            m = re.match(r"^pond_(\d+)_(\d+)_[a-z]+$", k)
            if m:
                pond_keys.setdefault(int(m.group(1)), set()).add(int(m.group(2)))
        if strict:
            if len(indices) > MAX_FACILITIES:
                raise ValidationError(_("Puedes registrar hasta %s instalaciones en el alta; "
                                        "agrega las demás después.") % MAX_FACILITIES)
            if sum(len(v) for v in pond_keys.values()) > MAX_PONDS:
                raise ValidationError(_("Puedes registrar hasta %s piscinas o tanques en el alta; "
                                        "agrega los demás después.") % MAX_PONDS)
        permitidos = _kinds_for(tipos)
        estados = request.env["res.country.state"].sudo()
        resultado, usados, codigos_fac, codigos_pond = [], set(), set(), set()
        for n, i in enumerate(indices[:MAX_FACILITIES], start=1):
            tipo = self._fac_kind(post, i)
            cfg = FACILITY_KINDS.get(tipo)
            g = lambda k: _normalize_text(post.get("fac_%s_%s" % (i, k)))  # noqa: E731
            nombre = g("name")
            etiqueta = (_("Instalación «%s»") % nombre) if nombre else (_("Instalación %s") % n)
            con_datos = any(g(k) for k in ("name", "code", "state_id", "city", "address", "lat", "lng")) or any(
                _normalize_text(post.get("pond_%s_%s_%s" % (i, j, k)) or "")
                for j in pond_keys.get(i, ()) for k in ("name", "code", "length", "width", "depth", "volume"))
            if not con_datos:
                continue
            if not cfg:
                if strict:
                    raise ValidationError(_("%s: elige el tipo de instalación.") % etiqueta)
                continue
            if tipo not in permitidos:
                if strict:
                    raise ValidationError(_("%s: una %s no corresponde a los perfiles que elegiste. "
                                            "Cambia su tipo o elimínala.") % (etiqueta, cfg["label"].lower()))
                continue
            fac = {
                "type": tipo, "name": nombre, "code": g("code"),
                "state_id": to_int(post.get("fac_%s_state_id" % i), 0) or False,
                "city": g("city"), "address": g("address"),
                "lat": self._num(post, "fac_%s_lat" % i, _("%s · latitud") % etiqueta, -90, 90, strict),
                "lng": self._num(post, "fac_%s_lng" % i, _("%s · longitud") % etiqueta, -180, 180, strict),
                "ponds": [],
            }
            usados.add(i)
            for j in sorted(pond_keys.get(i, ()))[:MAX_PONDS]:
                p = lambda k: post.get("pond_%s_%s_%s" % (i, j, k))  # noqa: E731
                pnombre = _normalize_text(p("name"))
                pet = (_("%s · %s «%s»") % (etiqueta, cfg["pond"], pnombre)) if pnombre else etiqueta
                ptipo = (p("type") or "").strip() or cfg["pond_types"][0]
                if ptipo not in cfg["pond_types"]:
                    if strict:
                        raise ValidationError(_("%s: el tipo «%s» no corresponde a una %s.") % (
                            pet, POND_TYPE_LABELS.get(ptipo, ptipo), cfg["label"].lower()))
                    ptipo = cfg["pond_types"][0]
                modo = (p("mode") or "").strip() or "dimensions"
                if modo not in POND_MODES:
                    if strict:
                        raise ValidationError(_("%s: elige cómo indicas su capacidad.") % pet)
                    modo = "dimensions"
                pond = {
                    "name": pnombre, "code": _normalize_text(p("code")), "type": ptipo, "mode": modo,
                    "length": self._num(post, "pond_%s_%s_length" % (i, j), _("%s · largo") % pet, strict=strict),
                    "width": self._num(post, "pond_%s_%s_width" % (i, j), _("%s · ancho") % pet, strict=strict),
                    "depth": self._num(post, "pond_%s_%s_depth" % (i, j), _("%s · profundidad") % pet,
                                       maximo=MAX_DEPTH, strict=strict),
                    "volume": self._num(post, "pond_%s_%s_volume" % (i, j), _("%s · volumen") % pet,
                                        maximo=MAX_VOLUME, strict=strict),
                }
                if modo == "volume":
                    pond.update(length=0.0, width=0.0, depth=0.0)
                else:
                    pond["volume"] = 0.0
                if not (pond["name"] or pond["code"] or pond["length"] or pond["width"]
                        or pond["depth"] or pond["volume"]):
                    continue
                if strict and not pond["name"]:
                    raise ValidationError(_("%s: escribe el nombre de cada %s.") % (etiqueta, cfg["pond"]))
                if strict and pond["code"]:
                    if pond["code"].lower() in codigos_pond:
                        raise ValidationError(_("El código de piscina o tanque «%s» está repetido.") % pond["code"])
                    codigos_pond.add(pond["code"].lower())
                fac["ponds"].append(pond)
            vacia = not (fac["name"] or fac["code"] or fac["city"] or fac["address"]
                         or fac["state_id"] or fac["lat"] or fac["lng"] or fac["ponds"])
            if vacia:
                continue
            if strict:
                if not fac["name"]:
                    raise ValidationError(_("%s: escribe su nombre.") % etiqueta)
                if fac["state_id"]:
                    estado = estados.browse(fac["state_id"]).exists()
                    if not estado or estado.country_id.code != "EC":
                        raise ValidationError(_("%s: elige una provincia de la lista.") % etiqueta)
                if fac["code"]:
                    if fac["code"].lower() in codigos_fac:
                        raise ValidationError(_("El código de instalación «%s» está repetido.") % fac["code"])
                    codigos_fac.add(fac["code"].lower())
            resultado.append(fac)
        # Piscinas o tanques sin una instalación agregada en el formulario.
        if strict:
            for i, js in pond_keys.items():
                if i in usados:
                    continue
                if any(_normalize_text(post.get("pond_%s_%s_%s" % (i, j, k)) or "")
                       for j in js for k in ("name", "code", "length", "width", "depth", "volume")):
                    raise ValidationError(_("Cada piscina o tanque debe pertenecer a una de las "
                                            "instalaciones que agregaste."))
        return resultado

    def _facility_types_posted(self, user_type, post):
        tipos = self._posted_types(post) or [user_type]
        return tipos, [t for t in tipos if t in FACILITY_ROLES]

    # ------------------------------------------------------------ ganchos
    def _extra_partner_vals(self, user_type, post):
        vals = super()._extra_partner_vals(user_type, post)
        tipos, con_fac = self._facility_types_posted(user_type, post)
        # Una sola validación (el gancho corre una vez por perfil elegido),
        # antes de crear la cuenta: un error vuelve al formulario.
        if con_fac and user_type == con_fac[0] and any(
                k.startswith(("fac_", "pond_")) for k in post):
            self._signup_facilities(post, tipos)
        return vals

    def _post_registration(self, partner, user_type, post):
        super()._post_registration(partner, user_type, post)
        tipos, con_fac = self._facility_types_posted(user_type, post)
        # Todas las instalaciones de una vez (con el primer perfil que tiene).
        if not con_fac or user_type != con_fac[0]:
            return
        Facility = request.env["shrimp.partner.facility"].sudo()
        Pond = request.env["shrimp.partner.pond"].sudo()
        estados = request.env["res.country.state"].sudo()
        ecuador = request.env.ref("base.ec", raise_if_not_found=False)
        for fac in self._signup_facilities(post, tipos):
            estado = estados.browse(fac["state_id"]).exists() if fac["state_id"] else estados
            registro = Facility.create({
                "partner_id": partner.id,
                "name": fac["name"],
                "code": fac["code"] or False,
                "facility_type": fac["type"],
                "province": estado.name or False,
                "country_id": estado.country_id.id if estado else (ecuador.id if ecuador else False),
                "city": fac["city"] or False,
                "address": fac["address"] or False,
                "latitude": fac["lat"] or 0.0,
                "longitude": fac["lng"] or 0.0,
            })
            for pond in fac["ponds"]:
                Pond.create({
                    "partner_id": partner.id,
                    "facility_id": registro.id,
                    "name": pond["name"],
                    "code": pond["code"] or False,
                    "pond_type": pond["type"],
                    "capacity_mode": pond["mode"],
                    "length_m": pond["length"],
                    "width_m": pond["width"],
                    "depth_m": pond["depth"],
                    "manual_volume_m3": pond["volume"],
                })
