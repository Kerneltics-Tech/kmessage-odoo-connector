# -*- coding: utf-8 -*-
"""Wiring both sides from one button.

What a customer used to get at the end of setup was a list of things to
forward to whoever administers their K-Message. Now the connector posts them
itself. These tests are about the three ways that goes: it works, the platform
is too old to take it, or this Odoo is somewhere nobody can call — and only the
first of those should ever leave the customer with nothing to do.
"""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..tools.client import DEFAULT_BASE_URL, KMessageClient, KMessageError
from .common import KMessageCase


@tagged('post_install', '-at_install')
class TestConnectEverything(KMessageCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.capability = cls.env.ref('kmessage_connector.capability_customer_lookup')

    def setUp(self):
        super().setUp()
        self.account.write({'webhook_secret': False, 'webhook_state': 'none',
                            'webhook_remote_id': False})
        # The bridges seed their own tools on a database that has them, and
        # this test counts what was sent, so it starts from none.
        self.env['kmessage.ai.tool'].with_context(active_test=False).search(
            [('account_id', '=', self.account.id)]).unlink()
        self.tool = self.env['kmessage.ai.tool'].create({
            'name': 'Invoices in Odoo (connect test)',
            'tool_name': 'odoo_connect_probe',
            'description': "That customer's own invoices. Say nothing when there is no match.",
            'account_id': self.account.id,
            'capability_id': self.capability.id,
            'fields_json': '[{"name": "number", "label": "Invoice"}]',
        })
        # The platform refuses an address it could not call, so the connection
        # has to look like a published Odoo for any of this to be attempted.
        self.env['ir.config_parameter'].sudo().set_param(
            'web.base.url', 'https://odoo.example.com')

    # -- the way it should go ---------------------------------------------
    def test_one_call_registers_the_webhook_and_the_tools(self):
        done, note = self.account.connect_everything()
        self.assertTrue(done, note)
        self.assertEqual(self.account.webhook_state, 'registered')
        self.assertTrue(self.account.webhook_remote_id)
        self.assertTrue(self.account.sudo().webhook_secret)
        self.assertEqual(self.tool.state, 'published')
        self.assertIn('1', note)

        registered = self.fake.module.STATE.webhooks
        self.assertEqual(len(registered), 1)
        hook = list(registered.values())[0]
        self.assertEqual(hook['url'], self.account.webhook_url)
        self.assertIn('button.reply', hook['events'])

    def test_the_tool_is_sent_with_a_token_of_its_own(self):
        self.account.connect_everything()
        contexts = list(self.fake.module.STATE.contexts.values())
        self.assertEqual(len(contexts), 1)
        headers = contexts[0]['api_config'].get('headers') or {}
        self.assertTrue(headers.get('Authorization', '').startswith('Bearer kmc_'))
        self.assertTrue(self.tool.token_id, 'the tool should hold the token it was given')

    def test_connecting_twice_leaves_one_subscription(self):
        self.account.connect_everything()
        self.account.connect_everything()
        self.assertEqual(len(self.fake.module.STATE.webhooks), 1)

    # -- the ways it should not ------------------------------------------
    def test_an_odoo_nobody_can_call_is_refused_before_anything_is_sent(self):
        self.env['ir.config_parameter'].sudo().set_param('web.base.url', 'http://127.0.0.1:8069')
        done, note = self.account.connect_everything()
        self.assertFalse(done)
        self.assertIn('127.0.0.1', note)
        self.assertFalse(self.fake.module.STATE.webhooks, 'nothing should have been sent')
        self.assertEqual(self.account.webhook_state, 'none')

    def test_an_older_platform_hands_back_to_the_long_way(self):
        """A 404 is not a failure — it is a platform without the endpoint."""
        def refuse(*args, **kwargs):
            raise KMessageError('Not Found', status=404)

        with patch.object(KMessageClient, 'connect_odoo', refuse):
            done, note = self.account.connect_everything()
        self.assertFalse(done)
        self.assertIn('yet', note)
        self.assertEqual(self.account.webhook_state, 'none')

    def test_a_refusal_is_reported_rather_than_raised(self):
        def refuse(*args, **kwargs):
            raise KMessageError('This is managed by your provider.',
                                status=403, error_type='operator_only')

        with patch.object(KMessageClient, 'connect_odoo', refuse):
            done, note = self.account.connect_everything()
        self.assertFalse(done)
        self.assertIn('provider', note)

    def test_anything_else_is_a_real_error(self):
        def blow_up(*args, **kwargs):
            raise KMessageError('the database is on fire', status=500)

        with patch.object(KMessageClient, 'connect_odoo', blow_up):
            with self.assertRaises(UserError):
                self.account.connect_everything()


@tagged('post_install', '-at_install')
class TestTheAddressIsNotAQuestion(KMessageCase):
    """There is one K-Message, so nobody is asked where it is.

    It stays overridable, because a developer running against the stand-in in
    ``dev/fake_kmessage.py`` has to be able to say so somewhere — just not on
    the screen a customer sees.
    """

    def test_the_wizard_opens_with_the_address_already_known(self):
        # The built-in address is what a database with nothing configured
        # gets, so say so rather than inheriting whatever this one has.
        self.env['ir.config_parameter'].sudo().set_param('kmessage.base_url', '')
        wizard = self.env['kmessage.connect'].create({'api_key': 'whm_x'})
        self.assertEqual(wizard.base_url, DEFAULT_BASE_URL)

    def test_a_system_parameter_moves_it(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'kmessage.base_url', 'http://127.0.0.1:9999/')
        wizard = self.env['kmessage.connect'].create({'api_key': 'whm_x'})
        self.assertEqual(wizard.base_url, 'http://127.0.0.1:9999',
                         'a trailing slash would double the one in every path')
        # A connection is one per company, so ask for the default rather than
        # making a second one.
        self.assertEqual(
            self.env['kmessage.account'].default_get(['base_url'])['base_url'],
            'http://127.0.0.1:9999')

    def test_the_customer_is_never_shown_it(self):
        """A field on a form is a question. This one is asked of nobody."""
        form = self.env.ref('kmessage_connector.view_kmessage_connect_form')
        self.assertNotIn('base_url', form.arch,
                         'the connect screen must ask for the token and nothing else')
