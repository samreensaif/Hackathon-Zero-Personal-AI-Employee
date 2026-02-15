"""
Test script for the Odoo MCP Server.

Launches odoo_mcp_server.py as a subprocess, sends MCP requests over stdio,
and prints the results.  Exit code 0 = all tests passed.

Usage:
    python test_odoo_mcp.py
"""

import json
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
SERVER_SCRIPT = SCRIPT_DIR / 'MCP_Servers' / 'odoo_mcp_server.py'

# ── helpers ────────────────────────────────────────────────────────────────

_req_id = 0


def _next_id() -> int:
    global _req_id
    _req_id += 1
    return _req_id


def send(proc: subprocess.Popen, method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC message to the MCP server and read the response."""
    msg = {
        'jsonrpc': '2.0',
        'id': _next_id(),
        'method': method,
    }
    if params is not None:
        msg['params'] = params

    line = json.dumps(msg) + '\n'
    proc.stdin.write(line)
    proc.stdin.flush()

    # Read one response line (block until available)
    resp_line = proc.stdout.readline()
    if not resp_line:
        raise RuntimeError('Server closed stdout — no response received')
    return json.loads(resp_line)


def print_section(title: str):
    print()
    print('=' * 60)
    print(f'  {title}')
    print('=' * 60)


def print_result(resp: dict):
    """Pretty-print the content from an MCP tool response."""
    if 'error' in resp:
        print(f'  ERROR: {resp["error"]}')
        return None

    result = resp.get('result', {})
    if result.get('isError'):
        content = result.get('content', [{}])[0].get('text', '{}')
        print(f'  TOOL ERROR: {content}')
        return json.loads(content)

    content_text = result.get('content', [{}])[0].get('text', '{}')
    data = json.loads(content_text)
    print(json.dumps(data, indent=2))
    return data


# ── main ───────────────────────────────────────────────────────────────────

def main():
    if not SERVER_SCRIPT.exists():
        print(f'ERROR: Server script not found at {SERVER_SCRIPT}')
        sys.exit(1)

    print_section('Starting Odoo MCP Server')
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(SCRIPT_DIR),
    )
    print(f'  PID: {proc.pid}')

    failures = 0

    try:
        # ── 1. Initialize handshake ───────────────────────────────────
        print_section('1. MCP Initialize Handshake')
        resp = send(proc, 'initialize', {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'test-client', 'version': '0.1'},
        })
        info = resp.get('result', {}).get('serverInfo', {})
        print(f'  Server: {info.get("name")} v{info.get("version")}')

        # Send initialized notification (no response expected)
        init_msg = json.dumps({
            'jsonrpc': '2.0',
            'method': 'notifications/initialized',
            'params': {},
        }) + '\n'
        proc.stdin.write(init_msg)
        proc.stdin.flush()
        time.sleep(0.2)

        # ── 2. List tools ─────────────────────────────────────────────
        print_section('2. List Available Tools')
        resp = send(proc, 'tools/list')
        tools = resp.get('result', {}).get('tools', [])
        for t in tools:
            req = t.get('inputSchema', {}).get('required', [])
            print(f'  - {t["name"]:25s} required={req}')
        print(f'\n  Total: {len(tools)} tools')

        if len(tools) != 7:
            print(f'  WARN: Expected 7 tools, got {len(tools)}')

        # ── 3. Create invoice ─────────────────────────────────────────
        print_section('3. Create Invoice')
        due = (date.today() + timedelta(days=30)).isoformat()
        resp = send(proc, 'tools/call', {
            'name': 'create_invoice',
            'arguments': {
                'customer_name': 'Test Client A',
                'amount': 500,
                'description': 'Consulting Services - January 2026',
                'due_date': due,
            },
        })
        inv_data = print_result(resp)
        if inv_data and inv_data.get('status') == 'created':
            invoice_id = inv_data['invoice_id']
            print(f'\n  Invoice created: id={invoice_id} '
                  f'number={inv_data.get("invoice_number")} '
                  f'total={inv_data.get("amount_total")}')
        else:
            print('\n  FAILED to create invoice')
            failures += 1
            invoice_id = None

        # ── 4. Get invoice by id ──────────────────────────────────────
        if invoice_id:
            print_section('4. Get Invoice by ID')
            resp = send(proc, 'tools/call', {
                'name': 'get_invoice_by_id',
                'arguments': {'invoice_id': invoice_id},
            })
            detail = print_result(resp)
            if detail and detail.get('id') == invoice_id:
                print(f'\n  Lines: {len(detail.get("lines", []))}')
            else:
                print('\n  FAILED to read invoice')
                failures += 1

        # ── 5. List invoices ──────────────────────────────────────────
        print_section('5. List Invoices (all)')
        resp = send(proc, 'tools/call', {
            'name': 'get_invoices',
            'arguments': {'limit': 5},
        })
        list_data = print_result(resp)
        if list_data:
            print(f'\n  Returned {list_data.get("count", 0)} invoice(s)')
        else:
            failures += 1

        # ── 6. Create expense ─────────────────────────────────────────
        print_section('6. Create Expense')
        resp = send(proc, 'tools/call', {
            'name': 'create_expense',
            'arguments': {
                'description': 'Cloud hosting - February 2026',
                'amount': 49.99,
                'category': 'AWS',
                'date': date.today().isoformat(),
            },
        })
        exp_data = print_result(resp)
        if exp_data and exp_data.get('status') == 'created':
            print(f'\n  Expense created: id={exp_data["expense_id"]} '
                  f'amount={exp_data.get("amount")}')
        else:
            print('\n  FAILED to create expense')
            failures += 1

        # ── 7. List expenses ─────────────────────────────────────────
        print_section('7. List Expenses')
        resp = send(proc, 'tools/call', {
            'name': 'get_expenses',
            'arguments': {'limit': 5},
        })
        exp_list = print_result(resp)
        if exp_list:
            print(f'\n  Returned {exp_list.get("count", 0)} expense(s)')
        else:
            failures += 1

        # ── 8. Revenue summary ────────────────────────────────────────
        print_section('8. Revenue Summary (current month)')
        resp = send(proc, 'tools/call', {
            'name': 'get_revenue_summary',
            'arguments': {},
        })
        rev_data = print_result(resp)
        if rev_data and 'total_invoiced' in rev_data:
            print(f'\n  Invoiced: {rev_data["total_invoiced"]}  '
                  f'Collected: {rev_data["total_collected"]}  '
                  f'Outstanding: {rev_data["total_outstanding"]}')
        else:
            print('\n  FAILED to get revenue summary')
            failures += 1

    except Exception as e:
        print(f'\n  EXCEPTION: {e}')
        import traceback
        traceback.print_exc()
        failures += 1

    finally:
        # ── Shutdown ──────────────────────────────────────────────────
        print_section('Shutting Down')
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print(f'  Server exited with code {proc.returncode}')

        # Print any stderr from the server
        stderr = proc.stderr.read()
        if stderr.strip():
            print(f'\n  Server stderr:\n{stderr[:500]}')

    # ── Summary ───────────────────────────────────────────────────────
    print_section('Test Summary')
    if failures == 0:
        print('  All tests PASSED')
    else:
        print(f'  {failures} test(s) FAILED')
    print()
    sys.exit(failures)


if __name__ == '__main__':
    main()
