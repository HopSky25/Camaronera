{
    "name": "Camaronera — Servicio de empaque (co-packing)",
    "version": "19.0.1.0.0",
    "summary": "Maquiladores que empacan camaron ajeno, y el acta que cuadra las libras",
    "description": """
Hay plantas que no compran camaron: lo empacan para otros. La camaronera o la
empacadora que no se abastece les manda su producto ya listo para empacar, con
sus propios insumos, y paga por libra empacada.

Lo que resuelve este modulo no es el rendimiento —no hay merma que conciliar,
el camaron llega limpio— sino dos cosas:

1. El cuadre. Entraron 40.000 libras, tienen que salir 40.000 empacadas. El
   acta la firman las dos partes y deja constancia de la diferencia si la hay.

2. La trazabilidad. Hoy, cuando un lote se va a empacar fuera, la cadena se
   corta: el certificado no puede decir donde se empaco ni bajo que codigo de
   establecimiento. Con el maquilador dentro de la plataforma, el paso queda
   registrado y sale impreso en el certificado del lote.

Va en modulo aparte, como verificacion, porque es un rol distinto con su propio
sitio y sus propias bandejas.
""",
    "author": "Carlos Carballo",
    "license": "LGPL-3",
    "category": "Industries",
    "depends": ["shrimp_marketplace", "shrimp_user_registry", "shrimp_packer", "website"],
    "data": [
        "data/sequences.xml",
        "security/ir.model.access.csv",
        "security/shrimp_copacking_rules.xml",
        "views/copack_client_templates.xml",
        "views/copack_request_templates.xml",
        "views/copack_order_templates.xml",
    ],
    "installable": True,
}
