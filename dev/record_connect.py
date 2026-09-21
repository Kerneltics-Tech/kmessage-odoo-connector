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
because anything is waiting.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

#: How long a caption stays up, and how long a filled field is left to be read.
BEAT = 2200


def caption(page, text, seconds=3.0):
    """Put a line of explanation over the page, the way a narrator would."""
    page.evaluate(
        """([text, seconds]) => {
            const id = 'kmessage-caption';
            document.getElementById(id)?.remove();
            const el = document.createElement('div');
            el.id = id;
            el.textContent = text;
            Object.assign(el.style, {
                position: 'fixed', left: '0', right: '0', bottom: '0',
                padding: '18px 28px', background: 'rgba(9,2,75,.94)', color: '#fff',
                font: '500 19px/1.5 -apple-system, Segoe UI, Roboto, sans-serif',
                zIndex: '2147483647', textAlign: 'center', letterSpacing: '.2px',
            });
            document.body.appendChild(el);
            setTimeout(() => el.remove(), seconds * 1000);
        }""",
        [text, seconds],
    )
    page.wait_for_timeout(seconds * 1000)


def log_in(page, odoo):
    page.goto('%s/web/login' % odoo, wait_until='domcontentloaded')
    page.wait_for_timeout(800)
    caption(page, 'Odoo, with the K-Message Connector installed.', 2.6)
    page.fill('input[name="login"]', 'admin')
    page.fill('input[name="password"]', 'admin')
    page.wait_for_timeout(600)
    page.click('button[type="submit"]')
    # Never networkidle here: Odoo's bus long-polls, so the page is never idle
    # and the wait would simply time out on a page that loaded perfectly.
    page.wait_for_selector('.o_main_navbar', timeout=30000)
    page.wait_for_timeout(1500)


def open_wizard(page, odoo):
    """Open the wizard, showing where it lives when the menus allow it.

    The apps menu is the part most likely to move between Odoo versions, so
    every step has a fallback and the action URL is the last one — a video
    that fails to record teaches nobody anything.
    """
    caption(page, 'K-Message → Configuration → Connect', 3.0)
    try:
        page.click('.o_navbar_apps_menu button, button.o_navbar_apps_menu, '
                   '[aria-label="Home menu"], [title="Home menu"]', timeout=6000)
        page.wait_for_timeout(1200)
        page.click('.o_app:has-text("K-Message"), a.o_app:has-text("K-Message")', timeout=6000)
        page.wait_for_timeout(2000)
        page.click('button[data-menu-xmlid$="menu_kmessage_setup"], '
                   'button:has-text("Configuration")', timeout=6000)
        page.wait_for_timeout(1000)
        page.click('[data-menu-xmlid$="menu_kmessage_connect"], '
                   'a:has-text("Connect"), span:has-text("Connect")', timeout=6000)
    except PlaywrightTimeout:
        page.goto('%s/odoo/action-kmessage_connector.action_kmessage_connect' % odoo,
                  wait_until='domcontentloaded')
        page.wait_for_timeout(2000)
    page.wait_for_selector('.modal, .o_form_view', timeout=20000)
    page.wait_for_timeout(2200)


def fill_and_connect(page, key):
    """Type the one field there is."""
    caption(page, 'One field: your private token from K-Message.', 3.4)
    field = page.locator(
        'div[name="api_key"] input, input[id^="api_key"], input[name="api_key"]').first
    field.wait_for(state='visible', timeout=15000)
    field.click()
    field.fill('')
    field.type(key, delay=45)
    page.wait_for_timeout(1400)

    caption(page, 'Connect looks first — it changes nothing yet.', 3.0)
    page.click('button:has-text("Connect")')
    page.wait_for_timeout(3500)


def apply_setup(page):
    caption(page, 'It reports who the token belongs to, and what it may do.', 3.6)
    page.wait_for_timeout(1200)
    caption(page, 'Now it writes the WhatsApp templates, registers itself with '
                  'K-Message, and publishes the assistant tools.', 4.2)
    page.click('button:has-text("Set it up")')
    page.wait_for_timeout(4000)
    caption(page, 'Done — line by line, what it did. Nothing to forward to anyone.', 4.0)
    page.wait_for_timeout(2200)
    caption(page, 'The issued token is shown once. Copy it now.', 3.6)
    page.wait_for_timeout(1500)


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
            viewport={'width': 1440, 'height': 900},
            record_video_dir=str(out),
            record_video_size={'width': 1440, 'height': 900},
        )
        page = context.new_page()
        try:
            log_in(page, args.odoo)
            open_wizard(page, args.odoo)
            fill_and_connect(page, args.key)
            apply_setup(page)
        finally:
            video = page.video
            context.close()
            browser.close()
            if video:
                print(video.path())
    return 0


if __name__ == '__main__':
    sys.exit(main())
