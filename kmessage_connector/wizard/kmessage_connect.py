# -*- coding: utf-8 -*-
"""Setup in one screen: a URL, a token, and a button.

What the button does depends on what the token turns out to be allowed to do,
because most tenants are on a managed plan where only the provider may add a
webhook. The wizard therefore does everything it can and then says, plainly,
what is left for a person to do — with the exact values to hand over.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools.client import KMessageClient, KMessageError

_logger = logging.getLogger(__name__)


class KMessageConnect(models.TransientModel):
    _name = 'kmessage.connect'
    _description = 'Connect to K-Message'

    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company)
    base_url = fields.Char(
        string='K-Message URL', required=True,
        default='https://api.k-message.kerneltics.com')
    api_key = fields.Char(string='Private Token', required=True)

    state = fields.Selection(
        [('start', 'Start'), ('checked', 'Checked'), ('done', 'Done')],
        default='start')

    # What the check found.
    remote_user = fields.Char(readonly=True)
    account_options = fields.Char(readonly=True)
    account_name = fields.Char(
        string='Send from',
        help="Which WhatsApp number to send from. Leave empty for the tenant's default.")
    template_total = fields.Integer(readonly=True)
    can_send = fields.Boolean(readonly=True)
    can_list_webhooks = fields.Boolean(readonly=True)
    provider_managed = fields.Boolean(readonly=True)

    # What to do, and what was done.
    setup_webhook = fields.Boolean(
        string='Let K-Message call this Odoo', default=True,
        help="Subscribes this Odoo to WhatsApp events so replies and delivery "
             "news come back. Needs a public address.")
    issue_token = fields.Boolean(
        string='Issue a token for K-Message', default=True,
        help="Creates the token K-Message uses to ask this Odoo about a customer.")
    publish_tools = fields.Boolean(
        string='Teach the assistant to ask Odoo', default=True,
        help="Publishes each switched-on capability to K-Message as an assistant tool. "
             "Needs a token whose role may change chatbot settings; when it may not, "
             "the tools stay here and can be published later.")
    summary = fields.Html(readonly=True)
    issued_token = fields.Char(readonly=True)
    webhook_url = fields.Char(readonly=True)
    webhook_secret = fields.Char(readonly=True)

    account_id = fields.Many2one('kmessage.account', readonly=True)

    # -- step one: look before leaping ------------------------------------
    def action_check(self):
        """Ask the platform who this token is and what it may do."""
        self.ensure_one()
        client = KMessageClient(self.base_url, self.api_key)
        try:
            me = client.me() or {}
        except KMessageError as error:
            raise UserError(_("K-Message did not accept that: %s", error))

        values = {
            'state': 'checked',
            'remote_user': me.get('full_name') or me.get('email') or _("this token"),
        }

        try:
            accounts = client.accounts()
            names = [account.get('name') for account in accounts if account.get('name')]
            values['account_options'] = ', '.join(repr(name) for name in names)
            default = next(
                (account.get('name') for account in accounts if account.get('is_default_outgoing')),
                names[0] if names else '')
            values['account_name'] = default
        except KMessageError as error:
            _logger.info('K-Message: could not list accounts (%s)', error)

        try:
            _rows, total = client.templates(limit=1)
            values['template_total'] = total
        except KMessageError:
            values['template_total'] = 0

        try:
            client.webhooks()
            values['can_list_webhooks'] = True
        except KMessageError as error:
            values['provider_managed'] = error.operator_only

        permissions = ((me.get('role') or {}).get('permissions')) or []
        granted = {(p.get('resource'), p.get('action')) for p in permissions if isinstance(p, dict)}
        values['can_send'] = ('chat', 'write') in granted or not granted

        self.write(values)
        return self._reopen()

    # -- step two: do it --------------------------------------------------
    def action_apply(self):
        """Save the connection and set up everything this token allows."""
        self.ensure_one()
        account = self.env['kmessage.account'].sudo()._for_company(self.company_id)
        values = {
            'base_url': self.base_url,
            'api_key': self.api_key,
            'account_name': self.account_name or False,
            'company_id': self.company_id.id,
        }
        if account:
            account.write(values)
        else:
            account = self.env['kmessage.account'].sudo().create(dict(values, name='K-Message'))

        account._probe()
        steps = []

        if account.state != 'connected':
            raise UserError(_("The connection could not be verified: %s", account.last_error))
        steps.append(_("Connected as %s.", account.sudo().remote_user or self.remote_user))

        if account.can_read_templates:
            templates = self.env['kmessage.template'].sync_from_platform(account)
            steps.append(_("%s templates are now available to choose from.", len(templates)))

        if self.setup_webhook:
            # A webhook that cannot be set up is a normal outcome — a managed
            # plan, or an Odoo that is not published yet — and it must not cost
            # the customer the rest of the setup, which has nothing to do with
            # it. The reason is reported and the wizard carries on.
            try:
                account.action_setup_webhook()
            except UserError as error:
                steps.append(_(
                    "The webhook was not set up: %s",
                    error.args[0] if error.args else error))
            else:
                if account.webhook_state == 'registered':
                    steps.append(_("K-Message will send replies and delivery news to this Odoo."))
                else:
                    steps.append(_(
                        "Your plan is managed by your provider, so the webhook has to be added by them. "
                        "The address and secret are below — send them these two lines."))

        if self.publish_tools:
            steps.append(self._publish_tools(account))

        raw_token = False
        if self.issue_token:
            capabilities = self.env['kmessage.capability'].sudo().search([('active', '=', True)])
            token, raw_token = self.env['kmessage.token'].sudo().issue(
                name=_("K-Message (%s)", self.company_id.name),
                company=self.company_id,
                capabilities=capabilities,
            )
            steps.append(_(
                "A token was issued for K-Message, covering %s capabilities. "
                "It is shown once, below.", len(capabilities)))

        self.write({
            'state': 'done',
            'account_id': account.id,
            'issued_token': raw_token or False,
            'webhook_url': account.webhook_url,
            'webhook_secret': account.sudo().webhook_secret or '',
            'summary': '<ul>%s</ul>' % ''.join('<li>%s</li>' % step for step in steps),
        })
        return self._reopen()

    def _publish_tools(self, account):
        """Publish every live assistant tool, and say plainly what happened.

        One tool refusing is not a failed setup: a managed tenant, or a token
        whose role cannot touch chatbot settings, simply keeps its tools here
        until somebody with the right permission presses Publish.
        """
        self.ensure_one()
        tools = self.env['kmessage.ai.tool'].sudo().search([
            ('account_id', '=', account.id),
            ('capability_id.active', '=', True),
        ])
        if not tools:
            return _("No assistant tools to publish yet — they arrive with the Invoicing, "
                     "Sales and Inventory apps.")

        published, refused = 0, []
        for tool in tools:
            try:
                tool._publish()
                published += 1
            except UserError as error:
                refused.append('%s (%s)' % (tool.name, error.args[0] if error.args else error))
            except Exception as error:  # noqa: BLE001 - one bad tool must not stop the rest
                _logger.exception('K-Message: publishing %s failed', tool.name)
                refused.append('%s (%s)' % (tool.name, error))

        if published and not refused:
            return _("The assistant can now answer %s question(s) from Odoo.", published)
        if published:
            return _("%(done)s tool(s) published; %(left)s still waiting: %(why)s",
                     done=published, left=len(refused), why='; '.join(refused))
        return _("The tools are ready in Odoo but K-Message would not take them: %s",
                 '; '.join(refused))

    def action_open_account(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'kmessage.account',
            'res_id': self.account_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
