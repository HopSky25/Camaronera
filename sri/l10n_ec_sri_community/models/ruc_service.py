# -*- coding: utf-8 -*-
"""Servicio de consulta de RUC/Cédula en el catastro público del SRI.

Portado desde la suite odoo_saas_ecuador (l10n_ec.sri.ruc.service). Consulta
los endpoints REST públicos del SRI y devuelve los datos del contribuyente
para autocompletar/validar contactos. Es un AbstractModel (sin tabla)."""

import logging
import urllib.parse
import requests
from odoo import api, models

_logger = logging.getLogger(__name__)

SRI_RUC_ENDPOINT = "https://srienlinea.sri.gob.ec/sri-catastro-sujeto-servicio-internet/rest/ConsolidadoContribuyente/obtenerPorNumerosRuc"
SRI_CEDULA_ENDPOINT = "https://srienlinea.sri.gob.ec/sri-catastro-sujeto-servicio-internet/rest/ConsolidadoContribuyente/obtenerPorNumeroCedula"
SRI_NOMBRE_ENDPOINT = "https://srienlinea.sri.gob.ec/sri-catastro-sujeto-servicio-internet/rest/ConsolidadoContribuyente/obtenerPorRazonSocial"

REQUEST_TIMEOUT = 15
_HEADERS = {"Accept": "application/json", "User-Agent": "Odoo/19.0 l10n_ec_sri_community"}


class EcSriRucService(models.AbstractModel):
    _name = 'ec.sri.ruc.service'
    _description = 'Servicio de consulta RUC/Cédula SRI'

    def _get(self, url):
        """GET acotado al SRI; nunca propaga la excepción cruda al usuario."""
        response = requests.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            return data if isinstance(data, list) else []
        if response.status_code == 404:
            return []
        raise requests.HTTPError('SRI HTTP %s' % response.status_code)

    @api.model
    def consultar_ruc(self, ruc):
        ruc = str(ruc or '').strip()
        if len(ruc) != 13 or not ruc.isdigit():
            return {"success": False, "error": "El RUC debe tener 13 dígitos numéricos"}
        try:
            data = self._get("%s?ruc=%s" % (SRI_RUC_ENDPOINT, ruc))
        except requests.Timeout:
            return {"success": False, "error": "Tiempo de espera agotado al consultar el SRI."}
        except requests.ConnectionError:
            return {"success": False, "error": "No se pudo conectar con el SRI."}
        except (requests.RequestException, ValueError) as exc:
            _logger.warning('Consulta RUC fallida: %s', type(exc).__name__)
            return {"success": False, "error": "Error al consultar el SRI."}
        if not data:
            return {"success": False, "error": "RUC %s no encontrado en la base del SRI" % ruc}
        return {"success": True, "data": self._parse_sri_response(data[0])}

    @api.model
    def consultar_cedula(self, cedula):
        cedula = str(cedula or '').strip()
        if len(cedula) != 10 or not cedula.isdigit():
            return {"success": False, "error": "La cédula debe tener 10 dígitos numéricos"}
        try:
            data = self._get("%s?cedula=%s" % (SRI_CEDULA_ENDPOINT, cedula))
        except (requests.RequestException, ValueError) as exc:
            _logger.warning('Consulta cédula fallida: %s', type(exc).__name__)
            return {"success": False, "error": "Error al consultar el SRI."}
        if not data:
            return {"success": False, "error": "Cédula %s no encontrada en el SRI" % cedula}
        return {"success": True, "data": self._parse_sri_response(data[0])}

    @api.model
    def consultar_por_nombre(self, nombre):
        nombre = str(nombre or '').strip()
        if len(nombre) < 3:
            return {"success": False, "error": "Ingrese al menos 3 caracteres para buscar"}
        url = "%s?razonSocial=%s" % (SRI_NOMBRE_ENDPOINT, urllib.parse.quote(nombre.upper()))
        try:
            data = self._get(url)
        except (requests.RequestException, ValueError) as exc:
            _logger.warning('Búsqueda por nombre fallida: %s', type(exc).__name__)
            return {"success": False, "error": "Error al consultar el SRI."}
        if not data:
            return {"success": False, "error": 'No se encontraron contribuyentes con "%s"' % nombre}
        resultados = [self._parse_sri_response(c) for c in data[:20]]
        return {"success": True, "count": len(resultados), "data": resultados}

    @api.model
    def validar_y_cargar_ruc(self, ruc):
        result = self.consultar_ruc(ruc)
        if not result.get("success"):
            return result
        data = result["data"]
        if (data.get("estado") or '').upper() != "ACTIVO":
            return {"success": False, "warning": True, "data": data,
                    "error": "El contribuyente %s tiene estado: %s" % (data.get('razon_social'), data.get('estado'))}
        return result

    def _parse_sri_response(self, data):
        return {
            "ruc": data.get("numeroRuc", ""),
            "razon_social": data.get("razonSocial", ""),
            "nombre_comercial": data.get("nombreComercial", ""),
            "estado": data.get("estadoContribuyente", ""),
            "estado_establecimiento": data.get("estadoEstablecimiento", ""),
            "clase_contribuyente": data.get("claseContribuyente", ""),
            "tipo_contribuyente": data.get("tipoContribuyente", ""),
            "obligado_contabilidad": data.get("obligadoContabilidad", "NO") == "SI",
            "actividad_economica": data.get("actividadEconomicaPrincipal", ""),
            "codigo_actividad": data.get("codigoActividadEconomica", ""),
            "direccion": data.get("direccionMatriz", ""),
            "provincia": data.get("nombreProvincia", ""),
            "canton": data.get("nombreCanton", ""),
            "parroquia": data.get("nombreParroquia", ""),
            "telefono": data.get("telefono1", ""),
            "email": data.get("correo", ""),
            "fecha_inicio_actividades": data.get("fechaInicioActividades", ""),
            "contribuyente_especial": data.get("contribuyenteEspecial", ""),
            "agente_retencion": data.get("agenteRetencion", ""),
            "regimen_rimpe": data.get("regimenRimpe", ""),
        }
