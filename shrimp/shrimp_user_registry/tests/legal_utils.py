"""Ayudas de tests: datos válidos para el asistente de alta (19.0.1.6.0).

El alta exige ahora una cédula/RUC de Ecuador con su dígito verificador, los
datos de la empresa y aceptar el contrato, las políticas y el anexo de cada
perfil (casillas legal_accept_<código> con el código de la versión vigente).
Lo usan los tests de todos los módulos shrimp que dan de alta por HTTP.
"""
import itertools

_SEQ = itertools.count(4321001)


def cedula_valida(n=None):
    """Cédula ecuatoriana válida (provincia 09, tercer dígito < 6)."""
    n = next(_SEQ) if n is None else n
    base = "09%07d" % (n % 10000000)  # 9 dígitos
    base = base[:2] + str(int(base[2]) % 6) + base[3:]
    total = 0
    for i, d in enumerate(base):
        v = int(d) * (2 if i % 2 == 0 else 1)
        total += v - 9 if v > 9 else v
    return base + str((10 - total % 10) % 10)


def ruc_natural_valido(n=None):
    return cedula_valida(n) + "001"


def ruc_sociedad_valido(n=None):
    """RUC de sociedad privada válido (tercer dígito 9, módulo 11 + 001)."""
    while True:
        n = next(_SEQ) if n is None else n
        base = "099%06d" % (n % 1000000)  # 9 dígitos
        pesos = (4, 3, 2, 7, 6, 5, 4, 3, 2)
        dv = (-sum(p * int(d) for p, d in zip(pesos, base))) % 11
        if dv < 10:
            return base + str(dv) + "001"
        n = None


def signup_post_juridica(env, roles, email, **extra):
    """POST válido de una PERSONA JURÍDICA (19.0.1.8.0)."""
    datos = signup_post(env, roles, email, **{
        "shrimp_person_type": "juridica",
        "vat_or_id": ruc_sociedad_valido(),
        "shrimp_razon_social": "Sociedad %s S.A." % email.split("@")[0],
        "shrimp_nombre_comercial": "Comercial %s" % email.split("@")[0],
        "shrimp_representante": "Representante %s" % email.split("@")[0],
    })
    datos.pop("name", None)
    datos.update(extra)
    return datos


def legal_accept_fields(env, roles):
    """Casillas de aceptación de los documentos generales y de los anexos de `roles`."""
    docs = env["shrimp.legal.document"].sudo()._shrimp_signup_documents(list(roles))
    return {"legal_accept_%s" % d.code: v.name for d, v in docs}


def signup_post(env, roles, email, name=None, vat=None, **extra):
    """POST completo y válido para /register/submit (sin csrf_token)."""
    if isinstance(roles, str):
        roles = [roles]
    estado = env["res.country.state"].sudo().search([("country_id.code", "=", "EC")], limit=1)
    datos = {
        "shrimp_user_types": list(roles),
        "name": name or "Alta %s" % email,
        "email": email,
        "password": "Clave-segura-123",
        "password_confirm": "Clave-segura-123",
        "vat_or_id": vat or ruc_natural_valido(),
        "shrimp_person_type": "natural",
        "shrimp_contact_name": "Persona %s" % email.split("@")[0],
        "phone": "0991234567",
        "state_id": str(estado.id),
        "city": "Machala",
        "street": "Av. 25 de Junio 100",
        # Datos de perfil que piden los bloques del asistente. La camaronera
        # ya no manda hectáreas (19.0.1.10.1) ni la empacadora el nombre de
        # la planta (shrimp_packer 19.0.1.14.1): no son obligatorios.
        "pack_capacidad_lb_semana": "10000",
    }
    datos.update(legal_accept_fields(env, roles))
    datos.update(extra)
    return datos
