#!/usr/bin/env python3
"""Scoped WhatsApp Web drafter/sender for Lexi.

This is deliberately browser-DOM automation, not desktop keyboard injection:
it only opens web.whatsapp.com, verifies an exact contact before typing, and
never accepts selectors, scripts, or URLs from Lexi's model output.

Two-phase protocol (stdin/stdout, one JSON object per line):
  1. `--draft`: open chat, verify exact contact, type the message, VERIFY the
     composer text, print {"success": true, "stage": "drafted", ...}.
     The message is NOT sent.
  2. Wait on stdin: "send" clicks Send (only after Lexi got an explicit user
     confirmation); "cancel", EOF, or a timeout clears the draft and exits.

WhatsApp Web is not a supported automation API and may rate-limit or block
automated use. For unattended sending use Meta's WhatsApp Business Cloud API.
"""

import argparse
import json
import os
import select
import shutil
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


APP_DIR = os.path.dirname(os.path.realpath(__file__))
PROFILE_DIR = os.path.join(APP_DIR, "whatsapp-playwright-profile")
WHATSAPP_URL = "https://web.whatsapp.com/"
LOGIN_WAIT_MS = 120000
CHAT_SYNC_WAIT_MS = 180000
CONFIRM_WAIT_SEC = 180


def result(success, reason, **extra):
    payload = {"success": success, "reason": reason}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0 if success else 1


def chrome_binary():
    for candidate in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def first_visible(page, selectors, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                locator.wait_for(state="visible", timeout=min(1000, remaining))
                return locator
            except PlaywrightTimeoutError:
                pass
    return None


def is_login_page(page):
    return bool(first_visible(page, (
        "[data-testid='qrcode']",
        "canvas[aria-label*='QR']",
        "text=Log into WhatsApp Web",
        "text=Scan QR code",
    ), 500))


def wait_for_chat_search(page, timeout_ms):
    """Wait for a usable chat search or identify the QR-login state."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        search = first_visible(page, (
            "[aria-label='Search input textbox']",
            "div[contenteditable='true'][data-tab='3']",
            "div[contenteditable='true'][role='textbox']",
        ), 1000)
        if search:
            return search, False
        if is_login_page(page):
            return None, True
    return None, False


def find_exact_contact(page, contact, timeout_ms):
    """Return the single chat-list element whose title equals `contact` (case-insensitive)."""
    deadline = time.monotonic() + timeout_ms / 1000
    want = contact.strip().casefold()
    while time.monotonic() < deadline:
        spans = page.locator("#pane-side span[title]")
        matches = []
        try:
            count = min(spans.count(), 40)
            for i in range(count):
                el = spans.nth(i)
                if (el.get_attribute("title") or "").strip().casefold() == want:
                    matches.append(el)
        except Exception:
            matches = []
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, f"More than one chat is named '{contact}'; I won't guess which"
        time.sleep(0.4)
    return None, f"No exact WhatsApp contact named '{contact}' was found"


def header_title(page):
    header = first_visible(page, ("header span[title]", "header [data-testid='conversation-info-header-chat-title']"), 10000)
    if not header:
        return ""
    return (header.get_attribute("title") or header.inner_text()).strip()


def get_composer(page):
    return first_visible(page, (
        "footer div[contenteditable='true'][role='textbox']",
        "footer div[contenteditable='true']",
    ), 10000)


def normalise_ws(text):
    return " ".join((text or "").split())


def wait_for_decision(timeout_sec):
    """Block for 'send' / 'cancel' on stdin. EOF or timeout counts as cancel."""
    ready, _, _ = select.select([sys.stdin], [], [], timeout_sec)
    if not ready:
        return "cancel"
    line = sys.stdin.readline()
    if not line:
        return "cancel"
    return line.strip().lower()


def draft_message(contact, message):
    binary = chrome_binary()
    if not binary:
        return result(False, "Google Chrome or Chromium is not installed")

    os.makedirs(PROFILE_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(PROFILE_DIR, 0o700)
    except OSError:
        pass

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            executable_path=binary,
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        composer = None
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=30000)

            # A newly linked WhatsApp session can spend minutes downloading chats.
            # Keep its visible window open and wait for the real chat-search signal.
            search, login_required = wait_for_chat_search(page, CHAT_SYNC_WAIT_MS)
            if not search:
                if not login_required:
                    return result(False, "WhatsApp Web did not show its chat search; it may still be loading or require login")
                search, _ = wait_for_chat_search(page, LOGIN_WAIT_MS)
                if not search:
                    return result(False, "WhatsApp is still waiting for QR login. Scan the visible QR code, then ask me again.", login_required=True)

            search.fill(contact)
            row, problem = find_exact_contact(page, contact, 15000)
            if row is None:
                return result(False, problem, contact_verified=False)
            row.click()

            opened = header_title(page)
            if opened.casefold() != contact.strip().casefold():
                return result(False, f"Refused to type: opened '{opened or 'unknown chat'}', not exact contact '{contact}'", contact_verified=False)

            composer = get_composer(page)
            if not composer:
                return result(False, "WhatsApp message box was not available", contact_verified=True)
            composer.fill(message)
            typed = normalise_ws(composer.inner_text())
            if typed != normalise_ws(message):
                composer.fill("")
                return result(False, "The draft text did not appear correctly in WhatsApp, so I cleared it", contact_verified=True, draft_verified=False)

            # Draft is in place and verified. NOTHING is sent yet.
            result(True, "Draft typed; waiting for explicit confirmation", stage="drafted",
                   contact_verified=True, draft_verified=True, sent=False)

            decision = wait_for_decision(CONFIRM_WAIT_SEC)
            if decision != "send":
                try:
                    composer.fill("")
                except Exception:
                    pass
                return result(False, "Draft cancelled; nothing was sent", stage="cancelled", sent=False)

            # Re-check we are still in the verified chat before clicking Send.
            if header_title(page).casefold() != contact.strip().casefold():
                return result(False, "The open chat changed, so I did not send", stage="aborted", sent=False)
            if normalise_ws(composer.inner_text()) != normalise_ws(message):
                return result(False, "The draft text changed, so I did not send", stage="aborted", sent=False)
            send_button = first_visible(page, (
                "button[aria-label='Send']",
                "[data-testid='compose-btn-send']",
                "span[data-icon='send']",
            ), 5000)
            if not send_button:
                return result(False, "WhatsApp's Send button was not available, so nothing was sent", stage="aborted", sent=False)
            send_button.click()
            # Verify: the composer empties once the message is accepted.
            sent_ok = False
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if not normalise_ws(composer.inner_text()):
                    sent_ok = True
                    break
                time.sleep(0.3)
            return result(True, "Send clicked in the verified chat", stage="sent", sent=True,
                          send_verified=sent_ok, contact_verified=True)
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", action="store_true", help="required: type a draft, then wait for confirmation")
    parser.add_argument("--contact", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    if not args.draft:
        return result(False, "Direct sending without a confirmed draft is disabled")
    contact = args.contact.strip()
    message = args.message.strip()
    if not contact or len(contact) > 100 or not message or len(message) > 4000:
        return result(False, "Contact or message is invalid")
    return draft_message(contact, message)


if __name__ == "__main__":
    sys.exit(main())
