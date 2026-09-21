# -*- coding: utf-8 -*-
"""What every test here needs: a K-Message that is not the real one.

There were two ways to keep these tests off the network. Monkeypatching the
client's ``requests`` session is the cheaper one, and it is also the one that
would have let through exactly the mistakes this suite exists to catch:
whether a document leaves as multipart, and whether an error envelope becomes a
:class:`KMessageError`, are questions about bytes on a socket, and a stubbed
session answers them by assumption. So ``dev/fake_kmessage.py`` is started for
real, on a port the operating system picks, and the connector talks to it over
HTTP exactly as it would to the platform.

The fake runs inside the test process, so what it was sent is read from its
``STATE`` directly rather than through the ``/_sent`` hook — the same data, one
socket fewer, and no chance of reading a page a keep-alive connection has not
flushed yet.

``dev/`` is not part of a packaged addon. When the fake is not on disk the
tests that need it skip with a reason instead of failing: an addon installed
from a zip is not a broken checkout.
"""

import importlib.util
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from odoo.tests.common import TransactionCase

from odoo.addons.kmessage_connector.tools.phone import match_key

#: The key the fake accepts. Matches its own default, so a developer running
#: ``python3 dev/fake_kmessage.py`` by hand can reuse these fixtures.
FAKE_API_KEY = 'test-key'

#: A number with a Saudi shape, saved the way a salesperson types it.
CUSTOMER_MOBILE = '0512345678'

_LOADED = {}


def fake_source():
    """Where ``dev/fake_kmessage.py`` is, or None.

    Searched upwards rather than at a fixed depth: the Odoo 17 copy of this
    addon sits two directories deeper than the source one.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'dev' / 'fake_kmessage.py'
        if candidate.is_file():
            return candidate
    return None


def _fake_module():
    """The fake, imported once per process from outside the addon path."""
    if 'module' not in _LOADED:
        source = fake_source()
        if source is None:
            _LOADED['module'] = None
        else:
            spec = importlib.util.spec_from_file_location('kmessage_fake', source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _LOADED['module'] = module
    return _LOADED['module']


def free_port():
    """A port nothing is listening on, for the one test that needs a refusal."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class FakeServer:
    """The fake K-Message, on a port of its own, for the life of one test class."""

    def __init__(self, module):
        self.module = module
        module.Handler.api_key = FAKE_API_KEY
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
        self.url = 'http://127.0.0.1:%s' % self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)

    # -- what it remembers ------------------------------------------------
    @property
    def sent(self):
        with self.module.STATE.lock:
            return list(self.module.STATE.sent)

    def last_sent(self):
        sent = self.sent
        return sent[-1] if sent else None

    @property
    def contexts(self):
        """The assistant tools registered on the fake, newest last."""
        with self.module.STATE.lock:
            return list(self.module.STATE.contexts.values())

    def reset(self):
        self.module.STATE.reset()

    def fail_next(self, count=1, status=503):
        """Make the next ``count`` sends answer ``status`` instead of succeeding."""
        with self.module.STATE.lock:
            self.module.STATE.fail_next = count
            self.module.STATE.fail_status = status


def start_fake(cls):
    """Start the fake for ``cls``, or skip the whole class with a reason."""
    module = _fake_module()
    if module is None:
        raise unittest.SkipTest(
            "dev/fake_kmessage.py is not in this checkout, so there is nothing to talk to")
    server = FakeServer(module)
    cls.addClassCleanup(server.stop)
    return server


def own_the_number(env, *numbers):
    """Make sure nobody else in this database answers to these numbers.

    A test database is reused, and a contact left behind on the same number
    would make ``_kmessage_find_by_phone`` refuse to answer — which is the
    right behaviour and the wrong fixture. Rolled back with the rest of the
    test, like everything else here.
    """
    keys = [key for key in (match_key(number) for number in numbers) if key]
    if keys:
        env['res.partner'].with_context(active_test=False).search(
            [('kmessage_phone_key', 'in', keys)]).write({'mobile': False, 'phone': False})


def connection_for(env, company, values):
    """The company's one connection, pointed at ``values``.

    A company may hold only a single connection and a reused database may
    already have one, so this takes the existing row over rather than adding a
    second and falling over the constraint.
    """
    account = env['kmessage.account'].with_context(active_test=False).search(
        [('company_id', '=', company.id)], limit=1)
    if account:
        account.write(dict(values, active=True))
        return account
    return env['kmessage.account'].create(dict(values, company_id=company.id))


def mirrored_template(account, values):
    """One template mirrored onto ``account``, replacing any of the same name."""
    values = dict(values, account_id=account.id)
    existing = account.env['kmessage.template'].search([
        ('account_id', '=', account.id),
        ('name', '=', values['name']),
        ('language', '=', values['language']),
    ], limit=1)
    if existing:
        existing.write(values)
        return existing
    return account.env['kmessage.template'].create(values)


def connect(cls, base_url):
    """Give ``cls`` a connected company, a customer and two templates.

    The templates are the two the fake will accept: one plain, one whose header
    is a document. Tests that must not render a PDF use the plain one.
    """
    cls.company = cls.env.company
    cls.company.country_id = cls.env.ref('base.sa')
    cls.account = connection_for(cls.env, cls.company, {
        'name': 'K-Message (test)',
        'base_url': base_url,
        'api_key': FAKE_API_KEY,
        'account_name': 'main number ',
        'state': 'connected',
        'outbound_enabled': True,
        'inbound_enabled': True,
        'dry_run': False,
        'webhook_secret': 'the-secret-only-the-two-of-us-know',
    })
    cls.template = mirrored_template(cls.account, {
        'name': 'order_confirmed',
        'language': 'ar',
        'status': 'APPROVED',
        'header_type': 'none',
        'body_content': 'طلبك {{1}} تم تأكيده.',
    })
    cls.doc_template = mirrored_template(cls.account, {
        'name': 'invoice_ready',
        'language': 'ar',
        'status': 'APPROVED',
        'header_type': 'DOCUMENT',
        'body_content': 'مرحباً {{1}}، فاتورتك رقم {{2}} بمبلغ {{3}} جاهزة.',
    })
    own_the_number(cls.env, CUSTOMER_MOBILE)
    cls.partner = cls.env['res.partner'].create({
        'name': 'Layla',
        'mobile': CUSTOMER_MOBILE,
        'lang': 'en_US',
    })


def issue_token(env, capabilities=None, **values):
    """A token that may use ``capabilities`` — every one of them by default."""
    if capabilities is None:
        capabilities = env['kmessage.capability'].with_context(active_test=False).search([])
    token, raw = env['kmessage.token'].issue('Test token', capabilities=capabilities)
    if values:
        token.write(values)
    return token, raw


class FakeServerCase(TransactionCase):
    """A test that needs something at the other end of the client."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fake = start_fake(cls)

    def setUp(self):
        super().setUp()
        self.fake.reset()


class KMessageCase(FakeServerCase):
    """…and a company connected to it, with a customer and templates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        connect(cls, cls.fake.url)
