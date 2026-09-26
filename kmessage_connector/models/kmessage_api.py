# -*- coding: utf-8 -*-
"""What K-Message is allowed to ask this Odoo, and how it is answered.

The rule this file exists to enforce, taken from how K-Message treats its own
tools: **identity is never a parameter**. Every personal capability is given a
phone number — the one from the live conversation — and answers only for the
customer that number belongs to. There is no capability that takes a customer
id, and none that lists other people's records, so no sentence a customer can
type reaches somebody else's invoices.

Capabilities are looked up by code and handled by ``_handle_<code>``; a bridge
module adds a capability by adding its data row and its handler. Core ships the
three that need no other app installed.
"""

import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class KMessageFile(object):
    """A file answer rather than a JSON one.

    K-Message's document tool fetches a URL and expects the document itself
    back — it sniffs the bytes (a PDF must really start with ``%PDF-``) and
    hands the file to WhatsApp without ever showing it to the model. A handler
    returns one of these and the controller streams it.
    """

    __slots__ = ('content', 'filename', 'mimetype')

    def __init__(self, content, filename, mimetype='application/pdf'):
        self.content = content
        self.filename = filename
        self.mimetype = mimetype


class KMessageApiError(Exception):
    """A refusal with a stable machine code, safe to show a caller."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class KMessageApi(models.AbstractModel):
    _name = 'kmessage.api'
    _description = 'K-Message Inbound API'

    # -- dispatch ---------------------------------------------------------
    @api.model
    def dispatch(self, code, payload, token):
        """Run one capability. Raises :class:`KMessageApiError` on refusal."""
        capability = self.env['kmessage.capability'].of(code)
        if not capability:
            raise KMessageApiError('unknown_capability', _("There is no such capability here."), 404)
        if not capability.active:
            raise KMessageApiError(
                'capability_disabled',
                _("“%s” is switched off in Odoo.", capability.name), 403)
        if not capability.available:
            raise KMessageApiError(
                'capability_unavailable',
                _("“%s” needs an Odoo app that is not installed.", capability.name), 503)
        if not token.allows(code):
            raise KMessageApiError(
                'not_permitted', _("This token may not use “%s”.", capability.name), 403)

        handler = getattr(self, '_handle_%s' % code.replace('.', '_'), None)
        if not handler:
            raise KMessageApiError('not_implemented', _("Nothing here answers that yet."), 501)

        result = handler(payload or {}, capability)
        capability.note_use()
        # Counted here rather than in each handler: a flow that wants to say
        # "you have three invoices" should not depend on every bridge author
        # having remembered the same key.
        if isinstance(result, dict) and 'rows' in result and 'count' not in result:
            result['count'] = len(result['rows'])
        return result

    # -- shared helpers ---------------------------------------------------
    @api.model
    def _partner_from(self, payload, required=True):
        """The customer the request is about, found from their phone number."""
        phone = (payload.get('phone') or payload.get('phone_number') or '').strip()
        if not phone:
            if not required:
                return self.env['res.partner']
            raise KMessageApiError('phone_required', _("Send the customer's phone number."), 400)
        partner = self.env['res.partner']._kmessage_find_by_phone(phone, self.env.company)
        if not partner and required:
            raise KMessageApiError(
                'customer_not_found',
                _("No customer here has that number."), 404)
        return partner

    @api.model
    def _limit(self, payload, capability):
        """How many rows to return: what was asked, capped by what is allowed."""
        try:
            asked = int(payload.get('limit') or 0)
        except (TypeError, ValueError):
            asked = 0
        ceiling = capability.max_rows or 10
        return min(asked, ceiling) if asked > 0 else ceiling

    @api.model
    def _money(self, amount, currency):
        return {
            'amount': round(amount or 0.0, currency.decimal_places if currency else 2),
            'currency': currency.name if currency else '',
        }

    # -- the capabilities core can answer on its own ----------------------
    @api.model
    def _handle_ping(self, payload, capability):
        """Proof of life, and an honest list of what is switched on."""
        accounts = self.env['kmessage.account']._all_for_company()
        capabilities = self.env['kmessage.capability'].sudo().search([])
        return {
            'odoo': True,
            'company': self.env.company.display_name,
            'connector_version': '1.0.0',
            'outbound_enabled': any(accounts.mapped('outbound_enabled')),
            'inbound_enabled': any(accounts.mapped('inbound_enabled')),
            'capabilities': [
                {'code': row.code, 'name': row.name, 'enabled': row.active and row.available}
                for row in capabilities
            ],
        }

    @api.model
    def _handle_customer_lookup(self, payload, capability):
        """Is this number a customer here, and what should an agent know first."""
        partner = self._partner_from(payload)
        company = self.env.company
        result = {
            'found': True,
            'name': partner.display_name,
            'language': (partner.lang or '').split('_')[0],
            'city': partner.city or '',
            'since': fields.Date.to_string(partner.create_date.date()) if partner.create_date else '',
            'is_company': partner.is_company,
            'opted_out': partner.kmessage_opt_out,
        }
        # Balance is only meaningful where accounting is installed; the bridge
        # fills it in when it is.
        result.update(self._customer_extras(partner, company))
        return result

    @api.model
    def _customer_extras(self, partner, company):
        """Extra facts about a customer. Bridges add to this."""
        return {}

    @api.model
    def _handle_tools(self, payload, capability):
        """A description of this Odoo's API, for whoever wires it up.

        Written as plain JSON rather than a spec format: what a person pasting
        it into a flow builder needs is the path, what to send and what comes
        back, and a schema they must first convert helps nobody.
        """
        base = (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')
        rows = []
        for capability_row in self.env['kmessage.capability'].sudo().search([]):
            if not (capability_row.active and capability_row.available):
                continue
            rows.append({
                'code': capability_row.code,
                'name': capability_row.name,
                'description': capability_row.description or '',
                'method': capability_row.method,
                'url': '%s%s' % (base, capability_row.endpoint or ''),
                'needs_phone': capability_row.personal,
                'max_rows': capability_row.max_rows,
            })
        return {
            'service': 'Odoo — %s' % self.env.company.display_name,
            'auth': {'type': 'bearer', 'header': 'Authorization', 'format': 'Bearer <token>'},
            'note': _(
                "Every personal request must carry the customer's phone number, "
                "and answers only ever concern that customer."),
            'capabilities': rows,
        }
