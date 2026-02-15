# LinkedIn Poster Skill

## Skill Summary

**Purpose:** Generate LinkedIn post drafts from business goals and completed work, route them through the approval workflow, and track post history for rate-limiting and template rotation.

**Trigger:** Manual invocation (`python linkedin_poster.py`) or scheduled mode (`--schedule`)
**Authority Level:** Medium — Can draft posts and submit for approval; cannot publish without human sign-off
**Success Metric:** Well-crafted drafts in Pending_Approval/ with proper metadata, within weekly posting limits

---

## Overview

This skill enables the AI Employee to:
1. Read `Business_Goals.md` for strategic priorities, milestones, and metrics
2. Scan `Done/` for recently completed tasks as evidence of progress
3. Select an appropriate post template (avoiding recently used ones)
4. Fill the template with content atoms (hook, value, CTA, hashtags)
5. Save the draft to `Pending_Approval/` with full metadata
6. Track post history in `posts_history.json` for rate-limiting
7. Scan for approval/rejection updates on previously submitted posts

---

## Prerequisites

- Access to `Business_Goals.md` and `Company_Handbook.md`
- Access to `Done/`, `Pending_Approval/`, `Approved/`, `Rejected/` folders
- Access to `Logs/` for `linkedin_poster.log`
- `posts_history.json` in `Watchers/` for tracking
- Environment variables configured in `.env`

---

## Step-by-Step Processing Instructions

### Step 1: Rate-Limit Check

**Before generating any content:**
1. Load `posts_history.json`
2. Count posts created in the current ISO week
3. If count >= `LINKEDIN_POST_MAX_PER_WEEK` (default: 3), skip generation
4. Log the current count for visibility

**Why:** Over-posting reduces engagement and annoys followers.

---

### Step 2: Gather Content Sources

**Read Business_Goals.md:**
- Extract strategic priorities (numbered bold items with descriptions)
- Extract active projects from markdown tables (name, status, priority, milestone)
- Store raw text for fallback content

**Read Done/ folder:**
- Get the 10 most recently modified `.md` files
- Extract the title (first `#` heading) from each
- Store filename, title, and first 300 chars as context

---

### Step 3: Select Template

**Available templates:**
| Template ID | Name | Best For |
|-------------|------|----------|
| `milestone` | Milestone Celebration | Announcing completed goals |
| `lesson_learned` | Lesson Learned | Sharing insights from work |
| `thought_leadership` | Thought Leadership | Industry perspectives |
| `progress_update` | Progress Update | Weekly/periodic summaries |
| `behind_the_scenes` | Behind the Scenes | Process and culture posts |

**Selection logic:**
1. Check which templates were used in the last 14 days
2. Prefer templates NOT recently used (freshness)
3. If all have been used recently, choose randomly from the full pool

---

### Step 4: Fill the Template

**Content atoms to generate:**

| Atom | Source | Fallback |
|------|--------|----------|
| `hook` | Random from HOOKS pool | Always available |
| `value` | Priority title + detail from Business_Goals.md | Generic value statement |
| `milestone` | Project milestone from Business_Goals.md | "a key checkpoint" |
| `project` | First active project name | "our latest initiative" |
| `takeaway` | Priority detail or lesson | Generic automation quote |
| `result` | Most recent Done/ item title | "steady progress" |
| `count` | Number of Done/ items | 0 |
| `cta` | Random from CTAS pool | Always available |
| `hashtags` | 3 random from HASHTAG_POOL | Always available |

---

### Step 5: Create Approval File

**Save to `Pending_Approval/` with this format:**

Filename: `{YYYY-MM-DD_HH-MM}_linkedin_post_{template_id}.md`

**Required sections:**
1. Header with Action Type, Recipient, Created timestamp, Template, Word Count, Urgency
2. Draft Content (the actual post text)
3. Post Metadata table (template, words, hashtags, recommended posting time)
4. Content Sources (which files informed the post)
5. Approval Checklist (professional, on-brand, no confidential info, etc.)
6. Instructions for approve/reject/edit workflow

---

### Step 6: Update History

**Record the draft in `posts_history.json`:**
```json
{
  "created_at": "2026-02-14T10:00:00",
  "filename": "2026-02-14_10-00_linkedin_post_milestone.md",
  "template_id": "milestone",
  "status": "pending_approval",
  "word_count": 85,
  "sources": ["Business_Goals.md — priority: ..."]
}
```

---

### Step 7: Scan for Previous Decisions

**On every run, also check:**
1. Are any previously pending posts now in `Approved/`? Update status.
2. Are any previously pending posts now in `Rejected/`? Update status.
3. Have any posts disappeared from all folders? Mark as `unknown`.

---

## Scheduled Mode

When run with `--schedule`:
1. Check once per hour
2. Only generate drafts on configured posting days during optimal hours
3. Scan for approval/rejection updates on every cycle (regardless of day)
4. Respect weekly post limit across all cycles

**Configuration:**
- `LINKEDIN_POSTING_DAYS`: e.g., "Tuesday,Thursday"
- `LINKEDIN_OPTIMAL_HOURS`: e.g., "9-11"
- `LINKEDIN_POST_MAX_PER_WEEK`: e.g., 3

---

## Content Guidelines (from Company_Handbook.md)

### Post Structure
1. **Hook** — Attention-grabbing opening line
2. **Value** — Core insight or announcement
3. **Takeaway** — What the reader should learn
4. **CTA** — Call to action (engagement prompt)
5. **Hashtags** — Max 3, relevant to content

### Rules
- All posts require CEO approval before publishing
- No confidential or internal-only information
- Professional tone aligned with brand voice
- No political, controversial, or divisive content
- Balance promotional vs. value-add content (80/20)

---

## Examples

### Good: Milestone Post Generated
```
Template: Milestone Celebration
Sources: Business_Goals.md (priority: "Launch AI Employee"), Done/setup_DONE.md
Word count: 78
Status: Saved to Pending_Approval/2026-02-14_10-00_linkedin_post_milestone.md
History: Updated posts_history.json (1/3 this week)
```

### Good: Weekly Limit Respected
```
Posts this week: 3/3
Action: SKIP — already at weekly limit
Log: [LIMIT] Already created 3/3 posts this week. Skipping.
```

### Good: Template Rotation
```
Recently used (14 days): milestone, lesson_learned
Available fresh: thought_leadership, progress_update, behind_the_scenes
Selected: progress_update (avoiding repetition)
```

### Bad: Posting Without Approval
```
NEVER publish directly to LinkedIn.
Always save to Pending_Approval/ and wait for human review.
The approval_manager.py handles the post-approval workflow.
```

---

## Common Pitfalls

| Pitfall | Correct Behaviour |
|---------|-------------------|
| Posting the same template repeatedly | Check recently_used_templates() before selecting |
| Exceeding weekly post limit | Always check posts_this_week() before generating |
| Including confidential data | Only use content from Business_Goals.md and Done/ titles |
| Skipping approval workflow | Always save to Pending_Approval/, never post directly |
| Not logging sources | Always record which files informed the post content |

---

## Integration Points

| Component | Interaction |
|-----------|-------------|
| `Business_Goals.md` | Content source for priorities and projects |
| `Done/` folder | Content source for completed work evidence |
| `Company_Handbook.md` | Rules for tone, structure, and approval |
| `Pending_Approval/` | Output destination for draft posts |
| `approval_manager.py` | Processes approved/rejected posts downstream |
| `posts_history.json` | Shared state for rate-limiting and tracking |
| `task_scheduler.py` | Launches linkedin_poster in scheduled mode |

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-14 | 1.0 | Initial skill creation for Gold Tier |

---

**Skill Author:** AI Employee Vault System
**Last Updated:** February 14, 2026
**Status:** Active
