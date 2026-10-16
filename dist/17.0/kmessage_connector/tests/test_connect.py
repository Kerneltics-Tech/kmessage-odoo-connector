# -*- coding: utf-8 -*-
"""Wiring both sides from one button.

What a customer used to get at the end of setup was a list of things to
forward to whoever administers their K-Message. Now the connector posts them
itself. These tests are about the three ways that goes: it works, the platform
is too old to take it, or this Odoo is somewhere nobody can call — and only the
first of those should ever leave the customer with nothing to do.
"""

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from ..models.kmessage_account import BASE_EVENTS, DELIVERY_EVENTS, STATUS_SCOPE
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


@tagged('post_install', '-at_install')
class TestDeliveryNewsSubscription(KMessageCase):
    """Delivery news is asked for only where it is limited to this Odoo's messages.

    On the platform, delivered, read and failed fire for every message the
    company sends. An Odoo subscribed to them without the scope would hear
    about every campaign message and every bot reply, so the connector asks
    for them only from a K-Message that echoes the scope back — and one that
    never confirmed it is never asked at all.
    """

    def setUp(self):
        super().setUp()
        self.account.write({'webhook_secret': False, 'webhook_state': 'none',
                            'webhook_remote_id': False, 'webhook_events': False})
        self.env['kmessage.ai.tool'].with_context(active_test=False).search(
            [('account_id', '=', self.account.id)]).unlink()
        self.env['ir.config_parameter'].sudo().set_param(
            'web.base.url', 'https://odoo.example.com')
        self.state = self.fake.module.STATE

    def old_platform(self, old=True):
        with self.state.lock:
            self.state.old_platform = old

    def connects(self):
        with self.state.lock:
            return list(self.state.connects)

    def hook(self):
        with self.state.lock:
            hooks = list(self.state.webhooks.values())
        self.assertEqual(len(hooks), 1)
        return hooks[0]

    def hand_made_hook(self, events, is_active=True):
        """A subscription somebody made in K-Message's own screens."""
        return KMessageClient(self.fake.url, FAKE_API_KEY).create_webhook(
            name='Made by hand', url=self.account.webhook_url, events=events,
            secret='their-own-secret', is_active=is_active)

    # -- a platform that scopes -------------------------------------------
    def test_a_scoping_platform_gets_all_seven_events(self):
        done, note = self.account.connect_everything()
        self.assertTrue(done, note)
        self.assertTrue(self.connects())
        for call in self.connects():
            self.assertEqual(call.get('status_scope'), STATUS_SCOPE)
        self.assertEqual(sorted(self.hook()['events']), sorted(BASE_EVENTS + DELIVERY_EVENTS))
        self.assertEqual(self.state.scopes[self.hook()['id']], STATUS_SCOPE)
        self.assertEqual(sorted(self.account._webhook_event_list()),
                         sorted(BASE_EVENTS + DELIVERY_EVENTS))
        self.assertTrue(self.account.delivery_news)
        self.assertEqual(self.account.webhook_status_scope, STATUS_SCOPE)
        self.assertIn('Delivery news is on', note)

    def test_a_full_connect_still_says_what_the_assistant_got(self):
        """As in 1.1.1, even when no tool was offered: the sentence is the outcome."""
        done, note = self.account.connect_everything()
        self.assertTrue(done, note)
        self.assertIn('The assistant can now answer 0 questions from Odoo.', note)
        self.assertIn('Delivery news is on', note)

    def test_the_news_is_added_only_after_the_scope_came_back(self):
        self.account.connect_everything()
        first, second = self.connects()
        self.assertFalse(set(DELIVERY_EVENTS) & set(first['events']),
                         'statuses were asked for before the platform confirmed the scope')
        self.assertEqual(sorted(second['events']), sorted(BASE_EVENTS + DELIVERY_EVENTS))
        self.assertEqual(second['tools'], [], 'the tools were already handled by the first call')

    def test_connecting_again_with_the_news_on_is_one_call(self):
        """No moment in which the news is off, and so none in which it is lost."""
        self.account.connect_everything()
        with self.state.lock:
            self.state.connects.clear()
        self.account.connect_everything()
        self.assertEqual(len(self.connects()), 1)
        self.assertEqual(sorted(self.connects()[0]['events']),
                         sorted(BASE_EVENTS + DELIVERY_EVENTS))
        self.assertTrue(self.account.delivery_news)

    # -- a platform from before the scope ---------------------------------
    def test_an_older_platform_is_never_asked_for_delivery_news(self):
        self.old_platform()
        done, note = self.account.connect_everything()
        self.assertTrue(done, note)
        for call in self.connects():
            self.assertFalse(set(DELIVERY_EVENTS) & set(call['events']))
        self.assertEqual(sorted(self.hook()['events']), sorted(BASE_EVENTS))
        self.assertEqual(sorted(self.account._webhook_event_list()), sorted(BASE_EVENTS))
        self.assertFalse(self.account.delivery_news)
        self.assertFalse(self.account.webhook_status_scope)
        self.assertIn('newer K-Message', note)

    def test_news_copied_from_a_hand_made_webhook_is_not_a_confirmed_scope(self):
        """The seven events a hand-made webhook had say nothing about the platform.

        Once that webhook is marked as the connector's, the next Connect must
        not carry the delivery events in its first call: a platform that never
        echoed the scope would be subscribed to every message's news.
        """
        hook = self.hand_made_hook(BASE_EVENTS + DELIVERY_EVENTS)
        self.account.connect_everything()
        self.assertTrue(self.account.delivery_news, 'the hand-made webhook sends the news')
        self.assertFalse(self.account.webhook_status_scope)
        with self.state.lock:
            self.state.webhooks[hook['id']]['headers'] = {'x-managed-by': 'odoo_connector'}
            self.state.connects.clear()
        self.old_platform()

        done, note = self.account.connect_everything()

        self.assertTrue(done, note)
        self.assertTrue(self.connects())
        for call in self.connects():
            self.assertFalse(set(DELIVERY_EVENTS) & set(call['events']),
                             'a platform that never confirmed the scope was asked for the news')
        self.assertEqual(sorted(self.hook()['events']), sorted(BASE_EVENTS))
        self.assertFalse(self.account.delivery_news)

    def test_news_without_the_scope_is_taken_back(self):
        """Only reachable if a platform stopped scoping; the four go back in."""
        self.account.connect_everything()
        self.assertTrue(self.account.delivery_news)
        self.old_platform()
        done, note = self.account.connect_everything()
        self.assertTrue(done, note)
        self.assertEqual(sorted(self.hook()['events']), sorted(BASE_EVENTS))
        self.assertFalse(self.account.delivery_news)
        self.assertIn('newer K-Message', note)

    # -- existing installs ------------------------------------------------
    def test_switching_on_delivery_news_touches_no_tool(self):
        self.old_platform()
        self.account.connect_everything()
        self.assertFalse(self.account.delivery_news)
        tool = self.env['kmessage.ai.tool'].create({
            'name': 'Invoices in Odoo (subscription test)',
            'tool_name': 'odoo_subscription_probe',
            'description': "That customer's own invoices.",
            'account_id': self.account.id,
            'capability_id': self.env.ref('kmessage_connector.capability_customer_lookup').id,
            'fields_json': '[{"name": "number", "label": "Invoice"}]',
        })
        with self.state.lock:
            self.state.connects.clear()
        self.old_platform(False)  # the provider updated K-Message

        action = self.account.action_update_subscription()

        self.assertEqual(action['params']['type'], 'success')
        self.assertEqual(action['params']['next']['tag'], 'soft_reload',
                         'the form has to show the banner gone and the events')
        self.assertNotIn('assistant', action['params']['message'],
                         'no tool was offered, so there is nothing to say about one')
        self.assertTrue(self.account.delivery_news)
        for call in self.connects():
            self.assertEqual(call['tools'], [])
        self.assertFalse(self.fake.contexts, 'no tool may be published by this button')
        self.assertEqual(tool.state, 'draft')

    def test_switching_on_with_an_older_platform_says_so(self):
        self.old_platform()
        self.account.connect_everything()
        action = self.account.action_update_subscription()
        self.assertEqual(action['params']['type'], 'warning')
        self.assertIn('newer K-Message', action['params']['message'])
        self.assertFalse(self.account.delivery_news)

    def test_only_an_administrator_may_switch_it_on(self):
        clerk = self.env['res.users'].create({
            'name': 'Clerk', 'login': 'kmessage-clerk',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('kmessage_connector.group_kmessage_user').id])],
        })
        with self.assertRaises(AccessError):
            self.account.with_user(clerk).action_update_subscription()
        self.assertFalse(self.connects())

    def test_a_webhook_made_by_hand_is_left_alone_and_the_way_out_is_named(self):
        self.hand_made_hook(BASE_EVENTS)
        done, note = self.account.connect_everything()
        self.assertTrue(done, note)
        self.assertIn('x-managed-by: odoo_connector', note)
        # Not only by hand: an older connector's own route made webhooks
        # without the marker, and a self-managed tenant's admin can add it.
        self.assertIn('older version of this connector', note)
        self.assertIn('whoever manages K-Message', note)
        self.assertEqual(sorted(self.hook()['events']), sorted(BASE_EVENTS))
        self.assertFalse(self.account.delivery_news)

    def test_a_hand_made_webhook_that_already_has_the_news_says_so(self):
        """The button never shows an empty notice."""
        self.hand_made_hook(BASE_EVENTS + DELIVERY_EVENTS)
        action = self.account.action_update_subscription()
        message = action['params']['message']
        self.assertEqual(action['params']['type'], 'success')
        self.assertIn('Delivery news is on', message)
        self.assertIn('every message the company sends', message,
                      'a hand-made webhook is not limited to this Odoo\'s messages')
        self.assertTrue(self.account.delivery_news)

    # -- the older route --------------------------------------------------
    def test_the_older_route_adds_missing_events_and_keeps_the_webhook_on(self):
        self.hand_made_hook(['message.incoming'])
        self.account.action_setup_webhook()
        hook = self.hook()
        self.assertEqual(sorted(hook['events']), sorted(BASE_EVENTS))
        self.assertTrue(hook['is_active'], 'an update without is_active switches the webhook off')
        self.assertEqual(self.account.webhook_state, 'registered')
        self.assertEqual(sorted(self.account._webhook_event_list()), sorted(BASE_EVENTS))

    def test_the_older_route_never_asks_for_delivery_news(self):
        """The general webhooks API cannot scope them, so it is never used for them."""
        self.account.action_setup_webhook()
        self.assertEqual(sorted(self.hook()['events']), sorted(BASE_EVENTS))
        self.assertFalse(self.account.delivery_news)
        self.assertFalse(self.account.webhook_status_scope)

    def test_the_older_route_marks_its_webhook_so_connect_can_scope_it_later(self):
        self.account.action_setup_webhook()
        self.assertEqual(self.hook()['headers'].get('x-managed-by'), 'odoo_connector')

        action = self.account.action_update_subscription()

        self.assertEqual(action['params']['type'], 'success', action['params']['message'])
        self.assertTrue(self.account.delivery_news)
        self.assertEqual(sorted(self.hook()['events']), sorted(BASE_EVENTS + DELIVERY_EVENTS))
        self.assertEqual(self.state.scopes[self.hook()['id']], STATUS_SCOPE)

    def test_the_hand_over_text_names_all_seven_events(self):
        form = self.env.ref('kmessage_connector.view_kmessage_account_form')
        for event in BASE_EVENTS + DELIVERY_EVENTS:
            self.assertIn(event, form.arch)
        # and what the last three cost on a webhook nobody can scope
        self.assertIn('every message the company sends', form.arch)
