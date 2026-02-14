 # Personal AI Employee - Bronze Tier

## Project Overview

**Project Name:** Personal AI Employee - Bronze Tier

This project implements a basic autonomous assistant that monitors a Gmail account for important unread messages, converts them into task files inside an Obsidian-style vault, and provides structured plans for human review. The system demonstrates end-to-end Perception → Reasoning → Action for an AI employee at the Bronze Tier.

**Current Tier & Capabilities:**
- Tier: **Bronze**
- Capabilities:
  - Monitor Gmail for unread important emails (Gmail Watcher)
  - Create actionable markdown task files in `Needs_Action`
  - Claude Code (or other agent) can read and write vault files
  - Agent Skill(s) defined for email processing
  - Basic lifecycle management: `Needs_Action` → `Plans` → `Done`

---

## Completed Requirements Checklist

- ✅ Obsidian vault with `Dashboard.md` and `Company_Handbook.md`
- ✅ One working watcher script: `gmail_watcher.py` (Gmail monitoring)
- ✅ Claude Code successfully reading from and writing to the vault
- ✅ Basic folder structure: `/Inbox`, `/Needs_Action`, `/Done`, `/Plans`, `/Logs`, `/Skills`
- ✅ AI functionality implemented as Agent Skills (see `Skills/email_processor_SKILL.md`)

---

## Verified Working Features

The system has been tested and verified to be fully operational:

1. ✅ **Gmail Watcher** successfully monitoring inbox every 2 minutes
   - Polls Gmail API for unread important emails
   - Properly authenticated with OAuth 2.0
   - Runs continuously with automatic token refresh

2. ✅ **Automatic Task File Creation** in Needs_Action folder
   - Creates `.md` files when important emails arrive
   - Files include sender, subject, timestamp, email ID, and preview
   - Proper file paths resolved (works from any directory)
   - Enhanced logging shows exact file creation locations

3. ✅ **Claude Code Integration** can read and process task files
   - Full read/write access to vault files
   - Can execute Agent Skills
   - Processes tasks and creates plans

4. ✅ **Dashboard.md** updates with real-time task counts
   - Shows pending tasks in Needs_Action
   - Displays processed tasks in Done
   - Active plans in Plans folder

5. ✅ **Email Processor Skill** ready to guide Claude
   - Step-by-step instructions for processing emails
   - Decision trees for Reply/Forward/Archive/Escalate
   - Templates for creating Plan.md files

**Recent Fixes Applied:**
- Fixed file path resolution to use script directory instead of working directory
- Enhanced logging with detailed file creation tracking (✓/✗ indicators)
- Added traceback logging for debugging file creation issues
- All paths now work correctly regardless of where script is run from

---

## Architecture Overview

High-level flow: Perception → Reasoning → Action

- Perception: `gmail_watcher.py` polls the Gmail API for unread important messages and writes a task file to `Needs_Action`.
- Reasoning: Claude Code (or your chosen agent) reads the task file, consults `Company_Handbook.md` and skill definitions in `Skills/`, and decides on the next steps.
- Action: The agent generates a `Plan.md` in `Plans/`, updates or drafts responses (marked "FOR REVIEW"), and moves processed task files to `Done/`.

Components:
- Gmail Watcher: Python script `gmail_watcher.py` handling Gmail API auth and polling
- Obsidian Vault: Folder-based storage with markdown files for tasks, plans, and logs
- Claude Code (Agent): Reads/writes files and executes Agent Skills
- Agent Skills: Markdown skill guides in `Skills/` (e.g., `email_processor_SKILL.md`)

---

## Setup Instructions

Prerequisites:
- Python 3.10+ installed
- `pip` installed
- Google Cloud Console project with Gmail API enabled
- OAuth 2.0 Desktop credentials (JSON)

1. Install dependencies

```bash
pip install -r AI-Employee-Vault/requirements.txt
```

2. Configure Gmail API credentials
- In Google Cloud Console enable **Gmail API** and create **OAuth 2.0 Client (Desktop App)** credentials.
- Download the credentials JSON and save it to `AI-Employee-Vault/credentials.json`.
- Create a `.env` file in the `AI-Employee-Vault` directory (or copy `.env.example`) and set:

```
GMAIL_CREDENTIALS_PATH=E:\Hackathon-Zero\Bronze-Tier\AI-Employee-Vault\credentials.json
GMAIL_CHECK_INTERVAL=120
```

3. First-run authentication
- Run the watcher:

```bash
python AI-Employee-Vault/gmail_watcher.py
```

- A browser window will open to authorize the application. After authorization, a `token.json` file will be saved for future runs.

4. Verify the vault
- Confirm files and folders exist in `AI-Employee-Vault` (see File Structure below).

---

## File Structure

Visual tree (vault root: `AI-Employee-Vault`)

```
AI-Employee-Vault/
├── Dashboard.md
├── Company_Handbook.md
├── gmail_watcher.py
├── requirements.txt
├── .env (or .env.example)
├── credentials.json (your OAuth 2.0 credentials)
├── token.json (created after auth)
├── processed_emails.json
├── Inbox/
├── Needs_Action/
├── Done/
├── Plans/
├── Logs/
└── Skills/
    └── email_processor_SKILL.md
```

Folder purposes:
- `Inbox/`: (optional) raw imported items
- `Needs_Action/`: new tasks created by watchers (one markdown per email task)
- `Plans/`: generated `Plan.md` files with recommended actions
- `Done/`: processed tasks and archived emails
- `Logs/`: runtime logs created by scripts
- `Skills/`: skill definitions that guide agent behavior

---

## Usage Guide

Start the system:

```bash
python AI-Employee-Vault/gmail_watcher.py
```

What happens:
- The watcher checks Gmail at the interval set in `.env` (default 120 seconds).
- When an unread important email is detected, a markdown task is created in `Needs_Action/` containing `From`, `Subject`, a snippet, `Email ID`, and timestamp.
- Claude Code (or a manual operator) reads the task, uses `Skills/email_processor_SKILL.md` and `Company_Handbook.md` to decide next steps, and creates a plan in `Plans/`.
- After a plan is created, the task file is moved to `Done/` with a processing footer.

Monitoring:
- Check `Logs/gmail_watcher.log` for watcher activity and errors.
- The `Dashboard.md` displays a summary and task counts; edit or extend it as needed.

Where to find results:
- Action plans: `Plans/`
- Processed task records: `Done/`
- Raw tasks waiting to be processed: `Needs_Action/`

---

## Testing & Verification

### How to Test the Gmail Watcher

**Method 1: Send a Test Email**
1. Ensure the Gmail Watcher is running:
   ```bash
   python AI-Employee-Vault/gmail_watcher.py
   ```

2. From another email account, send an email to your monitored Gmail account
   - Mark it as important (⭐ star it in Gmail)
   - Keep it unread
   - The watcher queries for `is:unread is:important` emails

3. Wait up to 2 minutes (or your configured CHECK_INTERVAL)

4. Watch the console logs for:
   ```
   INFO - Found 1 unread important email(s)
   INFO - Processing new email - From: sender@example.com, Subject: Test
   INFO - Creating task file for email from sender@example.com
   INFO - Target file path: E:\Hackathon-Zero\Bronze-Tier\AI-Employee-Vault\Needs_Action\...
   INFO - ✓ Successfully created task file: 20260212_123456_sender_action.md
   INFO - ✓ File location: E:\...\Needs_Action\20260212_123456_sender_action.md
   INFO - ✓ File exists: True
   INFO - ✓ Successfully processed and saved email from sender@example.com
   ```

**Method 2: Check Existing Emails**
1. Mark an existing email in Gmail as important and unread
2. The watcher will detect it on the next poll
3. A task file will be created immediately

### Expected Behavior

When a new important unread email is detected:

1. **Task File Created** in `AI-Employee-Vault/Needs_Action/`
   - Filename format: `YYYYMMDD_HHMMSS_sender_action.md`
   - Example: `20260212_173045_john.doe@company_action.md`

2. **File Contents** include:
   - Email sender and subject
   - Received timestamp and Email ID
   - Email body preview (first 500 characters)
   - Suggested action checklist (Reply/Forward/Archive/Spam)
   - Notes section for manual additions

3. **Console Logs** show:
   - Exact file path where file was created
   - Success confirmation with ✓ indicator
   - File existence verification

4. **Processed Emails Tracking**
   - Email ID stored in `processed_emails.json`
   - Prevents duplicate processing on subsequent polls

### Verify Claude Code Can Process Tasks

1. **Open Claude Code** in the AI-Employee-Vault directory

2. **List pending tasks:**
   ```
   Show me all files in the Needs_Action folder
   ```

3. **Process a task using the Email Processor Skill:**
   ```
   Use the email_processor_SKILL to process the task file in Needs_Action
   ```

4. **Expected Claude Code behavior:**
   - Reads the task file from Needs_Action
   - Analyzes sender, subject, and content
   - Consults Company_Handbook.md for policies
   - Creates a Plan.md in Plans folder with recommendations
   - Moves processed task to Done folder
   - Updates Dashboard.md with new counts

### Monitoring & Logs

**Console Output:**
- Real-time status updates every 2 minutes
- Detailed email processing logs with ✓/✗ indicators
- File path verification for debugging

**Log Files:**
- `gmail_watcher.log` - Complete watcher activity log
- `Logs/` folder - Additional runtime logs

**Quick Health Check:**
```bash
# Check if watcher is running
ps aux | grep gmail_watcher.py

# View recent logs
tail -f gmail_watcher.log

# Count pending tasks
dir AI-Employee-Vault\Needs_Action | find /c ".md"

# Count processed tasks
dir AI-Employee-Vault\Done | find /c ".md"
```

### Troubleshooting Test Issues

**No task file created?**
1. Check console logs for errors
2. Verify email is marked as both unread AND important
3. Check the target directory path in logs
4. Ensure `Needs_Action` folder exists and has write permissions

**Files created in wrong location?**
- This has been fixed! The script now uses absolute paths based on script location
- Files will always be created in `AI-Employee-Vault/Needs_Action/` regardless of where you run the script from

**Email not detected?**
- Verify Gmail API credentials are valid
- Check `token.json` exists and is not expired
- Ensure the email matches the query: `is:unread is:important`
- Wait for the next poll cycle (default: 2 minutes)

---

## Skills Documentation

Available skills:
- `email_processor_SKILL.md` — Guides the agent to read tasks in `Needs_Action/`, analyze sender and content, decide whether to Reply/Forward/Archive/Escalate, create a `Plan.md`, and move the original task to `Done/`.

How skills guide behavior:
- Skills are authoritative how-to documents the agent follows for decision-making and output structure. They include rules drawn from `Company_Handbook.md`, decision trees, and templates for `Plan.md` output.

---

## Future Enhancements (Silver & Gold Tier)

Planned improvements:
- Add more watchers (Slack watcher, Calendar watcher, Filewatcher)
- Implement an MCP server for Claude Code to enable persistent agent state and webhooks
- Add automated senders with approval flows for approved drafts
- Add richer NLP for intent classification and entity extraction
- Integrate with company SSO and secure secrets management

---

## Troubleshooting

Common issues & solutions:

- "ModuleNotFoundError: No module named 'dotenv'"
  - Ensure `python-dotenv` is installed: `pip install python-dotenv`

- "Credentials file not found"
  - Confirm `credentials.json` is present at the path in `.env` and `GMAIL_CREDENTIALS_PATH` is set correctly.

- Watcher authentication fails
  - Delete `token.json` and re-run the watcher to re-authorize.

- No tasks created
  - Check Gmail label/filters; the watcher queries `is:unread is:important` by default. Ensure messages match the query.

How to check components:
- Gmail Watcher running: watch output in console or `Logs/gmail_watcher.log`
- Agent file access: verify the agent can read and write files inside `AI-Employee-Vault`

---

## Credits & Resources

- Hackathon: Personal AI Employee Hackathon (Bronze Tier deliverables)
- Learning resources:
  - Gmail API docs: https://developers.google.com/gmail/api
  - google-api-python-client documentation
  - python-dotenv docs
  - Obsidian.md for vault workflows
- Technologies used:
  - Python
  - Gmail API
  - google-auth, google-auth-oauthlib, google-api-python-client
  - Obsidian-style markdown vaults
  - Claude Code (agent) for reasoning and actions

---

## Status

**System Status:** ✅ Fully Operational

The Gmail Watcher has been tested and verified to be working correctly. All components of the Bronze Tier implementation are functional and ready for use.

**Latest Updates:**
- February 12, 2026 - Fixed file path resolution issue, added enhanced logging
- February 12, 2026 - Verified end-to-end workflow from Gmail → Task Files → Claude Processing
- February 12, 2026 - Added comprehensive testing and verification documentation

---

If you want, I can also:
- Add a quick-start shell script to run the watcher in the background
- Add a sample `Plan.md` generator that demonstrates the exact output format

Last updated: February 12, 2026
