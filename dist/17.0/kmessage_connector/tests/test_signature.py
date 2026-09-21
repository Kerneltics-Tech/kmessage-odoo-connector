# -*- coding: utf-8 -*-
"""The one thing standing between a public URL and anybody's chatter.

Every case that is not a genuine signature has to come back ``False``. Not an
exception: the webhook route calls this before it knows anything about the
caller, and a traceback there would turn a malformed request into a 500 that
K-Message would then retry.
"""

from odoo.tests.common import TransactionCase

from odoo.addons.kmessage_connector.tools.signature import (
    SIGNATURE_HEADER, compute_signature, verify_signature,
)

BODY = b'{"event":"button.reply","data":{"in_reply_to":{"reference":"x"}}}'
SECRET = 'the-secret-only-the-two-of-us-know'


def header_for(body=BODY, secret=SECRET):
    return 'sha256=%s' % compute_signature(body, secret)


class TestSignature(TransactionCase):

    def test_the_header_is_the_one_k_message_sends(self):
        self.assertEqual(SIGNATURE_HEADER, 'X-Webhook-Signature')

    def test_a_good_signature_verifies(self):
        self.assertTrue(verify_signature(BODY, header_for(), SECRET))

    def test_a_string_body_signs_the_same_as_its_bytes(self):
        self.assertEqual(compute_signature('hello', SECRET), compute_signature(b'hello', SECRET))
        self.assertTrue(verify_signature('hello', header_for(b'hello'), SECRET))

    def test_case_and_surrounding_space_in_the_header_are_forgiven(self):
        self.assertTrue(verify_signature(BODY, '  %s  ' % header_for().upper(), SECRET))

    def test_a_tampered_body_does_not_verify(self):
        signature = header_for()
        self.assertFalse(verify_signature(BODY.replace(b'button', b'BUTTON'), signature, SECRET))
        # One byte more at the end is enough.
        self.assertFalse(verify_signature(BODY + b' ', signature, SECRET))
        # And re-serialising parsed JSON would be, too.
        self.assertFalse(verify_signature(b'{"event": "button.reply"}', signature, SECRET))

    def test_another_secret_does_not_verify(self):
        self.assertFalse(verify_signature(BODY, header_for(secret='nearly-right'), SECRET))
        self.assertFalse(verify_signature(BODY, header_for(), SECRET + '!'))

    def test_a_malformed_header_is_false_rather_than_an_exception(self):
        digest = compute_signature(BODY, SECRET)
        for bad in (
            None,
            '',
            '   ',
            digest,                       # the hex without the algorithm
            'sha256=',
            'sha1=%s' % digest,           # the wrong algorithm
            'sha256=%s' % digest[:-1],    # one character short
            'sha256=%sff' % digest,       # one byte long
            'sha256=%szz' % digest[:-2],  # not hexadecimal
            'sha256=%s, sha256=%s' % (digest, digest),
        ):
            self.assertFalse(verify_signature(BODY, bad, SECRET), repr(bad))

    def test_a_missing_body_or_secret_is_false(self):
        signature = header_for()
        self.assertFalse(verify_signature(b'', signature, SECRET))
        self.assertFalse(verify_signature(None, signature, SECRET))
        self.assertFalse(verify_signature(BODY, signature, ''))
        self.assertFalse(verify_signature(BODY, signature, None))
