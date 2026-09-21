# The inbound API

What K-Message may ask this Odoo, and exactly how to ask it.

Everything lives under `/kmessage/api/v1/`. It is plain HTTP with real status
codes rather than Odoo's JSON-RPC, because the callers are a flow tree that
branches on `http:2xx` and an assistant that reads ordinary REST — both of
which would read a JSON-RPC `200 OK` with the error inside the body as success.

A capability is one endpoint with its own switch. Switching one off refuses it
for every caller at once, with a reason; it never answers an empty list
instead.

---

## Authentication

Every request carries a token issued in Odoo (*K-Message → Configuration →
Tokens*, or the connect wizard):

```
Authorization: Bearer kmc_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`X-KMessage-Token: kmc_…` is accepted as well, for callers that cannot set an
`Authorization` header. The token is stored as a fingerprint, so it is shown
once when issued and cannot be read back afterwards; *Regenerate* mints a new
value and shows that once.

A token carries a company, a list of capabilities it may use, an optional
expiry date and a per-minute rate limit. Anything not ticked is refused.

Two optional headers:

| Header | Effect |
| --- | --- |
| `X-KMessage-Lang` | The language the human-readable parts of the answer are written in (`ar`, `en_US`, …). Defaults to `en_US`. |
| `Content-Type` | `application/json` for the bodies below. A form-encoded body is accepted too, which is what makes these endpoints usable from a flow builder that only sends forms. |

## The shape of an answer

Success — always an envelope, always `200`:

```json
{"ok": true, "data": { … }}
```

Refusal — the same envelope inverted, with the HTTP status matching the
reason:

```json
{"ok": false, "error": {"code": "customer_not_found", "message": "No customer here has that number."}}
```

A capability that returns a **document** answers with the file itself: the
bytes, `Content-Type` of the document, and a `Content-Disposition` naming it.
There is no envelope around a file — K-Message sniffs the first bytes (a PDF
must really start with `%PDF-`) and hands the file to WhatsApp without ever
showing it to a model.

A capability that returns **rows** puts them under `rows`, so the full path to
them is `data.rows`. That is what an assistant tool's `items_path` points at,
and what a flow's `response_mapping` walks. Beside it is `data.count`, added
centrally rather than by each handler, so a caller that wants to say "you have
three invoices" can do so without every bridge having remembered the same
key.

## Two rules that apply everywhere

**Identity is never a parameter.** Every personal capability takes a phone
number — the one from the live conversation — and answers only for the customer
that number belongs to. There is no capability that takes a customer id, and
none that lists other people's records. A number found on two partners answers
only when both roll up to the same company, which is how a person's invoices
are found under the company they belong to; a number sitting on two unrelated
partners is no match at all. Answering with somebody's invoices because a
number was reused would be worse than answering with nothing.

**Rows are capped.** `limit` may be sent, but the capability's own ceiling
wins. A conversation cannot read a long list, and a long list is how an
accident becomes an export.

---

## Capabilities that core provides

These three need no other Odoo app.

### `GET /kmessage/api/v1/ping`

Proof of life, and an honest list of what is switched on. Carries no customer
data, so it is the safe thing to call from a monitor.

```bash
curl -s https://odoo.example.com/kmessage/api/v1/ping \
     -H 'Authorization: Bearer kmc_…'
```

```json
{
  "ok": true,
  "data": {
    "odoo": true,
    "company": "Example Trading Co.",
    "connector_version": "1.0.0",
    "outbound_enabled": true,
    "inbound_enabled": true,
    "capabilities": [
      {"code": "ping", "name": "Health check", "enabled": true},
      {"code": "customer_lookup", "name": "Recognise a customer", "enabled": true}
    ]
  }
}
```

### `GET /kmessage/api/v1/tools.json`

A description of this Odoo's API for whoever is wiring K-Message up: the
endpoints that are switched on right now, their full URLs, whether each one
needs a phone number, and how many rows it will return. Written as plain JSON
rather than a spec format, because what a person pasting into a flow builder
needs is the path, what to send and what comes back.

```bash
curl -s https://odoo.example.com/kmessage/api/v1/tools.json \
     -H 'Authorization: Bearer kmc_…'
```

```json
{
  "ok": true,
  "data": {
    "service": "Odoo — Example Trading Co.",
    "auth": {"type": "bearer", "header": "Authorization", "format": "Bearer <token>"},
    "note": "Every personal request must carry the customer's phone number, and answers only ever concern that customer.",
    "capabilities": [
      {
        "code": "customer_lookup",
        "name": "Recognise a customer",
        "description": "Given the number in the conversation, says whether that person is a customer here…",
        "method": "POST",
        "url": "https://odoo.example.com/kmessage/api/v1/customer/lookup",
        "needs_phone": true,
        "max_rows": 1
      }
    ]
  }
}
```

The URLs come from Odoo's `web.base.url`. If they come back as
`http://localhost:8069/…`, that parameter is wrong, and K-Message will refuse
the address — see [SECURITY.md](SECURITY.md) and the README's limits.

### `POST /kmessage/api/v1/customer/lookup`

Is this number a customer here, and what should an agent know first.

| Field | Required | Meaning |
| --- | --- | --- |
| `phone` | yes | The customer's number. `phone_number` is accepted as a synonym, because that is the name K-Message substitutes. Any format: `+966 50 738 6853`, `0507386853` and `966507386853` all find the same partner. |

```bash
curl -s https://odoo.example.com/kmessage/api/v1/customer/lookup \
     -H 'Authorization: Bearer kmc_…' \
     -H 'Content-Type: application/json' \
     -d '{"phone": "966507386853"}'
```

```json
{
  "ok": true,
  "data": {
    "found": true,
    "name": "Ahmed Al-Qahtani",
    "language": "ar",
    "city": "Riyadh",
    "since": "2024-03-11",
    "is_company": false,
    "opted_out": false,
    "balance_due": {"amount": 1725.0, "currency": "SAR"},
    "oldest_unpaid": "2026-08-02",
    "open_orders": 1
  }
}
```

An unknown number is `404 customer_not_found`, not an empty answer: the caller
should be able to tell "we do not know you" from "we know you and there is
nothing".

The last three keys come from bridge modules — a bridge adds facts to this
answer rather than adding an endpoint of its own. `balance_due` and
`oldest_unpaid` arrive with the invoicing bridge, `open_orders` with the sales
one, and an Odoo without those apps simply does not have those keys.

---

## Capabilities that come with a bridge module

A bridge installs itself when its Odoo app is present, and contributes both
automations and capabilities. Until then its capability rows are *unavailable*:
a call gets `503 capability_unavailable` naming the missing app, never a
plausible-looking empty list.

| Endpoint | Code | Needs | Personal | Answers |
| --- | --- | --- | --- | --- |
| `POST /kmessage/api/v1/invoices` | `invoices` | `account` | yes | That customer's posted invoices, newest first, with what is left to pay |
| `POST /kmessage/api/v1/invoice/pdf` | `invoice_pdf` | `account` | yes | One of that customer's own invoices, as the PDF itself |
| `POST /kmessage/api/v1/orders` | `orders` | `sale` | yes | That customer's quotations and orders, newest first |
| `POST /kmessage/api/v1/order/status` | `order_status` | `sale` | yes | Where one named order of theirs stands, and what is on it |
| `POST /kmessage/api/v1/stock/check` | `stock_check` | `stock` | no | Whether a product is available, branch by branch |
| `POST /kmessage/api/v1/delivery/status` | `delivery_status` | `stock` | yes | Where that customer's recent deliveries have got to |

A capability's code is its path with the leading `/kmessage/api/v1/` removed
and slashes and dashes turned into underscores; `.json` is dropped. That is how
the controller finds the handler, so the two can never drift apart.

Every personal endpoint takes `phone` exactly as `customer/lookup` does, plus
its own fields, and every row-returning endpoint takes an optional `limit`
capped by the capability's *Max rows*.

### Invoices

`phone`, plus optional `unpaid_only` (anything that reads as yes: `true`,
`"yes"`, `1`) and `limit`.

```bash
curl -s https://odoo.example.com/kmessage/api/v1/invoices \
     -H 'Authorization: Bearer kmc_…' \
     -H 'Content-Type: application/json' \
     -d '{"phone": "966507386853", "unpaid_only": true, "limit": 3}'
```

```json
{
  "ok": true,
  "data": {
    "rows": [
      {
        "number": "INV/2026/00184",
        "date": "2026-09-02",
        "total": 1725.0,
        "due": 1725.0,
        "currency": "SAR",
        "status": "unpaid",
        "has_pdf": true
      }
    ],
    "count": 1
  }
}
```

`status` is read from what is left to pay rather than from Odoo's own
`payment_state`: `paid`, `partly_paid` or `unpaid`. `has_pdf` says whether a
printed copy is already stored on the invoice — useful to a caller deciding
whether to ask for one.

### One invoice as a PDF

`phone`, plus `invoice` (the number as the list gave it; `number` is accepted
as a synonym). The answer is the file: `application/pdf`, with a
`Content-Disposition` naming it. There is no JSON envelope around it.

```bash
curl -s https://odoo.example.com/kmessage/api/v1/invoice/pdf \
     -H 'Authorization: Bearer kmc_…' \
     -H 'Content-Type: application/json' \
     -d '{"phone": "966507386853", "invoice": "INV/2026/00184"}' \
     -o invoice.pdf
```

An invoice that belongs to somebody else is answered exactly as one that does
not exist — `404 not_found` — so that trying numbers teaches nobody which are
real.

### Orders and one order's status

`orders` takes `phone`, optional `limit`, and optional `state`, which is one of
`open`, `quotation`, `confirmed`, `done`, `cancelled`; anything else is
`400 bad_state`. `order_status` takes `phone` and `order` (or `number`).

```json
{
  "ok": true,
  "data": {
    "rows": [
      {
        "number": "S00412",
        "date": "2026-09-14",
        "total": 940.0,
        "currency": "SAR",
        "state": "confirmed",
        "delivery": "packed and ready"
      }
    ],
    "count": 1
  }
}
```

`state` and `delivery` are words a customer would use rather than Odoo keys.
`order_status` returns the same row with the order's lines added.

### Stock

The one capability here that is **not** personal: availability belongs to the
shelf, not to a person, so no phone number is asked for and none would change
the answer. It takes `product` (a code or a name), optional `branch` and
optional `limit`.

```bash
curl -s https://odoo.example.com/kmessage/api/v1/stock/check \
     -H 'Authorization: Bearer kmc_…' \
     -H 'Content-Type: application/json' \
     -d '{"product": "FILTER-OIL-12"}'
```

```json
{
  "ok": true,
  "data": {
    "product": "[FILTER-OIL-12] Oil filter 12mm",
    "rows": [
      {"location": "Riyadh", "level": "in stock"},
      {"location": "Qassim", "level": "only a few left"}
    ],
    "count": 2
  }
}
```

`level` is a band, not a number, and that is the default on purpose: the exact
figure tells a competitor what you buy and how fast you sell it, and it is
stale the moment somebody at the counter picks the item up. Switch *Say the
exact quantity* on the capability to add an `available` number to each row.

### Deliveries

`phone`, plus optional `reference` — which matches either our delivery number
or the order it came from, because customers quote whichever they were given —
and optional `limit`.

```json
{
  "ok": true,
  "data": {
    "rows": [
      {
        "reference": "WH/OUT/00231",
        "date": "2026-09-16",
        "state": "on its way",
        "carrier": "Aramex",
        "tracking": "41234567890"
      }
    ],
    "count": 1
  }
}
```

`carrier` and `tracking` appear only where the delivery app that provides them
is installed.

Columns can grow. Anything reading them — an assistant tool's field allowlist,
a flow's `response_mapping` — is worth checking against one real call rather
than against this page: `tools.json` says which endpoints exist on a given
Odoo, and one `curl` shows what a row looks like on it.

---

## Every refusal

| Code | Status | When |
| --- | --- | --- |
| `token_missing` | 401 | No `Authorization: Bearer …` and no `X-KMessage-Token`. |
| `token_invalid` | 401 | Unknown token, or one that is revoked or past its expiry date. |
| `rate_limited` | 429 | This token used up its minute. The limit is per token and per Odoo worker; the default is 120 requests a minute, and 0 means no limit. |
| `inbound_disabled` | 503 | *Answer questions* is switched off on the connection. Nothing inbound works while it is. |
| `unknown_capability` | 404 | No capability has that code — usually a path typo. |
| `capability_disabled` | 403 | The capability exists but is switched off in Odoo. |
| `capability_unavailable` | 503 | Its bridge module's Odoo app is not installed here. |
| `not_permitted` | 403 | The capability is on, but this token is not ticked for it. |
| `not_implemented` | 501 | A capability row exists with no handler behind it. Only reachable through a hand-written data row. |
| `phone_required` | 400 | A personal capability was called without a phone number. |
| `customer_not_found` | 404 | No partner in this company matches that number — or two do, which is treated the same way on purpose. |
| `internal_error` | 500 | Something broke in Odoo. The caller gets this sentence; the traceback goes to the server log and nowhere else. |
| `signature_invalid` | 401 | Webhook only: missing or wrong signature. |
| `bad_payload` | 400 | Webhook only: the body was not JSON. |

The bridges add their own, in the same shape:

| Code | Status | When |
| --- | --- | --- |
| `invoice_required` | 400 | `invoice/pdf` without an invoice number. |
| `not_found` | 404 | `invoice/pdf`: no invoice with that number **on this customer's account**. |
| `document_unavailable` | 503 | The invoice could not be printed. A broken report is our problem, so it is a 503 and not a 400. |
| `order_required` | 400 | `order/status` without an order number. |
| `order_not_found` | 404 | No order of theirs with that number. |
| `bad_state` | 400 | `orders` was given a `state` that is not open, quotation, confirmed, done or cancelled. |
| `product_required` | 400 | `stock/check` without a product to look for. |
| `product_not_found` | 404 | Nothing here is called that. |
| `product_not_stocked` | 404 | It exists, but Odoo keeps no quantity for it — a service, say. Answering "out of stock" would be a confident wrong answer. |
| `branch_not_found` | 404 | No warehouse or internal location matches the branch that was asked for. |

Branch on the HTTP status and on `ok` rather than on the list of codes: a
bridge may add a code, and the statuses are the stable part.

---

## The webhook

`POST /kmessage/api/v1/webhook` is the other direction: K-Message telling Odoo
what happened on WhatsApp. It is authenticated by signature rather than by
token, because the platform signs rather than bearing.

```
X-Webhook-Signature: sha256=<hex HMAC-SHA256 of the raw request body>
```

The secret is the webhook secret on the connection (readable by a K-Message
Administrator in Odoo). The signature covers the exact bytes sent, so a proxy
that re-serialises JSON will break it.

```json
{"ok": true, "event_id": 4821, "outcome": "matched to Ahmed Al-Qahtani"}
```

Once the signature is good the answer is always `200`, even for an event the
connector does not act on. Everything is written to the event log first and
routed second, so "did it reach us?" has an answer in the database even when
the routing then failed.
