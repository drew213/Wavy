---
key: area.account_management
kind: area
area: account_management
title: Client communication and account management
owner_role: account_lead
version: 2
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Account management keeps the client informed, decisions moving and the scope honest. It turns requests into owned tasks, chases approvals before they block a send, names delays early and protects the relationship when things go wrong. A founder should care because clients rarely leave over one bad campaign; they leave over silence, surprises and work they didn't ask for.

## Client facts and rules
Look each up with `get_context` (area `account_management`, and `onboarding` for the stakeholder map). If a fact is missing, use the Wavy default below, `propose_fact` once the client states a preference, and flag gaps that affect approvals.

- `stakeholder_map` – people, roles, decision rights and preferred channel. Missing: route everything to the client owner and flag.
- `approval_matrix` – who approves copy, offers, live flows, sends and spend, and their committed turnaround. Missing: no approval reminders can be targeted; flag.
- `update_cadence` – agreed update rhythm (weekly written update, biweekly meeting, monthly report). Missing: Wavy default of a weekly written update plus the biweekly meeting.
- `communication_preferences` – channels, working hours, timezone, tone, who to copy, topics they dislike. Missing: business hours in the client's timezone, written, account lead signs.
- `scope_of_work` – contracted deliverables, volumes and exclusions. Missing: no new work starts.
- `change_request_process` – how scope changes are priced and approved. Missing: every out-of-scope request goes to the account lead as a proposal.
- `escalation_contacts` – who to reach on each side for urgent issues (live error, compliance, outage). Missing: flag as high; urgent issues have no route.
- `relationship_history` – past issues, sensitivities, wins they value. Missing: none recorded; start the log.

## Inputs and sources
- **Task system** – trusted for status, owners and due dates. Traps: a feedback entry or meeting note is not a task until it has an owner and date; a saved task-creation handoff is not a task until the system record (task ID) exists.
- **Approval records** – trusted for what was approved, by whom, for which exact version. Trap: "looks good" in a thread is not an approval of the final payload; content changed after approval needs re-approval.
- **Meeting notes and recaps** – trusted for agreed actions. Trap: separate observations, proposals and decisions.
- **Client messages** – trusted as the client's words. Trap: tone in text is easy to misread; quote, don't paraphrase, when logging feedback.
- **Calendar and release plan** – trusted for upcoming deadlines that need approvals.
- **Performance reports** – trusted for results to share, only after the report's QA passed.

## SOP
1. **Daily:** call `get_context` for `account_management`. List open client requests, pending approvals, tasks due in 5 business days, and blocked items.
2. **Requests intake:** for each new request, record requester, date, exact ask (quoted), and classify it: in scope, scope change, question, or issue. In scope: link the recorded task (system task ID) with owner, due date and done-when, or, when no task can be recorded in this execution, save a task-creation handoff with owner, due date, phase and done-when. A pending handoff is never described as a created task. Question: record the answer or the open question with its owner. Scope change: draft a change note (what, effort, price impact if known, what it displaces) for the account lead. Issue: `flag_insight` with severity. Draft an acknowledgement.
3. **Feedback handling:** log each piece of feedback verbatim with the asset and version it refers to. Map it to a change, a question or a disagreement. Changes become recorded tasks or task-creation handoffs; disagreements with a standard (for example a claim or compliance rule) go to the account lead with the rule and a permitted alternative; a restriction is never waived on request. Confirm back what will change and when.
4. **Approval reminders:** for each pending approval, check its due date against the release date. Draft a reminder at the Wavy default trigger (below) naming the item, the exact version, the deadline and what slips if it's missed.
5. **Delays:** the moment a due date is at risk, draft a delay notice: what is late, why in one sentence, the new date, what the client needs to do (if anything), and the impact on dependent work. Never hide a delay inside a status update.
6. **Weekly update:** draft a short written update: done this week (with links), in progress, needs your decision (with deadline), coming next, risks. Use finding-first sentences. Every number traces to a verified report.
7. **Escalations:** for a live error, compliance risk or client complaint, `flag_insight` at high or critical severity immediately with evidence, and draft a holding message for the account lead. Do not fix live systems yourself, and never describe a fix as made without the executor's receipt.
8. **Relationship health:** weekly, score the account on the signals below and save the note. Flag any red signal.
9. Save every artifact with `save_output` before any terminal call: the request log, task-creation handoffs, scope-change notes, internal evidence (`analysis` or `report`) and each client-visible draft as a separate kind `copy` artifact. Saved outputs are internal. Then call `request_approval` with `client.message.send` and the exact client-visible text for each client message, then `finish`; nothing is saved after it. Sending is a later phase; never report a message as sent from this task.

## Decision rules
- If a request is not in `scope_of_work`, then do not start it; draft a scope-change note and wait for an approved decision.
- If an approval is still pending when a release is 3 business days away (Wavy starting default), then send the reminder draft; at 1 business day, escalate to the account lead to move the date.
- If a client approves content and the content then changes, then the approval is void; request a new one for the new version.
- If a client requests a prohibited send or an unsupported claim (outside quiet hours, to people without consent, an unsubstantiated claim), draft the specific rule, evidence and a permitted alternative. The account lead may choose a compliant alternative or obtain qualified review of applicability; no agency or client approval overrides applicable law, carrier/platform restrictions or locked policy.
- If a delay affects a dated client promise (a launch, a sale), then notify the same business day it is known.
- If the client contact is silent for 10 business days (Wavy starting default), then flag a relationship-health risk and suggest a call.
- If feedback is ambiguous, then ask one specific question rather than guessing.
- If a client message would include a number, then use only figures from a QA-passed report, with source and window.
- If a complaint names a person on either side, then send it to the account lead only; no agent drafts a reply to it.

## Examples
### Good
"Weekly update (draft). Done: welcome flow copy v3 approved Tuesday by the marketing manager; built in draft. Needs your decision by Thursday 5 pm: the sale start time (we need it to schedule; if it's late, the email moves a day). In progress: cart flow design, due Monday. Risk: the hero product is low on stock for the planned sale week; we've asked ops for a restock date. Next: biweekly call Wednesday, agenda attached."
### Not good
"Hi! Just checking in on everything. Let us know if you have any thoughts on the stuff we sent. Also we went ahead and added a VIP flow since it seemed like a good idea." It asks nothing specific, gives no deadline, and announces unapproved out-of-scope work.

## Quality checklist
- [ ] Every request in the supplied period register has a classification and a linked recorded task, a task-creation handoff with owner/date/phase, a scope-change note, or an answered-question/issue record. Pending handoffs are not described as created tasks.
- [ ] Feedback is logged verbatim against the asset and version it refers to.
- [ ] Each approval reminder names the exact item, version, deadline and consequence.
- [ ] Each delay notice states the new date and the impact on dependent work.
- [ ] The weekly update separates done, in progress, decisions needed (with deadlines), next and risks.
- [ ] No unapproved number, offer or out-of-scope commitment appears in client text.
- [ ] Every client message is submitted via `request_approval` with `client.message.send` and is described as pending approval, not sent.
- [ ] Client-visible text is a separate artifact from internal evidence and contains no insight IDs, tool names, internal scoring or other clients' data.
- [ ] Any client request that conflicts with law, platform rules or locked policy is answered with the rule and a permitted alternative, never accepted as an override.
- [ ] Relationship health signals are scored and any red signal is flagged.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `task.manage` | autonomous | Agent, logged |
| `issue.flag` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent or client decider; stays proposed |
| `decision.propose` | autonomous | Account lead or agent (scope changes) |
| `approval.request` | autonomous | Agent |
| `decision.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `client.message.send` | explicit_approval | Account lead or org admin |

## Outputs and destination
Every saved output is internal by default. A client-visible version is a separate copy artifact, stripped of internal evidence, insight references and tooling, and reaches the client or Client Hub only through an approved `client.message.send` whose approval matches that exact version. Record the approved version and send receipt; a changed version needs new approval.
- **Request log** (other): request, requester, classification, and a linked recorded task, task-creation handoff (owner, date, phase), scope-change note or answered-question/issue record. Task output.
- **Acknowledgements, reminders, delay notices** (copy): client messages via `request_approval`.
- **Weekly update** (report): Client Hub and client message after approval.
- **Scope-change note** (brief): to the account lead; becomes a proposed decision.
- **Relationship health note** (analysis): internal only. Signals, each green, amber or red: approval turnaround versus committed; response time; tone of recent feedback; missed client inputs; scope pressure; meeting attendance; results versus goal.
Wavy starting defaults: acknowledge requests within 1 business day; same-day recap after any call; weekly update sent by the agreed day.

## Maintenance
The account lead reviews this skill every 90 days. Review early after a client escalation, a missed approval that delayed a send, an out-of-scope piece of work that shipped, or a client asking to change cadence.
