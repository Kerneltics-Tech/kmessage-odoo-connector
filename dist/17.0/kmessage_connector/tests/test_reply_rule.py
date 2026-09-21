# -*- coding: utf-8 -*-
"""A tap on a button, and what Odoo is allowed to do about it.

The rule under test is the one that makes a reply worth having: the customer
answers "yes", and the invoice it was about is marked. Everything here is about
keeping that power pointed at the right record — the one Odoo itself named when
it sent the message — and about the tap that matches no rule still being
recorded rather than swallowed.
"""

from odoo.exceptions import UserError

from odoo.tests import tagged

from .common import KMessageCase, connection_for


class TestReplyRule(KMessageCase):

    def setUp(self):
        super().setUp()
        self.message = self.env['kmessage.message'].create({
            'account_id': self.account.id,
            'partner_id': self.partner.id,
            'phone': '966507386853',
            'template_name': 'invoice_ready',
            'state': 'sent',
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'reference': 'rule-test',
        })
        self.action = self.env['ir.actions.server'].create({
            'name': 'Note the confirmation',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'state': 'code',
            'code': "record.write({'ref': 'confirmed-on-whatsapp'})",
        })

    # -- helpers ----------------------------------------------------------
    def rule(self, **values):
        return self.env['kmessage.reply.rule'].create(dict({
            'name': 'Confirmation',
            'account_id': self.account.id,
            'button_id': 'confirm',
            'server_action_id': self.action.id,
            'active': True,
        }, **values))

    def tap(self, button_id='confirm', text='نعم', reference='rule-test'):
        return self.env['kmessage.event'].record_event(self.account, {
            'event': 'button.reply',
            'timestamp': '2026-09-20T17:00:00Z',
            'data': {
                'message_id': 'wamid-1',
                'contact_phone': '966507386853',
                'button': {'id': button_id, 'text': text},
                'in_reply_to': {'reference': reference},
            },
        })

    # -- the happy path ---------------------------------------------------
    def test_a_matching_tap_runs_the_action_on_the_right_record(self):
        rule = self.rule()
        event = self.tap()
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.ref, 'confirmed-on-whatsapp')
        self.assertTrue(event.handled)
        self.assertIn(rule.name, event.outcome)
        self.assertEqual(rule.run_count, 1)

    def test_a_tap_nobody_asked_for_changes_nothing(self):
        rule = self.rule()
        event = self.tap(button_id='something_else', text='لا')
        self.partner.invalidate_recordset()
        self.assertFalse(self.partner.ref)
        self.assertEqual(rule.run_count, 0)
        # Still recorded: an unmatched tap is news, not noise.
        self.assertTrue(event.handled)

    def test_an_inactive_rule_does_not_run(self):
        rule = self.rule(active=False)
        self.tap()
        self.partner.invalidate_recordset()
        self.assertFalse(self.partner.ref)
        self.assertEqual(rule.run_count, 0)

    def test_a_rule_can_match_on_the_words_instead_of_the_payload(self):
        """Not every button carries a payload; some only have their text."""
        self.rule(button_id=False, button_text='نعم')
        self.tap(button_id='', text='نعم')
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.ref, 'confirmed-on-whatsapp')

    def test_matching_on_words_ignores_case_and_padding(self):
        self.rule(button_id=False, button_text='Yes')
        self.tap(button_id='', text='  yes ')
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.ref, 'confirmed-on-whatsapp')

    # -- the promises -----------------------------------------------------
    def test_only_the_first_tap_counts(self):
        rule = self.rule(once_only=True)
        self.tap()
        self.partner.write({'ref': False})
        self.tap()
        self.partner.invalidate_recordset()
        self.assertFalse(self.partner.ref, 'the second tap should not have run the action again')
        self.assertEqual(rule.run_count, 1)

    def test_a_rule_whose_action_fails_does_not_break_the_webhook(self):
        broken = self.env['ir.actions.server'].create({
            'name': 'Broken',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'state': 'code',
            'code': "raise ValueError('nope')",
        })
        self.rule(server_action_id=broken.id)
        event = self.tap()
        # The webhook still settles, and the log says what went wrong.
        self.assertTrue(event.handled)
        self.assertIn('failed', (event.outcome or '').lower())

    def test_a_reply_to_a_message_we_never_sent_acts_on_nothing(self):
        rule = self.rule()
        event = self.tap(reference='a-reference-from-somewhere-else')
        self.partner.invalidate_recordset()
        self.assertFalse(self.partner.ref)
        self.assertEqual(rule.run_count, 0)
        self.assertTrue(event.handled)

    def test_a_tap_with_no_connection_left_on_it_matches_no_rule(self):
        """``account_id`` is set to null when a connection is deleted."""
        self.rule()
        matched = self.env['kmessage.reply.rule'].for_reply(
            self.env['kmessage.account'], 'confirm', 'نعم', self.message)
        self.assertFalse(matched)

    def test_a_rule_is_not_held_back_by_another_whose_name_contains_its_own(self):
        """“Pay” has not run just because “Payment received” has."""
        self.rule(name='Payment received')
        self.tap()
        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.ref, 'confirmed-on-whatsapp')

        second_action = self.env['ir.actions.server'].create({
            'name': 'Note the payment',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'state': 'code',
            'code': "record.write({'function': 'paid-on-whatsapp'})",
        })
        short = self.rule(name='Pay', server_action_id=second_action.id)

        self.tap()

        self.partner.invalidate_recordset()
        self.assertEqual(self.partner.function, 'paid-on-whatsapp')
        self.assertEqual(short.run_count, 1)

    def test_a_rule_must_do_something(self):
        with self.assertRaises(UserError):
            self.rule(server_action_id=False, reply_template_id=False)

    def test_the_answer_template_is_queued_to_the_customer(self):
        self.rule(server_action_id=False, reply_template_id=self.template.id)
        self.tap()
        answer = self.env['kmessage.message'].search([
            ('template_name', '=', self.template.name),
            ('partner_id', '=', self.partner.id),
            ('id', '!=', self.message.id),
        ])
        self.assertEqual(len(answer), 1)
        self.assertEqual(answer.state, 'queued')


# A second company only exists once accounting has loaded: the database's own
# NOT NULL columns are more than res.company carries while this module is
# still being installed.
@tagged('post_install', '-at_install')
class TestWhatATapCanReach(KMessageCase):
    """A reference is a model name and an id. It is guessable, and it is not a permit."""

    def test_a_tap_cannot_reach_a_message_another_connection_sent(self):
        """Two companies, two K-Message tenants, two webhook secrets.

        A tap signed by one of them, naming the other's reference, must find
        nothing: the record it points at is not this connection's to act on.
        """
        other_company = self._another_company()
        other_account = connection_for(self.env, other_company, {
            'name': 'Their K-Message',
            'base_url': 'https://kmessage.example.test',
            'api_key': 'their-private-token',
            'webhook_secret': 'a-secret-of-their-own',
        })
        theirs = self.env['res.partner'].create({'name': 'Their customer'})
        reference = 'res.partner:%s' % theirs.id
        self.env['kmessage.message'].create({
            'account_id': other_account.id,
            'partner_id': theirs.id,
            'phone': '966500000001',
            'template_name': 'invoice_ready',
            'state': 'sent',
            'res_model': 'res.partner',
            'res_id': theirs.id,
            'reference': reference,
        })

        action = self.env['ir.actions.server'].create({
            'name': 'Note the confirmation',
            'model_id': self.env['ir.model']._get_id('res.partner'),
            'state': 'code',
            'code': "record.write({'ref': 'confirmed-on-whatsapp'})",
        })
        rule = self.env['kmessage.reply.rule'].create({
            'name': 'Confirmation',
            'account_id': self.account.id,
            'button_id': 'confirm',
            'server_action_id': action.id,
            'active': True,
        })

        event = self.env['kmessage.event'].record_event(self.account, {
            'event': 'button.reply',
            'data': {
                'message_id': 'wamid-9',
                'contact_phone': '966507386853',
                'button': {'id': 'confirm', 'text': 'نعم'},
                'in_reply_to': {'reference': reference},
            },
        })

        theirs.invalidate_recordset()
        self.assertFalse(theirs.ref, 'our tap changed a record belonging to another company')
        self.assertEqual(rule.run_count, 0)
        self.assertTrue(event.handled)

    def test_delivery_news_does_not_settle_another_connections_message(self):
        other_company = self._another_company()
        other_account = connection_for(self.env, other_company, {
            'name': 'Their K-Message',
            'base_url': 'https://kmessage.example.test',
            'api_key': 'their-private-token',
            'webhook_secret': 'a-secret-of-their-own',
        })
        theirs = self.env['kmessage.message'].create({
            'account_id': other_account.id,
            'phone': '966500000001',
            'template_name': 'invoice_ready',
            'state': 'sent',
            'remote_message_id': 'wamid-shared',
        })

        self.env['kmessage.event'].record_event(self.account, {
            'event': 'message.sent',
            'data': {'message_id': 'wamid-shared'},
        })

        theirs.invalidate_recordset()
        self.assertEqual(theirs.delivery_status, 'pending')

    def _another_company(self):
        other = self.env['res.company'].search([('id', '!=', self.company.id)], limit=1)
        return other or self.env['res.company'].create({'name': 'Another company'})
