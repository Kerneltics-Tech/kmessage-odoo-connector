# -*- coding: utf-8 -*-
"""Is this Odoo reachable from the outside at all?

K-Message refuses to call a private, loopback or link-local address — its HTTP
client dials through a guard that rejects them outright, for webhooks and for
assistant tools alike. A connector that discovers this from a 400 halfway
through setup leaves the administrator reading a message about "callback_url"
with no idea that the real answer is "your Odoo is only reachable from your own
network". So it is checked here, before anything is sent.
"""

import ipaddress
from urllib.parse import urlparse

#: Host names that are a loopback by name rather than by number.
LOCAL_NAMES = {'localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback'}


def split_url(url):
    parsed = urlparse(url or '')
    return parsed.scheme, (parsed.hostname or '')


def is_public_url(url):
    """True when a service on the internet could plausibly reach ``url``.

    Judged from the address alone — no DNS, no connection. A name that is not
    an IP literal is taken at face value, because resolving it here would say
    nothing about what K-Message's own resolver will see.
    """
    scheme, host = split_url(url)
    if scheme not in ('http', 'https') or not host:
        return False
    if host.lower() in LOCAL_NAMES or host.lower().endswith('.local'):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # a real host name
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def why_not_public(url):
    """A sentence explaining the refusal, or an empty string when it is fine."""
    scheme, host = split_url(url)
    if not host:
        return "no address is configured"
    if scheme not in ('http', 'https'):
        return "the address does not start with http:// or https://"
    if not is_public_url(url):
        return "%s is only reachable from inside your own network" % host
    return ''
