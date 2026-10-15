# Upgrading to K-Message Connector 1.2.0

[بالعربي](README.ar.md)

1.2.0 brings **delivery news**: Odoo hears whether each WhatsApp message it
sent was *Delivered*, *Read* or *Not delivered*, and when WhatsApp refused one
(for example because the WhatsApp Business account has no working payment
method), the invoice it was about gets a note with WhatsApp's reason. On
Odoo 17 it also makes the connector's scheduled jobs run again. The full list
is in the [changelog](../../CHANGELOG.md).

This guide is for an Odoo that runs 1.1.1 or 1.1.0. It matters most if you,
or a developer for you, **changed the connector's code**: replacing the
folders with the new version would throw those changes away without a word.
The patches in this folder bring 1.2.0 into your copy and keep your changes.

## Before you start

1. **Back up** the database, and copy the connector's folders
   (`kmessage_connector*`: the connector and the invoicing, sales and
   inventory bridges you have) somewhere **outside** Odoo's addons path. A
   copy left beside them would look like another module to Odoo.
2. **Note your Odoo version** (17 or 18) and **your connector version**. In
   the folder that holds `kmessage_connector/`:

   ```bash
   grep "'version'" kmessage_connector/__manifest__.py
   ```

   `18.0.1.1.1` or `17.0.1.1.1` is 1.1.1, and `…1.1.0` is 1.1.0. This guide
   does not cover 1.0.x (`1.0.0` or `…1.0.0`): send us your folders
   ([route C](#route-c-send-us-your-folders)).
3. **On Odoo 17**, open *K-Message → Messages* and filter *Waiting*. Up to
   1.1.1 the connector's scheduled jobs stopped after their first run on
   Odoo 17, so messages may have waited there for weeks. The upgrade lets the
   ones from the last day go out and holds the older ones back: they are
   marked *Failed* with the reason *Held back when the connector was
   upgraded…*, and *Try again* sends one that should still go out.

## Step 1: see what you changed

Download the untouched 1.1.1 and compare your folders with it:

```bash
mkdir -p ~/kmessage-1.1.1 && cd ~/kmessage-1.1.1
curl -sL https://github.com/Kerneltics-Tech/kmessage-odoo-connector/archive/3c406d9d0fd312698db0b667702bea2387559505.tar.gz | tar -xz --strip-components=1

cd /path/to/your/addons       # the folder that holds kmessage_connector/

# Odoo 18:
for m in kmessage_connector*; do diff -ru -x __pycache__ -x '*.pyc' ~/kmessage-1.1.1/$m $m; done > ~/my-changes.diff
# Odoo 17 (the 17 build of 1.1.1 is in dist/17.0):
for m in kmessage_connector*; do diff -ru -x __pycache__ -x '*.pyc' ~/kmessage-1.1.1/dist/17.0/$m $m; done > ~/my-changes.diff
```

* `~/my-changes.diff` is empty: nobody changed the code. Take
  [route A](#route-a-you-never-changed-the-code).
* It lists changes: those are yours. Take [route B](#route-b-apply-the-patch-keep-your-changes),
  or [route C](#route-c-send-us-your-folders) if you would rather we merged
  them. Keep the file: it is the list of what must survive.

On 1.1.0 the list also shows the version lines and the logo pictures
(`Binary files … differ`). Those are not your changes: 1.1.1 changed only
version numbers and pictures, and its code is the same as 1.1.0's.

## Route A: you never changed the code

Take 1.2.0 as it is, then [upgrade the module](#upgrade-the-module):

* A git clone of `main` or `18.0`: `git pull`.
* A git clone of `17.0`: that branch is rebuilt for every release, so
  `git pull` will not work. As you changed nothing (`git status` shows
  nothing), run `git fetch origin && git reset --hard origin/17.0`.
* No git: download the
  [18.0](https://github.com/Kerneltics-Tech/kmessage-odoo-connector/archive/refs/heads/18.0.zip) or
  [17.0](https://github.com/Kerneltics-Tech/kmessage-odoo-connector/archive/refs/heads/17.0.zip)
  branch and replace your `kmessage_connector*` folders with the ones in it.

## Route B: apply the patch, keep your changes

There is one patch per Odoo version, from 1.1.1 to 1.2.0:

* [kmessage_connector-18.0.patch](kmessage_connector-18.0.patch) for Odoo 18.
* [kmessage_connector-17.0.patch](kmessage_connector-17.0.patch) for Odoo 17.
  Besides the connector, it adds one line to
  `kmessage_connector_account/data/ir_cron.xml` so that the *invoices that
  have fallen due* job keeps running. If your folder has no
  `kmessage_connector_account`, add `--exclude='kmessage_connector_account/*'`
  to the `git apply` commands.

The commands below use the Odoo 18 file; on Odoo 17 write `17.0` instead of
`18.0` everywhere.

```bash
curl -sL -o ~/kmessage_connector-18.0.patch https://raw.githubusercontent.com/Kerneltics-Tech/kmessage-odoo-connector/main/docs/upgrade/1.2.0/kmessage_connector-18.0.patch
cd /path/to/your/addons       # the folder that holds kmessage_connector/
```

**On 1.1.0 only**, first set the version line to 1.1.1. The code is the same,
and this line is the one thing that would stop the patch:

```bash
sed -i.bak "s/'version': '18.0.1.1.0'/'version': '18.0.1.1.1'/" kmessage_connector/__manifest__.py
rm kmessage_connector/__manifest__.py.bak
```

Then check whether the patch fits:

```bash
git apply --check -v ~/kmessage_connector-18.0.patch
# or, without git:  patch -p1 --dry-run < ~/kmessage_connector-18.0.patch
```

**No error**: none of your changes is on, or right next to, a line 1.2.0
changes. Apply it, and make sure it really went in:

```bash
git apply -v ~/kmessage_connector-18.0.patch
# or, without git:  patch -p1 < ~/kmessage_connector-18.0.patch
grep "'version'" kmessage_connector/__manifest__.py        # must now say 18.0.1.2.0
```

Then [upgrade the module](#upgrade-the-module). Your changes are still there:
compared with the untouched 1.2.0, only they differ.

> **If that folder is inside a git repository, but not at its top**,
> `git apply` skips every file, says nothing and reports success, and the
> version line still says 1.1.1. Run it from the top of the repository
> instead, with `--directory=` the path from there to the folder, for example
> `git apply -v --directory=custom/addons ~/kmessage_connector-18.0.patch`.
> `patch -p1` does not have this problem.

**Errors** (`patch failed: …` and `patch does not apply`, or `FAILED` from
`patch`): some of your changes are on, or right next to, lines 1.2.0 changes.
Nothing has been changed yet: `git apply` applies everything or nothing, and `--dry-run`
only looks. Let git merge the two in a scratch copy; it marks every place
where they meet:

```bash
mkdir ~/kmessage-merge && cp -R /path/to/your/addons/kmessage_connector* ~/kmessage-merge/
cd ~/kmessage-merge
git init -q && git add -A
git fetch -q https://github.com/Kerneltics-Tech/kmessage-odoo-connector.git main    # how 1.1.1 looked
git apply --3way ~/kmessage_connector-18.0.patch
git diff --name-only --diff-filter=U        # the files left for you to resolve
```

Everything else is applied. In each file listed, every place where your change
and 1.2.0's meet looks like this:

```
<<<<<<< ours
    (your version)
=======
    (1.2.0's version)
>>>>>>> theirs
```

Keep 1.2.0's version, put your change back into it where it still makes
sense, and delete the three marker lines. If your change was to add delivery
events to the list of events yourself, simply keep 1.2.0's version: it asks
for them itself, and only from a K-Message that limits them to this Odoo's
messages. When you are done, these must print nothing but the version:

```bash
grep -rnE '^(<<<<<<<|=======|>>>>>>>)' kmessage_connector*
python3 -m compileall -q kmessage_connector*
grep "'version'" kmessage_connector/__manifest__.py        # 18.0.1.2.0
```

Copy the result back over your folders (1.2.0 deletes no file, so copying
over is enough), then [upgrade the module](#upgrade-the-module):

```bash
cp -R ~/kmessage-merge/kmessage_connector* /path/to/your/addons/
```

Where your changes are most likely to meet 1.2.0's:
`models/kmessage_account.py` (the list of events at the top, the connection's
fields, connecting, setting up the webhook), `models/kmessage_event.py` (what
Odoo does with each event), `models/kmessage_message.py` (the delivery fields,
sending), `tools/client.py`, `controllers/main.py` (the end of the webhook),
`views/kmessage_account_views.xml`, `views/kmessage_message_views.xml`,
`data/ir_cron.xml` and `i18n/ar.po`. Beyond those, the patch changes only the
version line, the store page, the translation template (`.pot`) and the
tests, and on Odoo 17 the one line in the invoicing bridge. Your own
templates, rules and reports, and the rest of the bridges, are not touched.

**Your copy lives in your own git repository** (on Odoo.sh, for example): do
the same there instead of in a scratch copy. Commit your work, then, at the
top of the repository, run the `git fetch` above and
`git apply --3way --directory=<path to the folder> ~/kmessage_connector-18.0.patch`;
resolve, commit and push. If the repository is a clone of this one, with your
changes committed on `main` or `18.0`, `git pull --no-rebase` merges 1.2.0 for
you and stops the same way where your changes meet it.

## Route C: send us your folders

If you would rather not merge, or your version is one this guide does not
cover, send your K-Message contact the folders exactly as they are on your
server, and say whether it is Odoo 17 or 18:

```bash
cd /path/to/your/addons && zip -r ~/kmessage-connector-ours.zip kmessage_connector* -x '*/__pycache__/*'
```

We merge 1.2.0 into them the same way and send them back with your changes
kept, with a note of every place where yours and ours met. You copy them in
and upgrade the module.

## Upgrade the module

Upgrading the connector upgrades the invoicing, sales and inventory bridges
with it. Either:

* from the command line, then start Odoo again:

  ```bash
  odoo-bin -c <your odoo.conf> -d <database> -u kmessage_connector --stop-after-init
  ```

* or in Odoo: **restart the Odoo service first**, so that it reads the new
  Python code; then open *Apps*, find **K-Message Connector**, and choose
  *Upgrade* in its card's menu (or on its *Module Info*).

In developer mode, *Module Info* shows *Latest Version* 18.0.1.2.0
(17.0.1.2.0 on Odoo 17).

## Switch on delivery news

Open *K-Message → Configuration → Connection*. A connection made before 1.2.0
says **Delivery news is off for this connection.** Press **Switch on delivery
news** (only a K-Message administrator sees the button). It changes the
webhook's events and nothing else: no template, rule or assistant tool.

It worked when:

* the note says *Delivery news is on: Odoo hears when a message is delivered,
  read or not delivered*, and the banner is gone;
* *Events*, under *Webhook*, lists `message.delivered`, `message.failed` and
  `message.read` beside the four others;
* the next messages in *K-Message → Messages* move on to *Delivered* and
  *Read*. A message WhatsApp refused shows *Not delivered* with the reason,
  and its invoice gets a note. It is never sent again by itself: that stays
  your decision.

## If it does not switch on

* **K-Message cannot call this Odoo: …** Delivery news, like a customer's
  reply, is K-Message calling your Odoo, so Odoo has to be reachable from the
  internet at its web address (`web.base.url`). Sending works without that;
  this part does not.
* **The webhook for this address was set up outside Connect to K-Message (by
  hand, or by an older version of this connector)…** Odoo changes only a
  webhook it set up itself. Ask whoever manages K-Message for your company to
  add the header `x-managed-by: odoo_connector` to that webhook, then press
  *Switch on delivery news* again.
* **Delivery news needs a newer K-Message, so it stays off for now.** That
  K-Message cannot yet limit delivery news to this Odoo's own messages, and
  the connector will not take news about every message the company sends. Ask
  your provider to update K-Message, then press the button again. Until then
  the *Delivery* column stops at *Sent*, as in 1.1.1, and nothing else
  changes.
* **This K-Message does not take a connection from Odoo yet.** The platform is
  older still; the same applies.
* **There is no banner**, and the webhook state says *Waiting for your
  provider*: on your plan only the provider sets up webhooks. Ask them to add
  `message.delivered`, `message.read` and `message.failed` to this Odoo's
  webhook. The connection form shows its address and secret.
* **Your own code sends messages around the connector's outbox**, calling the
  K-Message API itself: Odoo hears nothing about those messages. K-Message
  sends news only about messages that carry a reference, and Odoo keeps only
  news it can put on a row in *Messages*. Send through the outbox instead,
  which gives every message both:
  `env['kmessage.message'].enqueue(account, phone, template, params=..., record=invoice)`.

## About the patches

Both come from `sh dev/make_upgrade_patch.sh 3c406d9`, run on the 1.2.0
release. The script checks them before it is done: each is applied to an
untouched 1.1.1, once with `git apply` and once with `patch -p1`, and the
result has to be 1.2.0 to the byte. They hold text only (no picture changed),
so either tool applies them. The 17.0 patch fits both the published `17.0`
branch of 1.1.1 and its `dist/17.0`, which hold the same files.
