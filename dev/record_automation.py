#!/usr/bin/env python3
"""Record the "switch on the first message" walkthrough as a video.

Connecting is half the story. The other half is the part a customer is right
to be nervous about: the moment Odoo starts messaging real people. So this
records it honestly, in the real web client — the rule being written, the
toggle that arrives off, an ordinary invoice being posted, and the message
that follows, with the invoice PDF on it.

    python3 dev/record_automation.py --odoo http://127.0.0.1:8169 \
        --invoice 717 --out /tmp/automation

The Odoo it drives must already be connected — the previous video ends where
this one begins — and must be running with a cron thread, because the point of
the last twenty seconds is that nobody pressed send.

Shared pieces live in ``dev/recording.py``.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright
from recording import VIEWPORT, caption, log_in, open_menu, pick, type_into

#: Which {{n}} gets which field. Three rows is what the invoice template takes,
#: and typing them is the whole of what a person does here.
PLACEHOLDERS = [
    (1, 'partner_id.name', "the customer's name"),
    (2, 'name', 'the invoice number'),
    (3, 'amount_total', 'the amount'),
]


def write_the_rule(page, odoo, template):
    caption(page, 'K-Message → Configuration → Automations', 3.0)
    open_menu(page, odoo, 'kmessage_connector.action_kmessage_automation',
              ['Configuration', 'Automations'], ready='.o_list_view, .o_form_view')

    caption(page, 'One rule: when this happens in Odoo, send that message.', 3.4)
    page.click('button.o_list_button_add, .o-kanban-button-new, button:has-text("New")',
               timeout=10000)
    page.wait_for_selector('.o_form_view', timeout=15000)
    page.wait_for_timeout(1200)

    type_into(page, 'name', 'Send the invoice when it is posted')
    page.wait_for_timeout(600)

    caption(page, 'When: an invoice is posted. Not created — posted.', 3.6)
    pick(page, 'trigger', 'An invoice or credit note is posted')
    page.wait_for_timeout(900)

    caption(page, 'Send: the template the connector wrote and Meta approved.', 3.4)
    pick(page, 'template_id', template)
    page.wait_for_timeout(1200)


def map_the_placeholders(page):
    """Say where each {{n}} gets its value.

    The rows are already there: picking the template offers one per
    placeholder it actually has, so the only thing left is what fills them.
    """
    caption(page, 'The rows are already there \u2014 one per {{n}} in the template.', 3.6)
    page.click('a:has-text("Placeholders"), .nav-link:has-text("Placeholders")', timeout=10000)
    page.wait_for_timeout(1400)

    for position, (_index, path, what) in enumerate(PLACEHOLDERS):
        row = page.locator('.o_field_x2many .o_data_row').nth(position)
        row.locator('div[name="field_path"], td[name="field_path"]').first.click()
        page.wait_for_timeout(400)
        cell = page.locator('.o_selected_row div[name="field_path"] input').first
        cell.fill('')
        cell.type(path, delay=55)
        page.wait_for_timeout(900)
        caption(page, '{{%s}} \u2014 %s.' % (position + 1, what), 2.0)
    page.keyboard.press('Escape')
    page.wait_for_timeout(1200)


def switch_it_on(page):
    caption(page, 'New rules arrive switched off. This is the only thing '
                  'between here and a customer’s phone.', 4.2)
    toggle = page.locator('div[name="active"] input').first
    toggle.wait_for(state='visible', timeout=10000)
    if not toggle.is_checked():
        toggle.check()
    page.wait_for_timeout(1600)

    # `active` is the archive flag, and Odoo saves the record the moment it is
    # toggled — so the save button is only there if something else is pending.
    save = page.locator('button[data-tooltip="Save manually"], .o_form_button_save').first
    if save.count() and save.is_visible():
        save.click()
        page.wait_for_timeout(2600)
    caption(page, 'Saved, and on.', 2.4)


def post_an_invoice(page, odoo, invoice_id):
    caption(page, 'Now an ordinary invoice — nothing about it is special.', 3.4)
    page.goto('%s/odoo/action-account.action_move_out_invoice_type/%s' % (odoo, invoice_id),
              wait_until='domcontentloaded')
    page.wait_for_selector('.o_form_view', timeout=25000)
    page.wait_for_timeout(2400)
    caption(page, 'Confirm is the trigger.', 2.8)
    page.click('button[name="action_post"]', timeout=15000)
    page.wait_for_timeout(3200)
    caption(page, 'Posted. Odoo queued the message on the way through.', 3.6)
    page.wait_for_timeout(1400)


def watch_it_go(page, odoo, patience=90):
    """Show the outbox, and wait for the cron the way a customer would."""
    open_menu(page, odoo, 'kmessage_connector.action_kmessage_message',
              ['Messages'], ready='.o_list_view')
    page.wait_for_timeout(1800)
    caption(page, 'Queued. Nobody pressed send — a cron drains this.', 3.6)

    # The wait is the point, so it is narrated rather than hidden: the row is
    # already carrying the PDF, and nothing in this stretch is a person.
    waiting = [
        'The PDF is already on it \u2014 Odoo printed the invoice when it posted.',
        'Nobody is watching this queue. The cron runs every two minutes.',
        'Still waiting. This is what the customer never sees.',
    ]
    waited = 0
    while waited < patience:
        if waited and waited % 12 == 0:
            caption(page, waiting[(waited // 12 - 1) % len(waiting)], 3.2)
        else:
            page.wait_for_timeout(4000)
        waited += 4
        try:
            page.reload(wait_until='domcontentloaded')
            page.wait_for_selector('.o_list_view', timeout=15000)
        except PlaywrightTimeout:
            continue
        if page.locator('.o_data_row:has-text("Sent")').count():
            page.wait_for_timeout(1200)
            caption(page, 'Sent — with the invoice PDF on it. Two minutes at worst.', 4.2)
            page.wait_for_timeout(1600)
            return True
    caption(page, 'Still queued; the cron runs every two minutes.', 3.4)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odoo', default='http://127.0.0.1:8169')
    parser.add_argument('--invoice', required=True, help='a draft customer invoice to post')
    parser.add_argument('--template', default='odoo_invoice_ready (ar)')
    parser.add_argument('--out', default='/tmp/kmessage-automation')
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
        sent = False
        try:
            log_in(page, args.odoo, 'Connected already. Still nothing sends.')
            write_the_rule(page, args.odoo, args.template)
            map_the_placeholders(page)
            switch_it_on(page)
            post_an_invoice(page, args.odoo, args.invoice)
            sent = watch_it_go(page, args.odoo)
        finally:
            video = page.video
            context.close()
            browser.close()
            if video:
                print(video.path())
            print('reached sent: %s' % sent)
    return 0


if __name__ == '__main__':
    sys.exit(main())
