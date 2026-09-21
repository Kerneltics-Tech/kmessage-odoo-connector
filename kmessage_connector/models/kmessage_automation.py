# -*- coding: utf-8 -*-
"""“When this happens in Odoo, send that template.”

Every automation is one switch. Nothing here runs until somebody creates a rule,
picks a template and ticks it on — installing the addon changes what a customer
*can* do, never what their Odoo starts doing behind their back.

Triggers are contributed by the bridge modules: core knows how to run a trigger,
`kmessage_connector_account` knows what "invoice posted" means. A trigger whose
module is not installed simply does not appear in the list.
"""

import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class KMessageAutomation(models.Model):
    _name = 'kmessage.automation'
    _description = 'K-Message Automation'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(
        default=False,
        help="New rules start switched off. Turn this on when the template and the "
             "parameters below are right.")
    account_id = fields.Many2one(
        'kmessage.account', required=True, ondelete='cascade', index=True,
        default=lambda self: self.env['kmessage.account']._for_company())
    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    trigger = fields.Selection(
        selection='_selection_trigger', required=True, index=True,
        help="What has to happen in Odoo for this message to go out.")
    model_name = fields.Char(compute='_compute_model_name', store=True)
    filter_domain = fields.Char(
        string='Only when',
        help="An optional extra condition, in Odoo's filter syntax. "
             "Records that do not match are left alone.")

    template_id = fields.Many2one(
        'kmessage.template', required=True, ondelete='restrict',
        domain="[('account_id', '=', account_id), ('usable', '=', True)]")
    param_ids = fields.One2many('kmessage.automation.param', 'automation_id')
    param_count = fields.Integer(related='template_id.param_count')

    attach_document = fields.Boolean(
        string='Attach the PDF', default=True,
        help="Send the printed document with the message. Only possible with a "
             "template whose header is a document.")
    report_name = fields.Char(
        string='Report',
        help="Which printout to attach. Empty means the document's usual one.")

    language_mode = fields.Selection(
        [('partner', "The customer's language"), ('template', "The template's language"),
         ('fixed', 'Always this language')],
        default='partner', required=True)
    fixed_language = fields.Char(default='ar')

    cooldown_hours = fields.Integer(
        default=24,
        help="Do not send this same message about the same record twice within "
             "these many hours. 0 switches the protection off.")

    sent_count = fields.Integer(readonly=True, default=0, copy=False)
    last_run = fields.Datetime(readonly=True, copy=False)

    # -- trigger registry -------------------------------------------------
    @api.model
    def _selection_trigger(self):
        """Bridges extend this; core alone offers nothing to trigger on."""
        return []

    @api.model
    def _trigger_model(self, trigger):
        """The Odoo model a trigger is about. Bridges extend."""
        return False

    @api.depends('trigger')
    def _compute_model_name(self):
        for automation in self:
            automation.model_name = automation._trigger_model(automation.trigger) or False

    # -- running ----------------------------------------------------------
    @api.model
    def run_trigger(self, trigger, records, params_builder=None):
        """Queue a message for every record that matches a live rule.

        Called from the bridges' business hooks. It must never raise into the
        caller: posting an invoice does not fail because WhatsApp is unhappy.

        Each rule runs inside its own savepoint. Swallowing the exception is
        not enough on its own — a failed *query* leaves the cursor unusable,
        and the invoice being posted would then fail a moment later with an
        error nobody can act on. The savepoint also keeps one broken rule from
        taking the rest of them down with it.
        """
        if not records:
            return
        try:
            automations = self.sudo().search([('trigger', '=', trigger)])
        except Exception:  # noqa: BLE001 - never break the business transaction
            _logger.exception('K-Message: could not read the rules for trigger %s', trigger)
            return

        for automation in automations:
            try:
                with self.env.cr.savepoint():
                    automation._run_on(records, params_builder=params_builder)
            except Exception:  # noqa: BLE001 - never break the business transaction
                _logger.exception(
                    'K-Message: rule %s failed on trigger %s', automation.id, trigger)

    def _run_on(self, records, params_builder=None):
        self.ensure_one()
        account = self.account_id
        if not (account.outbound_enabled and account.state == 'connected'):
            return

        candidates = records
        if 'company_id' in records._fields:
            # A record with no company of its own belongs to all of them, so it
            # is not somebody else's record.
            candidates = records.filtered(
                lambda r: not r.company_id or r.company_id == self.company_id)
        if self.filter_domain:
            domain = safe_eval(self.filter_domain)
            candidates = candidates.filtered_domain(domain)

        outbox = self.env['kmessage.message'].sudo()
        for record in candidates:
            partner = self._partner_of(record)
            if not partner:
                continue
            if self._recently_sent(record):
                continue

            params = params_builder(record) if params_builder else self._params_for(record)
            attachment = self._document_for(record, partner) if self.attach_document else None

            message = outbox.enqueue(
                account=account,
                phone=self._phone_of(partner),
                template=self.template_id,
                params=params,
                partner=partner,
                record=record,
                attachment=attachment,
                language=self._language_for(partner),
                reference='%s:%s:%s' % (self.id, record._name, record.id),
            )
            if message:
                self.sudo().write({
                    'sent_count': self.sent_count + 1,
                    'last_run': fields.Datetime.now(),
                })

    # -- the pieces of one message ---------------------------------------
    def _partner_of(self, record):
        """The customer this record is about."""
        for field_name in ('partner_id', 'commercial_partner_id', 'customer_id'):
            if field_name in record._fields and record[field_name]:
                return record[field_name]
        return self.env['res.partner']

    def _phone_of(self, partner):
        return partner.mobile or partner.phone or ''

    def _language_for(self, partner):
        if self.language_mode == 'fixed':
            return self.fixed_language
        if self.language_mode == 'partner' and partner.lang:
            # 'ar_001' → 'ar': the platform speaks language, not locale.
            return partner.lang.split('_')[0]
        return self.template_id.language

    def _params_for(self, record):
        """Fill the template's {{1}}, {{2}}, … from this record."""
        self.ensure_one()
        params = {}
        for param in self.param_ids.sorted('index'):
            params[str(param.index)] = param.value_for(record)
        return params

    def _document_for(self, record, partner):
        """Render the record's printout as an attachment, or None."""
        self.ensure_one()
        if not self.template_id.takes_document:
            return None
        try:
            return self.env['kmessage.document']._render(
                record, report_name=self.report_name, lang=self._language_for(partner))
        except Exception:  # noqa: BLE001 - a missing report must not stop the message
            _logger.exception('K-Message: could not render the document for %s', record)
            return None

    def _recently_sent(self, record):
        """True when this rule already messaged about this record recently."""
        self.ensure_one()
        if not self.cooldown_hours:
            return False
        since = fields.Datetime.subtract(fields.Datetime.now(), hours=self.cooldown_hours)
        return bool(self.env['kmessage.message'].sudo().search_count([
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('template_id', '=', self.template_id.id),
            ('state', 'in', ('queued', 'sent')),
            ('create_date', '>=', since),
        ]))

    # -- editing help -----------------------------------------------------
    @api.onchange('template_id')
    def _onchange_template_id(self):
        """Offer one parameter row per placeholder the template actually has."""
        for automation in self:
            wanted = automation.template_id.param_count or 0
            existing = {param.index for param in automation.param_ids}
            rows = [
                (0, 0, {'index': index})
                for index in range(1, wanted + 1) if index not in existing
            ]
            if rows:
                automation.param_ids = rows

    def action_toggle(self):
        for automation in self:
            automation.active = not automation.active

    @api.constrains('attach_document', 'template_id')
    def _check_document(self):
        for automation in self:
            if automation.attach_document and automation.template_id and not automation.template_id.takes_document:
                raise UserError(_(
                    "The template “%s” has no document header, so no PDF can be attached to it.",
                    automation.template_id.display_name))


class KMessageAutomationParam(models.Model):
    _name = 'kmessage.automation.param'
    _description = 'K-Message Template Parameter'
    _order = 'automation_id, index'

    automation_id = fields.Many2one('kmessage.automation', required=True, ondelete='cascade')
    index = fields.Integer(required=True, help="Which {{n}} in the template body this fills.")
    source = fields.Selection(
        [('field', 'A field on the record'), ('fixed', 'Always the same text'),
         ('expression', 'A small expression')],
        default='field', required=True)
    field_path = fields.Char(
        help="A field, or a path through relations: amount_total, partner_id.name.")
    fixed_value = fields.Char()
    expression = fields.Char(
        help="Advanced: Python evaluated with `record` available, e.g. "
             "record.amount_total - record.amount_residual.")

    def value_for(self, record):
        """The text that goes into the message, never blank.

        WhatsApp rejects a template parameter that is empty, so an unresolvable
        value becomes a dash rather than a failed send.
        """
        self.ensure_one()
        try:
            if self.source == 'fixed':
                value = self.fixed_value
            elif self.source == 'expression' and self.expression:
                value = safe_eval(self.expression, {'record': record}, nocopy=True)
            else:
                value = record
                for part in (self.field_path or '').split('.'):
                    if not part:
                        value = None
                        break
                    value = value[part] if value else None
        except Exception:  # noqa: BLE001 - a bad mapping is a dash, not an outage
            _logger.exception('K-Message: parameter %s could not be resolved', self.index)
            value = None
        return self._format(value, record)

    def _format(self, value, record):
        if value is None or value is False or value == '':
            return '-'
        if hasattr(value, 'display_name'):  # a recordset
            return value.display_name or '-'
        if isinstance(value, float):
            currency = record.currency_id if 'currency_id' in record._fields else None
            digits = currency.decimal_places if currency else 2
            return '{:,.{d}f}'.format(value, d=digits)
        return str(value)
