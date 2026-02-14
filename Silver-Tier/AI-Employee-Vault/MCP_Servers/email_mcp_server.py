"""
Email MCP Server — AI Employee Vault (Silver Tier)

A Model Context Protocol (MCP) server that exposes Gmail operations as tools
so that Claude Code (or any MCP client) can send emails, create drafts, and
retrieve messages through the approved Gmail API credentials.

Tools provided:
    send_email   — Send an email via Gmail
    draft_email  — Create a Gmail draft (for human review before sending)
    get_email    — Retrieve a single email by its Gmail message ID

Transport: stdio  (launched by Claude Code as a subprocess)

SETUP:
    1.  pip install mcp google-api-python-client google-auth-oauthlib python-dotenv
    2.  Ensure credentials.json exists in the vault root
    3.  On first run, a browser window will open for OAuth consent
        (the server needs gmail.send + gmail.compose + gmail.readonly scopes,
         so a new token_mcp.json will be created alongside the existing token.json)
    4.  Register this server in Claude Code — see mcp_config.json

Environment Variables (from .env):
    GMAIL_CREDENTIALS_PATH   Path to credentials.json
    GMAIL_MCP_TOKEN_PATH     Path to the MCP-specific OAuth token (default: token_mcp.json)
    LOGS_DIR                 Folder for email_mcp.log
"""

import os
import sys
import json
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths & environment
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR.parent.resolve()
load_dotenv(VAULT_ROOT / '.env')

CREDENTIALS_PATH = Path(
    os.getenv('GMAIL_CREDENTIALS_PATH', str(VAULT_ROOT / 'credentials.json'))
)
TOKEN_PATH = Path(
    os.getenv('GMAIL_MCP_TOKEN_PATH', str(VAULT_ROOT / 'token_mcp.json'))
)
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(VAULT_ROOT / 'Logs')))
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging  — file-only so we never pollute the stdio MCP channel
# ---------------------------------------------------------------------------

logger = logging.getLogger('email_mcp')
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(str(LOGS_DIR / 'email_mcp.log'))
_fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(_fh)

# ---------------------------------------------------------------------------
# Gmail API helpers
# ---------------------------------------------------------------------------

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.readonly',
]


def _get_gmail_service():
    """
    Build an authenticated Gmail API service.

    Uses token_mcp.json (separate from the watcher's token.json) because this
    server requires broader scopes (send + compose + readonly).
    """
    from google.oauth2.credentials import Credentials as UserCredentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None

    if TOKEN_PATH.exists():
        try:
            creds = UserCredentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
            logger.info('Loaded existing MCP token')
        except Exception as e:
            logger.warning(f'Failed to load MCP token: {e}')
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            logger.info('MCP token refreshed')
        except Exception as e:
            logger.warning(f'Token refresh failed: {e}')
            creds = None

    if not creds or not creds.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f'credentials.json not found at {CREDENTIALS_PATH}. '
                'Download it from Google Cloud Console.'
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
        logger.info('New MCP token saved')

    service = build('gmail', 'v1', credentials=creds)
    logger.info('Gmail API service built for MCP')
    return service


# Lazy singleton — created on first tool call
_service = None


def _gmail():
    global _service
    if _service is None:
        _service = _get_gmail_service()
    return _service


def _build_mime_message(to: str, subject: str, body: str,
                        cc: str = '', bcc: str = '') -> MIMEMultipart:
    """Construct a MIME message ready for the Gmail API."""
    msg = MIMEMultipart()
    msg['To'] = to
    msg['Subject'] = subject
    if cc:
        msg['Cc'] = cc
    if bcc:
        msg['Bcc'] = bcc
    msg.attach(MIMEText(body, 'plain'))
    return msg

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def tool_send_email(arguments: dict) -> dict[str, Any]:
    """Send an email via Gmail API."""
    to = arguments['to']
    subject = arguments['subject']
    body = arguments['body']
    cc = arguments.get('cc', '')
    bcc = arguments.get('bcc', '')

    logger.info(f'[SEND] to={to} subject="{subject}"')

    mime = _build_mime_message(to, subject, body, cc, bcc)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    result = _gmail().users().messages().send(
        userId='me', body={'raw': raw}
    ).execute()

    msg_id = result.get('id', 'unknown')
    logger.info(f'[SEND] Success — message_id={msg_id}')

    return {
        'status': 'sent',
        'message_id': msg_id,
        'to': to,
        'subject': subject,
    }


def tool_draft_email(arguments: dict) -> dict[str, Any]:
    """Create a Gmail draft (not sent — for human review)."""
    to = arguments['to']
    subject = arguments['subject']
    body = arguments['body']
    cc = arguments.get('cc', '')
    bcc = arguments.get('bcc', '')

    logger.info(f'[DRAFT] to={to} subject="{subject}"')

    mime = _build_mime_message(to, subject, body, cc, bcc)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    result = _gmail().users().drafts().create(
        userId='me', body={'message': {'raw': raw}}
    ).execute()

    draft_id = result.get('id', 'unknown')
    msg_id = result.get('message', {}).get('id', 'unknown')
    logger.info(f'[DRAFT] Success — draft_id={draft_id}')

    return {
        'status': 'draft_created',
        'draft_id': draft_id,
        'message_id': msg_id,
        'to': to,
        'subject': subject,
    }


def tool_get_email(arguments: dict) -> dict[str, Any]:
    """Retrieve a single email by Gmail message ID."""
    message_id = arguments['message_id']
    logger.info(f'[GET] message_id={message_id}')

    msg = _gmail().users().messages().get(
        userId='me', id=message_id, format='full'
    ).execute()

    headers = {h['name']: h['value'] for h in msg['payload']['headers']}

    # Extract plain-text body
    body = ''
    payload = msg['payload']
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and 'data' in part['body']:
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='replace')
                break
    elif 'body' in payload and 'data' in payload['body']:
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='replace')

    if not body:
        body = msg.get('snippet', '')

    logger.info(f'[GET] Success — from={headers.get("From", "?")}')

    return {
        'message_id': message_id,
        'from': headers.get('From', ''),
        'to': headers.get('To', ''),
        'subject': headers.get('Subject', ''),
        'date': headers.get('Date', ''),
        'body': body[:3000],
        'snippet': msg.get('snippet', ''),
        'label_ids': msg.get('labelIds', []),
    }

# ---------------------------------------------------------------------------
# MCP protocol  — JSON-RPC 2.0 over stdio
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        'name': 'send_email',
        'description': (
            'Send an email via Gmail. Use this only for APPROVED outbound '
            'messages — all sends are logged.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'to':      {'type': 'string', 'description': 'Recipient email address'},
                'subject': {'type': 'string', 'description': 'Email subject line'},
                'body':    {'type': 'string', 'description': 'Plain-text email body'},
                'cc':      {'type': 'string', 'description': 'CC recipients (comma-separated)', 'default': ''},
                'bcc':     {'type': 'string', 'description': 'BCC recipients (comma-separated)', 'default': ''},
            },
            'required': ['to', 'subject', 'body'],
        },
    },
    {
        'name': 'draft_email',
        'description': (
            'Create a Gmail draft for human review. The draft appears in Gmail '
            'but is NOT sent until a human clicks Send.'
        ),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'to':      {'type': 'string', 'description': 'Recipient email address'},
                'subject': {'type': 'string', 'description': 'Email subject line'},
                'body':    {'type': 'string', 'description': 'Plain-text email body'},
                'cc':      {'type': 'string', 'description': 'CC recipients (comma-separated)', 'default': ''},
                'bcc':     {'type': 'string', 'description': 'BCC recipients (comma-separated)', 'default': ''},
            },
            'required': ['to', 'subject', 'body'],
        },
    },
    {
        'name': 'get_email',
        'description': 'Retrieve a single email by its Gmail message ID.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'message_id': {'type': 'string', 'description': 'Gmail message ID'},
            },
            'required': ['message_id'],
        },
    },
]

TOOL_HANDLERS = {
    'send_email': tool_send_email,
    'draft_email': tool_draft_email,
    'get_email': tool_get_email,
}

SERVER_INFO = {
    'name': 'email-mcp-server',
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
                    {'type': 'text', 'text': json.dumps(result, indent=2)}
                ],
            })
        except Exception as e:
            logger.error(f'[RPC] Tool error ({tool_name}): {e}', exc_info=True)
            return _make_response(req_id, {
                'content': [
                    {'type': 'text', 'text': json.dumps({'error': str(e)})}
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
    logger.info('Email MCP Server starting (stdio transport)')
    logger.info(f'Credentials: {CREDENTIALS_PATH}')
    logger.info(f'MCP token:   {TOKEN_PATH}')
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
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()
            logger.info(f'[RPC] Responded to {msg.get("method")} (id={msg.get("id")})')

    logger.info('Email MCP Server shutting down (stdin closed)')


if __name__ == '__main__':
    run_stdio()
