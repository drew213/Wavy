---
key: area.knowledge_maintenance
kind: area
area: knowledge_maintenance
title: Knowledge maintenance and offboarding
owner_role: account_lead
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Knowledge maintenance keeps each client's approved facts current, records why decisions were made, and feeds proven learnings back into agency standards without leaking client data. Offboarding closes an account cleanly: access revoked, assets handed over, commitments resolved, records archived. A founder should care because stale facts produce wrong work, and a departed client whose keys still work is a liability the agency carries.

## Client facts and rules
Look each up with `get_context` (area `knowledge_maintenance`, plus every area whose facts are under review). If a fact is missing, flag it and do not reconstruct it from memory or old notes.

- `fact_review_dates` – each approved fact's `verified_by`, `verified_at` and `review_by`. Missing review date: treat the fact as due for review now.
- `decision_log` – each decision with its insights, who approved it, the plan scope, its tasks and measured result. Missing link: flag; a result without a decision or a decision without evidence breaks the chain.
- `approved_learnings` – results the client and account lead accepted as learned, with window and method. Missing: nothing is learned yet; observations stay observations.
- `access_register` – every connection, seat and key: platform, scope, grantor, created and revocation date. Missing: flag high; offboarding cannot be verified.
- `asset_inventory` – templates, flows, segments, briefs, designs, reports, and who owns each under the contract. Missing: ask the account lead; ownership terms come from the contract.
- `outstanding_commitments` – promised work, open approvals, scheduled sends, live tests, invoices. Missing: rebuild from tasks and approvals and flag the gaps.
- `contract_end_terms` – end date, notice, handoff obligations, data retention terms. Missing: no offboarding step starts.
- `archive_policy` – what is kept, for how long, and what is deleted. Missing: use the Wavy default below and flag.

## Inputs and sources
- **Client knowledge base** – trusted for approved facts and their review dates. Trap: a proposed fact is not knowledge; meeting notes never silently become rules.
- **Decision, task and result records** – trusted for what happened and what was measured. Trap: a result read before its window closed is provisional.
- **Approval records** – trusted for what was approved, by whom, on which payload.
- **Connections and access register** – trusted for configured access. Trap: a removed local credential does not mean the platform key was deleted; confirm on the platform.
- **Scheduled sends, live flows and tests** – trusted for what will keep running after the agency leaves.
- **Agency standards** – trusted for current SOPs and their versions. Trap: client knowledge never flows into standards automatically.

## SOP
**Maintenance (daily and quarterly)**
1. Daily, the staleness job lists facts past `review_by`. For each, call `get_context` for its area, check the current source (platform, client, document) and either confirm it or `propose_fact` an updated version citing the new source. If the source cannot be checked, `flag_insight` and mark dependent work at risk.
2. After each decision closes, write the decision-log entry: the question, evidence, options, choice, approver, scope, tasks, result with baseline, window and method, and what the team believes now. Label it observed change unless there was a holdout.
3. Propose a learning only when its result window has closed and its method is stated. A learning stays client-specific until promoted.
4. **Promotion to an agency standard:** a client learning becomes a standard only through a reviewed promotion. Draft it with every client name, number, offer, product and quote removed, state the general rule and the conditions it held under, and submit it for org-admin review. Agents never publish standards.
5. **SOP updates:** when a task repeatedly fails the same checklist item, or a client exception keeps recurring, draft a proposed skill or SOP change with the evidence count and send it to the skill's reviewer.
6. Quarterly, run the access review: every connection's scope, grantor, last use, and whether it is still needed. Flag unused or write-scoped access for revocation.

**Offboarding (on notice of end)**
7. Confirm `contract_end_terms` with the account lead. Create internal tasks for each checklist item with owners and dates.
8. List outstanding commitments: open tasks, pending approvals, scheduled campaigns, live tests and experiments, flows in draft, client inputs owed. For each, propose finish, hand over or cancel; scheduled sends and live flows after the end date need the client's explicit decision.
9. Build the asset handoff: inventory, location, format, and handoff method per item, following contract ownership. Export the final reports and the decision log for the client.
10. Draft the client offboarding message and handoff summary; `request_approval` with `client.message.send`.
11. **Key revocation:** every API key and integration the agency used is revoked on the platform and removed from the vault, and every agency seat is removed. A person with `connection.manage` does this; confirm each key no longer works and record the date. A key the client wants kept alive is recorded with its owner and that decision.
12. Archive per policy, delete scratch copies and any remote work packets, and set the workspace to archived. `save_output` the offboarding record (kind `report`) and `finish`.

## Decision rules
- If a fact is past its review date and drives a live message, offer or segment, then flag it high and hold new work that depends on it.
- If a fact's new source contradicts the approved fact, then `propose_fact` the new version and flag the conflict; never overwrite silently.
- If a learning includes any client name, number, offer, product or quote, then it cannot be promoted; strip it or keep it client-only.
- If a learning comes from one client and one test, then promote it only as a hypothesis for testing elsewhere, not as a rule.
- If a result window has not closed, then do not log it as learned.
- If a scheduled send or live test would run after the end date, then the client decides in writing to keep, hand over or stop it; changing it needs `campaign.publish` or `flow.change_live` approval.
- If any key or seat cannot be confirmed revoked, then the offboarding is not complete; flag it critical.
- If the client disputes asset ownership, then stop the handoff for that item and escalate to the account lead.

## Examples
### Good
"Promotion draft (for org-admin review). Observed at one client: a plain-text recovery email from a named person outperformed the designed version on revenue per recipient in a randomized split, window closed, sample stated in the client record. General rule proposed: test a plain-text variant in recovery flows before investing in design. Status: hypothesis, one account. No client data included."
### Not good
"Add to standards: brand X's 15% offer converts best, use it for everyone." It copies a client offer into a standard, generalizes from one account, has no method and skips review.

## Quality checklist
- [ ] Every reviewed fact is confirmed or re-proposed with a dated source; none is silently changed.
- [ ] Each decision-log entry links evidence, approver, tasks and a result with window and method.
- [ ] Learnings are only logged after their window closed and are labelled observed or controlled.
- [ ] Promotion drafts contain no client name, number, offer, product or quote.
- [ ] The access review lists every connection with scope, grantor and keep/revoke.
- [ ] The offboarding checklist shows every outstanding commitment with a finish, hand over or cancel decision.
- [ ] Every key and seat shows a confirmed revocation date, or an owner who decided to keep it.
- [ ] The archive list states what is kept, where and until when, and what was deleted.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `job.run` | autonomous | Staleness job |
| `insight.create` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `task.manage` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent or client decider; stays proposed |
| `result.record` | autonomous | Analyst agent after the window closes |
| `standard.publish` | autonomous | Org admin only (human); agents never publish |
| `connection.manage` | autonomous | Org admin or account lead only (human); key revocation |
| `fact.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `client.message.send` | explicit_approval | Account lead or org admin |
| `campaign.publish` | explicit_approval | Account lead, org admin or client owner/approver |
| `flow.change_live` | explicit_approval | Account lead, org admin or client owner/approver |

## Outputs and destination
- **Fact review log** (other): fact, old value reference, new source, outcome. Task output.
- **Decision-log entries** (analysis): one per closed decision. Client knowledge.
- **Promotion drafts** (draft_spec): client-free proposed standards. To org admin.
- **Access review** (report): quarterly, internal.
- **Offboarding record** (report): commitments, asset handoff, revocations with dates, archive list. Internal, with a client-facing handoff summary (copy) via `request_approval`.
Wavy starting defaults: fact review every 90 days, or 30 days for offers, stock and dates; access review quarterly; revoke audit keys within 24 hours of delivery and all keys by the contract end date; keep the archive (reports, decision log, approvals, aggregate evidence) for the contract's retention term, delete scratch copies and personal data at offboarding.

## Maintenance
The account lead reviews this skill every 90 days with the org admin. Review early after any offboarding, an access incident, a promotion that leaked client detail, or a stale fact that caused wrong work.
