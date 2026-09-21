# -*- coding: utf-8 -*-
"""The delivery template, written so a company need not write it."""

from odoo import api, models


class KMessageStarter(models.AbstractModel):
    _inherit = 'kmessage.starter'

    @api.model
    def catalogue(self):
        entries = super().catalogue()
        entries.append(self._entry(
            name='odoo_delivery_on_its_way',
            display_name='Odoo — delivery on its way',
            body='طلبك في الطريق يا {{1}}.\n\nرقم الشحنة {{2}}، وتفاصيلها مرفقة أعلاه.',
            samples=['عبدالله', 'WH/OUT/00031'],
        ))
        return entries
