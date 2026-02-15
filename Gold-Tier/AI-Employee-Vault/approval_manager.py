"""
Approval Manager -- AI Employee Vault (Gold Tier)

Completes the approval workflow defined in Company_Handbook.md:

    Pending_Approval/  --> Human moves file to --> Approved/  or  Rejected/
                                                       |              |
                                              Execute action     Archive + log
                                                       |
                                                    Done/

Monitors three folders in a continuous loop and takes action the moment a
file appears in Approved/ or Rejected/.

Supported action types (parsed from the approval file):
    linkedin_post      Log approval, update posts_history.json, archive to Done/
    email_reply        Send email via the Email MCP server, archive to Done/
    email_send         Same as email_reply
    linkedin_message   Log (manual send for now), archive to Done/
    social_media_post  Generic social media post (legacy)
    facebook_post      Approved Facebook post -- create summary, archive to Done/
    instagram_post     Approved Instagram post -- create summary, archive to Done/
    twitter_post       Approved Twitter/X post -- create summary, archive to Done/
    odoo_invoice       Create invoice in Odoo via Odoo MCP server
    odoo_payment       Record payment in Odoo via Odoo MCP server
    odoo_expense       Record expense in Odoo via Odoo MCP server
    ceo_briefing_send  Send CEO briefing via Email MCP server
    archive            Auto-archive
    escalate           Escalation noted
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
    SOCIAL_MEDIA_DIR           Social media archive folder
    CEO_EMAIL                  Email address for CEO briefings
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
SOCIAL_MEDIA_DIR = Path(os.getenv('SOCIAL_MEDIA_DIR', str(SCRIPT_DIR / 'Social_Media')))
REJECTED_ARCHIVE_DIR = SCRIPT_DIR / 'Rejected_Archive'

EMAIL_MCP_SERVER = SCRIPT_DIR / 'MCP_Servers' / 'email_mcp_server.py'
ODOO_MCP_SERVER = SCRIPT_DIR / 'MCP_Servers' / 'odoo_mcp_server.py'

APPROVAL_CHECK_INTERVAL = int(os.getenv('APPROVAL_CHECK_INTERVAL', 10))
CEO_EMAIL = os.getenv('CEO_EMAIL', '') or ''

AUDIT_LOG_PATH = LOGS_DIR / 'approvals.json'
POSTS_HISTORY_PATH = WATCHERS_DIR / 'posts_history.json'
SOCIAL_POSTS_HISTORY_PATH = WATCHERS_DIR / 'social_posts_history.json'

# Ensure directories
for d in (PENDING_APPROVAL_DIR, APPROVED_DIR, REJECTED_DIR,
          DONE_DIR, LOGS_DIR, REJECTED_ARCHIVE_DIR, SOCIAL_MEDIA_DIR):
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
# Audit log  (Logs/approvals.json) -- Enhanced for Gold Tier
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


def audit(filename: str, decision: str, action_type: str, detail: str,
          mcp_server: str = '', execution_time: float = 0.0,
          success: bool = True):
    """Append a single entry to the audit log with Gold Tier details."""
    entries = load_audit_log()
    entry = {
        'timestamp': datetime.now().isoformat(),
        'filename': filename,
        'decision': decision,
        'action_type': action_type,
        'detail': detail,
        'success': success,
    }
    if mcp_server:
        entry['mcp_server'] = mcp_server
    if execution_time > 0:
        entry['execution_time_seconds'] = round(execution_time, 2)
    entries.append(entry)
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
                logger.info(f'[HISTORY] Updated posts_history: {filename} -> {status}')
                break

        with open(POSTS_HISTORY_PATH, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.warning(f'[HISTORY] Could not update posts_history.json: {e}')


def update_social_posts_history(filename: str, status: str):
    """Update the social media poster's social_posts_history.json."""
    if not SOCIAL_POSTS_HISTORY_PATH.exists():
        return
    try:
        with open(SOCIAL_POSTS_HISTORY_PATH, 'r') as f:
            history = json.load(f)

        for post in history.get('posts', []):
            if post.get('filename') == filename:
                post['status'] = status
                ts_key = f'{status}_at'
                post[ts_key] = datetime.now().isoformat()
                logger.info(f'[HISTORY] Updated social_posts_history: {filename} -> {status}')
                break

        with open(SOCIAL_POSTS_HISTORY_PATH, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.warning(f'[HISTORY] Could not update social_posts_history.json: {e}')

# ---------------------------------------------------------------------------
# Odoo MCP client helper
# ---------------------------------------------------------------------------


def call_odoo_mcp(tool_name: str, arguments: dict) -> Dict:
    """
    Call an Odoo MCP server tool via JSON-RPC over stdio.

    Returns the parsed tool result dict on success.
    Raises RuntimeError on failure.
    """
    if not ODOO_MCP_SERVER.exists():
        raise RuntimeError(
            f'Odoo MCP server not found at {ODOO_MCP_SERVER}. '
            'Ensure Gold Tier MCP_Servers/ is set up.'
        )

    # Build the JSON-RPC messages: initialize handshake + tool call
    init_msg = json.dumps({
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'approval-manager', 'version': '1.0.0'},
        },
    })
    initialized_msg = json.dumps({
        'jsonrpc': '2.0',
        'method': 'notifications/initialized',
        'params': {},
    })
    tool_msg = json.dumps({
        'jsonrpc': '2.0',
        'id': 2,
        'method': 'tools/call',
        'params': {
            'name': tool_name,
            'arguments': arguments,
        },
    })

    rpc_input = f'{init_msg}\n{initialized_msg}\n{tool_msg}\n'

    logger.info(f'[ODOO_MCP] Calling tool={tool_name} args={json.dumps(arguments)[:200]}')

    proc = subprocess.run(
        [sys.executable, str(ODOO_MCP_SERVER)],
        input=rpc_input,
        capture_output=True,
        text=True,
        timeout=60,
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f'Odoo MCP server exited with code {proc.returncode}: '
            f'{proc.stderr[:300]}'
        )

    # Parse the response lines -- find the tool call response (id=2)
    for line in proc.stdout.strip().splitlines():
        try:
            resp = json.loads(line)
        except json.JSONDecodeError:
            continue

        if resp.get('id') != 2:
            continue

        # Check for JSON-RPC level error
        if 'error' in resp:
            raise RuntimeError(
                f'Odoo MCP error: {resp["error"].get("message", resp["error"])}'
            )

        result = resp.get('result', {})

        # Check for tool-level error
        if result.get('isError'):
            content = result.get('content', [{}])
            error_text = content[0].get('text', 'Unknown error') if content else 'Unknown error'
            raise RuntimeError(f'Odoo tool error: {error_text}')

        # Extract the tool result from content
        content = result.get('content', [])
        if content:
            text = content[0].get('text', '{}')
            try:
                tool_result = json.loads(text)
            except json.JSONDecodeError:
                tool_result = {'raw': text}

            if 'error' in tool_result:
                raise RuntimeError(f'Odoo operation error: {tool_result["error"]}')

            logger.info(f'[ODOO_MCP] Success: {json.dumps(tool_result)[:200]}')
            return tool_result

    raise RuntimeError('No parseable response from Odoo MCP server')


# ---------------------------------------------------------------------------
# Approval-file parser -- Enhanced for Gold Tier
# ---------------------------------------------------------------------------


def parse_approval_file(filepath: Path) -> Dict:
    """
    Read an approval .md file and extract structured fields.

    Handles formats produced by:
      - orchestrator.py     (plans with **Recommended Action:** etc.)
      - linkedin_poster.py  (drafts with **Action Type:** linkedin_post)
      - social_media_poster.py (drafts with **Action Type:** facebook_post etc.)
      - ceo_briefing_generator.py (briefings with **Action Type:** ceo_briefing_send)

    Also supports embedded JSON parameter blocks:
        ```json
        {"customer_name": "Acme Corp", "amount": 500}
        ```
    """
    content = filepath.read_text(encoding='utf-8')

    fields: Dict = {
        'raw_content': content,
        'action_type': 'other',
        'to': '',
        'subject': '',
        'body': '',
        'urgency': 'low',
        'platform': '',
        'parameters': {},
    }

    # --- Action Type ---
    m = re.search(r'\*\*Action Type:\*\*\s*(.+)', content)
    if m:
        fields['action_type'] = m.group(1).strip().lower()

    # Recommended Action (orchestrator format) -- map to action types
    m = re.search(r'\*\*Recommended Action:\*\*\s*(.+)', content)
    if m:
        action = m.group(1).strip().lower()
        if 'reply' in action or 'send' in action:
            if fields['action_type'] == 'other':
                fields['action_type'] = 'email_reply'
        elif 'archive' in action:
            fields['action_type'] = 'archive'
        elif 'escalate' in action:
            fields['action_type'] = 'escalate'
        elif 'financial' in action or 'invoice' in action:
            if fields['action_type'] == 'other':
                fields['action_type'] = 'odoo_invoice'
        elif 'social' in action:
            if fields['action_type'] == 'other':
                fields['action_type'] = 'social_media_post'
        elif 'briefing' in action:
            if fields['action_type'] == 'other':
                fields['action_type'] = 'ceo_briefing_send'

    # --- Platform detection (for social media) ---
    m = re.search(r'\*\*Platform:\*\*\s*(.+)', content)
    if m:
        fields['platform'] = m.group(1).strip().lower()

    # Infer platform from action type
    if fields['action_type'] in ('facebook_post', 'instagram_post', 'twitter_post'):
        fields['platform'] = fields['action_type'].replace('_post', '')

    # Infer platform from filename
    name_lower = filepath.name.lower()
    if not fields['platform']:
        for plat in ('facebook', 'instagram', 'twitter'):
            if plat in name_lower:
                fields['platform'] = plat
                if fields['action_type'] in ('social_media_post', 'other'):
                    fields['action_type'] = f'{plat}_post'
                break

    # --- To / Recipient ---
    m = re.search(r'\*\*To/Recipient:\*\*\s*(.+)', content)
    if m:
        fields['to'] = m.group(1).strip()

    # --- Subject / Context ---
    m = re.search(r'\*\*Subject(?:/Context)?:\*\*\s*(.+)', content)
    if m:
        fields['subject'] = m.group(1).strip()

    # --- Source Task ---
    m = re.search(r'\*\*Source Task:\*\*\s*`?(.+?)`?\s*$', content, re.MULTILINE)
    if m:
        fields['source_task'] = m.group(1).strip()

    # --- Urgency ---
    m = re.search(r'\*\*Urgency:\*\*\s*(\w+)', content)
    if m:
        fields['urgency'] = m.group(1).strip().lower()

    # --- Priority ---
    m = re.search(r'\*\*Priority:\*\*\s*(\w+)', content)
    if m:
        fields['priority'] = m.group(1).strip().lower()

    # --- Draft Content block ---
    m = re.search(
        r'## Draft Content\s*\n(.*?)(?=\n---|\n## |\Z)', content, re.DOTALL,
    )
    if m:
        fields['body'] = m.group(1).strip()

    # --- Post Content block (social media poster format) ---
    if not fields['body']:
        m = re.search(
            r'## Post Content\s*\n(.*?)(?=\n---|\n## |\Z)', content, re.DOTALL,
        )
        if m:
            fields['body'] = m.group(1).strip()

    # --- Briefing Content block (uses \n--- as boundary, not \n## ,
    #     because briefing content itself contains ## headings) ---
    if not fields['body']:
        m = re.search(
            r'## Briefing Content\s*\n(.*?)(?=\n---|\Z)', content, re.DOTALL,
        )
        if m:
            fields['body'] = m.group(1).strip()

    # --- Financial parameters ---
    m = re.search(r'\*\*Customer(?:/Company)?:\*\*\s*(.+)', content)
    if m:
        fields['parameters']['customer_name'] = m.group(1).strip()

    m = re.search(r'\*\*Amount:\*\*\s*\$?([\d,]+\.?\d*)', content)
    if m:
        fields['parameters']['amount'] = float(m.group(1).replace(',', ''))

    m = re.search(r'\*\*Description:\*\*\s*(.+)', content)
    if m:
        fields['parameters']['description'] = m.group(1).strip()

    m = re.search(r'\*\*Due Date:\*\*\s*(\d{4}-\d{2}-\d{2})', content)
    if m:
        fields['parameters']['due_date'] = m.group(1)

    m = re.search(r'\*\*Invoice ID:\*\*\s*(\d+)', content)
    if m:
        fields['parameters']['invoice_id'] = int(m.group(1))

    m = re.search(r'\*\*Payment Date:\*\*\s*(\d{4}-\d{2}-\d{2})', content)
    if m:
        fields['parameters']['payment_date'] = m.group(1)

    m = re.search(r'\*\*Category:\*\*\s*(.+)', content)
    if m:
        fields['parameters']['category'] = m.group(1).strip()

    m = re.search(r'\*\*Reference:\*\*\s*(.+)', content)
    if m:
        fields['parameters']['reference'] = m.group(1).strip()

    m = re.search(r'\*\*Memo:\*\*\s*(.+)', content)
    if m:
        fields['parameters']['memo'] = m.group(1).strip()

    # --- Week Of (for CEO briefing subject) ---
    m = re.search(r'\*\*Week Of:\*\*\s*(.+)', content)
    if m:
        fields['parameters']['week_of'] = m.group(1).strip()

    # --- Embedded JSON parameter block ---
    json_match = re.search(r'```json\s*\n(.*?)\n```', content, re.DOTALL)
    if json_match:
        try:
            json_params = json.loads(json_match.group(1))
            if isinstance(json_params, dict):
                # JSON block values override markdown-parsed values
                fields['parameters'].update(json_params)
                logger.info(f'[PARSE] Extracted JSON parameters: {list(json_params.keys())}')
        except json.JSONDecodeError as e:
            logger.warning(f'[PARSE] Invalid JSON block in {filepath.name}: {e}')

    return fields


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------

REQUIRED_PARAMS = {
    'odoo_invoice': ['customer_name', 'amount'],
    'odoo_payment': ['invoice_id', 'amount'],
    'odoo_expense': ['description', 'amount'],
    'ceo_briefing_send': [],  # body is required but checked separately
}


def validate_parameters(action_type: str, fields: Dict) -> Optional[str]:
    """
    Validate that required parameters exist for the given action type.
    Returns None if valid, or an error message string if invalid.
    """
    required = REQUIRED_PARAMS.get(action_type)
    if required is None:
        return None  # No validation rules for this action type

    params = fields.get('parameters', {})
    missing = [p for p in required if p not in params or params[p] in (None, '', 0)]

    if missing:
        return f'Missing required parameters for {action_type}: {", ".join(missing)}'

    if action_type == 'ceo_briefing_send' and not fields.get('body'):
        return 'Missing briefing content (body) for ceo_briefing_send'

    return None


# ---------------------------------------------------------------------------
# Action executors -- Silver Tier (preserved)
# ---------------------------------------------------------------------------


def execute_linkedin_post(filepath: Path, fields: Dict) -> bool:
    """Handle an approved LinkedIn post."""
    logger.info(f'[LINKEDIN] Approved post: {filepath.name}')
    logger.info('[LINKEDIN] Post logged -- manual publish or future automation required')
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
        logger.warning('[EMAIL] No valid recipient -- logging only')
        return True

    if not EMAIL_MCP_SERVER.exists():
        logger.warning(f'[EMAIL] MCP server not found at {EMAIL_MCP_SERVER} -- logging only')
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
        return True  # treat as success -- the server may have sent the email

    except subprocess.TimeoutExpired:
        logger.error('[EMAIL] MCP server timed out after 60s')
        return False
    except Exception as e:
        logger.error(f'[EMAIL] Failed to call MCP server: {e}')
        return False


# ---------------------------------------------------------------------------
# Action executors -- Gold Tier: Odoo
# ---------------------------------------------------------------------------


def execute_odoo_invoice(filepath: Path, fields: Dict) -> bool:
    """Create an invoice in Odoo via the Odoo MCP server."""
    params = fields.get('parameters', {})
    customer_name = params.get('customer_name', '')
    amount = params.get('amount', 0)
    description = params.get('description', 'Invoice')
    due_date = params.get('due_date', '')

    logger.info(
        f'[ODOO_INVOICE] Creating invoice: '
        f'customer={customer_name} amount=${amount} desc="{description[:40]}"'
    )

    args = {
        'customer_name': customer_name,
        'amount': amount,
        'description': description,
    }
    if due_date:
        args['due_date'] = due_date

    result = call_odoo_mcp('create_invoice', args)

    logger.info(
        f'[ODOO_INVOICE] Created: id={result.get("invoice_id")} '
        f'number={result.get("invoice_number")} '
        f'total=${result.get("amount_total", 0)}'
    )
    return True


def execute_odoo_payment(filepath: Path, fields: Dict) -> bool:
    """Record a payment in Odoo via the Odoo MCP server."""
    params = fields.get('parameters', {})
    invoice_id = params.get('invoice_id')
    amount = params.get('amount', 0)
    payment_date = params.get('payment_date', '')
    memo = params.get('memo', '')

    logger.info(
        f'[ODOO_PAYMENT] Recording payment: '
        f'invoice_id={invoice_id} amount=${amount}'
    )

    args = {
        'invoice_id': int(invoice_id),
        'amount': float(amount),
    }
    if payment_date:
        args['payment_date'] = payment_date
    if memo:
        args['memo'] = memo

    result = call_odoo_mcp('record_payment', args)

    logger.info(
        f'[ODOO_PAYMENT] Recorded: invoice={result.get("invoice_number")} '
        f'paid=${result.get("amount_paid", 0)} '
        f'remaining=${result.get("amount_remaining", 0)} '
        f'state={result.get("payment_state")}'
    )
    return True


def execute_odoo_expense(filepath: Path, fields: Dict) -> bool:
    """Record an expense in Odoo via the Odoo MCP server."""
    params = fields.get('parameters', {})
    description = params.get('description', 'Expense')
    amount = params.get('amount', 0)
    expense_date = params.get('date', '')
    category = params.get('category', '')
    reference = params.get('reference', '')

    logger.info(
        f'[ODOO_EXPENSE] Creating expense: '
        f'desc="{description[:40]}" amount=${amount}'
    )

    args = {
        'description': description,
        'amount': float(amount),
    }
    if expense_date:
        args['date'] = expense_date
    if category:
        args['category'] = category
    if reference:
        args['reference'] = reference

    result = call_odoo_mcp('create_expense', args)

    logger.info(
        f'[ODOO_EXPENSE] Created: id={result.get("expense_id")} '
        f'bill={result.get("bill_number")} '
        f'amount=${result.get("amount", 0)}'
    )
    return True


# ---------------------------------------------------------------------------
# Action executors -- Gold Tier: Social Media
# ---------------------------------------------------------------------------


def execute_platform_post(filepath: Path, fields: Dict) -> bool:
    """
    Handle an approved social media post for a specific platform.

    Creates a summary file in Social_Media/ with "APPROVED - Ready to post"
    status and the full post content for easy copy-paste.
    """
    platform = fields.get('platform', 'unknown')
    body = fields.get('body', '')

    logger.info(f'[SOCIAL] Approved {platform.upper()} post: {filepath.name}')

    # Create the approved summary in Social_Media/
    now = datetime.now()
    summary_name = f"APPROVED_{platform.upper()}_{now.strftime('%Y-%m-%d_%H-%M')}.md"
    summary_path = SOCIAL_MEDIA_DIR / summary_name

    summary_content = f"""# APPROVED - Ready to Post

**Platform:** {platform.title()}
**Status:** APPROVED - Ready to post manually
**Approved:** {now.strftime('%Y-%m-%d %H:%M:%S')}
**Source File:** `{filepath.name}`

---

## Post Content (Copy-Paste Ready)

{body}

---

## Posting Instructions

"""
    if platform == 'facebook':
        summary_content += (
            '1. Go to your Facebook Business Page\n'
            '2. Click "Create Post"\n'
            '3. Paste the content above\n'
            '4. Review and click "Post"\n'
            '5. Record engagement metrics later\n'
        )
    elif platform == 'instagram':
        summary_content += (
            '1. Open Instagram (mobile app recommended)\n'
            '2. Tap "+" to create a new post\n'
            '3. Select image/video content\n'
            '4. Paste the caption from above\n'
            '5. Add location and tags as appropriate\n'
            '6. Tap "Share"\n'
        )
    elif platform == 'twitter':
        summary_content += (
            '1. Go to twitter.com or open the X app\n'
            '2. Click "Post" / compose new tweet\n'
            '3. Paste the content above (280 char limit)\n'
            '4. Review and click "Post"\n'
            '5. Monitor replies and engagement\n'
        )
    else:
        summary_content += (
            f'1. Go to the {platform.title()} platform\n'
            '2. Create a new post\n'
            '3. Paste the content above\n'
            '4. Review and publish\n'
        )

    summary_content += (
        f'\n---\n'
        f'*Generated by Approval Manager (Gold Tier) at {now.strftime("%Y-%m-%d %H:%M:%S")}*\n'
    )

    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(summary_content)
        logger.info(f'[SOCIAL] Summary created: Social_Media/{summary_name}')
    except Exception as e:
        logger.error(f'[SOCIAL] Failed to write summary: {e}')

    # Update social posts history
    update_social_posts_history(filepath.name, 'approved')

    logger.info(
        f'[SOCIAL] {platform.upper()} post approved and ready for manual posting. '
        f'See Social_Media/{summary_name}'
    )
    return True


# ---------------------------------------------------------------------------
# Action executors -- Gold Tier: CEO Briefing
# ---------------------------------------------------------------------------


def execute_ceo_briefing_send(filepath: Path, fields: Dict) -> bool:
    """Send a CEO briefing via email using the Email MCP server."""
    body = fields.get('body', '')
    params = fields.get('parameters', {})
    week_of = params.get('week_of', datetime.now().strftime('%Y-%m-%d'))

    # Determine recipient
    to = fields.get('to', '') or CEO_EMAIL
    if not to:
        logger.warning(
            '[BRIEFING] No CEO_EMAIL configured in .env and no recipient '
            'in approval file -- logging only'
        )
        logger.info('[BRIEFING] Briefing approved but no email sent (no recipient)')
        return True

    subject = f'Weekly Business Briefing - Week of {week_of}'

    logger.info(f'[BRIEFING] Sending briefing to {to}: "{subject}"')

    # Use the email MCP server
    if not EMAIL_MCP_SERVER.exists():
        logger.warning(
            f'[BRIEFING] Email MCP server not found at {EMAIL_MCP_SERVER} '
            '-- logging only'
        )
        return True

    # Reuse the email sending infrastructure
    email_fields = {
        'to': to,
        'subject': subject,
        'body': body,
    }
    success = execute_email(filepath, email_fields)

    if success:
        logger.info(f'[BRIEFING] CEO briefing emailed successfully to {to}')
    else:
        logger.error(f'[BRIEFING] Failed to email CEO briefing to {to}')

    return success


# ---------------------------------------------------------------------------
# Action dispatcher
# ---------------------------------------------------------------------------


def execute_action(filepath: Path, fields: Dict) -> bool:
    """
    Dispatch to the correct executor based on action_type.

    Returns True if the action succeeded (file should go to Done/).
    Returns False if the action failed (file should go back to Pending_Approval/).
    """
    action_type = fields.get('action_type', 'other')
    start_time = time.time()

    # --- Validate parameters for action types that need them ---
    validation_error = validate_parameters(action_type, fields)
    if validation_error:
        logger.error(f'[VALIDATION] {validation_error}')
        return False

    try:
        # --- Silver Tier action types ---
        if action_type == 'linkedin_post':
            return execute_linkedin_post(filepath, fields)

        if action_type in ('email_reply', 'email_send'):
            return execute_email(filepath, fields)

        if action_type == 'linkedin_message':
            logger.info(f'[LINKEDIN_MSG] Approved -- manual send required: {filepath.name}')
            return True

        # --- Gold Tier: Odoo actions ---
        if action_type == 'odoo_invoice':
            return execute_odoo_invoice(filepath, fields)

        if action_type == 'odoo_payment':
            return execute_odoo_payment(filepath, fields)

        if action_type == 'odoo_expense':
            return execute_odoo_expense(filepath, fields)

        # --- Gold Tier: Platform-specific social media ---
        if action_type in ('facebook_post', 'instagram_post', 'twitter_post'):
            return execute_platform_post(filepath, fields)

        # --- Gold Tier: Legacy social_media_post (detect platform and delegate) ---
        if action_type == 'social_media_post':
            platform = fields.get('platform', '')
            if platform:
                fields['action_type'] = f'{platform}_post'
                return execute_platform_post(filepath, fields)
            # Fallback: generic social post
            logger.info(f'[SOCIAL] Approved generic post: {filepath.name}')
            return execute_platform_post(filepath, fields)

        # --- Gold Tier: CEO Briefing ---
        if action_type == 'ceo_briefing_send':
            return execute_ceo_briefing_send(filepath, fields)

        # --- Silver Tier: archive/escalate/other ---
        if action_type == 'archive':
            logger.info(f'[ARCHIVE] Auto-archive approved: {filepath.name}')
            return True

        if action_type == 'escalate':
            logger.info(f'[ESCALATE] Escalation noted: {filepath.name}')
            return True

        logger.info(f'[OTHER] Action type "{action_type}" -- logged: {filepath.name}')
        return True

    finally:
        elapsed = time.time() - start_time
        logger.info(
            f'[TIMING] {action_type} executed in {elapsed:.2f}s '
            f'for {filepath.name}'
        )

# ---------------------------------------------------------------------------
# Move helpers
# ---------------------------------------------------------------------------


def move_to_done(filepath: Path, fields: Dict, decision: str,
                 execution_time: float = 0.0):
    """Move an approved/processed file to Done/ with a footer."""
    done_name = filepath.name
    done_path = DONE_DIR / done_name
    if done_path.exists():
        done_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{done_name}"
        done_path = DONE_DIR / done_name

    footer = (
        f'\n\n---\n'
        f'**APPROVAL MANAGER NOTE (Gold Tier):**\n'
        f'- **Decision:** {decision}\n'
        f'- **Action Type:** {fields.get("action_type", "unknown")}\n'
        f'- **Execution Time:** {execution_time:.2f}s\n'
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
        f'**REJECTION NOTE (Gold Tier):**\n'
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
        f'**ACTION FAILED -- RETURNED FOR REVIEW (Gold Tier):**\n'
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


def create_retry_file(filepath: Path, action_type: str, error_msg: str):
    """
    Create a retry file in Pending_Approval/ for failed email/briefing sends.
    This gives the CEO a chance to re-approve with the error context.
    """
    now = datetime.now()
    retry_name = f"RETRY_{now.strftime('%Y%m%d_%H%M%S')}_{filepath.name}"
    retry_path = PENDING_APPROVAL_DIR / retry_name

    retry_content = f"""# Retry Required -- Action Failed

**Action Type:** {action_type}
**Original File:** `{filepath.name}`
**Error:** {error_msg}
**Failed At:** {now.strftime('%Y-%m-%d %H:%M:%S')}
**Urgency:** high

---

## What Happened

The approved action could not be executed due to the error above.
Please review and re-approve to retry, or reject to discard.

---

## Original Content

"""
    try:
        original_content = filepath.read_text(encoding='utf-8')
        retry_content += original_content
    except Exception:
        retry_content += '*Could not read original file.*\n'

    retry_content += (
        f'\n---\n'
        f'*Retry file created by Approval Manager (Gold Tier) '
        f'at {now.strftime("%Y-%m-%d %H:%M:%S")}*\n'
    )

    try:
        with open(retry_path, 'w', encoding='utf-8') as f:
            f.write(retry_content)
        logger.info(f'[RETRY] Created retry file: Pending_Approval/{retry_name}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to create retry file: {e}')


# ---------------------------------------------------------------------------
# Folder scanners -- Enhanced for Gold Tier
# ---------------------------------------------------------------------------


def process_approved(known_approved: Set[str]) -> Set[str]:
    """Process any new files in Approved/."""
    files = sorted(APPROVED_DIR.glob('*.md'))
    for filepath in files:
        if filepath.name in known_approved:
            continue

        logger.info(f'[APPROVED] New file detected: {filepath.name}')
        known_approved.add(filepath.name)

        start_time = time.time()
        try:
            fields = parse_approval_file(filepath)
            action_type = fields.get('action_type', 'other')
            logger.info(
                f'[APPROVED] type={action_type} '
                f'platform={fields.get("platform", "")} '
                f'to="{fields.get("to", "")}" '
                f'urgency={fields.get("urgency", "?")} '
                f'params={list(fields.get("parameters", {}).keys())}'
            )

            success = execute_action(filepath, fields)
            elapsed = time.time() - start_time

            # Determine which MCP server was used
            mcp_server = ''
            if action_type.startswith('odoo_'):
                mcp_server = 'odoo_mcp_server'
            elif action_type in ('email_reply', 'email_send', 'ceo_briefing_send'):
                mcp_server = 'email_mcp_server'

            if success:
                audit(
                    filepath.name, 'approved', action_type,
                    'Action executed successfully',
                    mcp_server=mcp_server,
                    execution_time=elapsed,
                    success=True,
                )
                move_to_done(filepath, fields, 'APPROVED', execution_time=elapsed)
            else:
                audit(
                    filepath.name, 'approved_failed', action_type,
                    'Action execution failed -- returned to pending',
                    mcp_server=mcp_server,
                    execution_time=elapsed,
                    success=False,
                )

                # For email/briefing failures, create a retry file
                if action_type in ('email_reply', 'email_send', 'ceo_briefing_send'):
                    create_retry_file(
                        filepath, action_type,
                        'Action execution failed -- see approval_manager.log'
                    )
                    # Remove the original from Approved/
                    try:
                        filepath.unlink()
                    except Exception:
                        pass
                else:
                    move_back_to_pending(
                        filepath,
                        'Action execution failed -- see approval_manager.log'
                    )

        except RuntimeError as e:
            # Odoo MCP or validation errors -- actionable error messages
            elapsed = time.time() - start_time
            error_msg = str(e)
            logger.error(f'[ERROR] {error_msg}')

            action_type = 'unknown'
            try:
                fields_parsed = parse_approval_file(filepath)
                action_type = fields_parsed.get('action_type', 'unknown')
            except Exception:
                pass

            mcp_server = 'odoo_mcp_server' if 'odoo' in error_msg.lower() else ''
            audit(
                filepath.name, 'approved_failed', action_type,
                f'Error: {error_msg[:200]}',
                mcp_server=mcp_server,
                execution_time=elapsed,
                success=False,
            )
            move_back_to_pending(filepath, error_msg[:300])

        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f'[ERROR] Processing approved file {filepath.name}: {e}')
            audit(
                filepath.name, 'error', 'unknown', str(e),
                execution_time=elapsed,
                success=False,
            )

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

            # Update social media history if relevant
            if action_type in ('facebook_post', 'instagram_post',
                               'twitter_post', 'social_media_post'):
                update_social_posts_history(filepath.name, 'rejected')

            audit(filepath.name, 'rejected', action_type,
                  'Rejected by human reviewer')
            move_to_rejected_archive(filepath, fields)

        except Exception as e:
            logger.error(f'[ERROR] Processing rejected file {filepath.name}: {e}')
            audit(filepath.name, 'error', 'unknown', str(e), success=False)

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
    logger.info('Approval Manager Started (Gold Tier)')
    logger.info(f'Pending Approval:  {PENDING_APPROVAL_DIR}')
    logger.info(f'Approved:          {APPROVED_DIR}')
    logger.info(f'Rejected:          {REJECTED_DIR}')
    logger.info(f'Done:              {DONE_DIR}')
    logger.info(f'Rejected Archive:  {REJECTED_ARCHIVE_DIR}')
    logger.info(f'Social Media:      {SOCIAL_MEDIA_DIR}')
    logger.info(f'Audit log:         {AUDIT_LOG_PATH}')
    logger.info(f'CEO Email:         {CEO_EMAIL or "(not configured)"}')
    logger.info(f'Check interval:    {APPROVAL_CHECK_INTERVAL}s')
    logger.info(f'Email MCP:         {"Available" if EMAIL_MCP_SERVER.exists() else "Not found"}')
    logger.info(f'Odoo MCP:          {"Available" if ODOO_MCP_SERVER.exists() else "Not found"}')
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
