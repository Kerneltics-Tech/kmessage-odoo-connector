# -*- coding: utf-8 -*-
"""A thin client for the K-Message HTTP API.

Deliberately free of Odoo imports so it can be read, reasoned about and tested
on its own. Everything it returns is already unwrapped from the service's
``{"status": …, "message": …, "data": …}`` envelope, and every failure is one
exception type carrying the pieces a caller needs to decide what to do:

    status      the HTTP status
    error_type  the service's machine-readable reason, when it sent one
                (``operator_only`` is the one that matters: the tenant is on a
                managed plan and only their provider may make that change)
"""

import json
import logging

import requests

_logger = logging.getLogger(__name__)

#: Where K-Message is, unless a tenant is hosted somewhere else. It is the
#: same address for every customer, so asking each of them to type it is a
#: field that can only ever be got wrong.
DEFAULT_BASE_URL = 'https://api.k-message.kerneltics.com'

DEFAULT_TIMEOUT = 30
SEND_TIMEOUT = 60
USER_AGENT = 'odoo-kmessage-connector/1.0'

#: Raised by the service when the tenant's plan reserves an action for the
#: provider. Not an error in the connector — a fact about the account.
ERROR_OPERATOR_ONLY = 'operator_only'


class KMessageError(Exception):
    """Any non-2xx answer, or a reply that did not parse."""

    def __init__(self, message, status=None, error_type=None, payload=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.error_type = error_type
        self.payload = payload

    @property
    def operator_only(self):
        return self.error_type == ERROR_OPERATOR_ONLY

    def __str__(self):
        if self.status:
            return '%s (HTTP %s)' % (self.message, self.status)
        return self.message


def _redirected(response):
    """The refusal for an answer that points somewhere else.

    Redirects are never followed. The API key travels in ``X-API-Key``, a
    header of our own, and ``requests`` only strips ``Authorization`` when a
    redirect crosses to another host — a custom header is carried along and
    would hand the key to whatever the redirect names. A K-Message URL that
    redirects is a URL that is wrong, and saying so is more useful than
    quietly following it.
    """
    destination = response.headers.get('Location') or ''
    return KMessageError(
        'K-Message answered with a redirect to %s. Set the K-Message URL to the '
        'address it finally answers on.' % (destination[:200] or 'somewhere else'),
        status=response.status_code,
    )


class KMessageClient:
    """Calls one tenant's K-Message API with one API key."""

    def __init__(self, base_url, api_key, timeout=DEFAULT_TIMEOUT, session=None):
        self.base_url = (base_url or '').rstrip('/')
        self.api_key = api_key or ''
        self.timeout = timeout
        self._session = session or requests.Session()

    # -- plumbing ---------------------------------------------------------
    def _headers(self, extra=None):
        headers = {
            'X-API-Key': self.api_key,
            'Accept': 'application/json',
            'User-Agent': USER_AGENT,
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method, path, timeout=None, **kwargs):
        if not self.base_url or not self.api_key:
            raise KMessageError('K-Message is not configured')
        url = '%s%s' % (self.base_url, path)
        try:
            response = self._session.request(
                method,
                url,
                headers=self._headers(kwargs.pop('headers', None)),
                timeout=timeout or self.timeout,
                allow_redirects=False,
                **kwargs
            )
        except requests.exceptions.Timeout:
            raise KMessageError('K-Message did not answer in time')
        except requests.exceptions.RequestException as error:
            raise KMessageError('Could not reach K-Message: %s' % error)
        if 300 <= response.status_code < 400:
            raise _redirected(response)
        return self._unwrap(response)

    @staticmethod
    def _unwrap(response):
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400 or (
            isinstance(payload, dict) and payload.get('status') == 'error'
        ):
            message = 'HTTP %s' % response.status_code
            error_type = None
            if isinstance(payload, dict):
                message = payload.get('message') or message
                error_type = payload.get('error_type')
            elif response.text:
                message = response.text[:200]
            raise KMessageError(
                message,
                status=response.status_code,
                error_type=error_type,
                payload=payload,
            )

        if isinstance(payload, dict) and 'data' in payload:
            return payload['data']
        return payload

    # -- reads ------------------------------------------------------------
    def me(self):
        """The user the API key belongs to, with its role and permissions."""
        return self._request('GET', '/api/me')

    def accounts(self):
        """The tenant's WhatsApp numbers."""
        return (self._request('GET', '/api/accounts') or {}).get('accounts', [])

    def templates(self, page=1, limit=50):
        data = self._request('GET', '/api/templates', params={'page': page, 'limit': limit}) or {}
        return data.get('templates', []), data.get('total', 0)

    def all_templates(self, page_size=50, max_pages=20):
        """Every template, walked page by page."""
        collected, page = [], 1
        while page <= max_pages:
            rows, total = self.templates(page=page, limit=page_size)
            collected.extend(rows)
            if len(collected) >= total or not rows:
                break
            page += 1
        return collected

    def webhooks(self):
        """Existing subscriptions plus the catalogue of events on offer."""
        data = self._request('GET', '/api/webhooks') or {}
        return data.get('webhooks', []), data.get('available_events', [])

    def find_contact(self, phone):
        """The contact whose number matches, or None."""
        data = self._request('GET', '/api/contacts', params={'search': phone, 'limit': 5}) or {}
        digits = ''.join(ch for ch in (phone or '') if ch.isdigit())
        for contact in data.get('contacts', []):
            existing = ''.join(ch for ch in (contact.get('phone_number') or '') if ch.isdigit())
            if existing and digits and (existing == digits or existing.endswith(digits[-9:])):
                return contact
        return None

    # -- writes -----------------------------------------------------------
    def create_webhook(self, name, url, events, secret=None, headers=None, is_active=True):
        """Subscribe a URL to events.

        Managed tenants answer 403 ``operator_only`` here; the caller is
        expected to fall back to telling the administrator what to set up by
        hand rather than treating it as a failure.
        """
        body = {
            'name': name,
            'url': url,
            'events': list(events or []),
            'is_active': is_active,
        }
        if secret:
            body['secret'] = secret
        if headers:
            body['headers'] = headers
        return self._request('POST', '/api/webhooks', json=body)

    def update_webhook(self, webhook_id, **values):
        return self._request('PUT', '/api/webhooks/%s' % webhook_id, json=values)

    def delete_webhook(self, webhook_id):
        return self._request('DELETE', '/api/webhooks/%s' % webhook_id)

    # -- templates the connector provisions -------------------------------
    # A tenant's templates are normally written by hand in K-Message and then
    # approved by Meta, which is a day's work before a single invoice can go
    # out. These three calls are how the connector does that for them: write
    # the template, give Meta the sample document it insists on for a document
    # header, and submit it.
    def create_template(self, account_name, name, language, category, body,
                        header_type=None, header_content=None, footer=None,
                        buttons=None, samples=None, display_name=None):
        body_values = {
            'whatsapp_account': account_name,
            'name': name,
            'display_name': display_name or name,
            'language': language,
            'category': category,
            'body_content': body,
            'footer_content': footer or '',
            'buttons': buttons or [],
            'sample_values': samples or [],
        }
        if header_type:
            body_values['header_type'] = header_type
        if header_content:
            body_values['header_content'] = header_content
        return self._request('POST', '/api/templates', json=body_values)

    def upload_template_media(self, account_name, filename, content, mimetype='application/pdf'):
        """Give Meta the sample file a media header needs; returns its handle."""
        data = self._request(
            'POST', '/api/templates/upload-media',
            data={'account': account_name},
            files={'file': (filename, content, mimetype)},
            timeout=SEND_TIMEOUT,
        ) or {}
        return data.get('handle') or ''

    def update_template(self, template_id, **values):
        return self._request('PUT', '/api/templates/%s' % template_id, json=values)

    def publish_template(self, template_id):
        """Submit to Meta. The answer carries the status Meta gave it."""
        return self._request('POST', '/api/templates/%s/publish' % template_id, json={})

    # -- wiring the whole connection in one call --------------------------
    def connect_odoo(self, webhook_url, webhook_secret, events, tools):
        """Hand K-Message everything it needs to call this Odoo back.

        Creating a webhook is otherwise reserved for the provider, which left
        the customer copying an address and a secret into a support ticket.
        This endpoint takes them directly. A platform that does not have it
        yet answers 404, and the caller falls back to the older path.
        """
        return self._request('POST', '/api/integrations/odoo/connect', json={
            'webhook_url': webhook_url,
            'webhook_secret': webhook_secret,
            'events': list(events or []),
            'tools': tools or [],
        }, timeout=SEND_TIMEOUT)

    # -- the assistant's tools --------------------------------------------
    # A "context" of type lookup_tool or document_tool is how K-Message lets a
    # company point the assistant at its own HTTP endpoint. Creating one needs
    # the chatbot-settings permission on the token's role, and the tenant must
    # have AI replies enabled — both refusals come back as plain 403s.
    def ai_contexts(self):
        data = self._request('GET', '/api/chatbot/ai-contexts') or {}
        return data.get('contexts', [])

    def create_ai_context(self, name, context_type, api_config, enabled=True, priority=10):
        return self._request('POST', '/api/chatbot/ai-contexts', json={
            'name': name,
            'context_type': context_type,
            'api_config': api_config,
            'enabled': enabled,
            'priority': priority,
        })

    def update_ai_context(self, context_id, **values):
        return self._request('PUT', '/api/chatbot/ai-contexts/%s' % context_id, json=values)

    def delete_ai_context(self, context_id):
        return self._request('DELETE', '/api/chatbot/ai-contexts/%s' % context_id)

    def flows(self):
        data = self._request('GET', '/api/chatbot/flows') or {}
        return data.get('flows') or data.get('data') or []

    def create_flow(self, payload):
        """Import one flow graph. The file is already the whole request body."""
        return self._request('POST', '/api/chatbot/flows', json=payload)

    def send_template(
        self,
        phone_number,
        template_name,
        language=None,
        account_name=None,
        template_params=None,
        button_params=None,
        reference=None,
        bot_reply=True,
        document=None,
    ):
        """Send a template message; returns the created message id.

        ``document`` is ``(filename, bytes, content_type)`` for a template whose
        header is a document. With one, the request goes out as multipart —
        that is the only shape that carries a file; without one, plain JSON.
        """
        fields = {
            'phone_number': phone_number,
            'template_name': template_name,
            'bot_reply': 'true' if bot_reply else 'false',
        }
        if language:
            fields['language'] = language
        if account_name:
            # Never stripped: account names may legitimately end in a space.
            fields['account_name'] = account_name
        if reference:
            fields['reference'] = reference

        params = template_params or {}
        buttons = button_params or {}

        if document:
            filename, content, content_type = document
            fields['template_params'] = json.dumps(params)
            fields['button_params'] = json.dumps(buttons)
            fields['header_media_filename'] = filename
            files = {
                'header_file': (filename, content, content_type or 'application/pdf'),
            }
            data = self._request(
                'POST', '/api/messages/template',
                data=fields, files=files, timeout=SEND_TIMEOUT,
            )
        else:
            body = dict(fields, template_params=params, button_params=buttons)
            body['bot_reply'] = bot_reply
            data = self._request(
                'POST', '/api/messages/template', json=body, timeout=SEND_TIMEOUT,
            )
        return self._message_id(data)

    def send_text(self, contact_id, body):
        data = self._request(
            'POST', '/api/contacts/%s/messages' % contact_id,
            json={'type': 'text', 'content': {'body': body}},
            timeout=SEND_TIMEOUT,
        )
        return self._message_id(data)

    @staticmethod
    def _message_id(data):
        """Lift the created message's id out of whatever nesting it arrived in."""
        for candidate in (data, (data or {}).get('message') if isinstance(data, dict) else None):
            if isinstance(candidate, dict) and candidate.get('id'):
                return candidate['id']
        _logger.warning('K-Message accepted the send but returned no message id: %s', data)
        return False
