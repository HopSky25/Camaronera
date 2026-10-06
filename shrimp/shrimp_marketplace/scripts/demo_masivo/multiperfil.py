# -*- coding: utf-8 -*-
"""Cuentas con varios perfiles (shrimp.partner.role).

Se ejecuta el ÚLTIMO y no usa el generador aleatorio: agrega secciones al
final de archivos ya generados, así que el resto de la demo sale idéntico.

- Un laboratorio que también es camaronera (perfil aprobado).
- Una camaronera que también es empacadora (aprobada) y otra con la
  solicitud de empacadora PENDIENTE (aparece en «Perfiles por aprobar»).
- Una empacadora que también es maquiladora (aprobada).

Los datos del perfil común que el nuevo rol exige se completan con
escrituras idempotentes (bloque noupdate="0"), para que también lleguen a
una base que ya tenía la demo al hacer -u.
"""
from .comun import D, DT, MP, R, SP, UR, r2  # noqa: F401

UC = "shrimp_copacking."


def _archivo(M, modulo, nombre):
    for f in M["archivos"]:
        if f.modulo == modulo and f.nombre == nombre:
            return f
    raise KeyError("%s/%s" % (modulo, nombre))


def construir(M):
    M.setdefault("multiperfil", [])

    # ------------------------------------------------ laboratorio + camaronera
    f = _archivo(M, "shrimp_user_registry", "demo_masivo_02_socios.xml")
    f.seccion("Varios perfiles por cuenta: laboratorio que también es camaronera")
    lab = M["lab"][0]
    p = M["partners"][lab]
    f.rec("demo_mp_%s_camaronera" % lab, "shrimp.partner.role", {
        "partner_id": R(lab), "role": "camaronera", "state": "approved",
        "notes": "Demo multiperfil: laboratorio con finca de engorde propia.",
    })
    f.fn_write("res.partner", [UR + lab], [("shrimp_representante", "=", False)], {
        "shrimp_representante": p["rep"], "shrimp_telefono": "04-2600101", "farm_area_ha": 42.0,
    })
    M["multiperfil"].append((p["nombre"], "laboratorio + camaronera", p.get("user")))

    # ------------------------------------------------ camaronera + empacadora
    f = _archivo(M, "shrimp_packer", "demo_04_masivo_empacadoras.xml")
    f.seccion("Varios perfiles por cuenta: camaroneras que también son empacadoras")
    for cam, estado, planta, codigo in (
            (M["cam"][7], "approved", "Planta propia de proceso", "EXP-MP01"),
            (M["cam"][9], "pending", "Planta en habilitación", False)):
        pc = M["partners"][cam]
        f.rec("demo_mp_%s_empacadora" % cam, "shrimp.partner.role", {
            "partner_id": R(UR + cam), "role": "empacadora", "state": estado,
            "notes": ("Demo multiperfil: procesa su propia cosecha y compra a terceros."
                      if estado == "approved" else
                      "Demo multiperfil: solicitud pendiente de revisión de la planta."),
        })
        f.fn_write("res.partner", [UR + cam], [("emp_planta_nombre", "=", False)], {
            "emp_planta_nombre": planta, "emp_codigo_exportador": codigo,
            "emp_contacto_comercial": pc["rep"],
        })
        M["multiperfil"].append((pc["nombre"], "camaronera + empacadora (%s)" % (
            "aprobada" if estado == "approved" else "pendiente"), pc.get("user")))

    # ------------------------------------------------ empacadora + maquilador
    f = _archivo(M, "shrimp_copacking", "demo_01_maquiladores.xml")
    f.seccion("Varios perfiles por cuenta: empacadora que también presta servicio de empaque")
    emp = "demo_ms_emp_01"
    f.rec("demo_mp_%s_maquilador" % emp, "shrimp.partner.role", {
        "partner_id": R(SP + emp), "role": "maquilador", "state": "approved",
        "notes": "Demo multiperfil: alquila capacidad ociosa de su planta.",
    })
    f.fn_write("res.partner", [SP + emp], [("pack_codigo_establecimiento", "=", False)], {
        "pack_codigo_establecimiento": "EST-MP-0101", "pack_presentaciones": "Entero y cola",
        "pack_lote_minimo_lb": 8000.0, "pack_desde_entero": 0.18, "pack_desde_cola": 0.26,
    })
    M["multiperfil"].append(("demo_ms_emp_01 (shrimp_packer)", "empacadora + maquilador", None))

    # ------------------------------------- empaque propio (self-packing)
    # La empacadora + maquilador empaca en SU planta parte de una compra
    # propia: sin solicitud, ofertas, tarifa, comisión ni acta de dos partes.
    # Se crea empacada y se cierra con el cierre interno (action_self_close),
    # que mueve el inventario como un empaque de terceros (merma consumida y
    # lote empacado) y la deja en la trazabilidad de esa compra rotulada
    # «Empaque propio — empacado por … (planta EST-MP-0101)».
    compra = next((t for t in M.get("compras_adulto", [])
                   if t["buyer"] == SP + emp and t["estado"] == "done"
                   and t.get("lot_comprador")), None)
    if compra:
        f3 = _archivo(M, "shrimp_copacking", "demo_03_solicitudes_ordenes.xml")
        f3.seccion("Empaque propio: la empacadora + maquilador empaca su propia compra")
        acordado = float(round(min(compra["qty"], 20000.0) * 0.5 / 10) * 10)
        recibido = acordado
        empacado = r2(recibido * 0.996)
        xid = "demo_mp_selfpack_01"
        f3.rec(xid, "shrimp.copack.order", {
            "self_packing": True,
            "client_partner_id": R(SP + emp), "copacker_partner_id": R(SP + emp),
            "transaction_id": R(SP + compra["xid"]), "product_id": R(SP + compra["prod"]["xid"]),
            "stock_lot_id": R(SP + compra["lot_comprador"]),
            "currency_id": R("base.USD"), "agreed_qty_lb": acordado, "agreed_overrun_pct": 10.0,
            "rate_per_lb": 0.0, "platform_rate_per_lb": 0.0, "tolerance_pct": 0.5,
            "supplies_notes": "Insumos propios de la planta: master 6 x 2 kg y etiquetas con su código.",
            "supplies_received": True,
            "received_lb": recibido, "received_date": DT(-3, 7, 0),
            "packed_lb": empacado, "packed_date": DT(-3, 16, 0),
            "boxes": int(empacado / 26.4), "packed_presentation": "entero",
            "packed_presentation_note": "Master 6 x 2 kg (bloque)",
            "state": "packed", "acceptance_state": "na"})
        f3.funcion("shrimp.copack.order", "action_self_close",
                   "[[ref('shrimp_copacking.%s')]]" % xid)
        M["multiperfil"].append(("demo_ms_emp_01 (shrimp_packer)",
                                 "empaque propio %s (%s)" % (xid, compra["xid"]), None))
