# -*- coding: utf-8 -*-
"""Turning the many ways a phone number is typed into one comparable form.

WhatsApp identifies a person by bare international digits (``966512345678``).
Odoo stores whatever the salesperson typed: ``+966 51 234 5678``,
``0512345678``, ``00966-51-234-5678``. Matching one against the other is the
single most common reason an integration answers "we could not find an account
under this number" for a customer who plainly has one.

Two functions, and the distinction between them matters:

``normalize``  best effort at full international digits, using the company's
               country code to complete a local number. What we store and send.
``match_key``  the last :data:`MATCH_DIGITS` digits, which is what we compare
               on. A number that lost its country code, kept a trunk ``0`` or
               was saved with an old prefix still matches here.
"""

import re

#: How many trailing digits decide a match. Nine is the length of a subscriber
#: number in most countries that use a trunk prefix, so it survives the two
#: mistakes that actually happen — a missing country code and a stray leading
#: zero — without being so short that two different people collide.
MATCH_DIGITS = 9

_NON_DIGITS = re.compile(r'\D+')


def digits_only(number):
    """Every digit in ``number``, in order, and nothing else."""
    if not number:
        return ''
    return _NON_DIGITS.sub('', str(number))


def normalize(number, country_code=None):
    """International digits for ``number``, with no ``+`` and no separators.

    ``country_code`` is the company's calling code (``'966'``), used only when
    the number does not carry one of its own. An empty or unusable number
    normalises to ``''`` rather than raising — bad data in a partner record
    must never break a send.
    """
    digits = digits_only(number)
    if not digits:
        return ''

    # 00 is the international prefix in most of the world; some regions dial 011.
    # Having dialled one, the caller has already said which country they meant,
    # so the company's code must not be put in front of it as well.
    dialled_abroad = False
    if digits.startswith('00'):
        digits, dialled_abroad = digits[2:], True
    elif digits.startswith('011') and len(digits) > 12:
        digits, dialled_abroad = digits[3:], True

    code = digits_only(country_code)
    if code and not dialled_abroad:
        if digits.startswith(code) and len(digits) > len(code) + 5:
            return digits
        # A national number: drop the trunk prefix before adding the code.
        local = digits[1:] if digits.startswith('0') else digits
        if len(local) <= 11:
            return code + local
    return digits


def match_key(number):
    """The comparable tail of ``number``: its last :data:`MATCH_DIGITS` digits.

    Returns ``''`` for anything too short to identify a person, which callers
    must treat as "no key" rather than as a wildcard — an empty key matching
    every partner is exactly the bug this exists to prevent.
    """
    digits = digits_only(number)
    if len(digits) < MATCH_DIGITS:
        return ''
    return digits[-MATCH_DIGITS:]


def same_number(left, right):
    """True when both numbers plausibly belong to the same person."""
    left_key, right_key = match_key(left), match_key(right)
    return bool(left_key) and left_key == right_key
