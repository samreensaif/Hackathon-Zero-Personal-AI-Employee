"""
Approval Manager — AI Employee Vault (Silver Tier)

Completes the approval workflow defined in Company_Handbook.md:

    Pending_Approval/  ──► Human moves file to ──► Approved/  or  Rejected/
                                                       │              │
                                              Execute action     Archive + log
                                                       │
                                                    Done/

Monitors three folders in a continuous loop and takes action the moment a
file appears in Approved/ or Rejected/.

Supported action types (parsed from the approval file):
    linkedin_post      Log approval, update posts_history.json, archive to Done/
    email_reply        Send email via the Email MCP server, archive to Done/
    email_send         Same as email_reply
    linkedin_message   Log (manual send for now), archive to Done/
    other / unknown    Log and archive to Done/

Run:
    python approval_manager.py

Environment Variables (from .env):
    PENDING_APPROVAL_DIR       Folder to watch for new requests
    APPROVED_DIR               Folder the human drops approved files into
    REJECTED_DIR               Folder the human drops rejected files into
    DONE_DIR                   Archive for completed items
    LOGS_DIR                   Folder for approval_manager.log + approvals.json
    WATCHERS_DIR               Location of posts_history.json
    APPROVAL_CHECK_INTERVAL    Poll interval in seconds (default: 10)
"""

import os
import re
import json
import shutil
import subprocess
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
load_dotenv(SCRIPT_DIR / '.env')

PENDING_APPROVAL_DIR = Path(os.getenv('PENDING_APPROVAL_DIR', str(SCRIPT_DIR / 'Pending_Approval')))
APPROVED_DIR = Path(os.getenv('APPROVED_DIR', str(SCRIPT_DIR / 'Approved')))
REJECTED_DIR = Path(os.getenv('REJECTED_DIR', str(SCRIPT_DIR / 'Rejected')))
DONE_DIR = Path(os.getenv('DONE_DIR', str(SCRIPT_DIR / 'Done')))
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(SCRIPT_DIR / 'Logs')))
WATCHERS_DIR = Path(os.getenv('WATCHERS_DIR', str(SCRIPT_DIR / 'Watchers')))
REJECTED_ARCHIVE_DIR = SCRIPT_DIR / 'Rejected_Archive'

EMAIL_MCP_SERVER = SCRIPT_DIR / 'MCP_Servers' / 'email_mcp_server.py'

APPROVAL_CHECK_INTERVAL = int(os.getenv('APPROVAL_CHECK_INTERVAL', 10))

AUDIT_LOG_PATH = LOGS_DIR / 'approvals.json'
POSTS_HISTORY_PATH = WATCHERS_DIR / 'posts_history.json'

# Ensure directories
for d in (PENDING_APPROVAL_DIR, APPROVED_DIR, REJECTED_DIR,
          DONE_DIR, LOGS_DIR, REJECTED_ARCHIVE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'approval_manager.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Audit log  (Logs/approvals.json)
# ---------------------------------------------------------------------------


def load_audit_log() -> List[Dict]:
    if AUDIT_LOG_PATH.exists():
        try:
            with open(AUDIT_LOG_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_audit_log(entries: List[Dict]):
    try:
        with open(AUDIT_LOG_PATH, 'w') as f:
            json.dump(entries, f, indent=2)
    except Exception as e:
        logger.error(f'[AUDIT] Failed to save audit log: {e}')


def audit(filename: str, decision: str, action_type: str, detail: str):
    """Append a single entry to the audit log."""
    entries = load_audit_log()
    entries.append({
        'timestamp': datetime.now().isoformat(),
        'filename': filename,
        'decision': decision,
        'action_type': action_type,
        'detail': detail,
    })
    save_audit_log(entries)
    logger.info(f'[AUDIT] {decision.upper()} | {action_type} | {filename}')

# ---------------------------------------------------------------------------
# Posts history helper  (shared with linkedin_poster.py)
# ---------------------------------------------------------------------------


def update_posts_history(filename: str, status: str):
    """Update the linkedin poster's posts_history.json for a given filename."""
    if not POSTS_HISTORY_PATH.exists():
        return
    try:
        with open(POSTS_HISTORY_PATH, 'r') as f:
            history = json.load(f)

        for post in history.get('posts', []):
            if post.get('filename') == filename:
                post['status'] = status
                ts_key = f'{status}_at'
                post[ts_key] = datetime.now().isoformat()
                logger.info(f'[HISTORY] Updated posts_history: {filename} → {status}')
                break

        with open(POSTS_HISTORY_PATH, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.warning(f'[HISTORY] Could not update posts_history.json: {e}')

# ---------------------------------------------------------------------------
# Approval-file parser
# ---------------------------------------------------------------------------


def parse_approval_file(filepath: Path) -> Dict:
    """
    Read an approval .md file and extract structured fields.

    Handles formats produced by:
      - orchestrator.py   (plans with **Recommended Action:** etc.)
      - linkedin_poster.py (drafts with **Action Type:** linkedin_post)
    """
    content = filepath.read_text(encoding='utf-8')

    fields: Dict = {
        'raw_content': content,
        'action_type': 'other',
        'to': '',
        'subject': '',
        'body': '',
        'urgency': 'low',
    }

    # Action Type (linkedin_poster format)
    m = re.search(r'\*\*Action Type:\*\*\s*(.+)', content)
    if m:
        fields['action_type'] = m.group(1).strip().lower()

    # Recommended Action (orchestrator format) — map to action types
    m = re.search(r'\*\*Recommended Action:\*\*\s*(.+)', content)
    if m:
        action = m.group(1).strip().lower()
        if 'reply' in action or 'send' in action:
            fields['action_type'] = 'email_reply'
        elif 'archive' in action:
            fields['action_type'] = 'archive'
        elif 'escalate' in action:
            fields['action_type'] = 'escalate'

    # To / Recipient
    m = re.search(r'\*\*To/Recipient:\*\*\s*(.+)', content)
    if m:
        fields['to'] = m.group(1).strip()

    # Subject / Context
    m = re.search(r'\*\*Subject(?:/Context)?:\*\*\s*(.+)', content)
    if m:
        fields['subject'] = m.group(1).strip()

    # Source Task (orchestrator links back to the original)
    m = re.search(r'\*\*Source Task:\*\*\s*`?(.+?)`?\s*$', content, re.MULTILINE)
    if m:
        fields['source_task'] = m.group(1).strip()

    # Urgency
    m = re.search(r'\*\*Urgency:\*\*\s*(\w+)', content)
    if m:
        fields['urgency'] = m.group(1).strip().lower()

    # Draft Content block (between "## Draft Content" and next "---" or "##")
    m = re.search(
        r'## Draft Content\s*\n(.*?)(?=\n---|\n## |\Z)', content, re.DOTALL,
    )
    if m:
        fields['body'] = m.group(1).strip()

    # Priority (orchestrator)
    m = re.search(r'\*\*Priority:\*\*\s*(\w+)', content)
    if m:
        fields['priority'] = m.group(1).strip().lower()

    return fields

# ---------------------------------------------------------------------------
# Action executors
# ---------------------------------------------------------------------------


def execute_linkedin_post(filepath: Path, fields: Dict) -> bool:
    """Handle an approved LinkedIn post."""
    logger.info(f'[LINKEDIN] Approved post: {filepath.name}')
    logger.info('[LINKEDIN] Post logged — manual publish or future automation required')
    update_posts_history(filepath.name, 'approved')
    return True


def execute_email(filepath: Path, fields: Dict) -> bool:
    """
    Send an approved email via the Email MCP server.

    Falls back to logging if the MCP server script is missing or the call fails.
    """
    to = fields.get('to', '')
    subject = fields.get('subject', '')
    body = fields.get('body', '')

    if not to or to.lower() in ('linkedin feed (public)', ''):
        logger.warning('[EMAIL] No valid recipient — logging only')
        return True

    if not EMAIL_MCP_SERVER.exists():
        logger.warning(f'[EMAIL] MCP server not found at {EMAIL_MCP_SERVER} — logging only')
        return True

    logger.info(f'[EMAIL] Sending via MCP: to={to} subject="{subject[:50]}"')

    # Build a JSON-RPC tools/call message for the MCP server
    rpc_message = json.dumps({
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'tools/call',
        'params': {
            'name': 'send_email',
            'arguments': {
                'to': to,
                'subject': subject,
                'body': body,
            },
        },
    }) + '\n'

    try:
        proc = subprocess.run(
            [sys.executable, str(EMAIL_MCP_SERVER)],
            input=rpc_message,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if proc.returncode != 0:
            logger.error(f'[EMAIL] MCP server exited with code {proc.returncode}')
            logger.error(f'[EMAIL] stderr: {proc.stderr[:500]}')
            return False

        # Parse response
        for line in proc.stdout.strip().splitlines():
            try:
                resp = json.loads(line)
                if 'error' in resp:
                    logger.error(f'[EMAIL] MCP error: {resp["error"]}')
                    return False
                result = resp.get('result', {})
                if result.get('isError'):
                    logger.error(f'[EMAIL] Tool error: {result}')
                    return False
                logger.info(f'[EMAIL] Success: {line[:200]}')
                return True
            except json.JSONDecodeError:
                continue

        logger.warning('[EMAIL] No parseable response from MCP server')
        return True  # treat as success — the server may have sent the email

    except subprocess.TimeoutExpired:
        logger.error('[EMAIL] MCP server timed out after 60s')
        return False
    except Exception as e:
        logger.error(f'[EMAIL] Failed to call MCP server: {e}')
        return False


def execute_action(filepath: Path, fields: Dict) -> bool:
    """
    Dispatch to the correct executor based on action_type.

    Returns True if the action succeeded (file should go to Done/).
    Returns False if the action failed (file should go back to Pending_Approval/).
    """
    action_type = fields.get('action_type', 'other')

    if action_type == 'linkedin_post':
        return execute_linkedin_post(filepath, fields)

    if action_type in ('email_reply', 'email_send'):
        return execute_email(filepath, fields)

    if action_type == 'linkedin_message':
        logger.info(f'[LINKEDIN_MSG] Approved — manual send required: {filepath.name}')
        return True

    if action_type == 'archive':
        logger.info(f'[ARCHIVE] Auto-archive approved: {filepath.name}')
        return True

    if action_type == 'escalate':
        logger.info(f'[ESCALATE] Escalation noted: {filepath.name}')
        return True

    logger.info(f'[OTHER] Action type "{action_type}" — logged: {filepath.name}')
    return True

# ---------------------------------------------------------------------------
# Move helpers
# ---------------------------------------------------------------------------


def move_to_done(filepath: Path, fields: Dict, decision: str):
    """Move an approved/processed file to Done/ with a footer."""
    done_name = filepath.name
    done_path = DONE_DIR / done_name
    if done_path.exists():
        done_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{done_name}"
        done_path = DONE_DIR / done_name

    footer = (
        f'\n\n---\n'
        f'**APPROVAL MANAGER NOTE:**\n'
        f'- **Decision:** {decision}\n'
        f'- **Action Type:** {fields.get("action_type", "unknown")}\n'
        f'- **Processed:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
    )

    try:
        content = filepath.read_text(encoding='utf-8')
        with open(done_path, 'w', encoding='utf-8') as f:
            f.write(content + footer)
        filepath.unlink()
        logger.info(f'[DONE] Archived: Done/{done_name}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to archive {filepath.name}: {e}')


def move_to_rejected_archive(filepath: Path, fields: Dict):
    """Move a rejected file to Rejected_Archive/ with timestamp."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"{ts}_REJECTED_{filepath.name}"
    archive_path = REJECTED_ARCHIVE_DIR / archive_name

    footer = (
        f'\n\n---\n'
        f'**REJECTION NOTE:**\n'
        f'- **Rejected at:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'- **Action Type:** {fields.get("action_type", "unknown")}\n'
        f'- **Archived to:** Rejected_Archive/{archive_name}\n'
    )

    try:
        content = filepath.read_text(encoding='utf-8')
        with open(archive_path, 'w', encoding='utf-8') as f:
            f.write(content + footer)
        filepath.unlink()
        logger.info(f'[REJECTED] Archived: Rejected_Archive/{archive_name}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to archive rejected file: {e}')


def move_back_to_pending(filepath: Path, error_msg: str):
    """On action failure, return the file to Pending_Approval/ with an error note."""
    pending_path = PENDING_APPROVAL_DIR / filepath.name

    error_note = (
        f'\n\n---\n'
        f'**ACTION FAILED — RETURNED FOR REVIEW:**\n'
        f'- **Error:** {error_msg}\n'
        f'- **Failed at:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'- **Please review and re-approve, or reject.**\n'
    )

    try:
        content = filepath.read_text(encoding='utf-8')
        with open(pending_path, 'w', encoding='utf-8') as f:
            f.write(content + error_note)
        filepath.unlink()
        logger.warning(f'[RETURNED] Moved back to Pending_Approval: {filepath.name}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to return {filepath.name} to pending: {e}')

# ---------------------------------------------------------------------------
# Folder scanners
# ---------------------------------------------------------------------------


def process_approved(known_approved: Set[str]) -> Set[str]:
    """Process any new files in Approved/."""
    files = sorted(APPROVED_DIR.glob('*.md'))
    for filepath in files:
        if filepath.name in known_approved:
            continue

        logger.info(f'[APPROVED] New file detected: {filepath.name}')
        known_approved.add(filepath.name)

        try:
            fields = parse_approval_file(filepath)
            action_type = fields.get('action_type', 'other')
            logger.info(
                f'[APPROVED] type={action_type} '
                f'to="{fields.get("to", "")}" '
                f'urgency={fields.get("urgency", "?")}'
            )

            success = execute_action(filepath, fields)

            if success:
                audit(filepath.name, 'approved', action_type, 'Action executed successfully')
                move_to_done(filepath, fields, 'APPROVED')
            else:
                audit(filepath.name, 'approved_failed', action_type, 'Action execution failed — returned to pending')
                move_back_to_pending(filepath, 'Action execution failed — see approval_manager.log')

        except Exception as e:
            logger.error(f'[ERROR] Processing approved file {filepath.name}: {e}')
            audit(filepath.name, 'error', 'unknown', str(e))

    return known_approved


def process_rejected(known_rejected: Set[str]) -> Set[str]:
    """Process any new files in Rejected/."""
    files = sorted(REJECTED_DIR.glob('*.md'))
    for filepath in files:
        if filepath.name in known_rejected:
            continue

        logger.info(f'[REJECTED] New file detected: {filepath.name}')
        known_rejected.add(filepath.name)

        try:
            fields = parse_approval_file(filepath)
            action_type = fields.get('action_type', 'other')

            # Update linkedin history if relevant
            if action_type == 'linkedin_post':
                update_posts_history(filepath.name, 'rejected')

            audit(filepath.name, 'rejected', action_type, 'Rejected by human reviewer')
            move_to_rejected_archive(filepath, fields)

        except Exception as e:
            logger.error(f'[ERROR] Processing rejected file {filepath.name}: {e}')
            audit(filepath.name, 'error', 'unknown', str(e))

    return known_rejected


def count_pending() -> int:
    """Return current count of files in Pending_Approval/."""
    return len(list(PENDING_APPROVAL_DIR.glob('*.md')))

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_manager():
    """Run the approval manager loop continuously."""
    logger.info('=' * 60)
    logger.info('Approval Manager Started (Silver Tier)')
    logger.info(f'Pending Approval:  {PENDING_APPROVAL_DIR}')
    logger.info(f'Approved:          {APPROVED_DIR}')
    logger.info(f'Rejected:          {REJECTED_DIR}')
    logger.info(f'Done:              {DONE_DIR}')
    logger.info(f'Rejected Archive:  {REJECTED_ARCHIVE_DIR}')
    logger.info(f'Audit log:         {AUDIT_LOG_PATH}')
    logger.info(f'Check interval:    {APPROVAL_CHECK_INTERVAL}s')
    logger.info('=' * 60)

    # Track what we've already seen so we don't re-process
    known_approved: Set[str] = set()
    known_rejected: Set[str] = set()

    try:
        while True:
            now_str = datetime.now().strftime('%H:%M:%S')
            pending = count_pending()

            # Status line
            if pending > 0:
                logger.info(
                    f'[{now_str}] Pending: {pending} item(s) awaiting approval'
                )
            else:
                logger.info(f'[{now_str}] No pending approvals')

            # Process approvals and rejections
            known_approved = process_approved(known_approved)
            known_rejected = process_rejected(known_rejected)

            time.sleep(APPROVAL_CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info('[STOP] Approval Manager stopped by user')
    except Exception as e:
        logger.error(f'[FATAL] Unexpected error: {e}')
        import traceback
        logger.error(f'[FATAL] {traceback.format_exc()}')


if __name__ == '__main__':
    run_manager()
