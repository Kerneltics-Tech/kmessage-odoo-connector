# -*- coding: utf-8 -*-
"""One extra switch on a capability: how much of the stock figure to say out loud."""

from odoo import fields, models


class KMessageCapability(models.Model):
    _inherit = 'kmessage.capability'

    show_exact_quantity = fields.Boolean(
        string='Say the exact quantity',
        help="Off, the availability answer is a band — in stock, only a few left, "
             "out of stock — and the number itself never leaves Odoo. Most shops want "
             "this: the figure tells a competitor what you buy and how fast you sell, "
             "and it is wrong the moment somebody at the counter picks the item up. "
             "Turn it on where the exact number is genuinely what a customer needs.")
