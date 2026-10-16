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

* **The manifest version names its series.** The Apps store wants the series
  in front (`18.0.1.2.0`), and Odoo refuses a version that names the other
  one: `18.0.…` on a 17 raises before the module is even read. So the source
  carries `18.0.`, and the build rewrites it to `17.0.` in the 17 copy.
  `python3 dev/build.py --check` reports what the conversion would change and
  fails on a source manifest without the `18.0.` prefix, which makes it a
  reasonable thing to run in CI.
* **Python never hardcodes a view type.** Anything that builds a `view_mode` at
  runtime imports it from `kmessage_connector/tools/compat.py`, which asks the
  running server which name it uses. The build script therefore copies Python
  untouched and rewrites only XML.
* **On 17 a cron has to say that it recurs.** Odoo 17 runs a cron `numbercall`
  times and then switches it off, and that number defaults to 1; Odoo 18
  dropped the field and refuses it. The build writes `numbercall` -1 on every
  cron. Builds up to 1.1.1 did not, so on Odoo 17 their crons — the outbox
  among them — stopped after their first run; upgrading the modules switches
  them back on. So that the outbox does not then send weeks-old messages by
  itself, the upgrade first holds back every message that had waited in the
  stopped outbox for more than a day: it is marked *Failed*, with the reason
  *Held back when the connector was upgraded…*, and *Try again* sends it if it
  should still go out. Messages from the last day go out as usual. The
  *invoices that have fallen due* cron comes back too, so a rule on that
  trigger starts reminding about every invoice still overdue, within the
  rule's own cooldown.

## Upgrading

Put the new code where the old was, then upgrade the module: restart Odoo
and press *Upgrade* on **K-Message Connector** in *Apps*, or

```bash
venv/bin/python odoo-bin -d <database> -u kmessage_connector --stop-after-init \
    --addons-path=<the same as above>
```

Upgrading the connector upgrades the bridges with it. How to get the new code
depends on one question: **has anybody changed the connector's code?**

* **No**: take the release as it is. `git pull` on `main` or `18.0`; on
  `17.0`, which is rebuilt for every release, `git fetch` and
  `git reset --hard origin/17.0`; or download the branch and replace the four
  folders.
* **Yes**: do not replace the folders, which would throw those changes away
  without a word. A release that needs it ships a patch per Odoo series and a
  guide in `docs/upgrade/<version>/`: for 1.2.0,
  [docs/upgrade/1.2.0](docs/upgrade/1.2.0/README.md)
  ([بالعربي](docs/upgrade/1.2.0/README.ar.md)). The patch applies to 1.1.1
  (and to 1.1.0 after a one-line change) and keeps the changes; where they
  meet the release's, git marks the place and you choose.

After upgrading to 1.2.0, open *K-Message → Configuration → Connection* and
press *Switch on delivery news*. For the maintainers: once a release is
committed, with `dist/17.0` rebuilt, `sh dev/make_upgrade_patch.sh <previous
release commit>` writes its patches into `docs/upgrade/<version>/` and checks
that each turns an untouched previous release into this one, byte for byte.

---

## Connecting

*K-Message → Configuration → Connect to K-Message*. The wizard asks for one
thing: the private token. There is one K-Message, so its address is never a
question — `kmessage.account._service_url()` answers it, reading the system
parameter `kmessage.base_url` if a developer has set one and otherwise the
address built into the addon.

**Connect** is the only button. It looks first — who the token is, which
WhatsApp numbers the tenant has, how many approved templates there are, whether
this token may manage webhooks — and then does everything it turned out to be
allowed to do. *Choose what it sets up*, beside it, stops at that review with a
switch per part, for anyone who would rather decide than be finished.

What it does, and reports back in a few lines:

* saves the connection and syncs the approved templates, so choosing a template
  later is a dropdown rather than a name typed from memory;
* writes the WhatsApp templates this addon sends and submits them to Meta;
* sets up the rules that use them, filled in — trigger, template, and a field
  for every `{{n}}`. **Sending the invoice arrives switched on**; the rest
  arrive off, because "we also messaged everyone about their quotations" is
  not a surprise anybody wants. None of them can be lost: *Restore the default
  rules* on the connection puts back whatever was deleted and touches nothing
  else — not a default you edited, and never a rule you wrote yourself;
* issues the token K-Message will present when it asks Odoo a question;
* imports the two ready-made flow trees with that token and this Odoo's
  address already in them, switched off — the answer for a tenant whose plan
  has no assistant at all;
* posts that token, the webhook address and the assistant tools to
  `POST /api/integrations/odoo/connect`, which registers all of it in one call.

That last call is the reason there is nothing to hand anybody. It exists
precisely because webhooks and custom actions are otherwise operator-only, and
it is narrow on purpose: it reads the tenant from the API key, it only ever
creates or updates rows it marked as its own, and a webhook or tool somebody
set up by hand is left exactly as it was. On a K-Message that does not have it
yet, the wizard falls back to the older route — see below. The webhook that
route creates carries the same marker, so Connect can bring it up to date
later.

That issued token is shown **once**. Odoo stores a fingerprint of it and nothing
else, so it cannot be read back later — copy it into K-Message now, or press
*Regenerate* on the token afterwards and use the new value.

Nothing sends yet. Sending starts when somebody creates an automation, picks a
template and switches it on.

## When it has to be handed over

The one-call route above removes this for anyone on a current K-Message. Two
cases still land here: a platform old enough not to have that endpoint, and a
tenant whose operator has disabled it. Then the older truth applies — creating
webhooks and custom actions answers `403 operator_only`, "This is managed by
your provider" — and the wizard says so, sets the webhook state to *Waiting for
your provider*, and shows the exact values to hand over.

Only then, send your provider exactly this:

```
Address:   https://<your-odoo>/kmessage/api/v1/webhook
Method:    POST
Events:    message.incoming, message.sent, button.reply, contact.created,
           message.delivered, message.read, message.failed
Secret:    (the webhook secret on the connection form, visible to a
            K-Message Administrator in Odoo)
Signature: X-Webhook-Signature: sha256=<hex HMAC-SHA256 of the raw body>
```

Odoo verifies that signature on every inbound call and refuses anything
unsigned or wrongly signed. Those seven events are the ones it acts on; the
others the platform offers — the `transfer.*` events among them — are recorded
and answered `200` without being acted on, because a webhook that retries after
we returned an error on an event we chose to ignore is a self-inflicted outage.

The last three are delivery news, and a webhook made by hand hears it about
every message the company sends, campaigns and bot replies included. The
connector copes — news about a message this Odoo never sent is answered and not
stored — but it is traffic for nothing. Connect, where the platform allows it,
asks for them limited to this Odoo's own messages instead.

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
afterwards never comes back on that call — it comes back as delivery news.

**Delivery news needs a K-Message that limits it to this Odoo.** On the
platform, `message.delivered`, `message.read` and `message.failed` fire for
every message a company sends. Connect sends `status_scope: referenced` —
only messages that carry a reference, which every message from this Odoo does —
and asks for the three only once the platform has echoed that scope back. A
K-Message that has never echoed it is never asked for them: the connection
keeps the four events above, says that delivery news needs a newer K-Message,
and the *Delivery* column stops at *Sent*. A connection made
before delivery news existed shows *Delivery news is off* on its form, with a
*Switch on delivery news* button that changes the subscription and nothing
else; connecting again does the same.

**What delivery news does.** The outbox shows how far each message got —
*Delivered*, *Read*, or *Not delivered* with WhatsApp's own reason ("add a
payment method in Meta Business Suite", "outside the 24-hour window") — and
the invoice, or whatever the message was about, gets one note saying it did
not arrive and why. News only moves a message forward, in the platform's own
order: a late *delivered* never undoes *read*, *not delivered* overrides
everything, and nothing overrides it. A message WhatsApp did not deliver stays
*Sent* and is never sent again by itself: a resend can reach the customer
twice, or fail again for as long as the reason lasts, so it is a person's
decision. A refusal at send time can reach Odoo a moment before the send that
caused it is committed; it is kept and applied by a cron within minutes, and
given up on after a day.

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

* [docs/SETUP.md](docs/SETUP.md) — the twenty-minute walkthrough: install,
  connect, turn on the first message, and what to hand your provider.
* [docs/quick-start/](docs/quick-start/) — the one-page sheet to hand a
  customer, in Arabic and English, and two videos of the real web client: 57
  seconds to connect, and two minutes to write the first rule, post an
  invoice and watch the message go.

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
* [docs/CHANGELOG.md](docs/CHANGELOG.md) — what each release changed, and
  [docs/upgrade/](docs/upgrade/) — how to take a release into a copy you
  changed yourself.

## Branches

| Branch | What it is |
| --- | --- |
| `main`, `18.0` | The source. Odoo 18. |
| `17.0` | Generated from it by `dev/build.py` — `<list>` becomes `<tree>`, the manifest version changes series, every cron gets `numbercall` -1. Do not fix anything here; run `sh dev/publish_17.sh` on 18.0 instead. |

Both install the same four modules, under the same names, at the root of the
branch — which is where the Odoo Apps store and every community repo look.

## Licence

LGPL-3, the same as Odoo's own community addons. See [LICENSE](LICENSE).
