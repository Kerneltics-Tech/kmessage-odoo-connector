# K-Message Connector for Odoo — design

Status: draft 1 · 2026-09-20 · Odoo 17.0 and 18.0 from one source tree

## What it is for

A K-Message customer runs Odoo. Today, wiring the two together means one of
their developers reading two APIs. This addon is what they install instead:
paste a K-Message URL and a private token, press Connect, and

* their invoices, quotations and delivery notes go out on WhatsApp as approved
  templates with the PDF attached,
* K-Message can ask Odoo, on a customer's behalf, "what do I owe", "where is my
  order", "is this in stock" — for the assistant or for a plain flow tree, with
  no AI required,
* every one of those behaviours is a switch, and every switch is off until
  somebody turns it on.

## The two directions

```
                    ┌──────────────────────── Odoo ────────────────────────┐
  invoice posted ──►│ automation ──► outbox ──► cron ──► K-Message client  │──► POST /api/messages/template
                    │                                                      │      (PDF as the template's
                    │                                                      │       document header)
                    │                                                      │
  K-Message  ──────►│ /kmessage/api/v1/<capability>   (token + capability) │──► rows the assistant reads
  assistant or      │ /kmessage/api/v1/webhook        (HMAC over raw body) │
  flow api_call     └──────────────────────────────────────────────────────┘
```

Outbound is Odoo deciding to say something. Inbound is K-Message asking. They
share a connection record and nothing else, so either can be switched off whole.

## What the platform actually allows (verified against the live API)

These are not assumptions; each was checked against
`https://api.k-message.kerneltics.com` with a tenant key on 2026-09-20.

| Call | With an API key | Consequence for the addon |
| --- | --- | --- |
| `GET /api/me` | ✅ returns the user, role and permissions | the connect wizard names who the token is |
| `GET /api/accounts` | ✅ | the sending number is chosen from a list, not typed |
| `GET /api/templates` | ✅ 76 rows, with `body_content`, `header_type`, `buttons`, `sample_values` | templates are synced, and `{{1}}`-style params are read off the body |
| `GET /api/webhooks` | ✅ including the event catalogue | the addon shows what it could subscribe to |
| `POST /api/webhooks` | ❌ `403 operator_only` — "This is managed by your provider." | **auto-subscribing cannot be assumed**; see below |
| `POST /api/custom-actions` | ❌ `403 operator_only` | same |
| `POST /api/messages/template` | ✅ accepts JSON *and* multipart; `404 Template not found` when the name is wrong | sending is the one write that always works |
| `GET /api/settings/odoo` | ❌ `403 Permission denied` | the addon cannot push its own credentials into K-Message |

Two consequences shape the whole design.

**1. The addon must probe, not presume.** On Connect it tries each thing once
and records what the token is allowed to do. What it can do, it does. What it
cannot, it writes out as an exact instruction — URL, event list, secret — for
whoever administers the tenant. A managed customer is the normal case, not an
error state.

**2. Identity is never a parameter.** K-Message's own Odoo tools resolve the
customer server-side from the conversation's phone number, on purpose: no
sentence a customer types can fetch somebody else's invoices. The inbound API
here keeps that property. Every personal capability takes a phone number and
answers only for the partner that number belongs to; there is no "list all
invoices" endpoint, and an unmatched number returns nothing rather than
guessing.

## Events on offer (live catalogue)

`message.incoming` · `message.sent` · `contact.created` · `transfer.created` ·
`transfer.assigned` · `transfer.resumed` · `button.reply`

The inbound webhook handler accepts all seven, verifies
`X-Webhook-Signature: sha256=<hex HMAC of the raw body>`, stores the event, and
routes it. Anything it does not act on is recorded and answered `200` — a
webhook that retries because we returned 500 on an event we simply ignore is a
self-inflicted outage.

## Modules

| Module | Installs when | Adds |
| --- | --- | --- |
| `kmessage_connector` | chosen | connection, tokens, templates, outbox, automations, capabilities, webhook, event log |
| `kmessage_connector_account` | `account` is present | invoice/payment automations, invoice + balance capabilities |
| `kmessage_connector_sale` | `sale` is present | quotation and order automations, order-status capability |
| `kmessage_connector_stock` | `stock` is present | delivery-note automation, stock-availability capability |

Bridges are `auto_install`, so a customer installs one thing and gets exactly
the parts that match their Odoo. Core depends only on `base`, `mail` and
`phone_validation`, so it installs on an Odoo with no accounting at all.

## Models

* **`kmessage.account`** — one connection per company: base URL, API key,
  sending account name (never stripped: real account names can end in a space),
  webhook secret, state, last check, and the probed capability flags.
* **`kmessage.token`** — the private tokens K-Message presents inbound. Stored
  hashed, shown once, each with scopes, an optional expiry, last-used stamp and
  an active flag.
* **`kmessage.template`** — synced from the platform, with the parameter list
  parsed from `body_content` so mapping is a form, not a guess.
* **`kmessage.automation`** — "when *this* happens in Odoo, send *that*
  template": model, trigger, filter domain, template, parameter mapping,
  language, whether to attach the PDF, active.
* **`kmessage.message`** — the outbox. One row per intended message with its
  state machine, attempt count, next attempt, provider message id, delivery
  status and the record it came from.
* **`kmessage.capability`** — one row per inbound endpoint, each with an active
  flag and a scope. Disabled means `403` with a stable code, never a silent
  empty answer.
* **`kmessage.event`** — every inbound webhook, with its signature verdict,
  and what was done about it.

## The inbound API

`/kmessage/api/v1/…`, `type='http'`, `auth='public'`, CSRF off, JSON in and out,
bearer token in `Authorization`. Written as plain HTTP rather than Odoo's JSON-RPC
so that status codes are real status codes — a flow tree branching on
`http:2xx` needs that, and so does anything else that speaks ordinary REST.

| Capability | Endpoint | Answers |
| --- | --- | --- |
| `ping` | `GET /ping` | version, company, which capabilities are on |
| `customer` | `POST /customer/lookup` | does this number belong to a customer, and what is their balance |
| `invoices` | `POST /invoices` | that customer's invoices, newest first, paid state included |
| `invoice_pdf` | `POST /invoice/pdf` | one invoice as base64, or a signed short-lived link |
| `orders` | `POST /orders` | that customer's recent orders and their state |
| `delivery` | `POST /delivery/status` | where an order's delivery has got to |
| `stock` | `POST /stock/check` | on-hand by location for a product search |
| `send` | `POST /send/document` | "send this customer their invoice" — Odoo does the rendering and the sending |
| `tools` | `GET /tools.json` | a machine-readable description of exactly the above, for pasting into K-Message |

Every request is rate-limited per token, logged, and refused with a stable error
code (`token_invalid`, `capability_disabled`, `not_found`, `rate_limited`) plus a
human sentence in the caller's language.

## Non-AI path

The flow builder's `api_call` node is a first-class consumer: the same
endpoints, called with a bearer token, branching on `http:2xx` / `http:non2xx`.
The addon ships importable flow JSON for the two flows customers ask for first —
"where is my invoice" and "is it in stock" — so the non-AI route is a file to
import, not a diagram to rebuild.

## Safety rails

* Nothing sends until a template is chosen and the automation is switched on.
* A partner can be opted out; the outbox refuses them.
* Dry-run mode logs what would have been sent without sending it.
* The outbox retries with backoff and stops permanently on a `4xx` that means
  "this will never work" (unknown template, bad number).
* Tokens are hashed at rest; the API key is stored in a field only the
  connector's own group may read.
* The webhook refuses an unsigned or wrongly signed body, always.
