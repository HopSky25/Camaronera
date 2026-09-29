from datetime import date, timedelta

from odoo.tests.common import TransactionCase


class CopackCommon(TransactionCase):
    """Montaje comun: un maquilador, un cliente y sus usuarios de portal.

    Se crea todo aqui y no en los datos de demo porque los tests tienen que
    correr en una base sin demo: si dependieran de ella, la bateria solo
    valdria en el equipo de desarrollo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Socio = cls.env["res.partner"]
        portal = cls.env.ref("base.group_portal")

        cls.maq = Socio.create({
            "name": "Maquiladora de prueba", "is_company": True,
            "email": "maquilador@prueba.test", "vat_or_id": "0955500001001",
            "shrimp_user_type": "maquilador",
            "pack_razon_social": "Maquiladora de prueba S.A.",
            "pack_ubicacion": "Guayaquil",
            "pack_codigo_establecimiento": "99321",
            "pack_capacidad_lb_semana": 200000,
            "pack_lote_minimo_lb": 5000,
        })
        cls.cli = Socio.create({
            "name": "Camaronera de prueba", "is_company": True,
            "email": "cliente@prueba.test", "vat_or_id": "0955500002001",
            "shrimp_user_type": "camaronera",
            "farm_razon_social": "Camaronera de prueba S.A.",
            "farm_representante": "Representante", "farm_telefono": "04-0000000",
            "farm_ubicacion": "Guayas",
        })
        cls.otro_cli = Socio.create({
            "name": "Camaronera ajena", "is_company": True,
            "email": "ajena@prueba.test", "vat_or_id": "0955500003001",
            "shrimp_user_type": "camaronera",
            "farm_razon_social": "Camaronera ajena S.A.",
            "farm_representante": "Otro", "farm_telefono": "04-1111111",
            "farm_ubicacion": "Guayas",
        })

        def usuario(socio, login):
            return cls.env["res.users"].create({
                "name": socio.name, "login": login, "partner_id": socio.id,
                "group_ids": [(6, 0, [portal.id])],
            })

        cls.u_maq = usuario(cls.maq, "maq@prueba.test")
        cls.u_cli = usuario(cls.cli, "cli@prueba.test")
        cls.u_otro = usuario(cls.otro_cli, "otro@prueba.test")

    def solicitud(self, libras=40000, dirigida=True, cliente=None):
        s = self.env["shrimp.copack.request"].create({
            "client_partner_id": (cliente or self.cli).id,
            "quantity_lb": libras, "presentation": "entero",
            "needed_from": date.today(),
            "needed_to": date.today() + timedelta(days=7),
            "copacker_partner_id": self.maq.id if dirigida else False,
        })
        s.action_publish()
        return s

    def adjudicar(self, sol, tarifa=0.18):
        oferta = self.env["shrimp.copack.offer"].create({
            "request_id": sol.id, "copacker_partner_id": self.maq.id,
            "rate_per_lb": tarifa, "capacity_lb": sol.quantity_lb,
            "available_from": date.today(),
            "available_to": date.today() + timedelta(days=6),
        })
        return oferta, oferta.action_accept(actor=sol.client_partner_id)

    def hasta_empacar(self, recibidas=40000, empacadas=39880):
        sol = self.solicitud()
        _, orden = self.adjudicar(sol)
        orden.write({"received_lb": recibidas})
        orden.action_register_reception()
        orden.write({"packed_lb": empacadas})
        orden.action_register_packing()
        return sol, orden
