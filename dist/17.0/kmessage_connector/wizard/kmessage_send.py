# -*- coding: utf-8 -*-
"""Sending one document by hand, from the record it belongs to."""

import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class KMessageSend(models.TransientModel):
    _name = 'kmessage.send'
    _description = 'Send on WhatsApp'

    account_id = fields.Many2one(
        'kmessage.account', required=True,
        default=lambda self: self.env['kmessage.account']._for_company())
    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    record_name = fields.Char(readonly=True)

    partner_id = fields.Many2one('res.partner', required=True)
    phone = fields.Char(required=True, help="The number the message goes to.")
    opted_out = fields.Boolean(related='partner_id.kmessage_opt_out', readonly=True)

    template_id = fields.Many2one(
        'kmessage.template', required=True,
        domain="[('account_id', '=', account_id), ('usable', '=', True)]")
    attach_document = fields.Boolean(string='Attach the PDF', default=True)
    preview = fields.Text(compute='_compute_preview', readonly=True)
    param_json = fields.Text(
        string='Parameters', default='{}',
        help='The template placeholders, as {"1": "…", "2": "…"}.')

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        context = self.env.context
        model = context.get('active_model') or context.get('default_res_model')
        res_id = context.get('active_id') or context.get('default_res_id')
        if model and res_id:
            record = self.env[model].browse(res_id)
            partner = record.partner_id if 'partner_id' in record._fields else self.env['res.partner']
            values.update({
                'res_model': model,
                'res_id': res_id,
                'record_name': record.display_name,
                'partner_id': partner.id,
                'phone': partner._kmessage_number() if partner else '',
            })
        return values

    @api.depends('template_id', 'param_json')
    def _compute_preview(self):
        for wizard in self:
            try:
                params = json.loads(wizard.param_json or '{}')
            except ValueError:
                params = {}
            wizard.preview = wizard.template_id.preview(params) if wizard.template_id else ''

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        for wizard in self:
            if wizard.partner_id:
                wizard.phone = wizard.partner_id._kmessage_number()

    def action_send(self):
        self.ensure_one()
        if self.opted_out:
            raise UserError(_("%s has asked not to be messaged on WhatsApp.", self.partner_id.display_name))
        try:
            params = json.loads(self.param_json or '{}')
        except ValueError:
            raise UserError(_("The parameters are not valid JSON."))

        record = self.env[self.res_model].browse(self.res_id)
        attachment = None
        if self.attach_document and self.template_id.takes_document:
            attachment = self.env['kmessage.document']._render(
                record, lang=(self.partner_id.lang or '').split('_')[0] or None)

        message = self.env['kmessage.message'].enqueue(
            account=self.account_id,
            phone=self.phone,
            template=self.template_id,
            params={str(key): str(value) for key, value in params.items()},
            partner=self.partner_id,
            record=record,
            attachment=attachment,
        )
        if not message:
            raise UserError(_("Nothing was queued — check the number and that sending is switched on."))

        message.send_now()
        if message.state == 'failed':
            raise UserError(_("K-Message refused it: %s", message.error))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Sent"),
                'message': _("The message is on its way to %s.", self.partner_id.display_name),
                'type': 'success',
            },
        }
