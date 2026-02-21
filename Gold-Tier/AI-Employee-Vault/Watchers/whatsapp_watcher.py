"""
whatsapp_watcher.py
-------------------
Monitors WhatsApp Web for incoming messages containing trigger keywords.
Creates structured task files in Needs_Action/ for each match found.

Usage:
    python Watchers/whatsapp_watcher.py

On first run (no saved session) the browser opens so you can scan the QR code.
Subsequent runs reuse the saved session from WHATSAPP_SESSION_PATH and can
run headless if WHATSAPP_HEADLESS=true is set in .env.

Environment Variables (.env):
    WHATSAPP_SESSION_PATH   Path to store the persistent browser profile
    WHATSAPP_CHECK_INTERVAL Seconds between scan cycles (default: 30)
    WHATSAPP_HEADLESS       true/false — headless after first login (default: false)
    NEEDS_ACTION_DIR        Where task .md files are written
    LOGS_DIR                Directory for whatsapp_watcher.log
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
# Paths & environment
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR.parent.resolve()

load_dotenv(VAULT_ROOT / ".env")


def _resolve(env_key: str, default: Path) -> Path:
    """Read a path from .env; resolve relative paths against VAULT_ROOT."""
    raw = os.getenv(env_key, "")
    if not raw:
        return default
    p = Path(raw)
    return (VAULT_ROOT / p) if not p.is_absolute() else p


SESSION_PATH = _resolve("WHATSAPP_SESSION_PATH", SCRIPT_DIR / "whatsapp_session")
CHECK_INTERVAL = int(os.getenv("WHATSAPP_CHECK_INTERVAL", "30"))
HEADLESS_ENV = os.getenv("WHATSAPP_HEADLESS", "false").strip().lower() == "true"
NEEDS_ACTION_DIR = _resolve("NEEDS_ACTION_DIR", VAULT_ROOT / "Needs_Action")
LOGS_DIR = _resolve("LOGS_DIR", VAULT_ROOT / "Logs")

PROCESSED_PATH = SCRIPT_DIR / "processed_whatsapp.json"

# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

# Exactly the keywords specified in requirements
TRIGGER_KEYWORDS = [
    "urgent", "asap", "invoice", "payment", "help", "problem", "issue",
    "emergency", "deadline", "contract", "quote",
]

HIGH_PRIORITY_KEYWORDS = {"urgent", "asap", "emergency"}

# ---------------------------------------------------------------------------
# WhatsApp Web selectors
# ---------------------------------------------------------------------------

WHATSAPP_URL = "https://web.whatsapp.com"
CHAT_LIST_SELECTOR = 'div[aria-label="Chat list"]'
QR_CODE_SELECTOR = 'canvas[aria-label="Scan this QR code to link a device"]'

# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

NEEDS_ACTION_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
SESSION_PATH.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging — both console and file
# ---------------------------------------------------------------------------

log = logging.getLogger("whatsapp_watcher")
log.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

_fh = logging.FileHandler(LOGS_DIR / "whatsapp_watcher.log", encoding="utf-8")
_fh.setFormatter(_fmt)
log.addHandler(_fh)

_ch = logging.StreamHandler(sys.stdout)
_ch.setFormatter(_fmt)
log.addHandler(_ch)

# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def is_first_run() -> bool:
    """Return True when no persisted Chromium session exists yet."""
    return not (SESSION_PATH / "Default" / "Cookies").exists()


# ---------------------------------------------------------------------------
# Processed-fingerprints store
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
    """MD5 fingerprint used to deduplicate messages."""
    raw = f"{chat_name}{text[:200]}{timestamp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Keyword helpers
# ---------------------------------------------------------------------------


def detect_keywords(text: str) -> list[str]:
    """Return trigger keywords found in *text* (case-insensitive)."""
    lower = text.lower()
    return [kw for kw in TRIGGER_KEYWORDS if kw in lower]


def determine_priority(keywords: list[str]) -> str:
    """HIGH for urgent/asap/emergency, MEDIUM for everything else."""
    if any(kw in HIGH_PRIORITY_KEYWORDS for kw in keywords):
        return "HIGH"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Task file creation
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:40]


def create_task_file(
    chat_name: str,
    message_text: str,
    timestamp: str,
    keywords: list[str],
) -> Path:
    """Write a Markdown task file with YAML front-matter to Needs_Action/."""
    now = datetime.now()
    date_str = now.strftime("%Y%m%d_%H%M%S")
    slug = _slugify(chat_name)
    filename = f"{date_str}_WA_{slug}_action.md"
    filepath = NEEDS_ACTION_DIR / filename

    priority = determine_priority(keywords)
    received_iso = timestamp if timestamp else now.isoformat()

    # Escape quotes in the preview so the YAML block remains valid
    preview = message_text[:500].replace('"', '\\"').replace("\n", " ")

    content = f"""---
type: whatsapp_message
source: WhatsApp
from: "{chat_name}"
received: "{received_iso}"
priority: {priority}
status: pending
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
- [ ] Obtain human approval before sending
- [ ] Move approved reply to `Approved/`
- [ ] Update `status` to `resolved` once sent

---
*Auto-generated by whatsapp_watcher.py at {now.strftime("%Y-%m-%d %H:%M:%S")}*
"""
    filepath.write_text(content, encoding="utf-8")
    log.info("Task file created: %s", filepath.name)
    return filepath


# ---------------------------------------------------------------------------
# WhatsApp Web scraping helpers
# ---------------------------------------------------------------------------


def get_unread_chats(page) -> list:
    """Return chat list elements that carry an unread indicator."""
    try:
        return page.query_selector_all('[aria-label*="unread"]')
    except Exception as exc:
        log.warning("Error querying unread chats: %s", exc)
        return []


def get_message_preview(chat_element) -> str:
    """Extract the text preview from a chat list item."""
    try:
        preview_el = (
            chat_element.query_selector('span[class*="last-msg"]')
            or chat_element.query_selector('span[dir="ltr"]')
        )
        if preview_el:
            return preview_el.inner_text().strip()
    except Exception:
        pass
    try:
        return chat_element.inner_text().strip()
    except Exception:
        return ""


def get_chat_name(chat_element) -> str:
    """Extract the contact/group name from a chat list item."""
    try:
        name_el = (
            chat_element.query_selector('span[dir="auto"][title]')
            or chat_element.query_selector('span[title]')
        )
        if name_el:
            title = name_el.get_attribute("title")
            if title:
                return title.strip()
            return name_el.inner_text().strip()
    except Exception:
        pass
    try:
        label = chat_element.get_attribute("aria-label") or ""
        # aria-label is often "N unread messages from Name"
        m = re.search(r"from (.+?)(?:,|$)", label, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return label.strip()
    except Exception:
        return "Unknown"


def scrape_incoming_messages(page, max_messages: int = 10) -> list[dict]:
    """
    Scrape the last *max_messages* incoming messages from the currently-open chat.
    Returns a list of dicts: {text: str, timestamp: str}.
    """
    messages = []
    try:
        page.wait_for_selector('div[class*="message-in"]', timeout=5000)
    except PlaywrightTimeoutError:
        log.debug("No incoming messages visible or selector timed out.")
        return messages

    try:
        msg_elements = page.query_selector_all('div[class*="message-in"]')
        msg_elements = msg_elements[-max_messages:]

        for el in msg_elements:
            text = ""
            timestamp = ""

            # --- Message text ---
            try:
                text_el = el.query_selector("span.selectable-text") or el.query_selector(
                    'span[class*="copyable-text"]'
                )
                text = text_el.inner_text().strip() if text_el else el.inner_text().strip()
            except Exception:
                text = el.inner_text().strip() if el else ""

            # --- Timestamp from data-pre-plain-text attribute ---
            try:
                copyable = el.query_selector("[data-pre-plain-text]")
                if copyable:
                    pre_text = copyable.get_attribute("data-pre-plain-text") or ""
                    ts_match = re.search(r"\[(.+?)\]", pre_text)
                    if ts_match:
                        timestamp = ts_match.group(1)
            except Exception:
                pass

            # --- Fallback: msg-meta span ---
            if not timestamp:
                try:
                    time_el = el.query_selector(
                        'span[data-testid="msg-meta"]'
                    ) or el.query_selector('span[class*="message-time"]')
                    if time_el:
                        timestamp = time_el.inner_text().strip()
                except Exception:
                    pass

            if not timestamp:
                timestamp = datetime.now().isoformat()

            if text:
                messages.append({"text": text, "timestamp": timestamp})

    except Exception as exc:
        log.warning("Error scraping messages: %s", exc)

    return messages


def click_chat(page, chat_element) -> bool:
    """Click on a chat element to open it. Returns True on success."""
    try:
        chat_element.click()
        page.wait_for_load_state("domcontentloaded", timeout=5000)
        time.sleep(1)
        return True
    except Exception as exc:
        log.warning("Could not click chat: %s", exc)
        return False


# ---------------------------------------------------------------------------
# One full scan cycle
# ---------------------------------------------------------------------------


def scan_for_messages(page, processed: dict) -> int:
    """
    Scan all unread chats for trigger keywords.
    Opens matching chats, reads last 10 incoming messages, creates task files.
    Returns the number of new task files created.
    """
    new_tasks = 0
    unread_chats = get_unread_chats(page)
    log.info("Found %d unread chat(s).", len(unread_chats))

    for chat_el in unread_chats:
        chat_name = get_chat_name(chat_el)
        preview = get_message_preview(chat_el)

        log.debug("Checking chat '%s' — preview: %s", chat_name, preview[:80])

        # Quick filter on the chat-list preview before opening the chat
        preview_keywords = detect_keywords(preview)
        if not preview_keywords:
            log.debug("No trigger keywords in preview for '%s', skipping.", chat_name)
            continue

        log.info(
            "Keyword(s) %s found in preview from '%s'. Opening chat.",
            preview_keywords,
            chat_name,
        )

        if not click_chat(page, chat_el):
            continue

        messages = scrape_incoming_messages(page, max_messages=10)
        log.debug("Scraped %d incoming message(s) from '%s'.", len(messages), chat_name)

        for msg in messages:
            text = msg["text"]
            timestamp = msg["timestamp"]
            keywords = detect_keywords(text)

            if not keywords:
                continue

            fingerprint = make_fingerprint(chat_name, text, timestamp)
            if fingerprint in processed:
                log.debug("Duplicate fingerprint %s — skipping.", fingerprint)
                continue

            log.info(
                "New actionable message from '%s' [keywords: %s] — creating task.",
                chat_name,
                keywords,
            )
            create_task_file(chat_name, text, timestamp, keywords)
            processed[fingerprint] = {
                "chat": chat_name,
                "timestamp": timestamp,
                "keywords": keywords,
                "processed_at": datetime.now().isoformat(),
            }
            new_tasks += 1

        # Navigate back to the chat list
        try:
            back_btn = page.query_selector(
                'button[aria-label="Back"]'
            ) or page.query_selector('[data-testid="back"]')
            if back_btn:
                back_btn.click()
                time.sleep(0.5)
        except Exception:
            pass

    return new_tasks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    first_run = is_first_run()

    # On first run always open the browser so the user can scan the QR code.
    # On subsequent runs, respect the WHATSAPP_HEADLESS env variable.
    headless = HEADLESS_ENV and not first_run

    log.info("=" * 60)
    log.info("WhatsApp Watcher starting up.")
    log.info("Session path  : %s", SESSION_PATH.resolve())
    log.info("Needs_Action  : %s", NEEDS_ACTION_DIR.resolve())
    log.info("Logs          : %s", LOGS_DIR.resolve())
    log.info("Check interval: %d seconds", CHECK_INTERVAL)
    log.info("Headless mode : %s (first_run=%s)", headless, first_run)
    log.info("Keywords      : %s", TRIGGER_KEYWORDS)
    log.info("=" * 60)

    if first_run:
        log.info("First run detected — browser will open for QR code scanning.")
        log.info("Open WhatsApp → Linked Devices → Link a Device and scan the QR.")

    processed = load_processed()
    log.info("Loaded %d previously processed fingerprints.", len(processed))

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_PATH.resolve()),
            headless=headless,
            args=["--no-sandbox"],
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})

        log.info("Navigating to %s …", WHATSAPP_URL)
        try:
            page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:
            log.error("Failed to load WhatsApp Web: %s", exc)
            browser.close()
            sys.exit(1)

        # Wait for either the chat list or the QR code canvas to appear
        log.info("Waiting for WhatsApp Web to initialise …")
        try:
            page.wait_for_selector(
                f"{CHAT_LIST_SELECTOR}, {QR_CODE_SELECTOR}",
                timeout=90000,
            )
        except PlaywrightTimeoutError:
            log.error("Timed out waiting for WhatsApp Web. Check your network.")
            browser.close()
            sys.exit(1)

        qr_visible = page.query_selector(QR_CODE_SELECTOR) is not None
        if qr_visible:
            if headless:
                log.error(
                    "QR code visible but running headless — cannot scan. "
                    "Delete '%s' and rerun with WHATSAPP_HEADLESS=false.",
                    SESSION_PATH,
                )
                browser.close()
                sys.exit(1)
            log.info("QR code detected. Waiting up to 120 seconds for scan …")
            try:
                page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=120000)
                log.info("Authentication successful! Session saved to %s", SESSION_PATH)
            except PlaywrightTimeoutError:
                log.error("QR code was not scanned in time. Exiting.")
                browser.close()
                sys.exit(1)
        else:
            log.info("Existing session found. Chat list loaded.")

        log.info("Entering monitoring loop. Press Ctrl+C to stop.")
        try:
            while True:
                log.info("--- Scan cycle starting (%s) ---", datetime.now().strftime("%H:%M:%S"))
                try:
                    new_tasks = scan_for_messages(page, processed)
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
            log.info("Browser closed. WhatsApp Watcher stopped.")


if __name__ == "__main__":
    main()
