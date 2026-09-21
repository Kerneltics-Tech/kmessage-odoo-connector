# -*- coding: utf-8 -*-
"""A token is the whole of the inbound side's security, so it is tested as one.

What matters is not that ``authenticate`` works — it is that everything else
does not: a revoked token, an expired one, one that was never issued, and a
database row that somebody read off a backup all have to authenticate to
nothing.
"""

import json

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

from odoo.addons.kmessage_connector.models.kmessage_token import TOKEN_PREFIX


class TestToken(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ping = cls.env.ref('kmessage_connector.capability_ping')
        cls.lookup = cls.env.ref('kmessage_connector.capability_customer_lookup')

    def issue(self, capabilities=None, **values):
        token, raw = self.env['kmessage.token'].issue(
            'Test token', capabilities=capabilities if capabilities is not None else self.ping)
        if values:
            token.write(values)
        return token, raw

    # -- issuing ----------------------------------------------------------
    def test_a_token_that_was_just_issued_authenticates_to_itself(self):
        token, raw = self.issue()
        self.assertTrue(raw.startswith(TOKEN_PREFIX))
        self.assertEqual(self.env['kmessage.token'].authenticate(raw), token)
        self.assertEqual(token.state, 'active')

    def test_the_raw_token_cannot_be_read_back_off_the_record(self):
        """A leaked database must not hand anybody access to this Odoo."""
        token, raw = self.issue()
        secret_part = raw[len(TOKEN_PREFIX) + 6:]
        stored = json.dumps(token.sudo().read()[0], default=str)
        self.assertNotIn(raw, stored)
        self.assertNotIn(secret_part, stored)
        self.assertNotEqual(token.sudo().token_hash, raw)
        # The hint is enough to tell two tokens apart and no more.
        self.assertTrue(token.token_hint.startswith(raw[:len(TOKEN_PREFIX) + 6]))
        self.assertTrue(token.token_hint.endswith('…'))

    def test_two_tokens_issued_in_a_row_are_different(self):
        _first, one = self.issue()
        _second, two = self.issue()
        self.assertNotEqual(one, two)

    # -- refusing ---------------------------------------------------------
    def test_a_token_nobody_issued_authenticates_to_nothing(self):
        self.issue()
        for raw in ('', None, 'nonsense', TOKEN_PREFIX, TOKEN_PREFIX + 'made-up-value'):
            self.assertFalse(self.env['kmessage.token'].authenticate(raw), repr(raw))

    def test_a_revoked_token_stops_working_at_once(self):
        token, raw = self.issue()
        token.action_revoke()
        self.assertEqual(token.state, 'revoked')
        self.assertFalse(self.env['kmessage.token'].authenticate(raw))

    def test_an_expired_token_stops_working(self):
        token, raw = self.issue()
        token.expires_on = fields.Date.subtract(fields.Date.context_today(token), days=1)
        self.assertEqual(token.state, 'expired')
        self.assertFalse(self.env['kmessage.token'].authenticate(raw))

    def test_a_token_that_expires_today_still_works(self):
        token, raw = self.issue()
        token.expires_on = fields.Date.context_today(token)
        self.assertEqual(self.env['kmessage.token'].authenticate(raw), token)

    def test_regenerating_replaces_the_secret(self):
        token, raw = self.issue()
        fresh = token.regenerate()
        self.assertNotEqual(fresh, raw)
        self.assertFalse(self.env['kmessage.token'].authenticate(raw))
        self.assertEqual(self.env['kmessage.token'].authenticate(fresh), token)

    # -- what a token may ask for -----------------------------------------
    def test_a_token_may_only_use_what_it_was_given(self):
        token, _raw = self.issue(capabilities=self.ping)
        self.assertTrue(token.allows('ping'))
        self.assertFalse(token.allows('customer_lookup'))
        self.assertFalse(token.allows('nothing_called_this'))

    def test_switching_a_capability_off_overrides_the_token(self):
        """The switch in Odoo wins, whatever was handed out months ago."""
        token, _raw = self.issue(capabilities=self.ping | self.lookup)
        self.assertTrue(token.allows('customer_lookup'))
        self.lookup.active = False
        self.assertFalse(token.allows('customer_lookup'))
        self.assertTrue(token.allows('ping'))

    # -- housekeeping -----------------------------------------------------
    def test_using_a_token_is_recorded(self):
        token, _raw = self.issue()
        self.assertEqual(token.use_count, 0)
        self.assertFalse(token.last_used)
        token.note_use()
        self.assertEqual(token.use_count, 1)
        self.assertTrue(token.last_used)

    def test_a_token_that_was_used_is_revoked_rather_than_deleted(self):
        token, _raw = self.issue()
        token.note_use()
        with self.assertRaises(UserError):
            token.unlink()
        unused, _raw = self.issue()
        unused.unlink()
        self.assertFalse(unused.exists())
