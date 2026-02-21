# WhatsApp Watcher — Skill Reference

**Skill ID:** whatsapp_watcher
**Version:** 1.0 | **Last Updated:** 2026-02-21
**Script:** `Watchers/whatsapp_watcher.py`
**Task Type Handled:** `whatsapp_message`

---

## What the Watcher Does

`whatsapp_watcher.py` runs a persistent Chromium browser session pointed at
[WhatsApp Web](https://web.whatsapp.com).  Every `WHATSAPP_CHECK_INTERVAL`
seconds it:

1. Scans the chat list for unread conversations.
2. Checks the message preview for any **trigger keyword** (see list below).
3. If a keyword is found, opens the chat and reads the last 10 incoming messages.
4. For each message that matches a keyword it:
   - Computes an **MD5 fingerprint** (chat name + message text + timestamp) to deduplicate.
   - Creates a structured `.md` task file in `Needs_Action/` with YAML front-matter.
   - Records the fingerprint in `Watchers/processed_whatsapp.json` so the same
     message is never actioned twice.
5. Logs every event to both the console **and** `Logs/whatsapp_watcher.log`.

---

## Setup Steps

### 1 — Install dependencies

```bash
pip install playwright python-dotenv
playwright install chromium
```

### 2 — Configure `.env`

Add the variables listed below to `Gold-Tier/AI-Employee-Vault/.env`.
The minimum required additions are:

```env
WHATSAPP_SESSION_PATH=./Watchers/whatsapp_session
WHATSAPP_CHECK_INTERVAL=30
WHATSAPP_HEADLESS=false
```

### 3 — First run: QR code scan

WhatsApp requires a one-time QR code scan to link a device.
Run with `WHATSAPP_HEADLESS=false` (the default) so the browser window appears:

```bash
python Watchers/whatsapp_watcher.py
```

A Chromium window opens and displays the WhatsApp QR code.
On your phone: **WhatsApp → Linked Devices → Link a Device → scan the QR**.

Once authenticated the session is saved to `Watchers/whatsapp_session/`.
The script will detect a saved session on all subsequent runs.

### 4 — Subsequent runs

```bash
# Headless is safe once the session is established
python Watchers/whatsapp_watcher.py
```

Set `WHATSAPP_HEADLESS=true` in `.env` for background / server operation.
The watcher always forces `headless=false` on the very first run (no saved
session) regardless of the env setting, so the QR scan can complete.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `WHATSAPP_SESSION_PATH` | `./Watchers/whatsapp_session` | Directory where Chromium stores the persistent browser profile (cookies, local storage, etc.) |
| `WHATSAPP_CHECK_INTERVAL` | `30` | Seconds between scan cycles |
| `WHATSAPP_HEADLESS` | `false` | Run browser without a visible window. Must be `false` (or absent) for the initial QR scan |
| `NEEDS_ACTION_DIR` | `./Needs_Action` | Where task `.md` files are written |
| `LOGS_DIR` | `./Logs` | Directory for `whatsapp_watcher.log` |

Relative paths are resolved against the vault root automatically.

---

## Trigger Keywords

The watcher flags messages that contain any of the following words
(case-insensitive, whole-word substring match):

| Keyword | Priority Assigned |
|---|---|
| `urgent` | **HIGH** |
| `asap` | **HIGH** |
| `emergency` | **HIGH** |
| `invoice` | MEDIUM |
| `payment` | MEDIUM |
| `help` | MEDIUM |
| `problem` | MEDIUM |
| `issue` | MEDIUM |
| `deadline` | MEDIUM |
| `contract` | MEDIUM |
| `quote` | MEDIUM |

Priority rule: **HIGH** if any of `urgent`, `asap`, or `emergency` is present;
**MEDIUM** for all other trigger keywords.

---

## Task File Format

Each detected message produces a file in `Needs_Action/` named:

```
YYYYMMDD_HHMMSS_WA_{chat_name_slug}_action.md
```

Example: `20260221_143022_WA_John_Smith_action.md`

The file contains YAML front-matter followed by a Markdown body:

```yaml
---
type: whatsapp_message
source: WhatsApp
from: "John Smith"
received: "2026-02-21T14:30:22"
priority: HIGH
status: pending
keywords_matched: [urgent, payment]
---

# WhatsApp Message — Action Required

**From:** John Smith
**Received:** 2026-02-21T14:30:22
**Priority:** HIGH
**Keywords detected:** urgent, payment

## Message

[full message text here]

## Suggested Actions

- [ ] Review message content
- [ ] Draft a reply (save to `Pending_Approval/`)
- [ ] Obtain human approval before sending
- [ ] Move approved reply to `Approved/`
- [ ] Update `status` to `resolved` once sent
```

---

## How Claude Should Process a `whatsapp_message` Task File

When a task file with `type: whatsapp_message` appears in `Needs_Action/`,
follow this 4-step workflow:

### Step 1 — Analyse

Read the task file.  Extract and note:
- **`from`** — who sent the message
- **`priority`** — HIGH or MEDIUM (determines urgency)
- **`keywords_matched`** — what triggered the alert
- **Message body** — what the sender actually wrote

For HIGH priority tasks, treat as P1 (complete within current work session).
For MEDIUM priority tasks, treat as P2 (complete within 24 hours).

### Step 2 — Draft a reply

Write a professional, context-appropriate reply that:
- Acknowledges the message promptly
- Addresses every point raised by the sender
- Matches the conversational tone of WhatsApp
- Requests any missing information needed to resolve the issue

Save the draft to `Pending_Approval/` with the filename:

```
YYYYMMDD_HHMMSS_WA_{chat_name_slug}_reply_draft.md
```

The draft file must include this YAML front-matter:

```yaml
---
type: whatsapp_reply_draft
original_task: "Needs_Action/YYYYMMDD_HHMMSS_WA_..._action.md"
to: "Contact Name"
status: pending_approval
draft_created: "ISO timestamp"
---
```

Mark the draft body clearly: `<!-- REQUIRES HUMAN APPROVAL BEFORE SENDING -->`.

### Step 3 — Human review

A human reviews the draft in `Pending_Approval/`:
- **Approve** — move or copy to `Approved/` (optionally editing first)
- **Reject / revise** — update the file with notes and re-flag for Claude

Claude should not send anything without an item first appearing in `Approved/`.

### Step 4 — Resolve & archive

Once the reply is sent (by a human or a future send-automation layer):

1. Update the original task file's `status` field from `pending` to `resolved`.
2. Move the original task file from `Needs_Action/` to `Done/`.
3. Record the outcome in `Dashboard.md` under "WhatsApp Tasks".

---

## Troubleshooting

### QR code appears but browser closes immediately
Ensure `WHATSAPP_HEADLESS=false` in `.env`.  The watcher exits with an error if
headless mode is active and a QR code is detected.

### Session expires / logged out
WhatsApp occasionally invalidates linked device sessions.
Delete `Watchers/whatsapp_session/` and re-run with `WHATSAPP_HEADLESS=false`:

```bash
rm -rf Watchers/whatsapp_session
python Watchers/whatsapp_watcher.py
```

### No unread chats detected
WhatsApp Web's DOM changes with updates.  Verify the `aria-label="Chat list"`
selector still matches by opening the URL manually in a browser.
Check `Logs/whatsapp_watcher.log` for warnings about selector failures.

### Duplicate task files being created
`processed_whatsapp.json` tracks seen fingerprints.  If the file is deleted, or
if WhatsApp slightly reformats the message text, new fingerprints are generated.
This is intentional conservative behaviour to avoid missing messages.

### High CPU usage
Increase `WHATSAPP_CHECK_INTERVAL` to `60` or `120` seconds.

### Playwright browser not found
```bash
playwright install chromium
```

### ImportError: No module named 'playwright'
```bash
pip install playwright python-dotenv
```

---

## Integration with Other Skills

- **Email Processor Skill** — same Needs_Action → Pending_Approval → Done pipeline
- **Approval Workflow Skill** — review WhatsApp reply drafts in Pending_Approval/
- **Orchestrator Skill** — can prioritise HIGH-priority WhatsApp tasks above routine email

---

**Skill Author:** AI Employee Vault System
**Status:** Active
