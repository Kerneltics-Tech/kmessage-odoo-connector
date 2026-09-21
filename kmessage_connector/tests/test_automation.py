# -*- coding: utf-8 -*-
"""When a rule is allowed to fire, and — more importantly — when it is not.

Core ships no triggers: knowing what "invoice posted" means is a bridge's job.
So these tests register one the way a bridge does, by extending the two hooks
``_selection_trigger`` and ``_trigger_model``, and then ask the questions that
are the same for every bridge there will ever be.

The last one is the promise the whole outbound design exists to keep: whatever
goes wrong in here, the business transaction that called it finishes.
"""

from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .common import KMessageCase, own_the_number

TRIGGER = 'test_thing_happened'


# Run once the whole registry is up. One test needs a second company, and
# res.company only carries the fields the accounting app adds — which the
# database's NOT NULL constraints demand — after every module has loaded.
# Installed on its own, at install time, the create fails on columns this
# module has never heard of.
@tagged('post_install', '-at_install')
class TestAutomation(KMessageCase):

    def setUp(self):
        super().setUp()
        automations = self.env['kmessage.automation']
        # A bridge contributes its triggers by extending these two. Patching
        # them is the same contribution, made for the length of one test.
        self.patch(type(automations), '_selection_trigger',
                   lambda self: [(TRIGGER, 'A thing happened')])
        self.patch(type(automations), '_trigger_model',
                   lambda self, trigger: 'res.partner' if trigger == TRIGGER else False)
        own_the_number(self.env, '0501112233')
        self.nora = self.env['res.partner'].create({
            'name': 'Nora', 'mobile': '0501112233', 'lang': 'en_US'})
        self.watermark = self.env['kmessage.message'].search([], order='id desc', limit=1).id or 0

    def rule(self, **values):
        return self.env['kmessage.automation'].create(dict({
            'name': 'When a thing happens',
            'active': True,
            'account_id': self.account.id,
            'trigger': TRIGGER,
            'template_id': self.template.id,
            'attach_document': False,
        }, **values))

    def outbox(self, template=None):
        """What this test queued — never what the database came with."""
        return self.env['kmessage.message'].search([
            ('id', '>', self.watermark),
            ('template_id', '=', (template or self.template).id),
        ])

    def fire(self, records=None):
        self.env['kmessage.automation'].run_trigger(TRIGGER, records or self.partner)

    def another_company(self):
        """A company that is not the connection's, however this database has one."""
        other = self.env['res.company'].search([('id', '!=', self.company.id)], limit=1)
        return other or self.env['res.company'].create({'name': 'Another company'})

    # -- firing -----------------------------------------------------------
    def test_one_message_per_matching_record(self):
        self.rule()
        self.fire(self.partner | self.nora)
        self.assertEqual(len(self.outbox()), 2)
        self.assertEqual(self.outbox().mapped('partner_id'), self.partner | self.nora)

    def test_the_rule_knows_which_model_it_is_about(self):
        self.assertEqual(self.rule().model_name, 'res.partner')

    def test_a_record_that_does_not_match_the_condition_is_left_alone(self):
        self.rule(filter_domain="[('name', '=', 'Layla')]")
        self.fire(self.partner | self.nora)
        self.assertEqual(self.outbox().partner_id, self.partner)

    def test_a_record_with_no_company_of_its_own_still_matches(self):
        """A contact belongs to every company, so it is not somebody else's record."""
        self.assertFalse(self.partner.company_id)
        self.rule()
        self.fire()
        self.assertEqual(len(self.outbox()), 1)

    def test_a_record_of_another_company_is_left_alone(self):
        self.partner.company_id = self.another_company()
        self.rule()
        self.fire()
        self.assertFalse(self.outbox())

    def test_the_parameters_are_filled_from_the_record(self):
        rule = self.rule()
        self.env['kmessage.automation.param'].create({
            'automation_id': rule.id, 'index': 1, 'source': 'field', 'field_path': 'name'})
        self.fire()
        message = self.outbox()
        self.assertEqual(message.params_json, '{"1": "Layla"}')
        self.assertIn('Layla', message.body_preview)

    def test_a_parameter_that_cannot_be_resolved_becomes_a_dash(self):
        """WhatsApp rejects an empty parameter, so a bad mapping must not be empty."""
        rule = self.rule()
        self.env['kmessage.automation.param'].create({
            'automation_id': rule.id, 'index': 1, 'source': 'field', 'field_path': 'no_such_field'})
        self.fire()
        self.assertEqual(self.outbox().params_json, '{"1": "-"}')

    def test_the_message_is_sent_in_the_customers_language(self):
        self.partner.lang = 'en_US'
        self.rule(language_mode='partner')
        self.fire()
        self.assertEqual(self.outbox().language, 'en')

    # -- not firing -------------------------------------------------------
    def test_a_rule_that_is_switched_off_does_nothing(self):
        self.rule(active=False)
        self.fire()
        self.assertFalse(self.outbox())

    def test_a_new_rule_starts_switched_off(self):
        self.assertFalse(self.env['kmessage.automation'].default_get(['active'])['active'])

    def test_nothing_goes_out_while_the_connection_is_off(self):
        self.rule()
        self.account.outbound_enabled = False
        self.fire()
        self.assertFalse(self.outbox())

    def test_nothing_goes_out_before_the_connection_has_been_tested(self):
        self.rule()
        self.account.state = 'draft'
        self.fire()
        self.assertFalse(self.outbox())

    def test_a_record_with_no_customer_is_skipped(self):
        self.rule()
        nobody = self.env['res.partner'].create({'name': 'Nobody', 'mobile': ''})
        self.env['kmessage.automation'].run_trigger(TRIGGER, nobody)
        self.assertFalse(self.outbox())

    def test_no_records_at_all_is_not_an_error(self):
        self.rule()
        self.env['kmessage.automation'].run_trigger(TRIGGER, self.env['res.partner'])
        self.assertFalse(self.outbox())

    # -- the cooldown -----------------------------------------------------
    def test_the_same_record_is_not_messaged_twice_within_the_window(self):
        self.rule(cooldown_hours=24)
        self.fire()
        self.fire()
        self.assertEqual(len(self.outbox()), 1)

    def test_an_older_message_does_not_hold_the_window_open(self):
        rule = self.rule(cooldown_hours=24)
        self.fire()
        self.env.cr.execute(
            "UPDATE kmessage_message SET create_date = %s WHERE id = %s",
            (fields.Datetime.subtract(fields.Datetime.now(), days=2), self.outbox().id))
        self.env['kmessage.message'].invalidate_model(['create_date'])
        self.fire()
        self.assertEqual(len(self.outbox()), 2)
        self.assertEqual(rule.sent_count, 2)

    def test_a_cooldown_of_zero_switches_the_protection_off(self):
        self.rule(cooldown_hours=0)
        self.fire()
        self.fire()
        self.assertEqual(len(self.outbox()), 2)

    def test_two_rules_on_one_trigger_both_fire(self):
        # The cooldown is keyed on the record and the template, not on the rule,
        # so two rules sending the same template need it out of the way.
        self.rule(cooldown_hours=0)
        second = self.rule(name='And another thing', cooldown_hours=0)
        self.fire()
        self.assertEqual(len(self.outbox()), 2)
        self.assertEqual(second.sent_count, 1)

    # -- the promise ------------------------------------------------------
    def test_a_failure_in_here_never_reaches_the_caller(self):
        """Posting an invoice does not fail because WhatsApp is unhappy."""
        self.rule(filter_domain="[('this_field_does_not_exist', '=', 1)]")
        self.fire()  # would raise if run_trigger let anything through
        self.assertFalse(self.outbox())

    def test_a_broken_rule_does_not_stop_the_next_one(self):
        self.rule(name='Broken', filter_domain="[('nope', '=', 1)]", sequence=1)
        self.rule(name='Working', sequence=2, cooldown_hours=0)
        self.fire()
        self.assertEqual(len(self.outbox()), 1)

    def test_a_database_error_leaves_the_callers_transaction_usable(self):
        """The strongest form of the promise: not even a failed query gets out.

        Without a savepoint around each rule, a query that fails in here aborts
        the transaction the invoice is being posted in, and the posting fails
        with an error about K-Message that nobody can act on.
        """
        def bad_query(*args, **kwargs):
            self.env.cr.execute('SELECT 1 FROM a_table_that_does_not_exist')

        self.rule()
        with patch.object(type(self.env['kmessage.message']), 'enqueue', bad_query):
            self.fire()
        self.partner.write({'comment': 'the invoice was posted anyway'})
        self.assertIn('the invoice was posted anyway', self.partner.comment)

    def test_a_document_that_will_not_render_does_not_stop_the_message(self):
        with patch.object(
            type(self.env['kmessage.document']), '_render',
            side_effect=ValueError('no report here'),
        ):
            self.rule(template_id=self.doc_template.id, attach_document=True)
            self.fire()
        message = self.outbox(template=self.doc_template)
        self.assertEqual(len(message), 1)
        self.assertFalse(message.attachment_id)
