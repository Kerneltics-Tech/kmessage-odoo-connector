# -*- coding: utf-8 -*-
"""Setup in one screen: a token and one button.

There is one K-Message, so its address is not a question — it comes from
``kmessage.account._service_url()`` and never appears on the form. What the
button does depends on what the token turns out to be allowed to do: it wires
both directions in one call where the platform supports it, and where it does
not, it says plainly what is left for a person to do.
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
    # Not on the form: there is one K-Message. It is a field rather than a
    # call so a developer can still aim a wizard at a stand-in.
    base_url = fields.Char(
        string='K-Message URL', required=True,
        default=lambda self: self.env['kmessage.account']._service_url())
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
    create_rules = fields.Boolean(
        string='Set up the rules too', default=True,
        help="Writes the rules that use those templates, filled in. Sending "
             "the invoice arrives switched on; the rest arrive off, and you "
             "can never lose them \u2014 Restore the default rules brings back "
             "any you delete, without touching your own.")
    create_templates = fields.Boolean(
        string='Write the templates for me', default=True,
        help="Creates the messages this addon sends — in Arabic, under names of its "
             "own — and submits them to Meta for approval. Templates that already "
             "exist are left alone, and anything created can be reworded in K-Message "
             "afterwards.")
    publish_tools = fields.Boolean(
        string='Teach the assistant to ask Odoo', default=True,
        help="Publishes each switched-on capability to K-Message as an assistant tool. "
             "Needs a token whose role may change chatbot settings; when it may not, "
             "the tools stay here and can be published later.")
    #: Whether the last step left something for a person to forward. Only
    #: then is the handover block worth a customer's attention.
    needs_handover = fields.Boolean(readonly=True)
    summary = fields.Html(readonly=True)
    issued_token = fields.Char(readonly=True)
    webhook_url = fields.Char(readonly=True)
    webhook_secret = fields.Char(readonly=True)

    account_id = fields.Many2one('kmessage.account', readonly=True)

    # -- step one: look before leaping ------------------------------------
    def action_connect(self):
        """The whole of setup, from the only thing a customer has: the token.

        Looking first and then asking which parts to do was honest, and it was
        also a screen of decisions nobody outside this addon is equipped to
        make. So the button does both: it looks, and then it does everything
        the token turned out to be allowed to do. Anyone who does want to
        choose can still press Choose what it sets up, which stops at the
        same review screen as before.
        """
        self.ensure_one()
        self.action_check()
        return self.action_apply()

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
        """Save the connection and wire both sides up."""
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
        if account.state != 'connected':
            raise UserError(_("The connection could not be verified: %s", account.last_error))

        steps = [_("Connected as %s.", account.sudo().remote_user or self.remote_user)]

        if self.create_templates:
            steps.append(self._create_templates(account))
        elif account.can_read_templates:
            self.env['kmessage.template'].sync_from_platform(account)

        if self.create_rules:
            steps.append(self._create_rules(account))

        # One call hands K-Message the webhook and the tools together. Older
        # platforms have neither the endpoint nor the permission, and then the
        # pieces are set up one at a time, as before.
        wired = False
        if self.setup_webhook or self.publish_tools:
            try:
                wired, note = account.connect_everything()
            except UserError as error:
                wired, note = False, (error.args[0] if error.args else str(error))
            if wired:
                steps.append(_("K-Message will call this Odoo."))
                if note:
                    steps.append(note)
            else:
                steps.extend(self._set_up_the_long_way(account, note))

        raw_token = False
        if self.issue_token:
            capabilities = self.env['kmessage.capability'].sudo().search([('active', '=', True)])
            _token, raw_token = self.env['kmessage.token'].sudo().issue(
                name=_("K-Message (%s)", self.company_id.name),
                company=self.company_id,
                capabilities=capabilities,
            )

        self.write({
            'state': 'done',
            'account_id': account.id,
            'issued_token': raw_token or False,
            'needs_handover': not wired and account.webhook_state != 'registered',
            'webhook_url': account.webhook_url,
            'webhook_secret': account.sudo().webhook_secret or '',
            'summary': '<ul>%s</ul>' % ''.join('<li>%s</li>' % step for step in steps),
        })
        return self._reopen()

    def _create_rules(self, account):
        """Offer the rules, and say what arrived on."""
        made, held_back = self.env['kmessage.starter.automation'].ensure(account)
        if not made:
            return _("The rules were already there.")

        live = self.env['kmessage.automation'].sudo().search_count([
            ('company_id', '=', self.company_id.id),
            ('starter_key', 'in', made),
            ('active', '=', True),
        ])
        if held_back:
            return _("%(made)s rules set up, %(live)s of them live; "
                     "%(held)s had no template to point at.",
                     made=len(made), live=live, held=len(held_back))
        return _("%(made)s rules set up, %(live)s of them live.",
                 made=len(made), live=live)

    def _set_up_the_long_way(self, account, note):
        """When the platform will not take the connection, do what it allows.

        Reported in one line each. The values a provider needs are on the
        screen already; repeating them in a paragraph helps nobody.
        """
        self.ensure_one()
        steps = [_("K-Message did not take the connection: %s", note)]
        if self.setup_webhook:
            try:
                account.action_setup_webhook()
            except UserError as error:
                steps.append(_("Webhook: %s", error.args[0] if error.args else error))
            else:
                if account.webhook_state == 'registered':
                    steps.append(_("K-Message will call this Odoo."))
        if self.publish_tools:
            steps.append(self._publish_tools(account))
        return steps

    def _create_templates(self, account):
        """Write the starter templates and submit them, then say what happened."""
        self.ensure_one()
        outcomes = self.env['kmessage.starter'].sudo().provision(account)
        if not outcomes:
            return _("No templates to write — they come with the Invoicing, Sales "
                     "and Inventory apps.")

        # Resynced because the ones that went through are now the tenant's, and
        # an automation should be able to pick them the moment this closes.
        if account.can_read_templates:
            self.env['kmessage.template'].sync_from_platform(account)

        # Counted, not listed. Five template names and five identical outcomes
        # is a paragraph that says one thing, and the one thing worth a
        # customer's attention is whatever did not work.
        sent = [name for name, outcome in outcomes if _("submitted") in outcome or 'Meta' in outcome]
        kept = [name for name, outcome in outcomes if outcome == _("already there")]
        trouble = [(name, outcome) for name, outcome in outcomes
                   if name not in sent and name not in kept]

        said = []
        if sent:
            said.append(_("%s templates written and sent to Meta for approval.", len(sent)))
        if kept:
            said.append(_("%s already there.", len(kept)))
        if trouble:
            said.append(_("%s need attention: %s", len(trouble),
                          '; '.join('%s — %s' % (name, outcome) for name, outcome in trouble)))
        return ' '.join(said) or _("Nothing to write.")

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
