# -*- coding: utf-8 -*-
"""The three moments in accounting worth a message.

Core knows how to run a trigger; it does not know what any of them mean. This
is where "an invoice was posted" becomes something an automation can be pointed
at — and the reason an Odoo without accounting never offers to message people
about invoices, because the option only exists while this module is installed.
"""

from odoo import _, api, models

#: Trigger codes are stored on every automation row, so they are part of the
#: database rather than an implementation detail: renaming one silently
#: switches off whatever rule was using it.
TRIGGER_INVOICE_POSTED = 'account.move.posted'
TRIGGER_INVOICE_DUE = 'account.move.due'
TRIGGER_PAYMENT_RECEIVED = 'account.payment.received'

INVOICE_TRIGGERS = (TRIGGER_INVOICE_POSTED, TRIGGER_INVOICE_DUE)

#: A week, where core's cooldown starts at a day. An invoice is posted once,
#: but it falls due and then stays due: the cron offers the same overdue
#: invoice again every night, so a day-long cooldown is a daily chase, which is
#: how a reminder turns into the reason somebody blocks the number. This only
#: decides where the field starts — a rule that says otherwise is left alone.
DUE_COOLDOWN_HOURS = 24 * 7


class KMessageAutomation(models.Model):
    _inherit = 'kmessage.automation'

    @api.model
    def _selection_trigger(self):
        return super()._selection_trigger() + [
            # Named for what actually reaches it: a credit note is posted the
            # same way an invoice is, and a rule that does not want them says
            # so with a filter of move_type.
            (TRIGGER_INVOICE_POSTED, _("An invoice or credit note is posted")),
            (TRIGGER_INVOICE_DUE, _("An invoice falls due")),
            (TRIGGER_PAYMENT_RECEIVED, _("A customer pays")),
        ]

    @api.model
    def _trigger_model(self, trigger):
        if trigger in INVOICE_TRIGGERS:
            return 'account.move'
        if trigger == TRIGGER_PAYMENT_RECEIVED:
            return 'account.payment'
        return super()._trigger_model(trigger)

    # -- where the due-date cooldown starts -------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Start a due-date rule on the longer cooldown, unless told otherwise."""
        for values in vals_list:
            if values.get('trigger') == TRIGGER_INVOICE_DUE and 'cooldown_hours' not in values:
                values['cooldown_hours'] = DUE_COOLDOWN_HOURS
        return super().create(vals_list)

    @api.onchange('trigger')
    def _onchange_trigger_cooldown(self):
        """Show the longer cooldown as soon as the due-date trigger is picked.

        Only where the field is still at core's own default: somebody who has
        typed a number has said what they want, and picking the trigger again
        must not quietly undo it.
        """
        default = self.default_get(['cooldown_hours']).get('cooldown_hours')
        for automation in self:
            if automation.trigger == TRIGGER_INVOICE_DUE and automation.cooldown_hours == default:
                automation.cooldown_hours = DUE_COOLDOWN_HOURS
