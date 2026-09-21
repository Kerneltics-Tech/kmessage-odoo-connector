# Quick start — the sheet and the video

What a customer gets handed. Everything here is generated from this directory,
so it can be regenerated when the screens change.

| File | What it is |
| --- | --- |
| `quick-start-ar.pdf` | One page, Arabic. Install → connect → first message |
| `quick-start-en.pdf` | The same, English |
| `connect-walkthrough.mp4` | 57 seconds: the real Odoo web client, one field, connected |
| `first-message-walkthrough.mp4` | 2¼ minutes: writing the rule, posting an invoice, watching the message go |

## Regenerating the PDFs

Chrome, not wkhtmltopdf — wkhtmltopdf's WebKit shapes Arabic badly and
collides the step badges:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --headless --no-pdf-header-footer \
    --print-to-pdf="$PWD/quick-start-ar.pdf" "file://$PWD/quick-start-ar.html"
```

## Regenerating the videos

It drives the real web client with Playwright — no slides, no mock-ups — against
the stand-in service in `dev/fake_kmessage.py`, so a recording never touches a
real tenant and never sends a real message.

```bash
python3 dev/fake_kmessage.py --port 8787 --api-key whm_demo…   # the stand-in
python3 dev/record_connect.py --out /tmp/connect               # records .webm
ffmpeg -i /tmp/connect/*.webm -vf scale=1440:900,format=yuv420p \
    -c:v libx264 -preset slow -crf 23 -movflags +faststart -an \
    connect-walkthrough.mp4
```

Aim that Odoo at the stand-in before starting it, by setting the system
parameter `kmessage.base_url`. There is no address on the screen, so there is
none in the recording either — the video shows the one field there is.

The second video picks up where the first ends, so its Odoo must already be
connected, and it needs three more things:

```bash
python3 dev/record_automation.py --invoice <a draft invoice id> --out /tmp/first
```

* **A cron thread** — `--max-cron-threads=1`. The last twenty seconds are the
  point: nobody presses send. Shorten `cron_send_queue` to a minute first, or
  the recording waits out its full two.
* **`wkhtmltopdf` on `PATH`** of the server process, or the message goes
  without the invoice PDF — which the addon allows, and which would make the
  closing caption a lie.
* **An approved template.** The stand-in leaves what the connector submits
  `PENDING`, as Meta does; `POST /_approve {"name": …}` is Meta getting back to
  you, and then a re-sync makes it usable.

Two things the recording needs, or the summary fills with refusals that are
true but unhelpful in a demo:

* **`web.base.url` must be a public address**, and `web.base.url.freeze` set to
  `True` — otherwise Odoo rewrites it from the request host on every login, and
  K-Message refuses a webhook pointing at `127.0.0.1`.
* **Restart Odoo after changing it.** The parameter is cached in the registry,
  so a direct database update is not seen by a running server.
