# Changelog

## 1.3.0 — 2026-10-20

* **Send again.** A message can be sent to the customer again by hand: the
  *Send again* button on a message, the same button on a row of the outbox
  that WhatsApp did not deliver, or *Action → Send again* on a selection. A
  message that never went out (its send failed, or somebody cancelled it) goes
  back in the queue as it is. One that did go out is repeated by a new row
  with the same template, values and document, linked both ways (*Sent again
  from* / *Sent again*), so the first row keeps its own delivery news. A row
  already waiting, a copy already waiting, a customer who opted out, a
  connection that is switched off, a practice run and a message whose document
  was deleted are left alone, and the answer says how many. Nothing is ever
  sent again by itself: that stays a person's decision.
* **Upgrade.** `-u kmessage_connector` (one new field on the outbox).

## 1.2.0 — 2026-10-16

* **Delivery news.** Odoo now hears what became of each message it sent. The
  *Delivery* column of the outbox moves on from *Sent* to *Delivered*, *Read*
  or *Not delivered*, the last with WhatsApp's own reason ("add a payment
  method in Meta Business Suite", "outside the 24-hour window"), and the
  invoice, or whatever the message was about, gets one note saying it did not
  arrive and why. News only moves a message forward, and a message WhatsApp
  did not deliver is never sent again by itself: that stays a person's
  decision. This replaces the 1.0.0 limit "there is no delivered/read event on
  the platform today".
* **What an existing installation has to do.** Upgrade the module, then open
  *K-Message → Configuration → Connection*: the form says *Delivery news is
  off for this connection*, and *Switch on delivery news* turns it on. The
  button changes the webhook's events and nothing else (no template, rule or
  assistant tool), only a K-Message administrator sees it, and connecting
  again does the same.
* **What it needs from K-Message.** The platform sends delivery news about
  every message a company sends, campaigns and bot replies included. The
  connector asks for it only from a K-Message that limits it to messages
  carrying a reference, which every message from this Odoo now does, and
  that confirms so by echoing `status_scope: referenced` back. An older
  K-Message is never asked: the connection keeps its four events, the button
  says delivery news needs a newer K-Message, and the *Delivery* column stops
  at *Sent* as before. A webhook set up outside *Connect* (by hand, or by an
  older version of this connector) is not changed from Odoo: whoever manages
  K-Message for the company adds the header `x-managed-by: odoo_connector` to
  it, and then the button works.
* **Odoo 17: the crons run again.** Builds up to 1.1.1 left `numbercall` at
  Odoo 17's default of 1, so every cron, the outbox among them, stopped after
  its first run. The 17 build now gives every cron `numbercall` -1, and
  upgrading switches them back on, the invoicing bridge's *invoices that have
  fallen due* included. Messages that had waited in the stopped outbox for
  more than a day are held back instead of going out weeks late: they are
  marked *Failed* with the reason *Held back when the connector was
  upgraded…*, and *Try again* sends one that should still go out. On Odoo 17,
  look at *Messages*, filter *Waiting*, before upgrading.
* **Upgrading a copy you changed yourself.** Replacing the folder would throw
  your changes away. [docs/upgrade/1.2.0/](upgrade/1.2.0/) has one patch per
  Odoo series, made from 1.1.1 (it serves 1.1.0 too), and a guide in English
  and Arabic: how to list your own changes, apply the patch with them kept,
  and what to do where your changes and the release meet.
  `dev/make_upgrade_patch.sh` writes the patches and proves them: applied to
  an untouched 1.1.1 tree, each must give the release exactly.
* Smaller things: a new cron applies, within minutes, a refusal that reached
  Odoo a moment before its send was recorded, and gives up after a day; news
  about a message this Odoo never sent is answered and not stored; the daily
  tidy deletes at most 20,000 old events a run; *Not delivered* has a filter
  and a button on the connection; the hand-over text for a provider lists
  seven events.

`kmessage_connector` is 18.0.1.2.0 (17.0.1.2.0 in the 17 build). The
invoicing, sales and inventory bridges keep 1.0.1; upgrading the connector
upgrades them with it.

## 1.1.1 — 2026-10-10

* The app icons and the Apps-store covers carry the official K-Message logo
  from the brand guide (the gradient "k" on navy; the account, sales and
  inventory add-ons keep their corner badge). No code change.

## 1.1.0 — 2026-09-26

* A company may hold more than one connection — one per K-Message tenant.
  *Connect* used to refuse a second one ("Each company has a single K-Message
  connection"); now a token from another tenant becomes a connection of its
  own, named after the tenant, and a new token for a tenant already connected
  updates that connection instead of adding one.
* The starter rules are offered per connection, so the second tenant gets its
  own rather than being told the first one's are "already there".
* A token is refused as "not answering" only when every connection of its
  company has answering switched off; `ping` reports the same.
* Upgrading drops the old one-per-company constraint from the database.

## 1.0.0 — 2026-09-20

First release. Installs on Odoo 17.0 and 18.0 from one source tree; the 17
flavour is generated by `dev/build.py` into `dist/17.0/`.

### Odoo sends

* A connection per company: private token, sending number,
  with master switches for sending, for answering, and for a practice run that
  records every message without sending it.
* Templates mirrored from the platform, with the `{{1}}`-style placeholders
  read off the body so mapping them is a form.
* Automations — "when this happens in Odoo, send that template" — with a
  filter, a parameter mapping, a language choice, an optional PDF, and a
  cooldown so the same customer is not told the same thing twice. New rules
  arrive switched off.
* An outbox with retries and backoff, drained by a cron, so a slow or broken
  WhatsApp service can never roll back an invoice. A `4xx` that will never
  succeed stops rather than retrying.
* *Send on WhatsApp* under the Action menu of an invoice, order or delivery.

### K-Message asks

* A token-authenticated REST API at `/kmessage/api/v1/`, plain HTTP with real
  status codes, so a flow tree can branch on `http:2xx`.
* Nine capabilities, each an individual switch: `ping`, `tools`,
  `customer_lookup`, `invoices`, `invoice_pdf`, `orders`, `order_status`,
  `stock_check`, `delivery_status`. Switching one off refuses it with a reason
  rather than answering an empty list.
* Identity is never a parameter: a personal capability takes the conversation's
  phone number and answers only for the customer it belongs to. A number that
  matches two partners matches neither.
* Tokens are stored as fingerprints, shown once, scoped to named capabilities,
  optionally dated, and rate limited.

### Setting up

* **The flows import themselves too.** The two ready-made trees — "where is my
  invoice" and "is it in stock" — used to be three manual steps, two of which
  were a secret copied by hand into a form. Connect now posts them with this
  Odoo's address and a real token already in them. Creating a flow is not one
  of the operator-only writes, so no permission stands in the way. They arrive
  switched off, and a flow of the same name is never replaced.
* **One button.** Connect checks the token and then does the rest. Looking
  first and then asking which parts to do was honest, and it was also a screen
  of decisions nobody outside this addon is equipped to make; *Choose what it
  sets up* keeps that screen for anyone who wants it.
* **One field.** There is one K-Message, so its address is never asked for: it
  comes from `kmessage.account._service_url()` — the system parameter
  `kmessage.base_url` when a developer sets one, otherwise the address built
  into the addon. The customer pastes the private token and presses Connect.
* **One call wires both directions.** Setting up posts the webhook address,
  its secret and the assistant tools to
  `POST /api/integrations/odoo/connect`, which registers them on the platform
  side — so a customer is handed nothing to forward to anybody. The endpoint is
  deliberately narrow: it reads the tenant from the API key, it only ever
  touches rows it marked as its own, and a webhook or tool created by hand is
  left alone even when the name or the address matches. A K-Message without it
  answers `404`, and the wizard falls back to the older hand-over route.
* Starter templates: on connect, the connector writes the templates its
  automations need — Arabic, UTILITY, with a real rendered invoice as the
  sample Meta requires for a document header — and submits them for approval.
  Existing templates are never touched.
* The result is four lines, not a page: who you connected as, how many
  templates went to Meta, that K-Message will call this Odoo, and how many
  questions the assistant can now answer.
* **Starter rules, not just starter templates.** Connecting writes the
  automations too, filled in: trigger, template, and a field path for every
  `{{n}}`. Sending the invoice arrives switched on — it is what the addon is
  installed for, and a rule that has to be discovered and switched on is a
  rule that does not run. Order confirmed, quotation sent and delivery on its
  way arrive off.
* Each one carries a `starter_key`, which is what makes them restorable.
  *Restore the default rules* re-creates whatever is missing and is otherwise
  inert: a default you edited keeps its edits, one you switched off stays off,
  and a rule with no key — anything you wrote — is never even read.
* A live rule whose template is still with Meta now waits instead of failing.
  It used to queue a send per record and take a permanent 404 for each, which
  the invoice rule arriving switched on would have turned into a column of
  failures during every new customer's first few minutes.
* Two recorded walkthroughs of the real web client, not slides: connecting,
  and switching on the first message — the rule written, an ordinary invoice
  posted, and the message leaving with the invoice PDF on it.

### Both directions meet

* Assistant tools: each capability can be published to K-Message as a
  `lookup_tool` or `document_tool`, so the assistant can answer from Odoo.
  K-Message fills in `{{phone_number}}`, and each tool carries its own
  revocable token.
* Two importable flow graphs for tenants who do not want an assistant —
  "where is my invoice" and "is it in stock".
* An inbound webhook, HMAC-verified over the raw body, recording every event
  before acting on it.
* Reply rules: a button tap on a message Odoo sent can run an Odoo server
  action on the record that message was about, and answer the customer back.

### Bridges

`kmessage_connector_account`, `_sale` and `_stock` install themselves beside
the connector when their Odoo app is present, and add the triggers and
capabilities that belong to it. The accounting bridge can also keep a PDF
attached to every posted invoice, which is what makes K-Message's own built-in
"send me my invoice" tool work.

### Known limits

* A send is *accepted*, not delivered: K-Message answers `pending` and reports
  a later failure on its own row. There is no delivered/read event on the
  platform today.
* Creating webhooks and assistant tools the ordinary way is refused on a
  managed plan (`403 operator_only`) — which is why the one-call endpoint
  exists. Against a platform that predates it, the connector does what it can
  and prints exactly what to ask the provider for.
* The inbound half needs an Odoo on a public address: K-Message refuses
  private, loopback and link-local hosts.
