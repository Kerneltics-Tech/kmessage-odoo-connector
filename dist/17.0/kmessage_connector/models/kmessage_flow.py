# -*- coding: utf-8 -*-
"""The answer without an assistant.

A flow is a tree: K-Message sends a message, waits for a reply, calls an HTTP
endpoint, branches on what came back. No model, no AI feature on the plan —
which makes it the only path to "a customer asked about their invoice and got
a real answer" for a tenant whose plan has no assistant at all.

Two of them ship with the addon as JSON, and they used to be a page of
instructions: import this with curl, then open the graph and replace the URL,
then replace the token. Three manual steps, two of which are a secret being
copied by hand into a form.

So the connector imports them, with the URL and the token already in them. It
can, where it cannot create a webhook, because creating a flow is not one of
the operator-only writes — a tenant key is enough.

They arrive **disabled**, every time. A flow that answered customers the
moment it was imported would be this addon deciding, on a company's behalf,
what their WhatsApp says back — and that is theirs to switch on.
"""

import json
import logging
import os

from odoo import _, api, models

from ..tools.client import KMessageError

_logger = logging.getLogger(__name__)

#: Where the shipped graphs live, relative to this module.
FLOW_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'flows')

#: What the files carry in place of the two things only Odoo knows.
URL_PLACEHOLDER = 'https://odoo.example.com'
TOKEN_PLACEHOLDER = 'Bearer kmc_REPLACE_ME'


class KMessageFlow(models.AbstractModel):
    _name = 'kmessage.flow'
    _description = 'K-Message Flow Graphs'

    @api.model
    def shipped(self):
        """Every graph in ``data/flows``, newest wording and all."""
        graphs = []
        if not os.path.isdir(FLOW_DIR):
            return graphs
        for filename in sorted(os.listdir(FLOW_DIR)):
            if not filename.endswith('.json'):
                continue
            try:
                with open(os.path.join(FLOW_DIR, filename), encoding='utf-8') as handle:
                    graphs.append(json.load(handle))
            except (OSError, ValueError):
                _logger.exception('K-Message: could not read the flow %s', filename)
        return graphs

    @api.model
    def _fill_in(self, payload, base_url, token):
        """Put this Odoo's address and token where the file left blanks."""
        text = json.dumps(payload)
        text = text.replace(URL_PLACEHOLDER, base_url.rstrip('/'))
        if token:
            text = text.replace(TOKEN_PLACEHOLDER, 'Bearer %s' % token)
        filled = json.loads(text)
        # Never on arrival. Switching a flow on is the company's decision.
        filled['enabled'] = False
        return filled

    @api.model
    def import_all(self, account, token=None):
        """Import whatever is not there yet. Returns (made, skipped, failed)."""
        graphs = self.shipped()
        if not graphs:
            return [], [], []

        base_url = (self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').strip()
        client = account._client()

        try:
            existing = {(flow.get('name') or '') for flow in client.flows()}
        except KMessageError as error:
            # Not being able to look is not a reason to import blindly: a
            # second copy of a flow somebody has since edited is worse than
            # no copy at all.
            _logger.info('K-Message: could not list the flows (%s)', error)
            return [], [], [_("could not read the existing flows: %s", error)]

        made, skipped, failed = [], [], []
        for graph in graphs:
            name = graph.get('name') or ''
            if name in existing:
                skipped.append(name)
                continue
            try:
                client.create_flow(self._fill_in(graph, base_url, token))
                made.append(name)
            except KMessageError as error:
                _logger.info('K-Message: could not import the flow %s (%s)', name, error)
                failed.append('%s — %s' % (name, error))
        return made, skipped, failed
