"""
Odoo MCP Server — AI Employee Vault (Gold Tier)

A Model Context Protocol (MCP) server that exposes Odoo accounting operations
as tools so that Claude Code (or any MCP client) can create invoices, record
payments, track expenses, and pull revenue summaries.

Tools provided:
    create_invoice     — Create a customer invoice in Odoo
    get_invoices       — List invoices with optional filters
    get_invoice_by_id  — Retrieve full details of a single invoice
    record_payment     — Register a payment against an invoice
    get_revenue_summary — Aggregate revenue for a date range
    create_expense     — Record a business expense
    get_expenses       — List expenses with optional filters

Transport: stdio  (launched by Claude Code as a subprocess)

Odoo connection: JSON-RPC 2.0 via /jsonrpc endpoint (Odoo 19 legacy RPC,
supported until Odoo 20).

SETUP:
    1.  pip install requests python-dotenv
    2.  Ensure Odoo 19 is running (docker-compose up -d)
    3.  Set ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD in .env
    4.  Register this server in .mcp.json at the project root

Environment Variables (from .env):
    ODOO_URL        Odoo instance URL  (default: http://localhost:8069)
    ODOO_DB         Database name      (default: odoo_gold_tier)
    ODOO_USERNAME   Login username     (default: admin)
    ODOO_PASSWORD   Login password     (default: admin)
    LOGS_DIR        Folder for odoo_mcp.log
"""

import os
import sys
import json
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR.parent.resolve()
load_dotenv(VAULT_ROOT / '.env')

ODOO_URL = os.getenv('ODOO_URL', '') or 'http://localhost:8069'
ODOO_DB = os.getenv('ODOO_DB', '') or 'odoo_gold_tier'
ODOO_USERNAME = os.getenv('ODOO_USERNAME', '') or 'admin'
ODOO_PASSWORD = os.getenv('ODOO_PASSWORD', '') or 'admin'

LOGS_DIR = Path(os.getenv('LOGS_DIR', str(VAULT_ROOT / 'Logs')))
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging — file-only so we never pollute the stdio MCP channel
# ---------------------------------------------------------------------------

logger = logging.getLogger('odoo_mcp')
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(str(LOGS_DIR / 'odoo_mcp.log'))
_fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(_fh)

# ---------------------------------------------------------------------------
# Odoo JSON-RPC client
# ---------------------------------------------------------------------------

_request_id = 0


def _next_id() -> int:
    global _request_id
    _request_id += 1
    return _request_id


class OdooClient:
    """Lightweight Odoo JSON-RPC client using the /jsonrpc endpoint."""

    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip('/')
        self.db = db
        self.username = username
        self.password = password
        self.uid: Optional[int] = None
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def _call(self, service: str, method: str, *args) -> Any:
        """Execute a single JSON-RPC call against /jsonrpc."""
        payload = {
            'jsonrpc': '2.0',
            'method': 'call',
            'params': {
                'service': service,
                'method': method,
                'args': list(args),
            },
            'id': _next_id(),
        }
        resp = self.session.post(
            f'{self.url}/jsonrpc',
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if 'error' in data:
            err = data['error']
            msg = err.get('data', {}).get('message', '') or err.get('message', str(err))
            raise RuntimeError(f'Odoo RPC error: {msg}')
        return data.get('result')

    def authenticate(self) -> int:
        """Authenticate and cache the uid."""
        uid = self._call('common', 'authenticate',
                         self.db, self.username, self.password, {})
        if not uid:
            raise RuntimeError(
                f'Authentication failed for {self.username}@{self.db}. '
                'Check ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD.'
            )
        self.uid = uid
        logger.info(f'[AUTH] Authenticated as uid={uid} ({self.username}@{self.db})')
        return uid

    def _ensure_auth(self):
        if self.uid is None:
            self.authenticate()

    def execute(self, model: str, method: str, *args) -> Any:
        """Call a method on an Odoo model via the object service."""
        self._ensure_auth()
        return self._call(
            'object', 'execute',
            self.db, self.uid, self.password,
            model, method, *args,
        )

    def search_read(self, model: str, domain: list, fields: list,
                    limit: int = 0, order: str = '') -> List[Dict]:
        """search_read shortcut with kwargs support."""
        self._ensure_auth()
        kwargs: Dict[str, Any] = {'fields': fields}
        if limit:
            kwargs['limit'] = limit
        if order:
            kwargs['order'] = order
        return self._call(
            'object', 'execute_kw',
            self.db, self.uid, self.password,
            model, 'search_read',
            [domain], kwargs,
        )

    def create(self, model: str, vals: dict) -> int:
        """Create a record and return its id."""
        self._ensure_auth()
        return self._call(
            'object', 'execute_kw',
            self.db, self.uid, self.password,
            model, 'create',
            [vals], {},
        )

    def write(self, model: str, ids: list, vals: dict) -> bool:
        """Update records."""
        self._ensure_auth()
        return self._call(
            'object', 'execute_kw',
            self.db, self.uid, self.password,
            model, 'write',
            [ids, vals], {},
        )

    def read(self, model: str, ids: list, fields: list) -> List[Dict]:
        """Read specific records by id."""
        self._ensure_auth()
        return self._call(
            'object', 'execute_kw',
            self.db, self.uid, self.password,
            model, 'read',
            [ids], {'fields': fields},
        )


# Lazy singleton
_client: Optional[OdooClient] = None


def _odoo() -> OdooClient:
    global _client
    if _client is None:
        _client = OdooClient(ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD)
    return _odoo_checked(_client)


def _odoo_checked(client: OdooClient) -> OdooClient:
    client._ensure_auth()
    return client


# ---------------------------------------------------------------------------
# Helper: resolve partner by name (create if missing)
# ---------------------------------------------------------------------------

def _resolve_partner(name: str) -> int:
    """Find a res.partner by name, creating one if it doesn't exist."""
    client = _odoo()
    partners = client.search_read(
        'res.partner',
        [['name', 'ilike', name]],
        ['id', 'name'],
        limit=1,
    )
    if partners:
        return partners[0]['id']
    pid = client.create('res.partner', {'name': name, 'is_company': True})
    logger.info(f'[PARTNER] Created partner "{name}" id={pid}')
    return pid


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_create_invoice(args: dict) -> dict:
    """Create a customer invoice (out_invoice) in Odoo."""
    customer_name = args['customer_name']
    amount = float(args['amount'])
    description = args.get('description', 'Invoice')
    due_date = args.get('due_date', '')

    logger.info(f'[INVOICE] Creating: customer={customer_name} amount={amount}')

    client = _odoo()
    partner_id = _resolve_partner(customer_name)

    vals: Dict[str, Any] = {
        'move_type': 'out_invoice',
        'partner_id': partner_id,
        'invoice_line_ids': [(0, 0, {
            'name': description,
            'quantity': 1,
            'price_unit': amount,
        })],
    }
    if due_date:
        vals['invoice_date_due'] = due_date

    invoice_id = client.create('account.move', vals)

    # Read back the created invoice for confirmation
    inv = client.read('account.move', [invoice_id],
                      ['name', 'amount_total', 'state', 'invoice_date_due'])[0]

    logger.info(f'[INVOICE] Created id={invoice_id} name={inv["name"]} total={inv["amount_total"]}')

    return {
        'status': 'created',
        'invoice_id': invoice_id,
        'invoice_number': inv.get('name', ''),
        'customer': customer_name,
        'amount_total': inv['amount_total'],
        'state': inv['state'],
        'due_date': inv.get('invoice_date_due') or '',
    }


def tool_get_invoices(args: dict) -> dict:
    """List recent invoices with optional filters."""
    customer = args.get('customer', '')
    status = args.get('status', '')
    date_from = args.get('date_from', '')
    date_to = args.get('date_to', '')
    limit = int(args.get('limit', 20))

    domain: list = [['move_type', 'in', ['out_invoice', 'out_refund']]]

    if customer:
        domain.append(['partner_id.name', 'ilike', customer])
    if status:
        state_map = {
            'draft': 'draft',
            'posted': 'posted',
            'cancelled': 'cancel',
            'canceled': 'cancel',
            'paid': 'posted',
        }
        mapped = state_map.get(status.lower(), status.lower())
        domain.append(['state', '=', mapped])
        if status.lower() == 'paid':
            domain.append(['payment_state', '=', 'paid'])
    if date_from:
        domain.append(['invoice_date', '>=', date_from])
    if date_to:
        domain.append(['invoice_date', '<=', date_to])

    logger.info(f'[INVOICES] Listing: domain={domain} limit={limit}')

    client = _odoo()
    invoices = client.search_read(
        'account.move', domain,
        ['name', 'partner_id', 'invoice_date', 'invoice_date_due',
         'amount_total', 'amount_residual', 'state', 'payment_state'],
        limit=limit,
        order='invoice_date desc, id desc',
    )

    results = []
    for inv in invoices:
        results.append({
            'id': inv['id'],
            'number': inv.get('name', ''),
            'customer': inv['partner_id'][1] if inv.get('partner_id') else '',
            'date': inv.get('invoice_date') or '',
            'due_date': inv.get('invoice_date_due') or '',
            'amount_total': inv.get('amount_total', 0),
            'amount_due': inv.get('amount_residual', 0),
            'state': inv.get('state', ''),
            'payment_state': inv.get('payment_state', ''),
        })

    logger.info(f'[INVOICES] Found {len(results)} invoice(s)')

    return {
        'count': len(results),
        'invoices': results,
    }


def tool_get_invoice_by_id(args: dict) -> dict:
    """Get full details of a specific invoice."""
    invoice_id = int(args['invoice_id'])

    logger.info(f'[INVOICE] Reading id={invoice_id}')

    client = _odoo()
    invoices = client.read('account.move', [invoice_id], [
        'name', 'partner_id', 'move_type', 'state', 'payment_state',
        'invoice_date', 'invoice_date_due', 'amount_untaxed',
        'amount_tax', 'amount_total', 'amount_residual',
        'invoice_line_ids', 'narration',
    ])

    if not invoices:
        return {'error': f'Invoice {invoice_id} not found'}

    inv = invoices[0]

    # Fetch line items
    lines = []
    if inv.get('invoice_line_ids'):
        line_data = client.read('account.move.line', inv['invoice_line_ids'], [
            'name', 'quantity', 'price_unit', 'price_subtotal',
            'product_id', 'account_id',
        ])
        for ln in line_data:
            lines.append({
                'id': ln['id'],
                'description': ln.get('name', ''),
                'quantity': ln.get('quantity', 0),
                'unit_price': ln.get('price_unit', 0),
                'subtotal': ln.get('price_subtotal', 0),
                'product': ln['product_id'][1] if ln.get('product_id') else '',
            })

    logger.info(f'[INVOICE] Read id={invoice_id} name={inv.get("name")} lines={len(lines)}')

    return {
        'id': invoice_id,
        'number': inv.get('name', ''),
        'customer': inv['partner_id'][1] if inv.get('partner_id') else '',
        'type': inv.get('move_type', ''),
        'state': inv.get('state', ''),
        'payment_state': inv.get('payment_state', ''),
        'date': inv.get('invoice_date') or '',
        'due_date': inv.get('invoice_date_due') or '',
        'amount_untaxed': inv.get('amount_untaxed', 0),
        'amount_tax': inv.get('amount_tax', 0),
        'amount_total': inv.get('amount_total', 0),
        'amount_due': inv.get('amount_residual', 0),
        'notes': inv.get('narration') or '',
        'lines': lines,
    }


def tool_record_payment(args: dict) -> dict:
    """Register a payment against an invoice."""
    invoice_id = int(args['invoice_id'])
    amount = float(args['amount'])
    payment_date = args.get('payment_date', date.today().isoformat())
    memo = args.get('memo', '')

    logger.info(f'[PAYMENT] Recording: invoice={invoice_id} amount={amount}')

    client = _odoo()

    # Verify the invoice exists and is posted
    invoices = client.read('account.move', [invoice_id],
                           ['name', 'state', 'amount_residual', 'move_type'])
    if not invoices:
        return {'error': f'Invoice {invoice_id} not found'}

    inv = invoices[0]
    if inv['state'] != 'posted':
        return {
            'error': f'Invoice {inv["name"]} is in state "{inv["state"]}". '
                     'Only posted invoices can receive payments. '
                     'Confirm the invoice first in Odoo.'
        }
    if inv['amount_residual'] <= 0:
        return {'error': f'Invoice {inv["name"]} has no outstanding balance.'}

    # Use the register payment wizard via action_register_payment
    # First, create the payment register wizard
    ctx = {
        'active_model': 'account.move',
        'active_ids': [invoice_id],
    }

    wizard_id = client._call(
        'object', 'execute_kw',
        client.db, client.uid, client.password,
        'account.payment.register', 'create',
        [{'amount': amount, 'payment_date': payment_date}],
        {'context': ctx},
    )

    # Execute the payment
    client._call(
        'object', 'execute_kw',
        client.db, client.uid, client.password,
        'account.payment.register', 'action_create_payments',
        [[wizard_id]],
        {'context': ctx},
    )

    # Read back updated invoice
    updated = client.read('account.move', [invoice_id],
                          ['name', 'amount_residual', 'payment_state'])[0]

    logger.info(
        f'[PAYMENT] Recorded for {updated["name"]}: '
        f'remaining={updated["amount_residual"]} '
        f'payment_state={updated["payment_state"]}'
    )

    return {
        'status': 'payment_recorded',
        'invoice_id': invoice_id,
        'invoice_number': updated['name'],
        'amount_paid': amount,
        'amount_remaining': updated['amount_residual'],
        'payment_state': updated['payment_state'],
        'payment_date': payment_date,
    }


def tool_get_revenue_summary(args: dict) -> dict:
    """Get total revenue for a time period."""
    date_from = args.get('date_from', date.today().replace(day=1).isoformat())
    date_to = args.get('date_to', date.today().isoformat())

    logger.info(f'[REVENUE] Summary: {date_from} to {date_to}')

    client = _odoo()

    # Get all customer invoices in the period
    invoices = client.search_read(
        'account.move',
        [
            ['move_type', '=', 'out_invoice'],
            ['invoice_date', '>=', date_from],
            ['invoice_date', '<=', date_to],
            ['state', '=', 'posted'],
        ],
        ['amount_total', 'amount_residual', 'payment_state', 'partner_id'],
        order='invoice_date asc',
    )

    # Get credit notes (refunds) in the period
    refunds = client.search_read(
        'account.move',
        [
            ['move_type', '=', 'out_refund'],
            ['invoice_date', '>=', date_from],
            ['invoice_date', '<=', date_to],
            ['state', '=', 'posted'],
        ],
        ['amount_total'],
    )

    total_invoiced = sum(inv['amount_total'] for inv in invoices)
    total_collected = sum(
        inv['amount_total'] - inv['amount_residual'] for inv in invoices
    )
    total_outstanding = sum(inv['amount_residual'] for inv in invoices)
    total_refunded = sum(ref['amount_total'] for ref in refunds)
    net_revenue = total_invoiced - total_refunded

    # Top customers
    customer_totals: Dict[str, float] = {}
    for inv in invoices:
        cname = inv['partner_id'][1] if inv.get('partner_id') else 'Unknown'
        customer_totals[cname] = customer_totals.get(cname, 0) + inv['amount_total']
    top_customers = sorted(customer_totals.items(), key=lambda x: x[1], reverse=True)[:5]

    paid_count = sum(1 for inv in invoices if inv.get('payment_state') == 'paid')
    unpaid_count = len(invoices) - paid_count

    logger.info(
        f'[REVENUE] invoiced={total_invoiced} collected={total_collected} '
        f'outstanding={total_outstanding} refunded={total_refunded}'
    )

    return {
        'period': {'from': date_from, 'to': date_to},
        'total_invoiced': total_invoiced,
        'total_collected': total_collected,
        'total_outstanding': total_outstanding,
        'total_refunded': total_refunded,
        'net_revenue': net_revenue,
        'invoice_count': len(invoices),
        'paid_count': paid_count,
        'unpaid_count': unpaid_count,
        'refund_count': len(refunds),
        'top_customers': [
            {'name': name, 'total': total} for name, total in top_customers
        ],
    }


def tool_create_expense(args: dict) -> dict:
    """Record a business expense in Odoo."""
    description = args['description']
    amount = float(args['amount'])
    expense_date = args.get('date', date.today().isoformat())
    category = args.get('category', '')
    reference = args.get('reference', '')

    logger.info(f'[EXPENSE] Creating: desc="{description}" amount={amount}')

    client = _odoo()

    # Create as a vendor bill (in_invoice) — this is how Odoo CE tracks expenses
    # without the HR Expense module
    vals: Dict[str, Any] = {
        'move_type': 'in_invoice',
        'invoice_date': expense_date,
        'ref': reference or f'Expense: {description[:50]}',
        'invoice_line_ids': [(0, 0, {
            'name': description,
            'quantity': 1,
            'price_unit': amount,
        })],
    }

    # If a category/vendor name is given, resolve as partner
    if category:
        vals['partner_id'] = _resolve_partner(category)

    expense_id = client.create('account.move', vals)

    exp = client.read('account.move', [expense_id],
                      ['name', 'amount_total', 'state', 'invoice_date', 'ref'])[0]

    logger.info(f'[EXPENSE] Created id={expense_id} name={exp["name"]} total={exp["amount_total"]}')

    return {
        'status': 'created',
        'expense_id': expense_id,
        'bill_number': exp.get('name', ''),
        'description': description,
        'amount': exp['amount_total'],
        'date': exp.get('invoice_date') or '',
        'reference': exp.get('ref') or '',
        'state': exp['state'],
    }


def tool_get_expenses(args: dict) -> dict:
    """List business expenses (vendor bills) with optional filters."""
    vendor = args.get('vendor', '')
    status = args.get('status', '')
    date_from = args.get('date_from', '')
    date_to = args.get('date_to', '')
    limit = int(args.get('limit', 20))

    domain: list = [['move_type', 'in', ['in_invoice', 'in_refund']]]

    if vendor:
        domain.append(['partner_id.name', 'ilike', vendor])
    if status:
        state_map = {
            'draft': 'draft',
            'posted': 'posted',
            'cancelled': 'cancel',
            'canceled': 'cancel',
            'paid': 'posted',
        }
        mapped = state_map.get(status.lower(), status.lower())
        domain.append(['state', '=', mapped])
        if status.lower() == 'paid':
            domain.append(['payment_state', '=', 'paid'])
    if date_from:
        domain.append(['invoice_date', '>=', date_from])
    if date_to:
        domain.append(['invoice_date', '<=', date_to])

    logger.info(f'[EXPENSES] Listing: domain={domain} limit={limit}')

    client = _odoo()
    bills = client.search_read(
        'account.move', domain,
        ['name', 'partner_id', 'invoice_date', 'ref',
         'amount_total', 'amount_residual', 'state', 'payment_state'],
        limit=limit,
        order='invoice_date desc, id desc',
    )

    results = []
    for bill in bills:
        results.append({
            'id': bill['id'],
            'number': bill.get('name', ''),
            'vendor': bill['partner_id'][1] if bill.get('partner_id') else '',
            'date': bill.get('invoice_date') or '',
            'reference': bill.get('ref') or '',
            'amount_total': bill.get('amount_total', 0),
            'amount_due': bill.get('amount_residual', 0),
            'state': bill.get('state', ''),
            'payment_state': bill.get('payment_state', ''),
        })

    logger.info(f'[EXPENSES] Found {len(results)} expense(s)')

    return {
        'count': len(results),
        'expenses': results,
    }


# ---------------------------------------------------------------------------
# MCP protocol — JSON-RPC 2.0 over stdio
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        'name': 'create_invoice',
        'description': (
            'Create a customer invoice in Odoo. Returns the invoice ID, '
            'number, and total. The invoice is created in draft state — '
            'it must be confirmed in Odoo before payments can be recorded.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'customer_name': {
                    'type': 'string',
                    'description': 'Customer/company name. Will be matched or created in Odoo contacts.',
                },
                'amount': {
                    'type': 'number',
                    'description': 'Invoice amount (single line item price).',
                },
                'description': {
                    'type': 'string',
                    'description': 'Line item description (e.g. "Consulting services — January 2026").',
                    'default': 'Invoice',
                },
                'due_date': {
                    'type': 'string',
                    'description': 'Payment due date in YYYY-MM-DD format. Optional.',
                    'default': '',
                },
            },
            'required': ['customer_name', 'amount'],
        },
    },
    {
        'name': 'get_invoices',
        'description': (
            'List customer invoices from Odoo with optional filters. '
            'Returns invoice number, customer, amounts, and payment status.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'customer': {
                    'type': 'string',
                    'description': 'Filter by customer name (partial match). Optional.',
                    'default': '',
                },
                'status': {
                    'type': 'string',
                    'description': 'Filter by status: draft, posted, paid, cancelled. Optional.',
                    'default': '',
                },
                'date_from': {
                    'type': 'string',
                    'description': 'Start date filter (YYYY-MM-DD). Optional.',
                    'default': '',
                },
                'date_to': {
                    'type': 'string',
                    'description': 'End date filter (YYYY-MM-DD). Optional.',
                    'default': '',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Max number of invoices to return.',
                    'default': 20,
                },
            },
            'required': [],
        },
    },
    {
        'name': 'get_invoice_by_id',
        'description': (
            'Get full details of a specific invoice including all line items, '
            'amounts, tax breakdown, and payment status.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'invoice_id': {
                    'type': 'integer',
                    'description': 'The Odoo ID of the invoice (account.move record).',
                },
            },
            'required': ['invoice_id'],
        },
    },
    {
        'name': 'record_payment',
        'description': (
            'Register a payment received against a posted invoice. '
            'The invoice must be in "posted" state (confirmed). '
            'Use get_invoices first to find the invoice ID.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'invoice_id': {
                    'type': 'integer',
                    'description': 'The Odoo ID of the invoice to pay.',
                },
                'amount': {
                    'type': 'number',
                    'description': 'Payment amount. Can be partial.',
                },
                'payment_date': {
                    'type': 'string',
                    'description': 'Payment date in YYYY-MM-DD format. Defaults to today.',
                    'default': '',
                },
                'memo': {
                    'type': 'string',
                    'description': 'Payment reference or memo. Optional.',
                    'default': '',
                },
            },
            'required': ['invoice_id', 'amount'],
        },
    },
    {
        'name': 'get_revenue_summary',
        'description': (
            'Get a revenue summary for a date range: total invoiced, '
            'collected, outstanding, refunded, net revenue, and top customers. '
            'Defaults to the current month.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'date_from': {
                    'type': 'string',
                    'description': 'Start date (YYYY-MM-DD). Defaults to 1st of current month.',
                    'default': '',
                },
                'date_to': {
                    'type': 'string',
                    'description': 'End date (YYYY-MM-DD). Defaults to today.',
                    'default': '',
                },
            },
            'required': [],
        },
    },
    {
        'name': 'create_expense',
        'description': (
            'Record a business expense as a vendor bill in Odoo. '
            'Created in draft state for review.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'description': {
                    'type': 'string',
                    'description': 'Expense description (e.g. "Office supplies", "Cloud hosting — Feb 2026").',
                },
                'amount': {
                    'type': 'number',
                    'description': 'Expense amount.',
                },
                'date': {
                    'type': 'string',
                    'description': 'Expense date (YYYY-MM-DD). Defaults to today.',
                    'default': '',
                },
                'category': {
                    'type': 'string',
                    'description': 'Vendor or expense category name. Optional.',
                    'default': '',
                },
                'reference': {
                    'type': 'string',
                    'description': 'External reference (receipt number, PO number). Optional.',
                    'default': '',
                },
            },
            'required': ['description', 'amount'],
        },
    },
    {
        'name': 'get_expenses',
        'description': (
            'List business expenses (vendor bills) from Odoo with optional '
            'filters by vendor, status, and date range.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'vendor': {
                    'type': 'string',
                    'description': 'Filter by vendor/category name (partial match). Optional.',
                    'default': '',
                },
                'status': {
                    'type': 'string',
                    'description': 'Filter by status: draft, posted, paid, cancelled. Optional.',
                    'default': '',
                },
                'date_from': {
                    'type': 'string',
                    'description': 'Start date filter (YYYY-MM-DD). Optional.',
                    'default': '',
                },
                'date_to': {
                    'type': 'string',
                    'description': 'End date filter (YYYY-MM-DD). Optional.',
                    'default': '',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Max number of expenses to return.',
                    'default': 20,
                },
            },
            'required': [],
        },
    },
]

TOOL_HANDLERS = {
    'create_invoice': tool_create_invoice,
    'get_invoices': tool_get_invoices,
    'get_invoice_by_id': tool_get_invoice_by_id,
    'record_payment': tool_record_payment,
    'get_revenue_summary': tool_get_revenue_summary,
    'create_expense': tool_create_expense,
    'get_expenses': tool_get_expenses,
}

SERVER_INFO = {
    'name': 'odoo-mcp-server',
    'version': '1.0.0',
}

SERVER_CAPABILITIES = {
    'tools': {},
}


def _make_response(req_id, result):
    return {'jsonrpc': '2.0', 'id': req_id, 'result': result}


def _make_error(req_id, code, message):
    return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': code, 'message': message}}


def handle_message(msg: dict) -> dict | None:
    """Process a single JSON-RPC 2.0 request and return the response."""
    method = msg.get('method', '')
    req_id = msg.get('id')
    params = msg.get('params', {})

    logger.info(f'[RPC] method={method} id={req_id}')

    # --- initialize handshake ---
    if method == 'initialize':
        return _make_response(req_id, {
            'protocolVersion': '2024-11-05',
            'capabilities': SERVER_CAPABILITIES,
            'serverInfo': SERVER_INFO,
        })

    # --- notifications (no response expected) ---
    if method == 'notifications/initialized':
        logger.info('[RPC] Client confirmed initialized')
        return None
    if method == 'notifications/cancelled':
        logger.info(f'[RPC] Request cancelled: {params}')
        return None

    # --- list tools ---
    if method == 'tools/list':
        return _make_response(req_id, {'tools': TOOL_DEFINITIONS})

    # --- call a tool ---
    if method == 'tools/call':
        tool_name = params.get('name', '')
        arguments = params.get('arguments', {})
        handler = TOOL_HANDLERS.get(tool_name)

        if handler is None:
            return _make_error(req_id, -32601, f'Unknown tool: {tool_name}')

        try:
            result = handler(arguments)
            return _make_response(req_id, {
                'content': [
                    {'type': 'text', 'text': json.dumps(result, indent=2, default=str)}
                ],
            })
        except Exception as e:
            logger.error(f'[RPC] Tool error ({tool_name}): {e}', exc_info=True)
            return _make_response(req_id, {
                'content': [
                    {'type': 'text', 'text': json.dumps({'error': str(e)}, default=str)}
                ],
                'isError': True,
            })

    # --- ping ---
    if method == 'ping':
        return _make_response(req_id, {})

    # Unknown method
    return _make_error(req_id, -32601, f'Method not found: {method}')


def run_stdio():
    """
    Main MCP stdio loop.

    Reads newline-delimited JSON-RPC messages from stdin, processes them,
    and writes responses to stdout.
    """
    logger.info('=' * 60)
    logger.info('Odoo MCP Server starting (stdio transport)')
    logger.info(f'Odoo URL:    {ODOO_URL}')
    logger.info(f'Odoo DB:     {ODOO_DB}')
    logger.info(f'Odoo User:   {ODOO_USERNAME}')
    logger.info('=' * 60)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning(f'[RPC] Invalid JSON: {e}')
            resp = _make_error(None, -32700, f'Parse error: {e}')
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()
            continue

        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()
            logger.info(f'[RPC] Responded to {msg.get("method")} (id={msg.get("id")})')

    logger.info('Odoo MCP Server shutting down (stdin closed)')


if __name__ == '__main__':
    run_stdio()
