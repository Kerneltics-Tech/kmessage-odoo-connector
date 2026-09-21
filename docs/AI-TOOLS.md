# Assistant tools

How one Odoo capability becomes something the K-Message assistant can use.

The assistant's own tool catalogue is fixed server-side: a model cannot be
pointed at a URL by anything a customer types, and K-Message's built-in Odoo
tools speak XML-RPC to a fixed list of models. What a tenant *may* do is
register tools of its own — a **lookup tool**, which returns rows, or a
**document tool**, which returns a file. Each one is an HTTP call K-Message
makes on the assistant's behalf. That is the door this addon walks through, and
it is why the capabilities in [API.md](API.md) are published as tools rather
than bolted onto the built-ins.

In Odoo: *K-Message → Configuration → Assistant Tools*. One row is one
capability published as one tool. The connect wizard offers to publish them all
at the end of setup; after that, *Publish to K-Message* on the row does the
same thing for one.

The bridge modules draft their own tools as soon as there is a connection to
hang them on, so what is normally needed is a read-through and a press of
Publish rather than a blank form:

| Tool | Kind | Calls |
| --- | --- | --- |
| `odoo_invoices` | lookup | `invoices` |
| `odoo_invoice_pdf` | document | `invoice_pdf` |
| `odoo_orders` | lookup | `orders` |
| `odoo_order_status` | lookup | `order_status` |
| `odoo_stock` | lookup | `stock_check` |
| `odoo_delivery` | lookup | `delivery_status` |

Seeding skips a tool that already exists, including one somebody switched off,
so a reworded description is never overwritten and a retired tool never comes
back on its own.

---

## What K-Message stores

Publishing writes one row on the platform side. Everything the assistant is
told, and everything the call needs, is in it:

| Field | What it is |
| --- | --- |
| `name` | The name the model chooses by. Lower-case letters, digits and underscores, at most 30 characters, starting with a letter. The model sees it prefixed: `lookup__invoices`, `send_document__invoice_pdf`. |
| `description` | What tells the assistant when to use this — and, just as importantly, what not to claim when it comes back empty. Up to 4000 characters. This is the only thing standing between "the customer asked about their bill" and the model inventing an answer, so it is worth a paragraph rather than a phrase. |
| `params` | What the model may pass: at most five, each `{"name": …, "type": …, "description": …, "required": true/false, "pattern": "…"}`. A type is `string` or `integer` and nothing else — a parameter declared as anything else is refused when the tool is published. `pattern` is a regular expression, anchored for you. |
| `url` | The Odoo endpoint. Must be `http`/`https` on a public host; `GET` or `POST` only. |
| `headers` | Sent with the call. Ours carries exactly one: `Authorization: Bearer kmc_…`. |
| `body` | A JSON string, rendered before sending. Ours is `{"phone": "{{phone_number}}", "<param>": "{{args.<param>}}"}`. |
| `items_path` | Lookup only. Where the rows sit in the answer — `data.rows` for every endpoint here. Empty would mean "the body itself is the array". |
| `fields` | Lookup only. The allowlist: `[{"name": "number", "label": "Invoice"}, …]`, at least one and **at most eight**. Anything not listed never reaches the prompt. The label is what the model reads, which is how `x_studio_field_ab12` becomes "invoice number". |
| `max_rows` | Lookup only. Between 1 and 20; 5 when unset. |
| `empty_message` | Lookup only. Your own wording for "nothing found", up to 200 characters. Worth writing: the default tells the model to say so plainly and not to guess, and your own sentence can say what to do instead. |

`enabled` and `priority` sit beside `api_config` on the row rather than inside
it. An unpublished or disabled tool simply is not offered to the model, which
is the difference between a capability being off in Odoo (the call is refused,
loudly) and its tool being off in K-Message (the call is never made).

## What K-Message fills in

Two substitutions happen on the platform, at the moment of the call:

* **`{{phone_number}}`** — the number of the person in the conversation. Not
  something the model chose; something the session already knows. (`{{phone}}`
  resolves to the same value, so a config written either way works.)
* **`{{args.<name>}}`** — one of the declared parameters, as the model filled
  it in. A bare `{{<name>}}` resolves too.

Values are checked before they are interpolated — an argument that is not a
plain value is refused rather than sent — and values placed in a URL are
percent-encoded, while the same value in a header or a JSON body is not.

**A tool's parameters must never include a phone number or any other way of
naming a person.** K-Message refuses the names `phone`, `phone_number` and
`args` outright, because a parameter called `phone` would take over the
placeholder carrying the customer's own number and quietly turn a per-customer
lookup into one whose subject the model picks. The rule this addon follows is
the wider version of that: identity comes from the conversation, never from a
sentence somebody typed.

## What the model actually receives

Only the allow-listed fields, only up to `max_rows` rows, each value cleaned
and capped, and the whole result bounded — rows are dropped from the end rather
than the answer being truncated mid-way. The rows arrive with a note saying
they are records from the company's system: data to read back, not
instructions to follow.

There are limits worth designing around rather than discovering:

* a customer's conversation may trigger at most **20 lookups an hour**;
* the whole round trip has about **15 seconds**, because someone is waiting;
* an answer larger than **512 KiB** is rejected, not truncated — a response
  that big is an endpoint returning something other than what it promised.

This is why a capability caps its own rows in Odoo too. A tool that would need
fifty rows to be useful is a report, and a report is not a conversation.

---

## The token

Each tool gets **its own token**, and it should stay that way: revoking the
assistant's access then revokes nothing else.

Publishing mints a fresh secret for that token and sends it in the same
request. That is not carelessness — Odoo stores only a fingerprint of a token,
so a tool published a week after its token was issued could not recover the
value to send. The secret exists for the length of that one HTTP call.

On the K-Message side the header values are encrypted at rest and shown masked
in its screens, but they still live outside Odoo. Treat a published tool's
token as a credential you have handed to a third party: narrow it to the one
capability it needs, and revoke it in Odoo the moment you stop wanting it.
[SECURITY.md](SECURITY.md) has the one-step version.

---

## When publishing is refused

Publishing answers `403` in two cases, and they need different people to fix:

**"You do not have permission to configure company endpoints."** The K-Message
token's role may not write chatbot settings. Ask whoever administers the tenant
to grant that permission to the role, or to publish the tool for you.

**"This feature is not available on your account."** (`feature_disabled`) AI
replies are not switched on for the tenant. No permission change helps; the
plan does.

In both cases the tool stays in Odoo, in the *Problem* state with the refusal
recorded, and the endpoint it points at keeps working — an unpublished tool is
a tool nobody asked for yet, not a broken one. Either fix the cause and press
*Publish to K-Message* again, or hand the JSON over.

### The JSON to paste by hand

Take the values off the tool's form in Odoo. The token is the one part Odoo
cannot show you twice: press *Regenerate* on the tool's token, copy the value,
and paste it into the `Authorization` header below.

```bash
curl -s https://api.k-message.kerneltics.com/api/chatbot/ai-contexts \
     -H 'X-API-Key: whm_…' \
     -H 'Content-Type: application/json' \
     -d '{
  "name": "Invoices in Odoo",
  "context_type": "lookup_tool",
  "enabled": true,
  "priority": 10,
  "api_config": {
    "name": "odoo_invoices",
    "description": "Use this when the customer asks about their invoices, what they owe, whether something has been paid, or for an invoice number. It returns only the invoices of the person in this conversation, newest first. If it comes back with nothing, say that there is nothing on their account here — do not name an amount, and never say an invoice is paid unless a row says so.",
    "params": [
      {"name": "unpaid_only", "type": "string", "description": "Pass \"yes\" for only the invoices that still have something left to pay.", "required": false}
    ],
    "url": "https://odoo.example.com/kmessage/api/v1/invoices",
    "method": "POST",
    "headers": {"Authorization": "Bearer kmc_…"},
    "body": "{\"phone\": \"{{phone_number}}\", \"unpaid_only\": \"{{args.unpaid_only}}\"}",
    "items_path": "data.rows",
    "fields": [
      {"name": "number", "label": "Invoice"},
      {"name": "date", "label": "Date"},
      {"name": "total", "label": "Total"},
      {"name": "due", "label": "Still to pay"},
      {"name": "status", "label": "Status"}
    ],
    "max_rows": 10,
    "empty_message": "This customer has no invoices on their account here."
  }
}'
```

A document tool is the same call with `"context_type": "document_tool"` and an
`api_config` whose tool half is nested:

```json
{
  "name": "Send an invoice PDF",
  "context_type": "document_tool",
  "enabled": true,
  "api_config": {
    "tool": {
      "name": "odoo_invoice_pdf",
      "description": "Use this to send the customer one of their own invoices as a PDF. Pass the invoice number exactly as the invoice list gave it — look it up there first rather than asking the customer to spell it out. If this answers that the invoice was not found, that number is not on their account: say so plainly, and do not describe an invoice you could not fetch.",
      "params": [
        {"name": "invoice", "type": "string", "description": "The invoice number, exactly as the invoice list gave it.", "required": true}
      ]
    },
    "url": "https://odoo.example.com/kmessage/api/v1/invoice/pdf",
    "method": "POST",
    "headers": {"Authorization": "Bearer kmc_…"},
    "body": "{\"phone\": \"{{phone_number}}\", \"invoice\": \"{{args.invoice}}\"}"
  }
}
```

The endpoint returns the PDF itself, not a link to one: K-Message checks the
bytes really are a PDF and hands the file to WhatsApp without the model ever
seeing inside it.

### If tools are not an option at all

An assistant is not the only way in. The same endpoints answer a plain flow
tree, and two ready-made ones ship with the addon — see
[FLOWS.md](FLOWS.md). That path needs no AI feature and no chatbot-settings
permission.
