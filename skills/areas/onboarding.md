---
key: area.onboarding
kind: area
area: onboarding
title: Client onboarding
owner_role: account_lead
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Onboarding turns a signed client into a workspace the team can operate safely: the right people, the right access, approved facts, an agreed scope and a first 30-day plan with owners. Most later failures (wrong order metric, unapproved offers, a key sitting in chat, work nobody asked for) start as onboarding gaps. A founder should care because a clean first month sets the client's trust and the agency's margin on the account.

## Client facts and rules
Look each fact up with `get_context` (area `onboarding`, plus `economics`, `product_customer`, `data_infrastructure` where noted). If a fact is missing, ask for it through the discovery questionnaire or kickoff, record the answer with `propose_fact` citing the source, and do not act on it until approved.

- `store_platform` – current store platform. Missing: block all revenue work; ask at kickoff.
- `platform_history` – past store platforms and switch dates (old metrics linger in the ESP). Missing: `flag_insight` for the analyst before any order-source work.
- `messaging_tools` – every tool that sends email or SMS, and what each sends. Missing: stop channel planning; overlapping senders cause duplicate messages and consent conflicts.
- `subscription_app` – subscription platform or "none", and whether its events reach the ESP. Missing: treat subscription status as unknown, never as "not subscribed".
- `client_goal_90d` – the goal in the client's words, with a number if given. Missing: record "no numeric goal"; never invent one.
- `regulated_limits` – category limits on claims, offers and channels (supplements, CBD/THC, alcohol, health claims), or "none". Missing: hold all copy and offer work.
- `stakeholder_map` – each person, role, decision rights (approve copy, offers, spend, live changes), and channel. Missing: no approvals can be routed; stop.
- `approval_matrix` – which approvals need which person, in writing, and the turnaround they committed to. Missing: default every live action to the client owner and flag it.
- `communication_preferences` – channels, update cadence, working hours, timezone, who to copy. Missing: use the written-recap default and ask in the first meeting.
- `scope_of_work` – contracted deliverables, channels, volumes, exclusions and term. Missing: do no production; ask the account lead.
- `sending_domains_dns_owner` – sending domains and who controls DNS. Missing: deliverability work cannot start; flag.

Exceptions: sister brands are separate workspaces with separate keys; if another agency runs one channel, write the boundary into `scope_of_work`.

## Inputs and sources
- **Signed agreement and proposal** – trusted for scope, term and price. Trap: a sales call promise is not scope until it is written.
- **Discovery questionnaire** – trusted for facts only the client knows (margins, offers, limits, history). Trap: answers are proposed facts, not approved ones, and margins are never inferred.
- **Kickoff call notes** – trusted for context and named owners. Trap: "proposed" in a note is not an approval.
- **Connected accounts** (ESP, store, SMS, reviews) – trusted for what exists. Trap: a working login is not permission to change anything, and list size is not sendable reach.
- **Any baseline audit** – trusted for the starting scorecard only after its verify stage passed.

## SOP
1. On contract signature, call `get_context` for `onboarding` and confirm the workspace, scope and stakeholders exist. Create internal tasks for every step below with owners and due dates.
2. Draft the discovery questionnaire from the standard sections: basics (platform, history, tools, subscription app, goal, limits, day-to-day contact), economics, products and customers, history (past agencies, big changes, sending domains, DNS owner, suspected breakages). Save it with `save_output` (kind `brief`). The account lead sends it; you never send it yourself.
3. Draft the access checklist: each platform, the access type requested (read-only API key or a named staff seat, never a shared password), who grants it, and how keys travel (one-time secret link or live entry, never chat or email). Save it as part of the onboarding pack.
4. Draft the kickoff agenda (60 minutes): introductions and roles; goals in the client's words; scope and exclusions; regulated limits and claims; offers and discount rules; stakeholder map and approval matrix; communication preferences; access status; the onboarding timeline; open questions with owners. Save it with `save_output` (kind `brief`) at least 48 hours before the call.
5. After kickoff, turn every answer into a `propose_fact` call with the right area and component, citing the note or form. Never merge an answer into a brand rule without approval.
6. Build the stakeholder map and approval matrix from the notes. Every live action type (campaign send, live flow change, discount, suppression, account setting) gets a named approver.
7. Track access. When the account lead confirms a connection, verify it opens the expected account (account ID and timezone match) with a read. A wrong account is common when brands share a login; flag it immediately.
8. Assemble the standard onboarding deliverables (see Outputs). For each missing input, record its owner, due date and what it blocks.
9. Draft the first 30-day plan: week 1 access and facts; week 2 baseline audit and source pinning; weeks 3–4 the first repairs (broken promises, links, events, compliance) before any new builds. Each item has an owner, due date, dependency and done-when.
10. Draft the client recap of kickoff and the 30-day plan. Call `request_approval` with `client.message.send` and the exact text, then `finish`.

## Decision rules
- If `regulated_limits` is missing, then do not draft any copy, offer or claim; flag it as high severity.
- If the client sends a full-access key or a password, then do not use it; flag it, ask the account lead to request a read-only key, and treat the exposed secret as needing rotation.
- If a key or password appears in chat, email, a task or a file, then flag it as critical so a lead can revoke it now.
- If two people claim the same approval right, then ask the account lead to settle it in writing before routing any approval.
- If the client asks for a live change during onboarding, then log it as a 30-day-plan item needing its own approval.
- If the store platform or order metric is ambiguous, then stop revenue work and hand it to `data_infrastructure`.
- If a requested deliverable is outside `scope_of_work`, then route it to account management as a scope change; do not start it.
- If the 30-day plan depends on a client input more than 5 business days late, then flag it and move dependent items, never fill the gap with assumptions.

## Examples
### Good
"Kickoff recap (draft for account lead). Agreed: the 90-day goal is the client's stated repeat-purchase target (fact proposed, source: kickoff notes). Approvals: copy by the marketing manager, offers and live flow changes by the founder, 2-business-day turnaround. Open: margin by product (owner: client finance, due Friday; blocks offer sizing). Access: ESP read-only key verified against the expected account ID; SMS platform seat pending (owner: client ops). Week 1–4 plan attached with owners."
### Not good
"Great kickoff! We'll start with a 20% welcome offer and launch the new flows next week." It invents an offer, promises live work before approvals and access, names no owners, and would go to the client without review.

## Quality checklist
- [ ] Every kickoff answer is recorded as a proposed fact with a source, not written into a rule.
- [ ] The stakeholder map names an approver for each live action type.
- [ ] The access checklist requests read-only keys or named seats only, and states how keys travel.
- [ ] No credential, key or personal data appears anywhere in the output.
- [ ] The standard onboarding deliverables list shows status, owner and due date for every item.
- [ ] Each missing input states what it blocks.
- [ ] The 30-day plan puts repairs before new builds, and each item has owner, due date, dependency and done-when.
- [ ] No offer, number or outcome is promised that isn't an approved fact.
- [ ] Client-facing text is submitted through `request_approval`, not sent.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged (questionnaire, agenda, plan, recap) |
| `task.manage` | autonomous | Agent, logged |
| `issue.flag` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent or client decider; stays proposed |
| `approval.request` | autonomous | Agent |
| `connection.manage` | autonomous (human only; agents are refused) | Org admin or account lead only (human) |
| `fact.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `decision.approve` | explicit_approval | Account lead, org admin or client owner/approver (30-day plan) |
| `client.message.send` | explicit_approval | Account lead or org admin |

## Outputs and destination
Standard onboarding deliverables, each saved with `save_output`:
1. Discovery questionnaire (brief) – to the account lead to send.
2. Kickoff agenda (brief) – task output, 48 hours before the call.
3. Access checklist and register (other) – task output; status per platform.
4. Stakeholder map and approval matrix (other) – task output, then proposed facts.
5. Communication preferences summary (other) – proposed facts.
6. Scope and deliverables summary, with exclusions (other) – task output.
7. Onboarding timeline (other): Wavy starting default of 10 business days from signature to approved 30-day plan; baseline readout within 5 business days of access.
8. First 30-day plan (draft_spec) – task output, then a decision for approval.
9. Kickoff recap (copy) – client message via `request_approval`.

## Maintenance
The account lead reviews this skill every 90 days and after each new client's first 30 days. Review early if a client onboarding missed an approval, an access incident occurred, or the questionnaire failed to capture a fact that later caused rework.
