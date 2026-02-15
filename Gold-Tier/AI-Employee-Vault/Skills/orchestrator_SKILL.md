# Orchestrator Skill

## Skill Summary

**Purpose:** Analyse task files in Needs_Action/, determine priority and approval requirements, generate structured Plan.md files, and route them to the correct folder (Plans/ or Pending_Approval/).

**Trigger:** New `.md` files appear in `Needs_Action/`
**Authority Level:** Medium — Can create plans and auto-approve internal actions; must route external actions for human approval
**Success Metric:** Every task in Needs_Action/ has a corresponding Plan in Plans/ or Pending_Approval/, with documented reasoning

---

## Overview

This skill enables the AI Employee to:
1. Continuously scan `Needs_Action/` for new task files
2. Parse email-action and file-action formats produced by the watchers
3. Classify each task by type (email, file, generic)
4. Assess priority (Low / Medium / High)
5. Determine whether human approval is required (per Company_Handbook.md)
6. Generate a comprehensive Plan.md with analysis, reasoning, and checklist
7. Route the plan to the correct output folder
8. Archive the original task to `Done/` with a processing footer

---

## Prerequisites

- Access to `Needs_Action/`, `Plans/`, `Pending_Approval/`, and `Done/` folders
- Access to `Company_Handbook.md` for approval rules
- Ability to read and create markdown files
- `processed_tasks.json` for deduplication tracking

---

## Step-by-Step Processing Instructions

### Step 1: Scan for New Tasks

**What to do:**
- List all `.md` files in `Needs_Action/`
- Compare against `processed_tasks.json` to skip already-handled files
- Process new files in timestamp order (oldest first)

**Check for:**
- File naming pattern: `{YYYYMMDD_HHMMSS}_{identifier}_action.md`
- Valid markdown structure with expected heading and fields

---

### Step 2: Parse the Task File

**Detect the task type from the heading:**

| Heading | Task Type | Source |
|---------|-----------|--------|
| `# Email Action Required` | `email` | gmail_watcher |
| `# File Action Required` | `file` | file_watcher |
| Any other heading | `generic` | Manual or unknown |

**Extract metadata fields:**
- Parse all `**Key:** Value` pairs from the markdown
- Extract the `## Preview` section (truncated to 500 chars)
- Store raw content for plan generation

---

### Step 3: Assess Priority

**Priority is determined by content signals:**

| Signal | Priority |
|--------|----------|
| Subject contains `urgent`, `important`, `asap`, `critical` | **High** |
| Sender is a real human (`@gmail.com`, `@yahoo.com`, `@hotmail.com`, `@outlook.com`) | At least **Medium** |
| Sender is automated (`noreply@`, `no-reply@`, `notifications@`, `security-noreply@`) | **Low** |
| No special signals detected | **Medium** (default) |

**Priority never decreases** — if a sender bumps it to Medium, a subject keyword can still raise it to High.

---

### Step 4: Determine Approval Requirement

This is the critical decision. Follow Company_Handbook.md strictly.

#### REQUIRES Human Approval

Apply these rules in order. If **any** rule matches, route to `Pending_Approval/`:

1. **Keyword match** — Task content contains outbound/external action words:
   `reply`, `respond`, `send`, `forward`, `post`, `linkedin`, `outbound`,
   `payment`, `invoice`, `contract`, `commit`, `sign`, `authorize`, `approve`,
   `financial`, `budget`, `purchase`

2. **Human sender** — Email is from a real person (not an automated notification).
   Detected by: sender domain is a personal email provider AND sender is NOT in the auto-archive list.

3. **High priority** — Any task assessed as High priority always gets human eyes.

#### Does NOT Require Approval (Auto-approved)

4. **File tasks** — All file-type tasks from file_watcher are internal triage.
   Per handbook: "Internal logging and file organization" does not need approval.

5. **Automated notifications** — Emails from `noreply@`, `notifications@`, etc.
   Recommended action: Archive. No external action needed.

#### Decision Tree

```
START: New task parsed
│
├─ Is task_type == "file"?
│  └─ YES → Auto-approve (internal triage)
│
├─ Is sender in AUTO_ARCHIVE list?
│  └─ YES → Auto-approve, recommend Archive
│
├─ Does content contain approval keywords?
│  └─ YES → Route to Pending_Approval
│
├─ Is sender a real human (personal email domain)?
│  └─ YES → Route to Pending_Approval
│
├─ Is priority HIGH?
│  └─ YES → Route to Pending_Approval
│
└─ DEFAULT → Auto-approve, save to Plans/
```

---

### Step 5: Determine Recommended Action

Based on task type and assessment:

| Task Type | Sender Type | Recommended Action |
|-----------|-------------|-------------------|
| email | Automated notification | **Archive** — no response needed |
| email | Human, high priority | **Escalate** — immediate human review |
| email | Human, normal priority | **Review & Reply** — draft response for approval |
| file | Any | **Review File** — examine contents, determine follow-up |
| generic | Any | **Review** — human determines action |

---

### Step 6: Generate Plan.md

**Every plan must include these sections:**

```markdown
# Action Plan

**Source Task:** `{original_filename}`
**Task Type:** {Email / File / Generic}
**Priority:** {HIGH / MEDIUM / LOW}
**Recommended Action:** {Archive / Review & Reply / Escalate / Review File / Review}
**Approval Status:** {PENDING APPROVAL / AUTO-APPROVED}
**Created:** {YYYY-MM-DD HH:MM:SS}

---

## Task Summary
| Field | Value |
|-------|-------|
{parsed metadata fields}

### Preview / Content
{email body or file description, max 500 chars}

---

## Analysis
- **Task Type:** ...
- **Priority Level:** ...
- **Recommended Action:** ...
- **Detail:** {1-2 sentence explanation of why this action was chosen}

### Approval Decision
- {Reason 1 for the approval/auto-approve decision}
- {Reason 2 if applicable}

**Result:** {PENDING APPROVAL / AUTO-APPROVED}

---

## Handbook Alignment
| Rule | Status |
|------|--------|
| No auto-sending outbound messages | Compliant |
| Approval for external actions | {Routed / N/A} |
| Audit trail maintained | Plan file + logs created |
| Escalate if uncertain | {Flagged / N/A} |

---

## Action Checklist
- [x] Task analysed by Orchestrator
- [x] {Routed for approval / Auto-approved}
- [ ] {Next steps...}
- [ ] Move to Done folder

---

## Processing Chain
`Needs_Action/{source}` → `{Plans or Pending_Approval}/{plan_file}` → Done/

---
*Auto-generated by Orchestrator at {timestamp}*
```

---

### Step 7: Route the Plan

| Needs Approval? | Destination | Plan Filename |
|-----------------|-------------|---------------|
| Yes | `Pending_Approval/` | `{original}_plan.md` |
| No | `Plans/` | `{original}_plan.md` |

---

### Step 8: Archive the Original Task

- Read the original task content
- Append a processing footer with: Status, Action, Priority, Approval result, Plan reference, timestamp
- Write to `Done/{original}_DONE.md`
- Delete the original from `Needs_Action/`
- Update `processed_tasks.json`

---

## Examples

### Good: Automated Notification Email
```
Input:  20260214_120000_noreply@github.com_action.md
Type:   email
Sender: noreply@github.com (automated)
Priority: Low
Action: Archive
Approval: Auto-approved → Plans/
Reason: Automated notification, no response needed
```

### Good: Personal Email Requiring Review
```
Input:  20260214_130000_client@gmail.com_action.md
Type:   email
Sender: client@gmail.com (human)
Subject: "Can we meet Thursday?" (no urgency keywords)
Priority: Medium
Action: Review & Reply
Approval: PENDING → Pending_Approval/
Reason: Human sender from personal domain requires review
```

### Good: Urgent Email Escalation
```
Input:  20260214_140000_boss@company.com_action.md
Type:   email
Subject: "URGENT: Contract deadline tomorrow"
Priority: High
Action: Escalate
Approval: PENDING → Pending_Approval/
Reason: High priority + contains "reply" keyword
```

### Good: File Triage (Auto-approved)
```
Input:  20260214_150000_report_file_action.md
Type:   file
Category: PDF
Priority: Medium
Action: Review File
Approval: Auto-approved → Plans/
Reason: File triage is internal — no external action
```

### Bad: Sending Email Without Approval
```
NEVER auto-send emails, regardless of priority.
All outbound messages MUST go through Pending_Approval/.
Even "Archive" recommendations create a plan — they don't delete anything.
```

---

## Common Pitfalls

| Pitfall | Correct Behaviour |
|---------|-------------------|
| Auto-approving an email reply | Always route email replies to Pending_Approval |
| Ignoring high-priority tasks | High priority = always route to Pending_Approval |
| Not archiving the original | Always move original to Done/ with footer |
| Skipping the plan for "simple" tasks | Every task gets a Plan.md, no exceptions |
| Deleting files from Needs_Action | Never delete — always move to Done/ |

---

## Integration Points

| Component | Interaction |
|-----------|-------------|
| `gmail_watcher.py` | Produces email-action files in Needs_Action/ |
| `file_watcher.py` | Produces file-action files in Needs_Action/ |
| `approval_manager.py` | Processes plans from Pending_Approval/ after human review |
| `Company_Handbook.md` | Source of truth for all approval rules |
| `processed_tasks.json` | Deduplication state |
| `Logs/orchestrator.log` | Activity and error logging |

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-14 | 1.0 | Initial skill creation for Silver Tier |

---

**Skill Author:** AI Employee Vault System
**Last Updated:** February 14, 2026
**Status:** Active
