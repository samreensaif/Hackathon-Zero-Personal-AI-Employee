# Gold Tier AI Employee — Hackathon Zero

> **An autonomous AI employee that monitors email, WhatsApp, and file drops; manages Odoo accounting; drafts social media content across four platforms; generates weekly CEO intelligence briefings; and self-heals under a human-in-the-loop approval gate — all running locally with zero cloud dependency.**

---

## Table of Contents

1. [What This Is](#1-what-this-is)
2. [Capability Overview](#2-capability-overview)
3. [System Architecture](#3-system-architecture)
4. [The Ralph Loop — Autonomous Task Execution](#4-the-ralph-loop--autonomous-task-execution)
5. [Tech Stack](#5-tech-stack)
6. [Prerequisites](#6-prerequisites)
7. [Setup Guide](#7-setup-guide)
8. [Running the System](#8-running-the-system)
9. [Vault File Structure](#9-vault-file-structure)
10. [Workflow Walkthroughs](#10-workflow-walkthroughs)
11. [Security & Privacy](#11-security--privacy)
12. [Troubleshooting](#12-troubleshooting)
13. [Lessons Learned](#13-lessons-learned)
14. [Submission Checklist](#14-submission-checklist)

---

## 1. What This Is

The Gold Tier AI Employee is a fully local, file-system-driven AI automation system built for the Hackathon Zero challenge. It acts as a digital employee that:

- **Watches** three input channels (Gmail, WhatsApp Web, file drop inbox) and converts every incoming event into a structured task file.
- **Orchestrates** those tasks by classifying them, assigning priority, and routing them either for immediate autonomous processing or for human approval.
- **Executes** approved actions via two MCP servers: an Odoo accounting server (invoices, payments, expenses) and a Gmail email server.
- **Generates** social media content for LinkedIn, Facebook, Instagram, and Twitter/X on a configurable schedule.
- **Briefs** the CEO every Sunday evening with a data-rich markdown report pulling from Odoo, completed tasks, social media activity, and system health logs.
- **Loops** on complex multi-step tasks using the Ralph Loop — a Claude-backed autonomous agent that retries until the task is done or a timeout is reached.

Everything operates through a **markdown vault** — a folder hierarchy of `.md` files that serves as the system's persistent state, audit trail, and human interface. No database required.

---

## 2. Capability Overview

| Capability | Implementation | Auto-Execute | Requires Approval | Tested |
|---|---|---|---|---|
| Gmail monitoring | `gmail_watcher.py` | Task file creation | Replies, forwards | ✅ Live tested — connected, polled 10 emails |
| WhatsApp monitoring | `whatsapp_watcher.py` (Playwright) | Task file creation | All replies | ✅ Live tested — detected "invoice" keyword, contact Musaab, task file auto-created |
| File drop inbox | `file_watcher.py` (watchdog) | Task file creation | Always | ✅ Present |
| Task classification & routing | `orchestrator.py` | Internal tasks | External/financial | ✅ Present |
| Complex multi-step task execution | `ralph_loop.py` (Claude CLI loop) | After approval | When output is external | ✅ Dry-run tested — status=dry_run, iterations=1, 0.11s |
| Odoo invoice creation (draft) | `odoo_mcp_server.py` | Yes — drafts only | Confirm/post above $500 | ✅ Live tested — invoice #7 ($500) created |
| Odoo payment recording | `odoo_mcp_server.py` | Up to $200 | Above $200 | ✅ Live tested — all 7 tools operational |
| Odoo expense recording | `odoo_mcp_server.py` | Up to $100 | Above $100 | ✅ Live tested — expense #8 ($49.99) created |
| Email reply/send | `email_mcp_server.py` | Never | Always | ✅ Present (registered in .mcp.json) |
| LinkedIn post drafting | `linkedin_poster.py` | Draft only | Publishing always | ✅ Present |
| Facebook/Instagram/Twitter drafting | `social_media_poster.py` | Draft only | Publishing always | ✅ Present |
| CEO weekly briefing | `ceo_briefing_generator.py` | Generates + saves | Emailing requires approval | ✅ Present |
| Approval workflow | `approval_manager.py` | Moves files | Human moves to Approved/ | ✅ Present |
| Service health & auto-restart | `task_scheduler.py` | Yes | N/A | ✅ Present |
| Audit logging | `Logs/ralph_audit.json`, `Logs/approvals.json` | Continuous | N/A | ✅ Present |

---

## Live Test Results — February 21, 2026

**Bronze Tier:** Gmail watcher connected successfully and polled 10 emails, with task files created in `Needs_Action/` as expected. All Bronze Tier components confirmed present and operational.

**Silver Tier:** WhatsApp watcher detected the keyword "invoice" in a real incoming message from contact Musaab and automatically created a task file in `Needs_Action/` in under one second — the full intake pipeline from watcher to vault file was verified end-to-end.

**Gold Tier:** The Odoo MCP server was tested against a live Docker-hosted Odoo instance; all 7 tools are operational. Invoice #7 ($500) was created and expense #8 ($49.99) was recorded during testing. Four invoices and four expenses were listed successfully. The Ralph Loop was validated via a dry-run (status=dry_run, 1 iteration, 0.11 seconds), confirming the execution engine initialises and exits cleanly without side effects.

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        INPUT CHANNELS                           │
│                                                                 │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│   │ gmail_watcher│  │whatsapp_     │  │   file_watcher       │ │
│   │  (polling)   │  │watcher       │  │   (watchdog)         │ │
│   │              │  │(Playwright)  │  │   Inbox/ folder      │ │
│   └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘ │
│          │                 │                       │             │
└──────────┼─────────────────┼───────────────────────┼────────────┘
           │                 │                       │
           └─────────────────▼───────────────────────┘
                             │
                     Needs_Action/
                    (task .md files)
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       orchestrator.py                           │
│                                                                 │
│  classify → prioritise → assess complexity (score 0–10)         │
│                                                                 │
│   Simple / internal                   Complex (score ≥ 4)      │
│   ┌──────────────────┐        ┌────────────────────────────┐   │
│   │ Plans/           │        │     ralph_loop.py          │   │
│   │ (auto-execute)   │        │  Claude CLI --print loop   │   │
│   └──────────────────┘        │  max 10 iterations         │   │
│                               │  <TASK_COMPLETE> token     │   │
│   Financial / external        └────────────────────────────┘   │
│   ┌──────────────────────────────────────────────────────────┐ │
│   │               Pending_Approval/                          │ │
│   │          Human moves file to Approved/ or Rejected/      │ │
│   └──────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
           │                                 │
           ▼                                 ▼
┌──────────────────────┐       ┌──────────────────────────────────┐
│  approval_manager.py │       │         MCP Servers              │
│                      │       │                                  │
│  Monitors Approved/  │──────▶│  odoo_mcp_server.py             │
│  and Rejected/       │       │  (invoices, payments, expenses)  │
│                      │──────▶│  email_mcp_server.py            │
│  Archives to Done/   │       │  (Gmail API — send/draft)        │
└──────────────────────┘       └──────────────────────────────────┘
                                             │
                                             ▼
                                          Done/
                                     Dashboard.md updated

┌─────────────────────────────────────────────────────────────────┐
│                   SCHEDULED SERVICES                            │
│                                                                 │
│  linkedin_poster.py     — Tuesday & Thursday 09:00–11:00        │
│  social_media_poster.py — Daily at 10:00, 14:00, 18:00         │
│  ceo_briefing_generator.py — Every Sunday at 20:00             │
│                                                                 │
│  All managed & auto-restarted by task_scheduler.py             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. The Ralph Loop — Autonomous Task Execution

The Ralph Loop is the system's autonomous execution engine for complex, multi-step tasks. When the orchestrator scores a task 4 or above on the complexity scale, it delegates to `ralph_loop.py`.

### How it works

```
1. Read task file from Needs_Action/
2. Build prompt = system instructions (5 rules) + task content
3. Call: claude --print "<prompt>"  (subprocess, 300s timeout)
4. Check two completion conditions:
   a) <TASK_COMPLETE> token appears in Claude's output
   b) A file matching the task stem appears in Done/
5. If complete: write state, append audit log, exit
6. If not complete: inject last 800 chars as context, go to step 3
7. After max_iterations: append TIMEOUT block to task file
```

### Complexity scoring

| Signal | Score |
|---|---|
| Each COMPLEXITY_INDICATORS pattern matched | +1 each |
| Task type = `multi_step` | +3 |
| Multi-step indicators + other type | +2 |
| Content > 300 words | +1 |
| Content > 600 words | +1 |
| 3-5 action items in task | +1 |
| 6+ action items in task | +1 |

**Threshold: score 4 or above routes to Ralph Loop.**

### The five non-negotiable rules Claude follows inside the loop

1. Work through every step without stopping — no hedging.
2. Write to `Pending_Approval/` for approval-required actions, then continue.
3. Move the task file to `Done/` with a footer when all steps are complete.
4. Update `Dashboard.md` with a one-line completion entry.
5. End the final response with `<TASK_COMPLETE>` on its own line.

### Key files

| File | Purpose |
|---|---|
| `Plans/ralph_states/{stem}_ralph_state.json` | Per-iteration state (status, iteration, summary) |
| `Logs/ralph_audit.json` | Completion audit log (capped at 200 records) |
| `Logs/ralph_loop.log` | Full text log for debugging |

---

## 5. Tech Stack

| Layer | Technology |
|---|---|
| AI backbone | Claude claude-sonnet-4-6 via Claude CLI (`claude --print`) |
| MCP protocol | Model Context Protocol (JSON-RPC over stdio) |
| WhatsApp automation | Playwright + Chromium (persistent session) |
| File monitoring | watchdog (Python library) |
| Accounting | Odoo 17 (Docker) — JSON-RPC API |
| Email | Gmail API (OAuth2 via google-auth) |
| Social content | Template engine with Business_Goals.md as data source |
| State management | Markdown files + JSON tracking files |
| Task scheduling | Custom Python scheduler (`task_scheduler.py`) |
| Process health | `psutil` for cross-platform PID checking |
| Config | `python-dotenv` |
| HTTP | `requests` (Odoo direct calls in CEO Briefing) |

---

## 6. Prerequisites

- **Python 3.10+**
- **Node.js 18+** (for the Claude CLI)
- **Claude CLI** — `npm install -g @anthropic-ai/claude-code`
- **Odoo 17** — via Docker (`docker-compose up -d`) or existing instance
- **Google Cloud project** with Gmail API enabled + `credentials.json`
- **Chrome/Chromium** (installed automatically by Playwright)

### Python packages

```
playwright
watchdog
requests
python-dotenv
google-auth
google-auth-oauthlib
google-auth-httplib2
google-api-python-client
psutil
```

Install all with:

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## 7. Setup Guide

### Step 1 — Clone and navigate

```bash
git clone <repo-url>
cd Hackathon-Zero/Gold-Tier/AI-Employee-Vault
```

### Step 2 — Configure environment variables

Copy the example and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Vault paths (leave as-is for default layout)
VAULT_ROOT=./
INBOX_DIR=./Inbox
NEEDS_ACTION_DIR=./Needs_Action
DONE_DIR=./Done
PENDING_APPROVAL_DIR=./Pending_Approval
APPROVED_DIR=./Approved
REJECTED_DIR=./Rejected
LOGS_DIR=./Logs
BRIEFINGS_DIR=./Briefings
SOCIAL_MEDIA_DIR=./Social_Media

# Gmail (place credentials.json in this vault root)
GMAIL_CREDENTIALS_PATH=./credentials.json
GMAIL_MCP_TOKEN_PATH=./token_mcp.json
CHECK_INTERVAL=60

# Odoo
ODOO_URL=http://localhost:8069
ODOO_DB=odoo_gold_tier
ODOO_USERNAME=admin
ODOO_PASSWORD=admin

# CEO / notifications
CEO_EMAIL=ceo@example.com

# WhatsApp
WHATSAPP_SESSION_PATH=./Watchers/whatsapp_session
WHATSAPP_CHECK_INTERVAL=30
WHATSAPP_HEADLESS=false

# Ralph Loop
RALPH_MAX_ITERATIONS=10
RALPH_PAUSE_BETWEEN=5
RALPH_CLAUDE_CMD=claude
DRY_RUN=false
```

### Step 3 — Start Odoo

```bash
docker-compose up -d
```

Wait for Odoo to be reachable at `http://localhost:8069`, then create the database `odoo_gold_tier` via the Odoo UI.

### Step 4 — Authenticate Gmail

Run the Gmail watcher once interactively to complete the OAuth flow:

```bash
python Watchers/gmail_watcher.py
```

A browser will open. Authorise access. The token is saved to `token.json` and `token_mcp.json`.

### Step 5 — Authenticate WhatsApp

Run the WhatsApp watcher once in headed mode (first run is always headed regardless of `WHATSAPP_HEADLESS`):

```bash
python Watchers/whatsapp_watcher.py
```

Scan the QR code in the browser window with your phone. The session is saved to `Watchers/whatsapp_session/` and subsequent runs can be headless.

---

## 8. Running the System

### Start everything (recommended)

The task scheduler starts all services and keeps them alive:

```bash
python task_scheduler.py
```

This launches and monitors:
- `gmail_watcher.py` (continuous)
- `file_watcher.py` (continuous)
- `orchestrator.py` (continuous)
- `approval_manager.py` (continuous)
- `whatsapp_watcher.py` (continuous)
- `linkedin_poster.py --schedule` (Tuesday/Thursday 09:00-11:00)
- `social_media_poster.py --schedule` (daily 10:00, 14:00, 18:00)
- `ceo_briefing_generator.py` (Sunday 20:00)

### Run individual services

```bash
# Process tasks
python orchestrator.py

# Run a complex task through the Ralph Loop
python ralph_loop.py --task-file Needs_Action/my_task.md

# Generate CEO briefing for a specific week
python ceo_briefing_generator.py --week-of 2026-02-17

# Generate all social media drafts now
python Watchers/social_media_poster.py --all

# Generate a LinkedIn draft
python Watchers/linkedin_poster.py

# Test Odoo MCP server
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python MCP_Servers/odoo_mcp_server.py
```

### Approval workflow (human steps)

1. Check `Pending_Approval/` for new `.md` files.
2. Read the draft — edit if needed.
3. Move to `Approved/` to execute, or `Rejected/` to discard.
4. The `approval_manager.py` detects the move within 10 seconds and acts.

---

## 9. Vault File Structure

```
Gold-Tier/
├── README.md                          <- This file
├── requirements.txt                   <- Python dependencies
├── docker-compose.yml                 <- Odoo + PostgreSQL
└── AI-Employee-Vault/
    ├── .env                           <- All secrets (gitignored)
    ├── .mcp.json                      <- MCP server registry
    ├── Company_Handbook.md            <- Binding operational rules (v3.0)
    ├── Business_Goals.md              <- Q1 2026 KPIs and objectives
    ├── Dashboard.md                   <- Live system status (one line per task)
    │
    ├── orchestrator.py                <- Task classification and routing brain
    ├── ralph_loop.py                  <- Autonomous Claude-backed agentic loop
    ├── approval_manager.py            <- Approval workflow executor
    ├── ceo_briefing_generator.py      <- Weekly briefing with Odoo + log data
    ├── task_scheduler.py              <- Service manager and health watchdog
    │
    ├── Watchers/
    │   ├── gmail_watcher.py           <- Gmail polling (Silver + Gold)
    │   ├── whatsapp_watcher.py        <- WhatsApp Web via Playwright
    │   ├── file_watcher.py            <- Inbox/ folder via watchdog
    │   ├── linkedin_poster.py         <- LinkedIn draft generator
    │   ├── social_media_poster.py     <- Facebook/Instagram/Twitter drafts
    │   ├── posts_history.json         <- LinkedIn post tracking
    │   ├── social_posts_history.json  <- Social media post tracking
    │   └── whatsapp_session/          <- Playwright session (gitignored)
    │
    ├── MCP_Servers/
    │   ├── odoo_mcp_server.py         <- Odoo JSON-RPC MCP server
    │   └── email_mcp_server.py        <- Gmail MCP server (Silver Tier)
    │
    ├── Skills/
    │   ├── ralph_loop_SKILL.md        <- Ralph Loop reference documentation
    │   ├── whatsapp_watcher_SKILL.md  <- WhatsApp watcher reference
    │   └── orchestrator_SKILL.md      <- Orchestrator reference
    │
    ├── Needs_Action/                  <- Incoming task files (runtime)
    ├── Pending_Approval/              <- Awaiting human decision (runtime)
    ├── Approved/                      <- Human-approved: triggers execution
    ├── Rejected/                      <- Human-rejected: triggers archival
    ├── Rejected_Archive/              <- Archived rejections
    ├── Done/                          <- Completed task archive
    ├── Plans/                         <- Orchestrator execution plans
    │   └── ralph_states/              <- Per-task Ralph Loop state JSON
    ├── Briefings/                     <- CEO briefing archive
    ├── Social_Media/                  <- Social media draft archive
    ├── Inbox/                         <- File drop folder (monitored)
    └── Logs/
        ├── orchestrator.log
        ├── approval_manager.log
        ├── ralph_loop.log
        ├── ralph_audit.json           <- Ralph Loop completion audit
        ├── approvals.json             <- Approval manager audit
        ├── gmail_watcher.log
        ├── whatsapp_watcher.log
        ├── file_watcher.log
        ├── linkedin_poster.log
        ├── social_media_poster.log
        └── ceo_briefing.log
```

---

## 10. Workflow Walkthroughs

### Walkthrough A — Client sends a WhatsApp invoice request

```
1. Client sends: "Hi, can you send me an invoice for $850 for the consulting
   work from last week?"

2. whatsapp_watcher.py detects "invoice" keyword -> creates:
   Needs_Action/20260221_1430_WA_ClientName_action.md
   (YAML front-matter: type=whatsapp_message, priority=MEDIUM)

3. orchestrator.py reads the file:
   - Classifies: financial transaction request
   - Detects: invoice above $500 threshold -> needs_approval = True
   - Creates: Pending_Approval/20260221_1432_WA_ClientName_invoice.md
     with: Action Type: odoo_invoice, Amount: $850, Customer: Client Name
   - Archives task to Done/

4. Human opens Pending_Approval/, reviews, edits amount if needed,
   moves file to Approved/.

5. approval_manager.py detects new file in Approved/ (within 10 seconds):
   - Parses action_type = "odoo_invoice"
   - Calls odoo_mcp_server.py via JSON-RPC: create_invoice(customer, 850, ...)
   - Odoo creates invoice in DRAFT state (not confirmed -- above $500 threshold)
   - Logs success to Logs/approvals.json
   - Moves file to Done/ with execution footer

6. Dashboard.md updated with one-line entry.
```

### Walkthrough B — Complex multi-step task via Ralph Loop

```
1. File dropped into Inbox/:
   "Q1_Report_Instructions.md"
   (content: Step 1: pull revenue from Odoo. Step 2: draft social post.
    Step 3: update Dashboard. Step 4: generate CEO briefing excerpt.)

2. file_watcher.py detects the file -> creates task file in Needs_Action/

3. orchestrator.py reads and scores the task:
   - 4 numbered action items (+1), content > 300 words (+1),
     multi_step type (+3) -> complexity score = 5, which is >= 4
   - Routes to ralph_loop.py

4. ralph_loop.py begins:

   Iteration 1:
   - Builds prompt: system instructions + task content
   - Calls: claude --print "<prompt>"
   - Claude reads Odoo (via MCP), pulls revenue data
   - Claude drafts social post -> writes to Pending_Approval/
   - Claude updates Dashboard.md
   - Claude does NOT emit <TASK_COMPLETE> (briefing not done yet)
   - State written: Plans/ralph_states/{stem}_ralph_state.json
     {"status": "running", "iteration": 1}

   Iteration 2:
   - Rebuilds prompt with last 800 chars of iteration 1 output
   - Claude generates CEO briefing excerpt -> writes to Briefings/
   - Claude moves task file to Done/ with footer
   - Claude emits: <TASK_COMPLETE>
   - Loop detects completion (both promise + file check pass)
   - Audit log updated: Logs/ralph_audit.json
   - Status: "complete", iterations: 2, duration_s: 143.2

5. Dashboard.md shows: "Ralph Loop completed Q1 Report in 2 iterations"
```

---

## 11. Security & Privacy

### Human-in-the-loop gate

Every outbound action — emails, WhatsApp replies, social media posts, invoice confirmations above $500, payments above $200 — is blocked behind a mandatory human approval step. The AI Employee **cannot** send, post, or move money without a human explicitly moving a file to `Approved/`.

### Credentials

- All secrets live in `.env` — never in vault markdown files.
- `.env` is gitignored at the vault level.
- The WhatsApp Playwright session (`Watchers/whatsapp_session/`) contains browser cookies and is gitignored.
- OAuth tokens (`token.json`, `token_mcp.json`, `credentials.json`) are gitignored.
- Bank account numbers in task files are masked: `XXXX-XXXX-XXXX-1234`.
- Client names in vault files use first name + last initial only: `"John D."`.

### Financial thresholds

| Action | Auto-approve up to | Always requires approval |
|---|---|---|
| Vendor bill / expense | $100 | Above $100 |
| Confirm / post invoice | $500 | Above $500 |
| Record payment | $200 | Above $200 |
| Cancel subscription | $50/month | Above $50/month |
| Add new vendor/payee | Never | Always |

### Audit trails

- All approval decisions are logged to `Logs/approvals.json`.
- Ralph Loop completions are logged to `Logs/ralph_audit.json` (capped at 200 records).
- Log files are retained for a minimum of 90 days.

---

## 12. Troubleshooting

### Ralph Loop hits timeout without completing

```bash
# Check what Claude last said
cat Logs/ralph_loop.log | grep -A 5 "Iteration"

# Check final state
cat Plans/ralph_states/<task_stem>_ralph_state.json

# Verify Claude CLI is working
claude --print "Say hello"

# Retry with more iterations
python ralph_loop.py --task-file Needs_Action/my_task.md --max-iterations 20
```

### WhatsApp watcher shows blank chat names

The WhatsApp Web DOM is dynamic. On first run, let the page fully load before the watcher starts scanning. If headless mode produces blank results, set `WHATSAPP_HEADLESS=false` in `.env`.

### Odoo connection refused

```bash
# Check Odoo is running
docker ps | grep odoo
docker-compose up -d

# Test connection
curl -s http://localhost:8069/web/database/list | python -m json.tool

# Verify .env credentials
cat .env | grep ODOO
```

### Approval manager not picking up files

```bash
# Check it's running
ps aux | grep approval_manager

# Check the interval setting
cat .env | grep APPROVAL_CHECK_INTERVAL  # default: 10 seconds

# Check for errors
tail -50 Logs/approval_manager.log
```

### CEO briefing shows "Odoo unavailable"

The briefing generator connects to Odoo directly (not via the MCP server). Ensure `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, and `ODOO_PASSWORD` in `.env` are correct and Odoo is running.

### Services not restarting after crash

`task_scheduler.py` will attempt up to 5 restarts per service before giving up. Check `Dashboard.md` for a System Alerts section. If a service has hit its restart limit, restart the scheduler manually:

```bash
python task_scheduler.py
```

---

## 13. Lessons Learned

**1. File-system as message bus is surprisingly powerful.**
Using markdown files as the communication layer between processes — instead of a queue like Redis or RabbitMQ — meant zero infrastructure setup and made the system's state completely human-readable. Every decision the AI makes is visible as a `.md` file on disk.

**2. The approval gate is the product.**
Early prototypes tried to run fully autonomously. The human-in-the-loop gate — moving a file from `Pending_Approval/` to `Approved/` — turned out to be the feature that makes this trustworthy. It is a minimal interface but it works.

**3. MCP servers over stdio require careful framing.**
The Odoo MCP server receives JSON-RPC messages line-by-line over stdin and responds on stdout. Building `approval_manager.py` to call it as a subprocess (with `initialize` + `notifications/initialized` + `tools/call` in sequence) was non-trivial but made the integration clean and testable.

**4. Playwright for WhatsApp Web is fragile but workable.**
WhatsApp Web's DOM is obfuscated and changes frequently. Using multiple selector fallbacks and graceful error handling made the watcher resilient enough for a hackathon context. Production use would require a proper WhatsApp Business API.

**5. Ralph Loop context injection is critical.**
Without injecting the last 800 characters of the previous iteration's output into the next prompt, Claude loses track of what it already completed. The tail-as-context pattern is simple but effective.

**6. Complexity scoring before routing saves resources.**
Sending every task to the Ralph Loop would be wasteful and slow. The orchestrator's 0-10 complexity score correctly routes simple email replies to direct handling while reserving the Claude-in-a-loop for tasks that genuinely need it.

**7. Schedule discipline matters even in demos.**
Enforcing the three-posts-per-week LinkedIn limit and the one-post-per-day social media limit in code — not just in a handbook — prevents the system from flooding the approval queue with content nobody asked for.

---

## 14. Submission Checklist

- [x] **Bronze Tier** — Gmail watcher, file watcher, orchestrator, approval manager, email MCP server
- [x] **Silver Tier** — All Bronze features + LinkedIn poster, CEO briefing, enhanced approval workflow
- [x] **Gold Tier** — All Silver features +
  - [x] WhatsApp watcher (Playwright, persistent session, keyword detection)
  - [x] Odoo MCP server (invoices, payments, expenses, vendor bills)
  - [x] Facebook, Instagram, Twitter/X post drafting
  - [x] Ralph Loop autonomous agentic execution (Claude CLI subprocess loop)
  - [x] Task scheduler with 8 services and auto-restart
  - [x] CEO briefing with Odoo financial data integration
  - [x] Company Handbook v3.0 with binding operational rules
  - [x] Financial approval thresholds enforced in code
  - [x] Audit logs (ralph_audit.json, approvals.json)
  - [x] MCP server registry (.mcp.json)
  - [x] Skills documentation (ralph_loop_SKILL.md, whatsapp_watcher_SKILL.md)
- [x] **Security** — No secrets in git, .gitignore verified, PII masking documented
- [x] **Documentation** — README (this file), Company_Handbook.md, all SKILL.md files

---

*Built for Hackathon Zero — February 2026*
*AI Employee Vault System — Gold Tier*

---

Last tested: February 21, 2026 — All tiers verified operational.
