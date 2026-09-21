# -*- coding: utf-8 -*-
"""The client's two jobs: unwrap the envelope, and put a document on the wire.

The envelope is checked against hand-built answers, because the interesting
cases — an error carried inside a 200, an ``error_type`` the caller has to
branch on, a body that is not JSON at all — are shapes the fake has no reason
to produce. The wire is checked against the fake, over a real socket, because
"does a document go out as multipart" is not a question a stub can answer.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from odoo.tests.common import TransactionCase

from odoo.addons.kmessage_connector.tools.client import (
    ERROR_OPERATOR_ONLY, KMessageClient, KMessageError,
)

from .common import FAKE_API_KEY, FakeServerCase, free_port

PDF = b'%PDF-1.4 a small but honest document'


class Answer:
    """The two things ``_unwrap`` reads off a response, and nothing else."""

    def __init__(self, status_code, payload=None, text=''):
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self):
        if self.payload is None:
            raise ValueError('not json')
        return self.payload


class TestEnvelope(TransactionCase):

    def test_the_data_is_lifted_out_of_the_envelope(self):
        unwrapped = KMessageClient._unwrap(
            Answer(200, {'status': 'success', 'message': 'ok', 'data': {'id': 'm-1'}}))
        self.assertEqual(unwrapped, {'id': 'm-1'})

    def test_an_answer_without_a_data_key_comes_back_whole(self):
        payload = {'status': 'success', 'templates': []}
        self.assertEqual(KMessageClient._unwrap(Answer(200, payload)), payload)

    def test_an_error_inside_a_200_is_still_an_error(self):
        """The service does this, and a caller that trusted the status would not notice."""
        with self.assertRaises(KMessageError) as caught:
            KMessageClient._unwrap(Answer(200, {'status': 'error', 'message': 'Nope', 'data': None}))
        self.assertEqual(caught.exception.message, 'Nope')
        self.assertEqual(caught.exception.status, 200)

    def test_the_error_type_travels_with_the_error(self):
        with self.assertRaises(KMessageError) as caught:
            KMessageClient._unwrap(Answer(403, {
                'status': 'error',
                'message': 'This is managed by your provider.',
                'error_type': ERROR_OPERATOR_ONLY,
            }))
        error = caught.exception
        self.assertEqual(error.status, 403)
        self.assertEqual(error.error_type, ERROR_OPERATOR_ONLY)
        self.assertTrue(error.operator_only)
        self.assertIn('403', str(error))

    def test_an_ordinary_error_is_not_operator_only(self):
        with self.assertRaises(KMessageError) as caught:
            KMessageClient._unwrap(Answer(404, {'status': 'error', 'message': 'Template not found'}))
        self.assertFalse(caught.exception.operator_only)
        self.assertIsNone(caught.exception.error_type)

    def test_a_body_that_is_not_json_still_says_something_useful(self):
        with self.assertRaises(KMessageError) as caught:
            KMessageClient._unwrap(Answer(502, None, text='<html>Bad Gateway</html>'))
        self.assertIn('Bad Gateway', caught.exception.message)

    def test_an_empty_body_falls_back_to_the_status(self):
        with self.assertRaises(KMessageError) as caught:
            KMessageClient._unwrap(Answer(500, None, text=''))
        self.assertEqual(caught.exception.message, 'HTTP 500')


class TestClientOverHttp(FakeServerCase):

    def setUp(self):
        super().setUp()
        self.client = KMessageClient(self.fake.url, FAKE_API_KEY)

    def test_it_reads_who_the_key_belongs_to(self):
        self.assertEqual(self.client.me()['full_name'], 'Odoo Connector')

    def test_it_reads_the_templates_and_their_total(self):
        rows, total = self.client.templates(limit=50)
        self.assertEqual(total, len(rows))
        self.assertIn('invoice_ready', [row['name'] for row in rows])

    def test_a_send_without_a_document_goes_out_as_json(self):
        message_id = self.client.send_template(
            phone_number='966507386853',
            template_name='order_confirmed',
            language='ar',
            account_name='main number ',
            template_params={'1': 'S00021'},
            reference='sale.order:21',
        )
        self.assertTrue(message_id)

        sent = self.fake.last_sent()
        self.assertIsNone(sent['header_file'])
        fields = sent['fields']
        self.assertEqual(fields['phone_number'], '966507386853')
        self.assertEqual(fields['reference'], 'sale.order:21')
        # JSON keeps its types: the parameters arrive as an object, not a string.
        self.assertEqual(fields['template_params'], {'1': 'S00021'})
        self.assertIs(fields['bot_reply'], True)
        # The account name is sent exactly as stored, trailing space and all.
        self.assertEqual(fields['account_name'], 'main number ')

    def test_a_send_with_a_document_goes_out_as_multipart(self):
        message_id = self.client.send_template(
            phone_number='966507386853',
            template_name='invoice_ready',
            template_params={'1': 'Layla', '2': 'INV/2026/0001', '3': '100.00'},
            document=('INV_2026_0001.pdf', PDF, 'application/pdf'),
        )
        self.assertTrue(message_id)

        sent = self.fake.last_sent()
        self.assertEqual(sent['header_file']['filename'], 'INV_2026_0001.pdf')
        self.assertEqual(sent['header_file']['content_type'], 'application/pdf')
        self.assertTrue(sent['header_file']['looks_like_pdf'])
        self.assertEqual(sent['header_file']['size'], len(PDF))

        fields = sent['fields']
        self.assertEqual(fields['header_media_filename'], 'INV_2026_0001.pdf')
        # A form carries strings, so the parameters are JSON inside a field.
        self.assertEqual(json.loads(fields['template_params']), {
            '1': 'Layla', '2': 'INV/2026/0001', '3': '100.00'})
        self.assertEqual(json.loads(fields['button_params']), {})

    def test_a_template_the_platform_does_not_have_is_a_404(self):
        with self.assertRaises(KMessageError) as caught:
            self.client.send_template(phone_number='966507386853', template_name='no_such_template')
        self.assertEqual(caught.exception.status, 404)
        self.assertFalse(self.fake.sent)

    def test_a_key_the_platform_does_not_know_is_a_401(self):
        with self.assertRaises(KMessageError) as caught:
            KMessageClient(self.fake.url, 'not-the-key').me()
        self.assertEqual(caught.exception.status, 401)

    def test_a_client_with_nothing_configured_says_so(self):
        with self.assertRaises(KMessageError) as caught:
            KMessageClient('', '').me()
        self.assertIn('not configured', caught.exception.message)

    def test_a_service_that_is_not_there_is_an_error_not_a_traceback(self):
        client = KMessageClient('http://127.0.0.1:%s' % free_port(), FAKE_API_KEY)
        with self.assertRaises(KMessageError) as caught:
            client.me()
        self.assertIn('Could not reach K-Message', caught.exception.message)


class Elsewhere(BaseHTTPRequestHandler):
    """A service that answers every call with "go and ask that host instead"."""

    #: Set per test. Every request that arrives here is remembered.
    seen = []

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        Elsewhere.seen.append(dict(self.headers))
        self.send_response(302)
        self.send_header('Location', 'https://somebody-elses-host.example/api/me')
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *args):
        """Quiet: the test's own output is the interesting one."""


class TestARedirectIsNotFollowed(TransactionCase):
    """The API key travels in a header of ours, and ``requests`` only strips
    ``Authorization`` when a redirect crosses hosts. A custom header is carried
    along, so following a redirect would hand the key to whoever it names.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Elsewhere)
        cls.addClassCleanup(cls.httpd.server_close)
        cls.addClassCleanup(cls.httpd.shutdown)
        thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        thread.start()
        cls.url = 'http://127.0.0.1:%s' % cls.httpd.server_address[1]

    def setUp(self):
        super().setUp()
        Elsewhere.seen = []

    def test_a_service_that_points_somewhere_else_is_refused(self):
        with self.assertRaises(KMessageError) as caught:
            KMessageClient(self.url, FAKE_API_KEY).me()
        self.assertEqual(caught.exception.status, 302)
        self.assertIn('redirect', caught.exception.message)
        self.assertEqual(len(Elsewhere.seen), 1, 'the redirect was followed')

    def test_the_key_never_leaves_the_address_it_was_given_for(self):
        with self.assertRaises(KMessageError):
            KMessageClient(self.url, 'the-private-key').me()
        self.assertEqual(Elsewhere.seen[0].get('X-API-Key'), 'the-private-key')
        # One request means one host saw it, and that host is the configured one.
        self.assertEqual(len(Elsewhere.seen), 1)
