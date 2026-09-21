#!/usr/bin/env python3
"""Record the connect walkthrough as a video.

What a person needs to see before they trust this with their customers is the
whole of it: where the screen lives, what the one field is, what the addon
reports back, and that it finishes. So this drives the real Odoo web client —
no mock-ups, no slides — against the bundled stand-in service in
``dev/fake_kmessage.py``, and Playwright records what the browser actually
showed.

    python3 dev/record_connect.py --odoo http://127.0.0.1:8169 \
        --key whm_… --out /tmp/connect

Point that Odoo at the stand-in before starting it, by setting the system
parameter ``kmessage.base_url`` — there is no address on the screen to type,
and the recording shows exactly that.

It writes a .webm; ``ffmpeg`` turns that into the .mp4 most people can open.
Deliberately slow: pauses are there so a viewer can read the screen, not
because anything is waiting. The parts both recordings share live in
``dev/recording.py``.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from playwright.sync_api import sync_playwright
from recording import VIEWPORT, caption, log_in, open_menu


def open_wizard(page, odoo):
    caption(page, 'K-Message → Configuration → Connect', 3.0)
    open_menu(page, odoo, 'kmessage_connector.action_kmessage_connect',
              ['Configuration', 'Connect'], ready='.modal, .o_form_view')


def fill_and_connect(page, key):
    """Type the one field there is, and press the one button."""
    caption(page, 'One field: your private token from K-Message.', 3.4)
    field = page.locator(
        'div[name="api_key"] input, input[id^="api_key"], input[name="api_key"]').first
    field.wait_for(state='visible', timeout=15000)
    field.click()
    field.fill('')
    field.type(key, delay=45)
    page.wait_for_timeout(1400)

    caption(page, 'One button. It checks the token, then does the rest.', 3.4)
    page.click('button:has-text("Connect")')
    page.wait_for_timeout(5000)


def show_what_it_did(page):
    caption(page, 'Templates written and sent to Meta. Rules set up. '
                  'K-Message told where to find this Odoo.', 4.4)
    page.wait_for_timeout(2000)
    caption(page, 'Done — line by line. Nothing to forward to anyone.', 4.0)
    page.wait_for_timeout(1800)
    caption(page, 'The issued token is shown once. Copy it now.', 3.6)
    page.wait_for_timeout(1500)
    caption(page, 'Sending the invoice is already switched on.', 3.6)
    page.wait_for_timeout(1200)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odoo', default='http://127.0.0.1:8169')
    parser.add_argument('--key', default='whm_demo0000000000000000000000000000')
    parser.add_argument('--out', default='/tmp/kmessage-connect')
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(out),
            record_video_size=VIEWPORT,
        )
        page = context.new_page()
        try:
            log_in(page, args.odoo)
            open_wizard(page, args.odoo)
            fill_and_connect(page, args.key)
            show_what_it_did(page)
        finally:
            video = page.video
            context.close()
            browser.close()
            if video:
                print(video.path())
    return 0


if __name__ == '__main__':
    sys.exit(main())
