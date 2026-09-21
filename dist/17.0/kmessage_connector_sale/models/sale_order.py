# -*- coding: utf-8 -*-
"""The two moments in a sale that a customer is waiting to hear about.

Both hooks do the same small thing: let the sale finish first, then hand the
records to :meth:`kmessage.automation.run_trigger` and return whatever Odoo was
going to return anyway. Nothing here decides that a message goes out — that is
the automation rules' job, and there are none until a user writes one — and
nothing here can stop an order being confirmed. ``run_trigger`` swallows its own
failures on purpose, so a WhatsApp outage costs a log line, not a sale.
"""

from odoo import models

#: The codes automations are stored against. Renaming one does not migrate the
#: rules that use it; it silently stops them matching, so they do not change.
TRIGGER_QUOTATION_SENT = 'sale.order.quotation_sent'
TRIGGER_CONFIRMED = 'sale.order.confirmed'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        result = super().action_confirm()
        confirmed = self.filtered(lambda order: order.state == 'sale')
        self.env['kmessage.automation'].run_trigger(TRIGGER_CONFIRMED, confirmed)
        return result

    def write(self, vals):
        """Catch a quotation being sent as the state change, not as the button.

        ``action_quotation_send`` is the obvious hook and the wrong one. It only
        opens the mail composer — for a single order in 17.0, for a selection in
        18.0 — and an opened composer is not a customer receiving anything; the
        person may still close it. What both versions do agree on is that a
        quotation becomes ``sent`` by a write: from “Mark as sent”, from the
        composer once the mail is actually posted, or from the portal. Watching
        that transition fires once, at the moment the customer really has the
        quotation, on either version.
        """
        becoming_sent = (
            self.filtered(lambda order: order.state != 'sent')
            if vals.get('state') == 'sent' else self.browse()
        )
        result = super().write(vals)
        if becoming_sent:
            self.env['kmessage.automation'].run_trigger(TRIGGER_QUOTATION_SENT, becoming_sent)
        return result
