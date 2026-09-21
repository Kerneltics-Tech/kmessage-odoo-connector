# -*- coding: utf-8 -*-
"""Turning a record into the PDF a customer receives.

Kept apart from the rest so that “which printout, in which language” is one
question answered in one place — the automations, the manual send wizard and
the inbound “send me my invoice” capability all come through here and therefore
all produce the identical file.
"""

import base64
import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class KMessageDocument(models.AbstractModel):
    _name = 'kmessage.document'
    _description = 'K-Message Document Rendering'

    @api.model
    def _render(self, record, report_name=None, lang=None, reuse=True):
        """An ``ir.attachment`` holding ``record``'s printout.

        ``reuse`` returns an attachment already stored on the record when one
        exists — invoices in particular are often printed the moment they are
        posted, and rendering the same PDF twice wastes a second of a customer's
        wait for no gain.
        """
        report = self._report_for(record, report_name)
        if not report:
            return None

        if lang:
            record = record.with_context(lang=lang)

        if reuse:
            existing = self._stored_attachment(record, report)
            if existing:
                return existing

        content, _content_type = self._render_pdf(report, record)
        if not content:
            return None

        return self.env['ir.attachment'].sudo().create({
            'name': self._filename(record),
            'datas': base64.b64encode(content),
            'mimetype': 'application/pdf',
            'res_model': record._name,
            'res_id': record.id,
            'company_id': record.company_id.id if 'company_id' in record._fields else False,
        })

    @api.model
    def _render_pdf(self, report, record):
        """Render, coping with both of the shapes Odoo has used for this call."""
        try:
            return report._render_qweb_pdf(report.report_name, res_ids=record.ids)
        except TypeError:
            # Older signature: no report reference, just the ids.
            return report._render_qweb_pdf(record.ids)

    @api.model
    def _report_for(self, record, report_name=None):
        """The report to print: the one asked for, else the record's usual one."""
        reports = self.env['ir.actions.report'].sudo()
        if report_name:
            report = reports.search([('report_name', '=', report_name)], limit=1)
            if not report:
                raise UserError(_("There is no report called “%s”.", report_name))
            return report
        return reports.search([
            ('model', '=', record._name),
            ('report_type', 'in', ('qweb-pdf', 'qweb-text')),
        ], order='id asc', limit=1)

    @api.model
    def _stored_attachment(self, record, report):
        """This record's own printout, if one is already stored. Nothing else.

        Deliberately not "any PDF filed against the record". That is the same
        question only in an Odoo where nobody files anything: a scan dropped in
        the chatter, a supplier's PDF forwarded in by mail, a signed delivery
        note — each of them is a PDF on the record, and sending one to a
        customer under the words "here is your invoice" is worse than sending
        nothing at all.

        Two things count. Odoo's own stored copy, when the report is configured
        to keep one, and a file this connector rendered earlier, which it names
        for itself. Recognising the second is what stops an external caller
        stacking a fresh copy of the same document on every request — stock
        Odoo sets no attachment expression on the invoice report, so without it
        reuse never happens at all.
        """
        stored = self._odoo_stored_copy(record, report)
        if stored:
            return stored

        ours = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('mimetype', '=', 'application/pdf'),
            ('name', '=', self._filename(record)),
        ], order='id desc', limit=1)
        return ours or None

    @api.model
    def _odoo_stored_copy(self, record, report):
        """What Odoo's own printer kept for this record, when it keeps one."""
        if not report.attachment:
            return None
        try:
            return report.sudo().retrieve_attachment(record) or None
        except Exception:  # noqa: BLE001 - an expression somebody wrote, not ours
            _logger.exception(
                "K-Message: could not read %s's stored copy of %s",
                report.report_name, record.display_name)
            return None

    @api.model
    def _filename(self, record):
        """A filename a person can read in their WhatsApp thread."""
        base = record.display_name or record._name
        safe = ''.join(char if (char.isalnum() or char in ' -_') else '_' for char in base).strip()
        return '%s.pdf' % (safe or 'document')
