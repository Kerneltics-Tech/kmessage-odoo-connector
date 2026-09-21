# -*- coding: utf-8 -*-
"""One switch on the connection, and the reason it is worth having.

K-Message's own assistant can already fetch an invoice from Odoo over its
built-in connection — but it only ever *reads*: it attaches a PDF that is
already on the invoice and never prints one. In an Odoo where invoices are
printed when somebody clicks Print, there is usually nothing there, and the
customer is told their invoice cannot be found. This switch is what closes that
gap, which is why it lives next to the other master switches rather than in a
settings page nobody opens.
"""

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class KMessageAccount(models.Model):
    _inherit = 'kmessage.account'

    prerender_invoice_pdf = fields.Boolean(
        string='Keep a PDF on every posted invoice',
        help="K-Message's own assistant can send a customer their invoice through its "
             "built-in link to Odoo, but only when the invoice already carries a PDF — it "
             "reads attachments, it never prints. Switch this on and Odoo renders the PDF "
             "once, as the invoice is posted, so “send me my invoice” has something to "
             "send. It costs a second of the posting, and nothing else changes.")

    @api.model_create_multi
    def create(self, vals_list):
        """Give a new connection the invoice tools it can publish.

        The tools cannot be plain data: they belong to a connection, and a
        database that installs this addon before connecting has none. So they
        are seeded here as well as at install, and creating a connection is
        never allowed to fail over a tool row.
        """
        accounts = super().create(vals_list)
        try:
            self.env['kmessage.ai.tool'].sudo()._seed_invoice_tools(accounts)
        except Exception:  # noqa: BLE001 - a tool row must not block connecting
            _logger.exception('K-Message: could not prepare the invoice assistant tools')
        return accounts
