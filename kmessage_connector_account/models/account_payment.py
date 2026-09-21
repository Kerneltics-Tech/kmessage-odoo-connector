# -*- coding: utf-8 -*-
"""“We have your payment, thank you.”

The one message in this module a customer never has to ask for, and the one
they notice is missing. It rides on ``action_post`` rather than on the journal
entry, because what a customer is being told about is the payment they made,
not the entry it produced.
"""

from odoo import models

from .kmessage_automation import TRIGGER_PAYMENT_RECEIVED


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def action_post(self):
        result = super().action_post()
        # Money coming in from a customer. Nothing here checks the state: Odoo
        # 17 and 18 spell a posted payment's state differently, and once
        # ``super()`` has returned these payments are posted by definition.
        received = self.filtered(
            lambda payment: payment.payment_type == 'inbound'
            and payment.partner_type == 'customer'
            and payment.partner_id)
        if received:
            self.env['kmessage.automation'].run_trigger(TRIGGER_PAYMENT_RECEIVED, received)
        return result
