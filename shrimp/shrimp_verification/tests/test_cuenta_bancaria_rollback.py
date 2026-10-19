"""La cuenta bancaria del verificador (alias ver_bank_* sobre res.partner.bank)
no deja escrituras pendientes que apunten a un banco deshecho.

Reproduce lo que hace Odoo al cargar la demo (modules/loading.py, load_demo):
savepoint SIN flush; si algo falla dentro, ROLLBACK TO SAVEPOINT. Antes, el
res.bank creado por la inversa se deshacía pero la escritura de la cuenta
seguía en la caché y el siguiente flush fallaba con
res_partner_bank_bank_id_fkey (instalación entera abortada)."""
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCuentaBancariaRollback(TransactionCase):

    def test_savepoint_sin_flush_no_deja_fk_rota(self):
        socio = self.env["res.partner"].create({"name": "Verificadora rollback", "is_company": True})
        socio.write({"ver_bank_name": "Banco existente rollback",
                     "ver_bank_account_number": "100200300"})
        self.env.flush_all()
        cuenta = socio.bank_ids
        self.assertEqual(len(cuenta), 1)
        banco_antes = cuenta.bank_id
        with self.assertRaises(ZeroDivisionError):
            with self.env.cr.savepoint(flush=False):
                socio.write({"ver_bank_name": "Banco nuevo dentro del savepoint",
                             "ver_bank_account_type": "corriente"})
                1 / 0  # noqa: B018 - la demo falla más adelante
        # Lo que estaba pendiente ya se ejecutó dentro del savepoint: el flush
        # de después no puede fallar.
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(cuenta.bank_id, banco_antes)
        self.assertFalse(self.env["res.bank"].search([("name", "=", "Banco nuevo dentro del savepoint")]))

    def test_banco_existente_se_reutiliza(self):
        banco = self.env["res.bank"].create({"name": "Banco Reutilizado"})
        socio = self.env["res.partner"].create({"name": "Verificadora banco", "is_company": True})
        socio.write({"ver_bank_name": "banco reutilizado", "ver_bank_account_number": "999"})
        self.assertEqual(socio.bank_ids.bank_id, banco)
        # Sin cuenta y sin número no se crea nada (ni el banco).
        otro = self.env["res.partner"].create({"name": "Sin cuenta", "is_company": True})
        otro.write({"ver_bank_name": "Banco que no debe existir"})
        self.assertFalse(otro.bank_ids)
        self.assertFalse(self.env["res.bank"].search([("name", "=", "Banco que no debe existir")]))
