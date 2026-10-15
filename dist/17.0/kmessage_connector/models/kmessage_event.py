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

#: News about a message this Odoo sent, and what each says about it.
DELIVERY_NEWS = {
    'message.delivered': 'delivered',
    'message.read': 'read',
    'message.failed': 'failed',
}

#: How long a "not delivered" that names no message in the outbox is kept
#: waiting for one. A refusal at send time can reach this Odoo a moment before
#: the send that caused it has been committed; a day is far more than that
#: moment, and short enough that a log of strangers' news cannot pile up.
NEWS_WAIT_HOURS = 24


class _NotYet(Exception):
    """The news is early: the message it names is not in the outbox yet.

    Raised by a handler and caught by ``_route``, which leaves the event
    unhandled for ``_cron_retry_delivery_news`` to try again.
    """


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
        """Store one webhook envelope and route it. Never raises.

        Returns the stored event — or an empty recordset for delivery news
        about a message this Odoo never sent, which is acknowledged and not
        kept: the log would otherwise grow by every message the company sends.
        """
        data = (envelope or {}).get('data') or {}
        in_reply_to = data.get('in_reply_to') or {}
        phone = data.get('contact_phone') or data.get('phone_number') or ''

        if self._is_someone_elses_news(account, (envelope or {}).get('event'), data):
            return self.browse()

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

    @api.model
    def _is_someone_elses_news(self, account, name, data):
        """True for delivery news this Odoo has no message for, and will not have.

        Delivered and read are about a message or they are noise: a webhook
        that hears every message of the company would otherwise write a row
        here for each of them. "Not delivered" gets one more chance, because a
        refusal at send time can arrive before the send has been committed:
        it is kept when its reference names a message of this connection that
        is still waiting for K-Message's id. Matching stays within the
        connection whose secret signed the webhook. Never raises — when in
        doubt, the news is kept.
        """
        if name not in DELIVERY_NEWS:
            return False
        try:
            message_id = data.get('message_id')
            if not account or not message_id:
                return True
            outbox = self.env['kmessage.message'].sudo()
            if outbox.search_count([
                ('remote_message_id', '=', message_id),
                ('account_id', '=', account.id),
            ], limit=1):
                return False
            reference = data.get('reference')
            if name == 'message.failed' and reference:
                return not outbox.search_count([
                    ('reference', '=', reference),
                    ('account_id', '=', account.id),
                    ('remote_message_id', '=', False),
                ], limit=1)
            return True
        except Exception:  # noqa: BLE001 - keeping a row too many beats losing news
            _logger.exception('K-Message: could not tell whose news %s is', name)
            return False

    def _route(self):
        """Do whatever this event means. Unknown events are recorded, not refused."""
        self.ensure_one()
        handler = getattr(self, '_handle_%s' % self.event.replace('.', '_'), None)
        if not handler:
            self.write({'handled': True, 'outcome': _("No action needed for this event.")})
            return
        try:
            outcome = handler() or _("Handled.")
        except _NotYet as early:
            # Left unhandled, which is what the retry cron looks for.
            self.write({'handled': False, 'outcome': str(early)})
            return
        self.write({'handled': True, 'outcome': outcome})

    def _data(self):
        """The ``data`` part of the stored envelope, or an empty dict."""
        try:
            return json.loads(self.payload or '{}').get('data') or {}
        except (ValueError, AttributeError):
            return {}

    # -- handlers ---------------------------------------------------------
    def _handle_message_sent(self):
        """Delivery news about something we sent."""
        return self._update_delivery('sent')

    def _handle_message_delivered(self):
        """WhatsApp handed it to the customer's phone."""
        return self._update_delivery('delivered')

    def _handle_message_read(self):
        """The customer opened it."""
        return self._update_delivery('read')

    def _handle_message_failed(self):
        """WhatsApp would not deliver it, and says why.

        The reason is what this whole event is for — "add a payment method",
        "outside the 24-hour window" — so it goes on the row, and once on the
        document the message was about.
        """
        if self.remote_message_id and self.account_id and not self._outbox_message():
            hours = self.env.context.get('kmessage_news_wait_hours', NEWS_WAIT_HOURS)
            waited_since = fields.Datetime.subtract(fields.Datetime.now(), hours=hours)
            if (self.create_date or fields.Datetime.now()) > waited_since:
                raise _NotYet(_("Waiting for the message to be recorded in the outbox."))
        return self._update_delivery('failed', reason=self._data().get('error_message'))

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

    def _outbox_message(self):
        """The outbox row this news is about, within the connection that signed it.

        Matched on K-Message's own id for the message and scoped to the
        connection, so a tenant holding one company's webhook secret cannot
        settle another company's message by naming its id.
        """
        outbox = self.env['kmessage.message'].sudo()
        if not (self.remote_message_id and self.account_id):
            return outbox
        return outbox.search([
            ('remote_message_id', '=', self.remote_message_id),
            ('account_id', '=', self.account_id.id),
        ], limit=1)

    def _update_delivery(self, status, reason=None):
        if not self.remote_message_id:
            return _("No message id to match.")
        message = self._outbox_message()
        if not message:
            return _("No matching message in the outbox.")
        return message._apply_delivery_news(status, reason=reason, when=self.create_date)

    @api.model
    def _cron_retry_delivery_news(self, hours=NEWS_WAIT_HOURS, limit=200):
        """Apply the "not delivered" news that arrived before its message.

        Each one is applied once its message turns up in the outbox, and closed
        with "no matching message" once ``hours`` have passed. A run reads only
        those two kinds — news it can now apply, and news it must give up on —
        so the ones still waiting are not read again every five minutes, and
        however many of them there are, they never keep a newer one from its
        turn. Bounded per run, and every event in a savepoint of its own, so
        one that cannot be applied never holds up the rest.
        """
        given_up_before = fields.Datetime.subtract(fields.Datetime.now(), hours=hours)
        self.env['kmessage.event'].flush_model(
            ['event', 'handled', 'remote_message_id', 'account_id'])
        self.env['kmessage.message'].flush_model(['remote_message_id', 'account_id'])
        self.env.cr.execute("""
            SELECT e.id
              FROM kmessage_event e
             WHERE e.event = 'message.failed'
               AND e.handled IS NOT TRUE
               AND (e.create_date < %s
                    OR EXISTS (SELECT 1
                                 FROM kmessage_message m
                                WHERE m.remote_message_id = e.remote_message_id
                                  AND m.account_id = e.account_id))
          ORDER BY e.id
             LIMIT %s
        """, (given_up_before, limit))
        waiting = self.sudo().browse([row[0] for row in self.env.cr.fetchall()])
        for event in waiting.with_context(kmessage_news_wait_hours=hours):
            try:
                with self.env.cr.savepoint():
                    event._route()
            except Exception:  # noqa: BLE001 - one bad event must not stop the rest
                _logger.exception('K-Message: could not apply delivery news %s', event.id)
                if event.create_date and event.create_date < given_up_before:
                    # Closed, or it would come round first on every run for ever.
                    event.write({'handled': True,
                                 'outcome': _("Handler failed; see the server log.")})
        return len(waiting)

    @api.model
    def _cron_clean(self, days=90, limit=20000):
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
