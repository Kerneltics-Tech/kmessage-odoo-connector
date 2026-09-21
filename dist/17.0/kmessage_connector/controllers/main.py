# -*- coding: utf-8 -*-
"""The door K-Message knocks on.

Plain HTTP with real status codes rather than Odoo's JSON-RPC, because the
callers are a flow builder that branches on ``http:2xx`` and an assistant that
reads ordinary REST. A JSON-RPC endpoint answers `200 OK` with the error inside
the body, which both of them would read as success.

Two routes carry everything:

``POST /kmessage/api/v1/<capability>``  a question, authenticated by token
``POST /kmessage/api/v1/webhook``       news from K-Message, authenticated by HMAC
"""

import json
import logging
import time
from collections import defaultdict, deque

from werkzeug.exceptions import RequestEntityTooLarge

from odoo import _, http
from odoo.http import Response, request

from ..models.kmessage_api import KMessageApiError, KMessageFile
from ..tools.signature import verify_signature

_logger = logging.getLogger(__name__)

#: Requests seen per token in the last minute, per worker process. Deliberately
#: in memory: the limit exists to stop a runaway loop, and a database write per
#: request to count requests would be the heavier problem.
_RECENT = defaultdict(deque)
_WINDOW = 60.0

#: The largest body either route will read. Both of them are asked questions
#: about one customer and answered from one signed envelope, so a megabyte is
#: already generous — and without a ceiling Odoo's own default lets an
#: unauthenticated caller make this worker hold 128MB while it reads a body
#: that was never going to be a question.
MAX_BODY_BYTES = 1024 * 1024


def _json_response(payload, status=200):
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str),
        status=status,
        content_type='application/json; charset=utf-8',
    )


def _error(code, message, status=400):
    return _json_response({'ok': False, 'error': {'code': code, 'message': message}}, status)


def _body_is_enormous():
    """True when the caller announced more than this door will ever read."""
    try:
        return (request.httprequest.content_length or 0) > MAX_BODY_BYTES
    except (TypeError, ValueError):
        return False


def _too_large():
    return KMessageApiError('body_too_large', _("That request was too big to read."), 413)


def _rate_limited(token):
    """True when this token has already used up its minute."""
    limit = token.rate_limit or 0
    if limit <= 0:
        return False
    now = time.monotonic()
    seen = _RECENT[token.id]
    while seen and now - seen[0] > _WINDOW:
        seen.popleft()
    if len(seen) >= limit:
        return True
    seen.append(now)
    return False


class KMessageApiController(http.Controller):

    # -- authentication ---------------------------------------------------
    def _token(self):
        """The token presented, as a record. Raises on anything wrong."""
        header = request.httprequest.headers.get('Authorization') or ''
        raw = header[7:].strip() if header[:7].lower() == 'bearer ' else ''
        if not raw:
            raw = (request.httprequest.headers.get('X-KMessage-Token') or '').strip()
        if not raw:
            raise KMessageApiError('token_missing', _("Send your token in the Authorization header."), 401)

        token = request.env['kmessage.token'].sudo().authenticate(raw)
        if not token:
            raise KMessageApiError('token_invalid', _("That token is not valid here."), 401)
        if _rate_limited(token):
            raise KMessageApiError('rate_limited', _("Too many requests; try again shortly."), 429)

        account = request.env['kmessage.account'].sudo()._for_company(token.company_id)
        if account and not account.inbound_enabled:
            raise KMessageApiError('inbound_disabled', _("This Odoo is not answering questions right now."), 503)
        return token

    def _payload(self):
        """The request body as a dict, whether it arrived as JSON or a form."""
        if _body_is_enormous():
            raise _too_large()
        try:
            raw = request.httprequest.get_data() or b''
        except RequestEntityTooLarge:
            raise _too_large()
        if raw:
            try:
                parsed = json.loads(raw.decode('utf-8'))
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, UnicodeDecodeError):
                pass
        form = request.httprequest.values.to_dict() if request.httprequest.values else {}
        return form or {}

    def _env_for(self, token):
        """An environment running as the connector, in the token's company.

        The company travels in ``allowed_company_ids`` rather than through
        ``with_company``: that is a recordset method, and what is needed here
        is the environment every later ``self.env.company`` will read from.
        """
        context = dict(
            request.env.context,
            allowed_company_ids=[token.company_id.id],
            lang=(request.httprequest.headers.get('X-KMessage-Lang') or 'en_US'),
        )
        return request.env(su=True, context=context)

    # -- the capability endpoints ----------------------------------------
    @http.route(
        ['/kmessage/api/v1/ping'],
        type='http', auth='public', methods=['GET', 'POST'], csrf=False, save_session=False,
        max_content_length=MAX_BODY_BYTES)
    def ping(self, **kwargs):
        return self._run('ping')

    @http.route(
        ['/kmessage/api/v1/tools.json'],
        type='http', auth='public', methods=['GET'], csrf=False, save_session=False,
        max_content_length=MAX_BODY_BYTES)
    def tools(self, **kwargs):
        return self._run('tools')

    @http.route(
        ['/kmessage/api/v1/<path:capability>'],
        type='http', auth='public', methods=['GET', 'POST'], csrf=False, save_session=False,
        max_content_length=MAX_BODY_BYTES)
    def capability(self, capability, **kwargs):
        code = capability.strip('/').replace('/', '_').replace('-', '_').replace('.json', '')
        return self._run(code)

    def _run(self, code):
        try:
            token = self._token()
            env = self._env_for(token)
            result = env['kmessage.api'].dispatch(code, self._payload(), token.with_env(env))
            token.sudo().note_use()
            if isinstance(result, KMessageFile):
                return Response(
                    result.content,
                    status=200,
                    content_type=result.mimetype,
                    headers=[
                        ('Content-Disposition',
                         'attachment; filename="%s"' % result.filename.replace('"', '')),
                        ('Content-Length', str(len(result.content))),
                    ],
                )
            return _json_response({'ok': True, 'data': result})
        except KMessageApiError as error:
            return _error(error.code, error.message, error.status)
        except Exception:  # noqa: BLE001 - never leak a traceback to the caller
            _logger.exception('K-Message: %s failed', code)
            return _error('internal_error', _("Something went wrong in Odoo."), 500)

    # -- inbound webhook --------------------------------------------------
    @http.route(
        ['/kmessage/api/v1/webhook'],
        type='http', auth='public', methods=['POST'], csrf=False, save_session=False,
        max_content_length=MAX_BODY_BYTES)
    def webhook(self, **kwargs):
        # Nothing here is authenticated until the signature is checked, so the
        # size of the body is the first thing to refuse.
        try:
            if _body_is_enormous():
                raise _too_large()
            raw = request.httprequest.get_data() or b''
        except (RequestEntityTooLarge, KMessageApiError):
            refusal = _too_large()
            return _error(refusal.code, refusal.message, refusal.status)
        signature = request.httprequest.headers.get('X-Webhook-Signature')

        accounts = request.env['kmessage.account'].sudo().search([('inbound_enabled', '=', True)])
        account = next(
            (candidate for candidate in accounts
             if candidate.webhook_secret and verify_signature(raw, signature, candidate.webhook_secret)),
            None,
        )
        if not account:
            # Say nothing useful: an unsigned caller learns only that it failed.
            _logger.warning('K-Message: rejected a webhook with %s signature',
                            'a bad' if signature else 'no')
            return _error('signature_invalid', _("Signature missing or wrong."), 401)

        try:
            envelope = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return _error('bad_payload', _("The body was not JSON."), 400)

        event = request.env['kmessage.event'].sudo().with_company(account.company_id).record_event(
            account, envelope)
        # Always 200 once the signature is good: K-Message retries on anything
        # else, and a retry of an event we chose not to act on is pure noise.
        return _json_response({'ok': True, 'event_id': event.id, 'outcome': event.outcome or ''})
