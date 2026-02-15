# Approval Workflow Skill

## Skill Summary

**Purpose:** Monitor the Approved/ and Rejected/ folders, execute approved actions (send emails, log LinkedIn posts), handle rejections, maintain a complete audit trail, and recover gracefully from failures.

**Trigger:** Files appear in `Approved/` or `Rejected/` (moved there by a human reviewer)
**Authority Level:** High — Can execute outbound actions (email sends) once human approval is confirmed
**Success Metric:** Every approved action is executed and logged; every rejection is archived; zero untracked decisions

---

## Overview

This skill enables the AI Employee to:
1. Detect when a human has approved or rejected an action
2. Parse the approval file to determine what action to take
3. Execute the approved action (email send, LinkedIn log, archive, etc.)
4. Handle failures by returning the file to Pending_Approval/ with an error note
5. Archive completed items to Done/ with processing metadata
6. Archive rejections to Rejected_Archive/ with timestamps
7. Maintain `Logs/approvals.json` as a permanent audit trail

---

## Prerequisites

- Access to `Pending_Approval/`, `Approved/`, `Rejected/`, `Done/`, and `Rejected_Archive/` folders
- Access to `MCP_Servers/email_mcp_server.py` for email sends
- Access to `Watchers/posts_history.json` for LinkedIn history updates
- Access to `Logs/` for audit log and activity log
- Understanding of the approval file formats from orchestrator.py and linkedin_poster.py

---

## The Approval Lifecycle

```
Human places file in Approved/ or Rejected/
              │
    ┌─────────┴──────────┐
    │                    │
 Approved/            Rejected/
    │                    │
 Parse file           Parse file
 Detect type          Log rejection
    │                 Update posts_history (if LinkedIn)
    │                 Move to Rejected_Archive/
    │                 Record in approvals.json
    │
 Execute action
    │
 ┌──┴──┐
 │     │
OK?  FAIL?
 │     │
Done/ Back to Pending_Approval/
      + error note appended
```

---

## Step-by-Step Processing Instructions

### Step 1: Detect New Decisions

**What to do:**
- Scan `Approved/` for `.md` files not previously processed
- Scan `Rejected/` for `.md` files not previously processed
- Track seen files in memory to avoid reprocessing within a session

**Check for:**
- Only `.md` files (ignore other file types)
- Files that weren't present in the previous scan cycle

---

### Step 2: Parse the Approval File

**Two file formats exist. The parser must handle both:**

#### Format A — Orchestrator Plans (from orchestrator.py)
```
**Recommended Action:** Review & Reply
**Source Task:** `{filename}`
**Priority:** HIGH
**Approval Status:** PENDING APPROVAL
```
Mapping:
- `Review & Reply` or `send` → action_type = `email_reply`
- `Archive` → action_type = `archive`
- `Escalate` → action_type = `escalate`

#### Format B — LinkedIn Drafts (from linkedin_poster.py)
```
**Action Type:** linkedin_post
**To/Recipient:** LinkedIn Feed (public)
**Urgency:** low
```
Uses action_type directly from the file.

**Fields to extract:**
| Field | Regex Pattern | Used For |
|-------|--------------|----------|
| Action Type | `**Action Type:** (.+)` | Dispatch decision |
| Recommended Action | `**Recommended Action:** (.+)` | Fallback dispatch |
| To/Recipient | `**To/Recipient:** (.+)` | Email recipient |
| Subject/Context | `**Subject(/Context)?:** (.+)` | Email subject |
| Urgency | `**Urgency:** (\w+)` | Logging |
| Source Task | `**Source Task:** (.+)` | Audit trail |
| Draft Content | `## Draft Content` section | Email body |

---

### Step 3: Execute Approved Actions

#### Action: `linkedin_post`
1. Log the approval
2. Update `Watchers/posts_history.json` — set status to `approved`, record `approved_at` timestamp
3. Move file to `Done/` with approval footer
4. **Do NOT auto-publish** — the human publishes manually (per handbook: "Auto-post without human approval" is forbidden)

#### Action: `email_reply` / `email_send`
1. Validate recipient — skip if To field is empty or is "LinkedIn Feed (public)"
2. Build a JSON-RPC message for the Email MCP server:
   ```json
   {
     "jsonrpc": "2.0",
     "id": 1,
     "method": "tools/call",
     "params": {
       "name": "send_email",
       "arguments": {
         "to": "<recipient>",
         "subject": "<subject>",
         "body": "<draft content>"
       }
     }
   }
   ```
3. Launch `email_mcp_server.py` as a subprocess, pipe the JSON-RPC on stdin
4. Parse the stdout response for success or error
5. On success: move to Done/
6. On failure: move back to Pending_Approval/ with error note (see Step 5)

#### Action: `linkedin_message`
1. Log the approval
2. Note: manual send required (no API automation yet)
3. Move to Done/

#### Action: `archive`
1. Log that the archive action was approved
2. Move to Done/ (the task was already triaged — nothing to send)

#### Action: `escalate`
1. Log the escalation acknowledgement
2. Move to Done/

#### Action: `other` / unknown
1. Log the action type
2. Move to Done/

---

### Step 4: Process Rejections

When a file appears in `Rejected/`:

1. Parse the file to extract action_type
2. If `linkedin_post`: update `posts_history.json` with status `rejected`
3. Create `Rejected_Archive/{YYYYMMDD_HHMMSS}_REJECTED_{filename}`
4. Append a rejection footer: timestamp, action type, archive path
5. Delete the original from `Rejected/`
6. Record in audit log

---

### Step 5: Handle Failed Actions

If an action fails (MCP server error, timeout, missing recipient):

1. **Do NOT discard the file**
2. Move it back to `Pending_Approval/`
3. Append an error note to the bottom:
   ```
   ---
   **ACTION FAILED — RETURNED FOR REVIEW:**
   - **Error:** {error message}
   - **Failed at:** {timestamp}
   - **Please review and re-approve, or reject.**
   ```
4. Log the failure in the audit log with decision = `approved_failed`
5. The human can then fix the issue and re-approve, or reject

---

### Step 6: Record Audit Trail

Every decision is appended to `Logs/approvals.json`:

```json
{
  "timestamp": "2026-02-14T15:30:00",
  "filename": "2026-02-14_15-00_linkedin_post_milestone.md",
  "decision": "approved",
  "action_type": "linkedin_post",
  "detail": "Action executed successfully"
}
```

**Valid decision values:**
| Decision | Meaning |
|----------|---------|
| `approved` | Action executed successfully |
| `approved_failed` | Approved but execution failed — returned to pending |
| `rejected` | Human rejected the action |
| `error` | Unexpected error during processing |

---

## Approval Criteria by Action Type

| Action Type | What to Verify Before Executing | Risk Level |
|-------------|-------------------------------|------------|
| `email_reply` | Valid recipient, subject, and body present | **High** — sends real email |
| `email_send` | Same as email_reply | **High** |
| `linkedin_post` | Content present, within weekly limit | **Medium** — requires manual publish |
| `linkedin_message` | Recipient identified | **Medium** — requires manual send |
| `archive` | Nothing to verify — no external action | **Low** |
| `escalate` | Nothing to verify — informational only | **Low** |

---

## Examples

### Good: LinkedIn Post Approved
```
File: Approved/2026-02-14_15-00_linkedin_post_milestone.md
Action: Update posts_history.json → status: approved
Result: Move to Done/ with footer
Audit: { decision: "approved", action_type: "linkedin_post" }
```

### Good: Email Reply Approved and Sent
```
File: Approved/20260214_130000_client@gmail.com_plan.md
Parse: to="client@gmail.com", subject="Re: Meeting Thursday"
Action: Call email_mcp_server.py → send_email
Result: MCP returns message_id → Move to Done/
Audit: { decision: "approved", action_type: "email_reply" }
```

### Good: Email Send Fails — Returned to Pending
```
File: Approved/20260214_140000_partner@company.com_plan.md
Action: Call email_mcp_server.py → timeout after 60s
Result: Move BACK to Pending_Approval/ with error note
Audit: { decision: "approved_failed", detail: "MCP server timed out" }
Human can: fix the issue and re-approve, or reject
```

### Good: Rejection Handled
```
File: Rejected/2026-02-14_15-00_linkedin_post_lesson.md
Action: Update posts_history.json → status: rejected
Result: Copy to Rejected_Archive/20260214_153000_REJECTED_..., delete from Rejected/
Audit: { decision: "rejected", action_type: "linkedin_post" }
```

### Bad: Executing Without Parsing
```
NEVER execute an action without first parsing the file.
NEVER assume action_type — always extract it from the file.
If parsing fails, log the error and skip the file.
```

### Bad: Deleting Failed Actions
```
NEVER delete a file that failed to execute.
Always return it to Pending_Approval/ with a clear error note.
The human must decide what to do next.
```

---

## Common Pitfalls

| Pitfall | Correct Behaviour |
|---------|-------------------|
| Auto-publishing a LinkedIn post on approval | Only log and archive — human publishes manually |
| Sending email without valid recipient | Validate To field; skip if empty or non-email |
| Losing files on action failure | Always move back to Pending_Approval/ with error note |
| Not updating posts_history.json | Always update on both approval AND rejection |
| Missing audit entries | Every decision gets an entry in approvals.json |
| Re-processing the same file | Track seen filenames in memory per session |

---

## Integration Points

| Component | Interaction |
|-----------|-------------|
| `orchestrator.py` | Produces plan files in Pending_Approval/ |
| `linkedin_poster.py` | Produces draft posts in Pending_Approval/ |
| `email_mcp_server.py` | Executes email sends via JSON-RPC subprocess |
| `posts_history.json` | Shared state with linkedin_poster for tracking |
| `approvals.json` | Permanent audit log in Logs/ |
| `Company_Handbook.md` | Source of truth for what requires approval |

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-14 | 1.0 | Initial skill creation for Silver Tier |

---

**Skill Author:** AI Employee Vault System
**Last Updated:** February 14, 2026
**Status:** Active
