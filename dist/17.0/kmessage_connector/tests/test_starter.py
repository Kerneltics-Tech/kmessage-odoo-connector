# -*- coding: utf-8 -*-
"""Writing the templates a company would otherwise write by hand.

The promise being tested is the one a customer notices: install the addon,
paste a key, and the messages this thing sends already exist and are on their
way through Meta's review — with no one having read a word about template
categories or sample values.

The awkward half is what happens when that does not work. A tenant whose plan
forbids it, a name Meta has seen before, a body Meta's rules refuse: each is
one line in the connect summary and none of them is allowed to cost the
customer the rest of their setup.
"""

from odoo.tests import tagged

from .common import KMessageCase


@tagged('post_install', '-at_install')
class TestStarter(KMessageCase):

    def starter(self):
        return self.env['kmessage.starter']

    def catalogue_of(self, *entries):
        """Run the provisioning against a catalogue written for one test."""
        self.patch(type(self.starter()), 'catalogue', lambda _self: list(entries))
        return self.starter().provision(self.account)

    def entry(self, **values):
        defaults = {
            'name': 'odoo_test_template',
            'body': 'مرحباً {{1}}، فاتورتك رقم {{2}} جاهزة.',
            'samples': ['عبدالله', 'INV/2026/0001'],
        }
        defaults.update(values)
        return self.starter()._entry(**defaults)

    def drafts(self):
        return self.fake.module.STATE.drafts

    # -- the promise ------------------------------------------------------
    def test_a_template_is_written_and_submitted(self):
        outcomes = self.catalogue_of(self.entry(header_type=None))
        self.assertEqual(len(outcomes), 1)
        name, outcome = outcomes[0]
        self.assertEqual(name, 'odoo_test_template')
        self.assertIn('Meta', outcome)
        self.assertEqual(self.drafts()['odoo_test_template']['status'], 'PENDING')

    def test_a_document_template_is_shown_a_sample_first(self):
        """Meta refuses to review a document header it has not been shown."""
        outcomes = self.catalogue_of(self.entry(header_type='DOCUMENT'))
        self.assertIn('Meta', outcomes[0][1])
        self.assertEqual(self.drafts()['odoo_test_template']['status'], 'PENDING')
        self.assertTrue(self.fake.module.STATE.uploads, 'a sample should have been uploaded')
        uploaded = self.fake.module.STATE.uploads[-1]
        self.assertTrue(uploaded['looks_like_pdf'], 'the sample must really be a PDF')

    def test_what_is_already_there_is_left_alone(self):
        """Second time round changes nothing — including a hand-edited one."""
        self.catalogue_of(self.entry(header_type=None))
        before = dict(self.drafts()['odoo_test_template'])
        outcomes = self.catalogue_of(self.entry(header_type=None))
        self.assertEqual(outcomes[0][1], 'already there')
        self.assertEqual(self.drafts()['odoo_test_template'], before)

    def test_the_templates_are_arabic_and_utility(self):
        """What ships, not what a test made up: the real catalogue."""
        entries = self.starter().catalogue()
        for entry in entries:
            self.assertEqual(entry['language'], 'ar', entry['name'])
            self.assertEqual(entry['category'], 'UTILITY', entry['name'])
            self.assertTrue(entry['samples'], '%s needs sample values' % entry['name'])

    def test_no_shipped_body_starts_or_ends_on_a_placeholder(self):
        """Meta refuses those outright, and the refusal is only seen live."""
        for entry in self.starter().catalogue():
            body = entry['body'].strip()
            self.assertFalse(body.startswith('{{'), '%s starts on a placeholder' % entry['name'])
            self.assertNotRegex(
                body, r'\{\{\d+\}\}[\s.،]*$', '%s ends on a placeholder' % entry['name'])

    # -- when it cannot be done -------------------------------------------
    def test_a_refusal_is_a_sentence_not_a_failure(self):
        """A name the platform will not take must not raise."""
        self.catalogue_of(self.entry(header_type=None))
        # The fake refuses a duplicate name the way the real one does, and the
        # connector only asks again because a hand-deleted template is gone
        # from the list while Meta still holds the name.
        self.patch(type(self.starter()), '_existing_names', lambda _self, _client: set())
        outcomes = self.catalogue_of(self.entry(header_type=None))
        self.assertIn('refused', outcomes[0][1].lower())

    def test_a_tenant_with_no_catalogue_is_not_an_error(self):
        self.assertEqual(self.catalogue_of(), [])
