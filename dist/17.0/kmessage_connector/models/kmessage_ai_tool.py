# -*- coding: utf-8 -*-
"""Teaching the K-Message assistant to ask Odoo.

K-Message's assistant cannot be pointed at a URL by the model — the tool
catalogue is fixed server-side — but a company may *register* its own tools:
a ``lookup_tool`` that returns rows, or a ``document_tool`` that returns a
file. That is the door this module walks through. Each row here is one Odoo
capability, published to the tenant as one assistant tool.

Three properties of that platform make the design what it is:

* The customer's number is substituted into the call as ``{{phone_number}}``. Identity
  therefore comes from the conversation, never from the model, which is the
  same rule the inbound API enforces at the other end.
* Only allow-listed fields of the answer ever reach the prompt, so each tool
  declares the columns it wants shown — at most eight.
* The endpoint's URL and headers are stored on the platform side in the clear.
  Tools are therefore issued their own token, narrow and revocable, rather than
  sharing the one a human uses.
"""

import json
import logging

import odoo
from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

from ..tools.client import KMessageError
from ..tools.net import why_not_public

_logger = logging.getLogger(__name__)

LOOKUP_TOOL = 'lookup_tool'
DOCUMENT_TOOL = 'document_tool'

#: The platform caps a lookup's field allowlist at eight.
MAX_FIELDS = 8


class KMessageAiTool(models.Model):
    _name = 'kmessage.ai.tool'
    _description = 'K-Message Assistant Tool'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    name = fields.Char(
        required=True, translate=True,
        help="What this tool is called in the K-Message screen.")
    tool_name = fields.Char(
        required=True,
        help="The name the assistant knows it by. Lower-case letters, digits and "
             "underscores; K-Message shows it as lookup__<name> or send_document__<name>.")
    description = fields.Text(
        required=True, translate=True,
        help="What tells the assistant when to use this — and, just as importantly, "
             "what not to claim when it returns nothing.")

    account_id = fields.Many2one(
        'kmessage.account', required=True, ondelete='cascade', index=True,
        default=lambda self: self.env['kmessage.account']._for_company())
    company_id = fields.Many2one(related='account_id.company_id', store=True, index=True)
    capability_id = fields.Many2one(
        'kmessage.capability', required=True, ondelete='cascade',
        help="The Odoo endpoint this tool calls.")
    token_id = fields.Many2one(
        'kmessage.token',
        help="The token the assistant presents. Keep it separate from any other, "
             "so revoking the assistant's access revokes nothing else.")

    kind = fields.Selection(
        [(LOOKUP_TOOL, 'Returns rows'), (DOCUMENT_TOOL, 'Returns a document')],
        default=LOOKUP_TOOL, required=True)
    params_json = fields.Text(
        string='Parameters', default='[]',
        help='What the assistant may pass, as [{"name": "unpaid_only", "type": "boolean", '
             '"description": "…"}]. The customer\'s number is added automatically and must '
             'not be listed here.')
    fields_json = fields.Text(
        string='Columns shown', default='[]',
        help='The answer\'s fields the assistant may see, as [{"name": "number", '
             '"label": "Invoice"}]. Anything not listed never reaches the assistant.')
    items_path = fields.Char(
        default='data.rows',
        help="Where the rows sit in the answer. Our endpoints put them under data.")
    max_rows = fields.Integer(default=10)
    empty_message = fields.Char(
        translate=True,
        help="What the assistant is told when there is nothing to show.")

    active = fields.Boolean(default=True)
    state = fields.Selection(
        [('draft', 'Not published'), ('published', 'Published'), ('error', 'Problem')],
        default='draft', readonly=True, copy=False)
    remote_id = fields.Char(readonly=True, copy=False)
    last_error = fields.Char(readonly=True, copy=False)
    last_published = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        ('tool_name_unique', 'unique(account_id, tool_name)',
         'Two tools on one connection cannot share a name.'),
    ]

    # -- what gets sent ---------------------------------------------------
    def _endpoint_url(self):
        """The public address of this tool's Odoo endpoint."""
        self.ensure_one()
        base = (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')
        return '%s%s' % (base, self.capability_id.endpoint or '')

    def _api_config(self):
        """The ``api_config`` K-Message stores for this tool."""
        self.ensure_one()
        token = self.token_id.sudo()
        if not token or token.state != 'active':
            raise UserError(_("Give this tool a live token before publishing it."))

        headers = {'Authorization': 'Bearer %s' % self._token_value()}
        # The phone travels in the body, not the URL: it is a personal
        # identifier and URLs end up in logs on both sides.
        body = json.dumps(self._body(), ensure_ascii=False)

        if self.kind == DOCUMENT_TOOL:
            return {
                'tool': {
                    'name': self.tool_name,
                    'description': self.description,
                    # 'parameters' here, 'params' for a lookup tool: the two
                    # halves of the platform read different keys, and a
                    # document tool sent the lookup spelling is refused for
                    # having declared none. It must declare at least one —
                    # a document tool with no arguments would fetch the same
                    # file for every customer.
                    'parameters': self._params(),
                },
                'url': self._endpoint_url(),
                'method': 'POST',
                'headers': headers,
                'body': body,
            }

        return {
            'name': self.tool_name,
            'description': self.description,
            'params': self._params(),
            'url': self._endpoint_url(),
            'method': 'POST',
            'headers': headers,
            'body': body,
            'items_path': self.items_path or 'data.rows',
            'fields': self._fields_allowlist(),
            'max_rows': self.max_rows or 10,
            'empty_message': self.empty_message or '',
        }

    def _token_value(self):
        """The raw token to hand the platform.

        Odoo keeps only a fingerprint of a token, so a tool that is published
        later than its token was issued cannot recover the secret. Publishing
        therefore mints a fresh one and stores nothing but its fingerprint —
        the value exists just long enough to travel in this request.
        """
        self.ensure_one()
        raw = self.env.context.get('kmessage_raw_token')
        if raw:
            return raw
        raise UserError(_(
            "This tool's token cannot be read back. Press “Publish to K-Message” "
            "from the connection, which issues a fresh one."))

    def _body(self):
        """The JSON body template K-Message fills in and posts to Odoo."""
        self.ensure_one()
        body = {'phone': '{{phone_number}}'}
        for param in self._params():
            name = param.get('name')
            if name:
                body[name] = '{{args.%s}}' % name
        body.update(self._body_values())
        return body

    def _body_values(self):
        """Extra fixed values sent with every call. Bridges may add to this."""
        self.ensure_one()
        return {}

    def _params(self):
        try:
            params = json.loads(self.params_json or '[]')
        except ValueError:
            raise UserError(_("The parameters of “%s” are not valid JSON.", self.name))
        return params if isinstance(params, list) else []

    def _fields_allowlist(self):
        try:
            allow = json.loads(self.fields_json or '[]')
        except ValueError:
            raise UserError(_("The columns of “%s” are not valid JSON.", self.name))
        if not isinstance(allow, list) or not allow:
            raise UserError(_(
                "“%s” must say which columns the assistant may see — "
                "K-Message shows nothing that is not listed.", self.name))
        if len(allow) > MAX_FIELDS:
            raise UserError(_(
                "K-Message allows at most %(max)s columns; “%(tool)s” lists %(count)s.",
                max=MAX_FIELDS, tool=self.name, count=len(allow)))
        return allow

    # -- publishing -------------------------------------------------------
    def action_publish(self):
        """Create or update this tool on the K-Message side."""
        for tool in self:
            tool._publish()
        return True

    def _publish(self):
        self.ensure_one()
        account = self.account_id.sudo()
        unreachable = why_not_public(self._endpoint_url())
        if unreachable:
            raise UserError(_(
                "The assistant could not call this tool: %(why)s.\n\n"
                "K-Message refuses private and loopback addresses, so Odoo has to be "
                "published on an address the internet can reach before a tool can work.",
                why=unreachable))
        client = account._client()

        # A token whose secret we cannot read is useless to the platform, so
        # publishing always issues the value it is about to send.
        token = self.token_id.sudo()
        if not token:
            token, raw = self.env['kmessage.token'].sudo().issue(
                name=_("K-Message assistant (%s)", account.company_id.name),
                company=account.company_id,
                capabilities=self.capability_id,
            )
            self.token_id = token.id
        else:
            # Taking a tool down revokes its token, so putting the same tool
            # back up puts the same row back in service — with a secret that
            # has never been anywhere, since publishing mints one regardless.
            if not token.active:
                token.write({'active': True})
            raw = token.regenerate()
            missing = self.capability_id - token.capability_ids
            if missing:
                token.capability_ids = [(4, capability.id) for capability in missing]

        config = self.with_context(kmessage_raw_token=raw)._api_config()
        try:
            if self.remote_id:
                client.update_ai_context(self.remote_id, api_config=config, enabled=self.active)
                remote_id = self.remote_id
            else:
                created = client.create_ai_context(
                    name=self.name,
                    context_type=self.kind,
                    api_config=config,
                    enabled=self.active,
                ) or {}
                remote_id = (created.get('context') or created).get('id')
        except KMessageError as error:
            self._remember_failure(error)
            if error.status == 403:
                raise UserError(_(
                    "K-Message would not let this token publish a tool: %s\n\n"
                    "The token's role needs permission to change chatbot settings, "
                    "and the tenant needs AI replies switched on.", error))
            raise UserError(_("K-Message refused the tool: %s", error))

        self.write({
            'remote_id': remote_id or self.remote_id,
            'state': 'published',
            'last_error': False,
            'last_published': fields.Datetime.now(),
        })
        return True

    def _remember_failure(self, error):
        """Record why publishing failed, on a cursor the raise cannot undo.

        Writing it on this cursor would be pointless theatre: the UserError
        raised a line later rolls the whole request back, taking the
        explanation with it and leaving a tool that says "not published" with
        no reason anywhere. A separate cursor commits the reason and lets the
        error go to the person who pressed the button.
        """
        self.ensure_one()
        try:
            with odoo.registry(self.env.cr.dbname).cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})[self._name].browse(self.id).write({
                    'state': 'error',
                    'last_error': str(error)[:500],
                })
        except Exception:  # noqa: BLE001 - the real error is the one being raised
            _logger.exception('K-Message: could not record why %s failed to publish', self.name)

    def action_unpublish(self):
        """Remove the tool from K-Message, and retire the secret it was given."""
        for tool in self:
            if tool.remote_id:
                try:
                    tool.account_id.sudo()._client().delete_ai_context(tool.remote_id)
                except KMessageError as error:
                    raise UserError(_("K-Message would not remove the tool: %s", error))
                tool.write({'remote_id': False, 'state': 'draft'})
            else:
                tool.state = 'draft'
            tool._retire_token()
        return True

    def _retire_token(self):
        """Revoke the token this tool handed to K-Message.

        The platform stores a tool's headers in the clear, so the moment a
        token is published it is a secret somebody else is holding — and has
        logged, and has backed up. Deleting the tool there takes the copy out
        of the screen; it does not make the key stop working. Revoking it here
        does, which is the difference between a tool that was taken down and
        one whose way in was taken away.

        A token somebody attached to a second tool is left alone: revoking it
        would break the tool still using it.
        """
        self.ensure_one()
        token = self.token_id.sudo()
        if not token or not token.active:
            return
        shared = self.with_context(active_test=False).sudo().search_count([
            ('id', '!=', self.id),
            ('token_id', '=', token.id),
            ('remote_id', '!=', False),
        ])
        if shared:
            return
        token.action_revoke()

    def write(self, values):
        """Archiving a published tool takes it down rather than hiding it.

        ``active`` only reaches K-Message at publish time, so a tool switched
        off here and left alone would go on being offered to the assistant,
        still holding a working token, with nobody in Odoo looking at it.
        """
        taken_down = self.browse()
        if 'active' in values and not values['active']:
            taken_down = self.filtered(lambda tool: tool.remote_id)
        result = super().write(values)
        if taken_down:
            taken_down.action_unpublish()
        return result

    def unlink(self):
        """Take the tool off the platform on the way out — but go either way.

        Unpublishing first is the point: a tool left behind on K-Message keeps
        the assistant offering an answer this Odoo no longer gives. But a
        platform that cannot be reached must not make the row undeletable —
        somebody clearing up after an outage would be stuck, with no way
        forward that does not involve the database. So a failure here is a
        warning in the log and a revoked token, which is what actually closes
        the door: the tool that stays behind can no longer get in.
        """
        going = self.filtered(lambda tool: tool.remote_id or tool.token_id)
        for tool in going:
            try:
                tool.action_unpublish()
            except (UserError, KMessageError) as error:
                _logger.warning(
                    'K-Message: %s could not be taken off the platform (%s); '
                    'revoking its token and deleting it here anyway',
                    tool.name, error)
                tool.token_id.sudo().action_revoke()
        return super().unlink()
