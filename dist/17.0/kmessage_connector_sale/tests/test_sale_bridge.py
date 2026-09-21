# -*- coding: utf-8 -*-
"""What must stay true of the sales bridge.

Most of these are about not telling the wrong thing to the wrong person: a
confirmation is announced exactly once, a customer's orders are the only orders
their number can reach — not another company's, not another group's — and
somebody else's order number is a dead end rather than a hint.

The rest are about the two ways this bridge is actually called. K-Message fills
a body template, so an argument the assistant left out arrives as its own
placeholder and must read as "no argument"; and an order must confirm even when
every rule watching it is broken.
"""

import json

from contextlib import contextmanager
from unittest.mock import patch

from odoo.addons.kmessage_connector.models.kmessage_api import KMessageApiError
from odoo.addons.kmessage_connector.models.kmessage_automation import KMessageAutomation
from odoo.tests.common import TransactionCase, tagged

from ..models.sale_order import TRIGGER_CONFIRMED, TRIGGER_QUOTATION_SENT


@tagged('post_install', '-at_install')
class TestSaleBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Folding desk',
            'type': 'consu',
        })
        # Two customers whose numbers share no tail, so a match is a match for
        # the right reason and not because the key happened to collide.
        cls.her = cls.env['res.partner'].create({
            'name': 'Salma Trading', 'mobile': '+966 50 123 4567',
        })
        cls.him = cls.env['res.partner'].create({
            'name': 'Faisal Workshops', 'mobile': '+966 50 987 6543',
        })
        cls.her_phone = '0501234567'
        cls.her_order = cls._order_for(cls.her)
        cls.his_order = cls._order_for(cls.him)

        cls.orders = cls.env.ref('kmessage_connector_sale.capability_orders')
        cls.order_status = cls.env.ref('kmessage_connector_sale.capability_order_status')
        cls.api = cls.env['kmessage.api']

    @classmethod
    def _order_for(cls, partner, company=None, state='sent'):
        """An order the customer may legitimately be told about.

        Defaults to ``sent`` rather than ``draft`` because that is the line
        Odoo itself draws: its customer portal lists quotations with
        ``('state', '=', 'sent')``, and a draft is the salesperson's working
        copy. A test that wants the other side of that line asks for it.
        """
        orders = cls.env['sale.order']
        if company:
            orders = orders.with_company(company)
        order = orders.create({
            'partner_id': partner.id,
            'company_id': (company or cls.env.company).id,
            'order_line': [(0, 0, {'product_id': cls.product.id, 'product_uom_qty': 2})],
        })
        if state != 'draft':
            order.state = state
        return order

    @contextmanager
    def _recording_triggers(self):
        """Collect what would have been announced, without any automation."""
        fired = []

        def record(self, trigger, records, params_builder=None):
            fired.append((trigger, records))

        with patch.object(KMessageAutomation, 'run_trigger', record):
            yield fired

    def test_confirming_announces_the_order_once(self):
        with self._recording_triggers() as fired:
            self.her_order.action_confirm()

        confirmations = [records for trigger, records in fired if trigger == TRIGGER_CONFIRMED]
        self.assertEqual(len(confirmations), 1, "one confirmation, one announcement")
        self.assertEqual(confirmations[0], self.her_order)

    def test_orders_are_only_the_caller_s_own(self):
        rows = self.api._handle_orders({'phone': self.her_phone}, self.orders)['rows']

        self.assertEqual([row['number'] for row in rows], [self.her_order.name])
        self.assertEqual(rows[0]['state'], 'quotation')

    def test_her_own_order_is_found_by_number(self):
        answer = self.api._handle_order_status(
            {'phone': self.her_phone, 'order': self.her_order.name}, self.order_status)

        self.assertEqual(answer['rows'][0]['number'], self.her_order.name)
        self.assertEqual(answer['rows'][0]['lines'], [{'product': 'Folding desk', 'quantity': 2.0}])

    def test_another_customer_s_order_is_not_found(self):
        with self.assertRaises(KMessageApiError) as refusal:
            self.api._handle_order_status(
                {'phone': self.her_phone, 'order': self.his_order.name}, self.order_status)

        self.assertEqual(refusal.exception.status, 404)
        self.assertEqual(refusal.exception.code, 'order_not_found')

    def test_a_new_connection_is_given_the_sales_tools(self):
        """The tools follow the connection, whenever it is made."""
        # A company of its own: one Odoo company holds one connection, and the
        # database this runs against may well already have one.
        company = self.env['res.company'].create({'name': 'A second branch'})
        account = self.env['kmessage.account'].create({
            'name': 'K-Message',
            'company_id': company.id,
            'base_url': 'https://k-message.example',
            'api_key': 'not-a-real-key',
        })
        # The other bridges draft their own tools on the same connection.
        tools = self.env['kmessage.ai.tool'].search([
            ('account_id', '=', account.id),
            ('tool_name', 'in', ['odoo_orders', 'odoo_order_status']),
        ])

        self.assertEqual(sorted(tools.mapped('tool_name')),
                         ['odoo_order_status', 'odoo_orders'])
        self.assertEqual(set(tools.mapped('state')), {'draft'}, "published by hand, never on install")
        for tool in tools:
            columns = json.loads(tool.fields_json)
            self.assertTrue(columns, "%s shows the assistant nothing" % tool.tool_name)
            self.assertLessEqual(len(columns), 8, "K-Message shows at most eight columns")
            self.assertNotIn(
                'phone', [param['name'] for param in json.loads(tool.params_json)],
                "identity comes from the conversation, never from the assistant")

    def test_a_wildcard_is_not_a_search(self):
        """“%” is a character in a number, never a pattern to match on."""
        with self.assertRaises(KMessageApiError) as refusal:
            self.api._handle_order_status(
                {'phone': self.her_phone, 'order': '%'}, self.order_status)

        self.assertEqual(refusal.exception.status, 404)

    # -- the arguments K-Message really sends -----------------------------
    def test_an_unfilled_state_is_not_a_filter(self):
        """An optional argument the assistant skipped must not refuse the call.

        K-Message leaves ``{{args.state}}`` in the body when nothing was passed,
        which is every ordinary “what have I ordered?” — read as a state it
        would answer with a 400 instead of the customer's orders.
        """
        rows = self.api._handle_orders(
            {'phone': self.her_phone, 'state': '{{args.state}}', 'limit': '{{args.limit}}'},
            self.orders)['rows']

        self.assertEqual([row['number'] for row in rows], [self.her_order.name])

    def test_a_state_nobody_offers_is_still_refused(self):
        with self.assertRaises(KMessageApiError) as refusal:
            self.api._handle_orders({'phone': self.her_phone, 'state': 'shipped'}, self.orders)

        self.assertEqual(refusal.exception.code, 'bad_state')
        self.assertEqual(refusal.exception.status, 400)

    def test_an_unfilled_order_number_is_asked_for(self):
        """Not naming an order is a question to put back, not a missing order."""
        with self.assertRaises(KMessageApiError) as refusal:
            self.api._handle_order_status(
                {'phone': self.her_phone, 'order': '{{args.order}}'}, self.order_status)

        self.assertEqual(refusal.exception.code, 'order_required')
        self.assertEqual(refusal.exception.status, 400)

    # -- whose orders these are -------------------------------------------
    def test_a_contact_asks_for_the_company_s_orders(self):
        """The buyer on WhatsApp is asking about their employer's order."""
        firm = self.env['res.partner'].create({
            'name': 'Hadeel Supplies', 'is_company': True, 'mobile': '+966 55 400 1100',
        })
        self.env['res.partner'].create({
            'name': 'Hadeel buyer', 'parent_id': firm.id, 'mobile': '+966 55 400 2200',
        })
        order = self._order_for(firm)

        rows = self.api._handle_orders({'phone': '0554002200'}, self.orders)['rows']

        self.assertEqual([row['number'] for row in rows], [order.name])

    def test_another_company_s_orders_are_out_of_reach(self):
        """One customer, two of our companies: only the asked one answers."""
        elsewhere = self.env['res.company'].create({'name': 'A branch of our own'})
        theirs = self._order_for(self.her, company=elsewhere)

        rows = self.api._handle_orders({'phone': self.her_phone}, self.orders)['rows']

        self.assertEqual([row['number'] for row in rows], [self.her_order.name])
        self.assertNotIn(theirs.name, [row['number'] for row in rows])

        with self.assertRaises(KMessageApiError) as refusal:
            self.api._handle_order_status(
                {'phone': self.her_phone, 'order': theirs.name}, self.order_status)
        self.assertEqual(refusal.exception.status, 404)

    def test_the_rows_say_nothing_about_our_side_of_the_deal(self):
        """What she agreed to buy, and not a figure more."""
        self.her_order.order_line.create({
            'order_id': self.her_order.id,
            'display_type': 'line_note',
            'name': 'Internal: chase the supplier before promising a date',
        })

        row = self.api._handle_order_status(
            {'phone': self.her_phone, 'order': self.her_order.name}, self.order_status)['rows'][0]

        self.assertEqual(row['lines'], [{'product': 'Folding desk', 'quantity': 2.0}])
        self.assertEqual(row['line_count'], 1, "a note is not something she ordered")
        for secret in ('margin', 'purchase_price', 'note', 'user_id', 'price_unit', 'cost'):
            self.assertNotIn(secret, row)

    # -- the triggers ------------------------------------------------------
    def test_marking_a_quotation_sent_announces_it_once(self):
        # Its own draft: the shared fixture is already sent, and Odoo refuses
        # to send a quotation twice.
        draft = self._order_for(self.her, state='draft')
        with self._recording_triggers() as fired:
            draft.action_quotation_sent()
            # Already sent: saying so again is not the customer receiving it again.
            draft.write({'state': 'sent'})

        sendings = [records for trigger, records in fired if trigger == TRIGGER_QUOTATION_SENT]
        self.assertEqual(len(sendings), 1, "one quotation reaching her, one announcement")
        self.assertEqual(sendings[0], draft)

    def test_confirming_is_not_a_quotation_being_sent(self):
        with self._recording_triggers() as fired:
            self.her_order.action_confirm()

        self.assertEqual([trigger for trigger, _records in fired], [TRIGGER_CONFIRMED])

    def test_a_broken_rule_does_not_stop_the_sale(self):
        """The whole point of the bridge: WhatsApp cannot cost us an order."""
        connection = self.env['kmessage.account']._for_company(self.env.company)
        if not connection:
            connection = self.env['kmessage.account'].create({
                'name': 'K-Message', 'company_id': self.env.company.id,
                'base_url': 'https://k-message.example', 'api_key': 'not-a-real-key',
            })
        connection.write({'state': 'connected', 'outbound_enabled': True})
        template = self.env['kmessage.template'].create({
            'account_id': connection.id,
            'name': 'order_confirmed',
            'language': 'en',
            'status': 'APPROVED',
            'header_type': 'TEXT',
            'body_content': 'Hello {{1}}, your order is confirmed.',
        })
        self.env['kmessage.automation'].create({
            'name': 'Broken rule',
            'account_id': connection.id,
            'trigger': TRIGGER_CONFIRMED,
            'template_id': template.id,
            'attach_document': False,
            'active': True,
            'filter_domain': "[('no_such_field', '=', 1)]",
        })

        self.her_order.action_confirm()

        self.assertEqual(self.her_order.state, 'sale')

    # -- the delivery hint -------------------------------------------------
    def test_the_delivery_hint_needs_no_warehouse(self):
        """A company selling services answers without installing stock."""
        self.assertNotIn(
            'stock', self.env['ir.module.module'].get_module_info(
                'kmessage_connector_sale').get('depends', []))

        order = self.her_order
        with patch.dict(type(order)._fields):
            type(order)._fields.pop('delivery_status', None)
            row = self.api._order_row(order)

        self.assertEqual(row['delivery'], '')
        self.assertEqual(row['number'], order.name)


    def test_a_parent_company_is_not_shown_its_subsidiary_s_orders(self):
        """A company filed under another is a customer of its own.

        Odoo gives a child *company* its own commercial entity and its own
        ledger, so the only thing a `child_of` scope would achieve here is
        handing a parent group every subsidiary's orders.
        """
        parent = self.env['res.partner'].create({
            'name': 'Group HQ', 'is_company': True, 'mobile': '0551110001'})
        subsidiary = self.env['res.partner'].create({
            'name': 'Group Subsidiary', 'is_company': True, 'parent_id': parent.id})
        theirs = self.env['sale.order'].create({'partner_id': subsidiary.id})

        rows = self.api._handle_orders({'phone': '0551110001'}, self.orders)['rows']
        self.assertNotIn(theirs.name, [row['number'] for row in rows])

    def test_a_draft_quotation_is_not_the_customer_s_business(self):
        """Priced but never offered: reading it out commits the company."""
        draft = self.env['sale.order'].create({'partner_id': self.her.id})
        self.assertEqual(draft.state, 'draft')

        rows = self.api._handle_orders({'phone': self.her_phone}, self.orders)['rows']
        self.assertNotIn(draft.name, [row['number'] for row in rows])

        with self.assertRaises(KMessageApiError):
            self.api._handle_order_status(
                {'phone': self.her_phone, 'order': draft.name}, self.order_status)
