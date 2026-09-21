# -*- coding: utf-8 -*-
"""The private tokens K-Message presents when it asks Odoo something."""

import hashlib
import hmac
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TOKEN_PREFIX = 'kmc_'


def _hash(raw_token):
    return hashlib.sha256((raw_token or '').encode()).hexdigest()


class KMessageToken(models.Model):
    _name = 'kmessage.token'
    _description = 'K-Message Access Token'
    _order = 'create_date desc'

    name = fields.Char(
        required=True, default='K-Message',
        help="What this token is for — 'K-Message production', 'test flow', and so on.")
    active = fields.Boolean(
        default=True,
        help="Switching this off stops K-Message using this token immediately.")
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company,
        help="The company whose data this token can reach.")

    # The token itself is never stored. What is stored cannot be turned back
    # into it, so a database leak does not hand anyone access to this Odoo.
    token_hash = fields.Char(readonly=True, copy=False, index=True, groups='base.group_system')
    token_hint = fields.Char(
        string='Token', readonly=True, copy=False,
        help="The first characters, so you can tell two tokens apart. The rest is shown only once.")

    capability_ids = fields.Many2many(
        'kmessage.capability', string='May use',
        help="Exactly what this token is allowed to ask for. Anything not ticked is refused.")
    expires_on = fields.Date(
        help="After this date the token stops working. Leave empty for no expiry.")
    last_used = fields.Datetime(readonly=True, copy=False)
    use_count = fields.Integer(readonly=True, copy=False, default=0)
    rate_limit = fields.Integer(
        default=120,
        help="Most requests per minute allowed on this token. 0 means no limit.")

    state = fields.Selection(
        [('active', 'Active'), ('expired', 'Expired'), ('revoked', 'Revoked')],
        compute='_compute_state')

    @api.depends('active', 'expires_on')
    def _compute_state(self):
        today = fields.Date.context_today(self)
        for token in self:
            if not token.active:
                token.state = 'revoked'
            elif token.expires_on and token.expires_on < today:
                token.state = 'expired'
            else:
                token.state = 'active'

    # -- issuing ----------------------------------------------------------
    @api.model
    def issue(self, name, company=None, capabilities=None, expires_on=None):
        """Create a token and return ``(record, raw_token)``.

        The raw token exists only in this return value: it is hashed on the way
        into the database and there is no way to read it back afterwards.
        """
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        record = self.create({
            'name': name,
            'company_id': (company or self.env.company).id,
            'token_hash': _hash(raw),
            'token_hint': raw[:len(TOKEN_PREFIX) + 6] + '…',
            'capability_ids': [(6, 0, (capabilities or self.env['kmessage.capability']).ids)],
            'expires_on': expires_on,
        })
        return record, raw

    def regenerate(self):
        """Replace the secret behind this token and return the new raw value.

        Used where the caller has somewhere to put the value immediately — such
        as publishing an assistant tool, which must send the secret it is about
        to stop being able to read.
        """
        self.ensure_one()
        raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
        self.sudo().write({'token_hash': _hash(raw), 'token_hint': raw[:len(TOKEN_PREFIX) + 6] + '…'})
        return raw

    def action_regenerate(self):
        """Replace the secret and show it once."""
        self.ensure_one()
        return self.env['kmessage.token.reveal'].show(self, self.regenerate())

    def action_revoke(self):
        self.write({'active': False})

    # -- checking ---------------------------------------------------------
    @api.model
    def authenticate(self, raw_token):
        """The token record for ``raw_token``, or an empty recordset.

        The lookup is by hash so the comparison happens in the index, and the
        result is re-checked in constant time to keep the timing of a wrong
        token indistinguishable from a right one.
        """
        if not raw_token or not raw_token.startswith(TOKEN_PREFIX):
            return self.browse()
        digest = _hash(raw_token)
        candidates = self.sudo().search([('token_hash', '=', digest)], limit=2)
        for candidate in candidates:
            if hmac.compare_digest(candidate.token_hash or '', digest):
                if candidate.state != 'active':
                    return self.browse()
                return candidate
        return self.browse()

    def allows(self, capability_code):
        """True when this token may use the capability, and the capability is on."""
        self.ensure_one()
        capability = self.capability_ids.filtered(lambda c: c.code == capability_code)
        return bool(capability) and capability.active

    def note_use(self):
        """Record that the token was used. Cheap on purpose: one write, no read."""
        self.ensure_one()
        self.sudo().write({
            'last_used': fields.Datetime.now(),
            'use_count': (self.use_count or 0) + 1,
        })

    def unlink(self):
        if any(token.use_count for token in self):
            raise UserError(_(
                "A token that has been used is revoked, not deleted, so its history stays readable."))
        return super().unlink()
