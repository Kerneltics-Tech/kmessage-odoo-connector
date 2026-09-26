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
from .common import FAKE_API_KEY, KMessageCase, mirrored_template


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
        # Ask for the default rather than making a connection just to read it.
        self.assertEqual(
            self.env['kmessage.account'].default_get(['base_url'])['base_url'],
            'http://127.0.0.1:9999')

    def test_the_customer_is_never_shown_it(self):
        """A field on a form is a question. This one is asked of nobody."""
        form = self.env.ref('kmessage_connector.view_kmessage_connect_form')
        self.assertNotIn('base_url', form.arch,
                         'the connect screen must ask for the token and nothing else')


@tagged('post_install', '-at_install')
class TestOneButton(KMessageCase):
    """Paste the token, press Connect, and be finished.

    Looking first and then asking which parts to do was honest, and it was
    also a screen of decisions nobody outside this addon is equipped to make.
    What has to stay true is that the short path does everything the long one
    did, and that the long one is still there for anyone who wants it.
    """

    def setUp(self):
        super().setUp()
        self.account.write({'webhook_secret': False, 'webhook_state': 'none',
                            'webhook_remote_id': False})
        self.env['ir.config_parameter'].sudo().set_param(
            'web.base.url', 'https://odoo.example.com')

    def _wizard(self):
        return self.env['kmessage.connect'].create({
            'api_key': FAKE_API_KEY,
            'base_url': self.fake.url,
        })

    def test_one_press_finishes_the_whole_thing(self):
        wizard = self._wizard()
        wizard.action_connect()

        self.assertEqual(wizard.state, 'done')
        self.assertTrue(wizard.account_id, 'the connection should have been saved')
        self.assertTrue(wizard.summary, 'and it should say what it did')
        self.assertEqual(wizard.account_id.state, 'connected')

    def test_it_looks_before_it_leaps_all_the_same(self):
        """A token the platform rejects must stop, not half-finish."""
        wizard = self.env['kmessage.connect'].create(
            {'api_key': 'whm_not_a_real_key', 'base_url': self.fake.url})
        with self.assertRaises(UserError):
            wizard.action_connect()
        self.assertEqual(wizard.state, 'start')
        self.assertFalse(wizard.account_id)

    def test_choosing_is_still_possible(self):
        wizard = self._wizard()
        wizard.action_check()
        self.assertEqual(wizard.state, 'checked', 'the review screen is still there')
        self.assertFalse(wizard.account_id, 'and it still changes nothing by itself')


@tagged('post_install', '-at_install')
class TestSeveralTenants(KMessageCase):
    """One Odoo company, more than one K-Message tenant.

    A company that talks to customers from two tenants — two brands, two
    numbers — needs a connection for each, and connecting the second must
    not quietly take the first one over.
    """

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param(
            'web.base.url', 'https://odoo.example.com')
        original = KMessageClient.me
        self.tenant = {'organization_id': 'org-1', 'organization': {'id': 'org-1', 'name': 'Alia'}}

        def me(client):
            return dict(original(client) or {}, **self.tenant)

        patcher = patch.object(KMessageClient, 'me', me)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _connect(self):
        wizard = self.env['kmessage.connect'].create({
            'api_key': FAKE_API_KEY,
            'base_url': self.fake.url,
            'setup_webhook': False,
            'publish_tools': False,
            'import_flows': False,
            'issue_token': False,
        })
        wizard.action_connect()
        return wizard.account_id

    def _connections(self):
        return self.env['kmessage.account'].search([('company_id', '=', self.company.id)])

    def test_a_company_may_hold_two_connections(self):
        second = self.env['kmessage.account'].create({
            'name': 'K-Message — Ziar',
            'company_id': self.company.id,
            'base_url': self.fake.url,
            'api_key': 'another-key',
        })
        self.assertIn(second, self._connections())
        self.assertIn(self.account, self._connections())

    def test_another_tenant_becomes_a_connection_of_its_own(self):
        alia = self._connect()
        self.assertEqual(alia, self.account, 'the same token is the same connection')
        self.assertEqual(alia.remote_organization, 'Alia')

        self.tenant = {'organization_id': 'org-2', 'organization': {'id': 'org-2', 'name': 'Ziar'}}
        ziar = self._connect()
        self.assertNotEqual(ziar, alia, 'Ziar must not take over Alia’s connection')
        self.assertEqual(ziar.name, 'K-Message — Ziar')
        self.assertEqual(ziar.remote_organization_id, 'org-2')
        self.assertEqual(alia.remote_organization_id, 'org-1', 'and Alia is left as it was')
        self.assertEqual(len(self._connections()), 2)

    def test_the_same_tenant_with_a_new_token_updates_its_connection(self):
        alia = self._connect()
        before = len(self._connections())
        self.assertEqual(self._connect(), alia)
        self.assertEqual(len(self._connections()), before)

    def test_each_connection_gets_the_rules(self):
        """The rules belong to a tenant: the first one's must not stop the second's."""
        starter = self.env['kmessage.starter.automation']
        catalogue = starter.catalogue()
        if not catalogue:
            self.skipTest('no bridge installed, so there are no rules to offer')
        other = self.account.copy({'name': 'K-Message — Ziar', 'api_key': 'another-key'})
        for account in (self.account, other):
            for rule in catalogue:
                mirrored_template(account, {
                    'name': rule['template'],
                    'language': 'ar',
                    'status': 'APPROVED',
                    'header_type': 'none',
                    'body_content': ' '.join('{{%d}}' % n for n in range(1, len(rule['params']) + 1)),
                })
        starter.ensure(self.account)
        made, held_back = starter.ensure(other)
        self.assertFalse(held_back)
        self.assertEqual(sorted(made), sorted(rule['key'] for rule in catalogue))
        rules = self.env['kmessage.automation'].with_context(active_test=False).search(
            [('account_id', '=', other.id), ('starter_key', '!=', False)])
        self.assertEqual(len(rules), len(catalogue))

    def test_a_token_is_refused_only_when_every_connection_is_off(self):
        other = self.account.copy({'name': 'K-Message — Ziar', 'api_key': 'another-key'})
        self.account.inbound_enabled = False
        ping = self.env['kmessage.api']._handle_ping({}, self.env['kmessage.capability'])
        self.assertTrue(ping['inbound_enabled'], 'Ziar still answers')
        other.inbound_enabled = False
        ping = self.env['kmessage.api']._handle_ping({}, self.env['kmessage.capability'])
        self.assertFalse(ping['inbound_enabled'])
