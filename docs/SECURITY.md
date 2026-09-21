# Security

Short, because there is not much to it: two credentials, one signature, one
rule about identity, and a switch that turns any of it off.

## What is stored, and where

| Secret | Where it lives | Who can read it |
| --- | --- | --- |
| The token K-Message presents to Odoo | `kmessage.token` — a SHA-256 fingerprint and the first few characters, never the value | nobody. It is shown once when issued and cannot be recovered; *Regenerate* mints a new one and shows that once |
| The K-Message API key Odoo presents to the platform | `kmessage.account.api_key` | the *K-Message / Administrator* group only, enforced by the field's `groups=` |
| The webhook secret | `kmessage.account.webhook_secret` | the same group, the same way |

Everything else — messages, events, templates — is ordinary business data under
ordinary record rules, scoped so one Odoo company never sees another's.

An assistant tool is the exception worth stating plainly: **its URL and its
`Authorization` header live on the K-Message side**, because that is where the
call is made from. K-Message encrypts those header values at rest and shows
them masked in its screens, but they are outside your Odoo. That is why
publishing a tool issues a fresh, narrow token for it rather than reusing one,
and why revoking that token is a one-step operation — see below.

## Why a phone number is not optional

Every capability that answers about a person takes a phone number and answers
only for the customer that number belongs to. There is no endpoint that takes a
customer id, none that lists other people's records, and no parameter anywhere
that names a subject.

The number comes from the live conversation — K-Message substitutes it as
`{{phone_number}}`, and the names `phone`, `phone_number` and `args` are
refused as tool parameters precisely so a model cannot supply one instead. Two
partners sharing a number is treated as no match at all: answering with
somebody's invoices because a number was reused would be worse than answering
with nothing.

The single exception proves the rule. `stock/check` asks for no phone number,
because what is on the shelf is not a fact about a person and no number would
change the answer. Everything that *is* about a person — invoices, orders,
deliveries, the PDF — is personal, and a record belonging to somebody else is
refused in exactly the same words as one that does not exist, so that trying
numbers teaches nobody which are real.

## The rest of the perimeter

* **Tokens are scoped.** A token carries a company, the exact list of
  capabilities it may use, an optional expiry and a rate limit. Anything not
  ticked is `403 not_permitted`.
* **Requests are rate-limited.** 120 a minute per token by default, counted per
  Odoo worker; over it is `429 rate_limited`. The limit exists to stop a
  runaway loop, not to meter usage.
* **The webhook is signed.** `X-Webhook-Signature: sha256=<hex>` is an
  HMAC-SHA256 over the exact bytes received, compared in constant time.
  Anything unsigned or wrongly signed is refused, and the refusal says nothing
  more than that.
* **Our failures are not explained to callers.** A traceback goes to the server
  log; the caller gets `internal_error` and one sentence.
* **A customer can be left alone.** *No WhatsApp* on a partner makes the outbox
  refuse them, whatever any automation says.

## Revoking access in one step

Pick the level you mean:

| To stop… | Do this | Effect |
| --- | --- | --- |
| one caller | *K-Message → Configuration → Tokens* → **Revoke** | every request with that token is refused from the next one onwards |
| one question | *Capabilities* → switch the row off | refused for every token at once, with a reason |
| all inbound | the connection → **Answer questions** off | `503` to everything, including the health check |
| all outbound | the connection → **Send messages** off | nothing is sent, whatever the automations say |

Revoking is preferred to deleting: a token that has been used cannot be
deleted, so its history stays readable. If a tool's token leaked, revoke it and
press *Publish to K-Message* on the tool again — publishing issues a fresh
secret and sends it, and the old one is dead the moment you revoked it.
