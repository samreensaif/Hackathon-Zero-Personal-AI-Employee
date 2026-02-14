"""
Orchestrator — AI Employee Vault (Silver Tier)

The "brain" of the system.  Runs as a continuous loop that:

1. Scans  Needs_Action/  for new .md task files
2. Parses each task (email-action, file-action, or generic)
3. Determines priority and whether human approval is needed
   (rules come from Company_Handbook.md)
4. Generates a structured Plan .md file
5. Routes the plan:
   - Approval required  → Pending_Approval/
   - Auto-approved      → Plans/  (ready for execution)
6. Moves the original task file to Done/

Environment Variables (from .env):
- NEEDS_ACTION_DIR          Folder to watch for incoming tasks
- PLANS_DIR / PLANS_FOLDER  Plans output folder   (defaults to ./Plans)
- PENDING_APPROVAL_DIR      Folder for plans that need human sign-off
- DONE_DIR                  Archive for processed task files
- LOGS_DIR                  Folder for orchestrator.log
- ORCHESTRATOR_CHECK_INTERVAL   Poll interval in seconds (default: 30)

Run:
    python orchestrator.py
"""

import os
import re
import json
import shutil
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / '.env')

NEEDS_ACTION_DIR = Path(os.getenv('NEEDS_ACTION_DIR', str(SCRIPT_DIR / 'Needs_Action')))
PLANS_DIR = Path(os.getenv('PLANS_DIR', str(SCRIPT_DIR / 'Plans')))
PENDING_APPROVAL_DIR = Path(os.getenv('PENDING_APPROVAL_DIR', str(SCRIPT_DIR / 'Pending_Approval')))
DONE_DIR = Path(os.getenv('DONE_DIR', str(SCRIPT_DIR / 'Done')))
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(SCRIPT_DIR / 'Logs')))
ORCHESTRATOR_CHECK_INTERVAL = int(os.getenv('ORCHESTRATOR_CHECK_INTERVAL', 30))

PROCESSED_TASKS_FILE = SCRIPT_DIR / 'processed_tasks.json'

# Ensure directories exist
for d in (NEEDS_ACTION_DIR, PLANS_DIR, PENDING_APPROVAL_DIR, DONE_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'orchestrator.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Approval rules  (derived from Company_Handbook.md)
# ---------------------------------------------------------------------------
# REQUIRES approval:
#   - All outbound messages (email replies, LinkedIn posts/messages)
#   - Any action that modifies external state
#   - Financial or commitment-related responses
#   - Anything the AI is uncertain about
#
# Does NOT require approval:
#   - Internal logging and file organisation
#   - Moving items between internal folders
#   - Summarising or triaging inbox items
#   - Updating the Dashboard

KEYWORDS_REQUIRING_APPROVAL = [
    'reply', 'respond', 'send', 'forward', 'post',
    'linkedin', 'outbound', 'payment', 'invoice',
    'contract', 'commit', 'sign', 'authorize', 'approve',
    'financial', 'budget', 'purchase',
]

SENDERS_REQUIRING_APPROVAL = [
    # Personal / real-human senders always get routed for review
    '@gmail.com', '@yahoo.com', '@hotmail.com', '@outlook.com',
]

AUTO_ARCHIVE_SENDERS = [
    'noreply@', 'no-reply@', 'notifications@', 'security-noreply@',
    'noreply@github.com', 'noreply@notification',
]

# ---------------------------------------------------------------------------
# Processed-task tracking
# ---------------------------------------------------------------------------


def load_processed_tasks() -> Set[str]:
    if PROCESSED_TASKS_FILE.exists():
        try:
            with open(PROCESSED_TASKS_FILE, 'r') as f:
                return set(json.load(f).get('processed_tasks', []))
        except Exception as e:
            logger.warning(f"Failed to load processed tasks: {e}")
    return set()


def save_processed_tasks(processed: Set[str]):
    try:
        with open(PROCESSED_TASKS_FILE, 'w') as f:
            json.dump({'processed_tasks': sorted(processed)}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save processed tasks: {e}")

# ---------------------------------------------------------------------------
# Task parsing
# ---------------------------------------------------------------------------


def parse_task_file(filepath: Path) -> Optional[Dict]:
    """
    Read a Needs_Action .md file and return a structured dict.

    Handles two formats produced by the watchers:
      - Email tasks  (gmail_watcher  → "# Email Action Required")
      - File tasks   (file_watcher   → "# File Action Required")
      - Generic tasks (anything else)
    """
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception as e:
        logger.error(f"[PARSE] Cannot read {filepath.name}: {e}")
        return None

    task: Dict = {
        'source_file': filepath.name,
        'source_path': str(filepath),
        'raw_content': content,
        'task_type': 'generic',
        'fields': {},
    }

    # Detect task type from heading
    if '# Email Action Required' in content:
        task['task_type'] = 'email'
    elif '# File Action Required' in content:
        task['task_type'] = 'file'

    # Extract **Key:** Value pairs
    for match in re.finditer(r'\*\*(.+?)\:\*\*\s*(.+)', content):
        key = match.group(1).strip()
        value = match.group(2).strip()
        task['fields'][key] = value

    # Extract preview / body (text between "## Preview" and next "##")
    preview_match = re.search(
        r'## Preview\s*\n(.*?)(?=\n## |\n---|\Z)', content, re.DOTALL,
    )
    if preview_match:
        task['fields']['Preview'] = preview_match.group(1).strip()[:500]

    return task

# ---------------------------------------------------------------------------
# Priority & approval decision
# ---------------------------------------------------------------------------


def assess_task(task: Dict) -> Dict:
    """
    Enrich *task* with priority, approval requirement, and recommended action.
    Returns the same dict with added keys.
    """
    fields = task['fields']
    content_lower = task['raw_content'].lower()
    task_type = task['task_type']

    # ----- priority -----
    priority = 'medium'
    sender = fields.get('From', fields.get('Source', '')).lower()
    subject = fields.get('Subject', '').lower()

    if any(w in subject for w in ('urgent', 'important', 'asap', 'critical')):
        priority = 'high'
    elif any(w in sender for w in AUTO_ARCHIVE_SENDERS):
        priority = 'low'
    # Personal senders bump to at least medium
    if any(dom in sender for dom in SENDERS_REQUIRING_APPROVAL):
        priority = max(priority, 'medium', key=['low', 'medium', 'high'].index)

    task['priority'] = priority

    # ----- requires approval? -----
    needs_approval = False
    approval_reasons = []

    # Rule 1: outbound / external actions
    if any(kw in content_lower for kw in KEYWORDS_REQUIRING_APPROVAL):
        needs_approval = True
        approval_reasons.append('Task mentions outbound/external action keywords')

    # Rule 2: email from a real person (not automated notification)
    if task_type == 'email':
        if not any(bot in sender for bot in AUTO_ARCHIVE_SENDERS):
            needs_approval = True
            approval_reasons.append('Email from a human sender — requires review')

    # Rule 3: high-priority always needs eyes
    if priority == 'high':
        needs_approval = True
        approval_reasons.append('High-priority task — flagged for human review')

    # Rule 4: file tasks are internal triage → auto-approve
    if task_type == 'file':
        needs_approval = False
        approval_reasons = ['File triage is internal — auto-approved per handbook']

    task['needs_approval'] = needs_approval
    task['approval_reasons'] = approval_reasons

    # ----- recommended action -----
    if task_type == 'email':
        if any(bot in sender for bot in AUTO_ARCHIVE_SENDERS):
            task['recommended_action'] = 'Archive'
            task['action_detail'] = 'Automated notification — no response needed.'
        elif priority == 'high':
            task['recommended_action'] = 'Escalate'
            task['action_detail'] = 'High-priority email — escalate for immediate human review.'
        else:
            task['recommended_action'] = 'Review & Reply'
            task['action_detail'] = 'Human sender — draft a response for approval.'
    elif task_type == 'file':
        task['recommended_action'] = 'Review File'
        task['action_detail'] = (
            f"New {fields.get('Category', 'unknown').lower()} file detected. "
            'Review contents and determine next steps.'
        )
    else:
        task['recommended_action'] = 'Review'
        task['action_detail'] = 'Generic task — review and determine action.'

    return task

# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------


def generate_plan(task: Dict) -> str:
    """Return the full markdown text for a Plan file."""
    fields = task['fields']
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    approval_status = 'PENDING APPROVAL' if task['needs_approval'] else 'AUTO-APPROVED'
    reasons_md = '\n'.join(f'- {r}' for r in task['approval_reasons'])

    # Build a field summary table
    field_rows = '\n'.join(
        f'| {k} | {v[:80]} |'
        for k, v in fields.items()
        if k != 'Preview'
    )

    preview = fields.get('Preview', '_No preview available_')

    # Checklist depends on recommended action
    action = task['recommended_action']
    if action == 'Archive':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Classified as automated notification\n'
            '- [ ] Move to Done folder\n'
            '- [ ] Update Dashboard'
        )
    elif action == 'Escalate':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Flagged as HIGH priority\n'
            '- [ ] Human reviews and decides action\n'
            '- [ ] Draft response (if reply needed)\n'
            '- [ ] Execute approved action\n'
            '- [ ] Move to Done folder'
        )
    elif action == 'Review & Reply':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Routed for human approval\n'
            '- [ ] Human reviews email content\n'
            '- [ ] Draft response created\n'
            '- [ ] Response approved and sent\n'
            '- [ ] Move to Done folder'
        )
    elif action == 'Review File':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Auto-approved (internal file triage)\n'
            '- [ ] Review file contents\n'
            '- [ ] Determine follow-up action\n'
            '- [ ] Process or delegate\n'
            '- [ ] Move to Done folder'
        )
    else:
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [ ] Human reviews task\n'
            '- [ ] Determine and execute action\n'
            '- [ ] Move to Done folder'
        )

    plan = f"""# Action Plan

**Source Task:** `{task['source_file']}`
**Task Type:** {task['task_type'].title()}
**Priority:** {task['priority'].upper()}
**Recommended Action:** {action}
**Approval Status:** {approval_status}
**Created:** {now_str}

---

## Task Summary

| Field | Value |
|-------|-------|
{field_rows}

### Preview / Content
{preview}

---

## Analysis

- **Task Type:** {task['task_type'].title()}
- **Priority Level:** {task['priority'].title()}
- **Recommended Action:** {action}
- **Detail:** {task['action_detail']}

### Approval Decision
{reasons_md}

**Result:** {approval_status}

---

## Handbook Alignment

| Rule | Status |
|------|--------|
| No auto-sending outbound messages | Compliant |
| Approval for external actions | {'Routed to Pending_Approval' if task['needs_approval'] else 'N/A — internal action'} |
| Audit trail maintained | Plan file + logs created |
| Escalate if uncertain | {'Yes — flagged' if task['priority'] == 'high' else 'N/A'} |

---

## Action Checklist

{checklist}

---

## Processing Chain

`Needs_Action/{task['source_file']}` → `{'Pending_Approval' if task['needs_approval'] else 'Plans'}/{task['source_file'].replace('_action.md', '_plan.md')}` → Done/

---
*Auto-generated by Orchestrator at {now_str}*
"""
    return plan

# ---------------------------------------------------------------------------
# Core processing loop
# ---------------------------------------------------------------------------


def process_task(filepath: Path, processed_tasks: Set[str]) -> bool:
    """
    Parse → assess → plan → route a single task file.
    Returns True on success.
    """
    logger.info(f"[PROCESSING] {filepath.name}")

    # 1. Parse
    task = parse_task_file(filepath)
    if task is None:
        return False

    # 2. Assess
    task = assess_task(task)
    logger.info(
        f"[ASSESSED] type={task['task_type']}  priority={task['priority']}  "
        f"action={task['recommended_action']}  approval={'YES' if task['needs_approval'] else 'NO'}"
    )

    # 3. Generate plan
    plan_content = generate_plan(task)
    plan_filename = filepath.name.replace('_action.md', '_plan.md').replace('_file_action.md', '_plan.md')
    if plan_filename == filepath.name:
        # Fallback: if no suffix matched, prefix with plan_
        plan_filename = f"plan_{filepath.name}"

    # 4. Route plan
    if task['needs_approval']:
        plan_path = PENDING_APPROVAL_DIR / plan_filename
        route_label = 'Pending_Approval'
    else:
        plan_path = PLANS_DIR / plan_filename
        route_label = 'Plans'

    try:
        with open(plan_path, 'w', encoding='utf-8') as f:
            f.write(plan_content)
        logger.info(f"[PLAN] Created: {route_label}/{plan_filename}")
    except Exception as e:
        logger.error(f"[ERROR] Failed to write plan: {e}")
        return False

    # 5. Move original task to Done
    done_filename = filepath.name.replace('_action.md', '_DONE.md').replace('_file_action.md', '_DONE.md')
    if done_filename == filepath.name:
        done_filename = f"DONE_{filepath.name}"
    done_path = DONE_DIR / done_filename

    try:
        # Append a processing footer to the original content before archiving
        footer = (
            f"\n\n---\n"
            f"**ORCHESTRATOR NOTE:**\n"
            f"- **Status:** Processed\n"
            f"- **Action:** {task['recommended_action']}\n"
            f"- **Priority:** {task['priority'].title()}\n"
            f"- **Approval:** {'Required — see Pending_Approval/' if task['needs_approval'] else 'Auto-approved — see Plans/'}\n"
            f"- **Plan Reference:** `{route_label}/{plan_filename}`\n"
            f"- **Processed Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        original_content = filepath.read_text(encoding='utf-8')
        with open(done_path, 'w', encoding='utf-8') as f:
            f.write(original_content + footer)

        filepath.unlink()  # remove from Needs_Action
        logger.info(f"[DONE] Archived: Done/{done_filename}")
    except Exception as e:
        logger.error(f"[ERROR] Failed to archive task to Done: {e}")
        return False

    # 6. Log summary
    if task['needs_approval']:
        logger.info(f"[APPROVAL] Plan routed to Pending_Approval — awaiting human review")
    else:
        logger.info(f"[AUTO-APPROVED] Plan saved to Plans — ready for execution")

    return True


def scan_needs_action(processed_tasks: Set[str]) -> Set[str]:
    """
    Scan Needs_Action/ for .md files and process any new ones.
    Returns the updated processed set.
    """
    md_files = sorted(NEEDS_ACTION_DIR.glob('*.md'))

    if not md_files:
        logger.info("[SCAN] No task files in Needs_Action/")
        return processed_tasks

    new_files = [f for f in md_files if f.name not in processed_tasks]
    if not new_files:
        logger.info(f"[SCAN] {len(md_files)} file(s) in Needs_Action — all already processed")
        return processed_tasks

    logger.info(f"[SCAN] Found {len(new_files)} new task(s) to process")

    newly_processed = 0
    for filepath in new_files:
        if process_task(filepath, processed_tasks):
            processed_tasks.add(filepath.name)
            newly_processed += 1

    if newly_processed:
        save_processed_tasks(processed_tasks)

    logger.info(f"[SUMMARY] Processed {newly_processed}/{len(new_files)} new task(s)")
    return processed_tasks

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_orchestrator():
    """Run the orchestrator loop continuously."""
    logger.info("=" * 60)
    logger.info("Orchestrator Started (Silver Tier)")
    logger.info(f"Watching:          {NEEDS_ACTION_DIR}")
    logger.info(f"Plans output:      {PLANS_DIR}")
    logger.info(f"Pending approval:  {PENDING_APPROVAL_DIR}")
    logger.info(f"Done archive:      {DONE_DIR}")
    logger.info(f"Logs:              {LOGS_DIR}")
    logger.info(f"Check interval:    {ORCHESTRATOR_CHECK_INTERVAL}s")
    logger.info("=" * 60)

    processed_tasks = load_processed_tasks()
    logger.info(f"[LOADED] {len(processed_tasks)} previously processed tasks")
    if processed_tasks:
        logger.info(f"[TIP] To reset tracking, delete '{PROCESSED_TASKS_FILE}' and restart")

    try:
        while True:
            logger.info(
                f"[CHECK] Scanning Needs_Action for new tasks... "
                f"({datetime.now().strftime('%H:%M:%S')})"
            )
            processed_tasks = scan_needs_action(processed_tasks)
            logger.info(f"[WAIT] Next check in {ORCHESTRATOR_CHECK_INTERVAL}s...")
            time.sleep(ORCHESTRATOR_CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("[STOP] Orchestrator stopped by user")
        save_processed_tasks(processed_tasks)
    except Exception as e:
        logger.error(f"[FATAL] Unexpected error: {e}")
        import traceback
        logger.error(f"[FATAL] {traceback.format_exc()}")
        save_processed_tasks(processed_tasks)


if __name__ == '__main__':
    run_orchestrator()
