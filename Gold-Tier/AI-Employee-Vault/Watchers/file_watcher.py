"""
File System Watcher for AI Employee Vault (Gold Tier)

This script monitors the Inbox folder for new files and automatically
creates task files in the Needs_Action folder with metadata and suggested actions.
The original file is moved from Inbox into Needs_Action for processing.

SETUP INSTRUCTIONS:
1. Install dependencies: pip install -r requirements.txt (needs watchdog, python-dotenv)
2. Ensure .env file exists in the AI-Employee-Vault root with correct paths
3. Run the script: python file_watcher.py

Environment Variables (from .env):
- INBOX_DIR: Path to the Inbox folder to monitor
- NEEDS_ACTION_DIR: Path to the Needs_Action output folder
- LOGS_DIR: Path to the Logs folder
- FILE_CHECK_INTERVAL: Stability check interval in seconds (default: 10)

TROUBLESHOOTING:
- If files are not detected, verify INBOX_DIR path in .env
- To reset processed file tracking: delete processed_files.json and restart
- Check file_watcher.log in Logs/ for detailed activity history
- Large files may take a moment to appear (the watcher waits for writes to finish)
"""

import os
import json
import shutil
import time
import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Set

from dotenv import load_dotenv
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

# Load .env from the vault root (two levels up from Watchers/)
SCRIPT_DIR = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR.parent.resolve()
load_dotenv(VAULT_ROOT / '.env')

INBOX_DIR = Path(os.getenv('INBOX_DIR', str(VAULT_ROOT / 'Inbox')))
NEEDS_ACTION_DIR = Path(os.getenv('NEEDS_ACTION_DIR', str(VAULT_ROOT / 'Needs_Action')))
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(VAULT_ROOT / 'Logs')))
FILE_CHECK_INTERVAL = int(os.getenv('FILE_CHECK_INTERVAL', 10))

PROCESSED_FILES_PATH = SCRIPT_DIR / 'processed_files.json'

# Ensure required directories exist
INBOX_DIR.mkdir(parents=True, exist_ok=True)
NEEDS_ACTION_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'file_watcher.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File-type helpers
# ---------------------------------------------------------------------------

FILE_TYPE_ACTIONS = {
    'document': [
        '- [ ] Read and review the document',
        '- [ ] Summarize key points',
        '- [ ] Forward to relevant team member',
        '- [ ] Archive after review',
    ],
    'spreadsheet': [
        '- [ ] Review data and figures',
        '- [ ] Verify calculations',
        '- [ ] Forward to finance/analytics team',
        '- [ ] Archive after review',
    ],
    'image': [
        '- [ ] Review the image',
        '- [ ] Determine if action is needed',
        '- [ ] Attach to relevant project or task',
        '- [ ] Archive after review',
    ],
    'code': [
        '- [ ] Review the code/script',
        '- [ ] Test in a safe environment',
        '- [ ] Integrate or delegate to dev team',
        '- [ ] Archive after review',
    ],
    'pdf': [
        '- [ ] Read and review the PDF',
        '- [ ] Extract key information',
        '- [ ] Forward to relevant person',
        '- [ ] Archive after review',
    ],
    'archive': [
        '- [ ] Extract archive contents',
        '- [ ] Review extracted files',
        '- [ ] Process individual items as needed',
        '- [ ] Archive after review',
    ],
    'unknown': [
        '- [ ] Identify file purpose',
        '- [ ] Determine appropriate action',
        '- [ ] Forward to relevant person if needed',
        '- [ ] Archive after review',
    ],
}

EXTENSION_CATEGORIES = {
    '.pdf': 'pdf',
    '.doc': 'document', '.docx': 'document', '.odt': 'document',
    '.txt': 'document', '.md': 'document', '.rtf': 'document',
    '.xls': 'spreadsheet', '.xlsx': 'spreadsheet', '.csv': 'spreadsheet',
    '.png': 'image', '.jpg': 'image', '.jpeg': 'image',
    '.gif': 'image', '.bmp': 'image', '.svg': 'image', '.webp': 'image',
    '.py': 'code', '.js': 'code', '.ts': 'code', '.json': 'code',
    '.html': 'code', '.css': 'code', '.sh': 'code', '.bat': 'code',
    '.zip': 'archive', '.tar': 'archive', '.gz': 'archive',
    '.rar': 'archive', '.7z': 'archive',
}


def classify_file(filepath: Path) -> str:
    """Return a human-readable category for *filepath*."""
    return EXTENSION_CATEGORIES.get(filepath.suffix.lower(), 'unknown')


def format_file_size(size_bytes: int) -> str:
    """Return a human-friendly file-size string."""
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

# ---------------------------------------------------------------------------
# Processed-file tracking  (mirrors gmail_watcher pattern)
# ---------------------------------------------------------------------------


def load_processed_files() -> Set[str]:
    """Load the set of already-processed file names."""
    if PROCESSED_FILES_PATH.exists():
        try:
            with open(PROCESSED_FILES_PATH, 'r') as f:
                data = json.load(f)
                return set(data.get('processed_files', []))
        except Exception as e:
            logger.warning(f"Failed to load processed files list: {e}")
    return set()


def save_processed_files(processed: Set[str]):
    """Persist the processed-file set to disk."""
    try:
        with open(PROCESSED_FILES_PATH, 'w') as f:
            json.dump({'processed_files': sorted(processed)}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save processed files list: {e}")

# ---------------------------------------------------------------------------
# Task-file creation
# ---------------------------------------------------------------------------


def create_task_file(filepath: Path) -> bool:
    """
    Create a Needs_Action .md task file describing *filepath* and move the
    original into Needs_Action alongside it.

    Returns True on success.
    """
    try:
        stat = filepath.stat()
        file_size = format_file_size(stat.st_size)
        mime_type = mimetypes.guess_type(str(filepath))[0] or 'unknown'
        category = classify_file(filepath)
        suggested_actions = '\n'.join(FILE_TYPE_ACTIONS.get(category, FILE_TYPE_ACTIONS['unknown']))
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = filepath.stem[:30].replace(' ', '_')

        # -- markdown task file --
        md_filename = f"{timestamp}_{safe_name}_file_action.md"
        md_path = NEEDS_ACTION_DIR / md_filename

        content = f"""# File Action Required

**Source:** Inbox (File System Watcher)
**Original Filename:** {filepath.name}
**File Type:** {mime_type}
**Category:** {category.title()}
**File Size:** {file_size}
**Detected:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## File Details

| Field | Value |
|-------|-------|
| Name | `{filepath.name}` |
| Extension | `{filepath.suffix}` |
| MIME Type | {mime_type} |
| Size | {file_size} |
| Category | {category.title()} |

## Suggested Actions
{suggested_actions}

## Processing Notes
- **Moved to:** `Needs_Action/{filepath.name}`
- **Task file:** `Needs_Action/{md_filename}`

## Notes
_Add your notes and actions here_

---
*Auto-generated by File System Watcher at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.info(f"[OK] Created task file: {md_filename}")

        # -- move original from Inbox -> Needs_Action --
        dest = NEEDS_ACTION_DIR / filepath.name
        if dest.exists():
            # Avoid overwriting: add timestamp prefix
            dest = NEEDS_ACTION_DIR / f"{timestamp}_{filepath.name}"
        shutil.move(str(filepath), str(dest))
        logger.info(f"[OK] Moved original file: {filepath.name} -> Needs_Action/{dest.name}")

        return True

    except Exception as e:
        logger.error(f"[ERROR] Failed to create task for {filepath.name}: {e}")
        import traceback
        logger.error(f"[ERROR] Traceback: {traceback.format_exc()}")
        return False

# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------


class InboxFileHandler(FileSystemEventHandler):
    """React to new files appearing in the Inbox folder."""

    def __init__(self, processed_files: Set[str]):
        super().__init__()
        self.processed_files = processed_files

    def on_created(self, event):
        if event.is_directory:
            return

        filepath = Path(event.src_path)
        logger.info(f"[DETECTED] New file in Inbox: {filepath.name}")

        # Skip hidden / temp files
        if filepath.name.startswith('.') or filepath.name.startswith('~'):
            logger.info(f"[SKIP] Ignoring temp/hidden file: {filepath.name}")
            return

        # Skip already-processed
        if filepath.name in self.processed_files:
            logger.info(f"[SKIP] Already processed: {filepath.name}")
            return

        # Wait for the file to finish writing (size stabilises)
        if not self._wait_for_stable(filepath):
            logger.warning(f"[WARN] File disappeared before processing: {filepath.name}")
            return

        # Process
        logger.info(f"[PROCESSING] {filepath.name} ({format_file_size(filepath.stat().st_size)})")
        if create_task_file(filepath):
            self.processed_files.add(filepath.name)
            save_processed_files(self.processed_files)
            logger.info(f"[DONE] Successfully processed: {filepath.name}")
        else:
            logger.error(f"[FAIL] Could not process: {filepath.name}")

    @staticmethod
    def _wait_for_stable(filepath: Path, checks: int = 3, interval: float = 1.0) -> bool:
        """
        Wait until the file size stops changing (i.e. the write is complete).
        Returns False if the file disappears.
        """
        prev_size = -1
        stable_count = 0
        for _ in range(checks * 5):  # generous upper bound
            if not filepath.exists():
                return False
            current_size = filepath.stat().st_size
            if current_size == prev_size:
                stable_count += 1
                if stable_count >= checks:
                    return True
            else:
                stable_count = 0
            prev_size = current_size
            time.sleep(interval)
        return filepath.exists()

# ---------------------------------------------------------------------------
# Startup scan -- catch files that arrived while the watcher was not running
# ---------------------------------------------------------------------------


def scan_existing_files(processed_files: Set[str]) -> Set[str]:
    """Process any files already sitting in Inbox/ that haven't been handled."""
    existing = [f for f in INBOX_DIR.iterdir()
                if f.is_file() and not f.name.startswith('.') and not f.name.startswith('~')]

    if not existing:
        logger.info("[SCAN] No existing files in Inbox")
        return processed_files

    logger.info(f"[SCAN] Found {len(existing)} file(s) already in Inbox")
    newly_processed = 0

    for filepath in existing:
        if filepath.name in processed_files:
            logger.info(f"[SCAN][SKIP] Already processed: {filepath.name}")
            continue

        logger.info(f"[SCAN] Processing existing file: {filepath.name}")
        if create_task_file(filepath):
            processed_files.add(filepath.name)
            newly_processed += 1

    if newly_processed:
        save_processed_files(processed_files)
    logger.info(f"[SCAN] Processed {newly_processed} existing file(s)")
    return processed_files

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_watcher():
    """Start the file-system watcher (runs continuously)."""
    logger.info("=" * 60)
    logger.info("File System Watcher Started (Gold Tier)")
    logger.info(f"Monitoring: {INBOX_DIR}")
    logger.info(f"Output:     {NEEDS_ACTION_DIR}")
    logger.info(f"Logs:       {LOGS_DIR}")
    logger.info(f"Stability check interval: {FILE_CHECK_INTERVAL}s")
    logger.info("=" * 60)

    # Validate directories
    if not INBOX_DIR.exists():
        logger.error(f"[ERROR] Inbox directory does not exist: {INBOX_DIR}")
        return
    if not NEEDS_ACTION_DIR.exists():
        logger.error(f"[ERROR] Needs_Action directory does not exist: {NEEDS_ACTION_DIR}")
        return

    # Load tracking state
    processed_files = load_processed_files()
    logger.info(f"[LOADED] {len(processed_files)} previously processed files")
    if processed_files:
        logger.info(f"[TIP] To reset tracking, delete '{PROCESSED_FILES_PATH}' and restart")

    # Catch up on anything that arrived while we were offline
    processed_files = scan_existing_files(processed_files)

    # Start watchdog observer
    handler = InboxFileHandler(processed_files)
    observer = Observer()
    observer.schedule(handler, str(INBOX_DIR), recursive=False)
    observer.start()
    logger.info("[WATCHING] File system observer is running...")
    logger.info("[WATCHING] Drop files into Inbox/ to trigger processing")

    try:
        while True:
            time.sleep(FILE_CHECK_INTERVAL)
    except KeyboardInterrupt:
        logger.info("[STOP] File System Watcher stopped by user")
    finally:
        observer.stop()
        observer.join()
        save_processed_files(handler.processed_files)
        logger.info("[STOP] Observer shut down cleanly")


if __name__ == '__main__':
    run_watcher()
