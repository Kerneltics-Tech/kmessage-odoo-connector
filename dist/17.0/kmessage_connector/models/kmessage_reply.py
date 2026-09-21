# -*- coding: utf-8 -*-
"""What happens in Odoo when a customer taps a button.

Sending a document and getting a reply back is only half a conversation. The
half that earns its keep is the tap that *does* something: "yes, the amount
arrived", "no, that is not my car", "confirm the appointment" — answered on
WhatsApp, recorded in Odoo, with nobody retyping anything.

A rule here is the join between the two. It matches the button that came back
against the message Odoo sent, and runs a server action on the record that
message was about. The record is not guessed: it comes from the reference Odoo
put on the outbound message, so a reply can only ever act on the thing it was
a reply to.

Nothing is built in. Deciding that a particular button means "mark this invoice
approved" is a decision about somebody's business, so it lives in a rule they
wrote, not in this addon.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class KMessageReplyRule(models.Model):
    _name = 'kmessage.reply.rule'
    _description = 'K-Message Reply Rule'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(
        default=False,
        help="Rules start switched off. Nothing runs in Odoo because of a tap "
             "until somebody turns this on.")
    account_id = fields.Many2one(
        'kmessage.account', required=True, ondelete='cascade', index=True,
        default=lambda self: self.env['kmessage.account']._for_company())
    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    # -- what it matches --------------------------------------------------
    button_id = fields.Char(
        help="The button's payload, as K-Message sends it back. Leave empty to match "
             "on the visible text instead.")
    button_text = fields.Char(
        help="Matched when the payload is empty or does not match: the words on the "
             "button, compared without case. Leave both empty to match every tap.")
    automation_id = fields.Many2one(
        'kmessage.automation',
        help="Only replies to messages this automation sent. Empty means any message.")
    model_id = fields.Many2one(
        'ir.model', string='On records of',
        help="Only replies about this kind of record. Empty means any.")
    model_name = fields.Char(related='model_id.model', store=True)

    # -- what it does -----------------------------------------------------
    server_action_id = fields.Many2one(
        'ir.actions.server', string='Run',
        domain="[('model_id', '=', model_id)]",
        help="The action to run, with the record the message was about. Written in "
             "Odoo's own automation screen, so it can do anything a scheduled action can.")
    reply_template_id = fields.Many2one(
        'kmessage.template', string='Answer with',
        domain="[('account_id', '=', account_id), ('usable', '=', True)]",
        help="Optional: a template sent back to the customer once the action has run, "
             "so a tap is acknowledged rather than swallowed.")
    once_only = fields.Boolean(
        string='Only the first tap', default=True,
        help="People tap twice. With this on, the second tap is recorded and ignored "
             "rather than running the action again.")

    run_count = fields.Integer(readonly=True, default=0, copy=False)
    last_run = fields.Datetime(readonly=True, copy=False)

    @api.constrains('server_action_id', 'reply_template_id')
    def _check_does_something(self):
        for rule in self:
            if not rule.server_action_id and not rule.reply_template_id:
                raise UserError(_(
                    "A rule that neither runs an action nor answers the customer "
                    "would do nothing at all."))

    # -- matching ---------------------------------------------------------
    @api.model
    def for_reply(self, account, button_id, button_text, message):
        """The live rules that match this tap, most specific first.

        An event with no connection on it — the connection was deleted after
        the event was logged — matches nothing. Falling back to an empty domain
        would run every company's rules on somebody else's tap.
        """
        if not account:
            return self.browse()
        rules = self.sudo().search([('account_id', '=', account.id)])

        def matches(rule):
            if rule.automation_id and message:
                # The reference an automation writes starts with its own id.
                if not (message.reference or '').startswith('%s:' % rule.automation_id.id):
                    return False
            elif rule.automation_id:
                return False
            if rule.model_name and (not message or message.res_model != rule.model_name):
                return False
            if rule.button_id:
                return (button_id or '') == rule.button_id
            if rule.button_text:
                return (button_text or '').strip().lower() == rule.button_text.strip().lower()
            return True

        return rules.filtered(matches)

    # -- running ----------------------------------------------------------
    def run_for(self, message, event):
        """Run this rule for the message that was replied to.

        Returns a sentence for the event log. Never raises: the webhook must
        answer 200 once its signature is good, and a rule somebody wrote badly
        is not a reason to make K-Message retry the same tap for an hour.
        """
        self.ensure_one()
        record = None
        if message and message.res_model and message.res_id:
            record = self.env[message.res_model].browse(message.res_id).exists()

        if self.once_only and self._already_ran(message):
            return _("“%s” had already run for this message.", self.name)

        outcome = []
        if self.server_action_id:
            if not record:
                return _("“%s” needs the record the message was about, and it is gone.", self.name)
            try:
                self.server_action_id.sudo().with_context(
                    active_model=record._name,
                    active_id=record.id,
                    active_ids=record.ids,
                    kmessage_event_id=event.id,
                ).run()
                outcome.append(_("ran %s", self.server_action_id.name))
            except Exception as error:  # noqa: BLE001 - a bad rule is not a failed delivery
                _logger.exception('K-Message: reply rule %s failed', self.name)
                return _("“%(rule)s” failed: %(error)s", rule=self.name, error=error)

        if self.reply_template_id and event.contact_phone:
            self.env['kmessage.message'].sudo().enqueue(
                account=self.account_id,
                phone=event.contact_phone,
                template=self.reply_template_id,
                partner=event.partner_id or None,
                record=record,
            )
            outcome.append(_("answered the customer"))

        self.sudo().write({
            'run_count': self.run_count + 1,
            'last_run': fields.Datetime.now(),
        })
        # Written on the event rather than left to be read out of the outcome
        # sentence: that is what the next tap on this message looks at.
        event.sudo().write({'rule_ids': [(4, self.id)]})
        return _("“%(rule)s”: %(what)s.", rule=self.name, what=', '.join(outcome))

    def _already_ran(self, message):
        """True when an earlier tap on this same message already ran this rule.

        Asked of the rule ids recorded on the earlier events, not of the
        sentence they put in ``outcome``: that sentence is translated, and
        looking for the rule's name inside it made a rule called “Pay” count a
        run of one called “Payment”, while a name holding a ``%`` matched
        almost anything.
        """
        self.ensure_one()
        if not message:
            return False
        return bool(self.env['kmessage.event'].sudo().search_count([
            ('id', '!=', self.env.context.get('kmessage_current_event', 0)),
            ('account_id', '=', self.account_id.id),
            ('reference', '=', message.reference),
            ('event', '=', 'button.reply'),
            ('rule_ids', 'in', self.ids),
        ]))

    def action_toggle(self):
        for rule in self:
            rule.active = not rule.active
