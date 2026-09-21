# -*- coding: utf-8 -*-
"""Everything K-Message tells this Odoo, written down before it is acted on.

The log exists for the question that always comes: “did it reach us?” Storing
the event first and routing it second means the answer is in the database even
when the routing then failed.
"""

import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class KMessageEvent(models.Model):
    _name = 'kmessage.event'
    _description = 'K-Message Inbound Event'
    _order = 'create_date desc, id desc'
    _rec_name = 'event'

    account_id = fields.Many2one('kmessage.account', index=True, ondelete='set null')
    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    event = fields.Char(required=True, index=True)
    remote_message_id = fields.Char(index=True)
    contact_phone = fields.Char(index=True)
    contact_name = fields.Char()
    partner_id = fields.Many2one('res.partner', index=True)
    reference = fields.Char(index=True, help="What we sent with the original message, echoed back.")
    payload = fields.Text()

    handled = fields.Boolean(default=False, index=True)
    outcome = fields.Char(help="What was done about it, or why nothing was.")
    rule_ids = fields.Many2many(
        'kmessage.reply.rule', string='Rules that ran', copy=False,
        help="The reply rules this tap actually ran, which is how a second tap on "
             "the same message is recognised as a repeat.")

    @api.model
    def record_event(self, account, envelope):
        """Store one webhook envelope and route it. Never raises."""
        data = (envelope or {}).get('data') or {}
        in_reply_to = data.get('in_reply_to') or {}
        phone = data.get('contact_phone') or data.get('phone_number') or ''

        event = self.sudo().create({
            'account_id': account.id if account else False,
            'event': (envelope or {}).get('event') or 'unknown',
            'remote_message_id': data.get('message_id') or in_reply_to.get('message_id') or '',
            'contact_phone': phone,
            'contact_name': data.get('contact_name') or '',
            'reference': in_reply_to.get('reference') or data.get('reference') or '',
            'payload': json.dumps(envelope, ensure_ascii=False)[:60000],
            'partner_id': self._partner_for(phone, account).id if phone else False,
        })
        try:
            event._route()
        except Exception:  # noqa: BLE001 - a bad handler must not make us answer 500
            _logger.exception('K-Message: could not route event %s', event.event)
            event.write({'outcome': _("Handler failed; see the server log.")})
        return event

    @api.model
    def _partner_for(self, phone, account):
        company = account.company_id if account else self.env.company
        return self.env['res.partner'].sudo()._kmessage_find_by_phone(phone, company)

    def _route(self):
        """Do whatever this event means. Unknown events are recorded, not refused."""
        self.ensure_one()
        handler = getattr(self, '_handle_%s' % self.event.replace('.', '_'), None)
        if not handler:
            self.write({'handled': True, 'outcome': _("No action needed for this event.")})
            return
        outcome = handler() or _("Handled.")
        self.write({'handled': True, 'outcome': outcome})

    # -- handlers ---------------------------------------------------------
    def _handle_message_sent(self):
        """Delivery news about something we sent."""
        return self._update_delivery('sent')

    def _handle_message_incoming(self):
        """A customer wrote to us. Recorded; the chatter gets it when we know who."""
        if not self.partner_id:
            return _("From a number we do not recognise.")
        body = ((json.loads(self.payload or '{}').get('data') or {}).get('content') or {}).get('body')
        if body:
            self.partner_id.sudo().message_post(
                body=_("WhatsApp message received: %s", body),
                message_type='comment')
            return _("Noted on %s.", self.partner_id.display_name)
        return _("Recorded.")

    def _handle_button_reply(self):
        """The customer tapped a button on a message we sent.

        Two things happen, in this order: the tap is written where a person
        will see it, and then any rule the company wrote for that button runs.
        The order matters — a rule that fails still leaves the tap recorded.
        """
        data = (json.loads(self.payload or '{}').get('data') or {})
        button = data.get('button') or {}
        label = button.get('text') or button.get('id') or ''

        # Scoped to the connection this webhook was signed for. A reference is
        # guessable — it is a model name and an id — and without the scope a
        # tenant holding one company's webhook secret could name another
        # company's message and have a reply rule act on its record.
        message = self.env['kmessage.message'].sudo().search([
            ('reference', '=', self.reference),
            ('account_id', '=', self.account_id.id),
        ], limit=1) if (self.reference and self.account_id) else self.env['kmessage.message']

        notes = []
        record = None
        if message and message.res_model and message.res_id:
            record = self.env[message.res_model].sudo().browse(message.res_id).exists()
        if record and hasattr(record, 'message_post'):
            record.message_post(body=_("The customer answered on WhatsApp: %s", label))
            notes.append(_("recorded on %s", record.display_name))
        elif self.partner_id:
            self.partner_id.sudo().message_post(body=_("Answered on WhatsApp: %s", label))
            notes.append(_("noted on %s", self.partner_id.display_name))

        rules = self.env['kmessage.reply.rule'].sudo().for_reply(
            self.account_id, button.get('id'), button.get('text'), message)
        for rule in rules:
            notes.append(rule.with_context(kmessage_current_event=self.id).run_for(message, self))

        if not notes:
            return _("Recorded: %s", label)
        return '; '.join(str(note) for note in notes)

    def _handle_contact_created(self):
        if self.partner_id:
            return _("Already a customer here.")
        return _("New contact on WhatsApp; no matching customer in Odoo.")

    def _update_delivery(self, status):
        if not self.remote_message_id:
            return _("No message id to match.")
        message = self.env['kmessage.message'].sudo().search([
            ('remote_message_id', '=', self.remote_message_id),
            ('account_id', '=', self.account_id.id),
        ], limit=1) if self.account_id else self.env['kmessage.message']
        if not message:
            return _("No matching message in the outbox.")
        message.write({'delivery_status': status})
        return _("Delivery status updated.")

    @api.model
    def _cron_clean(self, days=90, limit=5000):
        """Keep the log useful rather than infinite.

        Bounded on purpose. A tenant that has been running for a year has more
        old events than one transaction should delete in one go, and a tidy
        that times out every night tidies nothing at all. The cron comes round
        again tomorrow.
        """
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        old = self.sudo().search([('create_date', '<', cutoff)], limit=limit)
        count = len(old)
        old.unlink()
        return count
