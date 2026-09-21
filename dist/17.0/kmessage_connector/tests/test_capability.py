# -*- coding: utf-8 -*-
"""Everything that has to be true before a handler is allowed to run.

The refusals are the interesting part. Each one has a code a flow builder can
branch on and a status that means what it says, so that "switched off in Odoo"
and "this token may not" are never confused for "your customer does not exist".

The last few tests are about the promise the whole inbound side rests on:
a personal capability is given a phone number and answers for that person only.
"""

from odoo.tests.common import TransactionCase

from odoo.addons.kmessage_connector.models.kmessage_api import KMessageApiError

from .common import CUSTOMER_MOBILE, own_the_number


class TestCapabilityGate(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api = cls.env['kmessage.api']
        cls.ping = cls.env.ref('kmessage_connector.capability_ping')
        cls.lookup = cls.env.ref('kmessage_connector.capability_customer_lookup')
        cls.all = cls.env['kmessage.capability'].with_context(active_test=False).search([])
        cls.token, _raw = cls.env['kmessage.token'].issue('Test token', capabilities=cls.all)

    def refusal(self, code, payload=None, token=None):
        with self.assertRaises(KMessageApiError) as caught:
            self.api.dispatch(code, payload or {}, token or self.token)
        return caught.exception

    def test_a_capability_that_does_not_exist_is_a_404(self):
        error = self.refusal('there_is_no_such_thing')
        self.assertEqual(error.code, 'unknown_capability')
        self.assertEqual(error.status, 404)

    def test_a_capability_switched_off_is_refused_with_a_reason(self):
        self.ping.active = False
        error = self.refusal('ping')
        self.assertEqual(error.code, 'capability_disabled')
        self.assertEqual(error.status, 403)
        self.assertIn(self.ping.name, error.message)

    def test_a_capability_whose_app_is_missing_is_unavailable(self):
        """Not disabled and not unknown: the Odoo side of it cannot work at all."""
        capability = self.env['kmessage.capability'].create({
            'code': 'needs_something_else',
            'name': 'Needs another app',
            'requires_module': 'a_module_nobody_installed',
        })
        self.assertFalse(capability.available)
        self.token.capability_ids = [(4, capability.id)]
        error = self.refusal('needs_something_else')
        self.assertEqual(error.code, 'capability_unavailable')
        self.assertEqual(error.status, 503)

    def test_a_capability_whose_app_is_installed_is_available(self):
        capability = self.env['kmessage.capability'].create({
            'code': 'needs_base',
            'name': 'Needs base',
            'requires_module': 'base',
        })
        self.assertTrue(capability.available)

    def test_a_token_without_the_capability_is_refused(self):
        narrow, _raw = self.env['kmessage.token'].issue('Narrow', capabilities=self.ping)
        error = self.refusal('customer_lookup', token=narrow)
        self.assertEqual(error.code, 'not_permitted')
        self.assertEqual(error.status, 403)

    def test_a_capability_with_no_handler_says_so_rather_than_failing(self):
        capability = self.env['kmessage.capability'].create({
            'code': 'not_written_yet', 'name': 'Not written yet'})
        self.token.capability_ids = [(4, capability.id)]
        error = self.refusal('not_written_yet')
        self.assertEqual(error.code, 'not_implemented')
        self.assertEqual(error.status, 501)

    def test_a_capability_is_found_even_when_it_is_switched_off(self):
        """Otherwise "switched off" would be indistinguishable from "no such thing"."""
        self.ping.active = False
        self.assertEqual(self.env['kmessage.capability'].of('ping'), self.ping)

    def test_a_successful_call_is_counted(self):
        before = self.ping.use_count
        self.api.dispatch('ping', {}, self.token)
        self.assertEqual(self.ping.use_count, before + 1)

    def test_ping_says_what_is_switched_on(self):
        answer = self.api.dispatch('ping', {}, self.token)
        self.assertTrue(answer['odoo'])
        self.assertEqual(answer['company'], self.env.company.display_name)
        codes = {row['code'] for row in answer['capabilities']}
        self.assertIn('ping', codes)
        self.assertIn('customer_lookup', codes)

    def test_the_description_of_the_api_leaves_out_what_is_off(self):
        self.lookup.active = False
        answer = self.api.dispatch('tools', {}, self.token)
        codes = {row['code'] for row in answer['capabilities']}
        self.assertIn('ping', codes)
        self.assertNotIn('customer_lookup', codes)


class TestRowLimit(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api = cls.env['kmessage.api']
        cls.capability = cls.env['kmessage.capability'].create({
            'code': 'some_list', 'name': 'Some list', 'max_rows': 10})

    def test_nothing_asked_for_means_the_ceiling(self):
        self.assertEqual(self.api._limit({}, self.capability), 10)

    def test_less_than_the_ceiling_is_honoured(self):
        self.assertEqual(self.api._limit({'limit': 3}, self.capability), 3)
        self.assertEqual(self.api._limit({'limit': '3'}, self.capability), 3)

    def test_more_than_the_ceiling_is_capped(self):
        self.assertEqual(self.api._limit({'limit': 500}, self.capability), 10)

    def test_nonsense_is_treated_as_nothing_asked_for(self):
        for asked in ('all of them', None, '', -4, 0):
            self.assertEqual(self.api._limit({'limit': asked}, self.capability), 10, repr(asked))


class TestIdentityIsNeverAParameter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.api = cls.env['kmessage.api']
        cls.all = cls.env['kmessage.capability'].with_context(active_test=False).search([])
        cls.token, _raw = cls.env['kmessage.token'].issue('Test token', capabilities=cls.all)
        own_the_number(cls.env, CUSTOMER_MOBILE)
        cls.layla = cls.env['res.partner'].create({
            'name': 'Layla', 'mobile': CUSTOMER_MOBILE, 'city': 'Riyadh', 'lang': 'en_US'})

    def test_a_personal_request_without_a_number_is_refused(self):
        with self.assertRaises(KMessageApiError) as caught:
            self.api.dispatch('customer_lookup', {}, self.token)
        self.assertEqual(caught.exception.code, 'phone_required')
        self.assertEqual(caught.exception.status, 400)

    def test_a_number_nobody_here_has_is_a_404_not_a_guess(self):
        with self.assertRaises(KMessageApiError) as caught:
            self.api.dispatch('customer_lookup', {'phone': '966500000123'}, self.token)
        self.assertEqual(caught.exception.code, 'customer_not_found')
        self.assertEqual(caught.exception.status, 404)

    def test_a_known_number_answers_for_that_customer_only(self):
        answer = self.api.dispatch('customer_lookup', {'phone': '+966 50 738 6853'}, self.token)
        self.assertTrue(answer['found'])
        self.assertEqual(answer['name'], self.layla.display_name)
        self.assertEqual(answer['city'], 'Riyadh')
        self.assertEqual(answer['language'], 'en')
        self.assertFalse(answer['opted_out'])

    def test_the_number_may_also_arrive_as_phone_number(self):
        answer = self.api.dispatch('customer_lookup', {'phone_number': CUSTOMER_MOBILE}, self.token)
        self.assertEqual(answer['name'], self.layla.display_name)

    def test_a_partner_is_optional_only_where_the_handler_says_so(self):
        self.assertFalse(self.api._partner_from({}, required=False))
        self.assertEqual(self.api._partner_from({'phone': CUSTOMER_MOBILE}), self.layla)
