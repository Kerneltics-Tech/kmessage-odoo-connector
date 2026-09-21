# K-Message Connector for Odoo

Odoo and K-Message, joined in both directions.

**Odoo sends.** An invoice is posted, a quotation confirmed, a delivery
validated — and the customer receives the approved WhatsApp template with the
printed document attached, sent through the company's own K-Message tenant.

**K-Message asks.** A customer writes on WhatsApp, and K-Message can ask this
Odoo about them: what they owe, where their order is, whether the thing they
want is in stock. The question always carries the number of the person in the
conversation, and the answer only ever concerns that person.

Both directions are switches. Installing the addon changes what a company
*can* do; it changes nothing about what their Odoo starts doing on its own.

---

## What you need

* **Odoo 17.0 or 18.0**, Community or Enterprise. One source tree serves both —
  see [Installing on Odoo 17](#installing-on-odoo-17).
* **A K-Message tenant and a private token.** In K-Message: *Settings → API
  Keys*. The value looks like `whm_…` and is shown once.
* **For the inbound half, an Odoo reachable from the internet.** K-Message dials
  through a guard that refuses private, loopback and link-local addresses, so
  `localhost`, `127.0.0.1`, `10.x`, `192.168.x`, and host names ending in
  `.local` or `.internal` are turned away before a request is made. A tunnel
  with a public name works; an address only your office can resolve does not.
* Nothing else. The addon uses `requests`, which Odoo already depends on.

The outbound half needs none of that: Odoo calls K-Message, not the reverse, so
sending works from an Odoo nobody outside can reach.

---

## Installing on Odoo 18

Put the repository on the addons path and install the module:

```bash
git clone <this repository> /opt/odoo/kmessage-odoo-connector

cd /path/to/odoo-18
venv/bin/python odoo-bin -d <database> -i kmessage_connector --stop-after-init \
    --addons-path=addons,/opt/odoo/kmessage-odoo-connector
```

Or, in the interface: *Apps → Update Apps List*, then search for
**K-Message Connector** and press Activate.

## Installing on Odoo 17

Odoo 18 renamed the list view — the XML tag is `<list>` where 17 has `<tree>`,
and a view type is `list` where 17 says `tree`. A manifest cannot ask which
server it is running on (Odoo reads it with `ast.literal_eval`), so one XML file
cannot satisfy both. The source here is written for 18, and the 17 flavour is
produced from it:

```bash
cd /opt/odoo/kmessage-odoo-connector
python3 dev/build.py
```

That writes `dist/17.0/`, one converted copy per module. **`dist/17.0` is a
build product, not source, so a clone may not have one** — run the script once
after cloning, and again after pulling changes. It needs nothing but Python 3;
Odoo does not have to be installed to run it.

Then point Odoo 17 at that directory instead of at the repository root:

```bash
cd /path/to/odoo-17
venv/bin/python odoo-bin -d <database> -i kmessage_connector --stop-after-init \
    --addons-path=addons,/opt/odoo/kmessage-odoo-connector/dist/17.0
```

Two more details keep the one source tree honest, and they are worth knowing
before editing anything:

* **The manifest version carries no series prefix.** It is `1.0.0`, not
  `18.0.1.0.0`. Odoo stamps the running series onto it — the installed module
  reads `17.0.1.0.0` on a 17 and `18.0.1.0.0` on an 18 — and a version that
  already names a series raises on the other one. `python3 dev/build.py --check`
  reports what the conversion would change and fails on a manifest that has
  grown a prefix, which makes it a reasonable thing to run in CI.
* **Python never hardcodes a view type.** Anything that builds a `view_mode` at
  runtime imports it from `kmessage_connector/tools/compat.py`, which asks the
  running server which name it uses. The build script therefore copies Python
  untouched and rewrites only XML.

---

## Connecting

*K-Message → Configuration → Connect to K-Message*. The wizard asks for two
things: the K-Message URL (`https://api.k-message.kerneltics.com` unless the
tenant is hosted elsewhere) and the private token.

**Check** looks before leaping. It asks the platform who the token is, which
WhatsApp numbers the tenant has, how many approved templates there are, and
whether this token may manage webhooks — and then tells you, before anything is
written.

**Apply** does what the token turned out to be allowed to do:

* saves the connection and syncs the approved templates, so choosing a template
  later is a dropdown rather than a name typed from memory;
* subscribes this Odoo to WhatsApp events, when the token may do that;
* publishes the assistant tools, when the token may do that;
* issues the token K-Message will present when it asks Odoo a question.

Each of those is attempted once and reported honestly. A step the token is not
allowed to take is written down as an instruction for whoever administers the
tenant, not as a failure — see below.

That issued token is shown **once**. Odoo stores a fingerprint of it and nothing
else, so it cannot be read back later — copy it into K-Message now, or press
*Regenerate* on the token afterwards and use the new value.

Nothing sends yet. Sending starts when somebody creates an automation, picks a
template and switches it on.

## When the plan is managed by the provider

Most tenants are on a managed plan, where creating webhooks and custom actions
is reserved for whoever runs the platform. The API answers those two calls with
`403 operator_only` — "This is managed by your provider." That is a fact about
the account, not a failure of the connector: the wizard records it, sets the
webhook state to *Waiting for your provider*, and shows the exact values to
hand over.

Send your provider exactly this:

```
Address:   https://<your-odoo>/kmessage/api/v1/webhook
Method:    POST
Events:    message.incoming, message.sent, button.reply, contact.created
Secret:    (the webhook secret on the connection form, visible to a
            K-Message Administrator in Odoo)
Signature: X-Webhook-Signature: sha256=<hex HMAC-SHA256 of the raw body>
```

Odoo verifies that signature on every inbound call and refuses anything
unsigned or wrongly signed. Those four events are the ones it acts on; the
three the platform also offers — `transfer.created`, `transfer.assigned`,
`transfer.resumed` — are recorded and answered `200` without being acted on,
because a webhook that retries after we returned an error on an event we chose
to ignore is a self-inflicted outage.

Assistant tools are refused the same way when the token's role may not change
chatbot settings. [docs/AI-TOOLS.md](docs/AI-TOOLS.md) has the exact JSON to
paste by hand in that case. The flow path needs no provider at all:
[docs/FLOWS.md](docs/FLOWS.md) ships two ready graphs to import.

---

## What is in the box

| Module | Installs when | Adds |
| --- | --- | --- |
| `kmessage_connector` | you choose it | the connection, tokens, template sync, the outbox, automations, the inbound API, the webhook and the event log |
| `kmessage_connector_account` | `account` is installed | invoice and payment automations; the invoice list, the invoice PDF, and what a customer owes |
| `kmessage_connector_sale` | `sale` is installed | quotation and order automations; a customer's orders and the status of one of them |
| `kmessage_connector_stock` | `stock` is installed | delivery automations; "is it in stock", branch by branch, and "where is my delivery" |

The bridges install themselves beside the connector, so a company installs one
thing and gets exactly the parts that match their Odoo. A capability whose app
is not installed stays unavailable and says so, rather than answering with an
empty list.

The addon ships two ready-made K-Message flow graphs in
`kmessage_connector/data/flows/` — "where is my invoice" and "is it in stock" —
for tenants who want the inbound half without an assistant.

**The templates write themselves.** Nothing can be sent until an approved
WhatsApp template exists, and writing one means learning Meta's rules about
categories, sample values and media headers, then waiting for a review — a
day's work standing between installing this and a customer receiving anything.
So the connect wizard does it: it writes the templates the bridges need, in
Arabic, under names of its own, renders a real invoice as the sample document
Meta insists on for a document header, and submits them. Approval usually
arrives within minutes. Templates that already exist are left alone, and the
wording can be changed in K-Message afterwards — it is a starting point, not a
decision taken for you. Switch it off in the wizard if you would rather write
your own.

Two smaller things that are easy to miss:

**Sending one document by hand.** Any invoice, order or delivery has *Send on
WhatsApp* under its Action menu. It opens with the customer, their number and
the document already filled in, shows what they will read, and queues it — the
same path an automation uses, so a message sent by hand is recorded in the same
place and reports the same way.

**Making a tap do something** (*K-Message → Configuration → Reply rules*). When
a customer taps a button on a message Odoo sent, a rule can run an Odoo server
action on the record that message was about, and answer them back with a
template. The record is not guessed: it comes from the reference Odoo attached
when it sent the message, so a reply can only ever act on the thing it was a
reply to. Rules start switched off, and "only the first tap counts" is on by
default, because people tap twice.

---

## Limits, stated plainly

**A send is accepted, not delivered.** `POST /api/messages/template` answers
`pending`: K-Message has taken the message, not handed it to the customer.
Odoo's *Sent* therefore means *accepted by K-Message*. A failure that happens
afterwards never comes back on that call.

**There is no delivered or read event today.** The platform's event catalogue
offers `message.incoming`, `message.sent`, `button.reply`, `contact.created`
and the three `transfer.*` events — nothing for delivered or read. The delivery
column in the outbox therefore stops at *Sent*, and it would be dishonest to
show more.

**K-Message's own Odoo tools cannot call this addon.** They talk XML-RPC and
read a fixed list of models — `res.partner`, `account.move`, `ir.attachment`,
`product.product`, `stock.quant`, `stock.warehouse`, `pos.config`,
`res.company`. `sale.order` and `stock.picking` are not among them, and no
REST endpoint of ours is reachable that way. That is exactly why the
capabilities here are published as *lookup* and *document* tools, which are
plain HTTP calls the tenant registers, rather than as extensions of the
built-in tools. See [docs/AI-TOOLS.md](docs/AI-TOOLS.md).

**The built-in "send the invoice" tool renders nothing.** It attaches a PDF only
when an `ir.attachment` with mimetype `application/pdf` already exists on the
`account.move`. Odoo prints on demand, so a document capability here does the
rendering itself instead of hoping one is lying around.

**The inbound half needs a public address.** K-Message refuses private,
loopback and link-local hosts, and re-checks after resolving the name, so a
public name pointing at `192.168.1.10` is refused too. Odoo checks the same
thing before publishing a tool or a webhook, so the refusal arrives as a
sentence about your address rather than as a `400` from the platform.

**Rows are capped on both sides.** A capability has a row ceiling in Odoo, and
K-Message caps a lookup tool at 20 rows of at most 8 fields. A conversation
cannot read a long list, and a long list is how an accident becomes an export.

---

## Where things are written down

* [docs/DESIGN.md](docs/DESIGN.md) — why the addon is shaped like this, and what
  the platform was verified to allow.
* [docs/API.md](docs/API.md) — every inbound endpoint, with curl and every error
  code.
* [docs/AI-TOOLS.md](docs/AI-TOOLS.md) — how a capability becomes an assistant
  tool, and what to do when publishing is refused.
* [docs/FLOWS.md](docs/FLOWS.md) — the no-AI path: two importable flows and what
  to edit in them.
* [docs/SECURITY.md](docs/SECURITY.md) — what is stored where, and how to revoke
  access in one step.

## Licence

LGPL-3, the same as Odoo's own community addons. See [LICENSE](LICENSE).
