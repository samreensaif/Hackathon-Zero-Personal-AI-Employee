"""
Gmail Watcher for AI Employee Vault

This script monitors your Gmail inbox for unread important emails and automatically
creates task files in the Needs_Action folder.

SETUP INSTRUCTIONS:
1. Create a Gmail API project in Google Cloud Console (https://console.cloud.google.com/)
2. Enable the Gmail API
3. Create an OAuth 2.0 Desktop Application credential
4. Download the credentials JSON file as 'credentials.json'
5. Place credentials.json in the same directory as this script
6. Run the script - it will open a browser for authentication on first run
7. A token.json file will be created automatically for future runs

Environment Variables:
- GMAIL_CREDENTIALS_PATH: Path to your credentials.json file (optional, defaults to ./credentials.json)
- GMAIL_CHECK_INTERVAL: Check interval in seconds (optional, defaults to 120)

TROUBLESHOOTING:
- If the script stops detecting new emails, check if they're marked as already processed
- To reset processed emails tracking: delete processed_emails.json and restart the script
- To see which emails have been processed: view processed_emails.json
- Emails remain in the processed list until you delete the file or mark them as read in Gmail
"""

import os
import json
import time
import logging
import base64
from datetime import datetime
from pathlib import Path
from typing import Optional, Set
from dotenv import load_dotenv

# Gmail API imports
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Load environment variables from .env file in vault root
SCRIPT_DIR_ENV = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR_ENV.parent.resolve()
load_dotenv(VAULT_ROOT / '.env')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('gmail_watcher.log')
    ]
)
logger = logging.getLogger(__name__)

# Configuration
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
SCRIPT_DIR = Path(__file__).parent.resolve()
CREDENTIALS_PATH = os.getenv('GMAIL_CREDENTIALS_PATH', str(SCRIPT_DIR / 'credentials.json'))
CHECK_INTERVAL = int(os.getenv('GMAIL_CHECK_INTERVAL', 120))  # 2 minutes default
PROCESSED_EMAILS_FILE = str(SCRIPT_DIR / 'processed_emails.json')
NEEDS_ACTION_FOLDER = Path(os.getenv('NEEDS_ACTION_DIR', str(VAULT_ROOT / 'Needs_Action')))

# Ensure Needs_Action folder exists
NEEDS_ACTION_FOLDER.mkdir(parents=True, exist_ok=True)
logger.info(f"Needs_Action folder path: {NEEDS_ACTION_FOLDER.absolute()}")


def load_credentials():
    """
    Load Gmail API credentials.
    Handles OAuth 2.0 authentication flow with token refresh.

    Returns:
        Credentials object or None if authentication fails
    """
    creds = None
    token_path = SCRIPT_DIR / 'token.json'

    # Load existing token if available
    if token_path.exists():
        try:
            creds = UserCredentials.from_authorized_user_file(str(token_path), SCOPES)
            logger.info("Loaded existing credentials from token.json")
        except Exception as e:
            logger.warning(f"Failed to load existing token: {e}")
            creds = None

    # Refresh token if needed
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            logger.info("Token refreshed successfully")
        except RefreshError as e:
            logger.error(f"Failed to refresh token: {e}")
            creds = None

    # Perform fresh authentication if no valid credentials
    if not creds or not creds.valid:
        if not Path(CREDENTIALS_PATH).exists():
            logger.error(f"Credentials file not found at {CREDENTIALS_PATH}")
            logger.error("Please download credentials.json from Google Cloud Console")
            return None

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)

            # Save credentials for future use
            with open(token_path, 'w') as token:
                token.write(creds.to_json())
            logger.info("New credentials obtained and saved to token.json")
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return None

    return creds


def get_gmail_service(creds):
    """
    Build and return Gmail API service.

    Args:
        creds: Credentials object

    Returns:
        Gmail API service or None if build fails
    """
    try:
        # Build with custom timeout settings
        import socket
        socket.setdefaulttimeout(60)  # 60 second timeout
        service = build('gmail', 'v1', credentials=creds)
        logger.debug("Gmail API service built successfully")
        return service
    except Exception as e:
        logger.error(f"Failed to build Gmail API service: {e}")
        return None


def load_processed_emails() -> Set[str]:
    """Load set of already processed email IDs to avoid duplicates."""
    if Path(PROCESSED_EMAILS_FILE).exists():
        try:
            with open(PROCESSED_EMAILS_FILE, 'r') as f:
                data = json.load(f)
                return set(data.get('processed_ids', []))
        except Exception as e:
            logger.warning(f"Failed to load processed emails: {e}")
    return set()


def save_processed_emails(processed_ids: Set[str]):
    """Save set of processed email IDs."""
    try:
        with open(PROCESSED_EMAILS_FILE, 'w') as f:
            json.dump({'processed_ids': list(processed_ids)}, f)
    except Exception as e:
        logger.error(f"Failed to save processed emails: {e}")


def decode_email_part(data: str) -> str:
    """Decode base64 encoded email part."""
    try:
        return base64.urlsafe_b64decode(data).decode('utf-8')
    except Exception:
        return data


def get_email_body(service, user_id: str, message_id: str) -> str:
    """
    Extract email body from message.

    Args:
        service: Gmail API service
        user_id: Gmail user ID (typically 'me')
        message_id: ID of the message

    Returns:
        Email body string
    """
    try:
        message = service.users().messages().get(userId=user_id, id=message_id, format='full').execute()
        payload = message['payload']

        # Try to get body from parts
        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/plain':
                    if 'data' in part['body']:
                        return decode_email_part(part['body']['data'])[:500]  # Limit to 500 chars

        # Try to get body directly
        if 'body' in payload and 'data' in payload['body']:
            return decode_email_part(payload['body']['data'])[:500]

        return message.get('snippet', 'No body available')[:500]
    except Exception as e:
        logger.warning(f"Failed to get email body: {e}")
        return "Failed to retrieve email body"


def create_task_file(email_data: dict) -> bool:
    """
    Create a markdown task file in Needs_Action folder.

    Args:
        email_data: Dictionary with email information

    Returns:
        True if successful, False otherwise
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sender = email_data.get('sender', 'Unknown').replace('<', '').replace('>', '').split()[-1][:20]
        filename = f"{timestamp}_{sender}_action.md"
        filepath = NEEDS_ACTION_FOLDER / filename

        # Create markdown content
        content = f"""# Email Action Required

**From:** {email_data.get('sender', 'Unknown')}
**Subject:** {email_data.get('subject', 'No Subject')}
**Received:** {email_data.get('timestamp', '')}
**Email ID:** {email_data.get('email_id', 'Unknown')}

## Preview
{email_data.get('snippet', 'No preview available')}

## Suggested Actions
- [ ] Reply to sender
- [ ] Forward to relevant person
- [ ] Archive this email
- [ ] Mark as spam

## Notes
_Add your notes and actions here_

---
*Auto-generated by Gmail Watcher at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.info(f"[OK] Created task file: {filename}")
        return True
    except Exception as e:
        logger.error(f"Failed to create task file: {e}")
        logger.error(f"Target directory: {NEEDS_ACTION_FOLDER.absolute()}")
        logger.error(f"Directory exists: {NEEDS_ACTION_FOLDER.exists()}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False


def check_emails(service, processed_ids: Set[str]) -> tuple[bool, Set[str]]:
    """
    Check for unread important emails.

    Args:
        service: Gmail API service
        processed_ids: Set of already processed email IDs

    Returns:
        Tuple of (success: bool, updated_processed_ids: Set[str])
    """
    try:
        # Query for unread important emails with retry logic
        import socket
        max_retries = 3
        retry_count = 0
        results = None

        while retry_count < max_retries:
            try:
                results = service.users().messages().list(
                    userId='me',
                    q='is:unread is:important',
                    maxResults=10
                ).execute()
                break  # Success, exit retry loop
            except (socket.timeout, ConnectionError, OSError) as e:
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = retry_count * 5  # 5, 10, 15 seconds
                    logger.warning(f"[RETRY] Network error (attempt {retry_count}/{max_retries}): {e}")
                    logger.warning(f"[RETRY] Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                else:
                    raise  # Re-raise if max retries exceeded

        if results is None:
            logger.error("[ERROR] Failed to fetch emails after retries")
            return False, processed_ids

        messages = results.get('messages', [])

        if not messages:
            logger.info("[OK] No unread important emails found")
            return True, processed_ids

        logger.info(f"[FOUND] {len(messages)} unread important email(s)")

        # Track statistics
        already_processed_count = 0
        newly_processed_count = 0

        # Process each email
        for message in messages:
            msg_id = message['id']

            # Skip if already processed
            if msg_id in processed_ids:
                already_processed_count += 1
                logger.info(f"[SKIP] Email {msg_id[:8]}... already processed, skipping")
                continue

            try:
                # Fetch full message details
                msg = service.users().messages().get(userId='me', id=msg_id, format='metadata', metadataHeaders=['From', 'Subject', 'Date']).execute()

                headers = {h['name']: h['value'] for h in msg['payload']['headers']}

                email_data = {
                    'email_id': msg_id,
                    'sender': headers.get('From', 'Unknown'),
                    'subject': headers.get('Subject', 'No Subject'),
                    'timestamp': headers.get('Date', datetime.now().isoformat()),
                    'snippet': msg.get('snippet', 'No preview'),
                }

                logger.info(f"[NEW] Processing: {email_data['subject'][:50]} - From: {email_data['sender'][:30]}")

                # Get email body
                body = get_email_body(service, 'me', msg_id)
                email_data['snippet'] = body

                # Create task file
                if create_task_file(email_data):
                    processed_ids.add(msg_id)
                    newly_processed_count += 1
                else:
                    logger.warning(f"[FAIL] Failed to create task file for email {msg_id}")

            except HttpError as e:
                if e.resp.status == 404:
                    logger.warning(f"Email {msg_id} not found (already deleted?)")
                    processed_ids.add(msg_id)
                else:
                    logger.error(f"HTTP error processing email {msg_id}: {e}")
            except Exception as e:
                logger.error(f"Error processing email {msg_id}: {e}")

        # Log summary
        logger.info(f"[SUMMARY] {newly_processed_count} new, {already_processed_count} already processed, {len(messages)} total")

        return True, processed_ids

    except HttpError as e:
        logger.error(f"[ERROR] Gmail API error: {e}")
        if e.resp.status == 401:
            logger.error("[ERROR] Authentication failed - token may be expired")
            logger.error("[ERROR] Delete token.json and restart to re-authenticate")
        return False, processed_ids
    except (ConnectionError, OSError) as e:
        logger.error(f"[ERROR] Network connection error: {e}")
        logger.error("[ERROR] Please check your internet connection")
        logger.error("[ERROR] If using a firewall/VPN, ensure Python can access gmail.googleapis.com")
        return False, processed_ids
    except Exception as e:
        logger.error(f"[ERROR] Unexpected error checking emails: {e}")
        import traceback
        logger.error(f"[ERROR] Traceback: {traceback.format_exc()}")
        return False, processed_ids


def run_watcher():
    """Main watcher loop - runs continuously."""
    logger.info("=" * 60)
    logger.info("Gmail Watcher Started (Gold Tier)")
    logger.info(f"Check interval: {CHECK_INTERVAL} seconds")
    logger.info(f"Credentials path: {CREDENTIALS_PATH}")
    logger.info("=" * 60)

    # Load credentials
    creds = load_credentials()
    if not creds:
        logger.error("Failed to load credentials. Exiting.")
        return

    # Build Gmail service
    service = get_gmail_service(creds)
    if not service:
        logger.error("Failed to build Gmail service. Exiting.")
        return

    logger.info("Gmail service initialized successfully")

    # Test connection
    logger.info("[TEST] Testing connection to Gmail API...")
    try:
        service.users().getProfile(userId='me').execute()
        logger.info("[TEST] Connection test successful!")
    except Exception as e:
        logger.error(f"[TEST] Connection test failed: {e}")
        logger.error("[TEST] Please check your internet connection and firewall settings")
        logger.error("[TEST] Continuing anyway, but you may experience connection issues...")

    # Load previously processed emails
    processed_ids = load_processed_emails()
    logger.info(f"[LOADED] {len(processed_ids)} previously processed email IDs")
    if len(processed_ids) > 0:
        logger.info(f"[TIP] To reset tracking, delete '{PROCESSED_EMAILS_FILE}' and restart")

    # Main loop
    try:
        while True:
            logger.info(f"[CHECK] Checking for unread important emails... ({datetime.now().strftime('%H:%M:%S')})")

            success, processed_ids = check_emails(service, processed_ids)

            if success:
                save_processed_emails(processed_ids)
                logger.info(f"[WAIT] Waiting {CHECK_INTERVAL} seconds until next check...")
            else:
                logger.warning("[WARN] Failed to check emails, will retry")

            # Wait for next check
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info("Gmail Watcher stopped by user")
        save_processed_emails(processed_ids)
    except Exception as e:
        logger.error(f"Fatal error in watcher loop: {e}")
        save_processed_emails(processed_ids)


if __name__ == '__main__':
    run_watcher()
