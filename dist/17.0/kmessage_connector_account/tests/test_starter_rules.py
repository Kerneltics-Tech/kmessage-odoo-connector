# -*- coding: utf-8 -*-
"""The rules that come with the addon.

Three things have to hold, and the third is the one that would hurt. They
arrive filled in and the invoice one arrives live, or installing this achieves
nothing. A deleted one can be put back. And putting one back must never touch
what the customer has since done — because the only reason to press that
button is that something is already wrong, and a button that overwrites your
own rules while fixing the defaults is worse than no button.
"""

from odoo.tests import tagged

from odoo.addons.kmessage_connector.tests.common import KMessageCase


@tagged('post_install', '-at_install')
class TestStarterRules(KMessageCase):

    def setUp(self):
        super().setUp()
        self.env['kmessage.automation'].with_context(active_test=False).search(
            [('company_id', '=', self.account.company_id.id)]).unlink()
        self.template = self._template('odoo_invoice_ready', header_type='DOCUMENT')

    def _template(self, name, header_type=False):
        """The mirrored template, however this database came by it."""
        values = {
            'status': 'APPROVED',
            'header_type': header_type,
            'body_content': 'مرحباً {{1}}، فاتورتك {{2}} بمبلغ {{3}} جاهزة، وشكراً لك.',
        }
        existing = self.env['kmessage.template'].search(
            [('account_id', '=', self.account.id), ('name', '=', name)], limit=1)
        if existing:
            existing.write(values)
            return existing
        return self.env['kmessage.template'].create(
            dict(values, account_id=self.account.id, name=name, language='ar'))

    def _starters(self):
        return self.env['kmessage.automation'].with_context(active_test=False).search(
            [('company_id', '=', self.account.company_id.id),
             ('starter_key', '!=', False)])

    # -- it arrives -------------------------------------------------------
    def test_the_invoice_rule_arrives_filled_in_and_live(self):
        made, _held = self.env['kmessage.starter.automation'].ensure(self.account)
        self.assertIn('account.invoice_ready', made)

        rule = self._starters().filtered(lambda r: r.starter_key == 'account.invoice_ready')
        self.assertTrue(rule.active, 'the invoice rule is the one that arrives on')
        self.assertEqual(rule.template_id, self.template)
        self.assertEqual(
            [(p.index, p.field_path) for p in rule.param_ids.sorted('index')],
            [(1, 'partner_id.name'), (2, 'name'), (3, 'amount_total')])
        self.assertTrue(rule.attach_document, 'the template carries a document header')

    def test_the_rest_arrive_switched_off(self):
        self._template('odoo_payment_received')
        self.env['kmessage.starter.automation'].ensure(self.account)
        thanks = self._starters().filtered(
            lambda r: r.starter_key == 'account.payment_received')
        self.assertTrue(thanks, 'the payment rule should be offered')
        self.assertFalse(thanks.active, 'nobody asked to message everyone who pays')

    def test_a_rule_with_no_template_is_held_back_rather_than_pointed_at_nothing(self):
        self.env['kmessage.template'].search(
            [('account_id', '=', self.account.id),
             ('name', '=', 'odoo_payment_received')]).unlink()
        _made, held = self.env['kmessage.starter.automation'].ensure(self.account)
        self.assertIn('account.payment_received', held)
        self.assertFalse(
            self._starters().filtered(lambda r: r.starter_key == 'account.payment_received'))

    # -- it can be put back ----------------------------------------------
    def test_a_deleted_default_comes_back(self):
        self.env['kmessage.starter.automation'].ensure(self.account)
        self._starters().filtered(
            lambda r: r.starter_key == 'account.invoice_ready').unlink()

        self.account.action_restore_default_rules()
        self.assertTrue(
            self._starters().filtered(lambda r: r.starter_key == 'account.invoice_ready'))

    def test_restoring_twice_leaves_one_of_each(self):
        self.env['kmessage.starter.automation'].ensure(self.account)
        before = len(self._starters())
        self.account.action_restore_default_rules()
        self.assertEqual(len(self._starters()), before)

    # -- and it never touches what is already there -----------------------
    def test_an_edited_default_is_left_exactly_as_it_is(self):
        self.env['kmessage.starter.automation'].ensure(self.account)
        rule = self._starters().filtered(lambda r: r.starter_key == 'account.invoice_ready')
        rule.write({'name': 'Ours, renamed', 'active': False, 'cooldown_hours': 72})

        self.account.action_restore_default_rules()
        rule.invalidate_recordset()
        self.assertEqual(rule.name, 'Ours, renamed')
        self.assertFalse(rule.active, 'switching a default off must survive a restore')
        self.assertEqual(rule.cooldown_hours, 72)

    def test_a_rule_the_customer_wrote_is_never_looked_at(self):
        theirs = self.env['kmessage.automation'].create({
            'name': 'Mine, and nothing to do with the addon',
            'trigger': 'account.move.posted',
            'template_id': self.template.id,
            'account_id': self.account.id,
            'cooldown_hours': 1,
        })
        self.env['kmessage.starter.automation'].ensure(self.account)
        self.account.action_restore_default_rules()

        theirs.invalidate_recordset()
        self.assertFalse(theirs.starter_key, 'a hand-made rule carries no marker')
        self.assertEqual(theirs.cooldown_hours, 1)
        self.assertEqual(theirs.name, 'Mine, and nothing to do with the addon')
