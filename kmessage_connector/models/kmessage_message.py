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

from odoo import _, api, fields, models, modules
from odoo.exceptions import UserError

from ..tools.client import KMessageError
from ..tools.phone import normalize

_logger = logging.getLogger(__name__)

#: How long to wait before each further attempt, in minutes.
BACKOFF_MINUTES = [1, 5, 15, 60, 180]

#: Answers that will never succeed no matter how often they are repeated.
PERMANENT_STATUSES = (400, 401, 403, 404, 422)


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
    delivery_status = fields.Selection(
        [('pending', 'Pending'), ('sent', 'Sent'), ('delivered', 'Delivered'),
         ('read', 'Read'), ('failed', 'Failed')],
        default='pending', readonly=True, copy=False)

    display_name = fields.Char(compute='_compute_display_name')

    @api.depends('template_name', 'partner_id', 'phone')
    def _compute_display_name(self):
        for message in self:
            who = message.partner_id.display_name or message.phone or ''
            message.display_name = '%s → %s' % (message.template_name or _("Message"), who)

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

    def action_cancel(self):
        stuck = self.filtered(lambda m: m.state == 'sent')
        if stuck:
            raise UserError(_("A message that has already been sent cannot be cancelled."))
        self.write({'state': 'cancelled', 'next_attempt': False})
        return True
