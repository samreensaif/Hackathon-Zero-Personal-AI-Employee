"""
Orchestrator -- AI Employee Vault (Gold Tier)

The "brain" of the system.  Runs as a continuous loop that:

1. Scans  Needs_Action/  for new .md task files
2. Parses each task (email, file, accounting, social_media, briefing,
   multi_step, or generic)
3. Determines priority (HIGH / MEDIUM / LOW) with category-specific rules
4. Checks financial thresholds and approval requirements
5. Generates a structured Plan .md file with:
   - Related Resources, Estimated Time, Dependencies, Success Criteria
6. Routes the plan:
   - Approval required  -> Pending_Approval/
   - Auto-approved      -> Plans/  (ready for execution)
   - Complex task       -> Ralph Loop  (autonomous multi-step processing)
7. Moves the original task file to Done/
8. Logs categorization decisions, rules applied, and processing time

Integration hooks:
   - Odoo MCP   -- financial tasks check live invoice/expense data
   - Social Media Poster -- social tasks trigger draft generation
   - CEO Briefing -- briefing tasks trigger report generation

Environment Variables (from .env):
- NEEDS_ACTION_DIR          Folder to watch for incoming tasks
- PLANS_DIR / PLANS_FOLDER  Plans output folder   (defaults to ./Plans)
- PENDING_APPROVAL_DIR      Folder for plans that need human sign-off
- DONE_DIR                  Archive for processed task files
- LOGS_DIR                  Folder for orchestrator.log
- ORCHESTRATOR_CHECK_INTERVAL   Poll interval in seconds (default: 30)
- FINANCIAL_APPROVAL_THRESHOLD  Dollar amount requiring approval (default: 100)

Run:
    python orchestrator.py
"""

import os
import re
import json
import shutil
import time
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

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

FINANCIAL_APPROVAL_THRESHOLD = float(os.getenv('FINANCIAL_APPROVAL_THRESHOLD', '100'))

PROCESSED_TASKS_FILE = SCRIPT_DIR / 'processed_tasks.json'

# Paths for integration hooks
SOCIAL_MEDIA_POSTER = SCRIPT_DIR / 'Watchers' / 'social_media_poster.py'
CEO_BRIEFING_GENERATOR = SCRIPT_DIR / 'ceo_briefing_generator.py'
RALPH_LOOP_SCRIPT = SCRIPT_DIR / 'ralph_loop.py'

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
# Task type detection keywords
# ---------------------------------------------------------------------------

TASK_TYPE_KEYWORDS = {
    'accounting': [
        'invoice', 'invoicing', 'expense', 'payment', 'revenue',
        'financial', 'accounting', 'odoo', 'receipt', 'bill',
        'reconciliation', 'ledger', 'p&l', 'profit', 'loss',
        'budget', 'tax', 'refund', 'credit note', 'debit',
    ],
    'social_media': [
        'facebook', 'instagram', 'twitter', 'social media', 'social post',
        'hashtag', 'engagement', 'follower', 'content calendar',
        'post draft', 'social draft', 'tweet', 'reel', 'story',
    ],
    'briefing': [
        'ceo briefing', 'daily briefing', 'weekly briefing',
        'morning briefing', 'executive summary', 'business report',
        'status report', 'weekly report', 'monthly report',
    ],
    'multi_step': [
        'multi-step', 'multistep', 'workflow', 'pipeline',
        'sequence', 'then', 'after that', 'followed by',
        'step 1', 'step 2', 'first,', 'second,', 'third,',
        'complex task', 'multiple actions', 'series of',
    ],
}

# ---------------------------------------------------------------------------
# Approval rules  (derived from Company_Handbook.md)
# ---------------------------------------------------------------------------
# REQUIRES approval:
#   - All outbound messages (email replies, LinkedIn posts/messages)
#   - Any action that modifies external state
#   - Financial or commitment-related responses
#   - Financial transactions above FINANCIAL_APPROVAL_THRESHOLD
#   - All social media posts (per handbook)
#   - Anything the AI is uncertain about
#
# Does NOT require approval:
#   - Internal logging and file organisation
#   - Moving items between internal folders
#   - Summarising or triaging inbox items
#   - Updating the Dashboard
#   - Generating internal reports (CEO briefing)

KEYWORDS_REQUIRING_APPROVAL = [
    'reply', 'respond', 'send', 'forward', 'post',
    'linkedin', 'outbound', 'payment', 'invoice',
    'contract', 'commit', 'sign', 'authorize', 'approve',
    'financial', 'budget', 'purchase',
    'facebook', 'instagram', 'twitter', 'social media',
    'odoo', 'accounting',
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
# Complexity detection for Ralph Loop routing
# ---------------------------------------------------------------------------

COMPLEXITY_INDICATORS = [
    # Multi-step language
    r'step\s*\d', r'first[,.]', r'second[,.]', r'third[,.]',
    r'then\b', r'after that', r'followed by', r'next[,.]',
    # Lists of actions
    r'(?:^|\n)\s*\d+[\.\)]\s+\w+',  # numbered list items
    r'(?:^|\n)\s*[-*]\s+\w+.*(?:\n\s*[-*]\s+\w+){2,}',  # 3+ bullet items
    # Explicit complexity markers
    r'multi[- ]?step', r'complex', r'workflow', r'pipeline',
    r'series of', r'multiple actions', r'several tasks',
]

# ---------------------------------------------------------------------------
# Estimated time rules (minutes) by task type
# ---------------------------------------------------------------------------

ESTIMATED_TIMES = {
    'email': {'archive': 1, 'review_reply': 10, 'escalate': 5, 'default': 5},
    'file': {'default': 3},
    'accounting': {'default': 15, 'invoice': 10, 'expense': 10, 'report': 20},
    'social_media': {'default': 10, 'draft': 5, 'schedule': 3},
    'briefing': {'default': 20},
    'multi_step': {'default': 30},
    'generic': {'default': 10},
}

# ---------------------------------------------------------------------------
# Related resources by task type
# ---------------------------------------------------------------------------

RELATED_RESOURCES = {
    'email': ['Watchers/gmail_watcher.py', 'MCP_Servers/email_mcp_server.py'],
    'file': ['Watchers/file_watcher.py'],
    'accounting': ['MCP_Servers/odoo_mcp_server.py', 'Business_Goals.md'],
    'social_media': [
        'Watchers/social_media_poster.py',
        'Watchers/linkedin_poster.py',
        'Company_Handbook.md (Social Media Guidelines)',
    ],
    'briefing': ['ceo_briefing_generator.py', 'Business_Goals.md'],
    'multi_step': ['ralph_loop.py'],
    'generic': ['Company_Handbook.md'],
}

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

    Handles formats produced by the watchers:
      - Email tasks  (gmail_watcher  -> "# Email Action Required")
      - File tasks   (file_watcher   -> "# File Action Required")
      - Accounting tasks (Odoo-related content)
      - Social media tasks (social platform content)
      - Briefing tasks (report/briefing requests)
      - Multi-step tasks (complex workflows)
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
        'detected_types': [],
        'categorization_rules': [],
    }

    content_lower = content.lower()

    # --- Detect task type from heading (highest priority) ---
    if '# Email Action Required' in content:
        task['task_type'] = 'email'
        task['categorization_rules'].append('Heading match: "# Email Action Required"')
    elif '# File Action Required' in content:
        task['task_type'] = 'file'
        task['categorization_rules'].append('Heading match: "# File Action Required"')
    else:
        # --- Detect task type from keyword scanning ---
        for ttype, keywords in TASK_TYPE_KEYWORDS.items():
            matches = [kw for kw in keywords if kw in content_lower]
            if matches:
                task['detected_types'].append({
                    'type': ttype,
                    'matches': matches,
                    'score': len(matches),
                })

        if task['detected_types']:
            # Pick the type with the most keyword matches
            best = max(task['detected_types'], key=lambda x: x['score'])
            task['task_type'] = best['type']
            task['categorization_rules'].append(
                f'Keyword match: {best["type"]} ({best["score"]} hits: '
                f'{", ".join(best["matches"][:5])})'
            )

            # If multi_step was detected alongside another type, note it
            types_found = {d['type'] for d in task['detected_types']}
            if 'multi_step' in types_found and best['type'] != 'multi_step':
                task['is_multi_step'] = True
                task['categorization_rules'].append(
                    'Also detected: multi_step indicators'
                )

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

    # Extract dollar amounts for financial threshold checking
    amounts = re.findall(r'\$\s*([\d,]+(?:\.\d{2})?)', content)
    if amounts:
        try:
            task['detected_amounts'] = [
                float(a.replace(',', '')) for a in amounts
            ]
            task['max_amount'] = max(task['detected_amounts'])
        except ValueError:
            pass

    return task

# ---------------------------------------------------------------------------
# Complexity assessment (for Ralph Loop routing)
# ---------------------------------------------------------------------------


def assess_complexity(task: Dict) -> Dict:
    """
    Determine if a task is complex enough to warrant Ralph Loop processing.

    Returns a dict with:
        is_complex: bool
        complexity_score: int (0-10)
        complexity_reasons: list[str]
    """
    content = task['raw_content']
    score = 0
    reasons = []

    # Check for multi-step indicators via regex
    for pattern in COMPLEXITY_INDICATORS:
        if re.search(pattern, content, re.IGNORECASE | re.MULTILINE):
            score += 1

    # Task type bonuses
    if task['task_type'] == 'multi_step':
        score += 3
        reasons.append('Task classified as multi_step')
    if task.get('is_multi_step'):
        score += 2
        reasons.append('Multi-step indicators detected alongside primary type')

    # Content length as proxy for complexity
    word_count = len(content.split())
    if word_count > 300:
        score += 1
        reasons.append(f'Long task content ({word_count} words)')
    if word_count > 600:
        score += 1
        reasons.append(f'Very long task content ({word_count} words)')

    # Count distinct action items (numbered or bulleted)
    action_items = re.findall(r'(?:^|\n)\s*(?:\d+[\.\)]|[-*])\s+\w+', content)
    if len(action_items) >= 3:
        score += 1
        reasons.append(f'{len(action_items)} action items detected')
    if len(action_items) >= 6:
        score += 1
        reasons.append(f'{len(action_items)} action items (high count)')

    if score >= 1 and not reasons:
        reasons.append(f'Complexity patterns matched (score: {score})')

    # Threshold: score >= 4 means complex enough for Ralph Loop
    is_complex = score >= 4

    return {
        'is_complex': is_complex,
        'complexity_score': min(score, 10),
        'complexity_reasons': reasons,
    }


# ---------------------------------------------------------------------------
# Priority & approval decision
# ---------------------------------------------------------------------------


def assess_task(task: Dict) -> Dict:
    """
    Enrich *task* with priority, approval requirement, recommended action,
    and complexity assessment.  Returns the same dict with added keys.
    """
    fields = task['fields']
    content_lower = task['raw_content'].lower()
    task_type = task['task_type']

    rules_applied = list(task.get('categorization_rules', []))

    # ----- priority (enhanced: HIGH / MEDIUM / LOW) -----
    priority = 'MEDIUM'
    sender = fields.get('From', fields.get('Source', '')).lower()
    subject = fields.get('Subject', '').lower()

    # HIGH priority conditions
    if any(w in subject for w in ('urgent', 'important', 'asap', 'critical')):
        priority = 'HIGH'
        rules_applied.append('Priority HIGH: urgent/important keyword in subject')

    if any(w in content_lower for w in ('security', 'breach', 'down', 'outage', 'failure')):
        priority = 'HIGH'
        rules_applied.append('Priority HIGH: security/system-critical keyword detected')

    # Category-specific priority rules
    if task_type == 'accounting':
        max_amount = task.get('max_amount', 0)
        if max_amount >= 1000:
            priority = 'HIGH'
            rules_applied.append(f'Priority HIGH: financial amount ${max_amount:,.2f} >= $1000')
        elif max_amount >= FINANCIAL_APPROVAL_THRESHOLD:
            priority = max(priority, 'MEDIUM', key=['LOW', 'MEDIUM', 'HIGH'].index)
            rules_applied.append(f'Priority MEDIUM: financial amount ${max_amount:,.2f} >= threshold')

    if task_type == 'social_media':
        # Social media is generally MEDIUM unless urgent
        if priority != 'HIGH':
            priority = 'MEDIUM'
            rules_applied.append('Priority MEDIUM: social media content (standard)')

    if task_type == 'briefing':
        # Briefings are MEDIUM by default (internal reports)
        if priority != 'HIGH':
            priority = 'MEDIUM'
            rules_applied.append('Priority MEDIUM: briefing/report request')

    # LOW priority conditions
    if any(bot in sender for bot in AUTO_ARCHIVE_SENDERS):
        priority = 'LOW'
        rules_applied.append('Priority LOW: automated sender (auto-archive candidate)')

    # Personal senders bump to at least MEDIUM
    if any(dom in sender for dom in SENDERS_REQUIRING_APPROVAL):
        priority = max(priority, 'MEDIUM', key=['LOW', 'MEDIUM', 'HIGH'].index)
        rules_applied.append('Priority >= MEDIUM: personal email domain')

    task['priority'] = priority

    # ----- requires approval? -----
    needs_approval = False
    approval_reasons = []

    # Rule 1: outbound / external action keywords
    if any(kw in content_lower for kw in KEYWORDS_REQUIRING_APPROVAL):
        needs_approval = True
        approval_reasons.append('Task mentions outbound/external action keywords')
        rules_applied.append('Approval: external action keyword match')

    # Rule 2: email from a real person (not automated notification)
    if task_type == 'email':
        if not any(bot in sender for bot in AUTO_ARCHIVE_SENDERS):
            needs_approval = True
            approval_reasons.append('Email from a human sender -- requires review')
            rules_applied.append('Approval: human sender email')

    # Rule 3: HIGH priority always needs eyes
    if priority == 'HIGH':
        needs_approval = True
        approval_reasons.append('HIGH-priority task -- flagged for human review')
        rules_applied.append('Approval: HIGH priority auto-flag')

    # Rule 4: file tasks are internal triage -> auto-approve
    if task_type == 'file':
        needs_approval = False
        approval_reasons = ['File triage is internal -- auto-approved per handbook']
        rules_applied.append('Auto-approve: internal file triage')

    # Rule 5: social media ALWAYS requires approval (per handbook)
    if task_type == 'social_media':
        needs_approval = True
        approval_reasons.append('All social media posts require CEO approval per handbook')
        rules_applied.append('Approval: social media always requires review')

    # Rule 6: financial threshold
    max_amount = task.get('max_amount', 0)
    if max_amount >= FINANCIAL_APPROVAL_THRESHOLD:
        needs_approval = True
        approval_reasons.append(
            f'Financial amount ${max_amount:,.2f} exceeds '
            f'threshold ${FINANCIAL_APPROVAL_THRESHOLD:,.2f}'
        )
        rules_applied.append(
            f'Approval: financial threshold exceeded '
            f'(${max_amount:,.2f} >= ${FINANCIAL_APPROVAL_THRESHOLD:,.2f})'
        )

    # Rule 7: briefing tasks are internal -> auto-approve
    if task_type == 'briefing':
        needs_approval = False
        approval_reasons = ['Internal report generation -- auto-approved']
        rules_applied.append('Auto-approve: internal briefing/report')

    task['needs_approval'] = needs_approval
    task['approval_reasons'] = approval_reasons
    task['rules_applied'] = rules_applied

    # ----- complexity assessment -----
    complexity = assess_complexity(task)
    task['complexity'] = complexity

    # ----- recommended action -----
    if task_type == 'email':
        if any(bot in sender for bot in AUTO_ARCHIVE_SENDERS):
            task['recommended_action'] = 'Archive'
            task['action_detail'] = 'Automated notification -- no response needed.'
        elif priority == 'HIGH':
            task['recommended_action'] = 'Escalate'
            task['action_detail'] = 'HIGH-priority email -- escalate for immediate human review.'
        else:
            task['recommended_action'] = 'Review & Reply'
            task['action_detail'] = 'Human sender -- draft a response for approval.'

    elif task_type == 'file':
        task['recommended_action'] = 'Review File'
        task['action_detail'] = (
            f"New {fields.get('Category', 'unknown').lower()} file detected. "
            'Review contents and determine next steps.'
        )

    elif task_type == 'accounting':
        if any(kw in content_lower for kw in ('invoice', 'bill', 'receipt')):
            task['recommended_action'] = 'Process Financial Document'
            task['action_detail'] = 'Accounting document -- process via Odoo integration.'
        elif any(kw in content_lower for kw in ('report', 'summary', 'reconcil')):
            task['recommended_action'] = 'Generate Financial Report'
            task['action_detail'] = 'Financial report request -- generate via Odoo data.'
        else:
            task['recommended_action'] = 'Review Financial Task'
            task['action_detail'] = 'Accounting-related task -- review and determine action.'

    elif task_type == 'social_media':
        task['recommended_action'] = 'Generate Social Draft'
        task['action_detail'] = 'Social media task -- generate draft for CEO approval.'

    elif task_type == 'briefing':
        task['recommended_action'] = 'Generate Briefing'
        task['action_detail'] = 'Briefing/report request -- trigger CEO briefing generator.'

    elif task_type == 'multi_step':
        task['recommended_action'] = 'Ralph Loop Processing'
        task['action_detail'] = 'Multi-step task -- route to Ralph Loop for autonomous completion.'

    else:
        task['recommended_action'] = 'Review'
        task['action_detail'] = 'Generic task -- review and determine action.'

    # Override: complex tasks get Ralph Loop recommendation
    if complexity['is_complex'] and task_type not in ('file', 'briefing'):
        task['recommended_action'] = 'Ralph Loop Processing'
        task['action_detail'] = (
            f'Complex task (score: {complexity["complexity_score"]}) -- '
            'route to Ralph Loop for autonomous multi-step processing.'
        )

    return task

# ---------------------------------------------------------------------------
# Integration hooks
# ---------------------------------------------------------------------------


def trigger_social_media_hook(task: Dict) -> Optional[str]:
    """
    Trigger the social media poster to generate a draft.
    Returns a status message or None if the hook is not available.
    """
    if not SOCIAL_MEDIA_POSTER.exists():
        return None

    try:
        # Determine platform from task content
        content_lower = task['raw_content'].lower()
        platform = None
        for p in ('twitter', 'facebook', 'instagram'):
            if p in content_lower:
                platform = p
                break

        cmd = [sys.executable, str(SOCIAL_MEDIA_POSTER)]
        if platform:
            cmd.extend(['--platform', platform])
        else:
            cmd.append('--all')

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(SCRIPT_DIR),
        )

        if proc.returncode == 0:
            return f'Social media poster triggered (platform={platform or "all"})'
        else:
            return f'Social media poster returned code {proc.returncode}'

    except Exception as e:
        logger.warning(f'[HOOK] Social media poster failed: {e}')
        return f'Social media hook error: {e}'


def trigger_briefing_hook(task: Dict) -> Optional[str]:
    """
    Trigger the CEO briefing generator.
    Returns a status message or None if the hook is not available.
    """
    if not CEO_BRIEFING_GENERATOR.exists():
        return None

    try:
        proc = subprocess.run(
            [sys.executable, str(CEO_BRIEFING_GENERATOR)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(SCRIPT_DIR),
        )

        if proc.returncode == 0:
            return 'CEO briefing generator triggered successfully'
        else:
            return f'Briefing generator returned code {proc.returncode}'

    except Exception as e:
        logger.warning(f'[HOOK] Briefing generator failed: {e}')
        return f'Briefing hook error: {e}'


def trigger_ralph_loop(task: Dict, filepath: Path) -> Optional[Dict]:
    """
    Trigger the Ralph Loop for a complex task.
    Returns the Ralph Loop result dict or None if not available.
    """
    if not RALPH_LOOP_SCRIPT.exists():
        logger.warning('[HOOK] ralph_loop.py not found -- skipping Ralph Loop')
        return None

    try:
        # Import and call directly for better integration
        import importlib.util
        spec = importlib.util.spec_from_file_location('ralph_loop', RALPH_LOOP_SCRIPT)
        ralph = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ralph)

        result = ralph.process_task(filepath)
        logger.info(
            f'[RALPH] Result: status={result["status"]} '
            f'iterations={result["iterations"]} '
            f'summary={result.get("summary", "")[:80]}'
        )
        return result

    except Exception as e:
        logger.error(f'[HOOK] Ralph Loop invocation failed: {e}')
        return None


# ---------------------------------------------------------------------------
# Plan generation (enhanced with Gold Tier sections)
# ---------------------------------------------------------------------------


def estimate_time(task: Dict) -> str:
    """Estimate processing time based on task type and action."""
    task_type = task['task_type']
    action = task.get('recommended_action', '').lower().replace(' ', '_')

    times = ESTIMATED_TIMES.get(task_type, ESTIMATED_TIMES['generic'])

    # Try action-specific time first
    for key in (action, 'default'):
        if key in times:
            minutes = times[key]
            if minutes < 60:
                return f'{minutes} minutes'
            return f'{minutes // 60}h {minutes % 60}m'

    return times.get('default', 10)


def get_dependencies(task: Dict) -> List[str]:
    """Determine task dependencies based on type and content."""
    deps = []
    task_type = task['task_type']
    content_lower = task['raw_content'].lower()

    if task_type == 'accounting' or 'odoo' in content_lower:
        deps.append('Odoo server must be running (docker-compose up)')

    if task_type == 'social_media':
        deps.append('Social media API tokens configured in .env')
        deps.append('Company_Handbook.md social media guidelines')

    if task_type == 'briefing':
        deps.append('Business_Goals.md must be populated')
        deps.append('Done/ folder for completed task history')

    if task_type == 'email' or 'email' in content_lower:
        deps.append('Email MCP server configured')
        deps.append('Gmail API credentials')

    if task.get('complexity', {}).get('is_complex'):
        deps.append('Claude CLI installed (npm install -g @anthropic-ai/claude-code)')

    if not deps:
        deps.append('None')

    return deps


def get_success_criteria(task: Dict) -> List[str]:
    """Define success criteria based on task type and action."""
    criteria = []
    task_type = task['task_type']
    action = task.get('recommended_action', '')

    if action == 'Archive':
        criteria.append('Task file moved to Done/')
        criteria.append('Dashboard updated')

    elif action == 'Escalate':
        criteria.append('Human notified of HIGH-priority item')
        criteria.append('Response drafted and approved')
        criteria.append('Action executed and archived')

    elif action in ('Review & Reply', 'Review'):
        criteria.append('Task reviewed by human')
        criteria.append('Appropriate action taken')
        criteria.append('Task archived to Done/')

    elif task_type == 'accounting':
        criteria.append('Financial data processed via Odoo')
        criteria.append('Approval obtained if above threshold')
        criteria.append('Audit trail created')

    elif task_type == 'social_media':
        criteria.append('Draft generated with platform-appropriate formatting')
        criteria.append('Draft placed in Pending_Approval/')
        criteria.append('Character limits respected')
        criteria.append('CEO approval obtained before posting')

    elif task_type == 'briefing':
        criteria.append('Briefing generated with all 9 sections')
        criteria.append('Financial data pulled from Odoo (if available)')
        criteria.append('Saved to Briefings/ with date stamp')

    elif action == 'Ralph Loop Processing':
        criteria.append('All sub-tasks completed autonomously')
        criteria.append('Completion verified via promise or file movement')
        criteria.append('No escalation required')

    else:
        criteria.append('Task processed and archived to Done/')

    return criteria


def generate_plan(task: Dict) -> str:
    """Return the full markdown text for an enhanced Gold Tier Plan file."""
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

    # Related Resources
    resources = RELATED_RESOURCES.get(task['task_type'], RELATED_RESOURCES['generic'])
    resources_md = '\n'.join(f'- `{r}`' for r in resources)

    # Estimated Time
    est_time = estimate_time(task)

    # Dependencies
    deps = get_dependencies(task)
    deps_md = '\n'.join(f'- {d}' for d in deps)

    # Success Criteria
    criteria = get_success_criteria(task)
    criteria_md = '\n'.join(f'- [ ] {c}' for c in criteria)

    # Complexity info
    complexity = task.get('complexity', {})
    complexity_md = ''
    if complexity.get('is_complex'):
        complexity_md = (
            f'\n### Complexity Assessment\n'
            f'- **Score:** {complexity["complexity_score"]}/10\n'
            f'- **Route:** Ralph Loop (autonomous processing)\n'
        )
        for reason in complexity.get('complexity_reasons', []):
            complexity_md += f'- {reason}\n'

    # Rules applied (logging enhancement)
    rules = task.get('rules_applied', [])
    rules_md = '\n'.join(f'- {r}' for r in rules) if rules else '- No special rules applied'

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
    elif action == 'Generate Social Draft':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Routed for CEO approval\n'
            '- [ ] Social media draft generated\n'
            '- [ ] Draft reviewed by CEO\n'
            '- [ ] Approved draft published\n'
            '- [ ] Engagement tracked\n'
            '- [ ] Move to Done folder'
        )
    elif action == 'Process Financial Document':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Financial threshold checked\n'
            '- [ ] Document processed via Odoo\n'
            '- [ ] Approval obtained (if required)\n'
            '- [ ] Audit trail recorded\n'
            '- [ ] Move to Done folder'
        )
    elif action == 'Generate Financial Report':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Auto-approved (internal report)\n'
            '- [ ] Data pulled from Odoo\n'
            '- [ ] Report generated\n'
            '- [ ] Saved to appropriate folder\n'
            '- [ ] Move to Done folder'
        )
    elif action == 'Generate Briefing':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Auto-approved (internal report)\n'
            '- [ ] Briefing generator triggered\n'
            '- [ ] All sections populated\n'
            '- [ ] Saved to Briefings/\n'
            '- [ ] Move to Done folder'
        )
    elif action == 'Ralph Loop Processing':
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [x] Complexity assessed (routed to Ralph Loop)\n'
            '- [ ] Ralph Loop initiated\n'
            '- [ ] All sub-tasks completed\n'
            '- [ ] Completion verified\n'
            '- [ ] Move to Done folder'
        )
    else:
        checklist = (
            '- [x] Task analysed by Orchestrator\n'
            '- [ ] Human reviews task\n'
            '- [ ] Determine and execute action\n'
            '- [ ] Move to Done folder'
        )

    plan = f"""# Action Plan -- Gold Tier

**Source Task:** `{task['source_file']}`
**Task Type:** {task['task_type'].replace('_', ' ').title()}
**Priority:** {task['priority']}
**Recommended Action:** {action}
**Approval Status:** {approval_status}
**Estimated Time:** {est_time}
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

- **Task Type:** {task['task_type'].replace('_', ' ').title()}
- **Priority Level:** {task['priority']}
- **Recommended Action:** {action}
- **Detail:** {task['action_detail']}
{complexity_md}

### Approval Decision
{reasons_md}

**Result:** {approval_status}

---

## Related Resources

{resources_md}

---

## Dependencies

{deps_md}

---

## Success Criteria

{criteria_md}

---

## Categorization Rules Applied

{rules_md}

---

## Handbook Alignment

| Rule | Status |
|------|--------|
| No auto-sending outbound messages | Compliant |
| Approval for external actions | {'Routed to Pending_Approval' if task['needs_approval'] else 'N/A -- internal action'} |
| Social media requires CEO approval | {'Compliant -- routed for approval' if task['task_type'] == 'social_media' else 'N/A'} |
| Financial threshold (${FINANCIAL_APPROVAL_THRESHOLD:,.0f}) | {'Flagged -- above threshold' if task.get('max_amount', 0) >= FINANCIAL_APPROVAL_THRESHOLD else 'N/A or below threshold'} |
| Audit trail maintained | Plan file + logs created |
| Escalate if uncertain | {'Yes -- flagged' if task['priority'] == 'HIGH' else 'N/A'} |

---

## Action Checklist

{checklist}

---

## Processing Chain

`Needs_Action/{task['source_file']}` -> `{'Pending_Approval' if task['needs_approval'] else 'Plans'}/{task['source_file'].replace('_action.md', '_plan.md')}` -> Done/

---
*Auto-generated by Orchestrator (Gold Tier) at {now_str}*
"""
    return plan

# ---------------------------------------------------------------------------
# Core processing loop
# ---------------------------------------------------------------------------


def process_task(filepath: Path, processed_tasks: Set[str]) -> bool:
    """
    Parse -> assess -> plan -> route a single task file.
    Returns True on success.
    """
    start_time = time.time()
    logger.info(f"[PROCESSING] {filepath.name}")

    # 1. Parse
    task = parse_task_file(filepath)
    if task is None:
        return False

    # 2. Assess
    task = assess_task(task)
    elapsed = time.time() - start_time
    logger.info(
        f"[ASSESSED] type={task['task_type']}  priority={task['priority']}  "
        f"action={task['recommended_action']}  "
        f"approval={'YES' if task['needs_approval'] else 'NO'}  "
        f"complexity={task['complexity']['complexity_score']}/10  "
        f"time={elapsed:.2f}s"
    )

    # Log categorization rules
    for rule in task.get('rules_applied', []):
        logger.info(f"[RULE] {rule}")

    # 3. Trigger integration hooks before generating plan
    hook_results = {}

    if task['task_type'] == 'social_media':
        hook_msg = trigger_social_media_hook(task)
        if hook_msg:
            hook_results['social_media'] = hook_msg
            logger.info(f"[HOOK] {hook_msg}")

    if task['task_type'] == 'briefing':
        hook_msg = trigger_briefing_hook(task)
        if hook_msg:
            hook_results['briefing'] = hook_msg
            logger.info(f"[HOOK] {hook_msg}")

    task['hook_results'] = hook_results

    # 4. Generate plan
    plan_content = generate_plan(task)
    plan_filename = filepath.name.replace('_action.md', '_plan.md').replace('_file_action.md', '_plan.md')
    if plan_filename == filepath.name:
        # Fallback: if no suffix matched, prefix with plan_
        plan_filename = f"plan_{filepath.name}"

    # 5. Route plan
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

    # 6. Route complex tasks to Ralph Loop (after plan is written)
    ralph_result = None
    if (task['complexity']['is_complex']
            and task['recommended_action'] == 'Ralph Loop Processing'
            and not task['needs_approval']):
        logger.info(f"[RALPH] Routing complex task to Ralph Loop...")
        ralph_result = trigger_ralph_loop(task, filepath)
        if ralph_result:
            task['ralph_result'] = ralph_result

    # 7. Move original task to Done
    done_filename = filepath.name.replace('_action.md', '_DONE.md').replace('_file_action.md', '_DONE.md')
    if done_filename == filepath.name:
        done_filename = f"DONE_{filepath.name}"
    done_path = DONE_DIR / done_filename

    total_elapsed = time.time() - start_time

    try:
        # Append a processing footer to the original content before archiving
        ralph_note = ''
        if ralph_result:
            ralph_note = (
                f"- **Ralph Loop:** status={ralph_result['status']}, "
                f"iterations={ralph_result['iterations']}\n"
            )

        footer = (
            f"\n\n---\n"
            f"**ORCHESTRATOR NOTE (Gold Tier):**\n"
            f"- **Status:** Processed\n"
            f"- **Task Type:** {task['task_type'].replace('_', ' ').title()}\n"
            f"- **Action:** {task['recommended_action']}\n"
            f"- **Priority:** {task['priority']}\n"
            f"- **Complexity:** {task['complexity']['complexity_score']}/10\n"
            f"- **Approval:** {'Required -- see Pending_Approval/' if task['needs_approval'] else 'Auto-approved -- see Plans/'}\n"
            f"{ralph_note}"
            f"- **Plan Reference:** `{route_label}/{plan_filename}`\n"
            f"- **Processing Time:** {total_elapsed:.2f}s\n"
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

    # 8. Log summary with timing
    if task['needs_approval']:
        logger.info(
            f"[APPROVAL] Plan routed to Pending_Approval -- "
            f"awaiting human review (processed in {total_elapsed:.2f}s)"
        )
    else:
        logger.info(
            f"[AUTO-APPROVED] Plan saved to Plans -- "
            f"ready for execution (processed in {total_elapsed:.2f}s)"
        )

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
        logger.info(f"[SCAN] {len(md_files)} file(s) in Needs_Action -- all already processed")
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
    logger.info("Orchestrator Started (Gold Tier)")
    logger.info(f"Watching:          {NEEDS_ACTION_DIR}")
    logger.info(f"Plans output:      {PLANS_DIR}")
    logger.info(f"Pending approval:  {PENDING_APPROVAL_DIR}")
    logger.info(f"Done archive:      {DONE_DIR}")
    logger.info(f"Logs:              {LOGS_DIR}")
    logger.info(f"Check interval:    {ORCHESTRATOR_CHECK_INTERVAL}s")
    logger.info(f"Financial threshold: ${FINANCIAL_APPROVAL_THRESHOLD:,.2f}")
    logger.info(f"Ralph Loop:        {'Available' if RALPH_LOOP_SCRIPT.exists() else 'Not found'}")
    logger.info(f"Social Poster:     {'Available' if SOCIAL_MEDIA_POSTER.exists() else 'Not found'}")
    logger.info(f"CEO Briefing:      {'Available' if CEO_BRIEFING_GENERATOR.exists() else 'Not found'}")
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
