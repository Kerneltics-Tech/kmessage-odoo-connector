# -*- coding: utf-8 -*-
"""A connected Odoo with two customers, one of whom is on WhatsApp.

Rendering a real invoice PDF needs wkhtmltopdf, which no test machine is
obliged to have, so the printer is stood in for. What these tests are about is
which customer a document belongs to and whether a posting survives a printer
that does not work — neither of which is a question about wkhtmltopdf.
"""

from contextlib import contextmanager
from unittest.mock import patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

#: Real enough for anything that checks the first bytes of a file.
FAKE_PDF = b'%PDF-1.4 k-message test\n%%EOF\n'


@contextmanager
def fake_printer(env, content=FAKE_PDF):
    """Render every report as :data:`FAKE_PDF`, or fail when content is None."""

    def _render_pdf(self, report, record):
        if content is None:
            raise ValueError('the printer is on fire')
        return content, 'pdf'

    with patch.object(type(env['kmessage.document']), '_render_pdf', _render_pdf):
        yield


class KMessageAccountCommon(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # Whoever sets a connection up is a K-Message administrator; the
        # accounting user these tests run as is not one by default.
        cls.env.user.groups_id |= cls.env.ref('kmessage_connector.group_kmessage_manager')

        # One connection per company is the rule, so a database that already
        # has one is worked with rather than fought.
        cls.connection = cls.env['kmessage.account']._for_company(cls.company)
        if not cls.connection:
            cls.connection = cls.env['kmessage.account'].create({
                'name': 'K-Message test',
                'company_id': cls.company.id,
                'base_url': 'https://example.invalid',
                'api_key': 'test-key',
            })
        cls.connection.write({
            'state': 'connected',
            'outbound_enabled': True,
            'inbound_enabled': True,
            'dry_run': False,
            'prerender_invoice_pdf': False,
        })

        cls.template = cls.env['kmessage.template'].create({
            'account_id': cls.connection.id,
            'name': 'invoice_posted',
            'language': 'en',
            'status': 'APPROVED',
            'header_type': 'TEXT',
            'body_content': 'Hello {{1}}, invoice {{2}} is ready.',
        })

        cls.customer = cls.partner_a
        cls.customer.write({'mobile': '+966500000011'})
        cls.customer_phone = '0500000011'
        cls.other_customer = cls.partner_b
        cls.other_customer.write({'mobile': '+966500000022'})

    @classmethod
    def _other_company(cls):
        """A second company, however the accounting test base offers one.

        Odoo 17 sets one up for every accounting test; 18 asks for it by name.
        """
        data = getattr(cls, 'company_data_2', None)
        if data is None:
            data = cls.setup_other_company()
        return data['company']

    @classmethod
    def _rule(cls, trigger, **values):
        """A live automation on ``trigger``, sending the test template."""
        return cls.env['kmessage.automation'].create(dict({
            'name': 'Test rule',
            'account_id': cls.connection.id,
            'trigger': trigger,
            'template_id': cls.template.id,
            'attach_document': False,
            'active': True,
        }, **values))

    def _invoice(self, partner=None, post=True, amount=100.0, move_type='out_invoice'):
        invoice = self.init_invoice(
            move_type, partner=partner or self.customer, amounts=[amount], company=self.company)
        if post:
            invoice.action_post()
        return invoice

    def _messages_about(self, record):
        return self.env['kmessage.message'].search([
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
        ])
