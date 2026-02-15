# Gold Tier — Personal AI Employee

> Hackathon Zero | Started: 2026-02-14

## Overview

The Gold Tier AI Employee is a fully autonomous business assistant that extends the Silver Tier foundation with **social media management**, **accounting integration (Odoo)**, **daily CEO briefings**, and **proactive business intelligence**. It operates through a vault-based architecture with file watchers, MCP servers, and approval workflows.

## Architecture

```
Gold-Tier/
├── AI-Employee-Vault/          # Central operational vault
│   ├── Inbox/                  # Incoming tasks and requests
│   ├── Needs_Action/           # Tasks queued for AI processing
│   ├── Done/                   # Completed tasks (archive)
│   ├── Plans/                  # Multi-step execution plans
│   ├── Logs/                   # Audit trail and activity logs
│   ├── Skills/                 # Reusable AI skill definitions
│   ├── Pending_Approval/       # Items awaiting CEO approval
│   ├── Approved/               # CEO-approved items ready to execute
│   ├── Rejected/               # Rejected items with feedback
│   ├── Watchers/               # File watcher scripts
│   ├── MCP_Servers/            # MCP server implementations
│   ├── Briefings/              # Daily CEO briefing reports
│   ├── Accounting/             # Odoo financial data and reports
│   ├── Social_Media/           # Social media drafts and archives
│   ├── Dashboard.md            # Real-time operational dashboard
│   ├── Company_Handbook.md     # Business rules and guidelines
│   ├── Business_Goals.md       # Q1 2026 objectives and KPIs
│   └── Gold_Tier_Progress.md   # Implementation progress tracker
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable template
├── .gitignore                  # Git exclusion rules
└── README.md                   # This file
```

## Gold Tier Capabilities

### 1. Multi-Platform Social Media Manager
- Draft, schedule, and publish content to Facebook, Instagram, and Twitter/X
- CEO approval workflow before any post goes live
- Engagement tracking and performance reporting
- Content calendar management with optimal posting times

### 2. Accounting Integration (Odoo)
- Read invoices, expenses, and payment statuses from Odoo
- Generate daily/weekly/monthly financial summaries
- Flag overdue payments and anomalies
- Monthly P&L report generation

### 3. Daily CEO Briefings
- Automated morning briefing with key business metrics
- Tasks completed, pending decisions, financial snapshot
- Social media engagement report
- Alerts and flags requiring attention

### 4. Enhanced Watcher System
- **Inbox Watcher:** Processes new files dropped into Inbox/
- **Approval Watcher:** Monitors Pending_Approval/ for CEO decisions
- **Social Media Watcher:** Manages content publishing queue
- **Accounting Watcher:** Syncs data from Odoo on schedule
- **Briefing Watcher:** Triggers daily briefing generation

### 5. MCP Server Integration
- Modular MCP servers for each integration domain
- Email, calendar, accounting, and social media servers
- Standardized tool interfaces for Claude

### 6. Proactive AI Behavior
- Autonomous task identification from business data
- Pattern recognition and trend analysis
- Predictive alerts (overdue invoices, engagement drops)
- Self-improvement through logged outcomes

## Workflow

```
                    ┌─────────────┐
                    │   CEO/User  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │    Inbox    │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ Needs_Action│
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
     ┌────────▼───┐ ┌──────▼─────┐ ┌───▼────────┐
     │ Auto-Handle│ │ Draft Plan │ │ Needs      │
     │ (routine)  │ │ (complex)  │ │ Approval   │
     └────────┬───┘ └──────┬─────┘ └───┬────────┘
              │            │            │
              │     ┌──────▼─────┐ ┌───▼────────┐
              │     │   Plans/   │ │ Pending_   │
              │     │  Execute   │ │ Approval/  │
              │     └──────┬─────┘ └───┬────────┘
              │            │       ┌───┴───┐
              │            │  ┌────▼──┐ ┌──▼─────┐
              │            │  │Approved│ │Rejected│
              │            │  └────┬──┘ └────────┘
              │            │       │
              └────────────┴───────┘
                           │
                    ┌──────▼──────┐
                    │    Done/    │
                    └─────────────┘
```

## Getting Started

### 1. Set Up Environment
```bash
cd Gold-Tier
cp .env.example .env
# Edit .env with your API keys and credentials
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Business Rules
- Edit `AI-Employee-Vault/Company_Handbook.md` with your company details
- Set revenue targets in `AI-Employee-Vault/Business_Goals.md`
- Review approval thresholds and posting guidelines

### 4. Launch Watchers
```bash
python AI-Employee-Vault/Watchers/main_watcher.py
```

### 5. Start MCP Servers
```bash
python AI-Employee-Vault/MCP_Servers/start_servers.py
```

## Tier Progression

| Feature | Bronze | Silver | Gold |
|---------|--------|--------|------|
| File watching | - | Basic | Advanced |
| Email integration | - | Read/Send | Full automation |
| Task management | Manual | Semi-auto | Fully autonomous |
| Social media | - | - | Multi-platform |
| Accounting | - | - | Odoo integration |
| CEO briefings | - | - | Daily automated |
| MCP servers | - | Basic | Full suite |
| Proactive AI | - | - | Pattern recognition |

## Key Files

| File | Purpose |
|------|---------|
| `Dashboard.md` | Real-time view of all operations |
| `Company_Handbook.md` | Business rules, tone, and policies |
| `Business_Goals.md` | Q1 2026 targets and KPIs |
| `Gold_Tier_Progress.md` | Implementation checklist |
| `.env.example` | Required environment variables |
| `requirements.txt` | Python package dependencies |

## Security

- All API keys stored in `.env` (never committed)
- Financial data access logged in `Logs/`
- Approval workflow for all external-facing actions
- Sensitive files excluded via `.gitignore`
