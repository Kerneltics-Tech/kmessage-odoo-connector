# -*- coding: utf-8 -*-
"""The number a customer is saved under is almost never the number WhatsApp sends.

Every shape below is one that actually arrives: typed with a plus, dialled with
a double zero, saved with the trunk zero a Saudi keypad puts there, or pasted
with the spaces intact. They all have to end up comparable, and the short and
empty ones have to end up comparable to nothing at all.
"""

from odoo.tests.common import TransactionCase

from odoo.addons.kmessage_connector.tools.phone import (
    MATCH_DIGITS, digits_only, match_key, normalize, same_number,
)

from .common import own_the_number


class TestPhoneNormalising(TransactionCase):

    def test_digits_only_keeps_the_digits_in_order(self):
        self.assertEqual(digits_only('+966 51 234-5678'), '966512345678')
        self.assertEqual(digits_only(966512345678), '966512345678')
        self.assertEqual(digits_only(''), '')
        self.assertEqual(digits_only(None), '')
        self.assertEqual(digits_only('ext. 4'), '4')

    def test_one_saudi_number_in_every_shape_it_arrives_in(self):
        for written in ('+966 51 234 5678', '00966512345678', '966512345678',
                        '0512345678', '051-234-5678', ' 0512345678 ',
                        '+966-51-2345678'):
            self.assertEqual(normalize(written, '966'), '966512345678', written)

    def test_a_number_that_carries_its_own_country_code_is_left_alone(self):
        """A UAE mobile in a Saudi company's database is still a UAE mobile."""
        self.assertEqual(normalize('+971 50 123 4567', '966'), '971501234567')
        self.assertEqual(normalize('0097455123456', '966'), '97455123456')

    def test_a_landline_keeps_its_area_code(self):
        # 011 is Riyadh, not the international prefix some countries dial.
        self.assertEqual(normalize('011 234 5678', '966'), '966112345678')

    def test_the_international_prefix_only_goes_when_what_is_left_is_long_enough(self):
        self.assertEqual(normalize('011966512345678', '966'), '966512345678')

    def test_the_country_code_is_only_used_when_one_is_given(self):
        self.assertEqual(normalize('0512345678', None), '0512345678')
        self.assertEqual(normalize('0512345678', ''), '0512345678')
        self.assertEqual(normalize('0512345678', '+966'), '966512345678')

    def test_what_cannot_be_made_sense_of_comes_back_empty_rather_than_raising(self):
        """Bad data in a partner record must never be able to break a send."""
        self.assertEqual(normalize('', '966'), '')
        self.assertEqual(normalize(None, '966'), '')
        self.assertEqual(normalize('n/a', '966'), '')
        self.assertEqual(normalize('   ', '966'), '')


class TestPhoneMatching(TransactionCase):

    def test_every_shape_of_one_number_has_the_same_key(self):
        keys = {
            match_key(written)
            for written in ('+966 51 234 5678', '0512345678', '966512345678',
                            '00966512345678', '966 51 234 5678')
        }
        self.assertEqual(keys, {'512345678'})
        self.assertEqual(len(keys.pop()), MATCH_DIGITS)

    def test_a_number_too_short_to_identify_a_person_has_no_key(self):
        self.assertEqual(match_key(''), '')
        self.assertEqual(match_key(None), '')
        self.assertEqual(match_key('12345678'), '')
        self.assertEqual(match_key('123456789'), '123456789')

    def test_an_empty_key_matches_nobody(self):
        """The whole reason match_key returns '' instead of a short string."""
        self.assertFalse(same_number('', ''))
        self.assertFalse(same_number('', '966512345678'))
        self.assertFalse(same_number(None, '0512345678'))
        self.assertFalse(same_number('1234', '966512345678'))

    def test_the_two_mistakes_that_actually_happen_still_match(self):
        self.assertTrue(same_number('0512345678', '+966 51 234 5678'))
        self.assertTrue(same_number('966512345678', '0512345678'))

    def test_two_different_people_do_not_match(self):
        self.assertFalse(same_number('0512345678', '0512345679'))


class TestPartnerLookup(TransactionCase):
    """The promise the inbound API rests on: a number finds one person or none."""

    def setUp(self):
        super().setUp()
        own_the_number(self.env, '0512345678', '0501112233')
        self.partners = self.env['res.partner']

    def _partner(self, name, mobile, **values):
        partner = self.env['res.partner'].create(dict(values, name=name, mobile=mobile))
        self.partners |= partner
        return partner

    def test_a_customer_is_found_however_their_number_was_typed(self):
        layla = self._partner('Layla', '0512345678')
        for asked in ('966512345678', '+966 51 234 5678', '00966512345678', '0512345678'):
            self.assertEqual(
                self.env['res.partner']._kmessage_find_by_phone(asked, self.env.company),
                layla, asked)

    def test_the_stored_key_is_what_the_search_uses(self):
        layla = self._partner('Layla', '+966 51 234 5678')
        self.assertEqual(layla.kmessage_phone_key, '512345678')
        layla.mobile = '0501112233'
        self.assertEqual(layla.kmessage_phone_key, '501112233')

    def test_a_partner_with_no_number_has_no_key(self):
        nobody = self.env['res.partner'].create({'name': 'No number'})
        self.assertFalse(nobody.kmessage_phone_key)

    def test_an_empty_number_finds_nobody(self):
        self._partner('Layla', '0512345678')
        for asked in ('', None, '12345'):
            self.assertFalse(
                self.env['res.partner']._kmessage_find_by_phone(asked, self.env.company), asked)

    def test_a_number_two_unrelated_people_share_finds_neither(self):
        """Answering with the wrong person's invoices is worse than answering nothing."""
        self._partner('Layla', '0512345678')
        self._partner('Nora', '966512345678')
        self.assertFalse(
            self.env['res.partner']._kmessage_find_by_phone('0512345678', self.env.company))

    def test_a_number_shared_within_one_company_finds_the_company(self):
        company = self._partner('Al Noor Trading', '0512345678', is_company=True)
        self._partner('Layla', '0512345678', parent_id=company.id)
        self.assertEqual(
            self.env['res.partner']._kmessage_find_by_phone('0512345678', self.env.company),
            company)

    def test_the_number_to_message_a_partner_on_is_international(self):
        layla = self._partner('Layla', '0512345678')
        self.env.company.country_id = self.env.ref('base.sa')
        self.assertEqual(layla._kmessage_number(), '966512345678')
