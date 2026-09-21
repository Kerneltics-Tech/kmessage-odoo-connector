# -*- coding: utf-8 -*-
"""The two sales questions, offered to the K-Message assistant.

These are written from Python rather than declared as records in the data file
for one stubborn reason: a tool belongs to a connection, and the connection is
something a person creates in the Connect wizard — usually after installing the
addon, sometimes months after. A record in a data file would have nothing to
belong to. So the definitions live here and are written out whenever a
connection exists: at install if one is already set up, and at Connect if it is
set up later.

Writing a row publishes nothing. Each one is a draft carrying no token until
somebody presses “Publish to K-Message”, which is also where the tool is given
a narrow token of its own.
"""

import json
import logging

from odoo import _, api, models

from odoo.addons.kmessage_connector.models.kmessage_ai_tool import LOOKUP_TOOL

_logger = logging.getLogger(__name__)


class KMessageAiTool(models.Model):
    _inherit = 'kmessage.ai.tool'

    @api.model
    def _kmessage_sale_tool_specs(self):
        """One entry per capability this module adds.

        ``fields`` is the whole of what the assistant ever sees of an answer —
        K-Message shows nothing that is not listed, and at most eight columns —
        and the parameters deliberately carry no phone number: identity comes
        from the conversation, which is what makes these safe to expose.
        """
        return [
            {
                'capability': 'kmessage_connector_sale.capability_orders',
                'tool_name': 'odoo_orders',
                'name': _("Recent orders (Odoo)"),
                'description': _(
                    "Use this when the customer asks about their orders or quotations: "
                    "what they ordered, what it came to, or whether it has shipped. It "
                    "answers only for the person in this conversation. If it comes back "
                    "empty, say there is nothing on file for their number — do not guess "
                    "at an order."),
                'params': [
                    {
                        'name': 'state',
                        'type': 'string',
                        'description': _(
                            "Optional. One of open, quotation, confirmed, done, cancelled. "
                            "'open' means anything not yet finished or cancelled."),
                    },
                    {
                        'name': 'limit',
                        'type': 'number',
                        'description': _(
                            "Optional. How many orders to show, newest first. Five at most."),
                    },
                ],
                'fields': [
                    {'name': 'number', 'label': _("Order")},
                    {'name': 'date', 'label': _("Date")},
                    {'name': 'total', 'label': _("Total")},
                    {'name': 'currency', 'label': _("Currency")},
                    {'name': 'state', 'label': _("Status")},
                    {'name': 'delivery', 'label': _("Delivery")},
                ],
                'max_rows': 5,
                'empty_message': _("There are no orders on file for this number."),
                'sequence': 20,
            },
            {
                'capability': 'kmessage_connector_sale.capability_order_status',
                'tool_name': 'odoo_order_status',
                'name': _("One order's status (Odoo)"),
                'description': _(
                    "Use this when the customer names an order number and asks where it "
                    "stands. Pass the number exactly as they wrote it. It answers only for "
                    "orders belonging to the person in this conversation; anything else "
                    "comes back as not found, which is an answer, not a reason to try "
                    "another number."),
                'params': [
                    {
                        'name': 'order',
                        'type': 'string',
                        'description': _(
                            "The order number the customer mentioned, such as S00042."),
                    },
                ],
                'fields': [
                    {'name': 'number', 'label': _("Order")},
                    {'name': 'date', 'label': _("Date")},
                    {'name': 'total', 'label': _("Total")},
                    {'name': 'currency', 'label': _("Currency")},
                    {'name': 'state', 'label': _("Status")},
                    {'name': 'delivery', 'label': _("Delivery")},
                    {'name': 'lines', 'label': _("Items")},
                    {'name': 'line_count', 'label': _("Items in total")},
                ],
                'max_rows': 1,
                'empty_message': _("No order with that number belongs to this customer."),
                'sequence': 21,
            },
        ]

    @api.model
    def _kmessage_seed_sale_tools(self, accounts=None):
        """Draft the sales tools for these connections, or for every one.

        Safe to call twice: a tool is recognised by its name on the connection,
        so nothing is duplicated, and one a user deleted on purpose stays
        deleted until the next connection is made.
        """
        if accounts is None:
            accounts = self.env['kmessage.account'].sudo().search([])
        tools = self.sudo().with_context(active_test=False)
        for account in accounts:
            for spec in self._kmessage_sale_tool_specs():
                capability = self.env.ref(spec['capability'], raise_if_not_found=False)
                if not capability:
                    continue
                if tools.search_count([
                    ('account_id', '=', account.id),
                    ('tool_name', '=', spec['tool_name']),
                ]):
                    continue
                tools.create({
                    'name': spec['name'],
                    'tool_name': spec['tool_name'],
                    'description': spec['description'],
                    'account_id': account.id,
                    'capability_id': capability.id,
                    'kind': LOOKUP_TOOL,
                    # Both endpoints answer {'ok': .., 'data': {'rows': [..]}},
                    # which is where the rows sit for every capability here.
                    'items_path': 'data.rows',
                    'params_json': json.dumps(spec['params'], ensure_ascii=False, indent=2),
                    'fields_json': json.dumps(spec['fields'], ensure_ascii=False, indent=2),
                    'max_rows': spec['max_rows'],
                    'empty_message': spec['empty_message'],
                    'sequence': spec['sequence'],
                })
                _logger.info(
                    'K-Message: drafted the %s tool for %s',
                    spec['tool_name'], account.display_name)
        return True
