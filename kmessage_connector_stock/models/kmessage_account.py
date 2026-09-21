# -*- coding: utf-8 -*-
"""A new connection arrives with this bridge's assistant tools already drafted."""

from odoo import api, models


class KMessageAccount(models.Model):
    _inherit = 'kmessage.account'

    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        # The usual order of events is Inventory, then this bridge, then — days
        # later — somebody pasting a token. This is the moment the tools can
        # first exist, so it is where they are drafted.
        self.env['kmessage.ai.tool']._kmessage_seed_stock_tools(accounts)
        return accounts
