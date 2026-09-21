# -*- coding: utf-8 -*-
"""“It has left the warehouse” — the one thing a customer wants told to them.

The trigger and the hook that fires it live in the same file on purpose: what
``stock.picking.done`` means is decided by the two filters below that pick out
which pickings count, and reading one without the other leaves the question
half answered.

Validating a transfer is a business transaction, so the call into the connector
comes after ``super()`` and cannot fail into it — ``run_trigger`` swallows its
own problems, and a WhatsApp outage must never be able to unpost a delivery.
"""

from odoo import _, api, models

#: Goods have left the building, and the customer is the one they went to.
TRIGGER_PICKING_DONE = 'stock.picking.done'


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        # Which of these were still to validate, asked before ``super()`` has
        # the chance to change the answer. Odoo drops the ones already done and
        # validates the rest; a set read afterwards cannot tell the two apart,
        # and a second click on Validate would then be a second message about a
        # delivery the customer has already been told about.
        pending = self.filtered(lambda picking: picking.state not in ('done', 'cancel'))
        result = super().button_validate()
        # Validating can open a wizard — a backorder to confirm, quantities to
        # fill — and return without anything being validated at all. Only the
        # pickings that really reached 'done' are news.
        delivered = pending.filtered(
            lambda picking: picking.state == 'done'
            and picking.picking_type_id.code == 'outgoing')
        if delivered:
            self.env['kmessage.automation'].run_trigger(TRIGGER_PICKING_DONE, delivered)
        return result


class KMessageAutomation(models.Model):
    _inherit = 'kmessage.automation'

    @api.model
    def _selection_trigger(self):
        return super()._selection_trigger() + [
            (TRIGGER_PICKING_DONE, _("A delivery is validated")),
        ]

    @api.model
    def _trigger_model(self, trigger):
        if trigger == TRIGGER_PICKING_DONE:
            return 'stock.picking'
        return super()._trigger_model(trigger)
