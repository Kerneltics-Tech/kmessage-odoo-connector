# -*- coding: utf-8 -*-
"""Offering the sales triggers to the automation rules.

Core knows how to run a trigger but not what any of them mean, so a trigger
exists only while the module that fires it is installed. That is the point: a
company with no sales app is never shown a rule it could not use, and removing
this module removes both entries from the list rather than leaving rules
pointing at an event nothing will ever raise.
"""

from odoo import _, api, models

from .sale_order import TRIGGER_CONFIRMED, TRIGGER_QUOTATION_SENT


class KMessageAutomation(models.Model):
    _inherit = 'kmessage.automation'

    @api.model
    def _selection_trigger(self):
        return super()._selection_trigger() + [
            (TRIGGER_QUOTATION_SENT, _("Quotation sent to the customer")),
            (TRIGGER_CONFIRMED, _("Sales order confirmed")),
        ]

    @api.model
    def _trigger_model(self, trigger):
        if trigger in (TRIGGER_QUOTATION_SENT, TRIGGER_CONFIRMED):
            return 'sale.order'
        return super()._trigger_model(trigger)
