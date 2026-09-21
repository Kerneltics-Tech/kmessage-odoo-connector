# -*- coding: utf-8 -*-
"""What posting a customer invoice sets off, and what falling due sets off.

Two things happen when an invoice is posted: the automations are told, and —
only if the connection asks for it — the PDF is rendered and left on the
invoice. Neither may put the posting itself at risk, so both are wrapped, and
wrapped in a savepoint rather than in a bare ``except``: an unreachable
WhatsApp service or a report that will not render is a message that does not go
out, never an accounting entry that does not exist.

Falling due is not an event Odoo raises, so it is looked for once a day instead.
"""

import logging

from odoo import api, fields, models

from .kmessage_automation import TRIGGER_INVOICE_DUE, TRIGGER_INVOICE_POSTED

_logger = logging.getLogger(__name__)

#: The moves that are about a customer. A vendor bill is somebody else's
#: document and is never messaged about.
CUSTOMER_MOVES = ('out_invoice', 'out_refund')

#: How many overdue invoices one run of the cron hands over. A guard against a
#: runaway rather than a queue: without it, a nightly scheduled action loads
#: every unpaid invoice in the database into memory and then asks the outbox
#: about each one in turn.
DUE_BATCH_LIMIT = 1000


class AccountMove(models.Model):
    _inherit = 'account.move'

    # -- posting ----------------------------------------------------------
    def _post(self, soft=True):
        """Post as usual, then tell the connector about the customer invoices.

        ``super()`` comes first and its result is what we act on: with
        ``soft=True`` some of ``self`` may stay in the drawer until their date
        arrives, and those are not posted invoices yet.
        """
        posted = super()._post(soft=soft)
        invoices = posted.filtered(lambda move: move.move_type in CUSTOMER_MOVES)
        if invoices:
            invoices._kmessage_keep_pdf()
            self.env['kmessage.automation'].run_trigger(TRIGGER_INVOICE_POSTED, invoices)
        return posted

    def _kmessage_keep_pdf(self):
        """Render each invoice's PDF now, where the connection asked for it.

        Every step runs inside a savepoint, and the reason is the same one
        ``run_trigger`` gives: swallowing the exception is not enough on its
        own, because a failed *query* leaves the cursor unusable and the
        posting still in flight would then fail on its next write with an error
        nobody can act on. One savepoint per invoice, so a report that will not
        render costs that invoice its PDF and not the rest of the batch theirs.
        """
        try:
            with self.env.cr.savepoint():
                stored = self._kmessage_pdf_attachments()
                wanted = self._kmessage_prerender_wanted()
        except Exception:  # noqa: BLE001 - a printout must not undo a posting
            _logger.exception(
                'K-Message: could not work out which invoices want a PDF kept; none was rendered')
            return

        documents = self.env['kmessage.document']
        for invoice in wanted:
            if invoice.id in stored:
                continue
            try:
                with self.env.cr.savepoint():
                    # In the customer's own language: this is the file they will be
                    # sent, not a copy for the accountant.
                    documents._render(invoice, lang=invoice.partner_id.lang or None)
            except Exception:  # noqa: BLE001 - a printout must not undo a posting
                _logger.exception(
                    'K-Message: could not render the PDF of %s; the invoice is posted all the same',
                    invoice.display_name)

    def _kmessage_prerender_wanted(self):
        """The invoices whose own company asked for a PDF to be kept."""
        connections = {}
        wanted = self.browse()
        for invoice in self:
            company = invoice.company_id
            if company.id not in connections:
                connections[company.id] = self.env['kmessage.account']._for_company(company)
            connection = connections[company.id]
            if connection and connection.prerender_invoice_pdf:
                wanted |= invoice
        return wanted

    def _kmessage_pdf_attachments(self):
        """The invoice's own printout, by invoice id. Nothing else counts.

        Not "any PDF on the record", which is the same question only in an
        Odoo where nobody files anything: a scan dropped in the chatter, a
        signed delivery note, a customer's own attachment would each answer
        yes — and then the posting skips rendering the invoice nobody has
        printed, the list reports a PDF the customer can be sent, and what
        K-Message ends up sending them is the scan.

        Only the name can tell the two apart, so the two names this connector
        can produce are the ones looked for: the one it gives a file it
        rendered itself, and the one Odoo's own printer would have given a
        report that stores its output. Read in one query for the whole set,
        because this answers both "has this invoice got its PDF" for a list of
        ten and "must we render one" when a hundred are posted at once.
        """
        found = {}
        if not self.ids:
            return found
        documents = self.env['kmessage.document']
        report = documents._report_for(self[:1])
        if report and report.attachment:
            found.update(self._kmessage_report_attachments(report))

        wanted = {invoice.id: documents._filename(invoice) for invoice in self}
        attachments = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'account.move'),
            ('res_id', 'in', self.ids),
            ('mimetype', '=', 'application/pdf'),
            ('name', 'in', sorted(set(wanted.values()))),
        ], order='id desc')
        for attachment in attachments:
            if attachment.name == wanted.get(attachment.res_id):
                found.setdefault(attachment.res_id, attachment)
        return found

    def _kmessage_report_attachments(self, report):
        """What Odoo's own printer stored for these invoices, where it stores.

        Stock Odoo prints an invoice without keeping the file, on both series,
        so this is the rare case rather than the usual one — which is why it
        is a record at a time and the ordinary path is not made to pay for it.
        """
        found = {}
        for invoice in self:
            try:
                stored = report.sudo().retrieve_attachment(invoice)
            except Exception:  # noqa: BLE001 - a report's own naming is not ours to fix
                _logger.exception(
                    'K-Message: could not work out what %s would call its printout',
                    report.display_name)
                return found
            if stored and stored.mimetype == 'application/pdf':
                found[invoice.id] = stored
        return found

    # -- falling due ------------------------------------------------------
    @api.model
    def _kmessage_cron_due_invoices(self):
        """Hand over the invoices whose due date has arrived and gone unpaid.

        Nothing is looked for until somebody has a live rule on the trigger,
        and then only in that rule's own company. A scheduled action that reads
        every unpaid invoice in every company each night, to find that nobody
        wanted any of them, is a cost with no message at the end of it.

        Only real invoices: a credit note falling due is money owed to the
        customer, and "your invoice is due" is the wrong sentence for it.

        The same overdue invoice is handed over every day for as long as it
        stays overdue, because that is the honest answer to "which invoices are
        due". How often a customer is actually *told* is the rule's own
        cooldown, which starts at a week for this trigger.
        """
        automations = self.env['kmessage.automation'].sudo().search(
            [('trigger', '=', TRIGGER_INVOICE_DUE)])
        companies = automations.company_id
        if not companies:
            return True

        today = fields.Date.context_today(self)
        # sudo: a scheduled action has no company of its own, and an overdue
        # invoice in the second company is still overdue.
        due = self.sudo().search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('company_id', 'in', companies.ids),
            ('invoice_date_due', '!=', False),
            ('invoice_date_due', '<=', today),
            ('amount_residual', '>', 0),
        ], order='invoice_date_due asc, id asc', limit=DUE_BATCH_LIMIT)
        if len(due) == DUE_BATCH_LIMIT:
            # Said plainly, because the ordering is stable: the ones past the
            # ceiling are not deferred to tomorrow, they are never reached.
            _logger.warning(
                'K-Message: more than %s invoices have fallen due; only the %s oldest were '
                'handed over. Narrow the rule with a filter, or the newer ones stay unseen.',
                DUE_BATCH_LIMIT, DUE_BATCH_LIMIT)
        self.env['kmessage.automation'].run_trigger(TRIGGER_INVOICE_DUE, due)
        return True
