# -*- coding: utf-8 -*-
"""The two questions an inventory lets K-Message answer.

“Do you have it?” and “where is my delivery?” are what a shop is asked all day,
and neither is reachable through K-Message's own Odoo tools: those read stock
over XML-RPC and cannot see ``stock.picking`` at all. Answering them is the
whole reason this bridge exists.

The two are opposites in one respect worth naming. Availability is shop
information — it belongs to the shelf, not to a person, so anybody may ask it
and no phone number is involved. A delivery belongs to exactly one customer, so
that capability keeps the rule the rest of the connector keeps: the number comes
from the conversation, and the answer only ever concerns the person it belongs
to. There is no way to ask after somebody else's parcel.

What availability says is deliberately vague by default. See
``show_exact_quantity`` on the capability for why a shop usually does not want
its exact figures read out over WhatsApp.
"""

from collections import defaultdict

from odoo import _, api, models
from odoo.tools import float_compare, float_round

from odoo.addons.kmessage_connector.models.kmessage_api import KMessageApiError

#: At or below this, availability is “only a few left” rather than “in stock”.
#: A customer told “in stock” about the last one arrives to find it gone, which
#: costs more goodwill than the vaguer answer ever saves.
LOW_STOCK = 5.0

#: Most internal locations read while working out which branch somebody meant.
#: A word that matches more places than this is not the name of a branch, and
#: reading every location in a large warehouse to discover that helps nobody.
MAX_LOCATIONS_SCANNED = 200


def _given(value):
    """What the caller actually said, or ``''`` when they said nothing.

    An assistant that leaves an optional argument out does not send nothing:
    K-Message substitutes what it has, and an unfilled argument arrives as the
    ``{{args.reference}}`` placeholder itself. Read as a search term it turns
    “where is my delivery” into a question about a delivery called
    “{{args.reference}}”, which nobody has.
    """
    text = (value or '').strip()
    return '' if text.startswith('{{') else text


def _literally(term):
    """``term`` as text to match, not as a pattern of its own.

    Whatever the customer typed reaches the domain, and ``%`` and ``_`` are
    wildcards to Postgres: “%” on its own matches the first product in the
    database, and answering it as though somebody had asked about that product
    is how a stock figure reaches a person who never named it.
    """
    return term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class KMessageApi(models.AbstractModel):
    _inherit = 'kmessage.api'

    # -- is it in stock ---------------------------------------------------
    @api.model
    def _handle_stock_check(self, payload, capability):
        """What is on the shelf for a product, branch by branch."""
        term = _given(payload.get('product'))
        if not term:
            raise KMessageApiError(
                'product_required', _("Say which product to look for."), 400)

        company = self.env.company
        product = self._kmessage_stock_product(term, company)

        branch = _given(payload.get('branch'))
        warehouses = self._kmessage_stock_warehouses(branch, company)
        if not warehouses:
            if branch:
                raise KMessageApiError(
                    'branch_not_found',
                    _("There is no branch here called “%s”.", branch), 404)
            raise KMessageApiError(
                'no_warehouse', _("This company does not keep stock anywhere yet."), 404)

        free = self._kmessage_stock_free(product, warehouses, company)
        rounding = product.uom_id.rounding or 0.01

        # Most first, because a conversation reads the first line and stops.
        ranked = sorted(
            ((warehouse, free.get(warehouse.id, 0.0)) for warehouse in warehouses),
            key=lambda pair: (-pair[1], pair[0].name or ''))

        rows = []
        for warehouse, quantity in ranked[:self._limit(payload, capability)]:
            row = {
                'location': warehouse.name,
                'level': self._kmessage_stock_level(quantity, rounding),
            }
            if capability.show_exact_quantity:
                # A branch that over-delivered carries a negative figure in the
                # books. “Minus three” is not an answer to “do you have it”, and
                # the band above already says out of stock.
                row['available'] = max(
                    0.0, float_round(quantity, precision_rounding=rounding))
            rows.append(row)

        return {'product': product.display_name, 'rows': rows}

    @api.model
    def _kmessage_stock_level(self, quantity, rounding):
        """The band a quantity falls in, which is all most shops want said."""
        if float_compare(quantity, 0.0, precision_rounding=rounding) <= 0:
            return _("out of stock")
        if float_compare(quantity, LOW_STOCK, precision_rounding=rounding) <= 0:
            return _("only a few left")
        return _("in stock")

    @api.model
    def _kmessage_stock_product(self, term, company):
        """The product somebody means by ``term``, or a refusal saying why not.

        Searched the way a person looks: the code as typed, then the name as
        typed, then anything containing it. Only products Odoo keeps a quantity
        for can be answered at all — calling a service “out of stock” would be
        a confident wrong answer, and those are the expensive kind.
        """
        products = self.env['product.product']
        pattern = _literally(term)
        scope = [('company_id', 'in', [False, company.id])]
        # Odoo 18 replaced the 'product' product type with an is_storable flag.
        stocked = (
            [('is_storable', '=', True)] if 'is_storable' in products._fields
            else [('type', '=', 'product')]
        )

        for attempt in (
            [('default_code', '=ilike', pattern)],
            [('name', '=ilike', pattern)],
            ['|', ('default_code', 'ilike', pattern), ('name', 'ilike', pattern)],
        ):
            match = products.search(attempt + stocked + scope, limit=1)
            if match:
                return match

        untracked = products.search(
            ['|', ('default_code', 'ilike', pattern), ('name', 'ilike', pattern)] + scope, limit=1)
        if untracked:
            raise KMessageApiError(
                'product_not_stocked',
                _("We do not keep a stock figure for “%s”.", untracked.display_name), 404)
        raise KMessageApiError(
            'product_not_found', _("Nothing here is called “%s”.", term), 404)

    @api.model
    def _kmessage_stock_warehouses(self, branch, company):
        """The branches to report on: the one asked for, or all of them."""
        warehouses = self.env['stock.warehouse'].search([('company_id', '=', company.id)])
        if not branch:
            return warehouses

        wanted = branch.casefold()
        named = warehouses.filtered(
            lambda warehouse: wanted in (warehouse.name or '').casefold()
            or wanted == (warehouse.code or '').casefold())
        if named:
            return named

        # A customer's “branch” is as often a place inside one — a shop floor,
        # a back room — as it is the warehouse's own name. Searched inside this
        # company's own warehouses and no further: the question is which of
        # them was meant, and every other location in the database is both an
        # answer to nothing and a table scan.
        locations = self.env['stock.location'].search([
            ('warehouse_id', 'in', warehouses.ids),
            ('usage', '=', 'internal'),
            ('complete_name', 'ilike', _literally(branch)),
        ], limit=MAX_LOCATIONS_SCANNED)
        return locations.warehouse_id

    @api.model
    def _kmessage_stock_free(self, product, warehouses, company):
        """Quantity free to sell per warehouse: on hand, less what is reserved.

        Grouped by location and folded into warehouses here rather than in the
        database, because a quant's ``warehouse_id`` is a related field that is
        not stored and so cannot be grouped on.
        """
        groups = self.env['stock.quant']._read_group(
            [
                ('product_id', '=', product.id),
                ('location_id.usage', '=', 'internal'),
                ('location_id.warehouse_id', 'in', warehouses.ids),
                ('company_id', 'in', [False, company.id]),
            ],
            ['location_id'],
            ['quantity:sum', 'reserved_quantity:sum'],
        )
        free = defaultdict(float)
        for location, quantity, reserved in groups:
            free[location.warehouse_id.id] += (quantity or 0.0) - (reserved or 0.0)
        return free

    # -- where is my delivery ---------------------------------------------
    @api.model
    def _handle_delivery_status(self, payload, capability):
        """That customer's recent deliveries, newest first."""
        partner = self._partner_from(payload)
        pickings = self.env['stock.picking']

        domain = [
            ('picking_type_id.code', '=', 'outgoing'),
            # The commercial entity itself, not everything under it. A contact
            # or a delivery address below a company is the same customer and
            # may ask after the company's parcels. A *company* below a company
            # is not: Odoo gives it its own commercial entity and its own
            # invoices, and `child_of` would hand a parent group every
            # subsidiary's deliveries.
            ('partner_id.commercial_partner_id', '=', partner.commercial_partner_id.id),
            ('company_id', '=', self.env.company.id),
            ('state', '!=', 'draft'),
        ]
        reference = _given(payload.get('reference'))
        if reference:
            # Customers quote whichever number they were given: ours or the
            # order it came from. A number is text to match, never a pattern:
            # a stray % would quietly answer about a different delivery of
            # theirs as though it were the one they asked after.
            quoted = _literally(reference)
            domain += ['|', ('name', 'ilike', quoted), ('origin', 'ilike', quoted)]

        deliveries = pickings.search(
            domain, limit=self._limit(payload, capability), order='scheduled_date desc, id desc')
        if reference and not deliveries:
            # The same refusal whether that number is nobody's or somebody
            # else's: which numbers are real is itself a fact about another
            # customer, and a person trying numbers must not be able to learn
            # it. Silence — an empty list — would read as “not shipped yet”.
            raise KMessageApiError(
                'delivery_not_found',
                _("There is no delivery of yours with that number."), 404)

        # Carrier and tracking arrive with the delivery app, which plenty of
        # databases do not have. Asking the model beats guessing from the
        # module list.
        has_carrier = 'carrier_id' in pickings._fields
        has_tracking = 'carrier_tracking_ref' in pickings._fields

        rows = []
        for delivery in deliveries:
            moment = delivery.date_done or delivery.scheduled_date
            row = {
                'reference': delivery.name,
                'date': moment.date().isoformat() if moment else '',
                'state': self._kmessage_delivery_state(delivery),
            }
            if has_carrier:
                row['carrier'] = delivery.carrier_id.display_name if delivery.carrier_id else ''
            if has_tracking:
                row['tracking'] = delivery.carrier_tracking_ref or ''
            rows.append(row)
        return {'rows': rows}

    @api.model
    def _kmessage_delivery_state(self, delivery):
        """The state in the words a customer uses.

        Odoo's own labels are written for the warehouse — “Waiting Another
        Operation” answers a question nobody asked on WhatsApp.
        """
        return {
            'waiting': _("not started yet"),
            'confirmed': _("waiting for stock"),
            'assigned': _("packed and ready"),
            'done': _("on its way"),
            'cancel': _("cancelled"),
        }.get(delivery.state, _("being prepared"))
