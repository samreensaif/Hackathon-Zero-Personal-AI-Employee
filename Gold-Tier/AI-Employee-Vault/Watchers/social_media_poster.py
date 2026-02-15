"""
Social Media Poster — AI Employee Vault (Gold Tier)

Generates platform-specific post drafts for Facebook, Instagram, and Twitter/X
from Business_Goals.md and completed tasks in Done/, then routes them through
the approval workflow.

Platforms:
    Facebook   — 500 char limit, links allowed
    Instagram  — 2200 char limit, hashtag-heavy, no clickable links
    Twitter/X  — 280 char limit, concise, hashtags optional

Workflow:
    1. Read Business_Goals.md, Company_Handbook.md, and Done/ for content
    2. Pick a post type and fill a platform-specific template
    3. Save drafts to Social_Media/ as PLATFORM_POST_<timestamp>.md
    4. Copy drafts to Pending_Approval/ for human review
    5. Human reviews -> moves to Approved/ or Rejected/
    6. Record the outcome in posts_history.json (per-platform counters)

Modes:
    python social_media_poster.py --platform facebook     # Single platform draft
    python social_media_poster.py --platform twitter      # Single platform draft
    python social_media_poster.py --schedule              # Loop: all platforms

Environment Variables (from .env):
    SOCIAL_MEDIA_DIR           Folder for platform-specific drafts
    PENDING_APPROVAL_DIR       Folder for approval workflow
    APPROVED_DIR               Folder the human moves approved posts into
    DONE_DIR                   Completed-task archive
    LOGS_DIR                   Folder for social_media_poster.log
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
SOCIAL_MEDIA_DIR = Path(os.getenv('SOCIAL_MEDIA_DIR', str(VAULT_ROOT / 'Social_Media')))
LOGS_DIR = Path(os.getenv('LOGS_DIR', str(VAULT_ROOT / 'Logs')))

POSTS_HISTORY_PATH = SCRIPT_DIR / 'social_posts_history.json'

MAX_POSTS_PER_DAY = 1           # per platform per day
RECOMMENDED_TIMES = ['10:00 AM', '2:00 PM', '6:00 PM']
SCHEDULE_CHECK_INTERVAL = 3600  # check once per hour in --schedule mode

# Ensure directories exist
for d in (DONE_DIR, PENDING_APPROVAL_DIR, APPROVED_DIR, SOCIAL_MEDIA_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / 'social_media_poster.log')),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Platform definitions
# ---------------------------------------------------------------------------

PLATFORMS = {
    'facebook': {
        'name': 'Facebook',
        'char_limit': 500,
        'max_hashtags': 5,
        'links_allowed': True,
        'prefix': 'FACEBOOK_POST',
        'style': 'conversational and community-oriented',
    },
    'instagram': {
        'name': 'Instagram',
        'char_limit': 2200,
        'max_hashtags': 15,
        'links_allowed': False,
        'prefix': 'INSTAGRAM_POST',
        'style': 'visual-first with hashtag-heavy captions',
    },
    'twitter': {
        'name': 'Twitter/X',
        'char_limit': 280,
        'max_hashtags': 3,
        'links_allowed': True,
        'prefix': 'TWITTER_POST',
        'style': 'punchy and concise',
    },
}

# ---------------------------------------------------------------------------
# Post templates — by type, each with platform-specific variants
# ---------------------------------------------------------------------------

POST_TYPES = {
    'product_announcement': {
        'name': 'Product Announcement',
        'templates': {
            'facebook': (
                "{hook}\n\n"
                "We're thrilled to announce: {announcement}\n\n"
                "{value}\n\n"
                "What do you think? Let us know in the comments!\n\n"
                "{hashtags}"
            ),
            'instagram': (
                "{hook}\n\n"
                "{announcement}\n\n"
                "{value}\n\n"
                "Double-tap if you're as excited as we are!\n\n"
                "{image_note}\n\n"
                "{hashtags}"
            ),
            'twitter': (
                "{hook} {announcement_short}\n\n{hashtags}"
            ),
        },
    },
    'behind_the_scenes': {
        'name': 'Behind the Scenes',
        'templates': {
            'facebook': (
                "{hook}\n\n"
                "Here's a peek behind the curtain:\n\n"
                "{value}\n\n"
                "Building in public means sharing the real story — "
                "the wins AND the challenges.\n\n"
                "{cta}\n\n"
                "{hashtags}"
            ),
            'instagram': (
                "{hook}\n\n"
                "A look behind the scenes at what we're building:\n\n"
                "{value}\n\n"
                "Every great product starts with messy first drafts.\n\n"
                "{image_note}\n\n"
                "{cta}\n\n"
                "{hashtags}"
            ),
            'twitter': (
                "{hook} {value_short}\n\n{hashtags}"
            ),
        },
    },
    'customer_testimonial': {
        'name': 'Customer Testimonial',
        'templates': {
            'facebook': (
                "Nothing beats hearing from our users:\n\n"
                "\"{testimonial}\"\n\n"
                "{value}\n\n"
                "Thank you for the kind words! {cta}\n\n"
                "{hashtags}"
            ),
            'instagram': (
                "Our users said it best:\n\n"
                "\"{testimonial}\"\n\n"
                "{value}\n\n"
                "We build for moments like these.\n\n"
                "{image_note}\n\n"
                "{hashtags}"
            ),
            'twitter': (
                "\"{testimonial_short}\" — Thank you! {hashtags}"
            ),
        },
    },
    'quick_tip': {
        'name': 'Quick Tip / Advice',
        'templates': {
            'facebook': (
                "Quick tip: {tip}\n\n"
                "{value}\n\n"
                "Try this today and let us know how it goes!\n\n"
                "{hashtags}"
            ),
            'instagram': (
                "PRO TIP:\n\n"
                "{tip}\n\n"
                "{value}\n\n"
                "Save this for later! Bookmark it now.\n\n"
                "{image_note}\n\n"
                "{hashtags}"
            ),
            'twitter': (
                "Tip: {tip_short}\n\n{hashtags}"
            ),
        },
    },
    'milestone_celebration': {
        'name': 'Milestone Celebration',
        'templates': {
            'facebook': (
                "{hook}\n\n"
                "We just hit a milestone: {milestone}\n\n"
                "{value}\n\n"
                "Thank you to everyone who made this possible. "
                "Here's to the next one!\n\n"
                "{cta}\n\n"
                "{hashtags}"
            ),
            'instagram': (
                "{hook}\n\n"
                "MILESTONE REACHED: {milestone}\n\n"
                "{value}\n\n"
                "This wouldn't be possible without this amazing community.\n\n"
                "{image_note}\n\n"
                "{hashtags}"
            ),
            'twitter': (
                "{hook} We just hit: {milestone_short}\n\n{hashtags}"
            ),
        },
    },
}

# ---------------------------------------------------------------------------
# Content pools
# ---------------------------------------------------------------------------

HASHTAG_POOLS = {
    'facebook': [
        '#AI', '#Automation', '#SmallBusiness', '#Productivity',
        '#TechInnovation', '#FutureOfWork', '#DigitalTransformation',
        '#BusinessGrowth', '#WorkSmarter', '#Entrepreneurship',
    ],
    'instagram': [
        '#AI', '#Automation', '#TechStartup', '#Productivity',
        '#BuildInPublic', '#StartupLife', '#FutureOfWork',
        '#MachineLearning', '#Innovation', '#WorkSmarter',
        '#AITools', '#SoftwareEngineering', '#TechCommunity',
        '#DigitalTransformation', '#Entrepreneur',
    ],
    'twitter': [
        '#AI', '#Automation', '#BuildInPublic',
        '#TechStartup', '#FutureOfWork', '#Productivity',
    ],
}

HOOKS = [
    "Most people underestimate what automation can do in a week.",
    "We just shipped something we're genuinely proud of.",
    "What if your inbox could triage itself?",
    "The best tool is the one that works while you sleep.",
    "Small wins compound. Here's proof.",
    "Automation isn't about replacing people — it's about freeing them.",
    "Here's what happens when you let AI handle the busywork.",
    "Building the future, one commit at a time.",
]

HOOKS_SHORT = [
    "Small wins compound.",
    "AI + automation =",
    "We shipped something new.",
    "Your inbox, automated.",
    "Build smarter, not harder.",
    "One less manual task.",
    "The future is automated.",
]

CTAS = [
    "What's one task you wish you could automate? Tell us below!",
    "If this resonates, let us know — we'd love to hear your approach.",
    "What does your automation stack look like?",
    "Follow along as we build this in public.",
    "Agree? Disagree? Drop a comment!",
    "What would you automate first in your workflow?",
    "Tag someone who needs to see this.",
]

TIPS = [
    "Automate your repetitive tasks first — it frees up brainpower for creative work.",
    "Start small with automation. One workflow at a time builds momentum.",
    "Document your processes before automating. You can't automate what you don't understand.",
    "Review automated outputs regularly. AI is powerful but needs human oversight.",
    "Set up email filters + AI triage to cut inbox noise by 80%.",
]

TESTIMONIALS = [
    "This tool saved me 3 hours every week on email processing.",
    "Finally, an AI assistant that actually understands context.",
    "The approval workflow keeps us in control without slowing things down.",
    "We automated our invoicing and haven't looked back.",
]

IMAGE_SUGGESTIONS = [
    "Suggested image: Screenshot of the dashboard showing progress metrics",
    "Suggested image: Behind-the-scenes photo of the workspace/setup",
    "Suggested image: Clean graphic with the key stat or quote highlighted",
    "Suggested image: Before/after comparison of the manual vs. automated workflow",
    "Suggested image: Team celebration or milestone graphic",
    "Suggested image: Branded quote card with the tip text",
]

# ---------------------------------------------------------------------------
# Posts history tracking (per-platform counters)
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


def posts_today(history: Dict, platform: str) -> int:
    """Count posts created today for a specific platform."""
    today = datetime.now().strftime('%Y-%m-%d')
    count = 0
    for post in history.get('posts', []):
        if post.get('platform') != platform:
            continue
        try:
            ts = datetime.fromisoformat(post['created_at'])
            if ts.strftime('%Y-%m-%d') == today:
                count += 1
        except (KeyError, ValueError):
            pass
    return count


def recently_used_types(history: Dict, platform: str, days: int = 14) -> List[str]:
    """Return post type IDs used in the last *days* days for a platform."""
    cutoff = datetime.now() - timedelta(days=days)
    used = []
    for post in history.get('posts', []):
        if post.get('platform') != platform:
            continue
        try:
            ts = datetime.fromisoformat(post['created_at'])
            if ts >= cutoff:
                used.append(post.get('post_type', ''))
        except (KeyError, ValueError):
            pass
    return used


# ---------------------------------------------------------------------------
# Content extraction (same sources as LinkedIn poster)
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


def read_company_handbook() -> str:
    """Read Company_Handbook.md for brand voice context."""
    if not COMPANY_HANDBOOK_PATH.exists():
        logger.warning('[HANDBOOK] Company_Handbook.md not found')
        return ''
    try:
        return COMPANY_HANDBOOK_PATH.read_text(encoding='utf-8')
    except Exception as e:
        logger.warning(f'[HANDBOOK] Could not read: {e}')
        return ''


def read_done_folder(max_files: int = 10) -> List[Dict]:
    """Read the most recent Done/ files for completed-work context."""
    files = sorted(DONE_DIR.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True)
    items = []
    for f in files[:max_files]:
        try:
            content = f.read_text(encoding='utf-8')
            title_match = re.search(r'^#\s+(.+)', content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else f.stem
            items.append({'filename': f.name, 'title': title, 'snippet': content[:300]})
        except Exception as e:
            logger.warning(f'[DONE] Could not read {f.name}: {e}')
    return items


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------


def pick_post_type(history: Dict, platform: str) -> str:
    """Choose a post type, preferring ones not recently used on this platform."""
    recent = set(recently_used_types(history, platform))
    all_types = list(POST_TYPES.keys())
    fresh = [t for t in all_types if t not in recent]
    pool = fresh if fresh else all_types
    return random.choice(pool)


def _truncate(text: str, limit: int) -> str:
    """Truncate text to fit within character limit."""
    if len(text) <= limit:
        return text
    return text[:limit - 3].rsplit(' ', 1)[0] + '...'


def generate_draft(
    platform: str,
    goals: Dict,
    done_items: List[Dict],
    history: Dict,
) -> Optional[Dict]:
    """
    Build a platform-specific post draft from the available content.

    Returns a dict with all the metadata needed for the draft file,
    or None if there's nothing meaningful to post about.
    """
    pconfig = PLATFORMS[platform]
    post_type_id = pick_post_type(history, platform)
    post_type = POST_TYPES[post_type_id]
    template = post_type['templates'][platform]

    logger.info(f"[{platform.upper()}] Selected type: {post_type['name']} ({post_type_id})")

    # Gather content atoms
    priorities = goals.get('priorities', [])
    projects = goals.get('projects', [])

    # Build content strings
    if projects:
        proj = projects[0]
        milestone_str = proj.get('milestone', 'a key checkpoint')
        milestone_short = _truncate(milestone_str, 60)
    else:
        milestone_str = 'a key checkpoint in our journey'
        milestone_short = 'a new milestone'

    if priorities:
        prio = random.choice(priorities)
        value_str = f"{prio['title']} — {prio['detail']}"
        value_short = _truncate(prio['title'], 80)
    else:
        value_str = 'Building systems that work smarter, not harder.'
        value_short = 'Smarter systems, better results.'

    if done_items:
        announcement_str = f"We just completed: {done_items[0]['title']}"
        announcement_short = _truncate(done_items[0]['title'], 80)
    else:
        announcement_str = 'Something new is coming — and we can\'t wait to share.'
        announcement_short = 'Something new is coming.'

    # Pick random elements
    hook = random.choice(HOOKS)
    hook_short = random.choice(HOOKS_SHORT)
    cta = random.choice(CTAS)
    tip = random.choice(TIPS)
    tip_short = _truncate(tip, 200)
    testimonial = random.choice(TESTIMONIALS)
    testimonial_short = _truncate(testimonial, 150)
    image_note = random.choice(IMAGE_SUGGESTIONS)

    # Hashtags — platform-appropriate count
    pool = HASHTAG_POOLS[platform]
    num_tags = pconfig['max_hashtags']
    hashtags = ' '.join(random.sample(pool, k=min(num_tags, len(pool))))

    # Fill the template
    post_body = template.format(
        hook=hook if platform != 'twitter' else hook_short,
        announcement=announcement_str,
        announcement_short=announcement_short,
        value=value_str,
        value_short=value_short,
        milestone=milestone_str,
        milestone_short=milestone_short,
        tip=tip,
        tip_short=tip_short,
        testimonial=testimonial,
        testimonial_short=testimonial_short,
        cta=cta,
        hashtags=hashtags,
        image_note=image_note,
    )

    # Enforce character limit
    char_limit = pconfig['char_limit']
    if len(post_body) > char_limit:
        # Trim the body but keep hashtags
        hashtag_len = len(hashtags)
        available = char_limit - hashtag_len - 4  # 4 for \n\n separators
        body_without_tags = post_body.replace(hashtags, '').rstrip()
        body_trimmed = _truncate(body_without_tags, available)
        post_body = f"{body_trimmed}\n\n{hashtags}"

    # Determine content sources
    sources = []
    if priorities:
        sources.append(f"Business_Goals.md — priority: {priorities[0]['title']}")
    if done_items:
        sources.append(f"Done/{done_items[0]['filename']}")
    sources.append('Company_Handbook.md — brand voice')

    # Recommended posting time
    posting_time = random.choice(RECOMMENDED_TIMES)

    return {
        'platform': platform,
        'platform_name': pconfig['name'],
        'post_type': post_type_id,
        'post_type_name': post_type['name'],
        'post_body': post_body,
        'char_count': len(post_body),
        'char_limit': char_limit,
        'hashtags': hashtags,
        'cta': cta,
        'sources': sources,
        'recommended_time': posting_time,
        'image_suggestion': image_note,
        'links_allowed': pconfig['links_allowed'],
    }


# ---------------------------------------------------------------------------
# Draft file creation
# ---------------------------------------------------------------------------


def create_draft_files(draft: Dict, history: Dict) -> Optional[Path]:
    """
    Write the draft to Social_Media/ and Pending_Approval/.
    Records it in history.

    Returns the path to the approval file, or None on failure.
    """
    now = datetime.now()
    ts = now.strftime('%Y-%m-%d_%H-%M')
    platform = draft['platform']
    prefix = PLATFORMS[platform]['prefix']

    # Social_Media/ archive copy
    archive_filename = f"{prefix}_{ts}.md"
    archive_path = SOCIAL_MEDIA_DIR / archive_filename

    # Pending_Approval/ copy for review
    approval_filename = f"{ts}_{prefix}_{draft['post_type']}.md"
    approval_path = PENDING_APPROVAL_DIR / approval_filename

    sources_md = '\n'.join(f'- `{s}`' for s in draft['sources']) or '- _Manual trigger_'

    links_note = (
        "Links are supported on this platform."
        if draft['links_allowed']
        else "NOTE: Links are NOT clickable on this platform. Use 'link in bio' instead."
    )

    content = f"""# {draft['platform_name']} Post — Pending Approval

**Action Type:** social_media_post
**Platform:** {draft['platform_name']}
**Post Type:** {draft['post_type_name']}
**Created:** {now.strftime('%Y-%m-%d %H:%M:%S')}
**Characters:** {draft['char_count']} / {draft['char_limit']}
**Urgency:** low

---

## Draft Content

{draft['post_body']}

---

## Post Metadata

| Field | Value |
|-------|-------|
| Platform | {draft['platform_name']} |
| Post Type | {draft['post_type_name']} |
| Characters | {draft['char_count']} / {draft['char_limit']} |
| Hashtags | {draft['hashtags']} |
| Recommended Time | {draft['recommended_time']} |
| Links | {links_note} |

### Image Suggestion

{draft['image_suggestion']}

### Content Sources
{sources_md}

---

## Approval Checklist

- [ ] Post is professional and on-brand
- [ ] No confidential or internal information shared
- [ ] Content fits within {draft['char_limit']}-character limit
- [ ] Hashtags are relevant (max {PLATFORMS[platform]['max_hashtags']} for {draft['platform_name']})
- [ ] Tone matches Company Handbook guidelines
- [ ] Not posting more than once per day on this platform
- [ ] Image suggestion reviewed (attach actual image before posting)
- [ ] Ready to publish

---

## Instructions

1. **To approve:** Move this file to `Approved/`
2. **To reject:** Move this file to `Rejected/`
3. **To edit:** Modify the Draft Content above, then move to `Approved/`
4. **Post manually** on {draft['platform_name']} at the recommended time,
   or wait for future automation.

---
*Auto-generated by Social Media Poster at {now.strftime('%Y-%m-%d %H:%M:%S')}*
"""

    try:
        with open(archive_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f'[ARCHIVE] Social_Media/{archive_filename}')

        with open(approval_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f'[DRAFT] Pending_Approval/{approval_filename}')
    except Exception as e:
        logger.error(f'[ERROR] Failed to write draft files: {e}')
        return None

    # Record in history
    history.setdefault('posts', []).append({
        'created_at': now.isoformat(),
        'platform': platform,
        'filename': approval_filename,
        'archive_filename': archive_filename,
        'post_type': draft['post_type'],
        'char_count': draft['char_count'],
        'status': 'pending_approval',
        'sources': draft['sources'],
    })
    save_history(history)

    return approval_path


# ---------------------------------------------------------------------------
# Approval scanner
# ---------------------------------------------------------------------------


def scan_approvals(history: Dict) -> Dict:
    """Check if any pending posts have been moved to Approved/ or Rejected/."""
    rejected_dir = VAULT_ROOT / 'Rejected'
    updated = False

    for post in history.get('posts', []):
        if post.get('status') != 'pending_approval':
            continue

        fname = post['filename']
        pending_path = PENDING_APPROVAL_DIR / fname
        approved_path = APPROVED_DIR / fname
        rejected_path = rejected_dir / fname

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
            post['status'] = 'unknown'
            logger.warning(f'[UNKNOWN] {fname} missing from all folders')
            updated = True

    if updated:
        save_history(history)

    return history


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def generate_for_platform(platform: str):
    """Generate a single post draft for a specific platform."""
    if platform not in PLATFORMS:
        logger.error(f'[ERROR] Unknown platform: {platform}. '
                     f'Choose from: {", ".join(PLATFORMS.keys())}')
        return

    pname = PLATFORMS[platform]['name']
    logger.info('=' * 60)
    logger.info(f'Social Media Poster — Generating {pname} Draft')
    logger.info('=' * 60)

    history = load_history()

    # Rate-limit check: max 1 per platform per day
    today_count = posts_today(history, platform)
    if today_count >= MAX_POSTS_PER_DAY:
        logger.warning(
            f'[LIMIT] Already created {today_count} {pname} post(s) today. '
            'Skipping to avoid over-posting.'
        )
        return

    # Gather content
    goals = read_business_goals()
    done_items = read_done_folder()
    read_company_handbook()  # for brand voice awareness
    logger.info(
        f'[CONTENT] {len(goals["priorities"])} priorities, '
        f'{len(goals["projects"])} projects, '
        f'{len(done_items)} recent Done items'
    )

    # Generate draft
    draft = generate_draft(platform, goals, done_items, history)
    if draft is None:
        logger.warning(f'[SKIP] No meaningful content for {pname}')
        return

    logger.info(
        f'[DRAFT] {draft["char_count"]}/{draft["char_limit"]} chars, '
        f'type={draft["post_type_name"]}'
    )

    # Save files
    path = create_draft_files(draft, history)
    if path:
        logger.info(f'[DONE] Draft saved — awaiting approval: {path.name}')
        logger.info(f'[TIP]  Recommended posting time: {draft["recommended_time"]}')
    else:
        logger.error(f'[FAIL] Could not create draft files for {pname}')

    # Scan for previous approvals/rejections
    scan_approvals(history)


def generate_all_platforms():
    """Generate one draft per platform (used in --schedule mode)."""
    for platform in PLATFORMS:
        try:
            generate_for_platform(platform)
        except Exception as e:
            logger.error(f'[ERROR] Failed generating for {platform}: {e}')


def run_scheduled():
    """Loop mode: check once per hour, generate drafts at recommended times."""
    logger.info('=' * 60)
    logger.info('Social Media Poster — Scheduled Mode (Gold Tier)')
    logger.info(f'Platforms: {", ".join(p["name"] for p in PLATFORMS.values())}')
    logger.info(f'Recommended times: {", ".join(RECOMMENDED_TIMES)}')
    logger.info(f'Max per platform per day: {MAX_POSTS_PER_DAY}')
    logger.info(f'Check interval: {SCHEDULE_CHECK_INTERVAL}s')
    logger.info('=' * 60)

    # Posting hours extracted from RECOMMENDED_TIMES
    posting_hours = set()
    for t in RECOMMENDED_TIMES:
        try:
            h = int(t.split(':')[0])
            posting_hours.add(h)
        except ValueError:
            pass

    try:
        while True:
            now = datetime.now()
            hour = now.hour

            # Scan for approvals/rejections every cycle
            history = load_history()
            scan_approvals(history)

            # Generate drafts during recommended hours
            if hour in posting_hours:
                logger.info(
                    f'[SCHEDULE] {now.strftime("%A %H:%M")} — '
                    'posting window open, generating drafts'
                )
                generate_all_platforms()
            else:
                logger.info(
                    f'[SCHEDULE] {now.strftime("%A %H:%M")} — '
                    'outside posting window, sleeping'
                )

            time.sleep(SCHEDULE_CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.info('[STOP] Social Media Poster stopped by user')


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Social Media Poster — AI Employee Vault (Gold Tier)'
    )
    parser.add_argument(
        '--platform',
        choices=list(PLATFORMS.keys()),
        help='Generate a draft for a specific platform (facebook, instagram, twitter)',
    )
    parser.add_argument(
        '--schedule',
        action='store_true',
        help='Run continuously, generating drafts for all platforms at recommended times',
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='Generate one draft for every platform right now',
    )
    parser.add_argument(
        '--scan',
        action='store_true',
        help='Only scan for approval/rejection updates (no new drafts)',
    )
    args = parser.parse_args()

    if args.scan:
        logger.info('[SCAN] Checking for approval updates...')
        history = load_history()
        scan_approvals(history)
    elif args.schedule:
        run_scheduled()
    elif args.all:
        generate_all_platforms()
    elif args.platform:
        generate_for_platform(args.platform)
    else:
        # Default: generate for all platforms
        generate_all_platforms()


if __name__ == '__main__':
    main()
