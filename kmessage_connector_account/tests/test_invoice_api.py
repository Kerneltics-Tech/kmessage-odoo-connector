# -*- coding: utf-8 -*-
"""That an answer is about the customer who asked, and nobody else."""

from unittest.mock import patch

from odoo.addons.kmessage_connector.models.kmessage_api import KMessageApiError, KMessageFile
from odoo.tests import tagged

from .common import FAKE_PDF, KMessageAccountCommon, fake_printer


@tagged('post_install', '-at_install')
class TestInvoiceApi(KMessageAccountCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        capabilities = (
            cls.env.ref('kmessage_connector_account.capability_invoices')
            | cls.env.ref('kmessage_connector_account.capability_invoice_pdf')
            | cls.env.ref('kmessage_connector.capability_customer_lookup')
        )
        cls.token, cls.raw_token = cls.env['kmessage.token'].issue(
            name='K-Message tests', company=cls.company, capabilities=capabilities)

    def _ask(self, code, payload):
        """One request, the way the controller makes it."""
        return self.env['kmessage.api'].with_company(self.company).dispatch(code, payload, self.token)

    def _pay(self, invoice):
        """Settle an invoice in full, the way an accountant would."""
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create({})._create_payments()
        return invoice

    def test_the_list_holds_that_customer_invoices_only(self):
        mine = self._invoice()
        theirs = self._invoice(partner=self.other_customer)

        rows = self._ask('invoices', {'phone': self.customer_phone})['rows']

        self.assertEqual([row['number'] for row in rows], [mine.name])
        self.assertNotIn(theirs.name, [row['number'] for row in rows])
        self.assertEqual(rows[0]['status'], 'unpaid')
        self.assertEqual(rows[0]['total'], 100.0)
        self.assertEqual(rows[0]['due'], 100.0)
        self.assertFalse(rows[0]['has_pdf'])

    def test_a_settled_invoice_reads_as_paid(self):
        invoice = self._pay(self._invoice())

        rows = self._ask('invoices', {'phone': self.customer_phone})['rows']

        self.assertEqual(rows[0]['number'], invoice.name)
        self.assertEqual(rows[0]['status'], 'paid')
        self.assertEqual(rows[0]['due'], 0.0)

    def test_an_unfilled_argument_is_not_a_yes(self):
        """K-Message leaves the placeholder in the body when the assistant
        passes no argument, and that must not read as "unpaid only"."""
        invoice = self._pay(self._invoice())

        left_out = self._ask('invoices', {
            'phone': self.customer_phone,
            'unpaid_only': '{{args.unpaid_only}}',
            'limit': '{{args.limit}}',
        })['rows']
        asked_for = self._ask('invoices', {'phone': self.customer_phone, 'unpaid_only': True})['rows']

        self.assertEqual([row['number'] for row in left_out], [invoice.name])
        self.assertFalse(asked_for, "asked for the unpaid ones, a paid invoice is not one")

    def test_the_pdf_comes_back_for_the_right_customer(self):
        invoice = self._invoice()

        with fake_printer(self.env):
            answer = self._ask('invoice_pdf', {'phone': self.customer_phone, 'invoice': invoice.name})

        self.assertIsInstance(answer, KMessageFile)
        self.assertTrue(answer.content.startswith(b'%PDF'))
        self.assertEqual(answer.mimetype, 'application/pdf')
        self.assertEqual(answer.filename, '%s.pdf' % invoice.name.replace('/', '-'))

    def test_another_customer_invoice_is_not_found(self):
        theirs = self._invoice(partner=self.other_customer)

        with self.assertRaises(KMessageApiError) as refused:
            with fake_printer(self.env):
                self._ask('invoice_pdf', {'phone': self.customer_phone, 'invoice': theirs.name})

        self.assertEqual(refused.exception.code, 'not_found')
        self.assertEqual(refused.exception.status, 404)

    def test_a_missing_invoice_number_is_asked_for(self):
        with self.assertRaises(KMessageApiError) as refused:
            self._ask('invoice_pdf', {'phone': self.customer_phone, 'invoice': '{{args.invoice}}'})

        self.assertEqual(refused.exception.code, 'invoice_required')

    def test_a_lookup_says_what_the_customer_owes(self):
        invoice = self._invoice()

        answer = self._ask('customer_lookup', {'phone': self.customer_phone})

        self.assertTrue(answer['found'])
        self.assertEqual(answer['balance_due']['amount'], 100.0)
        self.assertEqual(answer['balance_due']['currency'], self.company.currency_id.name)
        self.assertEqual(answer['oldest_unpaid'], str(invoice.invoice_date_due))

    def test_a_credit_note_is_not_something_to_pay(self):
        """Odoo states a credit note as a positive amount like any other move,
        and handed over as it stands it reads as another hundred owed."""
        note = self._invoice(move_type='out_refund')

        rows = self._ask('invoices', {'phone': self.customer_phone})['rows']
        lookup = self._ask('customer_lookup', {'phone': self.customer_phone})

        self.assertEqual(rows[0]['number'], note.name)
        self.assertEqual(rows[0]['type'], 'credit_note')
        self.assertEqual(rows[0]['status'], 'credit_note')
        self.assertEqual(rows[0]['total'], -100.0, "money in the customer's favour")
        self.assertEqual(rows[0]['due'], -100.0, "nothing to pay on a credit note")
        self.assertEqual(lookup['balance_due']['amount'], -100.0,
                         "the row and the balance say the same thing")
        self.assertFalse(lookup['oldest_unpaid'], "a credit note is not an overdue invoice")

    def test_an_invoice_is_still_an_invoice(self):
        invoice = self._invoice()

        rows = self._ask('invoices', {'phone': self.customer_phone})['rows']

        self.assertEqual(rows[0]['type'], 'invoice')
        self.assertEqual(rows[0]['total'], 100.0)
        self.assertEqual(rows[0]['due'], 100.0)

    def test_another_company_invoice_is_out_of_reach(self):
        """The same customer, the same phone, an invoice in the other company."""
        other = self._other_company()
        self.env.user.company_ids |= other
        theirs = self.init_invoice(
            'out_invoice', partner=self.customer, amounts=[55.0], company=other)
        theirs.action_post()

        rows = self._ask('invoices', {'phone': self.customer_phone})['rows']
        self.assertFalse(rows, "this token is a token for one company")

        with self.assertRaises(KMessageApiError) as refused:
            with fake_printer(self.env):
                self._ask('invoice_pdf', {'phone': self.customer_phone, 'invoice': theirs.name})
        self.assertEqual(refused.exception.status, 404)

    def test_an_invoice_number_sent_as_a_number_is_refused_not_broken(self):
        """An assistant that writes {"invoice": 12345} gets an answer it can
        read out, not "something went wrong in Odoo"."""
        self._invoice()

        with self.assertRaises(KMessageApiError) as refused:
            with fake_printer(self.env):
                self._ask('invoice_pdf', {'phone': self.customer_phone, 'invoice': 12345})

        self.assertEqual(refused.exception.code, 'not_found')
        self.assertEqual(refused.exception.status, 404)

    def test_a_report_that_fails_on_a_query_leaves_the_request_usable(self):
        """Swallowing the exception is not enough: a failed query leaves the
        cursor unusable, and the 503 below would then be answered by a request
        that cannot commit."""
        invoice = self._invoice()

        def explode(document, report, record):
            document.env.cr.execute('SELECT 1 / 0')

        with patch.object(type(self.env['kmessage.document']), '_render_pdf', explode):
            with self.assertRaises(KMessageApiError) as refused:
                self._ask('invoice_pdf', {'phone': self.customer_phone, 'invoice': invoice.name})

        self.assertEqual(refused.exception.status, 503)
        self.assertTrue(self.env['res.partner'].search_count([]),
                        "the transaction survived the report")

    def test_asking_for_the_same_invoice_again_prints_it_once(self):
        invoice = self._invoice()

        with fake_printer(self.env):
            for _attempt in range(3):
                self._ask('invoice_pdf', {'phone': self.customer_phone, 'invoice': invoice.name})

        kept = self.env['ir.attachment'].search([
            ('res_model', '=', 'account.move'), ('res_id', '=', invoice.id)])
        self.assertEqual(len(kept), 1, "a customer who asks three times does not cost three PDFs")

    def test_a_foreign_pdf_is_not_offered_as_the_invoice(self):
        """K-Message sends what is attached to the invoice, so "there is a PDF"
        must mean the invoice's own printout and not a scan in the chatter."""
        invoice = self._invoice()
        self.env['ir.attachment'].create({
            'name': 'a scan somebody dropped in the chatter.pdf',
            'raw': FAKE_PDF,
            'mimetype': 'application/pdf',
            'res_model': 'account.move',
            'res_id': invoice.id,
        })

        rows = self._ask('invoices', {'phone': self.customer_phone})['rows']

        self.assertFalse(rows[0]['has_pdf'])

    def test_the_assistant_tools_are_seeded_once(self):
        tools = self.env['kmessage.ai.tool'].with_context(active_test=False)
        domain = [
            ('account_id', '=', self.connection.id),
            ('tool_name', 'in', ('odoo_invoices', 'odoo_invoice_pdf')),
        ]
        tools.search(domain).unlink()

        tools._seed_invoice_tools(self.connection)
        seeded = tools.search(domain)
        self.assertEqual(len(seeded), 2, "a connection gets both invoice tools")
        self.assertEqual(sorted(seeded.mapped('kind')), ['document_tool', 'lookup_tool'])

        tools._seed_invoice_tools(self.connection)
        self.assertEqual(tools.search_count(domain), 2, "seeding twice does not duplicate a tool")
