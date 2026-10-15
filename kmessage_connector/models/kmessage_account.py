# -*- coding: utf-8 -*-
"""The connections to K-Message: as many per Odoo company as it has tenants."""

import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..tools.client import DEFAULT_BASE_URL, KMessageClient, KMessageError
from ..tools.compat import LIST_FORM
from ..tools.net import why_not_public

_logger = logging.getLogger(__name__)

#: Events the connector knows how to act on, asked of every K-Message. The
#: platform may offer more; the ones we do not understand are logged and
#: ignored rather than refused.
BASE_EVENTS = ['message.incoming', 'message.sent', 'button.reply', 'contact.created']

#: News about a message this Odoo sent: delivered, read, or not delivered with
#: WhatsApp's reason. On the platform they fire for every message the company
#: sends — bot replies and campaigns included — so they are asked for only
#: from a K-Message that confirms it will limit them to messages carrying a
#: reference (STATUS_SCOPE), which every message from this Odoo does.
DELIVERY_EVENTS = ['message.delivered', 'message.read', 'message.failed']

#: What the connector asks K-Message to limit status events to.
STATUS_SCOPE = 'referenced'

#: The name the events went by before delivery news, kept for code that
#: imported it.
SUBSCRIBED_EVENTS = BASE_EVENTS


def _organization_of(me):
    """The tenant a ``/api/me`` answer belongs to, as account values.

    Only what the platform said: a missing key leaves the stored value alone
    rather than blanking a tenant an older platform simply did not name.
    """
    organization = me.get('organization') if isinstance(me.get('organization'), dict) else {}
    values = {}
    ident = me.get('organization_id') or organization.get('id')
    if ident:
        values['remote_organization_id'] = str(ident)
    if organization.get('name'):
        values['remote_organization'] = organization['name']
    return values


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
    remote_organization_id = fields.Char(
        string='Tenant ID', readonly=True, copy=False, index=True,
        help="Which K-Message tenant this token belongs to. Connecting a second "
             "token from the same tenant updates this connection; a token from "
             "another tenant becomes a connection of its own.")
    remote_organization = fields.Char(
        string='Tenant', readonly=True, copy=False,
        help="The K-Message tenant's name, as the platform gives it.")

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
    webhook_events = fields.Char(
        string='Events', readonly=True, copy=False,
        help="The events K-Message confirmed it sends to this Odoo, the last time "
             "the subscription was set up from here.")
    delivery_news = fields.Boolean(
        string='Delivery news on', compute='_compute_delivery_news',
        help="Whether K-Message tells this Odoo when a message it sent is "
             "delivered, read or not delivered.")

    template_ids = fields.One2many('kmessage.template', 'account_id')
    template_count = fields.Integer(compute='_compute_counts')
    message_count = fields.Integer(compute='_compute_counts')
    failed_count = fields.Integer(compute='_compute_counts')
    undelivered_count = fields.Integer(
        string='Undelivered messages', compute='_compute_undelivered_count')

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

    def _compute_undelivered_count(self):
        message = self.env['kmessage.message']
        for account in self:
            account.undelivered_count = message.search_count([
                ('account_id', '=', account.id), ('delivery_status', '=', 'failed'),
            ])

    @api.depends('webhook_events')
    def _compute_delivery_news(self):
        for account in self:
            account.delivery_news = set(DELIVERY_EVENTS) <= set(account._webhook_event_list())

    def _webhook_event_list(self):
        self.ensure_one()
        return [event.strip() for event in (self.webhook_events or '').split(',') if event.strip()]

    # -- access -----------------------------------------------------------
    @api.model
    def _for_company(self, company=None):
        """The first live connection for ``company``, or an empty recordset.

        A company may hold several — one per K-Message tenant — so this is only
        a default for a form to start from. Anything that must hold for the
        company as a whole asks ``_all_for_company`` instead.
        """
        return self._all_for_company(company)[:1]

    @api.model
    def _all_for_company(self, company=None):
        """Every live connection for ``company``, in the usual order."""
        company = company or self.env.company
        return self.sudo().search([('company_id', '=', company.id)])

    @api.model
    def action_restore_default_rules(self):
        """Put back any of the connector's own rules that went missing.

        Deliberately additive. A rule that is still there is left exactly as
        it is — edited, switched off, renamed, it is the customer's now — and
        a rule they wrote themselves carries no marker, so it is never even
        looked at. The worst this button can do is give you back something
        you deleted.
        """
        self.ensure_one()
        made, held_back = self.env['kmessage.starter.automation'].ensure(self)
        if made:
            message = _("%s rule(s) put back.", len(made))
        elif held_back:
            message = _("Nothing to put back \u2014 and %s had no template to "
                        "point at. Write the templates first.", len(held_back))
        else:
            message = _("Every default rule is already there.")
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': 'success', 'sticky': False},
        }

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
            values.update(_organization_of(me))
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

        ``tools`` limits what is offered: an empty recordset touches no tool
        at all, which is how the subscription alone is brought up to date.
        """
        self.ensure_one()
        record = self.sudo()
        unreachable = why_not_public(record.webhook_url)
        if unreachable:
            return False, _("K-Message cannot call this Odoo: %s.", unreachable)

        if not record.webhook_secret:
            record.webhook_secret = secrets.token_hex(32)

        # Asked before ``tools`` is filled in below: a caller that limited the
        # tools (the subscription-only button) gets no sentence about them.
        every_tool = tools is None
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
            answer, news = record._subscribe(record._client(), payloads)
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
        notes = []
        if every_tool or payloads:
            notes.append(_("The assistant can now answer %s questions from Odoo.", published))
        if news:
            notes.append(news)
        return True, ' '.join(str(note) for note in notes)

    def _subscribe(self, client, payloads):
        """Register the webhook and the tools; delivery news only where it is scoped.

        Delivery news is asked for only from a K-Message that confirms it will
        limit it to this Odoo's own messages, by echoing the scope back. So
        the first call asks for the events this connection already had
        confirmed — the four, on a first connect — together with the scope,
        and the answer decides the rest:

        * the scope came back and the news is not on yet: a second call adds
          it, with no tools, which the first call already handled;
        * the scope did not come back: the platform is older than the scope,
          the news stays off, and if it was somehow on, the four are
          registered again in its place;
        * the webhook was set up by hand (``unchanged``): nothing here may
          change it, and the note says who can.

        At no point is an older platform asked for delivery news, not even
        for a moment. Raises ``KMessageError`` only for the first call.
        Returns ``(answer to the first call, a sentence about delivery news)``
        and writes the events the platform confirmed to ``webhook_events``.
        """
        self.ensure_one()
        record = self.sudo()
        asked = list(BASE_EVENTS) + (list(DELIVERY_EVENTS) if record.delivery_news else [])
        answer = client.connect_odoo(
            webhook_url=record.webhook_url,
            webhook_secret=record.webhook_secret,
            events=asked,
            tools=payloads,
            status_scope=STATUS_SCOPE,
        ) or {}
        webhook = answer.get('webhook') or {}
        if not webhook.get('id'):
            return answer, ''

        events = webhook.get('events') or asked
        if webhook.get('status') == 'unchanged':
            record.webhook_events = ', '.join(sorted(set(events)))
            if record.delivery_news:
                return answer, ''
            return answer, _(
                "The webhook for this address was set up by hand in K-Message, so Odoo "
                "cannot change its events. Ask your provider to add the header "
                "x-managed-by: odoo_connector to that webhook, then press Switch on "
                "delivery news again.")

        scoped = webhook.get('status_scope') == STATUS_SCOPE
        has_news = bool(set(DELIVERY_EVENTS) & set(events))
        trouble = None
        if scoped != has_news:
            wanted = list(BASE_EVENTS) + (list(DELIVERY_EVENTS) if scoped else [])
            try:
                again = client.connect_odoo(
                    webhook_url=record.webhook_url,
                    webhook_secret=record.webhook_secret,
                    events=wanted,
                    tools=[],
                    status_scope=STATUS_SCOPE,
                ) or {}
                events = (again.get('webhook') or {}).get('events') or wanted
            except KMessageError as error:
                _logger.warning('K-Message: could not settle delivery news (%s)', error)
                trouble = error
        record.webhook_events = ', '.join(sorted(set(events)))

        if trouble and scoped:
            return answer, _("Delivery news could not be switched on: %s", trouble)
        if trouble:
            return answer, _(
                "K-Message is sending delivery news about every message to this Odoo and "
                "did not take it back: %s. Connect again to retry.", trouble)
        if record.delivery_news:
            return answer, _("Delivery news is on: Odoo hears when a message is delivered, "
                             "read or not delivered.")
        return answer, _("Delivery news needs a newer K-Message, so it stays off for now. "
                         "Your provider can update K-Message; then press Switch on delivery news.")

    def action_update_subscription(self):
        """Switch on delivery news for a connection made before it existed.

        Connects again with no tools, so the subscription is the only thing
        that changes: no assistant tool is created, updated or touched.
        """
        self.ensure_one()
        if not (self.env.su or self.env.user.has_group('kmessage_connector.group_kmessage_manager')):
            raise AccessError(_("Only a K-Message administrator can change the subscription."))
        done, note = self.connect_everything(tools=self.env['kmessage.ai.tool'])
        action = self._notify(note, warning=not (done and self.delivery_news))
        # Shown again afterwards, so the banner goes and the events appear.
        action['params']['next'] = {'type': 'ir.actions.client', 'tag': 'soft_reload'}
        return action

    def action_view_undelivered(self):
        action = self.action_view_messages()
        action.update(
            name=_("Not delivered"),
            domain=[('account_id', '=', self.id), ('delivery_status', '=', 'failed')],
        )
        return action

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
            return record._add_missing_events(client, mine)

        try:
            created = client.create_webhook(
                name='Odoo — %s' % record.company_id.name,
                url=record.webhook_url,
                events=BASE_EVENTS,
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
            'webhook_events': ', '.join(sorted(set(created.get('events') or BASE_EVENTS))),
        })
        return record._notify(_("K-Message will now call this Odoo."))

    def _add_missing_events(self, client, hook):
        """Bring an existing subscription up to the events this Odoo acts on.

        Only the four this route has always asked for: the general webhooks
        API cannot limit delivery news to this Odoo's own messages, so it is
        never used to ask for them — that is Connect's job. ``is_active`` is
        always sent, because the platform reads it from every update and a
        body without it would switch the webhook off.
        """
        self.ensure_one()
        record = self.sudo()
        have = list(hook.get('events') or [])
        missing = [event for event in BASE_EVENTS if event not in have]
        if not missing:
            record.webhook_events = ', '.join(sorted(set(have)))
            return record._notify(_("This Odoo is already subscribed."))
        try:
            updated = client.update_webhook(
                hook.get('id'), events=sorted(set(have) | set(BASE_EVENTS)), is_active=True) or {}
        except KMessageError as error:
            if error.operator_only:
                record.write({'provider_managed': True, 'webhook_state': 'manual'})
                return record._notify(
                    _("Only your K-Message provider can change the webhook on this plan. "
                      "The events to ask them for are on this page."),
                    warning=True)
            raise UserError(_("K-Message refused to update the webhook: %s", error))
        record.webhook_events = ', '.join(sorted(set(
            updated.get('events') or (set(have) | set(BASE_EVENTS)))))
        return record._notify(_("This Odoo was already subscribed; %s missing event(s) added.",
                                len(missing)))

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
