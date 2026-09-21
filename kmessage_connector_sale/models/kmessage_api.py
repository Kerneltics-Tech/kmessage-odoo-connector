# -*- coding: utf-8 -*-
"""Answering “where is my order?” without answering it for somebody else.

Both capabilities start from the phone number K-Message is talking to and search
only inside the partners that number belongs to. ``order_status`` takes an order
number as well, and that number is a filter, never a lookup key: an order that
exists but belongs to another customer is refused in exactly the same words as
one that does not exist at all, because telling the two apart is itself a fact
about someone else's business.

What comes back is written for a person reading a WhatsApp message — a state is
a word rather than an Odoo key, a delivery is a short phrase — and it stops at
what the customer already agreed to buy. No margins, no internal notes, no
salesperson's remarks.
"""

from odoo import _, api, fields, models

from odoo.addons.kmessage_connector.models.kmessage_api import KMessageApiError

#: The states a customer would call open. ``sale`` belongs here: an order still
#: being delivered is very much open to the person waiting for it.
#: A draft quotation is a salesperson's working copy: priced but not offered,
#: and routinely changed or abandoned before anyone means the customer to see
#: it. Reading one out as "your quotation" commits the company to a number it
#: never sent. Only what was actually sent, or confirmed, is the customer's
#: business — the same line Odoo's own portal draws.
CUSTOMER_STATES = ('sent', 'sale')

#: What "still open" means to a customer asking after their own orders.
OPEN_STATES = CUSTOMER_STATES

#: Most order lines described in one answer. A longer list is unreadable in a
#: chat, and ``line_count`` is there so a short list can still be honest.
MAX_LINES = 5


def _argument(value):
    """What the assistant actually passed, or nothing at all.

    K-Message substitutes every declared parameter into the request body, so an
    argument the assistant left out arrives as its own ``{{args.x}}``
    placeholder rather than as a missing key. That is an absent argument, not a
    value: read as one it turns “show me my orders” into a refusal, and an
    unnamed order into “there is no order of yours with that number”.
    """
    text = str(value or '').strip()
    return '' if text.startswith('{{') else text


class KMessageApi(models.AbstractModel):
    _inherit = 'kmessage.api'

    # -- the capabilities -------------------------------------------------
    @api.model
    def _handle_orders(self, payload, capability):
        """This customer's recent orders, newest first."""
        partner = self._partner_from(payload)
        orders = self.env['sale.order'].sudo().search(
            self._order_domain(partner)
            + [('state', 'in', CUSTOMER_STATES + ('cancel',))]
            + self._state_domain(payload.get('state')),
            order='date_order desc, id desc',
            limit=self._limit(payload, capability),
        )
        return {'rows': [self._order_row(order) for order in orders]}

    @api.model
    def _handle_order_status(self, payload, capability):
        """One order of this customer's, named by its number."""
        partner = self._partner_from(payload)
        number = _argument(payload.get('order') or payload.get('number'))
        if not number:
            raise KMessageApiError(
                'order_required', _("Send the order number to look up."), 400)

        # `=ilike` takes a pattern, and a number carrying a % or a _ would
        # match some other order of theirs, which would then be answered as
        # though it were the one they asked about.
        pattern = number.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        order = self.env['sale.order'].sudo().search(
            self._order_domain(partner)
            + [('state', 'in', CUSTOMER_STATES + ('cancel',)), ('name', '=ilike', pattern)],
            limit=1)
        if not order:
            raise KMessageApiError(
                'order_not_found',
                _("There is no order of yours with that number."), 404)

        row = self._order_row(order)
        row.update(self._order_detail(order))
        return {'rows': [row]}

    @api.model
    def _customer_extras(self, partner, company):
        extras = super()._customer_extras(partner, company)
        extras['open_orders'] = self.env['sale.order'].sudo().search_count(
            self._order_domain(partner, company) + [('state', 'in', OPEN_STATES)])
        return extras

    # -- what counts as this customer's -----------------------------------
    @api.model
    def _order_domain(self, partner, company=None):
        """Their orders, in this company's books, and nothing else.

        The scope is the commercial partner, as it is for invoices and for
        deliveries: an order placed in the name of a company belongs to that
        company, and whichever of its contacts is on WhatsApp is asking about
        it. Starting from the matched contact instead would leave a buyer at a
        company able to see their invoices and their parcels but none of the
        orders those came from.

        Matched on the commercial entity itself rather than with ``child_of``.
        A contact or a delivery address filed under a company shares that
        company's commercial entity and is the same customer; a *subsidiary*
        filed under a parent is not — Odoo gives it its own commercial entity
        and its own ledger, and ``child_of`` would hand a parent group every
        subsidiary's orders.

        The company clause is not decoration — these searches run as the
        connector, so the record rules that usually keep companies apart are
        not there to do it for us.
        """
        return [
            ('partner_id.commercial_partner_id', '=', partner.commercial_partner_id.id),
            ('company_id', '=', (company or self.env.company).id),
        ]

    @api.model
    def _state_domain(self, wanted):
        """The optional ``state`` filter, in the same words we answer with."""
        wanted = _argument(wanted)
        if not wanted:
            return []
        # 17.0 and 18.0 both retired the old 'done' state and kept a `locked`
        # flag in its place, so "done" is read off two fields rather than one.
        domains = {
            'open': [('state', 'in', OPEN_STATES)],
            'quotation': [('state', '=', 'sent')],
            'confirmed': [('state', '=', 'sale'), ('locked', '=', False)],
            'done': [('state', '=', 'sale'), ('locked', '=', True)],
            'cancelled': [('state', '=', 'cancel')],
        }
        domain = domains.get(wanted.lower())
        if domain is None:
            raise KMessageApiError(
                'bad_state',
                _("Ask for open, quotation, confirmed, done or cancelled orders."), 400)
        return domain

    # -- how an order reads -----------------------------------------------
    @api.model
    def _order_row(self, order):
        money = self._money(order.amount_total, order.currency_id)
        return {
            'number': order.name,
            'date': fields.Date.to_string(order.date_order.date()) if order.date_order else '',
            'total': money['amount'],
            'currency': money['currency'],
            'state': self._order_state_word(order),
            'delivery': self._order_delivery_word(order),
        }

    @api.model
    def _order_state_word(self, order):
        """Odoo's state key as the customer themselves would say it."""
        if order.state == 'sale':
            return _("done") if order.locked else _("confirmed")
        return {
            'draft': _("quotation"),
            'sent': _("quotation"),
            'cancel': _("cancelled"),
        }.get(order.state, order.state)

    @api.model
    def _order_delivery_word(self, order):
        """A phrase about the delivery, or nothing at all.

        ``delivery_status`` arrives with `sale_stock`, which this module does
        not depend on: a company selling services has no deliveries to report
        and should not have to install a warehouse to say so.
        """
        if 'delivery_status' not in order._fields:
            return ''
        return {
            'pending': _("not delivered yet"),
            'started': _("on its way"),
            'partial': _("partly delivered"),
            'full': _("delivered"),
        }.get(order.delivery_status, '')

    @api.model
    def _order_detail(self, order):
        """What is on the order — the part the customer already agreed to."""
        lines = order.order_line.filtered(lambda line: not line.display_type)
        return {
            'lines': [
                {
                    'product': line.product_id.name or line.name,
                    'quantity': line.product_uom_qty,
                }
                for line in lines[:MAX_LINES]
            ],
            'line_count': len(lines),
        }
