# -*- coding: utf-8 -*-
"""Which of a warehouse's printouts a customer is sent.

Core asks Odoo for the record's report and takes the first one. On an invoice
that is the invoice; on a transfer it is whichever of the warehouse's several
printouts happens to have been loaded first, which in a stock database is the
Reception Report, with Picking Operations behind it. Both are written for the
person on the loading bay — source locations, reserved quantities, operation
lines — and neither is the document the customer is waiting for.

So this bridge names the one that is. A rule that asks for a particular report
by name still gets it: the question answered here is only "which one when
nobody said".
"""

from odoo import api, models

#: Odoo's customer-facing transfer printout, under this id on 17.0 and 18.0.
DELIVERY_SLIP = 'stock.action_report_delivery'


class KMessageDocument(models.AbstractModel):
    _inherit = 'kmessage.document'

    @api.model
    def _report_for(self, record, report_name=None):
        if not report_name and record._name == 'stock.picking':
            slip = self.env.ref(DELIVERY_SLIP, raise_if_not_found=False)
            if slip:
                return slip.sudo()
        return super()._report_for(record, report_name=report_name)
