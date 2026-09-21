# -*- coding: utf-8 -*-
"""A connection made after this module was installed still gets its tools."""

from odoo import api, models


class KMessageAccount(models.Model):
    _inherit = 'kmessage.account'

    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        self.env['kmessage.ai.tool']._kmessage_seed_sale_tools(accounts)
        return accounts
