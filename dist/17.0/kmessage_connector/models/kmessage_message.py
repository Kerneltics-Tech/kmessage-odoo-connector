# -*- coding: utf-8 -*-
"""The outbox: one row for every message Odoo means to send.

Nothing is sent from the middle of a business transaction. Posting an invoice
writes a row here and returns; a cron picks it up a moment later. That ordering
is the whole point — a slow or broken WhatsApp service must never be able to
roll back an accounting entry, and a retry must never post the invoice twice.
"""

import base64
import json
import logging
from collections import defaultdict

from odoo import _, api, fields, models, modules
from odoo.exceptions import UserError
from odoo.tools.sql import column_exists, create_index

from ..tools.client import KMessageError
from ..tools.phone import normalize

_logger = logging.getLogger(__name__)

#: How long to wait before each further attempt, in minutes.
BACKOFF_MINUTES = [1, 5, 15, 60, 180]

#: How long a message may have waited in an outbox that had stopped and still
#: be sent by itself once an upgrade starts the outbox again. Older ones are
#: held back for a person to look at (``_hold_back_a_stopped_outbox``).
STALE_AFTER_HOURS = 24

#: Answers that will never succeed no matter how often they are repeated.
PERMANENT_STATUSES = (400, 401, 403, 404, 422)

#: How far a message has got, in the platform's own order (statusPriority in
#: K-Message). Webhooks do not arrive in order, so news only ever moves a row
#: forward: a late "delivered" never undoes "read", and "sent" never undoes
#: either. "failed" is not on this ladder: it overrides any step of it, and
#: nothing overrides it.
DELIVERY_ORDER = {'pending': 0, 'sent': 1, 'delivered': 2, 'read': 3}


class KMessageMessage(models.Model):
    _name = 'kmessage.message'
    _description = 'K-Message Message'
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    account_id = fields.Many2one('kmessage.account', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    partner_id = fields.Many2one('res.partner', index=True)
    phone = fields.Char(required=True, help="The number as it will be sent: international digits.")
    template_id = fields.Many2one('kmessage.template', ondelete='restrict')
    template_name = fields.Char(readonly=True, help="Kept even if the template is later removed.")
    language = fields.Char()

    params_json = fields.Text(default='{}')
    buttons_json = fields.Text(default='{}')
    body_preview = fields.Text(readonly=True, help="What the customer will read.")

    attachment_id = fields.Many2one('ir.attachment', ondelete='set null')
    attachment_name = fields.Char()

    # Where this message came from, so the chatter can be written back and a
    # duplicate can be recognised.
    res_model = fields.Char(index=True)
    res_id = fields.Integer(index=True)
    reference = fields.Char(
        index=True, copy=False,
        help="Sent along with the message and echoed back when the customer taps a button.")

    state = fields.Selection(
        [('draft', 'Draft'), ('queued', 'Queued'), ('sent', 'Sent'),
         ('failed', 'Failed'), ('cancelled', 'Cancelled'), ('skipped', 'Practice run')],
        default='draft', index=True, copy=False)
    attempts = fields.Integer(default=0, readonly=True, copy=False)
    next_attempt = fields.Datetime(index=True, copy=False)
    error = fields.Char(readonly=True, copy=False)
    permanent_error = fields.Boolean(readonly=True, copy=False)

    remote_message_id = fields.Char(readonly=True, index=True, copy=False)
    sent_at = fields.Datetime(readonly=True, copy=False)
    # "failed" here is WhatsApp not delivering a message K-Message accepted,
    # which is not the same thing as the send failing (state). The key stays;
    # the label says the difference.
    delivery_status = fields.Selection(
        [('pending', 'Pending'), ('sent', 'Sent'), ('delivered', 'Delivered'),
         ('read', 'Read'), ('failed', 'Not delivered')],
        default='pending', readonly=True, copy=False)
    delivery_error = fields.Char(
        string='Why not delivered', readonly=True, copy=False,
        help="WhatsApp's own reason for not delivering this message, as K-Message passed it on.")
    delivery_date = fields.Datetime(
        string='Delivery updated', readonly=True, copy=False,
        help="When K-Message last told this Odoo how far this message got.")

    # Send again: a message that went out is repeated by a new row, so each row
    # keeps the delivery news of its own send.
    resent_from_id = fields.Many2one(
        'kmessage.message', string='Sent again from', readonly=True, copy=False,
        index='btree_not_null', ondelete='set null',
        help="The message this one repeats, when somebody pressed Send again.")
    resend_ids = fields.One2many(
        'kmessage.message', 'resent_from_id', string='Sent again as', readonly=True)

    display_name = fields.Char(compute='_compute_display_name')

    @api.depends('template_name', 'partner_id', 'phone')
    def _compute_display_name(self):
        for message in self:
            who = message.partner_id.display_name or message.phone or ''
            message.display_name = '%s → %s' % (message.template_name or _("Message"), who)

    def init(self):
        """What an install or upgrade does before the module's data is loaded.

        Three things, each a no-op when there is nothing to do: the renamed
        delivery label below, an index for the messages that were not
        delivered, and, on Odoo 17, holding back what a stopped outbox kept
        (``_hold_back_a_stopped_outbox``).

        The delivery value ``failed`` was labelled "Failed" until delivery news
        existed; it is "Not delivered" now, to tell it apart from a send that
        failed. On an update Odoo keeps whatever translation a renamed label
        already had, so every upgraded Arabic database would go on saying
        فشلت. Removing exactly that stale word, before the labels are
        reflected and the translations loaded, lets the shipped one load in its
        place. Any other wording — one somebody chose included — is left
        alone, and a second run finds nothing to do.
        """
        stale = 'فشلت'
        self.env.cr.execute("""
            UPDATE ir_model_fields_selection s
               SET name = s.name - ARRAY(
                       SELECT key FROM jsonb_each_text(s.name)
                        WHERE key LIKE 'ar%%' AND value = %s)
              FROM ir_model_fields f
             WHERE f.id = s.field_id
               AND f.model = 'kmessage.message'
               AND f.name = 'delivery_status'
               AND s.value = 'failed'
               AND EXISTS (SELECT 1 FROM jsonb_each_text(s.name)
                            WHERE key LIKE 'ar%%' AND value = %s)
        """, (stale, stale))
        self._index_not_delivered()
        self._hold_back_a_stopped_outbox()

    def _index_not_delivered(self):
        """An index for the one delivery value anybody looks for.

        The connection form counts the messages WhatsApp did not deliver each
        time it opens, and the outbox filters on them. They are a sliver of an
        outbox that grows by a row for every invoice sent, so the index holds
        only them: small, and exactly what both questions need.
        """
        create_index(self.env.cr, 'kmessage_message_not_delivered_idx', self._table,
                     ['account_id'], where="delivery_status = 'failed'")

    def _hold_back_a_stopped_outbox(self):
        """On Odoo 17, keep the upgrade from sending what a stopped outbox kept.

        The Odoo 17 build of 1.1.1 and earlier did not say that its crons
        recur, and Odoo 17 runs a cron ``numbercall`` times — 1, unless it is
        told otherwise — and then switches it off. So the outbox ran once and
        has kept every message queued since. This version's data switches it
        back on, and its next run would send all of them: an invoice from weeks
        ago announced as if it were new, and a WhatsApp message cannot be
        taken back.

        So, before the data is loaded, every message that has waited more than
        ``STALE_AFTER_HOURS`` is marked failed with a reason that says why,
        and *Try again* sends it if it should still go out. Newer ones are sent
        as usual, and no note goes on any document. Only an outbox that really
        stopped is touched: Odoo 18 has no ``numbercall``, a 17 whose outbox
        runs does not have 0 there, and the data load writes -1, so the next
        upgrade finds nothing to do.
        """
        cr = self.env.cr
        if not column_exists(cr, 'ir_cron', 'numbercall'):
            return
        cr.execute("""
            SELECT c.numbercall
              FROM ir_cron c
              JOIN ir_model_data d ON d.res_id = c.id AND d.model = 'ir.cron'
             WHERE d.module = 'kmessage_connector' AND d.name = 'cron_send_queue'
        """)
        row = cr.fetchone()
        if not row or row[0] != 0:
            return
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), hours=STALE_AFTER_HOURS)
        # The rows the outbox would pick up: _cron_send_queue's own domain,
        # less the time of the next attempt, which a stopped outbox let pass.
        cr.execute("""
            SELECT id, create_date FROM kmessage_message
             WHERE state IN ('queued', 'failed')
               AND permanent_error IS NOT TRUE
               AND create_date < %s
        """, (cutoff,))
        stale = cr.fetchall()
        if not stale:
            return
        by_day = defaultdict(list)
        for message_id, created in stale:
            by_day[created.date()].append(message_id)
        # Written with nobody signed in, so in the language the administrator
        # reads Odoo in.
        admin = self.env.ref('base.user_admin', raise_if_not_found=False)
        writer = self.with_context(lang=(admin and admin.sudo().lang) or 'en_US')
        for day, ids in by_day.items():
            cr.execute("""
                UPDATE kmessage_message
                   SET state = 'failed', permanent_error = TRUE, next_attempt = NULL,
                       error = %s, write_date = (now() at time zone 'UTC')
                 WHERE id IN %s
            """, (writer._held_back_reason(day), tuple(ids)))
        _logger.warning(
            'K-Message: the outbox had stopped (Odoo 17 cron with numbercall 0). %s message(s) '
            'that had waited more than %s hours were held back instead of being sent now; '
            'they are marked failed, and Try again sends one.', len(stale), STALE_AFTER_HOURS)

    def _held_back_reason(self, day):
        return _("Held back when the connector was upgraded: it had waited since %s, "
                 "while the outbox was stopped on Odoo 17. Press Try again (or Queue again "
                 "on the message) if it should still go out.",
                 fields.Date.to_string(day))

    @api.model_create_multi
    def create(self, vals_list):
        """Every message leaves with a reference, even one about no record.

        K-Message can be asked to send delivery news only about messages that
        carry a reference, which is how this Odoo hears about its own messages
        and not about everything the company sends. A message without one
        would never be heard of again. The fallback names the row itself.
        """
        messages = super().create(vals_list)
        for message in messages:
            if not message.reference:
                message.reference = '%s:%s' % (self._name, message.id)
        return messages

    # -- queueing ---------------------------------------------------------
    @api.model
    def enqueue(self, account, phone, template, params=None, buttons=None,
                partner=None, record=None, attachment=None, language=None, reference=None):
        """Put one message in the outbox and return it.

        Refusals are silent by design: a partner who opted out, a connection
        switched off, or a number Odoo cannot make sense of are ordinary
        outcomes of posting an invoice, not errors to throw at the accountant.
        """
        if not account or not account.outbound_enabled:
            return self.browse()

        number = normalize(phone, account.company_id.country_id.phone_code)
        if not number:
            _logger.info('K-Message: no usable phone number, nothing queued')
            return self.browse()
        if partner and partner.kmessage_opt_out:
            _logger.info('K-Message: %s has opted out, nothing queued', partner.display_name)
            return self.browse()

        values = {
            'account_id': account.id,
            'partner_id': partner.id if partner else False,
            'phone': number,
            'template_id': template.id if template else False,
            'template_name': template.name if template else False,
            'language': language or (template.language if template else False),
            'params_json': json.dumps(params or {}, ensure_ascii=False),
            'buttons_json': json.dumps(buttons or {}, ensure_ascii=False),
            'body_preview': template.preview(params) if template else '',
            'state': 'queued',
            'next_attempt': fields.Datetime.now(),
        }
        if record is not None:
            values.update(res_model=record._name, res_id=record.id)
            values['reference'] = reference or '%s:%s' % (record._name, record.id)
        elif reference:
            values['reference'] = reference
        if attachment:
            values.update(attachment_id=attachment.id, attachment_name=attachment.name)

        message = self.create(values)
        if account.dry_run:
            message.write({'state': 'skipped', 'error': _("Practice mode: not sent.")})
        return message

    # -- sending ----------------------------------------------------------
    def send_now(self):
        """Try to send these messages once, here and now.

        Row by row, in the same isolation the cron uses. Sending several in one
        transaction and letting the first unexpected error out would roll back
        the rows already marked sent — and a row that is queued again is a
        customer who reads the same message twice. A transaction can be rolled
        back; a WhatsApp message cannot.
        """
        for message in self:
            self._send_in_isolation(message)
        return True

    def _send_one(self, receipt=None):
        """Send this one message. ``receipt`` is filled in the moment it goes out.

        The caller hands in a dict when it needs to know, after an exception,
        whether K-Message already took the message: a transaction can be rolled
        back, a WhatsApp message cannot.
        """
        self.ensure_one()
        if self.state not in ('queued', 'failed', 'draft'):
            return False
        if not self._claim():
            _logger.info('K-Message: message %s is already being sent elsewhere', self.id)
            return False
        if self.state not in ('queued', 'failed', 'draft'):
            # Somebody else had it between the search and the lock.
            return False
        account = self.account_id.sudo()
        if not account.outbound_enabled:
            self.write({'state': 'cancelled', 'error': _("Sending is switched off.")})
            return False

        document = None
        if self.attachment_id:
            document = (
                self.attachment_name or self.attachment_id.name,
                base64.b64decode(self.attachment_id.datas or b''),
                self.attachment_id.mimetype or 'application/pdf',
            )

        try:
            remote_id = account._client().send_template(
                phone_number=self.phone,
                template_name=self.template_name,
                language=self.language or None,
                account_name=account.account_name or None,
                template_params=json.loads(self.params_json or '{}'),
                button_params=json.loads(self.buttons_json or '{}'),
                reference=self.reference or None,
                document=document,
            )
        except KMessageError as error:
            return self._mark_failed(error)

        values = {
            'state': 'sent',
            'remote_message_id': remote_id or False,
            'sent_at': fields.Datetime.now(),
            'delivery_status': 'sent',
            'error': False,
            'next_attempt': False,
            'attempts': self.attempts + 1,
        }
        if receipt is not None:
            receipt.update(values)
        self.write(values)
        self._log_on_source(sent=True)
        return True

    def _claim(self):
        """Take this row for this worker alone, or report that it is taken.

        Two things drain the same outbox — the cron and whoever presses Send —
        and the row is the only place they can agree on who has it. The lock is
        held until the commit that follows the send, so the loser leaves the
        row alone instead of sending the same WhatsApp message a second time.
        ``SKIP LOCKED`` rather than a plain ``FOR UPDATE`` because waiting for
        a send that is already under way would achieve nothing.

        The recordset is invalidated afterwards on purpose: whatever the other
        worker committed while we were waiting is what the state now has to be
        read from, not what this transaction happened to have in cache.
        """
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute(
            'SELECT id FROM kmessage_message WHERE id = %s FOR UPDATE SKIP LOCKED',
            (self.id,))
        if not self.env.cr.fetchone():
            return False
        self.invalidate_recordset()
        return True

    def _mark_failed(self, error):
        self.ensure_one()
        attempts = self.attempts + 1
        permanent = error.status in PERMANENT_STATUSES
        exhausted = attempts > len(BACKOFF_MINUTES)
        values = {
            'attempts': attempts,
            'error': str(error)[:500],
            # This flag is what the queue reads as "stop", so a row that has
            # used up its attempts carries it too: leaving it off and clearing
            # the next attempt would mean "no time planned", and a row with no
            # time planned is exactly what a brand new message looks like — the
            # cron would pick it up again two minutes later, for ever.
            'permanent_error': permanent or exhausted,
            'state': 'failed',
        }
        if permanent or exhausted:
            values['next_attempt'] = False
        else:
            minutes = BACKOFF_MINUTES[attempts - 1]
            values['next_attempt'] = fields.Datetime.add(fields.Datetime.now(), minutes=minutes)
        self.write(values)
        _logger.warning(
            'K-Message: send failed for %s (%s attempt %s): %s',
            self.phone, self.template_name, attempts, error)
        self._log_on_source(sent=False)
        return False

    def _log_on_source(self, sent):
        """Say on the invoice — or whatever it was — what happened.

        The note is worth less than the fact it describes. Whatever goes wrong
        writing it — a model that was uninstalled, a chatter that refuses the
        post — is swallowed inside a savepoint, because the alternative is an
        exception that undoes the row saying the message went out, and a row
        put back in the queue is a customer messaged twice.
        """
        self.ensure_one()
        if not (self.res_model and self.res_id):
            return
        try:
            with self.env.cr.savepoint():
                record = self.env[self.res_model].browse(self.res_id).exists()
                if not record or not hasattr(record, 'message_post'):
                    return
                who = self.partner_id.display_name or self.phone
                if sent:
                    body = _("Sent on WhatsApp to %(who)s: %(template)s",
                             who=who, template=self.template_name)
                else:
                    body = _(
                        "Could not send on WhatsApp to %(who)s: %(error)s",
                        who=who, error=self.error or _("unknown error"))
                record.sudo().message_post(body=body)
        except Exception:  # noqa: BLE001 - a missing note is not a failed send
            _logger.exception(
                'K-Message: could not note message %s on %s %s',
                self.id, self.res_model, self.res_id)

    # -- news from K-Message about a message already sent ------------------
    def _apply_delivery_news(self, status, reason=None, when=None):
        """Move this row forward with what K-Message says; return what was done.

        Only ever the delivery columns. ``state`` stays where it is — a message
        WhatsApp would not deliver was still sent — and nothing is queued
        again: sending it a second time could reach the customer twice, or
        fail again for as long as the reason lasts. Sending again is a
        person's decision.
        """
        self.ensure_one()
        current = self.delivery_status or 'pending'
        when = when or fields.Datetime.now()
        reason = (reason or '').strip()[:500]

        if current == 'failed':
            # Nothing overrides "not delivered". A second refusal may still
            # bring the reason the first one lacked; it never brings a second
            # note on the document.
            if status == 'failed' and reason and not self.delivery_error:
                self.write({'delivery_error': reason, 'delivery_date': when})
            return _("Already marked not delivered.")

        if status == 'failed':
            self.write({
                'delivery_status': 'failed',
                'delivery_error': reason or False,
                'delivery_date': when,
            })
            self._log_not_delivered()
            return _("Not delivered: %s", reason or _("WhatsApp gave no reason."))

        rank, known = DELIVERY_ORDER.get(status, -1), DELIVERY_ORDER.get(current, 0)
        if rank == known:
            # The everyday case for "sent": the send itself already said so.
            return _("Already known; nothing changed.")
        if rank < known:
            return _("Older news than what is already known; nothing changed.")
        self.write({'delivery_status': status, 'delivery_date': when})
        return _("Delivery status updated.")

    def _log_not_delivered(self):
        """Say on the invoice — or whatever it was — that WhatsApp did not deliver it.

        Written once, when the row first becomes "not delivered", and with the
        same care as ``_log_on_source``: inside a savepoint, so a chatter that
        refuses the note can never undo the row that records the news.
        """
        self.ensure_one()
        if not (self.res_model and self.res_id):
            return
        try:
            with self.env.cr.savepoint():
                record = self.env[self.res_model].browse(self.res_id).exists()
                if not record or not hasattr(record, 'message_post'):
                    return
                # The news comes from K-Message, through a webhook that runs as
                # Odoo's public user; the note is signed by OdooBot instead.
                record.sudo().message_post(
                    body=_("Not delivered on WhatsApp to %(who)s: %(reason)s",
                           who=self.partner_id.display_name or self.phone,
                           reason=self.delivery_error or _("WhatsApp gave no reason.")),
                    author_id=self.env.ref('base.partner_root').id)
        except Exception:  # noqa: BLE001 - a missing note is not lost news
            _logger.exception(
                'K-Message: could not note that message %s was not delivered on %s %s',
                self.id, self.res_model, self.res_id)

    # -- the cron ---------------------------------------------------------
    @api.model
    def _cron_send_queue(self, limit=50):
        """Drain the outbox. One row at a time, committing as it goes."""
        now = fields.Datetime.now()
        due = self.search([
            ('state', 'in', ('queued', 'failed')),
            ('permanent_error', '=', False),
            '|', ('next_attempt', '=', False), ('next_attempt', '<=', now),
        ], order='next_attempt asc, id asc', limit=limit)

        for message in due:
            self._send_in_isolation(message)
            # Each message stands alone: what has been sent stays recorded as
            # sent even if the next one raises.
            if not self._inside_a_test():
                self.env.cr.commit()
        return len(due)

    @api.model
    def _send_in_isolation(self, message):
        """Send one row without letting its failure reach the next one.

        The savepoint is what keeps a broken row — a bad parameter, a database
        error halfway through — from leaving the cursor unusable for the rest
        of the queue. What it cannot do is undo a message the customer has
        already read, so the receipt is written back afterwards: a row that
        went out stays sent, whatever fell over after it.

        The row is claimed out here rather than inside the savepoint, because
        rolling a savepoint back releases the locks taken inside it. This lock
        has to outlive that: the instant a rollback undoes the write that said
        "sent", an unlocked row is a row another worker will pick up and send
        again.
        """
        if not message._claim():
            _logger.info('K-Message: message %s is already being sent elsewhere', message.id)
            return
        receipt = {}
        try:
            with self.env.cr.savepoint():
                message._send_one(receipt=receipt)
            return
        except Exception:  # noqa: BLE001 - one bad row must not stop the queue
            _logger.exception('K-Message: unexpected error sending message %s', message.id)

        try:
            if receipt:
                message.write(receipt)
            else:
                message._mark_failed(KMessageError(_("Unexpected error; see the server log.")))
        except Exception:  # noqa: BLE001 - the log is the last place left to say it
            _logger.exception(
                'K-Message: could not record what happened to message %s', message.id)

    @api.model
    def _inside_a_test(self):
        """True while a test is running, however it opened its cursor.

        Committing is the point of the loop above and the one thing a test
        cannot survive: its whole run is one transaction that is rolled back at
        the end. ``registry.in_test_mode()`` alone does not answer this — it is
        about the shared cursor an HttpCase installs, and an ordinary
        TransactionCase never installs one.
        """
        return bool(modules.module.current_test) or self.env.registry.in_test_mode()

    # -- actions ----------------------------------------------------------
    def action_retry(self):
        self.write({
            'state': 'queued',
            'permanent_error': False,
            'error': False,
            'next_attempt': fields.Datetime.now(),
        })
        return True

    def action_resend(self):
        """Send these messages to the customer again: one message, or a selection.

        A message that never went out — its send failed, or somebody cancelled
        it — goes back in the queue as it is. One that did go out is never sent
        from its own row again: a new row is queued with the same template,
        values and document, so the old row keeps saying what became of the
        first send and the new one gets delivery news of its own.

        Nothing is doubled and nothing is forced. A row already waiting, a row
        whose copy is already waiting, a practice run, a customer who opted
        out, a connection that is switched off, and a message whose document
        has since been deleted are left as they are, and counted in the answer.
        """
        waiting_copy = self.search([
            ('resent_from_id', 'in', self.ids),
            ('state', 'in', ('draft', 'queued')),
        ]).resent_from_id
        requeue = self.browse()
        copies = []
        for message in self:
            if (message.state in ('draft', 'queued', 'skipped')
                    or message in waiting_copy
                    or not message.account_id.outbound_enabled
                    or message.partner_id.kmessage_opt_out
                    or (message.attachment_name and not message.attachment_id)):
                continue
            if message.state == 'sent':
                copies.append(message._resend_values())
            else:
                requeue |= message
        if requeue:
            requeue.action_retry()
        created = self.create(copies)
        practice = created.filtered(lambda m: m.account_id.dry_run)
        if practice:
            practice.write({'state': 'skipped', 'next_attempt': False,
                            'error': _("Practice mode: not sent.")})

        queued = len(requeue) + len(created - practice)
        left = len(self) - len(requeue) - len(created)
        lines = []
        if queued:
            lines.append(_("%s message(s) will go out again within two minutes.", queued))
        if practice:
            lines.append(_("%s message(s) only prepared: the connection is in practice mode.",
                           len(practice)))
        if left:
            lines.append(_("%s message(s) left as they are: already waiting to go out, or the "
                           "customer opted out, sending is switched off, or the document was "
                           "deleted.", left))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Send again"),
                'message': ' '.join(lines),
                'type': 'success' if queued else 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def _resend_values(self):
        """A new outbox row repeating this one, ready for the queue."""
        self.ensure_one()
        own_reference = '%s:%s' % (self._name, self.id)
        return {
            'account_id': self.account_id.id,
            'partner_id': self.partner_id.id,
            'phone': self.phone,
            'template_id': self.template_id.id,
            'template_name': self.template_name,
            'language': self.language,
            'params_json': self.params_json,
            'buttons_json': self.buttons_json,
            'body_preview': self.body_preview,
            'attachment_id': self.attachment_id.id,
            'attachment_name': self.attachment_name,
            'res_model': self.res_model,
            'res_id': self.res_id,
            # A button tapped on the copy must find the same document. A
            # message about no record named itself, so the copy names itself
            # instead (create fills that in).
            'reference': False if self.reference == own_reference else self.reference,
            'resent_from_id': self.id,
            'state': 'queued',
            'next_attempt': fields.Datetime.now(),
        }

    def action_cancel(self):
        stuck = self.filtered(lambda m: m.state == 'sent')
        if stuck:
            raise UserError(_("A message that has already been sent cannot be cancelled."))
        self.write({'state': 'cancelled', 'next_attempt': False})
        return True
