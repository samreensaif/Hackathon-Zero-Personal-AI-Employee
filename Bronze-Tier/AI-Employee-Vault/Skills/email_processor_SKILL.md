# Email Processor Skill

## Skill Summary

**Purpose:** Autonomously process email tasks that arrive in the Needs_Action folder, analyze sender and content, determine appropriate actions, create execution plans, and manage task lifecycle.

**Trigger:** When new .md files appear in Needs_Action folder
**Authority Level:** Medium - Can draft responses and create plans; requires approval for sending
**Success Metric:** All emails processed with documented action plans

---

## Overview

This skill enables Claude to:
1. Monitor and process email task files from the Needs_Action folder
2. Extract and analyze email metadata (sender, subject, content)
3. Classify emails and determine appropriate actions
4. Generate detailed action plans with next steps
5. Move completed emails to the Done folder
6. Maintain audit trail of processing decisions

---

## Prerequisites

- Access to Needs_Action, Done, and Plans folders
- Access to Company_Handbook.md for reference rules
- Ability to read and create markdown files
- Understanding of communication protocols (drafts, not automated sends)

---

## Step-by-Step Processing Instructions

### Step 1: Identify New Email Task Files

**What to do:**
- List all .md files in the Needs_Action folder
- Identify files that haven't been processed yet
- Note the timestamp in the filename (format: YYYYMMDD_HHMMSS)

**Check for:**
- File naming pattern: `{timestamp}_{sender}_action.md`
- Required sections: From, Subject, Received, Email ID, Preview
- File creation date vs. last processed time

---

### Step 2: Extract Email Information

**Read the email task file and extract:**
```
- **From:** [sender email]
- **Subject:** [email subject line]
- **Timestamp:** [when received]
- **Preview/Body:** [email content - first 500 chars]
- **Email ID:** [Gmail API ID for reference]
```

**Validate extraction:**
- Confirm all required fields are present
- Check sender is legitimate (not spam)
- Verify timestamp is reasonable

---

### Step 3: Analyze Email Content and Classify

**Classify the email by type:**

1. **Action Required (High Priority)**
   - Asks for specific response or decision
   - Time-sensitive (mentions deadlines)
   - From executives or key stakeholders
   - Requests approval or authorization

2. **Information/FYI**
   - Informational only
   - No response needed
   - Status updates
   - Newsletters/announcements

3. **Request for Clarification**
   - Unclear what's being asked
   - Missing context
   - Potentially spam
   - Duplicate/already handled

4. **Follow-up Needed**
   - Requires research before response
   - Needs coordination with others
   - Dependent on other tasks

---

### Step 4: Determine Appropriate Action

**Based on email type and Company_Handbook rules, decide:**

**Option A: Reply**
- Conditions: Direct question, feedback requested, action item for you
- Action: Draft a response (DO NOT SEND - requires human approval)
- Include reasoning for each point in draft

**Option B: Forward**
- Conditions: Task belongs to someone else, requires delegation
- Action: Identify best recipient, draft forwarding message
- Explain why this person is best suited

**Option C: Archive/Dismiss**
- Conditions: FYI only, already handled, not actionable
- Action: Move to Done folder with "archived" note
- Brief reason for dismissal

**Option D: Escalate/Request Clarification**
- Conditions: Unclear intent, potential issue, needs approval
- Action: Create escalation note with specific questions
- Recommend human review before proceeding

---

### Step 5: Apply Company Handbook Rules

**Always follow these protocols:**

#### Communication Rules (from Company_Handbook.md):
- Be professional and courteous in all communications
- Respond within 24 hours of receiving important emails
- Cite company policies when making decisions
- Maintain confidentiality of sensitive information
- Use company email templates for formal communication

#### Task Processing Rules:
- Only process one email per execution
- Create documented proof of decision-making
- Mark processing date and time
- Include reasoning for all actions
- Never delete emails - only archive to Done

#### Approval Requirements:
- Draft responses require human approval before sending
- High-priority emails (from executives) need review
- Forward decisions should be confirmed by manager
- Escalations must include specific questions for human review

---

### Step 6: Create Action Plan (Plan.md)

**Generate a detailed plan file with this structure:**

```markdown
# Email Action Plan

**Email From:** [sender name/email]
**Subject:** [email subject]
**Processed:** [current timestamp]
**Email ID:** [Gmail ID]
**Classification:** [type from Step 3]

---

## Email Summary

[Brief 1-2 sentence summary of the email content and main ask]

## Analysis

- **Priority Level:** High/Medium/Low
- **Urgency:** By [date if mentioned], otherwise ASAP
- **Key Points:** 
  - Point 1
  - Point 2
  - Point 3

## Recommended Action

**Primary Action:** [Reply/Forward/Archive/Escalate]

### Why This Action:
[2-3 sentences explaining the reasoning behind this choice]

### Alignment with Company Policy:
- ✅ Follows [relevant handbook rule]
- ✅ Responds within [timeframe]
- ✅ [Other relevant policy]

---

## Implementation Details

### If Action = "Reply":
**Draft Response:** (DO NOT SEND - REQUIRES APPROVAL)
```
[Draft email response here]
```
**Points Addressed:**
- Question 1: [answer]
- Question 2: [answer]

**Approval Checklist:**
- [ ] Response is professional and accurate
- [ ] All questions answered
- [ ] Tone is appropriate
- [ ] Ready to send

### If Action = "Forward":
**Forward To:** [recipient name/email]
**Suggested Message:**
```
[How to introduce/frame the forwarded email]
```
**Why [Name]:** [Explanation of why they should handle this]

### If Action = "Archive":
**Reason:** [Brief reason this is FYI/already handled]
**No further action needed.**

### If Action = "Escalate":
**Escalate To:** [manager/relevant person]
**Specific Questions:**
1. [Question to clarify]
2. [Question to resolve]
3. [Question about policy]

**Why Escalation Needed:** [Brief explanation]

---

## Next Steps

1. **Immediate:** [First action - usually human review]
2. **Follow-up:** [Secondary action if needed]
3. **Tracking:** Check back [within X days]

## Status

- [ ] Plan Created
- [ ] Submitted for Approval
- [ ] Action Taken
- [ ] Completed

---

*Created by: Email Processor Skill*
*Processing Chain: Needs_Action → [Action] → Done*
```

---

### Step 7: Move to Done Folder

**After creating the plan:**
- Create filename: `{original_timestamp}_{sender}_DONE.md`
- Move/copy the original email file to Done folder
- Include a marker that it's been processed
- Keep Plan file in Plans folder for reference

**Done Folder Entry format:**
```markdown
[Original email content with added footer]

---
**PROCESSOR NOTE:**
- Status: Processed
- Action: [Reply/Forward/Archive/Escalate]
- Plan Reference:** [Link to Plans/filename]
- Processed Date:** [timestamp]
```

---

## Examples: Good Email Processing Decisions

### Example 1: Customer Support Request
```
Email: "Can you help with our account setup?"
From: customer@company.com
Classification: Action Required
Decision: Reply
Reasoning: Direct question from customer, needs specific help, time-sensitive
Plan: Draft response with setup steps, offer call to discuss
```

### Example 2: FYI from Colleague
```
Email: "Just wanted to let you know Q1 project is on track"
From: colleague@company.com
Classification: Information/FYI
Decision: Archive
Reasoning: Status update, no response needed, informational only
Plan: Move to Done, mark as acknowledged
```

### Example 3: Ambiguous Request
```
Email: "Need to discuss the proposal changes"
From: manager@company.com
Classification: Request for Clarification
Decision: Escalate
Reasoning: Unclear what specific changes or timeline, needs clarification
Plan: Ask for specific proposal details and deadline; request meeting
```

### Example 4: Task for Colleague
```
Email: "Can you handle the vendor contract review?"
From: director@company.com
Classification: Action Required (but for someone else)
Decision: Forward
Reasoning: Legal team handles contracts, should be delegated
Plan: Forward to legal@company with note about timeline and priority
```

---

## Email Classification Decision Tree

```
START: New email arrived

├─ Is this spam/suspicious?
│  └─ YES → Archive
│
├─ Is this just information (FYI/update)?
│  └─ YES → Archive
│
├─ Does this ask for a direct response from me?
│  ├─ YES, I can answer → Reply
│  ├─ YES, but unclear → Escalate (request clarification)
│  └─ NO
│
├─ Should someone else handle this?
│  └─ YES → Forward to appropriate person
│
└─ DEFAULT → Escalate for human review

END: Action determined, Plan created
```

---

## Success Criteria

✅ All inbox emails have been read and analyzed
✅ Each email has a corresponding Plan file in Plans folder
✅ Classification is logical and well-reasoned
✅ Suggested actions follow Company Handbook rules
✅ No emails remain in Needs_Action without a plan
✅ Processed emails moved to Done folder
✅ Draft responses (if any) clearly marked "FOR REVIEW, DO NOT SEND"
✅ All decisions documented with clear reasoning

---

## Common Pitfalls to Avoid

❌ **Don't:** Send email responses without human approval
✅ **Do:** Always draft with explicit "REQUIRES APPROVAL" marker

❌ **Don't:** Delete or lose email records
✅ **Do:** Move to Done folder, keep audit trail

❌ **Don't:** Ignore Company Handbook rules
✅ **Do:** Reference specific policies in decision reasoning

❌ **Don't:** Make decisions on ambiguous emails
✅ **Do:** Escalate with specific questions for clarification

❌ **Don't:** Forget to include reasoning in plans
✅ **Do:** Always explain the decision-making logic

---

## Integration with Other Skills

- **Dashboard Updater:** After processing, update Dashboard with task count
- **Email Responder:** (Future) Human-approved responses via this skill
- **Archive Manager:** Cleanup of Done folder after 30 days
- **Escalation Handler:** Route escalations to appropriate managers

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-12 | 1.0 | Initial skill creation |

---

## Questions to Ask Before Processing

Before you mark an email as processed, ask yourself:

1. Do I fully understand what the sender is asking?
2. Is this something I should handle or delegate?
3. What does Company_Handbook.md say about this type of communication?
4. What's the appropriate timeline for response?
5. Have I included clear reasoning in my plan?
6. Does my recommended action align with company policy?
7. Is this documented properly for audit/review?

If you answer "No" to any of these, **Escalate** and ask for clarification.

---

**Skill Author:** AI Employee Vault System
**Last Updated:** February 12, 2026
**Status:** Active
