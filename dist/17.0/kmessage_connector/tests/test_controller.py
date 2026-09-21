# -*- coding: utf-8 -*-
"""The door itself, knocked on the way K-Message knocks on it.

These go through real HTTP rather than calling the controller's methods,
because the things worth checking are the things a direct call would skip: that
a refusal comes back as a status a flow builder can branch on, that the routes
are shaped the way the capability rows say they are, and that an unsigned body
gets nowhere near the event log.
"""

import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from odoo.addons.kmessage_connector.tools.signature import compute_signature

from .common import CUSTOMER_MOBILE, connect, issue_token

API = '/kmessage/api/v1'


@tagged('post_install', '-at_install')
class TestTheDoor(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # No fake platform here: nothing on these routes calls out. What the
        # connection record is for is the inbound switch and the shared secret.
        connect(cls, 'https://kmessage.example.test')

    def setUp(self):
        super().setUp()
        self.token, self.raw = issue_token(self.env)

    def call(self, path, payload=None, token=None, headers=None):
        request_headers = {'Content-Type': 'application/json'}
        if token is not False:
            request_headers['Authorization'] = 'Bearer %s' % (token or self.raw)
        request_headers.update(headers or {})
        return self.request(API + path, json.dumps(payload or {}), request_headers)

    def request(self, path, body, headers):
        """One HTTP call, with the fixtures on disk and the answer read back.

        The request runs on this test's cursor but in an environment of its
        own, so what the test has written has to be flushed for the controller
        to see it, and what the controller wrote has to be read again.
        """
        self.env.flush_all()
        answer = self.url_open(path, data=body, headers=headers, timeout=30)
        self.env.invalidate_all()
        return answer

    # -- authentication ---------------------------------------------------
    def test_without_a_token_nothing_is_answered(self):
        answer = self.call('/ping', token=False)
        self.assertEqual(answer.status_code, 401)
        body = answer.json()
        self.assertFalse(body['ok'])
        self.assertEqual(body['error']['code'], 'token_missing')

    def test_a_token_that_is_not_ours_is_not_valid_here(self):
        answer = self.call('/ping', token='kmc_something-somebody-made-up')
        self.assertEqual(answer.status_code, 401)
        self.assertEqual(answer.json()['error']['code'], 'token_invalid')

    def test_a_revoked_token_stops_working(self):
        self.token.action_revoke()
        self.assertEqual(self.call('/ping').status_code, 401)

    def test_the_token_may_also_travel_in_its_own_header(self):
        answer = self.call('/ping', token=False, headers={'X-KMessage-Token': self.raw})
        self.assertEqual(answer.status_code, 200)

    # -- a good call ------------------------------------------------------
    def test_a_good_token_gets_an_answer(self):
        answer = self.call('/ping')
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.headers['Content-Type'], 'application/json; charset=utf-8')
        body = answer.json()
        self.assertTrue(body['ok'])
        self.assertTrue(body['data']['odoo'])
        self.assertEqual(self.token.use_count, 1)

    def test_the_path_a_capability_publishes_is_the_path_that_works(self):
        """The capability rows advertise these; a typo here is a dead endpoint."""
        lookup = self.env.ref('kmessage_connector.capability_customer_lookup')
        answer = self.call(
            lookup.endpoint[len(API):], payload={'phone': CUSTOMER_MOBILE})
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()['data']['name'], self.partner.display_name)

    def test_a_capability_switched_off_answers_with_the_reason(self):
        self.env.ref('kmessage_connector.capability_ping').active = False
        answer = self.call('/ping')
        self.assertEqual(answer.status_code, 403)
        self.assertEqual(answer.json()['error']['code'], 'capability_disabled')

    def test_answering_questions_can_be_switched_off_whole(self):
        self.account.inbound_enabled = False
        answer = self.call('/ping')
        self.assertEqual(answer.status_code, 503)
        self.assertEqual(answer.json()['error']['code'], 'inbound_disabled')

    # -- the shape of the door --------------------------------------------
    def test_a_path_that_names_no_capability_is_a_404_and_says_no_more(self):
        for path in ('/nothing_here', '/a/b/c', '/customer/lookup/and/more'):
            answer = self.call(path)
            self.assertEqual(answer.status_code, 404, path)
            self.assertEqual(answer.json()['error']['code'], 'unknown_capability', path)

    def test_a_path_cannot_climb_out_of_the_route_it_was_given(self):
        """Written with %2f, which survives the client: this is the server deciding."""
        for path in ('/ping%2f..%2ftools.json', '/..%2f..%2fweb%2fsession%2fauthenticate'):
            answer = self.call(path)
            self.assertEqual(answer.status_code, 404, path)

    def test_a_body_that_is_not_json_does_not_reach_a_traceback(self):
        answer = self.request(API + '/ping', b'}{ not json at all', {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % self.raw,
        })
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.json()['ok'])

    def test_a_body_nobody_could_have_meant_is_refused_rather_than_read(self):
        """Without a ceiling this worker would hold Odoo's default 128MB."""
        answer = self.request(API + '/ping', b'{"padding": "' + b'x' * (2 * 1024 * 1024) + b'"}', {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % self.raw,
        })
        self.assertEqual(answer.status_code, 413)

    def test_an_error_says_what_went_wrong_and_nothing_about_this_server(self):
        """A refusal is for a flow builder to branch on, not a window into Odoo."""
        answer = self.call('/ping', token='kmc_something-somebody-made-up')
        body = answer.text
        for leak in ('Traceback', 'odoo.', 'psycopg2', 'kmessage_connector/'):
            self.assertNotIn(leak, body, leak)

    # -- the rate limit ---------------------------------------------------
    def test_a_runaway_caller_is_told_to_slow_down(self):
        self.token.rate_limit = 2
        self.assertEqual(self.call('/ping').status_code, 200)
        self.assertEqual(self.call('/ping').status_code, 200)
        answer = self.call('/ping')
        self.assertEqual(answer.status_code, 429)
        self.assertEqual(answer.json()['error']['code'], 'rate_limited')

    # -- the webhook ------------------------------------------------------
    def webhook(self, envelope, secret=None, signature=None):
        raw = json.dumps(envelope).encode()
        headers = {'Content-Type': 'application/json'}
        if signature is not None:
            headers['X-Webhook-Signature'] = signature
        elif secret:
            headers['X-Webhook-Signature'] = 'sha256=%s' % compute_signature(raw, secret)
        return self.request(API + '/webhook', raw, headers)

    def test_an_unsigned_webhook_gets_nowhere(self):
        before = self.env['kmessage.event'].search_count([])
        answer = self.webhook({'event': 'message.incoming', 'data': {}})
        self.assertEqual(answer.status_code, 401)
        self.assertEqual(answer.json()['error']['code'], 'signature_invalid')
        self.assertEqual(self.env['kmessage.event'].search_count([]), before)

    def test_a_webhook_signed_with_the_wrong_secret_gets_nowhere(self):
        answer = self.webhook({'event': 'message.incoming', 'data': {}}, secret='not-the-secret')
        self.assertEqual(answer.status_code, 401)

    def test_a_correctly_signed_webhook_is_recorded_exactly_once(self):
        envelope = {
            'event': 'message.incoming',
            'timestamp': '2026-09-20T10:00:00Z',
            'data': {'contact_phone': CUSTOMER_MOBILE, 'content': {'body': 'مرحبا'}},
        }
        before = self.env['kmessage.event'].search_count([])
        answer = self.webhook(envelope, secret=self.account.sudo().webhook_secret)
        self.assertEqual(answer.status_code, 200)
        body = answer.json()
        self.assertTrue(body['ok'])
        self.assertEqual(self.env['kmessage.event'].search_count([]), before + 1)

        event = self.env['kmessage.event'].browse(body['event_id'])
        self.assertEqual(event.event, 'message.incoming')
        self.assertEqual(event.partner_id, self.partner)

    def test_a_signed_body_that_is_not_json_is_a_400(self):
        raw = b'this is not json'
        answer = self.request(API + '/webhook', raw, {
            'Content-Type': 'application/json',
            'X-Webhook-Signature': 'sha256=%s' % compute_signature(
                raw, self.account.sudo().webhook_secret),
        })
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.json()['error']['code'], 'bad_payload')

    def test_an_event_we_do_nothing_about_is_still_answered_200(self):
        """Anything else and K-Message retries an event we chose to ignore."""
        answer = self.webhook(
            {'event': 'transfer.resumed', 'data': {}},
            secret=self.account.sudo().webhook_secret)
        self.assertEqual(answer.status_code, 200)
