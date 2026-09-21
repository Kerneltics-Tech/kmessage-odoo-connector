# -*- coding: utf-8 -*-
"""Who may do what, checked as a person rather than as the superuser.

Every other test here runs as the superuser, which is convenient and blind:
it passes whatever the access rules say. These run as the two groups the addon
actually ships — an administrator who holds the connection, and a user who may
look at what was sent and send a document by hand — and check both halves of
the promise: that each can do their own job, and that the private token is not
part of it.
"""

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user

from .common import connect


class TestWhoMayDoWhat(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connect(cls, 'https://kmessage.example.test')
        cls.administrator = new_test_user(
            cls.env, login='kmessage-administrator',
            groups='base.group_user,kmessage_connector.group_kmessage_manager')
        cls.user = new_test_user(
            cls.env, login='kmessage-user',
            groups='base.group_user,kmessage_connector.group_kmessage_user')

    # -- doing the job ----------------------------------------------------
    def test_an_administrator_can_open_the_setup_screen(self):
        wizard = self.env['kmessage.connect'].with_user(self.administrator).create({
            'base_url': 'https://kmessage.example.test',
            'api_key': 'a-private-token',
        })
        self.assertTrue(wizard.id)

    def test_an_administrator_can_be_shown_a_token_they_just_made(self):
        token, _raw = self.env['kmessage.token'].issue('For K-Message')
        action = token.with_user(self.administrator).action_regenerate()
        self.assertEqual(action['res_model'], 'kmessage.token.reveal')
        revealed = self.env['kmessage.token.reveal'].browse(action['res_id'])
        self.assertTrue(revealed.raw_token.startswith('kmc_'))

    def test_a_user_can_send_a_document_by_hand(self):
        wizard = self.env['kmessage.send'].with_user(self.user).create({
            'account_id': self.account.id,
            'res_model': 'res.partner',
            'res_id': self.partner.id,
            'partner_id': self.partner.id,
            'phone': '966507386853',
            'template_id': self.template.id,
        })
        self.assertTrue(wizard.id)

    def test_a_user_can_read_what_was_sent(self):
        message = self.env['kmessage.message'].enqueue(
            account=self.account, phone='966507386853', template=self.template,
            partner=self.partner)
        self.assertEqual(message.with_user(self.user).template_name, 'order_confirmed')

    # -- not doing somebody else's ----------------------------------------
    def test_the_private_token_is_not_readable_by_a_user(self):
        account = self.account.with_user(self.user)
        with self.assertRaises(AccessError):
            account.api_key  # noqa: B018 - reading it is the whole test
        with self.assertRaises(AccessError):
            account.webhook_secret  # noqa: B018

    def test_tokens_are_not_readable_by_a_user_at_all(self):
        self.env['kmessage.token'].issue('For K-Message')
        with self.assertRaises(AccessError):
            self.env['kmessage.token'].with_user(self.user).search([])

    def test_a_user_does_not_change_the_connection(self):
        with self.assertRaises(AccessError):
            self.account.with_user(self.user).write({'outbound_enabled': False})
