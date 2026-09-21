# -*- coding: utf-8 -*-
"""The connection to K-Message: one record per Odoo company."""

import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools.client import DEFAULT_BASE_URL, KMessageClient, KMessageError
from ..tools.compat import LIST_FORM
from ..tools.net import why_not_public

_logger = logging.getLogger(__name__)

#: Events the connector knows how to act on. The platform may offer more; the
#: ones we do not understand are logged and ignored rather than refused.
SUBSCRIBED_EVENTS = ['message.incoming', 'message.sent', 'button.reply', 'contact.created']


class KMessageAccount(models.Model):
    _name = 'kmessage.account'
    _description = 'K-Message Connection'
    _order = 'company_id, id'

    name = fields.Char(
        required=True, default='K-Message',
        help="How this connection is referred to inside Odoo.")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)

    base_url = fields.Char(
        string='K-Message URL', required=True,
        default=lambda self: self._service_url(),
        help="Where K-Message is. There is one, so nobody is asked for it: it "
             "comes from the system parameter kmessage.base_url when that is "
             "set, and otherwise from the address built into this addon. The "
             "field is here for a developer, not for a customer.")
    api_key = fields.Char(
        string='Private Token', required=True,
        groups='kmessage_connector.group_kmessage_manager',
        help="The API key from K-Message. Only K-Message administrators in Odoo can read it.")
    # Never stripped, never title-cased: account names on the platform may
    # legitimately end in a space, and sending to " main number" is not the
    # same as sending to "main number ".
    account_name = fields.Char(
        string='Send From',
        help="Which WhatsApp number to send from. Leave empty to use the tenant's default.")

    state = fields.Selection(
        [('draft', 'Not connected'), ('connected', 'Connected'), ('error', 'Problem')],
        default='draft', readonly=True, copy=False)
    last_check = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Char(readonly=True, copy=False)
    remote_user = fields.Char(
        string='Token belongs to', readonly=True, copy=False,
        help="The K-Message user this token authenticates as.")

    # -- master switches --------------------------------------------------
    outbound_enabled = fields.Boolean(
        string='Send messages', default=True,
        help="Off means Odoo never sends anything, whatever the automations say.")
    inbound_enabled = fields.Boolean(
        string='Answer questions', default=True,
        help="Off means K-Message gets nothing back from this Odoo, whatever the capabilities say.")
    dry_run = fields.Boolean(
        string='Practice mode',
        help="Prepare every message and record it, but do not actually send. "
             "Useful for a first day in production.")

    # -- what the token turned out to be allowed to do --------------------
    can_send = fields.Boolean(readonly=True, copy=False)
    can_read_templates = fields.Boolean(readonly=True, copy=False)
    # Listing is all a probe can honestly establish. Whether this token may
    # *create* a subscription is only known by trying, and on a managed plan
    # the answer is no — which is what provider_managed records, once the
    # attempt has been made.
    can_list_webhooks = fields.Boolean(
        string='Can see webhooks', readonly=True, copy=False,
        help="Whether this token may read the tenant's webhook list. Being able to add "
             "one is a separate permission, and most managed plans reserve it for the provider.")
    provider_managed = fields.Boolean(
        readonly=True, copy=False,
        help="The tenant is on a managed plan: some changes can only be made by the K-Message provider.")

    # -- the inbound side -------------------------------------------------
    webhook_url = fields.Char(compute='_compute_webhook_url')
    webhook_secret = fields.Char(
        groups='kmessage_connector.group_kmessage_manager', copy=False,
        help="Shared secret K-Message signs its webhooks with.")
    webhook_remote_id = fields.Char(readonly=True, copy=False)
    webhook_state = fields.Selection(
        [('none', 'Not set up'), ('registered', 'Registered by Odoo'), ('manual', 'Waiting for your provider')],
        default='none', readonly=True, copy=False)

    template_ids = fields.One2many('kmessage.template', 'account_id')
    template_count = fields.Integer(compute='_compute_counts')
    message_count = fields.Integer(compute='_compute_counts')
    failed_count = fields.Integer(compute='_compute_counts')

    _sql_constraints = [
        ('company_unique', 'unique(company_id)',
         'Each company has a single K-Message connection.'),
    ]

    # -- computes ---------------------------------------------------------
    def _compute_webhook_url(self):
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url') or ''
        for account in self:
            account.webhook_url = '%s/kmessage/api/v1/webhook' % base.rstrip('/')

    def _compute_counts(self):
        message = self.env['kmessage.message']
        for account in self:
            account.template_count = len(account.template_ids)
            account.message_count = message.search_count([('account_id', '=', account.id)])
            account.failed_count = message.search_count([
                ('account_id', '=', account.id), ('state', '=', 'failed'),
            ])

    # -- access -----------------------------------------------------------
    @api.model
    def _for_company(self, company=None):
        """The live connection for ``company``, or an empty recordset."""
        company = company or self.env.company
        return self.sudo().search([('company_id', '=', company.id)], limit=1)

    @api.model
    def _service_url(self):
        """The one address K-Message is at.

        There is a single service, so this is not a question a customer should
        ever be asked. It stays overridable through a system parameter because
        a developer running against a local stand-in needs somewhere to say so
        — and that is a technical setting, not a step in setup.
        """
        configured = self.env['ir.config_parameter'].sudo().get_param('kmessage.base_url')
        return (configured or DEFAULT_BASE_URL).rstrip('/')

    def _client(self):
        """A ready client, or a clear error explaining what is missing."""
        self.ensure_one()
        record = self.sudo()
        if not record.base_url or not record.api_key:
            raise UserError(_("Fill in the K-Message URL and token first."))
        return KMessageClient(record.base_url, record.api_key)

    # -- connecting -------------------------------------------------------
    def action_test_connection(self):
        """Check the token, and find out what it is allowed to do."""
        self.ensure_one()
        self._probe()
        if self.state == 'error':
            raise UserError(_("K-Message refused the connection: %s", self.last_error))
        return self._notify(_("Connected to K-Message as %s.", self.sudo().remote_user or _("this token")))

    def _probe(self):
        """Ask the platform who we are and what we may do. Never raises."""
        self.ensure_one()
        record = self.sudo()
        values = {
            'last_check': fields.Datetime.now(),
            'can_send': False,
            'can_read_templates': False,
            'can_list_webhooks': False,
            'provider_managed': False,
        }
        try:
            client = record._client()
            me = client.me() or {}
            values['remote_user'] = me.get('full_name') or me.get('email') or ''
            values['state'] = 'connected'
            values['last_error'] = False
        except KMessageError as error:
            values.update(state='error', last_error=str(error))
            record.write(values)
            return record

        # Reads first: they are safe to repeat and tell us most of what we need.
        try:
            client.templates(limit=1)
            values['can_read_templates'] = True
        except KMessageError as error:
            _logger.info('K-Message: templates are not readable with this token (%s)', error)

        try:
            client.webhooks()
            values['can_list_webhooks'] = True
        except KMessageError as error:
            if error.operator_only:
                values['provider_managed'] = True
            _logger.info('K-Message: webhooks are not listable with this token (%s)', error)

        # Sending is not probed by sending. The permission list on the token
        # says enough, and a probe message would be a real message.
        permissions = ((me.get('role') or {}).get('permissions')) or []
        granted = {
            (permission.get('resource'), permission.get('action'))
            for permission in permissions if isinstance(permission, dict)
        }
        values['can_send'] = ('chat', 'write') in granted or not granted

        record.write(values)
        return record

    def action_sync_templates(self):
        """Pull the approved templates so automations can pick from a list."""
        self.ensure_one()
        self.env['kmessage.template'].sync_from_platform(self)
        return self._notify(_("%s templates synced.", len(self.template_ids)))

    def connect_everything(self, tools=None):
        """Hand K-Message the webhook and the tools in a single call.

        The customer sees one outcome instead of a list of things to forward
        to their provider. Returns ``(done, note)`` — ``done`` is False when
        the platform is too old to have the endpoint, which is the caller's
        cue to fall back to setting each piece up on its own.
        """
        self.ensure_one()
        record = self.sudo()
        unreachable = why_not_public(record.webhook_url)
        if unreachable:
            return False, _("K-Message cannot call this Odoo: %s.", unreachable)

        if not record.webhook_secret:
            record.webhook_secret = secrets.token_hex(32)

        tools = tools if tools is not None else self.env['kmessage.ai.tool'].sudo().search([
            ('account_id', '=', record.id), ('capability_id.active', '=', True),
        ])
        payloads, by_name = [], {}
        for tool in tools:
            try:
                payload = tool._connect_payload()
            except UserError as error:
                _logger.info('K-Message: %s was not offered (%s)', tool.name, error)
                continue
            payloads.append(payload)
            by_name[payload['name']] = tool

        try:
            answer = record._client().connect_odoo(
                webhook_url=record.webhook_url,
                webhook_secret=record.webhook_secret,
                events=SUBSCRIBED_EVENTS,
                tools=payloads,
            ) or {}
        except KMessageError as error:
            if error.status == 404:
                return False, _("This K-Message does not take a connection from Odoo yet.")
            if error.operator_only or error.status == 403:
                return False, _("K-Message would not take the connection: %s", error)
            raise UserError(_("K-Message refused the connection: %s", error))

        webhook = answer.get('webhook') or {}
        if webhook.get('id'):
            record.write({
                'webhook_remote_id': webhook.get('id'),
                'webhook_secret': webhook.get('secret') or record.webhook_secret,
                'webhook_state': 'registered',
            })

        published = 0
        for outcome in answer.get('tools') or []:
            tool = by_name.get(outcome.get('name'))
            if not tool:
                continue
            if outcome.get('status') in ('created', 'updated'):
                published += 1
                tool.sudo().write({'state': 'published', 'last_error': False,
                                   'last_published': fields.Datetime.now()})
            else:
                tool.sudo().write({'state': 'error', 'last_error': outcome.get('message') or ''})
        return True, _("The assistant can now answer %s questions from Odoo.", published)

    def action_setup_webhook(self):
        """Subscribe our webhook URL, or explain what to ask the provider for."""
        self.ensure_one()
        record = self.sudo()
        # K-Message dials through a guard that refuses private and loopback
        # hosts, so an Odoo nobody outside can reach fails here rather than
        # halfway through, where the platform's own wording explains nothing.
        unreachable = why_not_public(record.webhook_url)
        if unreachable:
            raise UserError(_(
                "K-Message cannot call this Odoo: %(why)s.\n\n"
                "Publish Odoo on an address the internet can reach, then set that address in "
                "Settings ▸ Technical ▸ System Parameters as web.base.url — it is what this "
                "page's webhook address is built from.",
                why=unreachable))
        if not record.webhook_secret:
            record.webhook_secret = secrets.token_hex(32)

        try:
            client = record._client()
            existing, _catalogue = client.webhooks()
        except KMessageError as error:
            if error.operator_only:
                record.write({'provider_managed': True, 'webhook_state': 'manual'})
                return record._notify(
                    _("Your K-Message plan is managed by your provider. "
                      "Send them the webhook details shown on this page."),
                    warning=True)
            raise UserError(_("Could not read the webhooks: %s", error))

        mine = next((hook for hook in existing if hook.get('url') == record.webhook_url), None)
        if mine:
            record.write({'webhook_remote_id': mine.get('id'), 'webhook_state': 'registered'})
            return record._notify(_("This Odoo is already subscribed."))

        try:
            created = client.create_webhook(
                name='Odoo — %s' % record.company_id.name,
                url=record.webhook_url,
                events=SUBSCRIBED_EVENTS,
                secret=record.webhook_secret,
            ) or {}
        except KMessageError as error:
            if error.operator_only:
                record.write({'provider_managed': True, 'webhook_state': 'manual'})
                return record._notify(
                    _("Only your K-Message provider can add a webhook on this plan. "
                      "The address and secret to give them are on this page."),
                    warning=True)
            raise UserError(_("K-Message refused the webhook: %s", error))

        record.write({
            'webhook_remote_id': created.get('id'),
            # The secret comes back exactly once, at creation. If the platform
            # generated its own, this is the only moment we can learn it.
            'webhook_secret': created.get('secret') or record.webhook_secret,
            'webhook_state': 'registered',
        })
        return record._notify(_("K-Message will now call this Odoo."))

    # -- small helpers ----------------------------------------------------
    def _notify(self, message, warning=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("K-Message"),
                'message': message,
                'type': 'warning' if warning else 'success',
                'sticky': warning,
            },
        }

    def action_view_messages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Messages"),
            'res_model': 'kmessage.message',
            'view_mode': LIST_FORM,
            'domain': [('account_id', '=', self.id)],
            'context': {'default_account_id': self.id},
        }
