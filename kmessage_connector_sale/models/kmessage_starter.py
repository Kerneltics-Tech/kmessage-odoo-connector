# -*- coding: utf-8 -*-
"""The order templates, written so a company need not write them."""

from odoo import api, models


class KMessageStarter(models.AbstractModel):
    _inherit = 'kmessage.starter'

    @api.model
    def catalogue(self):
        entries = super().catalogue()
        entries.append(self._entry(
            name='odoo_quotation_sent',
            display_name='Odoo — quotation sent',
            body='مرحباً {{1}}،\n\nعرض السعر رقم {{2}} بمبلغ {{3}} مرفق أعلاه.\n\nفي انتظار ردك.',
            samples=['عبدالله', 'S00042', '1,250.00 ر.س'],
        ))
        entries.append(self._entry(
            name='odoo_order_confirmed',
            display_name='Odoo — order confirmed',
            body='تم تأكيد طلبك يا {{1}}.\n\nرقم الطلب {{2}} والمبلغ {{3}}. سنخبرك فور شحنه.',
            samples=['عبدالله', 'S00042', '1,250.00 ر.س'],
            header_type=None,
        ))
        return entries
