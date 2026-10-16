# -*- coding: utf-8 -*-
"""What arrives from K-Message, and where it ends up.

The log is written before anything is routed, which is the behaviour these
tests pin down: an event we have no handler for, an event whose handler fails
and an event about somebody we do not know are all *recorded*. "Did it reach
us?" has to be answerable from the database even when the answer to "and what
did we do about it?" is "nothing".
"""

import json

from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .common import CUSTOMER_MOBILE, KMessageCase, connection_for


def envelope(event, **data):
    return {'event': event, 'timestamp': '2026-09-20T10:00:00Z', 'data': data}


class TestEvent(KMessageCase):

    def record(self, envelope_dict):
        return self.env['kmessage.event'].record_event(self.account, envelope_dict)

    # -- the log ----------------------------------------------------------
    def test_an_event_is_written_down_before_it_is_acted_on(self):
        event = self.record(envelope(
            'message.incoming', contact_phone=CUSTOMER_MOBILE, contact_name='Layla',
            message_id='wamid.1', content={'body': 'مرحبا'}))
        self.assertEqual(event.event, 'message.incoming')
        self.assertEqual(event.contact_phone, CUSTOMER_MOBILE)
        self.assertEqual(event.partner_id, self.partner)
        self.assertEqual(json.loads(event.payload)['event'], 'message.incoming')
        self.assertTrue(event.handled)

    def test_an_event_we_have_no_handler_for_is_recorded_not_refused(self):
        event = self.record(envelope('transfer.assigned', agent='Sara'))
        self.assertEqual(event.event, 'transfer.assigned')
        self.assertTrue(event.handled)
        self.assertTrue(event.outcome, "an unhandled event still has to say why")

    def test_an_envelope_with_no_event_name_is_still_recorded(self):
        event = self.record({'data': {}})
        self.assertEqual(event.event, 'unknown')
        self.assertTrue(event.handled)

    def test_a_handler_that_fails_leaves_the_event_behind_with_a_reason(self):
        with patch.object(
            type(self.env['kmessage.event']), '_handle_message_sent',
            side_effect=ValueError('something gave way'),
        ):
            event = self.record(envelope('message.sent', message_id='wamid.1'))
        self.assertTrue(event.exists())
        self.assertIn('Handler failed', event.outcome)

    # -- a customer writing to us -----------------------------------------
    def test_an_incoming_message_is_noted_on_the_customer(self):
        self.record(envelope(
            'message.incoming', contact_phone=CUSTOMER_MOBILE, content={'body': 'وين طلبي؟'}))
        bodies = ' '.join(self.partner.message_ids.mapped('body'))
        self.assertIn('وين طلبي؟', bodies)

    def test_a_number_we_do_not_recognise_is_recorded_and_left_there(self):
        event = self.record(envelope(
            'message.incoming', contact_phone='966500000123', content={'body': 'hello'}))
        self.assertFalse(event.partner_id)
        self.assertTrue(event.handled)

    # -- the customer tapping a button ------------------------------------
    def test_a_button_reply_lands_on_the_record_the_message_was_about(self):
        message = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner, record=self.partner)
        event = self.record(envelope(
            'button.reply',
            contact_phone=CUSTOMER_MOBILE,
            button={'id': 'yes', 'text': 'تم'},
            in_reply_to={'message_id': 'wamid.1', 'reference': message.reference}))
        self.assertEqual(event.reference, message.reference)
        self.assertTrue(event.handled)
        bodies = ' '.join(self.partner.message_ids.mapped('body'))
        self.assertIn('تم', bodies)

    def test_a_button_reply_with_no_reference_falls_back_to_the_customer(self):
        event = self.record(envelope(
            'button.reply', contact_phone=CUSTOMER_MOBILE, button={'text': 'نعم'}))
        self.assertTrue(event.handled)
        bodies = ' '.join(self.partner.message_ids.mapped('body'))
        self.assertIn('نعم', bodies)

    def test_a_button_reply_from_a_stranger_is_still_recorded(self):
        event = self.record(envelope('button.reply', button={'text': 'نعم'}))
        self.assertTrue(event.handled)
        self.assertIn('نعم', event.outcome)

    # -- news about something we sent -------------------------------------
    def test_delivery_news_updates_the_message_it_is_about(self):
        message = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner)
        message.send_now()
        message.delivery_status = 'pending'
        event = self.record(envelope('message.sent', message_id=message.remote_message_id))
        self.assertEqual(message.delivery_status, 'sent')
        self.assertIn('Delivery status updated', event.outcome)

    def test_delivery_news_about_a_message_we_never_sent_is_harmless(self):
        event = self.record(envelope('message.sent', message_id='wamid.nobody'))
        self.assertTrue(event.handled)
        self.assertIn('No matching message', event.outcome)

    # -- a new contact ----------------------------------------------------
    def test_a_new_contact_who_is_already_a_customer_is_said_to_be_one(self):
        event = self.record(envelope('contact.created', contact_phone=CUSTOMER_MOBILE))
        self.assertIn('Already a customer', event.outcome)

    def test_a_new_contact_who_is_nobody_here_is_said_to_be_nobody(self):
        event = self.record(envelope('contact.created', contact_phone='966500000123'))
        self.assertIn('no matching customer', event.outcome)

    # -- housekeeping -----------------------------------------------------
    def test_the_log_is_tidied_but_only_what_is_old(self):
        fresh = self.record(envelope('transfer.created'))
        old = self.record(envelope('transfer.created'))
        self.env.cr.execute(
            "UPDATE kmessage_event SET create_date = now() - interval '200 days' WHERE id = %s",
            (old.id,))
        self.env['kmessage.event'].invalidate_model(['create_date'])
        self.assertEqual(self.env['kmessage.event']._cron_clean(days=90), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_the_tidy_takes_no_more_than_it_was_asked_for(self):
        """A year of a busy tenant is not one transaction's worth of deleting."""
        aged = [self.record(envelope('transfer.created')) for _ in range(3)]
        self.env.cr.execute(
            "UPDATE kmessage_event SET create_date = now() - interval '200 days' "
            "WHERE id IN %s", (tuple(event.id for event in aged),))
        self.env['kmessage.event'].invalidate_model(['create_date'])
        self.assertEqual(self.env['kmessage.event']._cron_clean(days=90, limit=2), 2)
        self.assertEqual(sum(1 for event in aged if event.exists()), 1)

    # -- what a reply is allowed to reach ---------------------------------
    def test_a_customers_own_words_are_not_html_when_they_reach_the_chatter(self):
        """A chatter body is HTML; a WhatsApp message is whatever somebody typed.

        Odoo escapes a body that is not already ``Markup``, so today this holds
        by the framework's doing rather than by ours — which is exactly what
        makes it worth pinning. The day this note is built out of ``Markup``,
        the customer's own angle brackets become markup along with it.
        """
        self.record(envelope(
            'message.incoming', contact_phone=CUSTOMER_MOBILE,
            content={'body': '<b>bold</b> & brackets'}))
        bodies = ' '.join(self.partner.message_ids.mapped('body'))
        self.assertIn('&lt;b&gt;bold&lt;/b&gt;', bodies)
        self.assertNotIn('<b>bold</b>', bodies)


#: The platform's own wording for Meta's error 131042 — product text, not a
#: customer's: the reason this whole feature exists.
PAYMENT_PROBLEM = ("Meta could not charge this WhatsApp Business account — add or update "
                   "a payment method in Meta Business Suite → Settings → Billing & payments.")


@tagged('post_install', '-at_install')
class TestDeliveryNews(KMessageCase):
    """Delivered, read and not delivered, as K-Message reports them.

    Three things are pinned here: news only ever moves a message forward, the
    reason WhatsApp gives reaches the row and — once — the document the
    message was about, and news about somebody else's message is answered
    without being written down.
    """

    def setUp(self):
        super().setUp()
        self.message = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner, record=self.partner)
        self.message.send_now()
        self.assertEqual(self.message.delivery_status, 'sent')

    def news(self, event, message=None, account=None, **data):
        message = message if message is not None else self.message
        data.setdefault('message_id', message.remote_message_id)
        data.setdefault('reference', message.reference)
        data.setdefault('contact_phone', '966512345678')
        return self.env['kmessage.event'].record_event(account or self.account, envelope(event, **data))

    def notes(self):
        return [body for body in self.partner.message_ids.mapped('body') if 'Not delivered' in body]

    # -- the order things happen in --------------------------------------
    def test_news_moves_a_message_forward_and_never_back(self):
        cases = [
            # (what arrives, in order) -> where the row ends up
            (('message.delivered',), 'delivered'),
            (('message.delivered', 'message.read'), 'read'),
            (('message.delivered', 'message.read', 'message.delivered'), 'read'),
            (('message.read', 'message.delivered'), 'read'),
            (('message.delivered', 'message.sent'), 'delivered'),
            (('message.read', 'message.sent'), 'read'),
        ]
        for events, expected in cases:
            with self.subTest(events=events):
                self.message.write({'delivery_status': 'sent', 'delivery_date': False})
                for name in events:
                    self.news(name)
                self.assertEqual(self.message.delivery_status, expected)
                self.assertTrue(self.message.delivery_date)

    def test_late_news_says_it_changed_nothing(self):
        self.news('message.read')
        event = self.news('message.delivered')
        self.assertEqual(self.message.delivery_status, 'read')
        self.assertIn('nothing changed', event.outcome)
        self.assertTrue(event.handled)

    def test_news_already_known_is_not_called_older(self):
        """Every message.sent lands on a row the send already marked sent."""
        when = self.message.delivery_date
        cases = [
            # (the row's status, the news) -> what the log says
            ('sent', 'message.sent', 'Already known; nothing changed.'),
            ('read', 'message.read', 'Already known; nothing changed.'),
            ('read', 'message.sent', 'Older news than what is already known; nothing changed.'),
        ]
        for status, name, outcome in cases:
            with self.subTest(status=status, news=name):
                self.message.write({'delivery_status': status, 'delivery_date': when})
                event = self.news(name)
                self.assertTrue(event.handled)
                self.assertEqual(event.outcome, outcome)
                self.assertEqual(self.message.delivery_status, status)
                self.assertEqual(self.message.delivery_date, when)

    def test_nothing_overrides_not_delivered(self):
        self.news('message.failed', error_message=PAYMENT_PROBLEM)
        for name in ('message.sent', 'message.delivered', 'message.read'):
            with self.subTest(event=name):
                self.news(name)
                self.assertEqual(self.message.delivery_status, 'failed')
                self.assertEqual(self.message.delivery_error, PAYMENT_PROBLEM)

    def test_not_delivered_overrides_delivered_and_read(self):
        self.news('message.delivered')
        self.news('message.read')
        self.news('message.failed', error_message=PAYMENT_PROBLEM)
        self.assertEqual(self.message.delivery_status, 'failed')

    # -- not delivered ----------------------------------------------------
    def test_not_delivered_keeps_whatsapps_reason(self):
        event = self.news('message.failed', error_message=PAYMENT_PROBLEM)
        self.assertEqual(self.message.delivery_status, 'failed')
        self.assertEqual(self.message.delivery_error, PAYMENT_PROBLEM)
        self.assertTrue(event.handled)
        self.assertIn(PAYMENT_PROBLEM, event.outcome)

    def test_not_delivered_is_written_once_on_the_document(self):
        self.news('message.failed', error_message=PAYMENT_PROBLEM)
        self.assertEqual(len(self.notes()), 1)
        self.assertIn('payment method', self.notes()[0])
        # WhatsApp may say it twice; the document hears it once.
        event = self.news('message.failed', error_message=PAYMENT_PROBLEM)
        self.assertEqual(len(self.notes()), 1)
        self.assertIn('Already marked not delivered', event.outcome)

    def test_a_second_refusal_may_bring_the_reason_the_first_lacked(self):
        self.news('message.failed')
        self.assertFalse(self.message.delivery_error)
        self.news('message.failed', error_message=PAYMENT_PROBLEM)
        self.assertEqual(self.message.delivery_error, PAYMENT_PROBLEM)
        self.assertEqual(len(self.notes()), 1)

    def test_not_delivered_is_not_a_failed_send_and_is_never_sent_again(self):
        """A resend could reach the customer twice; that is a person's decision."""
        self.news('message.failed', error_message=PAYMENT_PROBLEM)
        self.assertEqual(self.message.state, 'sent')
        self.assertFalse(self.message.permanent_error)
        self.assertFalse(self.message.next_attempt)
        self.assertFalse(self.message.error)
        self.fake.reset()
        self.env['kmessage.message']._cron_send_queue()
        self.assertFalse(self.fake.sent, 'a message WhatsApp did not deliver was sent again')

    def test_the_note_is_signed_by_odoobot_not_by_the_public_user(self):
        """The webhook runs as Odoo's public user; the news is K-Message's."""
        public = self.env.ref('base.public_user')
        self.env['kmessage.event'].with_user(public).sudo().record_event(self.account, envelope(
            'message.failed', message_id=self.message.remote_message_id,
            reference=self.message.reference, error_message=PAYMENT_PROBLEM))
        note = self.partner.message_ids.filtered(lambda m: 'Not delivered' in (m.body or ''))
        self.assertEqual(len(note), 1)
        self.assertEqual(note.author_id, self.env.ref('base.partner_root'))

    def test_the_reason_reaches_the_chatter_as_text_not_html(self):
        self.news('message.failed', error_message='<img src=x onerror=alert(1)> refused')
        note = self.notes()[0]
        self.assertNotIn('<img', note)
        self.assertIn('&lt;img', note)

    def test_a_message_about_no_document_still_records_the_news(self):
        loose = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template)
        loose.send_now()
        event = self.news('message.failed', message=loose, error_message=PAYMENT_PROBLEM)
        self.assertTrue(event.handled)
        self.assertEqual(loose.delivery_status, 'failed')
        self.assertFalse(self.notes(), 'there was no document to write on')

    # -- whose news it is -------------------------------------------------
    def test_news_signed_for_another_connection_does_not_touch_this_one(self):
        other_company = self.env['res.company'].search(
            [('id', '!=', self.company.id)], limit=1) or self.env['res.company'].create(
            {'name': 'Another company'})
        other = connection_for(self.env, other_company, {
            'name': 'Their K-Message',
            'base_url': 'https://kmessage.example.test',
            'api_key': 'their-private-token',
            'webhook_secret': 'a-secret-of-their-own',
        })
        for name in ('message.failed', 'message.delivered', 'message.read'):
            with self.subTest(event=name):
                event = self.news(name, account=other, error_message=PAYMENT_PROBLEM)
                self.assertFalse(event, 'news about our message was kept for another connection')
                self.assertEqual(self.message.delivery_status, 'sent')
        self.assertFalse(self.notes())

    def test_delivered_and_read_about_a_message_we_never_sent_are_not_kept(self):
        before = self.env['kmessage.event'].search_count([])
        for name in ('message.delivered', 'message.read'):
            with self.subTest(event=name):
                event = self.news(name, message_id='k-message-id-of-somebody-else',
                                  reference='order-from-another-system')
                self.assertFalse(event)
                self.assertEqual(event, self.env['kmessage.event'])
        self.assertEqual(self.env['kmessage.event'].search_count([]), before)

    def test_a_failure_that_names_nothing_of_ours_is_not_kept(self):
        for reference in ('order-from-another-system', ''):
            with self.subTest(reference=reference):
                event = self.news('message.failed', message_id='k-message-id-of-somebody-else',
                                  reference=reference, error_message=PAYMENT_PROBLEM)
                self.assertFalse(event)

    def test_news_without_a_message_id_is_not_kept(self):
        self.assertFalse(self.news('message.read', message_id=''))

    # -- news that arrives before its message -----------------------------
    def early_failure(self, message_id='k-message-id-early'):
        """A send-time refusal, posted while the send is still uncommitted."""
        waiting = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template,
            partner=self.partner, record=self.partner)
        self.assertFalse(waiting.remote_message_id)
        event = self.news('message.failed', message=waiting, message_id=message_id,
                          error_message=PAYMENT_PROBLEM)
        return waiting, event

    def send_committed(self, waiting, message_id):
        """The send that the early refusal was about, recorded a moment later."""
        waiting.write({'state': 'sent', 'delivery_status': 'sent',
                       'remote_message_id': message_id})

    def test_a_failure_that_beats_its_send_is_kept_for_later(self):
        waiting, event = self.early_failure()
        self.assertTrue(event, 'the failure is ours and must not be lost')
        self.assertFalse(event.handled)
        self.assertIn('Waiting', event.outcome)
        self.assertEqual(waiting.delivery_status, 'pending')

    def test_the_retry_cron_applies_it_once_the_send_is_recorded(self):
        waiting, event = self.early_failure()
        # The send commits a moment later, with the id the failure named.
        waiting.write({'state': 'sent', 'delivery_status': 'sent',
                       'remote_message_id': 'k-message-id-early'})
        self.env['kmessage.event']._cron_retry_delivery_news()
        self.assertTrue(event.handled)
        self.assertEqual(waiting.delivery_status, 'failed')
        self.assertEqual(waiting.delivery_error, PAYMENT_PROBLEM)
        self.assertEqual(waiting.state, 'sent')
        self.assertEqual(len(self.notes()), 1)

    def test_the_retry_cron_waits_while_there_is_still_nothing_to_apply_it_to(self):
        waiting, event = self.early_failure()
        self.env['kmessage.event']._cron_retry_delivery_news()
        self.assertFalse(event.handled)
        self.assertEqual(waiting.delivery_status, 'pending')

    def test_after_a_day_it_is_given_up_on(self):
        _waiting, event = self.early_failure()
        # Odoo keeps UTC; the database's own now() is in its session time zone.
        self.env.cr.execute(
            "UPDATE kmessage_event SET create_date = %s WHERE id = %s",
            (fields.Datetime.subtract(fields.Datetime.now(), hours=25), event.id))
        self.env['kmessage.event'].invalidate_model(['create_date'])
        self.env['kmessage.event']._cron_retry_delivery_news()
        self.assertTrue(event.handled)
        self.assertEqual(event.outcome, 'No matching message in the outbox.')

    def test_the_retry_cron_takes_no_more_than_it_was_asked_for(self):
        early = []
        for number in range(3):
            message_id = 'k-message-id-early-%s' % number
            waiting, event = self.early_failure(message_id)
            self.send_committed(waiting, message_id)
            early.append(event)
        retry = self.env['kmessage.event']._cron_retry_delivery_news
        self.assertEqual(retry(limit=2), 2)
        self.assertEqual([event.handled for event in early], [True, True, False])
        self.assertEqual(retry(limit=2), 1, 'the next run takes the rest')
        self.assertTrue(early[2].handled)

    def test_news_still_waiting_never_keeps_newer_news_from_its_turn(self):
        """A run reads only what it can settle now, however much is still waiting.

        Taken oldest first and bounded, the news still waiting for its message
        would fill every run, and a newer one that could be applied would wait
        until they had all been given up on a day later.
        """
        stuck = [self.early_failure('k-message-id-stuck-%s' % number)[1] for number in range(3)]
        waiting, latest = self.early_failure('k-message-id-latest')
        self.send_committed(waiting, 'k-message-id-latest')

        self.assertEqual(self.env['kmessage.event']._cron_retry_delivery_news(limit=2), 1)

        self.assertTrue(latest.handled)
        self.assertEqual(waiting.delivery_status, 'failed')
        self.assertEqual(waiting.delivery_error, PAYMENT_PROBLEM)
        self.assertFalse(any(event.handled for event in stuck))
        self.assertTrue(all('Waiting' in event.outcome for event in stuck))

    # -- a reply to a message about no record ------------------------------
    def test_a_reply_to_a_message_about_no_record_finds_its_row(self):
        """Every row now carries a reference, so a tap finds its message.

        What the rule may do with it does not change: a rule tied to a kind
        of record or to an automation still needs a message that has one.
        """
        loose = self.env['kmessage.message'].enqueue(
            account=self.account, phone=CUSTOMER_MOBILE, template=self.template)
        self.assertEqual(loose.reference, 'kmessage.message:%s' % loose.id)
        action = self.env['ir.actions.server'].create({
            'name': 'Note the confirmation',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'state': 'code',
            'code': "record.write({'ref': 'confirmed-on-whatsapp'})",
        })
        rules = self.env['kmessage.reply.rule']
        tied = rules.create({'name': 'Partners only', 'account_id': self.account.id,
                             'model_id': self.env['ir.model']._get_id('res.partner'),
                             'server_action_id': action.id, 'active': True})
        loose_rule = rules.create({'name': 'Anything', 'account_id': self.account.id,
                                   'reply_template_id': self.template.id, 'active': True})
        matched = rules.for_reply(self.account, 'yes', 'نعم', loose)
        self.assertIn(loose_rule, matched)
        self.assertNotIn(tied, matched)
