# Flows: the answer without an assistant

A flow is a tree: K-Message sends a message, waits for a reply, calls an HTTP
endpoint, branches on what came back. No model, no AI feature on the plan, no
chatbot-settings permission — which makes it the shortest path from "we
installed the connector" to "a customer asked about their invoice and got a
real answer".

Two ready-made ones ship with the addon:

| File | What it does |
| --- | --- |
| [`kmessage_connector/data/flows/invoice_status.json`](../kmessage_connector/data/flows/invoice_status.json) | "Where is my invoice" — asks Odoo for the invoices of the number in the conversation and reads them back. |
| [`kmessage_connector/data/flows/stock_check.json`](../kmessage_connector/data/flows/stock_check.json) | "Is it in stock" — asks which product, then answers from Odoo's on-hand quantities. |

**Connect imports both of them for you**, with this Odoo's address and a real
token already in them — creating a flow is not one of the operator-only writes,
so a tenant key is enough. They arrive switched off, every time: what a
company's WhatsApp answers with is theirs to turn on, not this addon's to
decide. *Import the ready-made flows* on the connect screen is the switch, and
importing again never replaces a flow of the same name, so one you have edited
is safe.

The rest of this page is the manual route — for a graph you want to change
before it lands, or a platform that refused the import.

---

## Importing one by hand

A flow is created by posting it. The file is already the whole request body —
name, description, trigger keywords, and the graph the platform stores:

```bash
curl -s https://api.k-message.kerneltics.com/api/chatbot/flows \
     -H 'X-API-Key: whm_…' \
     -H 'Content-Type: application/json' \
     --data-binary @kmessage_connector/data/flows/invoice_status.json
```

The answer carries the new flow's id. Open it in *Chatbot → Flows* to see the
same graph drawn, and to change anything by hand afterwards.

The token needs permission to write chatbot flows. If it answers `403`, the
file is still exactly what your provider needs: send it to them and ask for it
to be imported.

## What to edit before enabling

Three things. The first two live in the `api_call` node; the third is at the
top of the file:

1. **The URL.** `https://odoo.example.com/kmessage/api/v1/…` is a placeholder.
   Put your own Odoo's public address there. It must be a public one:
   K-Message dials through a guard that refuses private, loopback and
   link-local addresses, and re-checks after resolving the name.
2. **The token.** `Bearer kmc_REPLACE_ME` is a placeholder too. Issue a token
   in Odoo (*K-Message → Configuration → Tokens*) that is ticked for exactly
   the capability this flow calls, and paste its value. It is shown once.
3. **The trigger keywords.** The shipped lists are a starting point in English
   and Arabic. What your customers actually type is a question your inbox can
   answer better than we can. Keep them specific: a keyword matches when it
   appears *anywhere* in the message, the first flow that matches wins, and a
   short word therefore catches conversations that were not about it at all.

Then read the message nodes and make the wording yours. The copy here is
deliberately plain; it is not meant to survive contact with a brand.

## The fields the messages print

An `api_call` node lifts values out of the JSON answer into session variables
through `response_mapping` — a variable name on the left, a path into the
response on the right:

```json
"response_mapping": {"stock": "data.rows", "product_name": "data.product"}
```

Paths are dotted, and `rows[0].number` indexes an array. Every capability here
puts its rows under `data.rows` and how many there are under `data.count`, so
that half never changes. **The column names
inside a row do.** The invoice flow prints `{{invoice.number}}`,
`{{invoice.date}}`, `{{invoice.due}}` and `{{invoice.currency}}`; the stock
flow prints `{{item.location}}` and `{{item.level}}` — a band such as "in
stock" or "only a few left", because the exact figure is left in Odoo unless
*Say the exact quantity* is switched on for that capability, which adds
`{{item.available}}`. Those are the columns as the bridge modules ship them,
and a bridge may grow another. Make one real call with `curl` (see
[API.md](API.md)) and edit the loop to match what you actually get back — a
variable that does not resolve renders as nothing at all, which is how a flow
ends up sending a line of bullet points and no numbers.

The message engine understands three things, which is enough:

```
{{invoice.number}}                      a value, or nothing when it is missing
{{if invoices}}…{{else}}…{{endif}}      an empty list is false
{{for invoice in invoices}}…{{endfor}}  up to 50 rows
```

`{{phone_number}}` is always available: K-Message seeds it from the
conversation before the first node runs. That is what the invoice flow's
`api_call` body sends, and it is the reason neither flow asks who you are. The
stock flow sends no number at all — availability is a fact about the shelf, and
that capability is the one here that is not about a person.

## How the two graphs are wired

Nodes are of exactly these types: `start`, `message`, `buttons`, `prompt`,
`api_call`, `condition`, `timing`, `set_variable`, `ai_response`, `transfer`,
`webhook`, `goto_flow`, `whatsapp_flow`, `end`. An edge carries a condition,
and the ones these flows use are `default`, `http:2xx`, `http:non2xx` and
`max_retries`; the rest of the vocabulary is `button:<id>`, `no_match`,
`true`/`false`, `in_hours` and `out_of_hours`.

**Where is my invoice**

```
start → looking ─ default → ask_odoo ─ http:2xx ────→ answer → end
                                     └ http:non2xx ─→ sorry  → end
```

There is no question anywhere in it, and that is the point: the customer's
number already identifies them, and asking "which account are you?" is exactly
the invitation this connector refuses to extend. (It is also why `looking` is a
`message` rather than a `prompt`: a prompt node sends its question and then
waits for a reply, and there is no reply to wait for here.)

The `http:non2xx` branch is not only "the server is down". A number Odoo does
not recognise is a `404`, and it arrives on the same edge — which is why the
message on it says both things without guessing between them.

**Is it in stock**

```
start → ask_product ─ default ────→ ask_odoo ─ http:2xx ────→ answer → end
                    └ max_retries → give_up → end           └ http:non2xx → sorry → end
```

Here the `prompt` earns its place: it asks which product, stores the answer as
`product_query`, and the `api_call` sends that as the search term. Three
unusable replies take the `max_retries` edge and the flow lets go, rather than
asking a fourth time. A product nobody here sells is a `404` as well, so it
arrives on `http:non2xx` beside a genuine outage — which is why that message
names both possibilities and then says the useful thing: send the code from the
box.

## When it does not work

* **Nothing happens at all.** The flow is still `"enabled": false`, or nothing
  in what the customer typed contains one of its trigger keywords, or another
  enabled flow matched first and is holding the conversation.
* **The `http:non2xx` branch every time.** Call the same URL with the same
  token from a terminal. The error envelope names the reason —
  `token_invalid`, `not_permitted`, `capability_unavailable` — and
  [API.md](API.md) lists what each one means. If `curl` works from your laptop
  but the flow does not, the address is probably one K-Message refuses.
* **Bullet points with no values.** The column names in the loop do not match
  the ones the endpoint returns. One `curl` settles it.
* **It answered, but for nobody.** Check *K-Message → Tokens* in Odoo: the
  token's *Last used* tells you whether the call ever arrived.
