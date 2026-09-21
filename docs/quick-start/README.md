# Quick start — the sheet and the video

What a customer gets handed. Everything here is generated from this directory,
so it can be regenerated when the screens change.

| File | What it is |
| --- | --- |
| `quick-start-ar.pdf` | One page, Arabic. Install → connect → first message |
| `quick-start-en.pdf` | The same, English |
| `connect-walkthrough.mp4` | 57 seconds: the real Odoo web client, one field, connected |

## Regenerating the PDFs

Chrome, not wkhtmltopdf — wkhtmltopdf's WebKit shapes Arabic badly and
collides the step badges:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --headless --no-pdf-header-footer \
    --print-to-pdf="$PWD/quick-start-ar.pdf" "file://$PWD/quick-start-ar.html"
```

## Regenerating the video

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

`--platform` aims the wizard at the stand-in, and it does that behind the
*hosted elsewhere* switch — off camera, where a customer never goes. The
recording shows the one field there is.

Two things the recording needs, or the summary fills with refusals that are
true but unhelpful in a demo:

* **`web.base.url` must be a public address**, and `web.base.url.freeze` set to
  `True` — otherwise Odoo rewrites it from the request host on every login, and
  K-Message refuses a webhook pointing at `127.0.0.1`.
* **Restart Odoo after changing it.** The parameter is cached in the registry,
  so a direct database update is not seen by a running server.
