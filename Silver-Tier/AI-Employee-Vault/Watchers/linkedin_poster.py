"""
LinkedIn Poster — AI Employee Vault (Silver Tier)

Generates LinkedIn post drafts from Business_Goals.md and completed tasks in
Done/, then routes them through the approval workflow.

Workflow:
    1. Read Business_Goals.md for strategic priorities, milestones, and metrics
    2. Scan Done/ for recently completed work (evidence of progress)
    3. Pick the best content angle and fill a post template
    4. Save the draft to Pending_Approval/LINKEDIN_POST_<timestamp>.md
    5. Human reviews → moves to Approved/ or Rejected/
    6. Record the outcome in Posts_History.json

Modes:
    python linkedin_poster.py              # Generate one draft now
    python linkedin_poster.py --schedule   # Run on a loop (checks posting days)

Environment Variables (from .env):
    PENDING_APPROVAL_DIR           Folder for draft posts
    APPROVED_DIR                   Folder the human moves approved posts into
    DONE_DIR                       Completed-task archive
    LOGS_DIR                       Folder for linkedin_poster.log
    LINKEDIN_POST_MAX_PER_WEEK     Max posts per week (default: 3)
    LINKEDIN_POSTING_DAYS          Comma-separated day names (default: Tuesday,Thursday)
    LINKEDIN_OPTIMAL_HOURS         Hour range for posting (default: 9-11)
"""

import os
import re
import json
import time
import logging
import random
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
VAULT_ROOT = SCRIPT_DIR.parent.resolve()
load_dotenv(VAULT_ROOT / '.env')

BUSINESS_GOALS_PATH = VAULT_ROOT / 'Business_Goals.md'
COMPANY_HANDBOOK_PATH = VAULT_ROOT / 'Company_Handbook.md'
DONE_DIR = Path(os.getenv('DONE_DIR', str(VAULT_ROOT / 'Done')))
PENDING_APPROVAL_DIR = Path(os.getenv('PENDING_APPROVAL_DIR', str(VAULT_ROOT / 'Pending_Approval')))
APPROVED_DIR = Path(os.getenv('APPROVED_DIR', str(VAULT_ROOT / 'Approved')))
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(VAULT_ROOT / 'Logs')))

POSTS_HISTORY_PATH = SCRIPT_DIR / 'posts_history.json'

MAX_PER_WEEK = int(os.getenv('LINKEDIN_POST_MAX_PER_WEEK', 3))
POSTING_DAYS = [d.strip() for d in os.getenv('LINKEDIN_POSTING_DAYS', 'Tuesday,Thursday').split(',')]
OPTIMAL_HOURS = os.getenv('LINKEDIN_OPTIMAL_HOURS', '9-11')
SCHEDULE_CHECK_INTERVAL = 3600  # check once per hour when in --schedule mode

# Ensure directories exist
for d in (DONE_DIR, PENDING_APPROVAL_DIR, APPROVED_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'linkedin_poster.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Post templates  (follows Company_Handbook.md: Hook → Value → CTA → Hashtags)
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        'id': 'milestone',
        'name': 'Milestone Celebration',
        'body': (
            "{hook}\n\n"
            "We just hit a major milestone: {milestone}.\n\n"
            "{value}\n\n"
            "The key takeaway? {takeaway}\n\n"
            "{cta}\n\n"
            "{hashtags}"
        ),
    },
    {
        'id': 'lesson_learned',
        'name': 'Lesson Learned',
        'body': (
            "{hook}\n\n"
            "While working on {project}, we discovered something worth sharing:\n\n"
            "{value}\n\n"
            "Here's what we changed as a result: {takeaway}\n\n"
            "{cta}\n\n"
            "{hashtags}"
        ),
    },
    {
        'id': 'thought_leadership',
        'name': 'Thought Leadership',
        'body': (
            "{hook}\n\n"
            "{value}\n\n"
            "Our approach: {takeaway}\n\n"
            "The results speak for themselves — {result}.\n\n"
            "{cta}\n\n"
            "{hashtags}"
        ),
    },
    {
        'id': 'progress_update',
        'name': 'Progress Update',
        'body': (
            "{hook}\n\n"
            "This week we completed {count} tasks and moved closer to our goal:\n"
            "{value}\n\n"
            "What's next: {takeaway}\n\n"
            "{cta}\n\n"
            "{hashtags}"
        ),
    },
    {
        'id': 'behind_the_scenes',
        'name': 'Behind the Scenes',
        'body': (
            "{hook}\n\n"
            "Here's a peek behind the curtain at what we're building:\n\n"
            "{value}\n\n"
            "Why this matters: {takeaway}\n\n"
            "{cta}\n\n"
            "{hashtags}"
        ),
    },
]

HASHTAG_POOL = [
    '#AI', '#Automation', '#Productivity', '#TechInnovation',
    '#BuildInPublic', '#StartupLife', '#FutureOfWork',
    '#MachineLearning', '#DigitalTransformation', '#WorkSmarter',
    '#AITools', '#SoftwareEngineering', '#Innovation',
    '#Entrepreneurship', '#TechStartup',
]

HOOKS = [
    "Most people underestimate what automation can do in a week.",
    "We just shipped something we're genuinely proud of.",
    "What if your inbox could triage itself?",
    "The best tool is the one that works while you sleep.",
    "Small wins compound. Here's proof.",
    "Automation isn't about replacing people — it's about freeing them.",
    "We set out to solve one problem. We found three more along the way.",
    "Here's what happens when you let AI handle the busywork.",
]

CTAS = [
    "What's one task you wish you could automate? Drop it in the comments.",
    "If this resonates, let's connect — I'd love to hear your approach.",
    "What does your automation stack look like? I'm curious.",
    "Follow along as we build this in public.",
    "Agree? Disagree? Let me know below.",
    "What would you automate first in your workflow?",
]

# ---------------------------------------------------------------------------
# Posts history tracking
# ---------------------------------------------------------------------------


def load_history() -> Dict:
    if POSTS_HISTORY_PATH.exists():
        try:
            with open(POSTS_HISTORY_PATH, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f'Failed to load posts history: {e}')
    return {'posts': [], 'rejected': []}


def save_history(history: Dict):
    try:
        with open(POSTS_HISTORY_PATH, 'w') as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f'Failed to save posts history: {e}')


def posts_this_week(history: Dict) -> int:
    """Count posts created in the current ISO week."""
    now = datetime.now()
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    count = 0
    for post in history.get('posts', []):
        try:
            ts = datetime.fromisoformat(post['created_at'])
            if ts >= week_start:
                count += 1
        except (KeyError, ValueError):
            pass
    return count


def recently_used_templates(history: Dict, days: int = 14) -> List[str]:
    """Return template IDs used in the last *days* days."""
    cutoff = datetime.now() - timedelta(days=days)
    used = []
    for post in history.get('posts', []):
        try:
            ts = datetime.fromisoformat(post['created_at'])
            if ts >= cutoff:
                used.append(post.get('template_id', ''))
        except (KeyError, ValueError):
            pass
    return used

# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def read_business_goals() -> Dict:
    """Extract structured content from Business_Goals.md."""
    info: Dict = {
        'priorities': [],
        'projects': [],
        'metrics': [],
        'raw': '',
    }
    if not BUSINESS_GOALS_PATH.exists():
        logger.warning('[GOALS] Business_Goals.md not found')
        return info

    text = BUSINESS_GOALS_PATH.read_text(encoding='utf-8')
    info['raw'] = text

    # Strategic priorities
    for m in re.finditer(r'\d+\.\s+\*\*(.+?)\*\*\s*[—–-]\s*(.+)', text):
        info['priorities'].append({
            'title': m.group(1).strip(),
            'detail': m.group(2).strip(),
        })

    # Active projects (markdown table rows)
    for m in re.finditer(
        r'\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(In Progress|Done|Blocked|Planned)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*\|',
        text,
    ):
        name = m.group(1).strip()
        if name == 'Project' or name.startswith('—'):
            continue
        info['projects'].append({
            'name': name,
            'status': m.group(3).strip(),
            'priority': m.group(4).strip(),
            'milestone': m.group(5).strip(),
        })

    return info


def read_done_folder(max_files: int = 10) -> List[Dict]:
    """Read the most recent Done/ files for completed-work context."""
    files = sorted(DONE_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True)
    items = []
    for f in files[:max_files]:
        try:
            content = f.read_text(encoding='utf-8')
            # Try to extract the subject / title line
            title_match = re.search(r'^#\s+(.+)', content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else f.stem
            items.append({'filename': f.name, 'title': title, 'snippet': content[:300]})
        except Exception as e:
            logger.warning(f'[DONE] Could not read {f.name}: {e}')
    return items

# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------


def pick_template(history: Dict) -> Dict:
    """Choose a template, preferring ones not recently used."""
    recent = set(recently_used_templates(history))
    fresh = [t for t in TEMPLATES if t['id'] not in recent]
    pool = fresh if fresh else TEMPLATES
    return random.choice(pool)


def generate_draft(goals: Dict, done_items: List[Dict], history: Dict) -> Optional[Dict]:
    """
    Build a LinkedIn post draft from the available content.

    Returns a dict with all the metadata needed for the approval file,
    or None if there's nothing meaningful to post about.
    """
    template = pick_template(history)
    logger.info(f"[TEMPLATE] Selected: {template['name']} ({template['id']})")

    # Gather content atoms
    priorities = goals.get('priorities', [])
    projects = goals.get('projects', [])

    # Build the value / milestone / project strings
    if projects:
        proj = projects[0]
        project_str = proj['name']
        milestone_str = proj.get('milestone', 'the next phase')
    else:
        project_str = 'our latest initiative'
        milestone_str = 'a key checkpoint'

    if priorities:
        prio = random.choice(priorities)
        value_str = f"{prio['title']} — {prio['detail']}"
        takeaway_str = prio['detail']
    else:
        value_str = 'Building systems that work smarter, not harder.'
        takeaway_str = 'Focus on automation and quality control.'

    done_count = len(done_items)
    if done_items:
        recent_title = done_items[0]['title']
        result_str = f"we just completed: {recent_title}"
    else:
        result_str = 'steady, measurable progress every week'

    # Pick random hook, CTA, hashtags
    hook = random.choice(HOOKS)
    cta = random.choice(CTAS)
    hashtags = ' '.join(random.sample(HASHTAG_POOL, k=min(3, len(HASHTAG_POOL))))

    # Fill the template
    post_body = template['body'].format(
        hook=hook,
        milestone=milestone_str,
        value=value_str,
        takeaway=takeaway_str,
        project=project_str,
        cta=cta,
        hashtags=hashtags,
        count=done_count,
        result=result_str,
    )

    # Determine content source
    sources = []
    if priorities:
        sources.append(f"Business_Goals.md — priority: {priorities[0]['title']}")
    if done_items:
        sources.append(f"Done/{done_items[0]['filename']}")

    # Optimal posting window
    try:
        start_h, end_h = [int(x) for x in OPTIMAL_HOURS.split('-')]
    except ValueError:
        start_h, end_h = 9, 11
    posting_time = f"{random.choice(POSTING_DAYS)} between {start_h}:00 and {end_h}:00"

    return {
        'template_id': template['id'],
        'template_name': template['name'],
        'post_body': post_body,
        'hashtags': hashtags,
        'cta': cta,
        'sources': sources,
        'recommended_time': posting_time,
        'word_count': len(post_body.split()),
    }

# ---------------------------------------------------------------------------
# Approval file creation
# ---------------------------------------------------------------------------


def create_approval_file(draft: Dict, history: Dict) -> Optional[Path]:
    """
    Write the draft to Pending_Approval/ and record it in history.

    Returns the path to the approval file, or None on failure.
    """
    now = datetime.now()
    ts = now.strftime('%Y-%m-%d_%H-%M')
    filename = f"{ts}_linkedin_post_{draft['template_id']}.md"
    filepath = PENDING_APPROVAL_DIR / filename

    sources_md = '\n'.join(f'- `{s}`' for s in draft['sources']) or '- _Manual trigger_'

    content = f"""# LinkedIn Post — Pending Approval

**Action Type:** linkedin_post
**To/Recipient:** LinkedIn Feed (public)
**Created:** {now.strftime('%Y-%m-%d %H:%M:%S')}
**Template:** {draft['template_name']}
**Word Count:** {draft['word_count']}
**Urgency:** low

---

## Draft Content

{draft['post_body']}

---

## Post Metadata

| Field | Value |
|-------|-------|
| Template | {draft['template_name']} |
| Words | {draft['word_count']} |
| Hashtags | {draft['hashtags']} |
| Recommended Posting Time | {draft['recommended_time']} |

### Content Sources
{sources_md}

---

## Approval Checklist

- [ ] Post is professional and on-brand
- [ ] No confidential or internal information shared
- [ ] Hashtags are relevant (max 3 per handbook)
- [ ] Tone matches Company Handbook guidelines
- [ ] Not posting more than once per day
- [ ] Ready to publish

---

## Instructions

1. **To approve:** Move this file to `Approved/`
2. **To reject:** Move this file to `Rejected/`
3. **To edit:** Modify the Draft Content above, then move to `Approved/`
4. **Post manually** on LinkedIn at the recommended time, or wait for future automation.

---
*Auto-generated by LinkedIn Poster at {now.strftime('%Y-%m-%d %H:%M:%S')}*
"""

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f'[DRAFT] Created: Pending_Approval/{filename}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to write approval file: {e}')
        return None

    # Record in history
    history.setdefault('posts', []).append({
        'created_at': now.isoformat(),
        'filename': filename,
        'template_id': draft['template_id'],
        'status': 'pending_approval',
        'word_count': draft['word_count'],
        'sources': draft['sources'],
    })
    save_history(history)

    return filepath

# ---------------------------------------------------------------------------
# Approval scanner  — detect posts moved to Approved/ or Rejected/
# ---------------------------------------------------------------------------


def scan_approvals(history: Dict) -> Dict:
    """Check if any pending posts have been moved to Approved/ or Rejected/."""
    updated = False
    for post in history.get('posts', []):
        if post.get('status') != 'pending_approval':
            continue

        fname = post['filename']
        pending_path = PENDING_APPROVAL_DIR / fname
        approved_path = APPROVED_DIR / fname
        rejected_path = (VAULT_ROOT / 'Rejected') / fname

        if approved_path.exists():
            post['status'] = 'approved'
            post['approved_at'] = datetime.now().isoformat()
            logger.info(f'[APPROVED] {fname}')
            updated = True
        elif rejected_path.exists():
            post['status'] = 'rejected'
            post['rejected_at'] = datetime.now().isoformat()
            history.setdefault('rejected', []).append(fname)
            logger.info(f'[REJECTED] {fname}')
            updated = True
        elif not pending_path.exists():
            # File disappeared — mark as unknown
            post['status'] = 'unknown'
            logger.warning(f'[UNKNOWN] {fname} missing from all folders')
            updated = True

    if updated:
        save_history(history)

    return history

# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def generate_post():
    """One-shot: generate a single LinkedIn post draft."""
    logger.info('=' * 60)
    logger.info('LinkedIn Poster — Generating Draft')
    logger.info('=' * 60)

    history = load_history()

    # Rate-limit check
    count = posts_this_week(history)
    if count >= MAX_PER_WEEK:
        logger.warning(
            f'[LIMIT] Already created {count}/{MAX_PER_WEEK} posts this week. '
            'Skipping to avoid over-posting.'
        )
        return

    logger.info(f'[STATS] Posts this week: {count}/{MAX_PER_WEEK}')

    # Gather content
    goals = read_business_goals()
    done_items = read_done_folder()
    logger.info(
        f'[CONTENT] {len(goals["priorities"])} priorities, '
        f'{len(goals["projects"])} projects, '
        f'{len(done_items)} recent Done items'
    )

    # Generate draft
    draft = generate_draft(goals, done_items, history)
    if draft is None:
        logger.warning('[SKIP] No meaningful content to post about')
        return

    logger.info(f'[DRAFT] {draft["word_count"]} words, template={draft["template_name"]}')

    # Save to Pending_Approval
    path = create_approval_file(draft, history)
    if path:
        logger.info(f'[DONE] Draft saved — awaiting approval at: {path.name}')
        logger.info(f'[TIP]  Recommended posting time: {draft["recommended_time"]}')
    else:
        logger.error('[FAIL] Could not create approval file')

    # Also scan for any previous approvals/rejections
    scan_approvals(history)


def run_scheduled():
    """Loop mode: check once per hour, generate drafts on posting days."""
    logger.info('=' * 60)
    logger.info('LinkedIn Poster — Scheduled Mode')
    logger.info(f'Posting days: {", ".join(POSTING_DAYS)}')
    logger.info(f'Optimal hours: {OPTIMAL_HOURS}')
    logger.info(f'Max per week: {MAX_PER_WEEK}')
    logger.info(f'Check interval: {SCHEDULE_CHECK_INTERVAL}s')
    logger.info('=' * 60)

    try:
        while True:
            now = datetime.now()
            day_name = now.strftime('%A')
            hour = now.hour

            try:
                start_h, end_h = [int(x) for x in OPTIMAL_HOURS.split('-')]
            except ValueError:
                start_h, end_h = 9, 11

            # Scan for approvals/rejections every cycle
            history = load_history()
            scan_approvals(history)

            # Generate a draft only on posting days during optimal hours
            if day_name in POSTING_DAYS and start_h <= hour <= end_h:
                count = posts_this_week(history)
                if count < MAX_PER_WEEK:
                    logger.info(
                        f'[SCHEDULE] {day_name} {hour}:00 — posting window open, generating draft'
                    )
                    generate_post()
                else:
                    logger.info(f'[SCHEDULE] Weekly limit reached ({count}/{MAX_PER_WEEK})')
            else:
                logger.info(
                    f'[SCHEDULE] {day_name} {hour}:00 — outside posting window, sleeping'
                )

            time.sleep(SCHEDULE_CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info('[STOP] LinkedIn Poster stopped by user')


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='LinkedIn Poster — AI Employee Vault')
    parser.add_argument(
        '--schedule', action='store_true',
        help='Run continuously, generating drafts on posting days',
    )
    parser.add_argument(
        '--scan', action='store_true',
        help='Only scan for approval/rejection updates (no new drafts)',
    )
    args = parser.parse_args()

    if args.scan:
        logger.info('[SCAN] Checking for approval updates...')
        history = load_history()
        scan_approvals(history)
    elif args.schedule:
        run_scheduled()
    else:
        generate_post()


if __name__ == '__main__':
    main()
