"""
Ralph Loop — AI Employee Vault (Gold Tier)

The "Ralph Wiggum Loop" keeps Claude Code working autonomously on a task until
completion.  Named after the Simpsons episode where Ralph simply doesn't stop.

Architecture:
    1. Read a task file from Needs_Action/
    2. Build a context-rich prompt (task content + vault context + iteration history)
    3. Invoke `claude` CLI in print mode (non-interactive, single-shot)
    4. Check for completion:
        a. Promise-based:  Claude outputs <promise>TASK_COMPLETE</promise>
        b. File movement:  Task file moved from Needs_Action/ → Done/
    5. If NOT complete, re-invoke with feedback about what's still needed
    6. Repeat until complete, max iterations, or human intervention required

Safety:
    - Max iterations limit (default: 10)
    - Per-iteration timeout (default: 5 minutes)
    - Consecutive error detection → escalate after 3 in a row
    - Approval detection → pause loop if task requires human approval
    - Full state tracking in Logs/ralph_state_<task_id>.json

Modes:
    python ralph_loop.py --task-file "Needs_Action/some_task.md"   # Single task
    python ralph_loop.py --auto                                     # All in Needs_Action/
    python ralph_loop.py --status                                   # Show active loops
    python ralph_loop.py --resume <task_id>                         # Resume a paused loop

Environment Variables (from .env):
    NEEDS_ACTION_DIR / PENDING_APPROVAL_DIR / APPROVED_DIR / DONE_DIR / LOGS_DIR
    RALPH_MAX_ITERATIONS        Max iterations per task (default: 10)
    RALPH_ITERATION_TIMEOUT     Seconds per Claude call (default: 300)
"""

import os
import re
import sys
import json
import time
import hashlib
import logging
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR
load_dotenv(VAULT_ROOT / '.env')

NEEDS_ACTION_DIR = Path(os.getenv('NEEDS_ACTION_DIR', str(VAULT_ROOT / 'Needs_Action')))
PENDING_APPROVAL_DIR = Path(os.getenv('PENDING_APPROVAL_DIR', str(VAULT_ROOT / 'Pending_Approval')))
APPROVED_DIR = Path(os.getenv('APPROVED_DIR', str(VAULT_ROOT / 'Approved')))
DONE_DIR = Path(os.getenv('DONE_DIR', str(VAULT_ROOT / 'Done')))
PLANS_DIR = Path(os.getenv('PLANS_DIR', str(VAULT_ROOT / 'Plans')))
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(VAULT_ROOT / 'Logs')))

MAX_ITERATIONS = int(os.getenv('RALPH_MAX_ITERATIONS', '10'))
ITERATION_TIMEOUT = int(os.getenv('RALPH_ITERATION_TIMEOUT', '300'))
COMPLETION_PROMISE = 'TASK_COMPLETE'
CONSECUTIVE_ERROR_LIMIT = 3

for d in (NEEDS_ACTION_DIR, PENDING_APPROVAL_DIR, APPROVED_DIR, DONE_DIR,
          PLANS_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'ralph_loop.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task ID helper
# ---------------------------------------------------------------------------


def make_task_id(filepath: Path) -> str:
    """Generate a short, stable task ID from the filename."""
    name = filepath.stem
    # Use first 8 chars of md5 + simplified name for readability
    h = hashlib.md5(name.encode()).hexdigest()[:8]
    # Clean the name to something short
    short = re.sub(r'[^a-zA-Z0-9]', '_', name)[:30]
    return f"{short}_{h}"


# ---------------------------------------------------------------------------
# Loop state persistence
# ---------------------------------------------------------------------------


def state_path(task_id: str) -> Path:
    return LOGS_DIR / f"ralph_state_{task_id}.json"


def load_state(task_id: str) -> Dict:
    """Load or initialize loop state for a task."""
    sp = state_path(task_id)
    if sp.exists():
        try:
            with open(sp, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f'[STATE] Could not load state for {task_id}: {e}')

    return {
        'task_id': task_id,
        'status': 'initialized',   # initialized, running, paused, completed, failed, escalated
        'iteration': 0,
        'max_iterations': MAX_ITERATIONS,
        'consecutive_errors': 0,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat(),
        'task_file': '',
        'task_content': '',
        'iterations': [],
        'completion_method': None,
        'summary': '',
    }


def save_state(state: Dict):
    """Persist loop state to disk."""
    state['updated_at'] = datetime.now().isoformat()
    sp = state_path(state['task_id'])
    try:
        with open(sp, 'w') as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f'[STATE] Could not save state: {e}')


# ---------------------------------------------------------------------------
# Completion detection
# ---------------------------------------------------------------------------


def check_promise(claude_output: str) -> bool:
    """Check if Claude's output contains the completion promise tag."""
    pattern = rf'<promise>\s*{re.escape(COMPLETION_PROMISE)}\s*</promise>'
    return bool(re.search(pattern, claude_output, re.IGNORECASE))


def check_file_moved(task_filename: str) -> str:
    """
    Check if the task file has moved out of Needs_Action/.

    Returns:
        'needs_action' — still in Needs_Action/
        'done'         — moved to Done/
        'pending'      — moved to Pending_Approval/ (needs human approval)
        'approved'     — in Approved/
        'missing'      — gone from all known locations
    """
    basename = Path(task_filename).name

    if (NEEDS_ACTION_DIR / basename).exists():
        return 'needs_action'
    if any(DONE_DIR.glob(f'*{Path(basename).stem}*')):
        return 'done'
    if any(PENDING_APPROVAL_DIR.glob(f'*{Path(basename).stem}*')):
        return 'pending'
    if any(APPROVED_DIR.glob(f'*{Path(basename).stem}*')):
        return 'approved'
    # Also check Plans/ — orchestrator moves auto-approved plans there
    if any(PLANS_DIR.glob(f'*{Path(basename).stem}*')):
        return 'done'
    return 'missing'


def check_approval_needed(claude_output: str, task_filename: str) -> bool:
    """Detect if the task requires human approval before continuing."""
    # Check if a file related to this task appeared in Pending_Approval/
    stem = Path(task_filename).stem
    pending_files = list(PENDING_APPROVAL_DIR.glob(f'*{stem}*'))
    if pending_files:
        return True

    # Check Claude's output for approval indicators
    approval_phrases = [
        'pending approval', 'needs approval', 'requires approval',
        'awaiting approval', 'human review required', 'moved to pending_approval',
    ]
    output_lower = claude_output.lower()
    return any(phrase in output_lower for phrase in approval_phrases)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


def build_initial_prompt(task_content: str, task_filename: str) -> str:
    """Build the first-iteration prompt with full context."""
    return f"""You are the AI Employee (Gold Tier). You have been assigned a task to complete autonomously.

## Your Task

The following task file was placed in Needs_Action/ and needs to be processed:

**File:** `{task_filename}`

---
{task_content}
---

## Instructions

1. Analyze the task and determine what actions are needed.
2. Execute each action step by step.
3. Use the vault folder structure:
   - `Needs_Action/` — incoming tasks (this task is here)
   - `Pending_Approval/` — items needing human review before execution
   - `Plans/` — auto-approved action plans
   - `Done/` — completed tasks
4. If the task requires external actions (sending emails, posting to social media, financial transactions), create an approval request in `Pending_Approval/` and STOP.
5. If the task is internal (filing, summarizing, updating dashboard), complete it directly.
6. When you have fully completed the task, output exactly: <promise>TASK_COMPLETE</promise>
7. If you cannot complete the task, explain what's blocking you.

## Important Rules

- Follow the Company Handbook guidelines
- Never send outbound communications without approval
- Log your actions clearly
- If uncertain, create an approval request rather than proceeding

Work through this task now."""


def build_followup_prompt(
    task_content: str,
    task_filename: str,
    iteration: int,
    previous_output: str,
    previous_error: str,
) -> str:
    """Build a re-trigger prompt with context from the previous iteration."""
    context_parts = [
        f"""You are the AI Employee (Gold Tier). You are continuing work on a task.
This is iteration {iteration + 1} of processing.

## Original Task

**File:** `{task_filename}`

---
{task_content}
---

## Previous Iteration Output

"""
    ]

    # Include a truncated version of the previous output
    if previous_output:
        truncated = previous_output[-2000:] if len(previous_output) > 2000 else previous_output
        if len(previous_output) > 2000:
            truncated = "...(truncated)...\n" + truncated
        context_parts.append(truncated)
    else:
        context_parts.append("*(No output captured)*")

    if previous_error:
        context_parts.append(f"\n\n## Error from Previous Iteration\n\n```\n{previous_error[:500]}\n```")

    context_parts.append(f"""

## Instructions

The task is NOT yet complete. Continue where you left off.

- Review what was done in the previous iteration
- Determine what still needs to be done
- Complete the remaining steps
- When fully done, output: <promise>TASK_COMPLETE</promise>
- If blocked, explain what's needed

Continue working on this task now.""")

    return '\n'.join(context_parts)


# ---------------------------------------------------------------------------
# Claude CLI invocation
# ---------------------------------------------------------------------------


def invoke_claude(prompt: str, timeout: int = ITERATION_TIMEOUT) -> Dict:
    """
    Invoke the Claude CLI in print mode and capture its output.

    Returns a dict with:
        success: bool
        output:  str (stdout)
        error:   str (stderr)
        duration: float (seconds)
    """
    start = time.time()

    try:
        proc = subprocess.run(
            'claude --print --verbose',
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(VAULT_ROOT),
            shell=True,
        )

        duration = time.time() - start

        return {
            'success': proc.returncode == 0,
            'output': proc.stdout,
            'error': proc.stderr if proc.returncode != 0 else '',
            'return_code': proc.returncode,
            'duration': round(duration, 1),
        }

    except subprocess.TimeoutExpired:
        duration = time.time() - start
        logger.error(f'[CLAUDE] Timed out after {timeout}s')
        return {
            'success': False,
            'output': '',
            'error': f'Claude CLI timed out after {timeout} seconds',
            'return_code': -1,
            'duration': round(duration, 1),
        }
    except FileNotFoundError:
        logger.error('[CLAUDE] claude CLI not found in PATH')
        return {
            'success': False,
            'output': '',
            'error': 'claude CLI not found in PATH. Install with: npm install -g @anthropic-ai/claude-code',
            'return_code': -1,
            'duration': 0,
        }
    except Exception as e:
        duration = time.time() - start
        logger.error(f'[CLAUDE] Unexpected error: {e}')
        return {
            'success': False,
            'output': '',
            'error': str(e),
            'return_code': -1,
            'duration': round(duration, 1),
        }


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


def run_task_loop(
    task_filepath: Path,
    max_iterations: int = MAX_ITERATIONS,
    timeout: int = ITERATION_TIMEOUT,
) -> Dict:
    """
    Run the Ralph Loop on a single task file.

    Returns the final state dict with completion status and summary.
    """
    task_filename = task_filepath.name
    task_id = make_task_id(task_filepath)

    logger.info('=' * 60)
    logger.info(f'Ralph Loop — Starting')
    logger.info(f'Task:       {task_filename}')
    logger.info(f'Task ID:    {task_id}')
    logger.info(f'Max Iter:   {max_iterations}')
    logger.info(f'Timeout:    {timeout}s per iteration')
    logger.info('=' * 60)

    # Load or initialize state
    state = load_state(task_id)

    # Read task content
    if not task_filepath.exists():
        # Check if it was already processed
        file_status = check_file_moved(task_filename)
        if file_status == 'done':
            logger.info(f'[SKIP] Task already in Done/: {task_filename}')
            state['status'] = 'completed'
            state['completion_method'] = 'already_done'
            save_state(state)
            return state
        elif file_status == 'pending':
            logger.info(f'[PAUSED] Task is in Pending_Approval/: {task_filename}')
            state['status'] = 'paused'
            state['summary'] = 'Task requires human approval before continuing'
            save_state(state)
            return state
        else:
            logger.error(f'[ERROR] Task file not found: {task_filepath}')
            state['status'] = 'failed'
            state['summary'] = f'Task file not found: {task_filepath}'
            save_state(state)
            return state

    try:
        task_content = task_filepath.read_text(encoding='utf-8-sig')
    except UnicodeDecodeError:
        try:
            task_content = task_filepath.read_text(encoding='utf-16')
        except Exception as e:
            logger.error(f'[ERROR] Cannot read task file: {e}')
            state['status'] = 'failed'
            state['summary'] = f'Cannot read task file: {e}'
            save_state(state)
            return state
    except Exception as e:
        logger.error(f'[ERROR] Cannot read task file: {e}')
        state['status'] = 'failed'
        state['summary'] = f'Cannot read task file: {e}'
        save_state(state)
        return state

    state['task_file'] = str(task_filepath)
    state['task_content'] = task_content[:1000]  # Store truncated for reference
    state['status'] = 'running'
    save_state(state)

    previous_output = ''
    previous_error = ''

    # Main iteration loop
    for i in range(state['iteration'], max_iterations):
        state['iteration'] = i + 1
        logger.info(f'\n{"-" * 40}')
        logger.info(f'[ITERATION {i + 1}/{max_iterations}]')
        logger.info(f'{"-" * 40}')

        # Build prompt
        if i == 0 and not previous_output:
            prompt = build_initial_prompt(task_content, task_filename)
        else:
            prompt = build_followup_prompt(
                task_content, task_filename, i,
                previous_output, previous_error,
            )

        # Invoke Claude
        logger.info(f'[CLAUDE] Invoking (timeout={timeout}s)...')
        result = invoke_claude(prompt, timeout)

        # Record iteration
        iteration_record = {
            'iteration': i + 1,
            'timestamp': datetime.now().isoformat(),
            'success': result['success'],
            'duration': result['duration'],
            'output_length': len(result['output']),
            'error': result['error'][:200] if result['error'] else '',
            'promise_found': False,
            'file_status': '',
        }

        if result['success']:
            state['consecutive_errors'] = 0
            logger.info(f'[CLAUDE] Completed in {result["duration"]}s '
                        f'({len(result["output"])} chars)')
        else:
            state['consecutive_errors'] += 1
            logger.error(f'[CLAUDE] Failed (attempt {state["consecutive_errors"]}/'
                         f'{CONSECUTIVE_ERROR_LIMIT}): {result["error"][:200]}')

            if state['consecutive_errors'] >= CONSECUTIVE_ERROR_LIMIT:
                logger.error(f'[ESCALATE] {CONSECUTIVE_ERROR_LIMIT} consecutive errors — '
                             'escalating to human')
                state['status'] = 'escalated'
                state['summary'] = (
                    f'Escalated after {CONSECUTIVE_ERROR_LIMIT} consecutive errors. '
                    f'Last error: {result["error"][:200]}'
                )
                iteration_record['file_status'] = 'error_escalated'
                state['iterations'].append(iteration_record)
                save_state(state)
                _create_escalation_file(task_id, task_filename, state)
                return state

        previous_output = result['output']
        previous_error = result['error']

        # --- Check for completion ---

        # Strategy 1: Promise-based
        if result['output'] and check_promise(result['output']):
            logger.info(f'[COMPLETE] Promise detected: <promise>{COMPLETION_PROMISE}</promise>')
            state['status'] = 'completed'
            state['completion_method'] = 'promise'
            state['summary'] = f'Task completed via promise at iteration {i + 1}'
            iteration_record['promise_found'] = True
            iteration_record['file_status'] = 'complete_promise'
            state['iterations'].append(iteration_record)
            save_state(state)
            logger.info(f'[DONE] Task completed in {i + 1} iteration(s)')
            return state

        # Strategy 2: File movement
        file_status = check_file_moved(task_filename)
        iteration_record['file_status'] = file_status

        if file_status == 'done':
            logger.info(f'[COMPLETE] Task file moved to Done/')
            state['status'] = 'completed'
            state['completion_method'] = 'file_moved'
            state['summary'] = f'Task completed (file moved to Done/) at iteration {i + 1}'
            state['iterations'].append(iteration_record)
            save_state(state)
            logger.info(f'[DONE] Task completed in {i + 1} iteration(s)')
            return state

        if file_status == 'pending':
            logger.info(f'[PAUSED] Task routed to Pending_Approval/ — awaiting human')
            state['status'] = 'paused'
            state['summary'] = (
                f'Task paused at iteration {i + 1}: requires human approval. '
                'Check Pending_Approval/ and move to Approved/ to continue.'
            )
            state['iterations'].append(iteration_record)
            save_state(state)
            return state

        if file_status == 'approved':
            logger.info(f'[APPROVED] Task was approved — continuing')

        if file_status == 'missing' and i > 0:
            # The file was processed by the orchestrator (moved to Done via a
            # different filename pattern) — check Done/ more broadly
            logger.info(f'[CHECK] File missing from Needs_Action/ — checking Done/')
            state['status'] = 'completed'
            state['completion_method'] = 'file_moved'
            state['summary'] = f'Task file removed from Needs_Action/ at iteration {i + 1}'
            state['iterations'].append(iteration_record)
            save_state(state)
            return state

        # Check if approval was requested by Claude's output
        if check_approval_needed(result['output'], task_filename):
            logger.info(f'[PAUSED] Approval detected in Claude output — pausing loop')
            state['status'] = 'paused'
            state['summary'] = (
                f'Task paused at iteration {i + 1}: approval language detected. '
                'Review Pending_Approval/ and approve/reject before resuming.'
            )
            state['iterations'].append(iteration_record)
            save_state(state)
            return state

        # Not complete — continue to next iteration
        state['iterations'].append(iteration_record)
        save_state(state)
        logger.info(f'[CONTINUE] Task not yet complete — will retry')

        # Brief pause between iterations to avoid hammering
        if i < max_iterations - 1:
            time.sleep(2)

    # Max iterations reached
    logger.warning(f'[LIMIT] Max iterations ({max_iterations}) reached without completion')
    state['status'] = 'failed'
    state['summary'] = (
        f'Max iterations ({max_iterations}) reached without completion. '
        'Task may need manual intervention or a higher iteration limit.'
    )
    save_state(state)
    _create_escalation_file(task_id, task_filename, state)
    return state


# ---------------------------------------------------------------------------
# Escalation file
# ---------------------------------------------------------------------------


def _create_escalation_file(task_id: str, task_filename: str, state: Dict):
    """Create a file in Pending_Approval/ to alert the human that a loop failed."""
    now = datetime.now()
    escalation_name = f"ESCALATION_{task_id}_{now.strftime('%Y%m%d_%H%M%S')}.md"
    escalation_path = PENDING_APPROVAL_DIR / escalation_name

    iterations_summary = []
    for it in state.get('iterations', []):
        status_icon = 'OK' if it.get('success') else 'FAIL'
        iterations_summary.append(
            f"| {it['iteration']} | {status_icon} | {it.get('duration', 0)}s | "
            f"{it.get('file_status', '')} | {it.get('error', '')[:60]} |"
        )
    iterations_table = '\n'.join(iterations_summary) if iterations_summary else '| — | — | — | — | — |'

    content = f"""# Ralph Loop Escalation — Human Intervention Required

**Action Type:** escalation
**Task ID:** {task_id}
**Original Task:** `{task_filename}`
**Status:** {state.get('status', 'unknown').upper()}
**Iterations Completed:** {state.get('iteration', 0)}/{state.get('max_iterations', MAX_ITERATIONS)}
**Created:** {now.strftime('%Y-%m-%d %H:%M:%S')}
**Urgency:** high

---

## Summary

{state.get('summary', 'No summary available.')}

---

## Iteration History

| # | Status | Duration | File State | Error |
|---|--------|----------|------------|-------|
{iterations_table}

---

## What Happened

The Ralph Loop attempted to process this task autonomously but could not
complete it within the configured limits. Possible reasons:

- Task is too complex for autonomous processing
- External dependency blocking completion
- Repeated errors during execution
- Max iteration limit reached

---

## Recommended Actions

1. Review the original task in `Needs_Action/{task_filename}`
2. Check the state file: `Logs/ralph_state_{task_id}.json`
3. Check the log: `Logs/ralph_loop.log`
4. Either:
   a. Simplify the task and re-submit to Needs_Action/
   b. Process the task manually
   c. Increase `RALPH_MAX_ITERATIONS` and resume with: `python ralph_loop.py --resume {task_id}`

---

## Instructions

1. **To acknowledge:** Move this file to `Done/`
2. **To re-process:** Fix the issue and run: `python ralph_loop.py --resume {task_id}`

---
*Auto-generated by Ralph Loop at {now.strftime('%Y-%m-%d %H:%M:%S')}*
"""
    try:
        with open(escalation_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f'[ESCALATION] Created: Pending_Approval/{escalation_name}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to create escalation file: {e}')


# ---------------------------------------------------------------------------
# Auto-process mode
# ---------------------------------------------------------------------------


def auto_process():
    """Process all .md files in Needs_Action/ through the Ralph Loop."""
    md_files = sorted(NEEDS_ACTION_DIR.glob('*.md'))

    if not md_files:
        logger.info('[AUTO] No task files in Needs_Action/')
        return

    logger.info(f'[AUTO] Found {len(md_files)} task(s) in Needs_Action/')

    results = []
    for filepath in md_files:
        logger.info(f'\n{"=" * 60}')
        logger.info(f'[AUTO] Processing: {filepath.name}')
        logger.info(f'{"=" * 60}')

        state = run_task_loop(filepath)
        results.append({
            'file': filepath.name,
            'task_id': state['task_id'],
            'status': state['status'],
            'iterations': state['iteration'],
            'summary': state.get('summary', ''),
        })

        # If a task was paused for approval, don't block the rest
        if state['status'] == 'paused':
            logger.info(f'[AUTO] {filepath.name} paused for approval — continuing to next')

    # Print summary
    logger.info(f'\n{"=" * 60}')
    logger.info(f'[AUTO] Processing Summary')
    logger.info(f'{"=" * 60}')
    for r in results:
        icon = {
            'completed': 'DONE', 'paused': 'PAUSED',
            'failed': 'FAIL', 'escalated': 'ESCALATED',
        }.get(r['status'], r['status'].upper())
        logger.info(f'  [{icon}] {r["file"]} ({r["iterations"]} iter) — {r["summary"][:60]}')


# ---------------------------------------------------------------------------
# Resume mode
# ---------------------------------------------------------------------------


def resume_task(task_id: str):
    """Resume a paused or failed Ralph Loop by task ID."""
    sp = state_path(task_id)
    if not sp.exists():
        logger.error(f'[RESUME] No state file found for task ID: {task_id}')
        logger.info(f'[RESUME] Expected: {sp}')
        return

    state = load_state(task_id)
    logger.info(f'[RESUME] Task: {state.get("task_file", "unknown")}')
    logger.info(f'[RESUME] Previous status: {state["status"]}')
    logger.info(f'[RESUME] Completed iterations: {state["iteration"]}')

    task_file = state.get('task_file', '')
    if not task_file:
        logger.error('[RESUME] No task_file recorded in state')
        return

    task_path = Path(task_file)

    # If the task was paused for approval, check if it's been approved
    if state['status'] == 'paused':
        file_status = check_file_moved(task_path.name)
        if file_status == 'pending':
            logger.info('[RESUME] Task still in Pending_Approval/ — cannot resume yet')
            logger.info('[RESUME] Move the file to Approved/ or Rejected/ first')
            return
        elif file_status == 'done':
            logger.info('[RESUME] Task already completed (in Done/) — nothing to do')
            state['status'] = 'completed'
            state['completion_method'] = 'manual'
            save_state(state)
            return

    # Reset error counter and continue
    state['consecutive_errors'] = 0
    state['status'] = 'running'
    remaining = state['max_iterations'] - state['iteration']

    if remaining <= 0:
        logger.info('[RESUME] No iterations remaining — increasing limit by 5')
        state['max_iterations'] += 5
        remaining = 5

    save_state(state)

    # If the file is back in Needs_Action/ or still exists, continue
    if task_path.exists():
        result = run_task_loop(task_path, max_iterations=state['max_iterations'])
        logger.info(f'[RESUME] Final status: {result["status"]}')
    else:
        # File may have been moved — check
        file_status = check_file_moved(task_path.name)
        if file_status in ('done', 'missing'):
            logger.info(f'[RESUME] Task file no longer in Needs_Action/ (status={file_status})')
            state['status'] = 'completed'
            state['completion_method'] = 'manual'
            save_state(state)
        else:
            logger.warning(f'[RESUME] Task file status: {file_status} — cannot continue')


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------


def show_status():
    """Show all Ralph Loop state files and their current status."""
    state_files = sorted(LOGS_DIR.glob('ralph_state_*.json'))

    if not state_files:
        logger.info('[STATUS] No Ralph Loop state files found')
        return

    print(f'\n{"=" * 70}')
    print(f'  Ralph Loop — Active Tasks')
    print(f'{"=" * 70}')
    print(f'  {"Task ID":<35} {"Status":<12} {"Iter":<6} {"Updated"}')
    print(f'  {"-" * 35} {"-" * 12} {"-" * 6} {"-" * 20}')

    for sf in state_files:
        try:
            with open(sf, 'r') as f:
                state = json.load(f)
            task_id = state.get('task_id', sf.stem)
            status = state.get('status', '?')
            iteration = f"{state.get('iteration', 0)}/{state.get('max_iterations', MAX_ITERATIONS)}"
            updated = state.get('updated_at', '?')[:19]
            print(f'  {task_id:<35} {status:<12} {iteration:<6} {updated}')
        except Exception:
            print(f'  {sf.stem:<35} {"error":<12} {"?":<6} {"?":<20}')

    print(f'{"=" * 70}\n')


# ---------------------------------------------------------------------------
# Integration entry point (called by orchestrator)
# ---------------------------------------------------------------------------


def process_task(task_filepath: Path, **kwargs) -> Dict:
    """
    Entry point for the orchestrator or other scripts to invoke the Ralph Loop.

    Returns:
        {
            'task_id': str,
            'status': str,        # completed, paused, failed, escalated
            'iterations': int,
            'summary': str,
        }
    """
    max_iter = kwargs.get('max_iterations', MAX_ITERATIONS)
    timeout = kwargs.get('timeout', ITERATION_TIMEOUT)

    state = run_task_loop(task_filepath, max_iterations=max_iter, timeout=timeout)

    return {
        'task_id': state['task_id'],
        'status': state['status'],
        'iterations': state['iteration'],
        'summary': state.get('summary', ''),
        'completion_method': state.get('completion_method'),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description='Ralph Loop — Autonomous Task Processor (Gold Tier)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ralph_loop.py --task-file Needs_Action/some_task.md
  python ralph_loop.py --auto
  python ralph_loop.py --status
  python ralph_loop.py --resume some_task_id_abc12345
        """,
    )
    parser.add_argument(
        '--task-file',
        type=str,
        help='Path to a specific task file to process',
    )
    parser.add_argument(
        '--auto',
        action='store_true',
        help='Automatically process all .md files in Needs_Action/',
    )
    parser.add_argument(
        '--resume',
        type=str,
        metavar='TASK_ID',
        help='Resume a paused or failed loop by task ID',
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Show status of all Ralph Loop tasks',
    )
    parser.add_argument(
        '--max-iterations',
        type=int,
        default=MAX_ITERATIONS,
        help=f'Max iterations per task (default: {MAX_ITERATIONS})',
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=ITERATION_TIMEOUT,
        help=f'Timeout per iteration in seconds (default: {ITERATION_TIMEOUT})',
    )
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.resume:
        resume_task(args.resume)
    elif args.auto:
        auto_process()
    elif args.task_file:
        filepath = Path(args.task_file)
        if not filepath.is_absolute():
            filepath = VAULT_ROOT / filepath
        run_task_loop(
            filepath,
            max_iterations=args.max_iterations,
            timeout=args.timeout,
        )
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
