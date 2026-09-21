# -*- coding: utf-8 -*-
"""Which file the customer actually receives.

One question decides this whole module: given a record, is the PDF we are
about to send *that record's printout*, or merely a PDF that happens to be
filed against it? The difference is a scan somebody dropped in the chatter
going out under the words "here is your invoice".

The second question is cheaper but not small: a caller outside Odoo can ask
for the same document as often as it likes, so rendering must not leave a
fresh copy behind every time.
"""

import base64

from odoo.tests import tagged

from .common import KMessageCase

FAKE_PDF = b'%PDF-1.4 rendered by the test\n%%EOF\n'


@tagged('post_install', '-at_install')
class TestDocument(KMessageCase):

    def setUp(self):
        super().setUp()
        self.record = self.env['res.partner'].create({'name': 'Document Subject'})
        # A printout of its own, because a stock database defines none for a
        # contact — and the question here is what happens once one exists.
        self.report = self.env['ir.actions.report'].create({
            'name': 'Contact sheet',
            'model': 'res.partner',
            'report_type': 'qweb-pdf',
            'report_name': 'kmessage_connector.test_contact_sheet',
        })
        self.renders = []

        def _render_pdf(_self, report, record):
            self.renders.append(record.id)
            return FAKE_PDF, 'pdf'

        self.patch(type(self.env['kmessage.document']), '_render_pdf', _render_pdf)

    def documents(self):
        return self.env['kmessage.document']

    def attachments(self):
        return self.env['ir.attachment'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', self.record.id)])

    # -- the file that goes out -------------------------------------------
    def test_a_foreign_pdf_on_the_record_is_never_sent_as_the_printout(self):
        """Somebody else's file, filed against the same record."""
        scan = self.env['ir.attachment'].create({
            'name': 'scan from the customer.pdf',
            'datas': base64.b64encode(b'%PDF-1.4 not ours\n'),
            'mimetype': 'application/pdf',
            'res_model': 'res.partner',
            'res_id': self.record.id,
        })
        attachment = self.documents()._render(self.record)
        self.assertNotEqual(attachment, scan)
        self.assertEqual(attachment.raw, FAKE_PDF)

    def test_rendering_twice_leaves_one_copy(self):
        first = self.documents()._render(self.record)
        second = self.documents()._render(self.record)
        self.assertEqual(first, second, 'the second call should reuse the first file')
        self.assertEqual(len(self.renders), 1, 'the printer should have run once')
        self.assertEqual(
            len(self.attachments().filtered(lambda a: a.name == first.name)), 1)

    def test_asking_for_a_fresh_copy_renders_again(self):
        self.documents()._render(self.record)
        self.documents()._render(self.record, reuse=False)
        self.assertEqual(len(self.renders), 2)

    def test_the_file_is_named_after_the_record(self):
        attachment = self.documents()._render(self.record)
        self.assertEqual(attachment.name, 'Document Subject.pdf')

    def test_a_record_with_no_report_yields_nothing(self):
        """Nothing to print is not an error; it is simply no document."""
        self.report.unlink()
        self.assertIsNone(self.documents()._render(self.record))
        self.assertFalse(self.renders)

    def test_odoo_s_own_stored_copy_is_preferred_when_the_report_keeps_one(self):
        """A report configured to store its output owns that file."""
        self.report.attachment = "'kept.pdf'"
        kept = self.env['ir.attachment'].create({
            'name': 'kept.pdf',
            'datas': base64.b64encode(b'%PDF-1.4 kept by odoo\n'),
            'mimetype': 'application/pdf',
            'res_model': 'res.partner',
            'res_id': self.record.id,
            'res_field': False,
        })
        self.assertEqual(self.documents()._render(self.record), kept)
        self.assertFalse(self.renders, 'nothing should have been printed')
