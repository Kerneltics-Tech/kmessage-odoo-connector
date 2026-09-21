# -*- coding: utf-8 -*-
"""That the right business moments reach the outbox, and only once."""

from odoo import fields
from odoo.addons.kmessage_connector_account.models.kmessage_automation import (
    DUE_COOLDOWN_HOURS, TRIGGER_INVOICE_DUE, TRIGGER_INVOICE_POSTED, TRIGGER_PAYMENT_RECEIVED,
)
from odoo.tests import tagged

from .common import FAKE_PDF, KMessageAccountCommon, fake_printer


@tagged('post_install', '-at_install')
class TestInvoiceTriggers(KMessageAccountCommon):

    def test_posting_an_invoice_queues_one_message(self):
        rule = self._rule(TRIGGER_INVOICE_POSTED)
        invoice = self._invoice()

        messages = self._messages_about(invoice)
        self.assertEqual(len(messages), 1, "one posting, one message")
        self.assertEqual(messages.partner_id, self.customer)
        self.assertEqual(messages.state, 'queued')
        self.assertEqual(rule.sent_count, 1)

    def test_a_vendor_bill_is_not_a_customer_invoice(self):
        self._rule(TRIGGER_INVOICE_POSTED)
        bill = self._invoice(move_type='in_invoice')

        self.assertFalse(self._messages_about(bill), "a vendor bill is not a customer's invoice")

    def test_a_switched_off_rule_sends_nothing(self):
        self._rule(TRIGGER_INVOICE_POSTED, active=False)
        invoice = self._invoice()

        self.assertFalse(self._messages_about(invoice))

    def test_the_cooldown_stops_the_second_message(self):
        rule = self._rule(TRIGGER_INVOICE_POSTED, cooldown_hours=24)
        invoice = self._invoice()

        self.env['kmessage.automation'].run_trigger(TRIGGER_INVOICE_POSTED, invoice)
        self.assertEqual(len(self._messages_about(invoice)), 1,
                         "the same invoice within the cooldown is not messaged twice")

        rule.cooldown_hours = 0
        self.env['kmessage.automation'].run_trigger(TRIGGER_INVOICE_POSTED, invoice)
        self.assertEqual(len(self._messages_about(invoice)), 2,
                         "with the cooldown off, the rule sends again")

    def test_the_cron_finds_what_has_fallen_due(self):
        self._rule(TRIGGER_INVOICE_DUE)
        overdue = self._invoice()
        not_yet = self._invoice()
        not_yet.invoice_date_due = fields.Date.add(fields.Date.today(), days=30)

        self.env['account.move']._kmessage_cron_due_invoices()

        self.assertEqual(len(self._messages_about(overdue)), 1)
        self.assertFalse(self._messages_about(not_yet),
                         "an invoice whose due date has not arrived is not due")

    def test_the_same_overdue_invoice_is_not_chased_every_day(self):
        """An invoice is posted once but stays due, so the cron offers it again
        every night. What stops a reminder from becoming a daily chase is the
        cooldown this trigger starts on."""
        self._rule(TRIGGER_INVOICE_DUE)
        overdue = self._invoice()

        self.env['account.move']._kmessage_cron_due_invoices()
        self.env['account.move']._kmessage_cron_due_invoices()

        self.assertEqual(len(self._messages_about(overdue)), 1)

    def test_a_due_date_rule_starts_on_a_longer_cooldown(self):
        rule = self._rule(TRIGGER_INVOICE_DUE)
        self.assertEqual(rule.cooldown_hours, DUE_COOLDOWN_HOURS)

        typed = self._rule(TRIGGER_INVOICE_DUE, cooldown_hours=48)
        self.assertEqual(typed.cooldown_hours, 48, "a number somebody typed is left alone")

        # What the form does: the field is filled in before the record exists.
        drafted = self.env['kmessage.automation'].new({
            'account_id': self.connection.id,
            'template_id': self.template.id,
            'trigger': TRIGGER_INVOICE_DUE,
        })
        drafted._onchange_trigger_cooldown()
        self.assertEqual(drafted.cooldown_hours, DUE_COOLDOWN_HOURS)

    def test_a_credit_note_never_falls_due(self):
        """"Your invoice is due" is the wrong sentence for money we owe them."""
        self._rule(TRIGGER_INVOICE_DUE)
        note = self._invoice(move_type='out_refund')

        self.env['account.move']._kmessage_cron_due_invoices()

        self.assertFalse(self._messages_about(note))

    def test_a_customer_payment_says_thank_you(self):
        self._rule(TRIGGER_PAYMENT_RECEIVED)
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.customer.id,
            'amount': 100.0,
            'journal_id': self.company_data['default_journal_bank'].id,
        })
        payment.action_post()

        messages = self._messages_about(payment)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages.partner_id, self.customer)

    def test_a_payment_to_a_supplier_is_not_a_customer_payment(self):
        self._rule(TRIGGER_PAYMENT_RECEIVED)
        payment = self.env['account.payment'].create({
            'payment_type': 'outbound',
            'partner_type': 'supplier',
            'partner_id': self.customer.id,
            'amount': 100.0,
            'journal_id': self.company_data['default_journal_bank'].id,
        })
        payment.action_post()

        self.assertFalse(self._messages_about(payment))

    def test_the_pdf_is_kept_when_the_connection_asks_for_it(self):
        self.connection.prerender_invoice_pdf = True
        with fake_printer(self.env):
            invoice = self._invoice()

        self.assertTrue(invoice._kmessage_pdf_attachments(),
                        "K-Message's own tool can only send a PDF that is already there")

    def test_nothing_is_kept_when_it_is_switched_off(self):
        with fake_printer(self.env):
            invoice = self._invoice()

        self.assertFalse(invoice._kmessage_pdf_attachments())

    def test_a_scan_in_the_chatter_does_not_stand_in_for_the_invoice(self):
        """Any PDF on the record would be the wrong question in both
        directions: the invoice would never be printed, and what the assistant
        sends the customer would be somebody else's document."""
        self.connection.prerender_invoice_pdf = True
        invoice = self._invoice(post=False)
        scan = self.env['ir.attachment'].create({
            'name': 'a scan somebody dropped in the chatter.pdf',
            'raw': FAKE_PDF,
            'mimetype': 'application/pdf',
            'res_model': 'account.move',
            'res_id': invoice.id,
        })

        with fake_printer(self.env):
            invoice.action_post()

        kept = invoice._kmessage_pdf_attachments().get(invoice.id)
        self.assertTrue(kept, "the posting still had to print the invoice")
        self.assertNotEqual(kept, scan)
        self.assertEqual(kept.name, self.env['kmessage.document']._filename(invoice))

    def test_a_printer_on_fire_does_not_stop_a_posting(self):
        self.connection.prerender_invoice_pdf = True
        logger = 'odoo.addons.kmessage_connector_account.models.account_move'
        with fake_printer(self.env, content=None), self.assertLogs(logger, level='ERROR'):
            invoice = self._invoice()

        self.assertEqual(invoice.state, 'posted', "no WhatsApp trouble may undo an accounting entry")
        self.assertFalse(invoice._kmessage_pdf_attachments())
