# -*- coding: utf-8 -*-
"""The outbox, which is where the promise "nothing rolls back an invoice" lives.

Two halves. Queueing has to be unfailing: a customer who opted out, a number
nobody can make sense of, a connection switched off — all of them are ordinary
outcomes of posting an invoice and none of them may raise. Sending has to be
patient about what might work later and final about what never will.
"""

import base64
import json

from odoo import fields
from odoo.exceptions import UserError

from odoo.addons.kmessage_connector.models.kmessage_message import (
    BACKOFF_MINUTES, PERMANENT_STATUSES,
)

from .common import CUSTOMER_MOBILE, KMessageCase

PDF = b'%PDF-1.4 a small but honest document'


class TestQueueing(KMessageCase):

    def enqueue(self, **values):
        defaults = {
            'account': self.account,
            'phone': CUSTOMER_MOBILE,
            'template': self.template,
            'partner': self.partner,
        }
        return self.env['kmessage.message'].enqueue(**dict(defaults, **values))

    def test_a_queued_message_carries_what_it_needs_to_be_sent(self):
        message = self.enqueue(params={'1': 'S00021'})
        self.assertEqual(message.state, 'queued')
        self.assertEqual(message.phone, '966507386853')
        self.assertEqual(message.template_name, 'order_confirmed')
        self.assertEqual(json.loads(message.params_json), {'1': 'S00021'})
        self.assertIn('S00021', message.body_preview)
        self.assertTrue(message.next_attempt)

    def test_a_customer_who_opted_out_is_not_queued_and_nothing_is_raised(self):
        self.partner.kmessage_opt_out = True
        self.assertFalse(self.enqueue())
        self.assertFalse(self.env['kmessage.message'].search([('partner_id', '=', self.partner.id)]))

    def test_a_number_that_cannot_be_used_is_not_queued(self):
        for unusable in ('', None, 'call the office'):
            self.assertFalse(self.enqueue(phone=unusable), repr(unusable))

    def test_nothing_is_queued_while_sending_is_switched_off(self):
        self.account.outbound_enabled = False
        self.assertFalse(self.enqueue())

    def test_nothing_is_queued_without_a_connection(self):
        self.assertFalse(self.enqueue(account=self.env['kmessage.account']))

    def test_practice_mode_prepares_the_message_but_does_not_send_it(self):
        self.account.dry_run = True
        message = self.enqueue()
        self.assertEqual(message.state, 'skipped')
        message.send_now()
        self.assertFalse(self.fake.sent)

    def test_a_message_about_a_record_remembers_where_it_came_from(self):
        message = self.enqueue(record=self.partner)
        self.assertEqual(message.res_model, 'res.partner')
        self.assertEqual(message.res_id, self.partner.id)
        self.assertEqual(message.reference, 'res.partner:%s' % self.partner.id)


class TestSending(KMessageCase):

    def setUp(self):
        super().setUp()
        self.message = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner, record=self.partner, params={'1': 'S00021'})

    def seconds_until_next_attempt(self, message):
        return (message.next_attempt - fields.Datetime.now()).total_seconds()

    def test_a_message_that_goes_out_is_recorded_as_sent(self):
        self.message.send_now()
        self.assertEqual(self.message.state, 'sent')
        self.assertTrue(self.message.remote_message_id)
        self.assertTrue(self.message.sent_at)
        self.assertEqual(self.message.delivery_status, 'sent')
        self.assertFalse(self.message.next_attempt)
        self.assertEqual(len(self.fake.sent), 1)

    def test_what_happened_is_written_on_the_record_it_was_about(self):
        self.message.send_now()
        bodies = ' '.join(self.partner.message_ids.mapped('body'))
        self.assertIn('order_confirmed', bodies)

    def test_a_document_travels_with_the_message(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'INV_2026_0001.pdf',
            'datas': base64.b64encode(PDF),
            'mimetype': 'application/pdf',
        })
        message = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.doc_template,
            partner=self.partner, attachment=attachment)
        message.send_now()
        self.assertEqual(message.state, 'sent')
        header = self.fake.last_sent()['header_file']
        self.assertEqual(header['filename'], 'INV_2026_0001.pdf')
        self.assertTrue(header['looks_like_pdf'])

    def test_a_service_that_is_merely_unwell_is_tried_again(self):
        self.fake.fail_next(count=1, status=503)
        self.message.send_now()
        self.assertEqual(self.message.state, 'failed')
        self.assertFalse(self.message.permanent_error)
        self.assertEqual(self.message.attempts, 1)
        self.assertAlmostEqual(
            self.seconds_until_next_attempt(self.message), BACKOFF_MINUTES[0] * 60, delta=60)

    def test_each_further_attempt_waits_longer(self):
        self.fake.fail_next(count=2, status=503)
        self.message.send_now()
        self.message.send_now()
        self.assertEqual(self.message.attempts, 2)
        self.assertAlmostEqual(
            self.seconds_until_next_attempt(self.message), BACKOFF_MINUTES[1] * 60, delta=60)

    def test_a_message_that_finally_goes_through_forgets_the_failure(self):
        self.fake.fail_next(count=1, status=503)
        self.message.send_now()
        self.message.send_now()
        self.assertEqual(self.message.state, 'sent')
        self.assertFalse(self.message.error)
        self.assertFalse(self.message.next_attempt)

    def test_an_answer_that_will_never_change_is_not_retried(self):
        """404 means the template does not exist. Asking again cannot fix that."""
        self.assertIn(404, PERMANENT_STATUSES)
        unknown = self.env['kmessage.template'].create({
            'account_id': self.account.id,
            'name': 'a_template_nobody_approved',
            'language': 'ar',
            'status': 'APPROVED',
            'body_content': 'anything',
        })
        message = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=unknown, partner=self.partner)
        message.send_now()
        self.assertEqual(message.state, 'failed')
        self.assertTrue(message.permanent_error)
        self.assertFalse(message.next_attempt)
        self.assertEqual(message.attempts, 1)

    def test_a_failure_is_written_on_the_record_too(self):
        self.fake.fail_next(count=1, status=503)
        self.message.send_now()
        bodies = ' '.join(self.partner.message_ids.mapped('body'))
        self.assertIn('Could not send', bodies)

    def test_sending_is_refused_once_the_switch_is_off(self):
        self.account.outbound_enabled = False
        self.message.send_now()
        self.assertEqual(self.message.state, 'cancelled')
        self.assertFalse(self.fake.sent)

    def test_a_sent_message_cannot_be_cancelled(self):
        self.message.send_now()
        with self.assertRaises(UserError):
            self.message.action_cancel()

    def test_a_row_that_cannot_be_read_does_not_undo_the_row_already_sent(self):
        """Send is per row, because a transaction rolls back and WhatsApp does not.

        Selecting several messages and pressing Send used to run them in one
        transaction: the first unexpected error took the rows already sent down
        with it, they went back in the queue, and the cron sent them again.
        """
        good = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner)
        broken = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner)
        broken.params_json = 'this is not json'

        self.env['kmessage.message'].browse([good.id, broken.id]).send_now()

        self.assertEqual(good.state, 'sent')
        self.assertEqual(broken.state, 'failed')
        self.assertEqual(len(self.fake.sent), 1)

    def test_a_row_another_worker_is_holding_is_left_where_it_is(self):
        """The claim is what the two drainers agree on, and it is checked first."""
        self.patch(type(self.message), '_claim', lambda message: False)
        self.message.send_now()
        self.assertEqual(self.message.state, 'queued')
        self.assertFalse(self.fake.sent)

    def test_retrying_by_hand_clears_a_permanent_failure(self):
        self.fake.fail_next(count=1, status=403)
        self.message.send_now()
        self.assertTrue(self.message.permanent_error)
        self.message.action_retry()
        self.assertEqual(self.message.state, 'queued')
        self.assertFalse(self.message.permanent_error)


class TestTheCron(KMessageCase):

    def setUp(self):
        super().setUp()
        # The cron drains the whole outbox, so anything this database was
        # already carrying has to be out of the way first.
        self.env['kmessage.message'].search([('state', 'in', ('queued', 'failed'))]).write(
            {'state': 'cancelled', 'next_attempt': False})

    def queue(self, **values):
        message = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner)
        if values:
            message.write(values)
        return message

    def test_only_rows_that_are_due_are_drained(self):
        due = self.queue()
        later = self.queue(next_attempt=fields.Datetime.add(fields.Datetime.now(), hours=1))
        self.assertEqual(self.env['kmessage.message']._cron_send_queue(), 1)
        self.assertEqual(due.state, 'sent')
        self.assertEqual(later.state, 'queued')
        self.assertEqual(len(self.fake.sent), 1)

    def test_a_row_with_no_time_on_it_is_due(self):
        waiting = self.queue(next_attempt=False)
        self.env['kmessage.message']._cron_send_queue()
        self.assertEqual(waiting.state, 'sent')

    def test_a_permanent_failure_is_never_picked_up_again(self):
        stuck = self.queue(state='failed', permanent_error=True, next_attempt=False)
        self.assertEqual(self.env['kmessage.message']._cron_send_queue(), 0)
        self.assertEqual(stuck.state, 'failed')
        self.assertFalse(self.fake.sent)

    def test_one_bad_row_does_not_stop_the_queue(self):
        """The queue is drained row by row so that one broken message is one loss."""
        broken = self.queue(params_json='this is not json')
        good = self.queue()
        self.env['kmessage.message']._cron_send_queue()
        self.assertEqual(broken.state, 'failed')
        self.assertEqual(good.state, 'sent')
