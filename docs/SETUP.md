# Setting it up

Written to be followed once, by whoever installs it. Twenty minutes, most of
which is Meta reviewing the templates while you do something else.

---

## Before you start

| You need | Why |
| --- | --- |
| **Odoo 17.0 or 18.0** | Community or Enterprise, no difference |
| **wkhtmltopdf installed** | Odoo renders the invoice PDF with it. Without it Odoo cannot print, so nothing can be attached |
| **A K-Message private token** | *Settings → API Keys* in K-Message. Looks like `whm_…`, shown once |
| **A public address for Odoo** — only for the half where K-Message asks Odoo | K-Message refuses private, loopback and link-local hosts. `localhost`, `10.x`, `192.168.x` and `.local` names are turned away before a request is made |

Sending invoices needs none of that last row: Odoo calls K-Message, so it works
from an Odoo nobody outside can reach.

---

## 1. Install

**Odoo 18** — put the repository on the addons path:

```bash
git clone <this repository> /opt/odoo/kmessage-odoo-connector

cd /path/to/odoo-18
venv/bin/python odoo-bin -d <database> -i kmessage_connector --stop-after-init \
    --addons-path=addons,/opt/odoo/kmessage-odoo-connector
```

**Odoo 17** — build the 17 copy first, then point Odoo at `dist/17.0`:

```bash
cd /opt/odoo/kmessage-odoo-connector && python3 dev/build.py

cd /path/to/odoo-17
venv/bin/python odoo-bin -d <database> -i kmessage_connector --stop-after-init \
    --addons-path=addons,/opt/odoo/kmessage-odoo-connector/dist/17.0
```

Or from the interface: *Apps → Update Apps List*, search **K-Message
Connector**, Activate.

You install one module. The bridges — Invoicing, Sales, Inventory — install
themselves next to it if those apps are present, and stay away if they are not.

**Nothing sends yet.** Installing changes what your Odoo *can* do, not what it
starts doing.

---

## 2. Connect

*K-Message → Configuration → Connect*

One field: your **private token** from K-Message (*Settings → API Keys*, it
starts with `whm_`). There is nothing else to fill in.

Press **Connect**. It looks before it leaps — who the token belongs to, which
WhatsApp numbers the tenant has, how many templates exist, and what this token
is allowed to do — and shows you that before writing anything.

Then choose what it should set up. All four are on by default:

| | What it does |
| --- | --- |
| **Write the templates for me** | Creates the messages this addon sends, in Arabic, and submits them to Meta. Approval usually lands within minutes. Existing templates are never touched |
| **Let K-Message call this Odoo** | Subscribes this Odoo to WhatsApp events |
| **Teach the assistant to ask Odoo** | Publishes each capability as an assistant tool |
| **Issue a token for K-Message** | The key K-Message presents when it asks Odoo about a customer |

Press **Set it up**. It wires both directions itself — one call registers the
webhook and publishes the tools on the K-Message side — and hands you a summary,
line by line:

```
• Connected as Odoo Connector.
• 5 templates written and sent to Meta for approval.
• K-Message will call this Odoo.
• The assistant can now answer 6 questions from Odoo.
```

There is nothing to forward to anyone. If your K-Message is an older build
without that endpoint, it falls back to the older route and tells you the one
thing that is left to do.

> The issued token is shown **once**. Odoo keeps only a fingerprint of it.
> Copy it now, or press *Regenerate* on the token later and use the new value.

---

## 3. Turn on the first message

*K-Message → Configuration → Automations → New*

1. **When** — pick a trigger: an invoice is posted, an order is confirmed, a
   delivery is validated.
2. **Send** — pick a template. `odoo_invoice_ready` is there if step 2 wrote it
   and Meta has approved it (*Templates* shows the status).
3. **Placeholders** — one row per `{{n}}` in the template. For the invoice
   template: `partner_id.name`, `name`, and the amount.
4. **Switch it on.** New rules arrive off.

Then post an invoice. Within two minutes the cron sends it, and the invoice's
chatter says so.

**Try it safely first:** tick *Practice mode* on the connection. Every message
is prepared and recorded, and nothing leaves.

---

## 4. If it could not wire itself (rare)

The half that lets K-Message ask Odoo needs a **public address** — Odoo on
`localhost` is refused before anything is sent, and the screen says so.

Ordinarily step 2 registers the webhook for you. On an older K-Message, or when
a refusal comes back, the connection screen shows exactly what to send whoever
runs your tenant:

```
Address:   https://<your-odoo>/kmessage/api/v1/webhook
Method:    POST
Events:    message.incoming, message.sent, button.reply, contact.created
Secret:    the webhook secret on the connection form
Signature: X-Webhook-Signature: sha256=<hex HMAC-SHA256 of the raw body>
```

Assistant tools take the same route in that case: they wait in
*Configuration → Assistant tools* until somebody with the permission presses
**Publish to K-Message**.

No assistant on the plan? The flow path needs no permissions at all —
[FLOWS.md](FLOWS.md) ships two ready graphs to import.

---

## 5. Decide what Odoo will answer

*K-Message → Configuration → What K-Message may ask*

Nine capabilities, each with its own switch. Untick one and it is refused from
that moment, with a reason, for every caller at once.

Every personal capability takes the phone number from the live conversation and
answers only for that customer. There is no way to ask about somebody else, and
a number that matches two customers matches neither.

---

## Checking it works

```bash
curl -s https://<your-odoo>/kmessage/api/v1/ping \
     -H 'Authorization: Bearer kmc_…'
```

Answers `{"ok": true, …}` with the list of what is switched on. It carries no
customer data, so it is safe to point a monitor at.

Then watch the two lists that matter: **Messages** (everything Odoo sent, and
whether it arrived) and **Incoming** (everything K-Message told this Odoo,
recorded before anything was done about it).

---

## Things worth knowing

* **"Sent" means accepted.** K-Message answers immediately and reports a later
  failure on its own row. The platform has no delivered/read event today.
* **A template is a starting point.** Reword anything the connector wrote, in
  K-Message. It never touches a template twice.
* **Meta locks a deleted template name** for weeks. If you delete one the
  connector wrote, it cannot recreate it under the same name for a while.
* **Three crons do the work**: send the queue (every 2 minutes), chase invoices
  that fell due (daily), tidy the event log (daily).
* **A customer can be opted out** on their contact form — the outbox refuses
  them whatever the automations say.
