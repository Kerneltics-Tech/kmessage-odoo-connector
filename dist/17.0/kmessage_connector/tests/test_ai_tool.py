# -*- coding: utf-8 -*-
"""Publishing an Odoo capability to the K-Message assistant, and taking it back.

What makes this worth its own file is where the secret ends up. Publishing a
tool writes a bearer token into a configuration K-Message stores in the clear,
so from that moment the key into this Odoo is a thing somebody else is holding.
The tests below are therefore less about the happy path than about the way
back: taking the tool down has to take the key away with it, in every way a
tool can be taken down — unpublished, archived, deleted.
"""

import json

from odoo.exceptions import UserError

from .common import KMessageCase

PUBLIC_ODOO = 'https://odoo.example.test'


class TestAssistantTools(KMessageCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # K-Message refuses to dial a private address, and so does the fake.
        # The tool has to be published on something the internet could reach.
        cls.env['ir.config_parameter'].sudo().set_param('web.base.url', PUBLIC_ODOO)
        cls.capability = cls.env.ref('kmessage_connector.capability_customer_lookup')

    # -- fixtures ---------------------------------------------------------
    def tool(self, **values):
        return self.env['kmessage.ai.tool'].create(dict({
            'name': 'Recognise a customer',
            'tool_name': 'customer_lookup',
            'description': 'Look the caller up. Say nothing when there is no match.',
            'account_id': self.account.id,
            'capability_id': self.capability.id,
            'fields_json': json.dumps([{'name': 'name', 'label': 'Name'}]),
        }, **values))

    def published_secret(self):
        """The bearer token sitting in the configuration the fake was given."""
        contexts = self.fake.contexts
        self.assertTrue(contexts, 'nothing was registered on the platform')
        headers = contexts[-1]['api_config'].get('headers') or {}
        authorization = headers.get('Authorization') or ''
        self.assertTrue(authorization.startswith('Bearer kmc_'), authorization)
        return authorization[len('Bearer '):]

    # -- publishing -------------------------------------------------------
    def test_publishing_registers_the_tool_with_a_token_of_its_own(self):
        tool = self.tool()
        tool.action_publish()
        self.assertEqual(tool.state, 'published')
        self.assertTrue(tool.remote_id)
        self.assertTrue(tool.token_id)
        self.assertEqual(len(self.fake.contexts), 1)

        secret = self.published_secret()
        self.assertEqual(self.env['kmessage.token'].authenticate(secret), tool.token_id)

    def test_the_published_token_opens_only_the_one_capability(self):
        tool = self.tool()
        tool.action_publish()
        token = tool.token_id
        self.assertTrue(token.allows('customer_lookup'))
        self.assertFalse(token.allows('ping'))
        self.assertEqual(token.company_id, self.account.company_id)

    def test_the_customers_number_is_never_a_parameter_the_model_chooses(self):
        tool = self.tool()
        tool.action_publish()
        body = json.loads(self.fake.contexts[-1]['api_config']['body'])
        self.assertEqual(body['phone'], '{{phone_number}}')

    def test_a_tool_that_says_nothing_about_its_columns_is_refused(self):
        tool = self.tool(fields_json='[]')
        with self.assertRaises(UserError):
            tool.action_publish()
        self.assertFalse(self.fake.contexts)

    def test_an_odoo_the_assistant_could_never_reach_is_refused_before_anything_is_sent(self):
        self.env['ir.config_parameter'].sudo().set_param('web.base.url', 'http://localhost:8069')
        tool = self.tool()
        with self.assertRaises(UserError):
            tool.action_publish()
        self.assertFalse(self.fake.contexts)
        self.assertFalse(tool.token_id, 'no token should exist for a tool that never left')

    # -- taking it back ---------------------------------------------------
    def test_unpublishing_revokes_the_key_the_platform_was_holding(self):
        tool = self.tool()
        tool.action_publish()
        secret = self.published_secret()
        token = tool.token_id

        tool.action_unpublish()

        self.assertEqual(tool.state, 'draft')
        self.assertFalse(tool.remote_id)
        self.assertFalse(self.fake.contexts)
        self.assertEqual(token.state, 'revoked')
        self.assertFalse(self.env['kmessage.token'].authenticate(secret))

    def test_archiving_a_published_tool_takes_it_off_the_platform(self):
        tool = self.tool()
        tool.action_publish()
        secret = self.published_secret()

        tool.active = False

        self.assertFalse(self.fake.contexts, 'the assistant would still be offering it')
        self.assertFalse(self.env['kmessage.token'].authenticate(secret))

    def test_deleting_a_published_tool_takes_it_off_the_platform_too(self):
        tool = self.tool()
        tool.action_publish()
        secret = self.published_secret()
        token = tool.token_id

        tool.unlink()

        self.assertFalse(self.fake.contexts)
        self.assertEqual(token.state, 'revoked')
        self.assertFalse(self.env['kmessage.token'].authenticate(secret))

    def test_a_tool_that_never_reached_the_platform_still_gives_its_token_up(self):
        tool = self.tool()
        tool.action_publish()
        tool.write({'remote_id': False, 'state': 'draft'})
        token = tool.token_id

        tool.action_unpublish()

        self.assertEqual(token.state, 'revoked')

    def test_publishing_again_puts_the_same_token_back_with_a_new_secret(self):
        tool = self.tool()
        tool.action_publish()
        first = self.published_secret()
        tool.action_unpublish()

        tool.action_publish()
        second = self.published_secret()

        self.assertNotEqual(first, second)
        self.assertEqual(tool.state, 'published')
        self.assertFalse(self.env['kmessage.token'].authenticate(first))
        self.assertEqual(self.env['kmessage.token'].authenticate(second), tool.token_id)

    def test_a_token_another_published_tool_still_needs_is_left_alone(self):
        first = self.tool()
        first.action_publish()
        second = self.tool(tool_name='customer_lookup_2', token_id=first.token_id.id)
        second.action_publish()
        shared = first.token_id

        second.action_unpublish()

        self.assertEqual(shared.state, 'active', 'the first tool is still published')
        first.action_unpublish()
        self.assertEqual(shared.state, 'revoked')
