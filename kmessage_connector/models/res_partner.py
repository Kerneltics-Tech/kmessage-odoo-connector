# -*- coding: utf-8 -*-
"""Finding the customer behind a WhatsApp number — quickly and only once.

The stored key is the point. Without it, answering “who is 966512345678?”
means scanning every partner and comparing digits in Python, which is slow at
ten thousand partners and wrong at a hundred thousand. With it, the question is
an index lookup, and the same key is what the search uses, so a number that
matches in the list view is the number that matches in the API.
"""

from odoo import _, api, fields, models

from ..tools.compat import LIST_FORM
from ..tools.phone import match_key, normalize


class ResPartner(models.Model):
    _inherit = 'res.partner'

    kmessage_phone_key = fields.Char(
        string='WhatsApp key', compute='_compute_kmessage_phone_key',
        store=True, index=True, compute_sudo=True,
        help="The last digits of this customer's number, used to match incoming WhatsApp messages.")
    kmessage_opt_out = fields.Boolean(
        string='No WhatsApp',
        help="This customer is never messaged on WhatsApp, whatever the automations say.")
    kmessage_message_ids = fields.One2many('kmessage.message', 'partner_id')
    kmessage_message_count = fields.Integer(compute='_compute_kmessage_message_count')

    @api.depends('mobile', 'phone')
    def _compute_kmessage_phone_key(self):
        for partner in self:
            partner.kmessage_phone_key = match_key(partner.mobile or partner.phone or '')

    def _compute_kmessage_message_count(self):
        counts = {}
        if self.ids:
            grouped = self.env['kmessage.message'].sudo()._read_group(
                [('partner_id', 'in', self.ids)], ['partner_id'], ['__count'])
            counts = {partner.id: count for partner, count in grouped}
        for partner in self:
            partner.kmessage_message_count = counts.get(partner.id, 0)

    @api.model
    def _kmessage_find_by_phone(self, phone, company=None):
        """The single customer that number belongs to, or an empty recordset.

        Two partners with the same number is not a match: answering with
        somebody's invoices because a number was reused would be worse than
        answering with nothing. Company contacts are preferred over their
        children, so a person's invoices are found under the company they
        belong to.
        """
        key = match_key(phone)
        if not key:
            return self.browse()

        domain = [('kmessage_phone_key', '=', key)]
        if company:
            domain += ['|', ('company_id', '=', False), ('company_id', '=', company.id)]
        matches = self.sudo().search(domain, limit=5)
        if not matches:
            return self.browse()
        if len(matches) == 1:
            return matches

        commercial = matches.mapped('commercial_partner_id')
        if len(commercial) == 1:
            return commercial
        return self.browse()

    def _kmessage_number(self):
        """The number to message this partner on, in international digits."""
        self.ensure_one()
        code = (self.company_id or self.env.company).country_id.phone_code
        return normalize(self.mobile or self.phone or '', code)

    def action_kmessage_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("WhatsApp messages"),
            'res_model': 'kmessage.message',
            'view_mode': LIST_FORM,
            'domain': [('partner_id', '=', self.id)],
        }
