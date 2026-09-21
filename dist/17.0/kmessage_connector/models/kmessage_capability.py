# -*- coding: utf-8 -*-
"""One row per thing K-Message may ask this Odoo for, each with a switch."""

from odoo import _, api, fields, models


class KMessageCapability(models.Model):
    _name = 'kmessage.capability'
    _description = 'K-Message Capability'
    _order = 'sequence, code'

    sequence = fields.Integer(default=10)
    code = fields.Char(
        required=True, index=True,
        help="The name the endpoint is addressed by. Changing it breaks whatever already calls it.")
    name = fields.Char(required=True, translate=True)
    description = fields.Text(translate=True)
    active = fields.Boolean(
        default=True,
        help="Off means every request for this is refused, with a clear reason. "
             "Nothing else about the connection changes.")

    endpoint = fields.Char(
        readonly=True,
        help="The path K-Message calls, relative to this Odoo.")
    method = fields.Selection(
        [('GET', 'GET'), ('POST', 'POST')], default='POST', readonly=True)

    personal = fields.Boolean(
        readonly=True,
        help="Answers about one identified customer. These always require a phone "
             "number and only ever return that person's own records.")
    max_rows = fields.Integer(
        default=10,
        help="Most rows returned in one answer. A conversation cannot read a long list, "
             "and a long list is how an accident becomes an export.")
    requires_module = fields.Char(
        readonly=True,
        help="The Odoo app this needs. Capabilities whose app is not installed stay unavailable.")
    available = fields.Boolean(
        compute='_compute_available',
        help="Whether the Odoo side of this can work at all right now.")

    use_count = fields.Integer(readonly=True, default=0, copy=False)
    last_used = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        ('code_unique', 'unique(code)', 'Two capabilities cannot share a code.'),
    ]

    @api.depends('requires_module')
    def _compute_available(self):
        installed = set(self.env['ir.module.module'].sudo().search([
            ('state', '=', 'installed'),
        ]).mapped('name'))
        for capability in self:
            needed = capability.requires_module
            capability.available = (not needed) or needed in installed

    @api.model
    def of(self, code):
        """The capability with this code, whether or not it is switched on."""
        return self.sudo().with_context(active_test=False).search([('code', '=', code)], limit=1)

    def note_use(self):
        self.ensure_one()
        self.sudo().write({
            'use_count': (self.use_count or 0) + 1,
            'last_used': fields.Datetime.now(),
        })

    def action_toggle(self):
        for capability in self:
            capability.active = not capability.active
