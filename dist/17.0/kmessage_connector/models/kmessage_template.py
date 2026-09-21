# -*- coding: utf-8 -*-
"""A local copy of the templates approved on the platform.

Templates are authored and approved in K-Message (and, behind it, by Meta).
Odoo never creates one; it mirrors them so that choosing a template is a
dropdown and filling its parameters is a form, rather than a name typed from
memory and a `404 Template not found` an hour later.
"""

import json
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

#: WhatsApp body placeholders are positional: {{1}}, {{2}}, …
_PLACEHOLDER = re.compile(r'{{\s*(\d+)\s*}}')


class KMessageTemplate(models.Model):
    _name = 'kmessage.template'
    _description = 'K-Message Template'
    _order = 'name, language'
    _rec_names_search = ['name', 'display_name']

    account_id = fields.Many2one('kmessage.account', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)

    remote_id = fields.Char(readonly=True, index=True)
    name = fields.Char(required=True, readonly=True, index=True)
    language = fields.Char(readonly=True)
    status = fields.Char(readonly=True)
    category = fields.Char(readonly=True)
    whatsapp_account = fields.Char(readonly=True)

    header_type = fields.Selection(
        [('none', 'None'), ('TEXT', 'Text'), ('IMAGE', 'Image'),
         ('DOCUMENT', 'Document'), ('VIDEO', 'Video')],
        default='none', readonly=True)
    body_content = fields.Text(readonly=True)
    footer_content = fields.Text(readonly=True)
    buttons_json = fields.Text(readonly=True)

    param_count = fields.Integer(
        compute='_compute_params', store=True,
        help="How many {{1}}-style placeholders the body has.")
    button_count = fields.Integer(compute='_compute_buttons', store=True)
    takes_document = fields.Boolean(
        compute='_compute_params', store=True,
        help="Whether this template carries a document, such as an invoice PDF.")
    usable = fields.Boolean(
        compute='_compute_params', store=True,
        help="Approved templates are the only ones that can be sent.")

    display_name = fields.Char(compute='_compute_display_name', store=True)

    _sql_constraints = [
        ('account_name_lang_unique', 'unique(account_id, name, language)',
         'That template is already mirrored for this connection.'),
    ]

    @api.depends('body_content', 'header_type', 'status')
    def _compute_params(self):
        for template in self:
            numbers = {int(n) for n in _PLACEHOLDER.findall(template.body_content or '')}
            template.param_count = max(numbers) if numbers else 0
            template.takes_document = template.header_type == 'DOCUMENT'
            template.usable = (template.status or '').upper() == 'APPROVED'

    @api.depends('buttons_json')
    def _compute_buttons(self):
        for template in self:
            template.button_count = len(template._buttons())

    @api.depends('name', 'language')
    def _compute_display_name(self):
        for template in self:
            language = template.language or ''
            template.display_name = '%s (%s)' % (template.name, language) if language else template.name

    def _buttons(self):
        self.ensure_one()
        if not self.buttons_json:
            return []
        try:
            buttons = json.loads(self.buttons_json)
        except ValueError:
            return []
        return buttons if isinstance(buttons, list) else []

    # -- syncing ----------------------------------------------------------
    @api.model
    def sync_from_platform(self, account):
        """Mirror every template the platform has for ``account``.

        Rows that vanished upstream are kept, not deleted: an automation may
        still point at one, and a template that failed to come back in a
        paginated read is not proof that it is gone.
        """
        rows = account._client().all_templates()
        seen = self.browse()
        for row in rows:
            values = self._values_from_platform(account, row)
            existing = self.search([
                ('account_id', '=', account.id),
                ('name', '=', values['name']),
                ('language', '=', values['language']),
            ], limit=1)
            if existing:
                existing.write(values)
                seen |= existing
            else:
                seen |= self.create(values)
        _logger.info('K-Message: synced %s templates for %s', len(seen), account.display_name)
        return seen

    @api.model
    def _values_from_platform(self, account, row):
        header = (row.get('header_type') or '').upper()
        return {
            'account_id': account.id,
            'remote_id': row.get('id'),
            'name': row.get('name') or '',
            'language': row.get('language') or '',
            'status': row.get('status') or '',
            'category': row.get('category') or '',
            'whatsapp_account': row.get('whatsapp_account') or '',
            'header_type': header if header in ('TEXT', 'IMAGE', 'DOCUMENT', 'VIDEO') else 'none',
            'body_content': row.get('body_content') or '',
            'footer_content': row.get('footer_content') or '',
            'buttons_json': json.dumps(row.get('buttons') or [], ensure_ascii=False),
        }

    def preview(self, params=None):
        """The body with ``params`` substituted, for showing before sending."""
        self.ensure_one()
        params = params or {}
        body = self.body_content or ''

        def replace(match):
            return str(params.get(match.group(1), match.group(0)))

        return _PLACEHOLDER.sub(replace, body)
