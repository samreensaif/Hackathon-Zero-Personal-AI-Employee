# Ralph Loop — Skill Reference

**Skill ID:** ralph_loop
**Version:** 1.0 | **Last Updated:** 2026-02-21
**Script:** `ralph_loop.py`
**Imported by:** `orchestrator.py` via `importlib.util`

---

## What the Ralph Loop Does

`ralph_loop.py` runs an autonomous agentic loop that repeatedly invokes the
Claude CLI (`claude --print`) on a complex task until one of three outcomes:

| Outcome | status returned |
|---|---|
| Task completed (promise or file check) | `"complete"` |
| Max iterations exhausted | `"timeout"` |
| Claude binary not found / file unreadable | `"error"` |
| Dry-run mode active | `"dry_run"` |

Each iteration:
1. Builds a prompt containing the task file content + the five system rules.
2. Invokes `claude --print "<prompt>"` as a subprocess (`cwd=VAULT_ROOT`, 300 s timeout).
3. Checks **two** completion conditions.
4. If not done: pauses `RALPH_PAUSE_BETWEEN` seconds, adds the last 800 chars of
   previous output as context, then calls Claude again.
5. On exit (any outcome) writes a state file and appends to the audit log.

---

## When the Orchestrator Routes to Ralph Loop vs. Handles Directly

The orchestrator (`orchestrator.py`) calls `assess_complexity(task)` which scores
each task from 0–10 using these signals:

| Signal | Score added |
|---|---|
| Each regex pattern from COMPLEXITY_INDICATORS matched | +1 each |
| Task classified as `multi_step` type | +3 |
| Multi-step indicators alongside another type | +2 |
| Content > 300 words | +1 |
| Content > 600 words | +1 |
| 3–5 numbered/bulleted action items | +1 |
| 6+ action items | +1 |

**Threshold: score ≥ 4 → routed to Ralph Loop.**

Additionally, `task_type == "multi_step"` always sets the recommended action to
`"Ralph Loop Processing"`.  The orchestrator also skips Ralph Loop routing if
`needs_approval` is True (approval-required tasks go to `Pending_Approval/`
first).

### Examples

| Task | Score | Route |
|---|---|---|
| "Reply to John's email" | 0–1 | Orchestrator handles directly |
| "Create an invoice in Odoo" | 1–2 | Orchestrator → Odoo hook |
| "Step 1: pull revenue data. Step 2: draft social post. Step 3: update dashboard." | 5–7 | **Ralph Loop** |
| "Complex multi-step workflow: first reconcile accounts, then generate P&L, then brief CEO" | 8–9 | **Ralph Loop** |

---

## The Two Completion Strategies

Ralph Loop checks **two independent conditions** after each Claude call.
The default strategy is `"both"` (inclusive-or).

### Condition A — Promise (token check)

Claude's output is scanned for the exact string:

```
<TASK_COMPLETE>
```

This token must appear on its own line in Claude's **final** response only
(the five system rules prohibit Claude from emitting it mid-task).

**Example output tail that satisfies the promise check:**
```
... updated Dashboard.md with one-line entry.

<TASK_COMPLETE>
```

### Condition B — File movement (Done/ check)

The loop checks whether any file whose name begins with `{task_stem}` has
appeared in `Done/`.  This catches cases where Claude moved the file but forgot
the token, or where an external process (the orchestrator itself) archived it.

**Example:** task stem `20260221_143022_WA_John_action` → loop checks for
`Done/20260221_143022_WA_John_action*`.

### Strategy selection

| Strategy | Behaviour |
|---|---|
| `"both"` (default) | Complete when **either** condition is met |
| `"promise"` | Only the token check counts |
| `"file"` | Only the Done/ file check counts |

Pass `strategy=` as a keyword argument to `process_task()` when calling directly.

---

## What the System Prompt Tells Claude (The Five Rules)

The system prompt (`_SYSTEM_INSTRUCTIONS`) is prepended to every prompt Claude
receives inside the loop.  It imposes five non-negotiable rules:

1. **Work through every step without stopping.**  No pausing to ask for
   permission; no "I would need to …" hedging — act immediately.

2. **Write to `Pending_Approval/` then continue.**  For any action requiring
   human approval, create a draft file in `Pending_Approval/` with the proposed
   content, then immediately move on to the next step.  The loop does not halt.

3. **Move the task file to `Done/` with a footer.**  When all steps are done,
   Claude must move `Needs_Action/{task_file}` → `Done/` and append:
   ```
   ---
   **RALPH LOOP NOTE:** Processed autonomously. All steps complete.
   Completed: {timestamp}
   ```

4. **Update `Dashboard.md`.**  One-line entry summarising what was done and when.

5. **End the final response with exactly `<TASK_COMPLETE>`** on its own line,
   with no trailing text.

---

## State File Format

After every iteration Ralph writes:

**Location:** `Plans/ralph_states/{task_stem}_ralph_state.json`

```json
{
  "task":       "20260221_143022_WA_John_action",
  "status":     "running",
  "iteration":  2,
  "summary":    "Iteration 2 in progress.",
  "updated_at": "2026-02-21T14:32:05.123456"
}
```

`status` values during the loop: `"running"`, then one of `"complete"`,
`"timeout"`, `"error"`, `"dry_run"` on exit.

---

## Audit Log Format

On completion (any outcome) Ralph appends one record to:

**Location:** `Logs/ralph_audit.json`

The file is a JSON array, capped at **200 records** (oldest entries pruned).

```json
[
  {
    "timestamp":  "2026-02-21T14:35:12.456789",
    "task":       "/full/path/to/Needs_Action/20260221_143022_WA_John_action.md",
    "status":     "complete",
    "iterations": 2,
    "duration_s": 87.4,
    "summary":    "Completed in 2 iteration(s). Promise=yes, File=yes."
  }
]
```

---

## Config Variables and Defaults

All variables are read from `Gold-Tier/AI-Employee-Vault/.env`.

| Variable | Default | Description |
|---|---|---|
| `RALPH_MAX_ITERATIONS` | `10` | Maximum Claude invocations per task before timeout |
| `RALPH_PAUSE_BETWEEN` | `5` | Seconds to wait between iterations |
| `RALPH_CLAUDE_CMD` | `claude` | Claude CLI binary name or full path |
| `VAULT_ROOT` | *(script directory)* | Vault root; already set in .env |
| `DRY_RUN` | `false` | Skip subprocess calls; return `"dry_run"` after 1 fake iteration |

---

## CLI Reference

```bash
# Run on a specific task file
python ralph_loop.py --task-file Needs_Action/my_task.md

# Run with an ad-hoc prompt (creates a temp task file automatically)
python ralph_loop.py --prompt "Process all files in Needs_Action/"

# Override the iteration limit
python ralph_loop.py --task-file Needs_Action/my_task.md --max-iterations 15

# Dry-run: no Claude calls, returns immediately
python ralph_loop.py --task-file Needs_Action/my_task.md --dry-run
```

Exit codes: `0` = complete or dry_run | `1` = timeout or error.

---

## process_task() API

```python
from ralph_loop import process_task
from pathlib import Path

result = process_task(Path("Needs_Action/my_task.md"))

# result shape
{
    "status":     "complete",   # or "timeout" | "error" | "dry_run"
    "iterations": 2,
    "summary":    "Completed in 2 iteration(s). Promise=yes, File=yes.",
    "duration_s": 87.4,
}
```

Optional keyword overrides:

```python
result = process_task(
    filepath,
    max_iterations=15,   # override RALPH_MAX_ITERATIONS
    dry_run=True,        # override DRY_RUN
    strategy="promise",  # "both" | "promise" | "file"
)
```

---

## Troubleshooting

### Loop hits max iterations without completing

**Symptom:** `status="timeout"` in the result; a `⚠️ RALPH LOOP TIMEOUT` block
appended to the task file.

**Causes and fixes:**

| Cause | Fix |
|---|---|
| Task is too large for one session | Split into 2–3 smaller task files; resubmit each |
| Claude keeps making partial progress | Increase `RALPH_MAX_ITERATIONS` in `.env` |
| Prompt is too long and Claude loses context | Shorten the task file; remove boilerplate |
| Claude is writing to wrong paths | Check that `VAULT_ROOT` in `.env` points to the vault |

**Debug steps:**
1. Open `Logs/ralph_loop.log` and find the task's iterations.
2. Read the last state in `Plans/ralph_states/{task_stem}_ralph_state.json`.
3. Rerun with `--dry-run` to verify the prompt builds correctly.
4. Rerun with `--max-iterations 20` to give more headroom.

---

### `claude` binary not found

**Symptom:** `status="error"` immediately; log line:
```
[ERROR] Claude CLI not found: 'claude'
  Install with : npm install -g @anthropic-ai/claude-code
  Or set       : RALPH_CLAUDE_CMD=<full path> in .env
  Verify with  : where claude   (Windows) / which claude  (Unix)
```

**Fixes:**
1. Install the CLI: `npm install -g @anthropic-ai/claude-code`
2. If installed to a non-standard path, set `RALPH_CLAUDE_CMD=/full/path/to/claude`
   in `.env`.
3. Ensure the shell that runs orchestrator/ralph has the same PATH as your
   interactive shell (common issue with scheduled tasks / service accounts).

---

### Task file is not moving to Done/

**Symptom:** Loop reaches `max_iterations` (timeout) even though Claude appears to
complete the task in its output.

**Causes:**
- Claude wrote `<TASK_COMPLETE>` but used the wrong path when moving the file.
- File permission error prevented the move.
- Claude moved the file but used a different filename stem (breaking the glob check).

**Fixes:**
1. Check `Logs/ralph_loop.log` for "Promise=yes, File=no" in the final summary —
   this means the token was found but the file wasn't moved.  Manually move the
   task file to Done/ and the next orchestrator cycle will not reprocess it.
2. Verify `DONE_DIR` in `.env` points to the correct folder.
3. If Claude is using absolute paths, confirm `VAULT_ROOT` is correct.
4. Switch to promise-only detection: `process_task(fp, strategy="promise")` — or
   set the task to only require the token, not the file move.

---

### `DRY_RUN=true` returning unexpectedly

`DRY_RUN=true` in `.env` affects **all** Ralph Loop invocations, including those
triggered by the orchestrator.  Set it back to `false` (or remove it) for
production operation.  The `--dry-run` CLI flag only affects the CLI invocation,
not orchestrator-triggered loops.

---

## Integration with Other Skills

- **Orchestrator Skill** — routes complexity_score ≥ 4 tasks here automatically.
- **Approval Workflow Skill** — reviews drafts placed in `Pending_Approval/` by
  Claude during the loop; humans approve before any outbound action occurs.
- **Email Processor Skill** — Ralph may create email reply drafts in
  `Pending_Approval/` as part of a multi-step task.
- **CEO Briefing** — Ralph can trigger the briefing generator as one step inside
  a multi-step task without human intervention.

---

**Skill Author:** AI Employee Vault System
**Status:** Active
