# -*- coding: utf-8 -*-
"""What this bridge must keep true.

Four things, and each is a promise made somewhere else in the module: a validated
delivery is what queues a message and an internal move is not, a quantity is a
band until somebody asks for the figure, a customer's deliveries are only ever
their own, and the assistant is handed the tools without ever being handed a
phone number to put in them.

Each of those has an edge the obvious test walks straight past, and those are
here too: validating twice, a wizard that validates nothing, a search term that
is really a wildcard, an argument the assistant never filled in, a delivery
number belonging to somebody else, and a subsidiary whose parent is on
WhatsApp. The PDF a delivery goes out as is checked here as well — the report
Odoo hands out first is a warehouse document, not the customer's.
"""

import json

from odoo.tests import TransactionCase, tagged

from odoo.addons.kmessage_connector.models.kmessage_api import KMessageApiError
from odoo.addons.kmessage_connector.tools.phone import match_key


@tagged('post_install', '-at_install')
class TestStockBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        cls.customer = cls.env['res.partner'].create({
            'name': 'Layla Al-Harbi',
            'phone': '+966501112233',
        })
        cls.other_customer = cls.env['res.partner'].create({
            'name': 'Faisal Nasser',
            'phone': '+966509998877',
        })
        # A group, a subsidiary of its own and one of the group's delivery
        # addresses: three partners, but only two customers.
        cls.group = cls.env['res.partner'].create({
            'name': 'Al-Harbi Group',
            'is_company': True,
            'phone': '+966505554433',
        })
        cls.group_address = cls.env['res.partner'].create({
            'name': 'Al-Harbi Group — back gate',
            'type': 'delivery',
            'parent_id': cls.group.id,
        })
        cls.subsidiary = cls.env['res.partner'].create({
            'name': 'Al-Harbi Motors',
            'is_company': True,
            'parent_id': cls.group.id,
        })

        cls.warehouse = cls.env.ref('stock.warehouse0')
        cls.corner_shop = cls.env['stock.warehouse'].create({
            'name': 'Corner Shop',
            'code': 'CSH',
        })

        cls.product = cls.env['product.product'].create(dict(
            {'name': 'Blue Widget', 'default_code': 'BW-1'},
            **cls._stockable()))
        cls.service = cls.env['product.product'].create({
            'name': 'Widget Fitting',
            'type': 'service',
        })

        quants = cls.env['stock.quant']
        quants._update_available_quantity(cls.product, cls.warehouse.lot_stock_id, 40)
        quants._update_available_quantity(cls.product, cls.corner_shop.lot_stock_id, 2)

        cls.stock_capability = cls.env.ref('kmessage_connector_stock.capability_stock_check')
        cls.delivery_capability = cls.env.ref('kmessage_connector_stock.capability_delivery_status')
        cls.token, _raw = cls.env['kmessage.token'].issue(
            name='test', company=cls.company,
            capabilities=cls.stock_capability | cls.delivery_capability)

    @classmethod
    def _stockable(cls):
        """The values that make a product one Odoo keeps a quantity for."""
        if 'is_storable' in cls.env['product.product']._fields:
            return {'is_storable': True}
        return {'type': 'product'}

    def _delivery_for(self, partner, warehouse=None, quantity=1):
        """A confirmed outgoing transfer of the test product."""
        warehouse = warehouse or self.warehouse
        picking = self.env['stock.picking'].create({
            'partner_id': partner.id,
            'picking_type_id': warehouse.out_type_id.id,
            'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'move_ids': [(0, 0, {
                'name': self.product.name,
                'product_id': self.product.id,
                'product_uom_qty': quantity,
                'product_uom': self.product.uom_id.id,
                'location_id': warehouse.lot_stock_id.id,
                'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            })],
        })
        picking.action_confirm()
        picking.action_assign()
        return picking

    def _connection(self):
        """This company's K-Message connection, live and ready to send.

        A company has exactly one, so the test uses whichever one the database
        already has rather than insisting on its own.
        """
        account = self.env['kmessage.account']._for_company(self.company)
        if not account:
            account = self.env['kmessage.account'].create({
                'name': 'Test connection',
                'base_url': 'https://kmessage.invalid',
                'api_key': 'secret',
                'company_id': self.company.id,
            })
        account.write({'state': 'connected', 'outbound_enabled': True})
        return account

    def _connected_automation(self):
        """A live rule that messages a customer when their delivery is validated."""
        account = self._connection()
        template = self.env['kmessage.template'].create({
            'account_id': account.id,
            'name': 'delivery_on_its_way',
            'language': 'en',
            'status': 'APPROVED',
            'body_content': 'Hello {{1}}, your delivery is on its way.',
        })
        return self.env['kmessage.automation'].create({
            'name': 'Delivery validated',
            'account_id': account.id,
            'trigger': 'stock.picking.done',
            'template_id': template.id,
            'attach_document': False,
            'cooldown_hours': 0,
            'active': True,
            'param_ids': [(0, 0, {'index': 1, 'source': 'field', 'field_path': 'partner_id.name'})],
        })

    # -- the trigger ------------------------------------------------------
    def test_validated_delivery_queues_a_message(self):
        self._connected_automation()

        picking = self._delivery_for(self.customer, quantity=3)
        picking.move_ids.write({'quantity': 3, 'picked': True})
        picking.button_validate()
        self.assertEqual(picking.state, 'done')

        message = self.env['kmessage.message'].search([
            ('res_model', '=', 'stock.picking'), ('res_id', '=', picking.id)])
        self.assertEqual(len(message), 1, "validating a delivery should queue exactly one message")
        self.assertEqual(message.partner_id, self.customer)
        self.assertEqual(message.state, 'queued')
        self.assertIn('Layla', message.params_json)

    def test_validating_twice_tells_the_customer_once(self):
        """A second press of Validate is not a second delivery.

        Odoo's own ``button_validate`` quietly drops the pickings that are
        already done, so the second press does nothing at all in the warehouse;
        the cooldown is switched off here so that the filter has to hold on its
        own rather than being propped up by it.
        """
        automation = self._connected_automation()
        automation.cooldown_hours = 0

        picking = self._delivery_for(self.customer, quantity=3)
        picking.move_ids.write({'quantity': 3, 'picked': True})
        picking.button_validate()
        picking.button_validate()

        self.assertEqual(len(self.env['kmessage.message'].search([
            ('res_model', '=', 'stock.picking'), ('res_id', '=', picking.id)])), 1)

    def test_a_backorder_question_is_not_a_delivery(self):
        """Part-validating opens a wizard, and nothing has left the warehouse yet."""
        automation = self._connected_automation()
        automation.cooldown_hours = 0

        picking = self._delivery_for(self.customer, quantity=5)
        picking.move_ids.write({'quantity': 2, 'picked': True})

        action = picking.button_validate()
        self.assertIsInstance(action, dict, "a part-validated transfer must ask about the backorder")
        self.assertNotEqual(picking.state, 'done')
        queued = [('res_model', '=', 'stock.picking'), ('res_id', '=', picking.id)]
        self.assertFalse(self.env['kmessage.message'].search(queued),
                         "a wizard on the screen is not a delivery on the road")

        wizard = self.env[action['res_model']].with_context(action['context']).create({})
        wizard.process()
        self.assertEqual(picking.state, 'done')
        self.assertEqual(len(self.env['kmessage.message'].search(queued)), 1)

    def test_internal_transfer_does_not_trigger(self):
        """Only what leaves the building is news to a customer."""
        self._connected_automation()

        picking = self.env['stock.picking'].create({
            'partner_id': self.customer.id,
            'picking_type_id': self.warehouse.int_type_id.id,
            'location_id': self.warehouse.lot_stock_id.id,
            'location_dest_id': self.warehouse.wh_input_stock_loc_id.id,
            'move_ids': [(0, 0, {
                'name': self.product.name,
                'product_id': self.product.id,
                'product_uom_qty': 1,
                'product_uom': self.product.uom_id.id,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.warehouse.wh_input_stock_loc_id.id,
            })],
        })
        picking.action_confirm()
        picking.action_assign()
        picking.move_ids.write({'quantity': 1, 'picked': True})
        picking.button_validate()

        self.assertFalse(self.env['kmessage.message'].search([
            ('res_model', '=', 'stock.picking'), ('res_id', '=', picking.id)]))

    # -- availability -----------------------------------------------------
    def test_quantities_are_banded_until_asked_for(self):
        self.stock_capability.show_exact_quantity = False
        answer = self.env['kmessage.api'].dispatch(
            'stock_check', {'product': 'Blue Widget'}, self.token)

        self.assertEqual(answer['product'], self.product.display_name)
        rows = {row['location']: row for row in answer['rows']}
        self.assertEqual(rows[self.warehouse.name]['level'], 'in stock')
        self.assertEqual(rows['Corner Shop']['level'], 'only a few left')
        for row in answer['rows']:
            self.assertNotIn('available', row, "the figure itself must stay in Odoo")

        self.stock_capability.show_exact_quantity = True
        answer = self.env['kmessage.api'].dispatch(
            'stock_check', {'product': 'BW-1'}, self.token)
        rows = {row['location']: row for row in answer['rows']}
        self.assertEqual(rows[self.warehouse.name]['available'], 40.0)
        self.assertEqual(rows['Corner Shop']['available'], 2.0)
        self.assertEqual(rows[self.warehouse.name]['level'], 'in stock')

    def test_reserved_stock_is_not_available(self):
        self.stock_capability.show_exact_quantity = True
        self._delivery_for(self.customer, quantity=38)

        answer = self.env['kmessage.api'].dispatch(
            'stock_check', {'product': 'Blue Widget', 'branch': self.warehouse.name}, self.token)
        self.assertEqual(len(answer['rows']), 1)
        self.assertEqual(answer['rows'][0]['available'], 2.0)
        self.assertEqual(answer['rows'][0]['level'], 'only a few left')

    def test_a_branch_with_none_left_says_so(self):
        answer = self.env['kmessage.api'].dispatch(
            'stock_check', {'product': 'Blue Widget', 'branch': 'CSH'}, self.token)
        self.assertEqual([row['location'] for row in answer['rows']], ['Corner Shop'])

        self.env['stock.quant']._update_available_quantity(
            self.product, self.corner_shop.lot_stock_id, -2)
        answer = self.env['kmessage.api'].dispatch(
            'stock_check', {'product': 'Blue Widget', 'branch': 'Corner'}, self.token)
        self.assertEqual(answer['rows'][0]['level'], 'out of stock')

    def test_the_figure_is_odoos_own_free_quantity(self):
        """Available means on hand less reserved, on either series."""
        self.stock_capability.show_exact_quantity = True
        self._delivery_for(self.customer, quantity=38)

        for warehouse in (self.warehouse, self.corner_shop):
            answer = self.env['kmessage.api'].dispatch(
                'stock_check', {'product': 'BW-1', 'branch': warehouse.code}, self.token)
            # `location` rather than `warehouse`: 17.0 and 18.0 spell the
            # warehouse context key differently, the location key the same.
            expected = self.product.with_context(
                location=warehouse.view_location_id.id).free_qty
            self.assertEqual(answer['rows'][0]['available'], expected)

    def test_an_over_delivered_branch_does_not_report_a_negative(self):
        """A shelf in the red is out of stock, not “minus three in stock”."""
        self.stock_capability.show_exact_quantity = True
        self.env['stock.quant']._update_available_quantity(
            self.product, self.corner_shop.lot_stock_id, -5)

        answer = self.env['kmessage.api'].dispatch(
            'stock_check', {'product': 'BW-1', 'branch': 'CSH'}, self.token)
        self.assertEqual(answer['rows'][0]['level'], 'out of stock')
        self.assertEqual(answer['rows'][0]['available'], 0.0)

    def test_a_wildcard_is_not_a_search_term(self):
        """What a customer types is text to match, never a pattern of its own."""
        self.stock_capability.show_exact_quantity = True
        for typed in ('%', '%Widget', 'Blue_Widget'):
            with self.assertRaises(KMessageApiError) as caught:
                self.env['kmessage.api'].dispatch(
                    'stock_check', {'product': typed}, self.token)
            self.assertEqual(caught.exception.code, 'product_not_found', typed)

        with self.assertRaises(KMessageApiError) as caught:
            self.env['kmessage.api'].dispatch(
                'stock_check', {'product': 'BW-1', 'branch': '%'}, self.token)
        self.assertEqual(caught.exception.code, 'branch_not_found')

    def test_an_argument_the_assistant_left_out_is_not_a_search_term(self):
        """K-Message sends the placeholder itself when an argument goes unfilled."""
        with self.assertRaises(KMessageApiError) as caught:
            self.env['kmessage.api'].dispatch(
                'stock_check', {'product': '{{args.product}}'}, self.token)
        self.assertEqual(caught.exception.code, 'product_required')

        answer = self.env['kmessage.api'].dispatch(
            'stock_check', {'product': 'BW-1', 'branch': '{{args.branch}}'}, self.token)
        self.assertLessEqual(
            {self.warehouse.name, 'Corner Shop'}, {row['location'] for row in answer['rows']},
            "an unfilled branch means every branch")

    def test_a_service_is_not_reported_as_out_of_stock(self):
        with self.assertRaises(KMessageApiError) as caught:
            self.env['kmessage.api'].dispatch(
                'stock_check', {'product': 'Widget Fitting'}, self.token)
        self.assertEqual(caught.exception.code, 'product_not_stocked')

    # -- deliveries -------------------------------------------------------
    def test_delivery_status_answers_only_for_the_asking_customer(self):
        mine = self._delivery_for(self.customer)
        theirs = self._delivery_for(self.other_customer)

        answer = self.env['kmessage.api'].dispatch(
            'delivery_status', {'phone': '+966501112233'}, self.token)
        references = [row['reference'] for row in answer['rows']]
        self.assertIn(mine.name, references)
        self.assertNotIn(theirs.name, references)

        answer = self.env['kmessage.api'].dispatch(
            'delivery_status', {'phone': '0509998877'}, self.token)
        references = [row['reference'] for row in answer['rows']]
        self.assertEqual(references, [theirs.name])
        self.assertEqual(answer['rows'][0]['state'], 'packed and ready')

    def test_a_delivery_number_that_is_not_theirs_is_not_found(self):
        """Somebody else's delivery number is refused exactly like a made-up one."""
        mine = self._delivery_for(self.customer)
        theirs = self._delivery_for(self.other_customer)

        answer = self.env['kmessage.api'].dispatch(
            'delivery_status', {'phone': '+966501112233', 'reference': mine.name}, self.token)
        self.assertEqual([row['reference'] for row in answer['rows']], [mine.name])

        for quoted in (theirs.name, 'WH/OUT/NOPE', '%'):
            with self.assertRaises(KMessageApiError) as caught:
                self.env['kmessage.api'].dispatch(
                    'delivery_status',
                    {'phone': '+966501112233', 'reference': quoted}, self.token)
            self.assertEqual(caught.exception.code, 'delivery_not_found', quoted)
            self.assertEqual(caught.exception.status, 404)

    def test_a_subsidiarys_delivery_is_not_its_parents(self):
        """A company under a company is a customer of its own.

        Odoo gives it its own commercial entity, so its parcels are no more
        the parent group's than a stranger's are. ``child_of`` cannot tell the
        two apart: it reads correctly on the flat pair of partners a test
        usually has, and hands the group every subsidiary's deliveries the
        moment somebody models the group the way Odoo intends.
        """
        ours = self._delivery_for(self.group_address)
        theirs = self._delivery_for(self.subsidiary)

        answer = self.env['kmessage.api'].dispatch(
            'delivery_status', {'phone': '+966505554433'}, self.token)
        references = [row['reference'] for row in answer['rows']]
        self.assertIn(ours.name, references,
                      "a delivery address below the group is still the group")
        self.assertNotIn(theirs.name, references)

        with self.assertRaises(KMessageApiError) as caught:
            self.env['kmessage.api'].dispatch(
                'delivery_status',
                {'phone': '+966505554433', 'reference': theirs.name}, self.token)
        self.assertEqual(caught.exception.code, 'delivery_not_found')

    def test_an_unfilled_reference_still_lists_their_deliveries(self):
        """“Where is my delivery” must not become “where is delivery {{args}}”."""
        mine = self._delivery_for(self.customer)

        answer = self.env['kmessage.api'].dispatch(
            'delivery_status',
            {'phone': '+966501112233', 'reference': '{{args.reference}}'}, self.token)
        self.assertIn(mine.name, [row['reference'] for row in answer['rows']])

    def test_delivery_status_needs_a_number_it_knows(self):
        with self.assertRaises(KMessageApiError) as caught:
            self.env['kmessage.api'].dispatch('delivery_status', {}, self.token)
        self.assertEqual(caught.exception.code, 'phone_required')

        # A number nobody has: proven, not assumed. A neat literal is exactly
        # the sort of number a demo record turns out to be using.
        stranger = '+96650%s' % '9' * 7
        self.assertFalse(
            self.env['res.partner'].search(
                [('kmessage_phone_key', '=', match_key(stranger))], limit=1),
            'this test needs a number no partner has')
        with self.assertRaises(KMessageApiError) as caught:
            self.env['kmessage.api'].dispatch(
                'delivery_status', {'phone': stranger}, self.token)
        self.assertEqual(caught.exception.code, 'customer_not_found')

    # -- the PDF that goes with it ----------------------------------------
    def test_a_delivery_is_sent_as_the_delivery_slip(self):
        """Not the Reception Report, which is what Odoo offers first.

        A stock database carries several printouts for a transfer and the
        first of them is written for the loading bay. Picking whichever one
        loaded first sends the customer a warehouse document.
        """
        picking = self._delivery_for(self.customer)
        report = self.env['kmessage.document']._report_for(picking)
        self.assertEqual(report, self.env.ref('stock.action_report_delivery'))

        # A rule that names a report still gets the one it asked for.
        named = self.env['kmessage.document']._report_for(
            picking, report_name='stock.report_picking')
        self.assertEqual(named.report_name, 'stock.report_picking')

    # -- what the assistant is offered ------------------------------------
    def test_a_connection_is_offered_both_tools(self):
        account = self._connection()
        tools = self.env['kmessage.ai.tool']
        mine = [('account_id', '=', account.id), ('tool_name', 'in', ('odoo_stock', 'odoo_delivery'))]
        tools.search(mine).unlink()

        tools._kmessage_seed_stock_tools()
        offered = tools.search(mine)
        self.assertEqual(set(offered.mapped('tool_name')), {'odoo_stock', 'odoo_delivery'})
        self.assertEqual(set(offered.mapped('state')), {'draft'},
                         "a tool reaches K-Message only when somebody publishes it")

        tools._kmessage_seed_stock_tools()
        self.assertEqual(len(tools.search(mine)), 2, "seeding twice must not double the tools")

        for tool in offered:
            names = {param['name'] for param in json.loads(tool.params_json)}
            self.assertNotIn('phone', names, "identity comes from the conversation, never the model")
            self.assertLessEqual(len(json.loads(tool.fields_json)), 8)
