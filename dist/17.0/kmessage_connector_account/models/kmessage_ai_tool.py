# -*- coding: utf-8 -*-
"""The two invoice tools the K-Message assistant is taught.

These cannot be ordinary XML data. A tool belongs to a connection, and a
database that installs this addon before anyone has connected it to K-Message
has none — an XML record would fail on ``account_id`` and take the install down
with it. So the rows are described here and asked for twice: once at install,
for a database that is already connected, and again whenever a connection is
created. Both paths skip a tool that already exists, so nothing is duplicated
and a description somebody has reworded is never overwritten.

The descriptions are written the way K-Message writes its own: what the tool is
for, and what the assistant must not say when it comes back with nothing. An
assistant that is told only the first half fills the silence itself.
"""

import json

from odoo import _, api, models


class KMessageAiTool(models.Model):
    _inherit = 'kmessage.ai.tool'

    @api.model
    def _seed_invoice_tools(self, accounts=None):
        """Put the invoice tools on every connection that has not got them."""
        accounts = accounts if accounts is not None else self.env['kmessage.account'].sudo().search([])
        created = self.browse()
        for definition in self._invoice_tool_definitions():
            values = dict(definition)
            capability = self.env.ref(values.pop('capability'), raise_if_not_found=False)
            if not capability:
                continue
            for account in accounts:
                if self._invoice_tool_exists(account, values['tool_name']):
                    continue
                created |= self.sudo().create(dict(
                    values, account_id=account.id, capability_id=capability.id))
        return created

    @api.model
    def _invoice_tool_exists(self, account, tool_name):
        # A tool somebody switched off is still a tool: seeding must not bring
        # back what an administrator deliberately retired.
        return bool(self.sudo().with_context(active_test=False).search_count([
            ('account_id', '=', account.id),
            ('tool_name', '=', tool_name),
        ]))

    @api.model
    def _invoice_tool_definitions(self):
        return [
            {
                'capability': 'kmessage_connector_account.capability_invoices',
                'tool_name': 'odoo_invoices',
                'name': _("Invoices in Odoo"),
                'kind': 'lookup_tool',
                'description': _(
                    "Use this when the customer asks about their invoices, what they owe, "
                    "whether something has been paid, or for an invoice number. It returns "
                    "only the invoices of the person in this conversation, newest first. "
                    "A row whose kind is “credit_note” is money in the customer's favour "
                    "and shows as a negative amount: it is never something to pay. "
                    "If it comes back with nothing, say that there is nothing on their "
                    "account here — do not name an amount, and never say an invoice is paid "
                    "unless a row says so."),
                'params_json': json.dumps([
                    {
                        'name': 'unpaid_only',
                        'type': 'boolean',
                        'description': _("Only the documents that are not settled yet."),
                    },
                    {
                        'name': 'limit',
                        'type': 'integer',
                        'description': _("How many of the most recent invoices to look at, up to ten."),
                    },
                ], ensure_ascii=False),
                'fields_json': json.dumps([
                    {'name': 'number', 'label': _("Invoice")},
                    {'name': 'date', 'label': _("Date")},
                    {'name': 'type', 'label': _("Kind")},
                    {'name': 'total', 'label': _("Total")},
                    {'name': 'due', 'label': _("Still to pay")},
                    {'name': 'status', 'label': _("Status")},
                ], ensure_ascii=False),
                'items_path': 'data.rows',
                'max_rows': 10,
                'empty_message': _("This customer has no invoices on their account here."),
                'sequence': 20,
            },
            {
                'capability': 'kmessage_connector_account.capability_invoice_pdf',
                'tool_name': 'odoo_invoice_pdf',
                'name': _("Send an invoice PDF"),
                'kind': 'document_tool',
                'description': _(
                    "Use this to send the customer one of their own invoices as a PDF. Pass "
                    "the invoice number exactly as the invoice list gave it — look it up "
                    "there first rather than asking the customer to spell it out. If this "
                    "answers that the invoice was not found, that number is not on their "
                    "account: say so plainly, and do not describe an invoice you could not "
                    "fetch."),
                'params_json': json.dumps([
                    {
                        'name': 'invoice',
                        'type': 'string',
                        'description': _("The invoice number, exactly as the invoice list gave it."),
                    },
                ], ensure_ascii=False),
                'sequence': 21,
            },
        ]
