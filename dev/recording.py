# -*- coding: utf-8 -*-
"""The parts every walkthrough recording needs.

There are two videos — connecting, and switching on the first message — and
they are recorded the same way: drive the real Odoo web client with Playwright
against the stand-in in ``dev/fake_kmessage.py``, narrate with an overlaid
caption, and let the browser record what it actually showed. Nothing here is a
mock-up.

Kept in one module so the two scripts stay siblings. A caption that is timed
differently in one video than the other is a bug nobody notices until the two
are watched back to back.
"""

from __future__ import annotations

from playwright.sync_api import TimeoutError as PlaywrightTimeout

#: How long a caption stays up, and how long a filled field is left to be read.
#: Deliberately slow: the pauses are for a viewer, not for the software.
BEAT = 2200

#: The frame the recordings are made at. Big enough that Odoo lays out as it
#: would on a laptop, and an even multiple of nothing in particular.
VIEWPORT = {'width': 1440, 'height': 900}


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


def log_in(page, odoo, note='Odoo, with the K-Message Connector installed.'):
    page.goto('%s/web/login' % odoo, wait_until='domcontentloaded')
    page.wait_for_timeout(800)
    caption(page, note, 2.6)
    page.fill('input[name="login"]', 'admin')
    page.fill('input[name="password"]', 'admin')
    page.wait_for_timeout(600)
    page.click('button[type="submit"]')
    # Never networkidle here: Odoo's bus long-polls, so the page is never idle
    # and the wait would simply time out on a page that loaded perfectly.
    page.wait_for_selector('.o_main_navbar', timeout=30000)
    page.wait_for_timeout(1500)


def open_menu(page, odoo, action_xmlid, menu_labels, ready='.o_list_view, .o_form_view, .modal'):
    """Open a screen by clicking through the menus, and land there regardless.

    The apps menu is the part most likely to move between Odoo versions, so
    every step has a fallback and the action URL is the last one — a video that
    fails to record teaches nobody anything.
    """
    try:
        page.click('.o_navbar_apps_menu button, button.o_navbar_apps_menu, '
                   '[aria-label="Home menu"], [title="Home menu"]', timeout=6000)
        page.wait_for_timeout(1200)
        page.click('.o_app:has-text("K-Message"), a.o_app:has-text("K-Message")', timeout=6000)
        page.wait_for_timeout(2000)
        for label in menu_labels:
            page.click('button:has-text("%s"), a:has-text("%s"), span:has-text("%s")'
                       % (label, label, label), timeout=6000)
            page.wait_for_timeout(1000)
    except PlaywrightTimeout:
        page.goto('%s/odoo/action-%s' % (odoo, action_xmlid), wait_until='domcontentloaded')
        page.wait_for_timeout(2000)
    page.wait_for_selector(ready, timeout=20000)
    page.wait_for_timeout(1800)


def type_into(page, name, value, delay=45):
    """Fill a named field the way a person would, slowly enough to read."""
    field = page.locator(
        'div[name="%s"] input, div[name="%s"] textarea, input[id^="%s"], input[name="%s"]'
        % (name, name, name, name)).first
    field.wait_for(state='visible', timeout=15000)
    field.click()
    field.fill('')
    field.type(value, delay=delay)
    page.wait_for_timeout(600)


def pick(page, name, label):
    """Choose ``label`` in the many2one or selection field ``name``."""
    field = page.locator('div[name="%s"] input, div[name="%s"] select' % (name, name)).first
    field.wait_for(state='visible', timeout=15000)
    if field.evaluate('el => el.tagName') == 'SELECT':
        field.select_option(label=label)
        page.wait_for_timeout(600)
        return
    field.click()
    page.wait_for_timeout(400)
    field.fill(label[:24])
    page.wait_for_timeout(900)
    page.click('.o-autocomplete--dropdown-item:has-text("%s"), '
               '.ui-menu-item:has-text("%s")' % (label[:24], label[:24]), timeout=8000)
    page.wait_for_timeout(700)


def save(page):
    """Press the breadcrumb's save, and wait for the record to settle."""
    page.click('button[data-tooltip="Save manually"], .o_form_button_save, '
               'button:has-text("Save manually")', timeout=10000)
    page.wait_for_timeout(2500)
