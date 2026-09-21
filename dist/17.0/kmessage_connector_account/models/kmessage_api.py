# -*- coding: utf-8 -*-
"""The two invoice questions K-Message may put to Odoo.

Both follow core's rule to the letter: the request carries a phone number, the
number decides the customer, and nothing outside that customer's own invoices
can be reached — not by asking for a different name, not by guessing a number.
That is why the "give me this invoice" handler answers the same *not found* for
an invoice that belongs to somebody else as for one that does not exist: a
customer who tries numbers must not be able to learn which of them are real.

The rows the list returns are deliberately few and plain. They are read out
loud in a WhatsApp conversation, and a model that is handed thirty columns
invents relationships between them.
"""

import logging

from odoo import _, api, fields, models
from odoo.addons.kmessage_connector.models.kmessage_api import KMessageApiError, KMessageFile

from .account_move import CUSTOMER_MOVES

_logger = logging.getLogger(__name__)


def _flag(value):
    """A yes/no that survived being substituted into a JSON body.

    An assistant's argument arrives as whatever K-Message put in the body: a
    real boolean, the word "false", or — when the assistant left the argument
    out altogether — the ``{{args.unpaid_only}}`` placeholder itself. Only an
    unambiguous yes counts as one, so an unfilled placeholder reads as no.
    """
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')


class KMessageApi(models.AbstractModel):
    _inherit = 'kmessage.api'

    # -- the invoice list -------------------------------------------------
    @api.model
    def _handle_invoices(self, payload, capability):
        """That customer's posted invoices, newest first."""
        partner = self._partner_from(payload)
        domain = self._kmessage_invoice_domain(partner)
        if _flag(payload.get('unpaid_only')):
            domain.append(('amount_residual', '!=', 0))

        invoices = self.env['account.move'].sudo().search(
            domain, order='invoice_date desc, id desc', limit=self._limit(payload, capability))
        stored = invoices._kmessage_pdf_attachments()
        return {
            'rows': [self._kmessage_invoice_row(invoice, invoice.id in stored) for invoice in invoices],
        }

    @api.model
    def _kmessage_invoice_row(self, invoice, has_pdf):
        """One document, the way the customer's own account reads it.

        Amounts are signed. Odoo stores a credit note the way it stores an
        invoice, as a positive amount, and handed over like that it reads as
        another hundred to pay — the opposite of what it is. The row names
        which of the two it is as well, because a bare -100 is something an
        assistant will otherwise explain for itself.
        """
        currency = invoice.currency_id or invoice.company_id.currency_id
        credit_note = invoice.move_type == 'out_refund'
        sign = -1 if credit_note else 1
        return {
            'number': invoice.name or '',
            'date': fields.Date.to_string(invoice.invoice_date) or '',
            'type': 'credit_note' if credit_note else 'invoice',
            'total': self._kmessage_amount(sign * invoice.amount_total, currency),
            'due': self._kmessage_amount(sign * invoice.amount_residual, currency),
            'currency': currency.name or '',
            'status': self._kmessage_invoice_status(invoice),
            'has_pdf': has_pdf,
        }

    @api.model
    def _kmessage_amount(self, amount, currency):
        """Rounded the way the currency itself rounds, not to two decimals.

        Some currencies have no decimals at all and some round to five. An
        amount read out to a customer that does not match what is printed on
        their invoice is worse than no amount at all.
        """
        return currency.round(amount or 0.0) if currency else round(amount or 0.0, 2)

    @api.model
    def _kmessage_invoice_status(self, invoice):
        """Read from what is left to settle rather than from ``payment_state``.

        The two servers this addon runs on spell some of that field's values
        differently, and "is there anything still on it" is the question a
        customer is asking anyway. A credit note gets words of its own:
        calling it unpaid puts the money on the wrong side.
        """
        currency = invoice.currency_id or invoice.company_id.currency_id
        settled = currency.is_zero(invoice.amount_residual)
        if invoice.move_type == 'out_refund':
            return 'settled' if settled else 'credit_note'
        if settled:
            return 'paid'
        if currency.compare_amounts(abs(invoice.amount_residual), abs(invoice.amount_total)) < 0:
            return 'partly_paid'
        return 'unpaid'

    # -- one invoice as a file --------------------------------------------
    @api.model
    def _handle_invoice_pdf(self, payload, capability):
        """The PDF of one of that customer's own invoices."""
        partner = self._partner_from(payload)
        # Whatever the assistant put in the body: an invoice number that looks
        # like a number often arrives as one, and a document request must not
        # end in "something went wrong in Odoo" over a missing pair of quotes.
        number = str(payload.get('invoice') or payload.get('number') or '').strip()
        if not number or number.startswith('{{'):
            raise KMessageApiError(
                'invoice_required', _("Say which invoice, by the number it was listed under."), 400)

        invoice = self.env['account.move'].sudo().search(
            self._kmessage_invoice_domain(partner) + [('name', '=', number)], limit=1)
        if not invoice:
            raise KMessageApiError(
                'not_found', _("There is no invoice with that number on this customer's account."), 404)

        # The printout already kept for this invoice, where there is one. The
        # whole point of keeping it is that the file exists, and printing it
        # again would leave the invoice carrying a second identical PDF after
        # every "send me my invoice": a customer who asks five times must not
        # cost five attachments.
        kept = invoice._kmessage_pdf_attachments().get(invoice.id)
        content = kept.sudo().raw if kept else None
        if not content:
            try:
                # In a savepoint for the reason the posting hook gives: a
                # report that fails on a *query* leaves the cursor unusable,
                # and the refusal below would then be answered by a request
                # that can no longer be committed.
                with self.env.cr.savepoint():
                    attachment = self.env['kmessage.document']._render(
                        invoice, lang=partner.lang or None)
                content = attachment.sudo().raw if attachment else None
            except Exception:  # noqa: BLE001 - a broken report is our problem, not the caller's
                _logger.exception('K-Message: %s could not be rendered', invoice.display_name)
                content = None
        if not content:
            raise KMessageApiError(
                'document_unavailable',
                _("That invoice could not be printed just now. Try again in a moment."), 503)

        return KMessageFile(content, '%s.pdf' % number.replace('/', '-'), 'application/pdf')

    # -- what an agent sees about a customer ------------------------------
    @api.model
    def _customer_extras(self, partner, company):
        """Add what this customer owes to core's answer about them.

        In the company's currency, not each invoice's: one sentence cannot add
        two currencies together, and the company's own is the one the person
        reading it thinks in.
        """
        extras = super()._customer_extras(partner, company)
        open_invoices = self.env['account.move'].sudo().search(
            self._kmessage_invoice_domain(partner, company=company) + [('amount_residual', '!=', 0)],
            order='invoice_date_due asc, invoice_date asc, id asc')
        oldest = next(
            (invoice.invoice_date_due or invoice.invoice_date
             for invoice in open_invoices if invoice.move_type == 'out_invoice'),
            False)
        extras.update({
            'balance_due': self._money(sum(open_invoices.mapped('amount_residual_signed')),
                                       company.currency_id),
            'oldest_unpaid': fields.Date.to_string(oldest) or '',
        })
        return extras

    # -- the one place the scope is decided -------------------------------
    @api.model
    def _kmessage_invoice_domain(self, partner, company=None):
        """Everything that keeps an answer to this customer's own invoices.

        ``commercial_partner_id`` rather than ``partner_id`` on purpose: an
        invoice addressed to a person at a company belongs to the company, and
        whoever of them is on WhatsApp should be able to ask about it.
        """
        return [
            ('move_type', 'in', list(CUSTOMER_MOVES)),
            ('state', '=', 'posted'),
            ('commercial_partner_id', '=', partner.commercial_partner_id.id),
            ('company_id', '=', (company or self.env.company).id),
        ]
