# -*- coding: utf-8 -*-
"""Importing the ready-made trees.

The flows used to be three manual steps, two of which were a secret copied by
hand into a form. Doing it for the customer is only an improvement if the
copy that lands is a working one — with this Odoo's address and its token in
it — and if it lands switched off, because what a company's WhatsApp says
back is theirs to decide.
"""

import json

from odoo.tests import tagged

from .common import KMessageCase


@tagged('post_install', '-at_install')
class TestFlowImport(KMessageCase):

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param(
            'web.base.url', 'https://odoo.example.com')

    def test_the_shipped_graphs_are_readable(self):
        graphs = self.env['kmessage.flow'].shipped()
        self.assertTrue(graphs, 'the addon ships flow graphs')
        for graph in graphs:
            self.assertTrue(graph.get('name'))
            self.assertIn('graph', graph)

    def test_importing_fills_in_the_address_and_the_token(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'web.base.url', 'https://books.example.com')
        made, _skipped, failed = self.env['kmessage.flow'].import_all(
            self.account, token='kmc_a_real_one')
        self.assertTrue(made, failed)
        self.assertFalse(failed)

        imported = list(self.fake.module.STATE.flows.values())
        self.assertEqual(len(imported), len(made))
        body = json.dumps(imported)
        self.assertIn('https://books.example.com/kmessage/api/v1/', body)
        self.assertIn('Bearer kmc_a_real_one', body)
        self.assertNotIn('odoo.example.com', body)
        self.assertNotIn('REPLACE_ME', body)

    def test_they_arrive_switched_off(self):
        self.env['kmessage.flow'].import_all(self.account, token='kmc_a_real_one')
        for flow in self.fake.module.STATE.flows.values():
            self.assertFalse(
                flow.get('enabled'),
                'what a company answers customers with is theirs to switch on')

    def test_importing_twice_leaves_one_of_each(self):
        self.env['kmessage.flow'].import_all(self.account, token='kmc_one')
        first = len(self.fake.module.STATE.flows)
        made, skipped, _failed = self.env['kmessage.flow'].import_all(
            self.account, token='kmc_two')
        self.assertFalse(made)
        self.assertTrue(skipped)
        self.assertEqual(len(self.fake.module.STATE.flows), first)

    def test_a_flow_somebody_edited_is_not_replaced(self):
        """Matching by name is the whole guard: same name, hands off."""
        self.env['kmessage.flow'].import_all(self.account, token='kmc_one')
        theirs = list(self.fake.module.STATE.flows.values())[0]
        theirs['description'] = 'We rewrote this'

        self.env['kmessage.flow'].import_all(self.account, token='kmc_two')
        again = [f for f in self.fake.module.STATE.flows.values()
                 if f['name'] == theirs['name']]
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]['description'], 'We rewrote this')
