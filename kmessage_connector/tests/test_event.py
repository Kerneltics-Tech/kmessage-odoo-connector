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

from .common import CUSTOMER_MOBILE, KMessageCase


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
