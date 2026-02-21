"""
whatsapp_watcher.py
-------------------
Monitors WhatsApp Web for incoming messages containing trigger keywords.
Creates task files in Needs_Action/ for each match found.

Usage:
    python Watchers/whatsapp_watcher.py

On first run a QR code will appear — scan it with your phone.
Subsequent runs reuse the saved session in Watchers/whatsapp_session/.
"""

import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

SESSION_PATH = Path(os.getenv("WHATSAPP_SESSION_PATH", "./Watchers/whatsapp_session"))
CHECK_INTERVAL = int(os.getenv("WHATSAPP_CHECK_INTERVAL", "30"))
HEADLESS = os.getenv("WHATSAPP_HEADLESS", "false").strip().lower() == "true"
NEEDS_ACTION_DIR = Path(os.getenv("NEEDS_ACTION_DIR", "./Needs_Action"))
LOGS_DIR = Path(os.getenv("LOGS_DIR", "./Logs"))

PROCESSED_PATH = Path("./Watchers/processed_whatsapp.json")

TRIGGER_KEYWORDS = [
    "urgent", "asap", "invoice", "payment", "help", "problem", "issue",
    "emergency", "important", "deadline", "overdue", "contract", "quote",
    "proposal", "meeting",
]

HIGH_PRIORITY_KEYWORDS = {"urgent", "asap", "emergency", "overdue"}

WHATSAPP_URL = "https://web.whatsapp.com"
CHAT_LIST_SELECTOR = 'div[aria-label="Chat list"]'
QR_CODE_SELECTOR = 'canvas[aria-label="Scan this QR code to link a device"]'

CDP_URL = os.getenv("WHATSAPP_CDP_URL", "http://localhost:9222")
CHROME_USER_DATA = os.getenv("WHATSAPP_CHROME_USER_DATA", r"C:\chrome_wa_session")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOGS_DIR.mkdir(parents=True, exist_ok=True)
NEEDS_ACTION_DIR.mkdir(parents=True, exist_ok=True)
SESSION_PATH.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("whatsapp_watcher")
log.setLevel(logging.DEBUG)

if not log.handlers:
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    _fh = logging.FileHandler(LOGS_DIR / "whatsapp_watcher.log", encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)

    _ch = logging.StreamHandler(sys.stdout)
    _ch.setFormatter(_fmt)
    log.addHandler(_ch)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_processed() -> dict:
    """Load already-processed message fingerprints from disk."""
    if PROCESSED_PATH.exists():
        try:
            with open(PROCESSED_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not load processed fingerprints: %s", exc)
    return {}


def save_processed(processed: dict) -> None:
    """Persist processed fingerprints to disk."""
    PROCESSED_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(PROCESSED_PATH, "w", encoding="utf-8") as f:
            json.dump(processed, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        log.error("Could not save processed fingerprints: %s", exc)


def make_fingerprint(chat_name: str, text: str, timestamp: str) -> str:
    raw = f"{chat_name}{text[:200]}{timestamp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def slugify(text: str) -> str:
    """Convert a string to a safe filename slug."""
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s]+", "_", text.strip())
    return text[:40]


def detect_keywords(text: str) -> list[str]:
    """Return trigger keywords found in text (case-insensitive)."""
    lower = text.lower()
    return [kw for kw in TRIGGER_KEYWORDS if kw in lower]


def determine_priority(keywords: list[str]) -> str:
    if any(kw in HIGH_PRIORITY_KEYWORDS for kw in keywords):
        return "HIGH"
    return "MEDIUM"


def create_task_file(
    chat_name: str,
    message_text: str,
    timestamp: str,
    keywords: list[str],
) -> Path:
    """Write a Markdown task file to Needs_Action/ and return its path."""
    now = datetime.now()
    date_str = now.strftime("%Y%m%d_%H%M%S")
    slug = slugify(chat_name)
    filename = f"{date_str}_WA_{slug}_action.md"
    filepath = NEEDS_ACTION_DIR / filename

    priority = determine_priority(keywords)
    received_iso = timestamp if timestamp else now.isoformat()

    # Escape any YAML-special characters in the message preview
    preview = message_text[:500].replace('"', '\\"').replace("\n", " ")

    content = f"""---
type: whatsapp_message
source: WhatsApp
from: "{chat_name}"
received: "{received_iso}"
priority: {priority}
status: needs_action
keywords_matched: [{", ".join(keywords)}]
---

# WhatsApp Message — Action Required

**From:** {chat_name}
**Received:** {received_iso}
**Priority:** {priority}
**Keywords detected:** {", ".join(keywords)}

## Message

{message_text}

## Suggested Actions

- [ ] Review message content
- [ ] Draft a reply (save to `Pending_Approval/`)
- [ ] Obtain human approval
- [ ] Move approved reply to `Approved/`
- [ ] Send via WhatsApp

---
*Auto-generated by whatsapp_watcher.py*
"""
    filepath.write_text(content, encoding="utf-8")
    log.info("Task file created: %s", filepath)
    return filepath


# ---------------------------------------------------------------------------
# Core scanning logic
# ---------------------------------------------------------------------------



def scan_page_for_keywords(page, processed: dict) -> int:
    """
    Scan WhatsApp Web for trigger keywords without navigating away from the
    chat list.

    Strategy:
      1. Fast gate — read full body text; bail if no keywords present.
      2. For each unread-badge element, walk up the DOM to the full chat row
         and read its innerText directly.  The first line is the contact name;
         the rest is the message preview.
      3. If keywords found in the row text, create a task file immediately.
         No chat is ever opened; the page never navigates.

    Returns the number of new task files created.
    """
    new_tasks = 0

    # --- Step 1: fast gate ---
    try:
        body_text = page.inner_text("body")
    except Exception as exc:
        log.error("Could not read page body text: %s", exc)
        return 0

    if not detect_keywords(body_text):
        log.debug("No trigger keywords found in page text — skipping.")
        return 0

    log.info("Trigger keyword(s) detected on page. Checking unread chat rows …")

    # --- Step 2: collect unread badge elements ---
    try:
        badges = page.query_selector_all('[aria-label*="unread message"]')
    except Exception as exc:
        log.error("Could not query unread badge elements: %s", exc)
        return 0

    log.info("Found %d unread chat badge(s).", len(badges))

    for badge in badges:
        # Walk up the DOM to the full chat row and grab its rendered text
        try:
            row_text = badge.evaluate("""el => {
                let node = el;
                for (let i = 0; i < 10; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    const r = node.getBoundingClientRect();
                    if (r.height > 50 && r.width > 200) {
                        return node.innerText;
                    }
                }
                return '';
            }""")
        except Exception as exc:
            log.warning("Could not read chat row text: %s", exc)
            continue

        if not row_text or not row_text.strip():
            log.debug("Empty row text — skipping badge.")
            continue

        matched_keywords = detect_keywords(row_text)
        if not matched_keywords:
            log.debug("No trigger keywords in row text — skipping.")
            continue

        # First line of the row text is the contact/group name
        lines = [l.strip() for l in row_text.splitlines() if l.strip()]
        chat_name = lines[0] if lines else "Unknown"
        preview = " ".join(lines[1:])[:500] if len(lines) > 1 else row_text[:500]

        log.info(
            "Keyword(s) %s found in row for '%s'.",
            matched_keywords, chat_name,
        )

        # --- Deduplication ---
        timestamp = datetime.now().isoformat()
        fingerprint = make_fingerprint(chat_name, row_text, "")
        if fingerprint in processed:
            log.debug("Already processed '%s' — skipping.", chat_name)
            continue

        # --- Create task file ---
        log.info(
            "New actionable message from '%s' [keywords: %s] — creating task.",
            chat_name, matched_keywords,
        )
        create_task_file(chat_name, preview, timestamp, matched_keywords)
        processed[fingerprint] = {
            "chat": chat_name,
            "timestamp": timestamp,
            "keywords": matched_keywords,
            "processed_at": timestamp,
        }
        new_tasks += 1

    return new_tasks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    log.info("LOGS_DIR is: %s", LOGS_DIR.resolve())
    log.info("=" * 60)
    log.info("WhatsApp Watcher starting up.")
    log.info("Session path : %s", SESSION_PATH.resolve())
    log.info("Needs_Action : %s", NEEDS_ACTION_DIR.resolve())
    log.info("Check interval: %d seconds", CHECK_INTERVAL)
    log.info("=" * 60)

    processed = load_processed()
    log.info("Loaded %d previously processed fingerprints.", len(processed))

    with sync_playwright() as pw:

        # --- Step 1: Try connecting to an already-running Chrome via CDP ---
        log.info("Trying to connect to Chrome via CDP at %s …", CDP_URL)
        browser = None
        try:
            browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=5000)
            log.info("Connected to existing Chrome session.")
        except Exception:
            # Chrome is not running with remote debugging — print instructions
            print()
            print("=" * 60)
            print("  Chrome with remote debugging is not running.")
            print("=" * 60)
            print()
            print("  Open a NEW terminal and run:")
            print()
            print(r'  & "C:\Program Files\Google\Chrome\Application\chrome.exe"'
                  r' --remote-debugging-port=9222 --user-data-dir="C:\chrome_wa_session"')
            print()
            print("  Then go to https://web.whatsapp.com and scan the QR code")
            print("  with your phone (WhatsApp → Linked Devices → Link a Device).")
            print()
            input("  >>> Press Enter once Chrome is open and WhatsApp Web QR is scanned … ")
            print()

            log.info("Retrying CDP connection at %s …", CDP_URL)
            try:
                browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=10000)
                log.info("Connected to Chrome via CDP.")
            except Exception as exc:
                log.error(
                    "Could not connect to Chrome on %s. "
                    "Make sure chrome.exe was started with --remote-debugging-port=9222. "
                    "Error: %s",
                    CDP_URL, exc,
                )
                sys.exit(1)

        # --- Step 2: Get a page from the connected browser ---
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})

        # Navigate to WhatsApp Web if the tab isn't already there
        if "web.whatsapp.com" not in page.url:
            log.info("Navigating to %s …", WHATSAPP_URL)
            try:
                page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=60000)
            except Exception as exc:
                log.error("Failed to load WhatsApp Web: %s", exc)
                browser.close()
                sys.exit(1)

        # --- Step 3: Wait for the chat list or QR code ---
        log.info("Waiting for WhatsApp Web to initialise …")
        try:
            page.wait_for_selector(
                f'{CHAT_LIST_SELECTOR}, {QR_CODE_SELECTOR}',
                timeout=180000,
            )
        except PlaywrightTimeoutError:
            log.error("Timed out waiting for WhatsApp Web. Check your network.")
            browser.close()
            sys.exit(1)

        qr_visible = page.query_selector(QR_CODE_SELECTOR) is not None
        if qr_visible:
            log.info("QR code detected. Please scan it with your phone.")
            input("  >>> Please scan the QR code then press Enter … ")
            try:
                page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=120000)
                log.info("Authentication successful!")
            except PlaywrightTimeoutError:
                log.error("QR code was not scanned in time. Exiting.")
                browser.close()
                sys.exit(1)
        else:
            log.info("Chat list loaded. Ready to monitor.")

        # --- Main polling loop ---
        log.info("Entering monitoring loop. Press Ctrl+C to stop.")
        try:
            while True:
                log.info("--- Scan cycle starting ---")
                try:
                    new_tasks = scan_page_for_keywords(page, processed)
                    log.info("Scan complete. New tasks created: %d", new_tasks)
                    if new_tasks:
                        save_processed(processed)
                except Exception as exc:
                    log.error("Unexpected error during scan: %s", exc, exc_info=True)

                log.info("Sleeping %d seconds …", CHECK_INTERVAL)
                time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt received. Saving state and exiting …")
            save_processed(processed)
            log.info("Processed fingerprints saved (%d total).", len(processed))

        finally:
            browser.close()
            log.info("Browser connection closed. WhatsApp Watcher stopped.")


if __name__ == "__main__":
    main()
