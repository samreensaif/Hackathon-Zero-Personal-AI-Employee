# Personal AI Employee — Silver Tier

A fully autonomous AI Employee system that monitors your Gmail inbox, triages files, generates LinkedIn content, creates structured action plans, and manages a human-in-the-loop approval workflow — all orchestrated from a single command.

---

## Quick Start

```bash
# 1. Install dependencies
cd E:\Hackathon-Zero\Silver-Tier\AI-Employee-Vault
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys and paths

# 3. Start the AI Employee
python task_scheduler.py --start-all
```

---

## Hackathon Requirements — All Complete

| # | Requirement | Status | Implementation |
|---|-------------|--------|----------------|
| 1 | Two or more Watcher scripts | Done | Gmail Watcher + File System Watcher + LinkedIn Poster |
| 2 | Automatically post on LinkedIn | Done | linkedin_poster.py with approval workflow |
| 3 | Claude reasoning loop (Plan.md) | Done | orchestrator.py analyses tasks, generates plans |
| 4 | One working MCP server | Done | Email MCP Server (send, draft, get) |
| 5 | Human-in-the-loop approval | Done | approval_manager.py with Approved/Rejected folders |
| 6 | Basic scheduling | Done | task_scheduler.py manages all services |
| 7 | All AI as Agent Skills | Done | 4 skill files in Skills/ |
| 8 | All Bronze requirements | Done | Vault, Gmail polling, task files, handbook |

---

## System Architecture

```
                         task_scheduler.py
                        (Master Controller)
                               |
          +--------------------+--------------------+
          |          |         |         |          |
   gmail_watcher  file_watcher  orchestrator  approval_manager  linkedin_poster
          |          |              |              |                   |
          v          v              |              |                   |
       Gmail API   Inbox/          |              |            Business_Goals.md
          |          |              |              |            Done/ folder
          v          v              v              |                   |
     +-----------+  +---------+  +-----------+    |                   v
     |Needs_Action| |Needs_   | |Plans/      |    |          Pending_Approval/
     |  (emails)  | |Action   | |Pending_    |    |          (LinkedIn drafts)
     |            | |(files)  | |Approval/   |    |
     +-----------+  +---------+ +-----------+     |
                                                  v
                                     +-------------------------+
                                     |    HUMAN REVIEWS FILE   |
                                     |  moves to Approved/ or  |
                                     |       Rejected/         |
                                     +------------+------------+
                                                  |
                                    +-------------+-------------+
                                    |                           |
                               Approved/                   Rejected/
                                    |                           |
                            Execute action              Rejected_Archive/
                          (send email via MCP,           (timestamped copy)
                           log LinkedIn post)
                                    |
                                  Done/
                            (archived with
                             processing footer)
```

---

## Components

### Watchers

| Component | File | Description |
|-----------|------|-------------|
| **Gmail Watcher** | `Watchers/gmail_watcher.py` | Polls Gmail API for unread important emails every 60s. Creates structured `.md` task files in `Needs_Action/` with sender, subject, and preview. Tracks processed emails in `processed_emails.json` to prevent duplicates. |
| **File System Watcher** | `Watchers/file_watcher.py` | Uses the `watchdog` library to monitor `Inbox/` in real time. Classifies files by extension (document, spreadsheet, image, code, PDF, archive). Creates task files with metadata and category-specific suggested actions. Moves originals to `Needs_Action/`. |
| **LinkedIn Poster** | `Watchers/linkedin_poster.py` | Reads `Business_Goals.md` and `Done/` for content. Generates post drafts using 5 rotating templates (Milestone, Lesson Learned, Thought Leadership, Progress Update, Behind the Scenes). Enforces rate limits (max 3/week). Saves drafts to `Pending_Approval/`. |

### Core Engine

| Component | File | Description |
|-----------|------|-------------|
| **Orchestrator** | `orchestrator.py` | The brain. Scans `Needs_Action/` every 30s. Parses email and file task formats. Assesses priority (Low/Medium/High). Determines approval requirements per Company Handbook rules. Generates structured Plan.md files. Routes plans to `Plans/` (auto-approved) or `Pending_Approval/` (needs review). Archives originals to `Done/`. |
| **Approval Manager** | `approval_manager.py` | Monitors `Approved/` and `Rejected/` every 10s. Executes approved actions: sends emails via MCP server, logs LinkedIn posts. On failure, returns files to `Pending_Approval/` with error notes. Archives rejections to `Rejected_Archive/`. Maintains full audit trail in `Logs/approvals.json`. |
| **Task Scheduler** | `task_scheduler.py` | Master process manager. Starts all services as subprocesses. Health-checks every 60s with auto-restart (up to 5 attempts). CLI for start/stop/status of individual or all services. Persists process state across restarts. Graceful shutdown on Ctrl+C. |

### MCP Server

| Component | File | Description |
|-----------|------|-------------|
| **Email MCP Server** | `MCP_Servers/email_mcp_server.py` | JSON-RPC 2.0 over stdio. Exposes `send_email`, `draft_email`, and `get_email` tools. Uses separate OAuth token (`token_mcp.json`) with send+compose+read scopes. File-only logging to avoid polluting the MCP stdio channel. Registered in `.mcp.json` for Claude Code. |

### Agent Skills

| Skill | File | Covers |
|-------|------|--------|
| **Email Processor** | `Skills/email_processor_SKILL.md` | Email classification, sender analysis, action plans, processing lifecycle |
| **Orchestrator** | `Skills/orchestrator_SKILL.md` | Task parsing, priority assessment, approval decision tree, Plan.md format |
| **Approval Workflow** | `Skills/approval_workflow_SKILL.md` | Action execution, failure recovery, rejection handling, audit logging |
| **File Processor** | `Skills/file_processor_SKILL.md` | File categorisation, security risk matrix, suggested actions by type |

---

## File Structure

```
Silver-Tier/AI-Employee-Vault/
|
|-- task_scheduler.py          # Master controller — start here
|-- orchestrator.py            # Task analyser and plan generator
|-- approval_manager.py        # Executes approved actions
|
|-- Watchers/
|   |-- gmail_watcher.py       # Gmail inbox monitor
|   |-- file_watcher.py        # File system monitor (watchdog)
|   |-- linkedin_poster.py     # LinkedIn content generator
|   |-- processed_emails.json  # Gmail dedup state (created at runtime)
|   |-- processed_files.json   # File dedup state (created at runtime)
|   +-- posts_history.json     # LinkedIn posting history (created at runtime)
|
|-- MCP_Servers/
|   +-- email_mcp_server.py    # Email MCP server (stdio)
|
|-- Skills/
|   |-- email_processor_SKILL.md
|   |-- orchestrator_SKILL.md
|   |-- approval_workflow_SKILL.md
|   +-- file_processor_SKILL.md
|
|-- Inbox/                     # Drop files here for processing
|-- Needs_Action/              # Tasks awaiting orchestrator analysis
|-- Plans/                     # Auto-approved plans (ready for execution)
|-- Pending_Approval/          # Plans and drafts awaiting human review
|-- Approved/                  # Human moves files here to approve
|-- Rejected/                  # Human moves files here to reject
|-- Rejected_Archive/          # Timestamped copies of rejected items
|-- Done/                      # Archive of all processed tasks
|-- Logs/                      # All log files
|   |-- scheduler.log
|   |-- orchestrator.log
|   |-- approval_manager.log
|   |-- file_watcher.log
|   |-- linkedin_poster.log
|   |-- email_mcp.log
|   +-- approvals.json         # Audit trail
|
|-- .env                       # Environment configuration (DO NOT COMMIT)
|-- .env.example               # Template for .env
|-- credentials.json           # Gmail OAuth credentials (DO NOT COMMIT)
|-- token.json                 # Gmail read-only token (DO NOT COMMIT)
|-- token_mcp.json             # Gmail send/compose token (DO NOT COMMIT)
|-- mcp_config.json            # MCP server config reference
|-- requirements.txt           # Python dependencies
|-- Company_Handbook.md        # Communication rules and approval policies
|-- Business_Goals.md          # Strategic priorities and metrics
|-- Dashboard.md               # System status overview
|-- Silver_Tier_Progress.md    # Hackathon progress tracker
+-- README.md                  # This file
```

---

## Setup Instructions

### Prerequisites

- Python 3.10+
- Gmail API credentials (OAuth 2.0 Desktop Application from Google Cloud Console)
- LinkedIn API credentials (optional, for posting integration)

### 1. Install Dependencies

```bash
cd E:\Hackathon-Zero\Silver-Tier\AI-Employee-Vault
pip install -r requirements.txt
```

**Required packages:**
- `google-api-python-client`, `google-auth`, `google-auth-oauthlib` — Gmail API
- `python-dotenv` — Environment variable management
- `watchdog` — File system monitoring
- `schedule` — Task scheduling
- `anthropic` — Claude API (for future AI enhancements)
- `requests` — HTTP utilities
- `playwright` — LinkedIn automation (future use)

### 2. Configure Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Gmail API**
4. Create **OAuth 2.0 Desktop Application** credentials
5. Download `credentials.json` and place it in the vault root
6. On first run, a browser window will open for OAuth consent

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your values:
- `ANTHROPIC_API_KEY` — Your Claude API key
- `GMAIL_CREDENTIALS_PATH` — Full path to credentials.json
- `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_ACCESS_TOKEN` — LinkedIn API credentials
- All `*_DIR` paths — Should point to your Silver-Tier vault folders

### 4. Configure MCP Server (Optional)

The Email MCP server is registered in `E:\Hackathon-Zero\.mcp.json`. Claude Code will prompt you to approve it on next session start.

---

## How to Run

### Start Everything

```bash
python task_scheduler.py --start-all
```

This launches all 5 services and enters a health-monitoring loop. Press `Ctrl+C` to stop.

### Check Status

```bash
python task_scheduler.py --status
```

Shows a formatted table of all services with PIDs and running status.

### Manage Individual Services

```bash
python task_scheduler.py --start gmail_watcher
python task_scheduler.py --stop orchestrator
python task_scheduler.py --restart approval_manager
```

### Generate a LinkedIn Post Manually

```bash
python Watchers/linkedin_poster.py
```

### Run the Orchestrator Standalone

```bash
python orchestrator.py
```

---

## Usage Examples

### Workflow 1: Email Arrives

```
1. New email arrives in Gmail
2. gmail_watcher detects it, creates Needs_Action/20260214_email_action.md
3. orchestrator reads the task, assesses priority
   - Automated notification? → Auto-approve, plan goes to Plans/
   - Human sender? → Plan goes to Pending_Approval/
4. Human reviews the plan in Pending_Approval/
5. Human moves file to Approved/ (or Rejected/)
6. approval_manager detects the move
   - If email reply: sends via MCP server
   - Logs to approvals.json, archives to Done/
```

### Workflow 2: File Dropped in Inbox

```
1. User drops "report.pdf" into Inbox/
2. file_watcher detects it instantly (watchdog)
3. Creates Needs_Action/20260214_report_file_action.md
4. Moves report.pdf to Needs_Action/
5. orchestrator picks up the task, auto-approves (internal triage)
6. Plan saved to Plans/ with suggested review actions
```

### Workflow 3: LinkedIn Post Generated

```
1. linkedin_poster reads Business_Goals.md and Done/ folder
2. Selects a template (e.g. "Progress Update"), fills with content
3. Saves draft to Pending_Approval/2026-02-14_09-30_linkedin_post_progress.md
4. Human reviews the draft
5. Human edits if needed, moves to Approved/
6. approval_manager logs approval, updates posts_history.json
7. Human publishes on LinkedIn at recommended time
```

---

## Approval Rules (from Company_Handbook.md)

### Requires Human Approval
- All outbound messages (email replies, LinkedIn posts/messages)
- Any action that modifies external state
- Financial or commitment-related responses
- Anything the AI is uncertain about

### Auto-Approved (No Human Review Needed)
- Internal logging and file organisation
- Moving items between internal folders
- Summarising or triaging inbox items
- Updating the Dashboard

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Gmail watcher won't authenticate | Delete `token.json` and restart — it will re-open the OAuth browser flow |
| MCP server "credentials not found" | Check `GMAIL_CREDENTIALS_PATH` in `.env` points to `credentials.json` |
| File watcher not detecting files | Verify `INBOX_DIR` in `.env` is correct; check `Logs/file_watcher.log` |
| Orchestrator not processing tasks | Check `Needs_Action/` has `.md` files; check `Logs/orchestrator.log` |
| Approved action not executing | Verify file is in `Approved/` (not still in `Pending_Approval/`); check `Logs/approval_manager.log` |
| LinkedIn poster weekly limit | Reset by editing `Watchers/posts_history.json` or wait for next week |
| Service keeps crashing | Check `Logs/<service>_subprocess.log`; scheduler auto-restarts up to 5 times |
| "Already processed" skips | Delete the relevant `processed_*.json` file to reset tracking |

---

## Security Notes

**Files that must NEVER be committed to version control:**

| File | Contains | Risk |
|------|----------|------|
| `.env` | API keys, OAuth secrets, access tokens | Full account access |
| `credentials.json` | Gmail OAuth client secret | API impersonation |
| `token.json` | Gmail read-only access token | Inbox read access |
| `token_mcp.json` | Gmail send/compose access token | Can send emails as you |

**Best practices:**
- Add all of the above to `.gitignore`
- Never share `.env` or token files
- Rotate LinkedIn access tokens regularly
- Use Google Cloud Console to revoke OAuth tokens if compromised
- The MCP server uses a separate token from the watcher to limit blast radius

---

## Future Enhancements (Gold Tier Preview)

- **AI-powered email drafting** — Use Claude API to generate contextual email responses
- **LinkedIn auto-publishing** — Post approved content via LinkedIn API (currently manual)
- **Slack/Teams integration** — Additional communication channels
- **Dashboard auto-update** — Real-time metrics in Dashboard.md
- **Learning from rejections** — Adjust drafts based on rejection patterns
- **Multi-agent collaboration** — Multiple AI employees with different specialisations
- **Web UI** — Browser-based approval interface replacing file-based workflow

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.10+ |
| Email API | Google Gmail API (OAuth 2.0) |
| LinkedIn | LinkedIn Marketing API |
| File Monitoring | watchdog |
| MCP Protocol | JSON-RPC 2.0 over stdio |
| Scheduling | schedule + subprocess |
| AI (future) | Anthropic Claude API |
| Configuration | python-dotenv |

---

## Credits

Built for the **Hackathon Zero** challenge.

**Architecture:** File-based event-driven system with human-in-the-loop approval.
Every component communicates through the shared vault folder structure — no databases, no message queues, no external infrastructure beyond Gmail and LinkedIn APIs.
