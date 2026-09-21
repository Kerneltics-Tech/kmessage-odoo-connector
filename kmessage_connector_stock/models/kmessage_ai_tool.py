# -*- coding: utf-8 -*-
"""The two inventory questions, offered to the K-Message assistant.

These cannot be plain XML records: every tool hangs off a connection, and at
install time there is usually no connection at all — the bridge installs itself
beside Inventory, long before anybody pastes a K-Message token. So the tools are
described here and created for whichever connections exist, on install and again
whenever one is made. They arrive as drafts; nothing reaches K-Message until
somebody presses Publish.

Neither tool takes a phone number. K-Message fills that in from the conversation
itself, and a tool that let the assistant pass one would be a way to ask after
somebody else's parcel.
"""

import json

from odoo import _, api, models


class KMessageAiTool(models.Model):
    _inherit = 'kmessage.ai.tool'

    @api.model
    def _kmessage_stock_tools(self):
        """This bridge's tools, as values less the connection they hang on."""
        stock_check = self.env.ref(
            'kmessage_connector_stock.capability_stock_check', raise_if_not_found=False)
        delivery_status = self.env.ref(
            'kmessage_connector_stock.capability_delivery_status', raise_if_not_found=False)

        tools = []
        if stock_check:
            tools.append({
                'name': _("Stock availability"),
                'tool_name': 'odoo_stock',
                'capability_id': stock_check.id,
                'kind': 'lookup_tool',
                'description': _(
                    "Use this when somebody asks whether a product is available, or "
                    "available at a particular branch. It answers per branch, and it "
                    "answers how many are left only where the shop has chosen to say so — "
                    "when it gives a band such as “only a few left”, say the band and do "
                    "not invent a number."),
                'params_json': json.dumps([
                    {
                        'name': 'product',
                        'type': 'string',
                        'required': True,
                        'description': _("The product as the customer named it, or its code."),
                    },
                    {
                        'name': 'branch',
                        'type': 'string',
                        'required': False,
                        'description': _("The branch or shop they asked about. Leave it "
                                         "out for every branch."),
                    },
                ], ensure_ascii=False),
                'fields_json': json.dumps([
                    {'name': 'location', 'label': _("Branch")},
                    {'name': 'level', 'label': _("Availability")},
                    {'name': 'available', 'label': _("Quantity")},
                ], ensure_ascii=False),
                'max_rows': 5,
                'empty_message': _("I could not find that product in our stock."),
                'sequence': 40,
            })
        if delivery_status:
            tools.append({
                'name': _("Delivery status"),
                'tool_name': 'odoo_delivery',
                'capability_id': delivery_status.id,
                'kind': 'lookup_tool',
                'description': _(
                    "Use this when somebody asks where their order or delivery has got to. "
                    "It returns only this customer's own deliveries, newest first. If it "
                    "returns nothing, say we cannot see a delivery for them yet rather than "
                    "guessing at one."),
                'params_json': json.dumps([
                    {
                        'name': 'reference',
                        'type': 'string',
                        'required': False,
                        'description': _("A delivery or order number, if they quoted one."),
                    },
                ], ensure_ascii=False),
                'fields_json': json.dumps([
                    {'name': 'reference', 'label': _("Delivery")},
                    {'name': 'date', 'label': _("Date")},
                    {'name': 'state', 'label': _("Status")},
                    {'name': 'carrier', 'label': _("Carrier")},
                    {'name': 'tracking', 'label': _("Tracking")},
                ], ensure_ascii=False),
                'max_rows': 3,
                'empty_message': _("I cannot see a delivery for you yet."),
                'sequence': 41,
            })
        return tools

    @api.model
    def _kmessage_seed_stock_tools(self, accounts=None):
        """Draft this bridge's tools on connections that do not have them.

        Idempotent by tool name, so an upgrade repairs a missing row and leaves
        an edited one exactly as the customer left it.
        """
        tools = self.sudo()
        if accounts is None:
            accounts = self.env['kmessage.account'].sudo().search([])
        for account in accounts:
            for values in self._kmessage_stock_tools():
                existing = tools.with_context(active_test=False).search([
                    ('account_id', '=', account.id),
                    ('tool_name', '=', values['tool_name']),
                ], limit=1)
                if not existing:
                    tools.create(dict(values, account_id=account.id))
        return True
