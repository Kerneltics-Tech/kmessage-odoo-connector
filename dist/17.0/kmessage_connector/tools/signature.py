# -*- coding: utf-8 -*-
"""HMAC verification for inbound K-Message webhooks."""

import hashlib
import hmac
import re

SIGNATURE_HEADER = 'X-Webhook-Signature'
_SIGNATURE_RE = re.compile(r'^sha256=([0-9a-f]{64})$', re.IGNORECASE)


def compute_signature(raw_body, secret):
    """Hex HMAC-SHA256 of the exact request bytes, keyed with the webhook secret."""
    if isinstance(secret, str):
        secret = secret.encode()
    if isinstance(raw_body, str):
        raw_body = raw_body.encode()
    return hmac.new(secret, raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body, header_value, secret):
    """True when ``header_value`` is a valid ``sha256=<hex>`` signature of ``raw_body``.

    The signature covers the raw bytes, so callers must hand in the untouched
    body: re-serialising parsed JSON would break on key order or whitespace.
    Every failure mode — no body, no secret, malformed header, wrong length —
    is a plain ``False``, never an exception, and the comparison is constant
    time.
    """
    if not raw_body or not secret or not header_value:
        return False
    match = _SIGNATURE_RE.match(header_value.strip())
    if not match:
        return False
    expected = compute_signature(raw_body, secret)
    return hmac.compare_digest(expected, match.group(1).lower())
