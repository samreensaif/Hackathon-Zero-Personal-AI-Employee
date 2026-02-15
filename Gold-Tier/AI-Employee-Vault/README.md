# Personal AI Employee -- Gold Tier

**Hackathon Zero | Gold Tier Implementation**

> An autonomous AI business assistant that monitors inboxes, manages accounting,
> drafts social media content, generates CEO briefings, and processes complex
> multi-step tasks -- all with human-in-the-loop approval.

---

## What Makes Gold Tier Special

Gold Tier extends the Silver Tier foundation with four major capabilities:

| Capability | Description |
|---|---|
| **Odoo Accounting** | Full accounting integration via MCP -- invoices, payments, expenses, revenue summaries |
| **Multi-Platform Social** | Draft and manage posts for Facebook, Instagram, and Twitter/X with per-platform rules |
| **CEO Briefing** | Weekly automated business briefing with 9 sections pulling from all data sources |
| **Ralph Loop** | Autonomous task completion engine that invokes Claude CLI iteratively until done |

---

## Gold Tier Requirements Checklist

- [x] All Silver Tier features (Gmail watcher, file watcher, LinkedIn poster, orchestrator, approval manager)
- [x] Odoo Community integration via MCP Server (JSON-RPC 2.0)
- [x] Facebook / Instagram / Twitter posting with platform-specific rules
- [x] Weekly CEO Briefing generator with 9 sections
- [x] Ralph Wiggum autonomous loop for complex multi-step tasks
- [x] Error recovery and graceful degradation (works even when Odoo is down)
- [x] Comprehensive audit logging with MCP server tracking and execution timing
- [x] All functionality exposed as Agent Skills
- [x] Financial threshold approval routing ($100+ requires CEO approval)
- [x] Task complexity scoring and automatic Ralph Loop routing

---

## Architecture Overview

### System Flow

```
                         Inbox/Gmail
                              |
                    +---------+---------+
                    |                   |
              Gmail Watcher       File Watcher
                    |                   |
                    +----> Needs_Action/ <----+
                              |
                        Orchestrator
                     (parse, assess, plan)
                              |
               +--------------+--------------+
               |              |              |
          Auto-Approved   Needs Approval   Complex Task
               |              |              |
            Plans/    Pending_Approval/   Ralph Loop
               |              |           (Claude CLI)
               |         Human Review         |
               |         /        \           |
               |    Approved/  Rejected/       |
               |        |          |          |
               |   Approval     Rejected      |
               |   Manager      Archive       |
               |   (execute)                  |
               |        |                     |
               +--------+---------------------+
                        |
                      Done/
```

### Integration Points

```
+-------------------+     JSON-RPC 2.0      +------------------+
|  Approval Manager | <------ stdio -------> |  Odoo MCP Server |
|  CEO Briefing Gen |                        |  (7 tools)       |
+-------------------+                        +--------+---------+
                                                      |
+-------------------+     JSON-RPC 2.0      +------------------+
|  Approval Manager | <------ stdio -------> |  Email MCP Server|
|                   |                        |  (send/draft)    |
+-------------------+                        +------------------+

+-------------------+     subprocess         +------------------+
|   Orchestrator    | <---- import --------> |   Ralph Loop     |
|                   |                        |  (Claude CLI)    |
+-------------------+                        +------------------+

+-------------------+     subprocess         +------------------+
|   Orchestrator    | <------ call --------> | Social Media     |
|                   |                        | Poster           |
+-------------------+                        +------------------+
```

---

## System Components

### Watchers (Continuous Monitoring)

| Component | File | Purpose |
|---|---|---|
| Gmail Watcher | `Watchers/gmail_watcher.py` | Polls Gmail API every 20s, creates task files in Needs_Action/ |
| File Watcher | `Watchers/file_watcher.py` | Monitors Inbox/ folder for new files, categorizes and creates task files |

### Content Generators

| Component | File | Purpose |
|---|---|---|
| LinkedIn Poster | `Watchers/linkedin_poster.py` | Generates LinkedIn post drafts using 5 templates, tracks in posts_history.json |
| Social Media Poster | `Watchers/social_media_poster.py` | Multi-platform poster for Facebook (500 char), Instagram (2200 char), Twitter (280 char) with 5 post types, per-platform rate limiting |
| CEO Briefing Generator | `ceo_briefing_generator.py` | Weekly 9-section briefing with its own Odoo client (graceful degradation), pulls from Done/, Business_Goals.md, social history, and logs |

### Core Processing

| Component | File | Purpose |
|---|---|---|
| Orchestrator | `orchestrator.py` | The brain -- parses tasks, detects 7 types (email, file, accounting, social_media, briefing, multi_step, generic), scores complexity 0-10, routes to Plans/ or Pending_Approval/ or Ralph Loop |
| Approval Manager | `approval_manager.py` | Executes approved actions -- 13 action types including Odoo invoice/payment/expense, platform-specific social posts with copy-paste summaries, CEO briefing email, retry files on failure |
| Ralph Loop | `ralph_loop.py` | Autonomous task processor -- invokes Claude CLI iteratively (max 10), detects completion via promise tags or file movement, escalates on failure |
| Task Scheduler | `task_scheduler.py` | Service manager for all 8 components -- continuous, scheduled, and on-demand modes with Odoo health checks |

### MCP Servers

| Server | File | Tools |
|---|---|---|
| Odoo MCP | `MCP_Servers/odoo_mcp_server.py` | `create_invoice`, `get_invoices`, `get_invoice_by_id`, `record_payment`, `get_revenue_summary`, `create_expense`, `get_expenses` |
| Email MCP | `MCP_Servers/email_mcp_server.py` | `send_email`, `draft_email`, `get_email` (from Silver Tier) |

### Agent Skills

| Skill | File |
|---|---|
| Email Processor | `Skills/email_processor_SKILL.md` |
| File Processor | `Skills/file_processor_SKILL.md` |
| LinkedIn Poster | `Skills/linkedin_poster_SKILL.md` |
| Orchestrator | `Skills/orchestrator_SKILL.md` |
| Approval Workflow | `Skills/approval_workflow_SKILL.md` |

---

## Setup Instructions

### Prerequisites

- **Python 3.10+** with pip
- **Docker Desktop** (for Odoo 19 + PostgreSQL)
- **Claude Code CLI** (`npm install -g @anthropic-ai/claude-code`)
- **Obsidian** (recommended for viewing .md files in the vault)
- **Gmail API credentials** (credentials.json from Google Cloud Console)

### 1. Install Python Dependencies

```bash
cd Gold-Tier
pip install -r requirements.txt
```

### 2. Start Odoo (Docker)

```bash
cd Gold-Tier
docker-compose up -d
```

Odoo will be available at http://localhost:8069. First-run setup:
1. Open http://localhost:8069 in browser
2. Create the database (name: `odoo_gold_tier`)
3. Set master password, admin email, and admin password
4. Install the **Invoicing** module

### 3. Configure Environment

Copy the example and fill in your credentials:

```bash
cp AI-Employee-Vault/.env.example AI-Employee-Vault/.env
```

Key variables to set:

| Variable | Description | Example |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key | `sk-ant-...` |
| `GMAIL_CREDENTIALS_PATH` | Path to Gmail credentials.json | `/path/to/credentials.json` |
| `ODOO_URL` | Odoo instance URL | `http://localhost:8069` |
| `ODOO_DB` | Odoo database name | `odoo_gold_tier` |
| `CEO_EMAIL` | Email for CEO briefings | `ceo@company.com` |
| `LINKEDIN_ACCESS_TOKEN` | LinkedIn API token | (from LinkedIn Developer Portal) |
| `FACEBOOK_ACCESS_TOKEN` | Facebook Page token | (from Meta Developer Portal) |

### 4. Configure MCP Servers

Ensure `.mcp.json` at the project root registers both servers:

```json
{
  "mcpServers": {
    "email": {
      "command": "python",
      "args": ["Gold-Tier/AI-Employee-Vault/MCP_Servers/email_mcp_server.py"]
    },
    "odoo": {
      "command": "python",
      "args": ["Gold-Tier/AI-Employee-Vault/MCP_Servers/odoo_mcp_server.py"]
    }
  }
}
```

### 5. Start the System

```bash
cd Gold-Tier/AI-Employee-Vault

# Start Odoo first (if not already running)
python task_scheduler.py --start-odoo

# Start all services
python task_scheduler.py --start-all
```

---

## Usage Guide

### Service Management

```bash
# Start all services
python task_scheduler.py --start-all

# View service status
python task_scheduler.py --status

# Check system health (Odoo connectivity, pending approvals, etc.)
python task_scheduler.py --health

# Start/stop individual services
python task_scheduler.py --start orchestrator
python task_scheduler.py --stop gmail_watcher
python task_scheduler.py --restart approval_manager
```

### Monitoring the System

**Check service status:**
```bash
python task_scheduler.py --status
```

Output shows all 8 services with their type, status, PID, and last activity.

**Check logs:**
```bash
# Orchestrator decisions
tail -f Logs/orchestrator.log

# Approval actions
tail -f Logs/approval_manager.log

# Ralph Loop iterations
tail -f Logs/ralph_loop.log

# Odoo MCP calls
tail -f Logs/odoo_mcp.log
```

**Check audit trail:**
```bash
# View approval history (JSON)
cat Logs/approvals.json | python -m json.tool
```

### Approving Actions

1. Files needing approval appear in `Pending_Approval/`
2. Review the file in Obsidian or any text editor
3. To approve: move the file to `Approved/`
4. To reject: move the file to `Rejected/`
5. The Approval Manager detects the move and executes the action

### Generating a CEO Briefing

```bash
# Generate briefing for current week
python ceo_briefing_generator.py

# Generate for a specific week
python ceo_briefing_generator.py --week-of 2026-02-10

# Generate and queue email draft
python ceo_briefing_generator.py --email
```

Briefings are saved to `Briefings/` and optionally queued for email approval.

### Triggering the Ralph Loop

```bash
# Process a specific task
python ralph_loop.py --task-file Needs_Action/complex_task.md

# Process all tasks in Needs_Action/
python ralph_loop.py --auto

# Check status of running loops
python ralph_loop.py --status

# Resume a paused loop
python ralph_loop.py --resume <task_id>
```

### Social Media Drafts

```bash
# Generate drafts for all platforms
python Watchers/social_media_poster.py --all

# Generate for a specific platform
python Watchers/social_media_poster.py --platform twitter

# Scan Inbox/ for content to post about
python Watchers/social_media_poster.py --scan
```

Drafts go to `Social_Media/` (archive) and `Pending_Approval/` (for CEO review).

---

## File Structure

```
Gold-Tier/
|-- docker-compose.yml          # Odoo 19 + PostgreSQL 16
|-- requirements.txt            # Python dependencies
|-- credentials.json            # Gmail API credentials
|-- README.md                   # This file (repo root)
|-- README_ODOO.md              # Odoo setup guide
|
+-- AI-Employee-Vault/          # The Obsidian vault / working directory
    |-- .env                    # All credentials and configuration
    |-- README.md               # This documentation
    |
    |-- orchestrator.py         # Task parser, assessor, plan generator
    |-- approval_manager.py     # Action executor for approved items
    |-- ralph_loop.py           # Autonomous multi-step task processor
    |-- ceo_briefing_generator.py  # Weekly CEO briefing generator
    |-- task_scheduler.py       # Service manager (8 services)
    |-- processed_tasks.json    # Orchestrator deduplication tracker
    |
    |-- Business_Goals.md       # Q1 2026 objectives, KPIs, revenue targets
    |-- Company_Handbook.md     # Approval rules, social media guidelines
    |-- Dashboard.md            # System status dashboard
    |-- Gold_Tier_Progress.md   # Implementation progress tracker
    |
    |-- Watchers/
    |   |-- gmail_watcher.py    # Gmail API polling
    |   |-- file_watcher.py     # Inbox/ folder monitoring
    |   |-- linkedin_poster.py  # LinkedIn post draft generator
    |   |-- social_media_poster.py  # Multi-platform post generator
    |   |-- posts_history.json  # LinkedIn post tracking
    |   +-- social_posts_history.json  # Social media post tracking
    |
    |-- MCP_Servers/
    |   |-- email_mcp_server.py # Gmail MCP (send/draft/read)
    |   +-- odoo_mcp_server.py  # Odoo MCP (7 accounting tools)
    |
    |-- Skills/
    |   |-- email_processor_SKILL.md
    |   |-- file_processor_SKILL.md
    |   |-- linkedin_poster_SKILL.md
    |   |-- orchestrator_SKILL.md
    |   +-- approval_workflow_SKILL.md
    |
    |-- Inbox/                  # Drop files here for processing
    |-- Needs_Action/           # Tasks awaiting orchestrator
    |-- Plans/                  # Auto-approved action plans
    |-- Pending_Approval/       # Items needing human review
    |-- Approved/               # Human-approved items (triggers execution)
    |-- Rejected/               # Human-rejected items
    |-- Rejected_Archive/       # Archived rejected items
    |-- Done/                   # Completed task archive
    |-- Briefings/              # Generated CEO briefings
    |-- Social_Media/           # Social media post archive + approved summaries
    |-- Accounting/             # Financial data directory
    +-- Logs/
        |-- orchestrator.log
        |-- approval_manager.log
        |-- ralph_loop.log
        |-- ceo_briefing.log
        |-- odoo_mcp.log
        |-- scheduler.log
        |-- social_media_poster.log
        +-- approvals.json      # Audit trail (JSON)
```

---

## Configuration Reference

### Environment Variables (.env)

#### Core

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | -- | Claude API key for Ralph Loop |
| `ORCHESTRATOR_CHECK_INTERVAL` | `30` | Seconds between orchestrator scans |
| `APPROVAL_CHECK_INTERVAL` | `10` | Seconds between approval folder checks |
| `FINANCIAL_APPROVAL_THRESHOLD` | `100` | Dollar amount requiring CEO approval |

#### Gmail

| Variable | Default | Description |
|---|---|---|
| `GMAIL_CREDENTIALS_PATH` | -- | Path to Google OAuth credentials.json |
| `GMAIL_TOKEN_PATH` | -- | Path to stored OAuth token |
| `GMAIL_CHECK_INTERVAL` | `20` | Seconds between Gmail polls |

#### LinkedIn

| Variable | Default | Description |
|---|---|---|
| `LINKEDIN_ACCESS_TOKEN` | -- | OAuth 2.0 access token |
| `LINKEDIN_AUTHOR_URN` | -- | LinkedIn person URN |
| `LINKEDIN_POST_MAX_PER_WEEK` | `3` | Weekly post limit |
| `LINKEDIN_POSTING_DAYS` | `Tuesday,Thursday` | Days to generate posts |
| `LINKEDIN_OPTIMAL_HOURS` | `9-11` | Hours to generate posts |

#### Odoo

| Variable | Default | Description |
|---|---|---|
| `ODOO_URL` | `http://localhost:8069` | Odoo instance URL |
| `ODOO_DB` | `odoo_gold_tier` | Database name |
| `ODOO_USERNAME` | `admin` | Login username |
| `ODOO_PASSWORD` | `admin` | Login password |

#### Social Media

| Variable | Default | Description |
|---|---|---|
| `FACEBOOK_ACCESS_TOKEN` | -- | Facebook Page access token |
| `INSTAGRAM_ACCESS_TOKEN` | -- | Instagram Graph API token |
| `TWITTER_API_KEY` | -- | Twitter/X API key |
| `TWITTER_API_SECRET` | -- | Twitter/X API secret |

#### CEO Briefing

| Variable | Default | Description |
|---|---|---|
| `CEO_EMAIL` | -- | Email address for briefings |
| `BRIEFING_TIME` | `08:00` | Time to generate briefing |
| `BRIEFING_ENABLED` | `false` | Enable scheduled briefings |

#### Ralph Loop

| Variable | Default | Description |
|---|---|---|
| `RALPH_MAX_ITERATIONS` | `10` | Max iterations per task |
| `RALPH_ITERATION_TIMEOUT` | `300` | Seconds per Claude CLI call |

### Scheduling Configuration

| Service | Schedule | Type |
|---|---|---|
| Gmail Watcher | Continuous | Long-running process |
| File Watcher | Continuous | Long-running process |
| Orchestrator | Continuous (30s poll) | Long-running process |
| Approval Manager | Continuous (10s poll) | Long-running process |
| LinkedIn Poster | Tue/Thu, 9-11 AM | Scheduled one-shot |
| Social Media Poster | Daily at 10 AM, 2 PM, 6 PM | Scheduled one-shot |
| CEO Briefing | Sunday 8 PM | Scheduled one-shot |
| Ralph Loop | On-demand | Triggered by orchestrator |

---

## Features Showcase

### CEO Briefing Example

The weekly briefing includes 9 sections:

```
# Monday Morning CEO Briefing
Week of February 10, 2026

## 1. Executive Summary
Revenue tracking at $5,000 this period. 12 tasks completed. All systems operational.

## 2. Revenue & Financial Analysis
| Metric           | Value      |
|------------------|------------|
| Total Invoiced   | $5,000.00  |
| Collected        | $3,500.00  |
| Outstanding      | $1,500.00  |
| Overdue          | $500.00    |

## 3. Completed Work This Week
- 8 emails triaged and responded to
- 3 invoices created in Odoo
- 2 social media posts published

## 4. Bottlenecks & Blockers
- 1 invoice overdue > 30 days (Acme Corp, $500)

## 5. Proactive Suggestions
- Consider follow-up on overdue Acme Corp invoice
- Social media engagement up 15% -- increase posting frequency?

## 6. Social Media Report
| Platform  | Posts | Pending |
|-----------|-------|---------|
| Facebook  | 2     | 1       |
| Twitter   | 1     | 0       |
| Instagram | 1     | 1       |

## 7. System Health
All 8 services operational. Odoo connected. No errors in last 7 days.

## 8. Upcoming Priorities
- Q1 revenue review due March 1
- Content calendar refresh needed

## 9. KPI Dashboard
| KPI                    | Target | Current | Status |
|------------------------|--------|---------|--------|
| Tasks Auto-Completed   | --     | 12/day  | --     |
| Briefings On Time      | 100%   | 100%    | OK     |
| Invoice Processing     | < 1 hr | 45 min  | OK     |
```

### Multi-Platform Social Media Example

A single content idea generates platform-appropriate drafts:

**Twitter (280 char limit):**
```
Exciting news! We just launched our new product line.
Check it out! #launch #newproduct #tech
```

**Facebook (500 char limit):**
```
Exciting news! We just launched our new product line and we
couldn't be more thrilled to share it with our community.

Check out the full details on our website and let us know
what you think in the comments!

#launch #newproduct #innovation #tech #community
```

**Instagram (2200 char limit):**
```
Exciting news! We just launched our new product line and we
couldn't be more thrilled to share it with our community.

This has been months in the making and represents our commitment
to delivering the best solutions for our customers. From concept
to launch, every detail was crafted with care.

Check out the link in our bio for full details!

#launch #newproduct #innovation #tech #community
#startup #entrepreneurship #business #growth
```

### Odoo Integration Example

When an invoice approval file is moved to `Approved/`:

```markdown
# Invoice Approval Request

**Action Type:** odoo_invoice
**Customer/Company:** Acme Corp
**Amount:** $500.00
**Description:** Consulting services -- February 2026
**Due Date:** 2026-03-15
```

The Approval Manager:
1. Parses the financial parameters
2. Validates required fields (customer_name, amount)
3. Calls Odoo MCP `create_invoice` tool via JSON-RPC
4. Logs the result (invoice ID, number, total)
5. Archives to Done/ with execution time

### Ralph Loop Example

For a complex multi-step task:

```
Iteration 1/10: Analyzing task requirements...
Iteration 2/10: Executing step 1 (data collection)...
Iteration 3/10: Executing step 2 (processing)...
Iteration 4/10: Task complete! <promise>TASK_COMPLETE</promise>

Result: Completed in 4 iterations (127.3s total)
```

If the task needs approval mid-way, the loop pauses:
```
Iteration 2/10: Task requires human approval -- pausing loop
Status: PAUSED (check Pending_Approval/)
```

---

## Troubleshooting

### Odoo Connection Failed

```
Error: Invalid URL '/jsonrpc': No scheme supplied
```

**Fix:** Check `.env` has actual values (not empty strings):
```
ODOO_URL=http://localhost:8069   # NOT just ODOO_URL=
ODOO_DB=odoo_gold_tier
```

### Odoo Docker Not Starting

```bash
# Check container status
docker-compose ps

# View logs
docker-compose logs odoo

# Restart
docker-compose down && docker-compose up -d
```

Odoo takes ~30 seconds to boot. Run `python task_scheduler.py --health` to check.

### Claude CLI Not Found (Ralph Loop)

```
Error: claude CLI not found in PATH
```

**Fix:** Install Claude Code globally:
```bash
npm install -g @anthropic-ai/claude-code
```

The Ralph Loop uses `shell=True` for subprocess calls to resolve npm binaries on Windows.

### Unicode Errors on Windows

```
UnicodeEncodeError: 'charmap' codec can't encode character
```

**Fix:** All Gold Tier scripts use ASCII-only log output. If you see this in a custom script, replace Unicode box characters (`---`) with ASCII equivalents (`---`).

### Social Media Rate Limited

```
Rate limited: already posted to twitter today
```

This is expected behavior. Each platform has a daily rate limit tracked in `Watchers/social_posts_history.json`. Delete or edit the history file to reset.

### Checking Logs

| Log File | What It Shows |
|---|---|
| `Logs/orchestrator.log` | Task categorization, priority, rules applied, routing decisions |
| `Logs/approval_manager.log` | Action execution, MCP calls, timing, errors |
| `Logs/ralph_loop.log` | Iteration progress, completion detection, escalations |
| `Logs/ceo_briefing.log` | Data collection, Odoo queries, briefing generation |
| `Logs/odoo_mcp.log` | All Odoo JSON-RPC calls and responses |
| `Logs/scheduler.log` | Service start/stop, scheduling decisions |
| `Logs/approvals.json` | Full audit trail with MCP server, timing, success status |

---

## Security

### Credential Management

- All API keys and tokens stored in `.env` (listed in `.gitignore`)
- `.env` is never committed to version control
- Each MCP server reads credentials at startup, not at import time

### API Key Safety

- Odoo credentials use the `or` fallback pattern to prevent empty-string defaults:
  ```python
  ODOO_URL = os.getenv('ODOO_URL', '') or 'http://localhost:8069'
  ```
- Social media tokens are optional -- system degrades gracefully without them
- Gmail uses OAuth 2.0 with refresh tokens (no password storage)

### Audit Trail

Every approved or rejected action is logged to `Logs/approvals.json` with:

```json
{
  "timestamp": "2026-02-15T11:46:06",
  "filename": "invoice_plan.md",
  "decision": "approved",
  "action_type": "odoo_invoice",
  "detail": "Action executed successfully",
  "success": true,
  "mcp_server": "odoo_mcp_server",
  "execution_time_seconds": 1.23
}
```

### Approval Workflow

No outbound action (email, social post, financial transaction) is ever executed
without explicit human approval. The system enforces this at three levels:

1. **Orchestrator** -- detects external action keywords and routes to Pending_Approval/
2. **Financial threshold** -- amounts >= $100 always require approval
3. **Social media** -- all posts require CEO approval per Company Handbook

---

## Future Enhancements (Platinum Tier Preview)

| Feature | Description |
|---|---|
| **Real-Time Social Posting** | Direct API integration with Facebook, Instagram, Twitter for automated posting |
| **Engagement Tracking** | Pull post metrics and generate performance reports |
| **Smart Content Calendar** | AI-generated content calendar with A/B testing |
| **Slack Integration** | Real-time notifications and approval via Slack |
| **Voice Briefing** | Text-to-speech CEO briefing delivery |
| **Predictive Analytics** | Revenue forecasting from Odoo historical data |
| **Multi-Vault Support** | Manage multiple businesses from a single scheduler |
| **Custom MCP Tools** | User-defined MCP tools for business-specific workflows |
| **Dashboard Auto-Update** | Live Dashboard.md with real-time metrics |
| **Mobile Approval** | Approve actions via mobile notification |

---

*Built for Hackathon Zero -- Personal AI Employee Challenge*
