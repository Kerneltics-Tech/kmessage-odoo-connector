# -*- coding: utf-8 -*-
"""Showing a freshly issued token — once, and never again."""

from odoo import _, fields, models


class KMessageTokenReveal(models.TransientModel):
    _name = 'kmessage.token.reveal'
    _description = 'Your new token'

    token_id = fields.Many2one('kmessage.token', readonly=True)
    raw_token = fields.Char(
        string='Token', readonly=True,
        help="Copy this into K-Message now. Odoo stores only a fingerprint of it, "
             "so this is the last time it can be read.")

    def show(self, token, raw_token):
        """Open the one-time view of ``raw_token``."""
        wizard = self.create({'token_id': token.id, 'raw_token': raw_token})
        return {
            'type': 'ir.actions.act_window',
            'name': _("Your new token"),
            'res_model': self._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }
