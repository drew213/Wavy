---
key: area.flow_audit
kind: area
area: flow_audit
title: Flow audit
owner_role: analyst
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Find out, read-only and with evidence, whether the client's automated flows are live, triggered on the right events, filtered correctly, timed to customer behavior, and delivering what they promise. The audit ends in a ranked list of repairs, each with an owner and release proof. The founder cares because a broken welcome or a checkout flow on a dormant metric loses money every day, silently.

## Client facts and rules
Look up each with `get_context`.

- `canonical_order_metric` – the pinned Placed Order metric and any excluded candidates. Missing: stop. Score DAT-01 first; no revenue number until it passes.
- `store_platform`, `past_store_platforms` – needed to spot dormant integrations. Missing: stop.
- `account_timezone` – for monthly buckets (DAT-04). Missing: `propose_fact` from account settings.
- `messaging_tools`, `sms_provider` – every system that can contact customers. Missing: report SMS coverage as unknown, not absent.
- `subscription_platform` – and which service messages it sends natively. Missing: RET-03 and RET-04 are unknown.
- `approved_welcome_offer`, `offer_stacking_rules` – what flows should promise. Missing: CRV-05 is unknown; flag it.
- `current_domain` – the live store domain for link checks. Missing: `propose_fact` from the store settings.
- `regulated_category_limits` and `approved_claims` – what content may say. Missing: flag claim language without judging it compliant.
- `audit_track` – snapshot or deep. Missing: ask the account lead.

The audit is read-only. No flow edits, sends, list changes or setting changes, even if a fix is obvious.

## Inputs and sources
- **Flow inventory**: query live, draft, manual and archived flows explicitly. A default listing can omit archived records.
- **Flow definitions**: triggers, trigger filters, profile filters, splits, delays, actions, message status. Trusted for today's configuration only; they don't show past states.
- **Message-level reporting**: send-date basis. Trusted for exposure and attributed revenue per message. Trap: summed monthly uniques are not deduplicated people.
- **Order-event attribution**: event-time basis. Never add to send-date figures.
- **Metric event counts**: prove a trigger metric is receiving events (DAT-02).
- **Template HTML**: trusted for size (CRV-01) and image share (CRV-02). Not a received message.
- **Received messages and link crawls**: trusted for what customers see, links (CRV-03), UTMs (CRV-04) and offers (CRV-05).

## SOP
1. `get_context`. Confirm the facts above. Score DAT-01, DAT-02 and DAT-05 first; if any fails, stop revenue scoring and `flag_insight`.
2. **Inventory.** List every flow with status, trigger, channel and message counts. Count live, draft and manual messages separately.
3. **Classify.** Apply the check library flow classifier. A flow is a live flow of type X only when it is live, not archived, triggers on X, has a live send action, and had recipients in the last 90 days. Custom-metric or name-only matches need human confirmation.
4. **Trigger validation (DAT-02).** For each live flow, confirm its trigger metric had events in each of the last 2 full months and comes from the current integration. Check profile filters that reference order metrics too.
5. **Coverage.** Score FLW-01 welcome, FLW-02 checkout, FLW-03 cart, FLW-04 browse, FLW-05 post-purchase, FLW-06 winback, FLW-07 sunset, RET-03 and RET-04 subscription, SMS-02 and SMS-05 where SMS exists. Note journeys covered by another platform as covered externally, with evidence.
6. **Filters and exclusions.** Score FLW-09 (intent flows exit on canonical purchase) and FLW-10 (Smart Sending, no transactional flag on marketing, no drafts in live core flows). Check cart excludes checkout starters and browse has a re-entry limit.
7. **Timing.** Compare post-purchase, replenishment and winback delays with the account's p25 and median repeat gaps (RET-02, FLW-06).
8. **Message performance.** Per message: delivered, click rate, placed-order rate, revenue per recipient, unsubscribe and complaint rate. Keep historical revenue from messages now in draft, and from flow IDs missing from today's inventory, with a "history" label.
9. **Offer logic.** Read every offer stated in flows. Redeem each at checkout. Score CRV-05.
10. **Content quality.** Review received renders: brand name, current products, claims, mobile and dark mode, image-only (CRV-02), clipping (CRV-01).
11. **Links.** Crawl every link in live flows. Score CRV-03 and CRV-04.
12. **Score and rank** (Decision rules). Size failed and warned checks with each check's impact method.
13. `save_output` the scorecard (analysis) and recommendations (report). `flag_insight` every critical fail. A separate QA reviewer runs `record_check` on recomputes. `finish`.

## Decision rules
- **Verdicts:** pass, warn, fail, n/a (precondition absent, with reason), or unknown (data unavailable). Unknown is never pass or fail, and missing data is never zero.
- **Area status (Wavy starting default):** Fail if any critical check fails; Warn if any high check fails or any critical check warns; otherwise Pass. Show counts by severity next to it. No hidden weighting.
- **Purpose-aware warnings:** a missing purchase exit is a defect in welcome, browse, cart and checkout. It is not automatically a defect in post-purchase education, service messages or consent-sync webhooks.
- Keep these states separate: confirmed defect, configuration risk, test opportunity, not applicable, unknown.
- Potential overlap between two flows' eligibility is a configuration risk. Confirmed duplicate delivery needs received-message or send-record evidence.
- If a later welcome email earns little, don't call it worthless. Purchase exits and earlier steps change who reaches it. Propose a test, not a deletion.
- Revenue per recipient across flow positions is descriptive, not a causal ranking.
- If a flow named for one channel sends on another, report it by what it actually sends.
- **Prioritize repairs in this order:** broken promises and offers, dormant triggers, broken links, missing purchase exits, service routing, missing core flows, timing, then content and test ideas. Within a tier, rank by eligible reach × severity ÷ effort.
- Every recommendation names the affected flow and message ID, the evidence, the fix, owner, effort, dependency, release proof and success measure.
- Escalate to the account lead before reporting anything that implies another agency, team member or vendor is at fault. Name prefixes and tracking footprints prove nothing about ownership.

### Thresholds
All are **Wavy starting defaults** from the check library: welcome live with at least 3 emails sending in 90 days (FLW-01); checkout live with at least 2 emails and coverage of at least 25% of checkout starters (FLW-02); flows at least 35% of email-attributed revenue (FLW-08); Smart Sending on at least 90% of marketing flow emails (FLW-10); winback timed at 1.5–2× the median repeat gap (FLW-06); no template over 102,400 bytes, the size where Gmail clips (CRV-01); at least 90% of links tagged (CRV-04). Recalibrate after 5 or more audits; when a value is close to a threshold, say so.

## Examples
### Good
"FLW-02 fail (critical path). The live checkout flow triggers on a Started Checkout metric with zero events in the last 2 full months; the current integration's metric has steady events. Evidence: metric event counts and the flow trigger ID. Fix: clone the flow onto the canonical metric, keep filters and content, test with a real checkout, then turn off the old version. Owner: Wavy build. Release proof: test profile enters and receives message 1. Measure: checkout flow recipients per checkout starter."
### Not good
"Checkout flow revenue is low; rewrite the emails." It fails because it diagnoses creative before checking the trigger, cites no message or metric, and gives no release proof.

## Quality checklist
- [ ] DAT-01, DAT-02 and DAT-05 are scored before any revenue finding.
- [ ] The inventory includes archived flows and counts live and draft messages separately.
- [ ] Every scored check has a verdict, a value and an evidence path; unknown and n/a carry reasons.
- [ ] Coverage claims about SMS or subscription service state the platform checked, or say unknown.
- [ ] Offer and link findings come from checkout tests and link crawls, not templates.
- [ ] Send-date and event-time figures are never added together, and each states its basis and window.
- [ ] Every recommendation has flow and message ID, fix, owner, dependency, release proof and measure.
- [ ] No account change was made during the audit.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| issue.flag | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed |
| decision.propose | autonomous | Agent (logged) |
| task.manage | autonomous | Agent (logged) |
| flow.change_live | explicit_approval | Org admin, account lead, or client owner/approver |
| client.message.send | explicit_approval | Org admin or account lead |

## Outputs and destination
- **analysis**: the scorecard. One row per check ID with value, verdict, severity, evidence path and fix, plus the flow inventory. Goes to task output for QA.
- **report**: ranked recommendations with sized ranges, gates and "what we couldn't see". Goes to the Client Hub after independent QA and account lead approval.
- **other**: new-check candidates for findings no check covers. Goes to the check library owner.

## Maintenance
The analyst lead reviews this skill every 90 days and whenever the check library version changes. Review early when the ESP changes flow, filter or reporting behavior, or when QA recomputes fail on the same step twice.
