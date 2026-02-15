"""
CEO Briefing Generator — AI Employee Vault (Gold Tier)

Generates a comprehensive weekly "Monday Morning CEO Briefing" by pulling data
from every part of the business:

    - Odoo (JSON-RPC)    → Revenue, invoices, expenses, profit/loss
    - Business_Goals.md  → Revenue targets, KPIs, active projects
    - Done/ folder       → Completed tasks from the past 7 days
    - Social media       → Posts made, draft queue, platform activity
    - Logs/              → System health, uptime, error counts

Output:
    Briefings/YYYY-MM-DD_Monday_Briefing.md    (archive)
    Pending_Approval/CEO_BRIEFING_YYYY-MM-DD.md (for review)

Modes:
    python ceo_briefing_generator.py               # Generate now
    python ceo_briefing_generator.py --email        # Generate and queue email draft
    python ceo_briefing_generator.py --week-of 2026-02-09  # Specific week

Environment Variables (from .env):
    ODOO_URL / ODOO_DB / ODOO_USERNAME / ODOO_PASSWORD
    DONE_DIR / PENDING_APPROVAL_DIR / LOGS_DIR / SOCIAL_MEDIA_DIR / BRIEFINGS_DIR
    CEO_EMAIL           Recipient for emailed briefings
"""

import os
import re
import json
import logging
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR
load_dotenv(VAULT_ROOT / '.env')

BUSINESS_GOALS_PATH = VAULT_ROOT / 'Business_Goals.md'
COMPANY_HANDBOOK_PATH = VAULT_ROOT / 'Company_Handbook.md'
DONE_DIR = Path(os.getenv('DONE_DIR', str(VAULT_ROOT / 'Done')))
PENDING_APPROVAL_DIR = Path(os.getenv('PENDING_APPROVAL_DIR', str(VAULT_ROOT / 'Pending_Approval')))
SOCIAL_MEDIA_DIR = Path(os.getenv('SOCIAL_MEDIA_DIR', str(VAULT_ROOT / 'Social_Media')))
BRIEFINGS_DIR = Path(os.getenv('BRIEFINGS_DIR', str(VAULT_ROOT / 'Briefings')))
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(VAULT_ROOT / 'Logs')))

CEO_EMAIL = os.getenv('CEO_EMAIL', '')

ODOO_URL = os.getenv('ODOO_URL', '') or 'http://localhost:8069'
ODOO_DB = os.getenv('ODOO_DB', '') or 'odoo_gold_tier'
ODOO_USERNAME = os.getenv('ODOO_USERNAME', '') or 'admin'
ODOO_PASSWORD = os.getenv('ODOO_PASSWORD', '') or 'admin'

SOCIAL_POSTS_HISTORY = VAULT_ROOT / 'Watchers' / 'social_posts_history.json'
LINKEDIN_POSTS_HISTORY = VAULT_ROOT / 'Watchers' / 'posts_history.json'

for d in (DONE_DIR, PENDING_APPROVAL_DIR, BRIEFINGS_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'ceo_briefing.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Odoo JSON-RPC client (self-contained — no dependency on MCP server process)
# ---------------------------------------------------------------------------

_rpc_id = 0


def _next_id() -> int:
    global _rpc_id
    _rpc_id += 1
    return _rpc_id


class OdooClient:
    """Lightweight Odoo JSON-RPC client."""

    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip('/')
        self.db = db
        self.username = username
        self.password = password
        self.uid: Optional[int] = None
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def _call(self, service: str, method: str, *args) -> Any:
        payload = {
            'jsonrpc': '2.0',
            'method': 'call',
            'params': {'service': service, 'method': method, 'args': list(args)},
            'id': _next_id(),
        }
        resp = self.session.post(f'{self.url}/jsonrpc', json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if 'error' in data:
            err = data['error']
            msg = err.get('data', {}).get('message', '') or err.get('message', str(err))
            raise RuntimeError(f'Odoo RPC error: {msg}')
        return data.get('result')

    def authenticate(self) -> int:
        uid = self._call('common', 'authenticate',
                         self.db, self.username, self.password, {})
        if not uid:
            raise RuntimeError(f'Odoo auth failed for {self.username}@{self.db}')
        self.uid = uid
        return uid

    def _ensure_auth(self):
        if self.uid is None:
            self.authenticate()

    def search_read(self, model: str, domain: list, fields: list,
                    limit: int = 0, order: str = '') -> List[Dict]:
        self._ensure_auth()
        kwargs: Dict[str, Any] = {'fields': fields}
        if limit:
            kwargs['limit'] = limit
        if order:
            kwargs['order'] = order
        return self._call(
            'object', 'execute_kw',
            self.db, self.uid, self.password,
            model, 'search_read', [domain], kwargs,
        )


# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------


def collect_odoo_financials(week_start: date, week_end: date) -> Dict:
    """Pull revenue, invoices, and expenses from Odoo for the given week."""
    data: Dict[str, Any] = {
        'available': False,
        'revenue': {},
        'invoices': [],
        'expenses': [],
        'overdue_invoices': [],
        'error': None,
    }

    try:
        client = OdooClient(ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD)
        client.authenticate()
        logger.info(f'[ODOO] Connected as uid={client.uid}')
    except Exception as e:
        data['error'] = str(e)
        logger.warning(f'[ODOO] Connection failed: {e}')
        return data

    data['available'] = True
    ws = week_start.isoformat()
    we = week_end.isoformat()
    today = date.today().isoformat()

    # --- Customer invoices created this week ---
    try:
        invoices = client.search_read(
            'account.move',
            [
                ['move_type', 'in', ['out_invoice', 'out_refund']],
                ['create_date', '>=', ws],
                ['create_date', '<=', we + ' 23:59:59'],
            ],
            ['name', 'partner_id', 'invoice_date', 'invoice_date_due',
             'amount_total', 'amount_residual', 'state', 'payment_state',
             'move_type'],
            order='create_date desc',
        )
        data['invoices'] = invoices
    except Exception as e:
        logger.warning(f'[ODOO] Invoice fetch failed: {e}')

    # --- Revenue summary (posted invoices this week) ---
    try:
        posted = [i for i in data['invoices']
                  if i.get('state') == 'posted' and i.get('move_type') == 'out_invoice']
        refunds = [i for i in data['invoices']
                   if i.get('state') == 'posted' and i.get('move_type') == 'out_refund']

        total_invoiced = sum(i['amount_total'] for i in posted)
        total_collected = sum(i['amount_total'] - i['amount_residual'] for i in posted)
        total_outstanding = sum(i['amount_residual'] for i in posted)
        total_refunded = sum(i['amount_total'] for i in refunds)

        data['revenue'] = {
            'total_invoiced': total_invoiced,
            'total_collected': total_collected,
            'total_outstanding': total_outstanding,
            'total_refunded': total_refunded,
            'net_revenue': total_invoiced - total_refunded,
            'invoice_count': len(posted),
            'paid_count': sum(1 for i in posted if i.get('payment_state') == 'paid'),
        }
    except Exception as e:
        logger.warning(f'[ODOO] Revenue calc failed: {e}')

    # --- All invoices (including drafts) for full picture ---
    try:
        all_invoices = client.search_read(
            'account.move',
            [['move_type', '=', 'out_invoice']],
            ['name', 'partner_id', 'invoice_date', 'invoice_date_due',
             'amount_total', 'amount_residual', 'state', 'payment_state'],
            order='invoice_date_due asc',
        )

        # Outstanding across all time
        data['revenue']['total_outstanding_all'] = sum(
            i['amount_residual'] for i in all_invoices if i.get('state') == 'posted'
        )

        # Overdue invoices (due date < today, still has balance)
        for inv in all_invoices:
            if (inv.get('state') == 'posted'
                    and inv.get('amount_residual', 0) > 0
                    and inv.get('invoice_date_due')
                    and inv['invoice_date_due'] < today):
                data['overdue_invoices'].append(inv)
    except Exception as e:
        logger.warning(f'[ODOO] Overdue check failed: {e}')

    # --- Expenses (vendor bills) this week ---
    try:
        expenses = client.search_read(
            'account.move',
            [
                ['move_type', 'in', ['in_invoice', 'in_refund']],
                ['create_date', '>=', ws],
                ['create_date', '<=', we + ' 23:59:59'],
            ],
            ['name', 'partner_id', 'invoice_date', 'ref',
             'amount_total', 'state'],
            order='create_date desc',
        )
        data['expenses'] = expenses
        data['revenue']['total_expenses'] = sum(
            e['amount_total'] for e in expenses if e.get('state') in ('posted', 'draft')
        )
    except Exception as e:
        logger.warning(f'[ODOO] Expense fetch failed: {e}')

    return data


def collect_done_tasks(week_start: date, week_end: date) -> List[Dict]:
    """Read Done/ folder for tasks completed in the given week."""
    tasks = []
    if not DONE_DIR.exists():
        return tasks

    ws_dt = datetime.combine(week_start, datetime.min.time())
    we_dt = datetime.combine(week_end, datetime.max.time())

    for f in sorted(DONE_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if ws_dt <= mtime <= we_dt:
                content = f.read_text(encoding='utf-8')
                title_match = re.search(r'^#\s+(.+)', content, re.MULTILINE)
                title = title_match.group(1).strip() if title_match else f.stem

                # Try to extract action type
                action_match = re.search(r'\*\*Action Type:\*\*\s*(.+)', content)
                action_type = action_match.group(1).strip() if action_match else 'task'

                tasks.append({
                    'filename': f.name,
                    'title': title,
                    'action_type': action_type,
                    'completed_at': mtime.strftime('%Y-%m-%d %H:%M'),
                    'snippet': content[:200],
                })
        except Exception as e:
            logger.warning(f'[DONE] Could not read {f.name}: {e}')

    return tasks


def collect_business_goals() -> Dict:
    """Extract structured data from Business_Goals.md."""
    info: Dict = {
        'objectives': [],
        'kpis': [],
        'revenue_targets': [],
        'initiatives': [],
        'raw': '',
    }
    if not BUSINESS_GOALS_PATH.exists():
        return info

    text = BUSINESS_GOALS_PATH.read_text(encoding='utf-8')
    info['raw'] = text

    # Objectives
    for m in re.finditer(r'###\s+Objective\s+\d+:\s*(.+)', text):
        info['objectives'].append(m.group(1).strip())

    # KPI table rows — only parse the table under "Key Performance Indicators"
    # Find the KPI section, then extract just the first table block
    kpi_header = re.search(r'## Key Performance Indicators', text)
    if kpi_header:
        kpi_text = text[kpi_header.end():]
        # Stop at the next section header or document end
        next_section = re.search(r'\n---\n|\n## ', kpi_text)
        if next_section:
            kpi_text = kpi_text[:next_section.start()]
        for m in re.finditer(
            r'\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|',
            kpi_text,
        ):
            kpi_name = m.group(1).strip()
            if kpi_name in ('KPI', '') or kpi_name.startswith('—') or '---' in kpi_name:
                continue
            if 'Target' in m.group(2) and 'Current' in m.group(3):
                continue
            info['kpis'].append({
                'name': kpi_name,
                'target': m.group(2).strip(),
                'current': m.group(3).strip(),
                'status': m.group(4).strip(),
            })

    # Strategic Initiatives
    for m in re.finditer(r'###\s+Initiative\s+\d+:\s*(.+)', text):
        info['initiatives'].append(m.group(1).strip())

    return info


def collect_social_media(week_start: date, week_end: date) -> Dict:
    """Summarize social media activity for the week."""
    data: Dict[str, Any] = {
        'posts_by_platform': {},
        'total_posts': 0,
        'drafts_pending': 0,
        'approved': 0,
        'rejected': 0,
    }

    # Social media posts history
    for history_path in (SOCIAL_POSTS_HISTORY, LINKEDIN_POSTS_HISTORY):
        if not history_path.exists():
            continue
        try:
            with open(history_path, 'r') as f:
                history = json.load(f)
        except Exception:
            continue

        ws_dt = datetime.combine(week_start, datetime.min.time())
        we_dt = datetime.combine(week_end, datetime.max.time())

        for post in history.get('posts', []):
            try:
                ts = datetime.fromisoformat(post['created_at'])
            except (KeyError, ValueError):
                continue
            if ws_dt <= ts <= we_dt:
                platform = post.get('platform', 'linkedin')
                data['posts_by_platform'][platform] = \
                    data['posts_by_platform'].get(platform, 0) + 1
                data['total_posts'] += 1

                status = post.get('status', '')
                if status == 'pending_approval':
                    data['drafts_pending'] += 1
                elif status == 'approved':
                    data['approved'] += 1
                elif status == 'rejected':
                    data['rejected'] += 1

    # Count files currently in Social_Media/ as queue
    if SOCIAL_MEDIA_DIR.exists():
        data['queue_size'] = len(list(SOCIAL_MEDIA_DIR.glob('*.md')))

    return data


def collect_system_health(week_start: date, week_end: date) -> Dict:
    """Scan log files for errors, warnings, and activity metrics."""
    data: Dict[str, Any] = {
        'log_files_scanned': 0,
        'total_lines': 0,
        'errors': 0,
        'warnings': 0,
        'info_events': 0,
        'notable_errors': [],
    }

    if not LOGS_DIR.exists():
        return data

    ws_str = week_start.strftime('%Y-%m-%d')

    for log_file in LOGS_DIR.glob('*.log'):
        data['log_files_scanned'] += 1
        try:
            lines = log_file.read_text(encoding='utf-8', errors='replace').splitlines()
            for line in lines:
                # Only count lines from this week (best-effort date check)
                if ws_str > line[:10] if len(line) >= 10 else False:
                    continue

                data['total_lines'] += 1
                if ' - ERROR - ' in line:
                    data['errors'] += 1
                    if len(data['notable_errors']) < 5:
                        # Keep first 120 chars as context
                        data['notable_errors'].append({
                            'file': log_file.name,
                            'message': line[:120],
                        })
                elif ' - WARNING - ' in line:
                    data['warnings'] += 1
                elif ' - INFO - ' in line:
                    data['info_events'] += 1
        except Exception as e:
            logger.warning(f'[LOGS] Could not scan {log_file.name}: {e}')

    return data


# ---------------------------------------------------------------------------
# Briefing builder
# ---------------------------------------------------------------------------


def _fmt_currency(amount) -> str:
    """Format a number as currency."""
    try:
        return f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _pct_change(current: float, target: float) -> str:
    """Return a percentage string relative to target."""
    if target == 0:
        return "N/A"
    pct = (current / target) * 100
    return f"{pct:.1f}%"


def build_briefing(
    week_start: date,
    week_end: date,
    financials: Dict,
    done_tasks: List[Dict],
    goals: Dict,
    social: Dict,
    health: Dict,
) -> str:
    """Assemble the full CEO briefing markdown document."""

    now = datetime.now()
    week_label = f"{week_start.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"

    rev = financials.get('revenue', {})
    total_invoiced = rev.get('total_invoiced', 0)
    total_collected = rev.get('total_collected', 0)
    total_outstanding = rev.get('total_outstanding', 0)
    total_outstanding_all = rev.get('total_outstanding_all', 0)
    total_expenses = rev.get('total_expenses', 0)
    net_revenue = rev.get('net_revenue', 0)
    profit = net_revenue - total_expenses

    # -----------------------------------------------------------------------
    # Executive Summary
    # -----------------------------------------------------------------------
    tasks_done = len(done_tasks)
    invoice_count = rev.get('invoice_count', 0)
    overdue_count = len(financials.get('overdue_invoices', []))
    social_posts = social.get('total_posts', 0)
    error_count = health.get('errors', 0)

    # Build a concise summary paragraph
    highlights = []
    if tasks_done > 0:
        highlights.append(f"{tasks_done} task(s) completed")
    if invoice_count > 0:
        highlights.append(f"{_fmt_currency(total_invoiced)} invoiced across {invoice_count} invoice(s)")
    elif total_invoiced == 0 and len(financials.get('invoices', [])) > 0:
        highlights.append(f"{len(financials['invoices'])} invoice(s) created (draft)")
    if total_collected > 0:
        highlights.append(f"{_fmt_currency(total_collected)} collected")
    if social_posts > 0:
        highlights.append(f"{social_posts} social media post(s) drafted")
    if overdue_count > 0:
        highlights.append(f"{overdue_count} overdue invoice(s) require attention")
    if error_count > 0:
        highlights.append(f"{error_count} system error(s) logged")

    if not highlights:
        exec_summary = (
            "This was a setup/configuration week. Systems are being initialized "
            "and no significant operational activity was recorded yet. "
            "Focus areas for the coming week should include finalizing integrations "
            "and starting revenue-generating activities."
        )
    else:
        exec_summary = (
            f"Week of {week_label}: {'. '.join(highlights)}. "
        )
        if profit > 0:
            exec_summary += f"Net profit for the week: {_fmt_currency(profit)}. "
        elif total_expenses > 0 and net_revenue == 0:
            exec_summary += (
                f"Total expenses of {_fmt_currency(total_expenses)} with no revenue posted yet. "
            )
        if error_count == 0:
            exec_summary += "All systems operated without errors."
        else:
            exec_summary += f"Note: {error_count} error(s) detected — see System Health section."

    # -----------------------------------------------------------------------
    # Revenue Analysis section
    # -----------------------------------------------------------------------
    if financials.get('available'):
        revenue_section = f"""### Revenue This Week

| Metric | Amount |
|--------|--------|
| Total Invoiced | {_fmt_currency(total_invoiced)} |
| Total Collected (Payments) | {_fmt_currency(total_collected)} |
| Outstanding (This Week) | {_fmt_currency(total_outstanding)} |
| Total Outstanding (All Time) | {_fmt_currency(total_outstanding_all)} |
| Total Expenses | {_fmt_currency(total_expenses)} |
| Net Revenue | {_fmt_currency(net_revenue)} |
| **Estimated Profit** | **{_fmt_currency(profit)}** |"""

        # Invoice detail table
        invoices = financials.get('invoices', [])
        if invoices:
            invoice_rows = []
            for inv in invoices:
                customer = inv['partner_id'][1] if inv.get('partner_id') else 'N/A'
                inv_type = 'Invoice' if inv.get('move_type') == 'out_invoice' else 'Refund'
                invoice_rows.append(
                    f"| {inv.get('name', 'Draft')} | {customer} | "
                    f"{_fmt_currency(inv['amount_total'])} | "
                    f"{inv.get('state', '')} | {inv.get('payment_state', '')} | {inv_type} |"
                )
            revenue_section += f"""

### Invoice Activity

| # | Customer | Amount | State | Payment | Type |
|---|----------|--------|-------|---------|------|
{chr(10).join(invoice_rows)}"""

        # Expense detail table
        expenses = financials.get('expenses', [])
        if expenses:
            expense_rows = []
            for exp in expenses:
                vendor = exp['partner_id'][1] if exp.get('partner_id') else 'N/A'
                expense_rows.append(
                    f"| {exp.get('name', 'Draft')} | {vendor} | "
                    f"{exp.get('ref', '')} | {_fmt_currency(exp['amount_total'])} | "
                    f"{exp.get('state', '')} |"
                )
            revenue_section += f"""

### Expenses This Week

| # | Vendor | Description | Amount | State |
|---|--------|-------------|--------|-------|
{chr(10).join(expense_rows)}"""

    else:
        odoo_err = financials.get('error', 'unknown')
        revenue_section = f"""### Revenue This Week

> **Odoo unavailable** — Could not connect to Odoo to retrieve financial data.
> Error: `{odoo_err}`
>
> Ensure Odoo is running (`docker-compose up -d`) and `.env` credentials are correct."""

    # -----------------------------------------------------------------------
    # Completed Work section
    # -----------------------------------------------------------------------
    if done_tasks:
        task_rows = []
        for t in done_tasks:
            task_rows.append(
                f"| {t['title'][:50]} | {t['action_type']} | {t['completed_at']} |"
            )
        completed_section = f"""| Task | Type | Completed |
|------|------|-----------|
{chr(10).join(task_rows)}

**Total completed:** {tasks_done} task(s)"""
    else:
        completed_section = (
            "*No tasks were completed this week (Done/ folder is empty for this period).*\n\n"
            "This may indicate:\n"
            "- The system is still being set up\n"
            "- Completed tasks were not moved to Done/\n"
            "- Focus was on configuration rather than task execution"
        )

    # -----------------------------------------------------------------------
    # Bottlenecks section
    # -----------------------------------------------------------------------
    bottlenecks = []

    if overdue_count > 0:
        overdue_total = sum(
            inv.get('amount_residual', 0) for inv in financials.get('overdue_invoices', [])
        )
        bottleneck_rows = []
        for inv in financials.get('overdue_invoices', []):
            customer = inv['partner_id'][1] if inv.get('partner_id') else 'N/A'
            bottleneck_rows.append(
                f"  - **{inv.get('name', 'N/A')}** — {customer}: "
                f"{_fmt_currency(inv['amount_residual'])} overdue "
                f"(due {inv.get('invoice_date_due', 'N/A')})"
            )
        bottlenecks.append(
            f"**Overdue Invoices ({overdue_count})** — "
            f"Total: {_fmt_currency(overdue_total)}\n" +
            '\n'.join(bottleneck_rows)
        )

    if error_count > 0:
        error_details = []
        for err in health.get('notable_errors', []):
            error_details.append(f"  - `{err['file']}`: {err['message'][:80]}")
        bottlenecks.append(
            f"**System Errors ({error_count})** — Review needed\n" +
            '\n'.join(error_details)
        )

    # Check for pending approvals piling up
    pending_count = len(list(PENDING_APPROVAL_DIR.glob('*.md')))
    if pending_count > 3:
        bottlenecks.append(
            f"**Approval Queue Backlog** — {pending_count} items awaiting approval. "
            "Consider reviewing and clearing the queue."
        )

    if not bottlenecks:
        bottlenecks_section = "*No bottlenecks identified this week. All clear.*"
    else:
        bottlenecks_section = '\n\n'.join(f"{i+1}. {b}" for i, b in enumerate(bottlenecks))

    # -----------------------------------------------------------------------
    # Proactive Suggestions section
    # -----------------------------------------------------------------------
    suggestions = []

    # Cost optimization
    if total_expenses > 0 and net_revenue == 0:
        suggestions.append(
            "**Review expenses** — Expenses were recorded but no revenue has been posted. "
            "Verify that invoices are being confirmed (moved from draft to posted) in Odoo."
        )
    if total_expenses > net_revenue > 0:
        suggestions.append(
            f"**Cost watch** — Expenses ({_fmt_currency(total_expenses)}) exceed "
            f"revenue ({_fmt_currency(net_revenue)}). Review spending categories for "
            "optimization opportunities."
        )

    # Revenue opportunities
    if total_outstanding_all > 0:
        suggestions.append(
            f"**Follow up on receivables** — {_fmt_currency(total_outstanding_all)} "
            "outstanding across all invoices. Send payment reminders for invoices "
            "approaching or past due."
        )
    if invoice_count == 0 and tasks_done > 0:
        suggestions.append(
            "**Revenue opportunity** — Work was completed this week but no invoices "
            "were created. Consider whether any completed work is billable."
        )

    # Process improvements
    if pending_count > 5:
        suggestions.append(
            f"**Streamline approvals** — {pending_count} items in the approval queue. "
            "Consider setting up auto-approval rules for low-risk items."
        )
    if social_posts == 0:
        suggestions.append(
            "**Social media gap** — No posts were drafted this week. "
            "Consistent posting builds audience. Run `social_media_poster.py --all` "
            "to generate drafts for all platforms."
        )
    if tasks_done == 0:
        suggestions.append(
            "**Operational activation** — No tasks in Done/ this week. Ensure watchers "
            "are running (`task_scheduler.py`) and that Inbox/ has items to process."
        )

    if not suggestions:
        suggestions.append(
            "Operations are running smoothly. Continue current trajectory and "
            "monitor for emerging opportunities."
        )

    suggestions_section = '\n\n'.join(f"{i+1}. {s}" for i, s in enumerate(suggestions))

    # -----------------------------------------------------------------------
    # Social Media Performance section
    # -----------------------------------------------------------------------
    if social.get('total_posts', 0) > 0 or social.get('queue_size', 0) > 0:
        platform_rows = []
        for platform, count in social.get('posts_by_platform', {}).items():
            platform_rows.append(f"| {platform.title()} | {count} |")
        if not platform_rows:
            platform_rows.append("| *(none)* | 0 |")

        social_section = f"""| Platform | Posts Drafted |
|----------|-------------|
{chr(10).join(platform_rows)}

| Metric | Count |
|--------|-------|
| Total Posts Drafted | {social.get('total_posts', 0)} |
| Pending Approval | {social.get('drafts_pending', 0)} |
| Approved | {social.get('approved', 0)} |
| Rejected | {social.get('rejected', 0)} |
| Drafts in Queue | {social.get('queue_size', 0)} |"""
    else:
        social_section = (
            "*No social media activity recorded this week.*\n\n"
            "To generate posts, run:\n"
            "```\npython Watchers/social_media_poster.py --all\n```"
        )

    # -----------------------------------------------------------------------
    # System Health section
    # -----------------------------------------------------------------------
    health_section = f"""| Metric | Value |
|--------|-------|
| Log Files Scanned | {health.get('log_files_scanned', 0)} |
| Total Log Events | {health.get('total_lines', 0)} |
| INFO Events | {health.get('info_events', 0)} |
| Warnings | {health.get('warnings', 0)} |
| Errors | {health.get('errors', 0)} |"""

    if health.get('errors', 0) == 0:
        health_section += "\n\nAll systems healthy. No errors detected."
    else:
        health_section += "\n\n**Errors requiring attention:**\n"
        for err in health.get('notable_errors', []):
            health_section += f"- `{err['file']}`: {err['message'][:100]}\n"

    # -----------------------------------------------------------------------
    # Upcoming Priorities section
    # -----------------------------------------------------------------------
    upcoming = []

    # Deadlines from overdue invoices
    for inv in financials.get('overdue_invoices', []):
        customer = inv['partner_id'][1] if inv.get('partner_id') else 'Unknown'
        upcoming.append(
            f"**OVERDUE** — Invoice {inv.get('name', 'N/A')} for {customer}: "
            f"{_fmt_currency(inv['amount_residual'])} (was due {inv.get('invoice_date_due', 'N/A')})"
        )

    # Goals-based priorities
    for obj in goals.get('objectives', []):
        upcoming.append(f"**Goal** — {obj}")

    for initiative in goals.get('initiatives', []):
        upcoming.append(f"**Initiative** — {initiative}")

    # Standard weekly priorities
    upcoming.append("**Review** — Clear approval queue and review pending items")
    upcoming.append("**Social** — Schedule posts for the coming week")
    if financials.get('available'):
        upcoming.append("**Finance** — Confirm draft invoices and send payment reminders")

    priorities_section = '\n'.join(f"{i+1}. {u}" for i, u in enumerate(upcoming))

    # -----------------------------------------------------------------------
    # KPI Dashboard
    # -----------------------------------------------------------------------
    kpis = goals.get('kpis', [])
    if kpis:
        kpi_rows = []
        for kpi in kpis:
            kpi_rows.append(
                f"| {kpi['name']} | {kpi['target']} | {kpi['current']} | {kpi['status']} |"
            )
        kpi_section = f"""| KPI | Target | Current | Status |
|-----|--------|---------|--------|
{chr(10).join(kpi_rows)}"""
    else:
        kpi_section = "*KPIs not yet configured in Business_Goals.md.*"

    # -----------------------------------------------------------------------
    # Assemble full document
    # -----------------------------------------------------------------------
    briefing = f"""# Monday Morning CEO Briefing

**Week of:** {week_label}
**Generated:** {now.strftime('%Y-%m-%d %H:%M:%S')}
**Prepared by:** AI Employee (Gold Tier)

---

## 1. Executive Summary

{exec_summary}

---

## 2. Revenue & Financial Analysis

{revenue_section}

---

## 3. Completed Work

{completed_section}

---

## 4. Bottlenecks Identified

{bottlenecks_section}

---

## 5. Proactive Suggestions

{suggestions_section}

---

## 6. Social Media Performance

{social_section}

---

## 7. System Health

{health_section}

---

## 8. Upcoming Priorities (Next 7 Days)

{priorities_section}

---

## 9. KPI Dashboard

{kpi_section}

---

## Quick Actions

- [ ] Review and clear approval queue ({pending_count} item(s))
- [ ] Confirm draft invoices in Odoo
- [ ] Follow up on overdue payments ({overdue_count} invoice(s))
- [ ] Review social media draft queue
- [ ] Update Business_Goals.md with current metrics

---

*This briefing was auto-generated by the AI Employee (Gold Tier) at {now.strftime('%Y-%m-%d %H:%M:%S')}.*
*Data sources: Odoo, Done/ folder, Business_Goals.md, Social_Media/, Logs/*
"""
    return briefing


# ---------------------------------------------------------------------------
# Output: save files and optionally queue email
# ---------------------------------------------------------------------------


def save_briefing(briefing: str, week_start: date) -> Dict[str, Path]:
    """Save the briefing to Briefings/ and Pending_Approval/."""
    monday = week_start + timedelta(days=(7 - week_start.weekday()) % 7)
    date_str = monday.strftime('%Y-%m-%d')

    # Briefings/ archive
    archive_name = f"{date_str}_Monday_Briefing.md"
    archive_path = BRIEFINGS_DIR / archive_name

    # Pending_Approval/ for review
    approval_name = f"CEO_BRIEFING_{date_str}.md"
    approval_path = PENDING_APPROVAL_DIR / approval_name

    paths = {}
    try:
        with open(archive_path, 'w', encoding='utf-8') as f:
            f.write(briefing)
        paths['archive'] = archive_path
        logger.info(f'[SAVE] Briefings/{archive_name}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to save archive: {e}')

    try:
        with open(approval_path, 'w', encoding='utf-8') as f:
            f.write(briefing)
        paths['approval'] = approval_path
        logger.info(f'[SAVE] Pending_Approval/{approval_name}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to save approval file: {e}')

    return paths


def queue_email_draft(briefing: str, week_start: date):
    """
    Create a plain-text email draft via the email MCP server.

    This writes a file to Pending_Approval/ with email metadata so the
    approval_manager can send it once approved. The actual sending would
    use the email MCP server's draft_email or send_email tool.
    """
    if not CEO_EMAIL:
        logger.warning('[EMAIL] CEO_EMAIL not set in .env — skipping email draft')
        return

    monday = week_start + timedelta(days=(7 - week_start.weekday()) % 7)
    date_str = monday.strftime('%Y-%m-%d')
    subject = f"Weekly CEO Briefing — Week of {monday.strftime('%b %d, %Y')}"

    email_filename = f"EMAIL_CEO_BRIEFING_{date_str}.md"
    email_path = PENDING_APPROVAL_DIR / email_filename

    email_content = f"""# Email — Pending Approval

**Action Type:** send_email
**To:** {CEO_EMAIL}
**Subject:** {subject}
**Created:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Urgency:** medium

---

## Email Body

{briefing}

---

## Instructions

1. **To approve and send:** Move this file to `Approved/`
2. **To reject:** Move this file to `Rejected/`
3. **To edit:** Modify the content above, then move to `Approved/`

---
*Auto-generated by CEO Briefing Generator*
"""
    try:
        with open(email_path, 'w', encoding='utf-8') as f:
            f.write(email_content)
        logger.info(f'[EMAIL] Draft queued: Pending_Approval/{email_filename}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to create email draft: {e}')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def generate_briefing(week_of: Optional[str] = None, send_email: bool = False):
    """Generate a complete CEO briefing."""
    logger.info('=' * 60)
    logger.info('CEO Briefing Generator — Gold Tier')
    logger.info('=' * 60)

    # Determine the week range
    if week_of:
        try:
            ref_date = date.fromisoformat(week_of)
        except ValueError:
            logger.error(f'[ERROR] Invalid date: {week_of}. Use YYYY-MM-DD format.')
            return
    else:
        ref_date = date.today()

    # Week = Monday to Sunday
    week_start = ref_date - timedelta(days=ref_date.weekday())
    week_end = week_start + timedelta(days=6)
    logger.info(f'[WEEK] {week_start} to {week_end}')

    # Collect data from all sources
    logger.info('[COLLECT] Gathering Odoo financial data...')
    financials = collect_odoo_financials(week_start, week_end)
    if financials.get('available'):
        rev = financials.get('revenue', {})
        logger.info(
            f'[ODOO] Invoiced={_fmt_currency(rev.get("total_invoiced", 0))} '
            f'Collected={_fmt_currency(rev.get("total_collected", 0))} '
            f'Expenses={_fmt_currency(rev.get("total_expenses", 0))}'
        )
    else:
        logger.warning(f'[ODOO] Unavailable: {financials.get("error", "unknown")}')

    logger.info('[COLLECT] Scanning Done/ folder...')
    done_tasks = collect_done_tasks(week_start, week_end)
    logger.info(f'[DONE] {len(done_tasks)} task(s) completed this week')

    logger.info('[COLLECT] Reading Business Goals...')
    goals = collect_business_goals()
    logger.info(
        f'[GOALS] {len(goals["objectives"])} objectives, '
        f'{len(goals["kpis"])} KPIs, '
        f'{len(goals["initiatives"])} initiatives'
    )

    logger.info('[COLLECT] Summarizing social media activity...')
    social = collect_social_media(week_start, week_end)
    logger.info(f'[SOCIAL] {social["total_posts"]} posts, {social.get("queue_size", 0)} in queue')

    logger.info('[COLLECT] Scanning system logs...')
    health = collect_system_health(week_start, week_end)
    logger.info(
        f'[HEALTH] {health["total_lines"]} events, '
        f'{health["errors"]} errors, {health["warnings"]} warnings'
    )

    # Build the briefing
    logger.info('[BUILD] Assembling briefing...')
    briefing = build_briefing(
        week_start, week_end,
        financials, done_tasks, goals, social, health,
    )

    # Save to disk
    paths = save_briefing(briefing, week_start)
    for label, path in paths.items():
        logger.info(f'[OUTPUT] {label}: {path}')

    # Optionally queue email
    if send_email:
        queue_email_draft(briefing, week_start)

    logger.info('=' * 60)
    logger.info('[DONE] CEO Briefing generated successfully')
    logger.info('=' * 60)

    return paths


def main():
    parser = argparse.ArgumentParser(
        description='CEO Briefing Generator — AI Employee Vault (Gold Tier)'
    )
    parser.add_argument(
        '--week-of',
        type=str,
        default=None,
        help='Generate briefing for the week containing this date (YYYY-MM-DD). Defaults to current week.',
    )
    parser.add_argument(
        '--email',
        action='store_true',
        help='Also create an email draft in Pending_Approval/ for sending to CEO',
    )
    args = parser.parse_args()

    generate_briefing(week_of=args.week_of, send_email=args.email)


if __name__ == '__main__':
    main()
