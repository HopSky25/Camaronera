# Copyright 2024 Tecnativa - Víctor Martínez
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models


class HrEmployee(models.Model):
    # 19.0 removed hr.employee.base, the abstract model that hr.employee and
    # hr.employee.public used to share, so the mixin is applied to each of them
    # separately (see hr_employee_public.py).
    _name = "hr.employee"
    _inherit = ["hr.employee", "dms.field.mixin"]
