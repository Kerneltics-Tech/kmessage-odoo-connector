# -*- coding: utf-8 -*-
"""The invoice templates, written so a company need not write them."""

from odoo import api, models


class KMessageStarter(models.AbstractModel):
    _inherit = 'kmessage.starter'

    @api.model
    def catalogue(self):
        entries = super().catalogue()
        entries.append(self._entry(
            name='odoo_invoice_ready',
            display_name='Odoo — invoice ready',
            body='مرحباً {{1}}،\n\nفاتورتك رقم {{2}} بمبلغ {{3}} جاهزة، ومرفقة أعلاه.\n\nشكراً لك.',
            samples=['عبدالله', 'INV/2026/0001', '295.00 ر.س'],
        ))
        entries.append(self._entry(
            name='odoo_payment_received',
            display_name='Odoo — payment received',
            body='شكراً {{1}}،\n\nاستلمنا دفعتك بمبلغ {{2}} على الفاتورة رقم {{3}}، وحسابك محدّث الآن.',
            samples=['عبدالله', '295.00 ر.س', 'INV/2026/0001'],
            header_type=None,
        ))
        return entries

    @api.model
    def _sample_from_odoo(self, account):
        """A real posted invoice, printed — what Meta will actually review."""
        invoice = self.env['account.move'].sudo().search([
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('company_id', '=', account.company_id.id),
        ], order='id desc', limit=1)
        if not invoice:
            return super()._sample_from_odoo(account)
        try:
            attachment = self.env['kmessage.document']._render(invoice)
        except Exception:  # noqa: BLE001 - a sample is worth no risk at all
            return super()._sample_from_odoo(account)
        if not attachment or not attachment.raw:
            return super()._sample_from_odoo(account)
        return ('invoice-sample.pdf', attachment.raw, 'application/pdf')
