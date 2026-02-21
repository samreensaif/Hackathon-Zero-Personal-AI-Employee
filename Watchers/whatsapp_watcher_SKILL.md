# WhatsApp Watcher — Skill Reference

## What the Watcher Does

`whatsapp_watcher.py` runs a persistent browser session pointed at [WhatsApp Web](https://web.whatsapp.com).
Every `WHATSAPP_CHECK_INTERVAL` seconds it:

1. Scans the chat list for unread conversations.
2. Checks the message preview for any **trigger keyword** (see list below).
3. If a keyword is found, opens the chat and reads the last 10 incoming messages.
4. For each message that matches a keyword it:
   - Computes an MD5 fingerprint to deduplicate.
   - Creates a structured `.md` task file in `Needs_Action/`.
   - Records the fingerprint in `Watchers/processed_whatsapp.json` so the same message is never actioned twice.
5. Logs every event to both the console and `Logs/whatsapp_watcher.log`.

---

## Setup Steps

### 1. Install dependencies

```bash
pip install playwright python-dotenv
playwright install chromium
```

### 2. Configure `.env`

Copy the variables below into your project `.env` (see the full list in the section below).

### 3. First run — QR code scan

WhatsApp requires a one-time QR code scan to link a device:

```bash
# Make sure WHATSAPP_HEADLESS=false so the browser window appears
python Watchers/whatsapp_watcher.py
```

A Chromium window will open and display the WhatsApp QR code.
Open WhatsApp on your phone → **Linked Devices** → **Link a Device** → scan the QR.

Once authenticated the session is saved to `Watchers/whatsapp_session/`.
All subsequent runs reuse that session — no re-scan needed.

### 4. Subsequent runs

```bash
# Headless is safe once session is established
python Watchers/whatsapp_watcher.py
```

Or set `WHATSAPP_HEADLESS=true` in `.env` for background / server operation.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WHATSAPP_SESSION_PATH` | `./Watchers/whatsapp_session` | Directory where Chromium stores the persistent browser profile (cookies, local storage, etc.) |
| `WHATSAPP_CHECK_INTERVAL` | `30` | Seconds between scan cycles |
| `WHATSAPP_HEADLESS` | `false` | Run browser without a visible window. Must be `false` for the initial QR scan |
| `NEEDS_ACTION_DIR` | `./Needs_Action` | Where task `.md` files are written |
| `LOGS_DIR` | `./Logs` | Directory for `whatsapp_watcher.log` |

---

## Trigger Keywords

The watcher flags messages that contain any of the following words (case-insensitive):

| Keyword | Priority |
|---|---|
| `urgent` | **HIGH** |
| `asap` | **HIGH** |
| `emergency` | **HIGH** |
| `overdue` | **HIGH** |
| `invoice` | MEDIUM |
| `payment` | MEDIUM |
| `help` | MEDIUM |
| `problem` | MEDIUM |
| `issue` | MEDIUM |
| `important` | MEDIUM |
| `deadline` | MEDIUM |
| `contract` | MEDIUM |
| `quote` | MEDIUM |
| `proposal` | MEDIUM |
| `meeting` | MEDIUM |

---

## Task File Format

Each detected message produces a file in `Needs_Action/` named:

```
YYYYMMDD_HHMMSS_WA_{chat_name_slug}_action.md
```

Example: `20250115_143022_WA_John_Smith_action.md`

The file contains YAML front-matter followed by a Markdown body:

```yaml
---
type: whatsapp_message
source: WhatsApp
from: "John Smith"
received: "2025-01-15T14:30:22"
priority: HIGH
status: needs_action
keywords_matched: [urgent, payment]
---
```

---

## How Claude Should Process a WhatsApp Task File

When Claude receives a task file of `type: whatsapp_message`, the recommended workflow is:

### Step 1 — Analyse
Read the task file. Note:
- **Who** sent the message (`from`)
- **What** they need (message body)
- **Priority** level
- **Keywords** matched

### Step 2 — Draft a reply
Write a professional, context-appropriate reply. Consider:
- Acknowledging the message promptly
- Addressing every point raised
- Matching the tone of the original conversation
- Requesting any missing information needed to resolve the issue

Save the draft to `Pending_Approval/` with the filename:

```
YYYYMMDD_HHMMSS_WA_{chat_name_slug}_reply_draft.md
```

Include a front-matter block:

```yaml
---
type: whatsapp_reply_draft
original_task: "path/to/original_task.md"
to: "Contact Name"
status: pending_approval
draft_created: "ISO timestamp"
---
```

### Step 3 — Human review
A human reviews the draft in `Pending_Approval/`. They either:
- **Approve** — move or copy to `Approved/` (optionally editing first)
- **Reject / revise** — update the file and re-flag for Claude

### Step 4 — Send
Once the file reaches `Approved/`, a human (or a future send-automation layer) copies the reply text into WhatsApp and sends it manually, or a future automation step handles dispatch.

Update the original task file's `status` field to `resolved` after sending.

---

## Troubleshooting

### QR code appears but browser closes immediately
- Ensure `WHATSAPP_HEADLESS=false` in `.env`.
- The watcher exits with an error if headless mode is on and a QR code is detected.

### Session expires / logged out
WhatsApp occasionally invalidates linked device sessions.
Delete `Watchers/whatsapp_session/` and re-run with `WHATSAPP_HEADLESS=false` to scan a fresh QR code.

```bash
rm -rf Watchers/whatsapp_session
python Watchers/whatsapp_watcher.py
```

### No unread chats detected
- WhatsApp Web's DOM changes with updates. Verify the `aria-label="Chat list"` selector still matches in a manual browser session.
- Check `Logs/whatsapp_watcher.log` for warnings about selector failures.

### Duplicate task files being created
`processed_whatsapp.json` tracks seen fingerprints. If the file is deleted or the message text/timestamp changes slightly (e.g. WhatsApp reformats timestamps), new fingerprints are generated and a new task file will be created. This is intentional conservative behaviour.

### High CPU usage
Increase `WHATSAPP_CHECK_INTERVAL` (e.g. `60` or `120` seconds) to reduce scan frequency.

### Playwright browser not found
```bash
playwright install chromium
```

### ImportError: No module named 'playwright'
```bash
pip install playwright python-dotenv
```
