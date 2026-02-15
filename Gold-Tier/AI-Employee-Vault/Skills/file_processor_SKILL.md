# File Processor Skill

## Skill Summary

**Purpose:** Monitor the Inbox folder for newly dropped files, categorise them by type, generate a structured task file in Needs_Action/ with metadata and suggested actions, and move the original to a safe location for processing.

**Trigger:** New file appears in `Inbox/` (detected by watchdog or startup scan)
**Authority Level:** Low — Internal triage only; never modifies external state
**Success Metric:** Every file that enters Inbox/ gets a task file in Needs_Action/ with correct metadata, and the original is safely moved

---

## Overview

This skill enables the AI Employee to:
1. Detect new files in the Inbox folder in real time (watchdog) or on startup (scan)
2. Wait for file writes to complete (stability check)
3. Classify the file by extension and MIME type
4. Generate category-specific suggested actions
5. Create a structured `.md` task file in `Needs_Action/`
6. Move the original file from `Inbox/` to `Needs_Action/` alongside the task file
7. Track processed files to prevent duplicates

---

## Prerequisites

- `watchdog` library installed (`pip install watchdog`)
- Access to `Inbox/`, `Needs_Action/`, and `Logs/` folders
- Environment variables configured in `.env` (INBOX_DIR, NEEDS_ACTION_DIR, LOGS_DIR)
- `processed_files.json` for deduplication tracking

---

## Step-by-Step Processing Instructions

### Step 1: Detect New Files

**Two detection methods operate together:**

#### A. Real-time Detection (watchdog)
- A `FileSystemEventHandler` monitors `Inbox/` for `FileCreatedEvent`
- Triggers immediately when the OS reports a new file
- Only reacts to file events, not directory events

#### B. Startup Scan
- On launch, iterate all files already in `Inbox/`
- Process any that aren't in `processed_files.json`
- Catches files that arrived while the watcher was offline

**Skip these files:**
| Pattern | Reason |
|---------|--------|
| Starts with `.` | Hidden / system file |
| Starts with `~` | Temporary file (Office lock files, etc.) |
| Already in `processed_files.json` | Already handled |

---

### Step 2: Wait for Write Completion

Before processing, confirm the file is fully written:

1. Read the file size
2. Wait 1 second
3. Read the file size again
4. If unchanged, increment a stability counter
5. Repeat until the counter reaches 3 consecutive stable readings
6. If the file disappears during this check, abort (return False)

**Why:** Files copied over a network, downloaded by a browser, or written by another process may not be complete when the event fires. Processing a partial file corrupts the metadata.

---

### Step 3: Classify the File

**Classification uses the file extension:**

| Extensions | Category | Description |
|-----------|----------|-------------|
| `.pdf` | PDF | Portable Document Format |
| `.doc`, `.docx`, `.odt`, `.txt`, `.md`, `.rtf` | Document | Text documents |
| `.xls`, `.xlsx`, `.csv` | Spreadsheet | Data files |
| `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.svg`, `.webp` | Image | Visual media |
| `.py`, `.js`, `.ts`, `.json`, `.html`, `.css`, `.sh`, `.bat` | Code | Source code / scripts |
| `.zip`, `.tar`, `.gz`, `.rar`, `.7z` | Archive | Compressed files |
| Everything else | Unknown | Requires manual classification |

**Also determine:**
- MIME type via Python's `mimetypes.guess_type()`
- File size (formatted as B / KB / MB / GB)

---

### Step 4: Generate Suggested Actions

Each category has a predefined set of actions:

#### Document
```
- [ ] Read and review the document
- [ ] Summarize key points
- [ ] Forward to relevant team member
- [ ] Archive after review
```

#### Spreadsheet
```
- [ ] Review data and figures
- [ ] Verify calculations
- [ ] Forward to finance/analytics team
- [ ] Archive after review
```

#### Image
```
- [ ] Review the image
- [ ] Determine if action is needed
- [ ] Attach to relevant project or task
- [ ] Archive after review
```

#### Code
```
- [ ] Review the code/script
- [ ] Test in a safe environment
- [ ] Integrate or delegate to dev team
- [ ] Archive after review
```

#### PDF
```
- [ ] Read and review the PDF
- [ ] Extract key information
- [ ] Forward to relevant person
- [ ] Archive after review
```

#### Archive
```
- [ ] Extract archive contents
- [ ] Review extracted files
- [ ] Process individual items as needed
- [ ] Archive after review
```

#### Unknown
```
- [ ] Identify file purpose
- [ ] Determine appropriate action
- [ ] Forward to relevant person if needed
- [ ] Archive after review
```

---

### Step 5: Create the Task File

**Filename format:** `{YYYYMMDD_HHMMSS}_{filestem}_file_action.md`

**The task file must contain:**

```markdown
# File Action Required

**Source:** Inbox (File System Watcher)
**Original Filename:** {name}
**File Type:** {MIME type}
**Category:** {category}
**File Size:** {formatted size}
**Detected:** {YYYY-MM-DD HH:MM:SS}

## File Details

| Field | Value |
|-------|-------|
| Name | `{name}` |
| Extension | `{extension}` |
| MIME Type | {MIME type} |
| Size | {formatted size} |
| Category | {category} |

## Suggested Actions
{category-specific actions from Step 4}

## Processing Notes
- **Moved to:** `Needs_Action/{original_name}`
- **Task file:** `Needs_Action/{task_filename}`

## Notes
_Add your notes and actions here_

---
*Auto-generated by File System Watcher at {timestamp}*
```

---

### Step 6: Move the Original File

1. Destination: `Needs_Action/{original_filename}`
2. If a file with the same name already exists, prefix with timestamp: `{YYYYMMDD_HHMMSS}_{original_filename}`
3. Use `shutil.move()` (not copy — we want the file out of Inbox)
4. Log the move with source and destination paths

---

### Step 7: Update Tracking State

1. Add the filename to the processed set
2. Save to `Watchers/processed_files.json`:
   ```json
   {
     "processed_files": ["report.pdf", "data.csv", ...]
   }
   ```
3. This prevents reprocessing if the watcher restarts

---

## Security Considerations

### File Types That Warrant Extra Caution

| Category | Risk | Guidance |
|----------|------|----------|
| Code (`.py`, `.js`, `.sh`, `.bat`) | **High** — could be executable | Never auto-execute. Always route for human review. Log prominently. |
| Archive (`.zip`, `.rar`, `.7z`) | **Medium** — could contain anything | Note in the task file that contents are unknown. Do not auto-extract. |
| Unknown extensions | **Medium** — unrecognised file type | Flag for human identification. Do not attempt to open or parse. |
| Document / PDF | **Low** — generally safe to review | Standard processing. |
| Image | **Low** — generally safe | Standard processing. |
| Spreadsheet | **Low** — may contain macros in `.xls` | Note if `.xls` (older format may have macros). |

### Rules

1. **Never execute files** — even if they're scripts. The watcher only creates metadata.
2. **Never open or parse file contents** — the watcher reads only file-system metadata (name, size, mtime).
3. **Never send files externally** — file triage is internal. Any forwarding requires human approval via the orchestrator.
4. **Log everything** — every detection, classification, and move is recorded in `Logs/file_watcher.log`.

---

## When to Require Human Review

Per Company_Handbook.md, file processing is internal triage and **does not require approval**.

However, the **orchestrator** will assess the resulting task file and may route it to Pending_Approval/ if:
- The task file suggests forwarding to someone (outbound action)
- The file type is high-risk (code, archive)
- The file relates to a financial or contractual matter

The file_watcher itself **never makes approval decisions** — it only creates the task. The orchestrator decides what happens next.

---

## Examples

### Good: PDF Dropped in Inbox
```
File: Inbox/Q1_Report.pdf
Category: PDF
Size: 2.4 MB
Task: Needs_Action/20260214_150000_Q1_Report_file_action.md
Move: Needs_Action/Q1_Report.pdf
Log:  [DETECTED] New file in Inbox: Q1_Report.pdf
      [PROCESSING] Q1_Report.pdf (2.4 MB)
      [OK] Created task file: 20260214_150000_Q1_Report_file_action.md
      [OK] Moved original file: Q1_Report.pdf -> Needs_Action/Q1_Report.pdf
      [DONE] Successfully processed: Q1_Report.pdf
```

### Good: Python Script Detected
```
File: Inbox/deploy.py
Category: Code
Size: 4.2 KB
Task: Needs_Action/20260214_160000_deploy_file_action.md
Suggested actions include: "Test in a safe environment"
Note: The orchestrator will likely route this for human review (code = caution)
```

### Good: Temp File Skipped
```
File: Inbox/.DS_Store
Action: SKIP (hidden file — starts with ".")
File: Inbox/~$document.docx
Action: SKIP (temp file — starts with "~")
```

### Good: Duplicate File Skipped
```
File: Inbox/report.pdf (already in processed_files.json)
Action: SKIP (already processed)
```

### Bad: Executing a Script
```
NEVER run `python deploy.py` or `bash script.sh`.
The watcher only records metadata — it never opens or executes files.
```

### Bad: Extracting Archives
```
NEVER run `unzip archive.zip`.
Create a task file noting "Extract archive contents" as a suggested action.
Let the human or orchestrator decide whether to extract.
```

---

## Common Pitfalls

| Pitfall | Correct Behaviour |
|---------|-------------------|
| Processing a partially-written file | Always wait for 3 stable size readings |
| Leaving the original in Inbox/ | Always move to Needs_Action/ after creating task |
| Re-processing after restart | Check processed_files.json before acting |
| Attempting to read file contents | Only read file-system metadata (name, size, mtime) |
| Classifying by MIME type alone | Use extension as primary classifier, MIME as secondary |

---

## Integration Points

| Component | Interaction |
|-----------|-------------|
| `Inbox/` folder | Input — files are dropped here by users or other systems |
| `Needs_Action/` folder | Output — task files and original files land here |
| `orchestrator.py` | Picks up file-action tasks and generates plans |
| `processed_files.json` | Deduplication state (in Watchers/) |
| `Logs/file_watcher.log` | Activity and error logging |
| `.env` | Configuration: INBOX_DIR, NEEDS_ACTION_DIR, LOGS_DIR, FILE_CHECK_INTERVAL |

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-14 | 1.0 | Initial skill creation for Silver Tier |

---

**Skill Author:** AI Employee Vault System
**Last Updated:** February 14, 2026
**Status:** Active
