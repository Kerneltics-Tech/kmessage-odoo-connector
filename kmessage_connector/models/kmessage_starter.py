# -*- coding: utf-8 -*-
"""The templates a company would otherwise have to write themselves.

Nothing in this addon can send a word until an approved WhatsApp template
exists, and writing one means learning Meta's rules about categories, sample
values and media headers, then waiting for a review. That is a day of work
standing between "I installed the connector" and "my customers get their
invoices", and it is the same day for every company that installs it.

So the connector writes them. On connect it submits a small set of templates —
in Arabic, in the tenant's own account, under names of its own — and Meta
approves them in the usual few minutes. A company that wants different wording
edits them in K-Message afterwards, the way they would have had to write them
anyway; a company that does not need never think about it.

Three rules the catalogue below obeys, each learned from what Meta refuses:

* **UTILITY, never MARKETING.** These are transactional messages about a
  document the customer is already part of. A marketing category would drag
  them into opt-in rules they do not belong in.
* **A document header needs a sample.** Meta will not approve a template that
  says it carries a PDF without being shown one, so the connector renders a
  real document and uploads it.
* **Every placeholder needs a sample value**, and the samples must read like
  the real thing — a reviewer rejects ``{{1}} {{2}} {{3}}`` filled with "test".
* **A body may not begin or end with a placeholder.** Meta refuses it outright
  («يجب ألا تكون المتغيرات في بداية القالب أو نهايته»), and a trailing full
  stop does not save it — the words have to carry on after the last variable.
"""

import logging

from odoo import _, api, models

from ..tools.client import KMessageError

_logger = logging.getLogger(__name__)

#: Meta's own words for a template that has not been reviewed yet.
PENDING_STATES = ('PENDING', 'IN_APPEAL', 'PENDING_DELETION')


class KMessageStarter(models.AbstractModel):
    _name = 'kmessage.starter'
    _description = 'K-Message Starter Templates'

    # -- the catalogue ----------------------------------------------------
    @api.model
    def catalogue(self):
        """Every template the connector offers to create.

        Core knows about none of them by itself — what is worth announcing
        depends on which Odoo apps are installed, so each bridge adds its own
        and this list stays empty on an Odoo with neither invoicing nor sales.
        """
        return []

    @api.model
    def _entry(self, name, body, samples, header_type='DOCUMENT', language='ar',
               category='UTILITY', display_name=None, footer=None, buttons=None):
        """One catalogue row, with the shape the platform expects."""
        return {
            'name': name,
            'display_name': display_name or name,
            'language': language,
            'category': category,
            'header_type': header_type,
            'body': body,
            'footer': footer,
            'buttons': buttons or [],
            'samples': [
                {'component': 'body', 'index': index, 'value': value}
                for index, value in enumerate(samples, start=1)
            ],
        }

    # -- provisioning -----------------------------------------------------
    @api.model
    def provision(self, account, sample_document=None):
        """Create and submit every template the tenant does not already have.

        Returns a list of ``(name, outcome)``. Never raises: a template that
        Meta or the platform refuses is a line in the connect summary, not a
        failed installation — and the ones that did go through are still there.
        """
        catalogue = self.catalogue()
        if not catalogue:
            return []

        client = account.sudo()._client()
        existing = self._existing_names(client)
        sample_document = sample_document or self._sample_document(account)

        outcomes = []
        for entry in catalogue:
            if entry['name'] in existing:
                outcomes.append((entry['name'], _("already there")))
                continue
            outcomes.append((entry['name'], self._provision_one(account, client, entry, sample_document)))
        return outcomes

    @api.model
    def _existing_names(self, client):
        try:
            return {row.get('name') for row in client.all_templates()}
        except KMessageError as error:
            _logger.info('K-Message: could not read the existing templates (%s)', error)
            return set()

    @api.model
    def _provision_one(self, account, client, entry, sample_document):
        """Write one template, give Meta its sample, and submit it."""
        account_name = account.sudo().account_name or self._default_account(client)
        if not account_name:
            return _("no WhatsApp number to create it on")

        try:
            created = client.create_template(
                account_name=account_name,
                name=entry['name'],
                display_name=entry.get('display_name'),
                language=entry['language'],
                category=entry['category'],
                body=entry['body'],
                header_type=entry.get('header_type') or None,
                footer=entry.get('footer'),
                buttons=entry.get('buttons'),
                samples=entry.get('samples'),
            ) or {}
        except KMessageError as error:
            return self._refusal(error)

        template_id = created.get('id')
        if not template_id:
            return _("the platform did not say which template it created")

        # A document header is refused review until Meta has seen a sample, so
        # the handle has to be attached before the template is submitted.
        if (entry.get('header_type') or '') == 'DOCUMENT':
            if not sample_document:
                return _("created, but there was no document to show Meta as a sample")
            try:
                handle = client.upload_template_media(
                    account_name, sample_document[0], sample_document[1], sample_document[2])
                if not handle:
                    return _("created, but Meta did not accept the sample document")
                client.update_template(template_id, **{
                    'whatsapp_account': account_name,
                    'name': entry['name'],
                    'language': entry['language'],
                    'category': entry['category'],
                    'header_type': 'DOCUMENT',
                    'header_content': handle,
                    'body_content': entry['body'],
                    'sample_values': entry.get('samples') or [],
                })
            except KMessageError as error:
                return _("created, but the sample could not be attached: %s", error)

        try:
            published = client.publish_template(template_id) or {}
        except KMessageError as error:
            return _("created, but not submitted: %s", error)

        status = (published.get('status') or '').upper()
        if status in PENDING_STATES:
            return _("submitted to Meta, waiting for approval")
        if status == 'APPROVED':
            return _("approved and ready to send")
        return _("submitted (%s)", status or _("no status"))

    @api.model
    def _default_account(self, client):
        """The tenant's own default sending number, when none was chosen."""
        try:
            accounts = client.accounts()
        except KMessageError:
            return ''
        for account in accounts:
            if account.get('is_default_outgoing'):
                return account.get('name') or ''
        return (accounts[0].get('name') or '') if accounts else ''

    @api.model
    def _refusal(self, error):
        if error.operator_only:
            return _("only your K-Message provider may add templates on this plan")
        if error.status == 403:
            return _("this token may not add templates")
        return _("refused: %s", error)

    # -- the sample Meta is shown ----------------------------------------
    @api.model
    def _sample_document(self, account):
        """A real printed document to show Meta, as ``(filename, bytes, mime)``.

        A real one on purpose: Meta's reviewers look at it, and a blank page is
        a slower approval than an invoice. Bridges offer whatever their app can
        print; failing that the connector sends a plain page rather than
        nothing, because a template with a document header cannot be reviewed
        without one.
        """
        rendered = self._sample_from_odoo(account)
        if rendered:
            return rendered
        return ('sample.pdf', self._blank_pdf(), 'application/pdf')

    @api.model
    def _sample_from_odoo(self, account):
        """A printout of a real record. Bridges override; core has none."""
        return None

    @api.model
    def _blank_pdf(self):
        """The smallest valid PDF, for when this Odoo has nothing to print."""
        return (
            b'%PDF-1.4\n'
            b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
            b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
            b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n'
            b'trailer<</Root 1 0 R>>\n'
            b'%%EOF\n'
        )


class KMessageStarterAutomations(models.AbstractModel):
    """The rules a company would otherwise have to write themselves.

    Writing the templates was only half of it. A template that nobody has
    pointed a rule at sends nothing, and building that rule means knowing
    which trigger matches which template and which Odoo field fills each
    ``{{n}}`` — four screens of knowledge that is the same for every company
    that installs this.

    So the connector offers the rules too, filled in. They are marked with a
    ``starter_key``, and that marker is the whole of the restore story: a
    default somebody deleted can be offered again, a default somebody edited
    is never overwritten, and a rule somebody wrote themselves is never
    touched, because it carries no marker at all.

    One of them arrives switched on — the invoice. It is what people install
    this for, and a rule that has to be discovered and switched on is a rule
    that does not run. The rest arrive off, because "we also messaged all your
    customers about their quotations" is not a surprise anybody wants.
    """

    _name = 'kmessage.starter.automation'
    _description = 'K-Message Starter Automations'

    @api.model
    def catalogue(self):
        """Every rule the connector offers. Empty here; the bridges fill it."""
        return []

    @api.model
    def _rule(self, key, name, trigger, template, params, active=False,
              attach_document=True, cooldown_hours=24):
        """One offered rule.

        ``params`` is the field path for each placeholder, in order, so
        ``['partner_id.name', 'name']`` fills ``{{1}}`` and ``{{2}}``.
        """
        return {
            'key': key,
            'name': name,
            'trigger': trigger,
            'template': template,
            'params': params,
            'active': active,
            'attach_document': attach_document,
            'cooldown_hours': cooldown_hours,
        }

    @api.model
    def ensure(self, account, keys=None):
        """Create whatever offered rule is missing. Returns (made, held_back).

        Nothing is ever updated: a rule that exists is the customer's, whether
        they changed it, switched it off, or left it as it came.
        """
        automations = self.env['kmessage.automation'].sudo()
        existing = set(automations.with_context(active_test=False).search(
            [('account_id', '=', account.id),
             ('starter_key', '!=', False)]).mapped('starter_key'))

        made, held_back = [], []
        for rule in self.catalogue():
            if keys is not None and rule['key'] not in keys:
                continue
            if rule['key'] in existing:
                continue
            template = self.env['kmessage.template'].sudo().search([
                ('account_id', '=', account.id),
                ('name', '=', rule['template']),
            ], limit=1)
            if not template:
                # The template is not there — usually because this tenant
                # declined to have them written. A rule pointing at nothing
                # would be worse than no rule.
                held_back.append(rule['key'])
                continue
            automations.create({
                'name': rule['name'],
                'starter_key': rule['key'],
                'trigger': rule['trigger'],
                'template_id': template.id,
                'account_id': account.id,
                'company_id': account.company_id.id,
                'active': rule['active'],
                'attach_document': rule['attach_document'] and template.takes_document,
                'cooldown_hours': rule['cooldown_hours'],
                'param_ids': [
                    (0, 0, {'index': index, 'source': 'field', 'field_path': path})
                    for index, path in enumerate(rule['params'], start=1)
                ],
            })
            made.append(rule['key'])
        return made, held_back
