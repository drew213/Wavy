---
key: area.quarterly_planning
kind: area
area: quarterly_planning
title: Quarterly planning and growth roadmap
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Quarterly planning decides which few retention initiatives deserve the next 90 days, what each could be worth, what must be true first, and who does what by when. The quarterly business review (QBR) reports what the last plan delivered. A founder should care because a ranked, gated plan stops the team from spreading effort across ideas that can't pay back, and an honest range beats a promised number that a missing dependency quietly turns to zero.

## Client facts and rules
Call `get_context` for `quarterly_planning`, `economics`, `reporting` and `journey`. If a fact is missing, `flag_insight` naming the initiative it blocks and `propose_fact` only with a written source. Never size with invented inputs.

- `business_goals` – the quarter's goal with metric, number and date. Missing: plan to ranked opportunities and print "no numeric goal".
- `revenue_target` and `target_basis` – attributed or store revenue. Missing: no gap-to-target math. Attributed targets sit inside store revenue; never add them.
- `contribution_margin` – for valuing levers. Missing: value in gross revenue, labelled gross on every figure.
- `order_metric` and `account_timezone` – for AOV, baselines and quarter boundaries. Missing: stop.
- `plan_window` – quarter start and end dates. Missing: use the calendar quarter, labelled.
- `wavy_capacity` – build-days per month available beyond ordinary campaign production. Missing: rank by value only and flag that the plan isn't capacity-checked.
- `client_capacity` – design, developer, approval and operations bandwidth. Missing: every client-dependent item shows "capacity unconfirmed".
- `promo_calendar` – peaks, launches and freeze windows. Missing: no launches scheduled inside likely peak weeks.
- `approved_offers` and `inventory_constraints` – what levers may promise and what can ship. Missing: offer and volume levers are gated.
- `holdout_policy` – whether incremental measurement is possible. Missing: results will be observational; say so in the plan.

## Inputs and sources
- **Last quarter's roadmap, decisions and results** – trusted for what was committed and what was measured. Trap: a task done isn't a result measured.
- **Audit scorecard and re-audit delta** – trusted for gaps by area. Trap: compare only on the same check library version and definitions.
- **Order-level history and cohorts** – trusted for baselines, seasonality and repeat curves. Traps: immature cohorts; send-date versus event-date totals differ and the difference isn't growth.
- **Current consented, deduplicated audience counts** – the only valid eligible pool. Trap: list size, historical buyers and stored addresses aren't sendable reach.
- **Learning log** – approved test results. Trap: a learning from another population doesn't transfer unchanged.

## SOP
1. `get_context` for the four areas. List facts as approved, proposed or missing.
2. Goal review: last quarter's goal against the actual on the same basis, timezone and definitions, plus what was committed, done, measured and still pending. Separate what Wavy changed from season, offers and acquisition mix.
3. Lifecycle maturity: score each stage (capture, welcome, browse and cart recovery, post-purchase onboarding, second order and replenishment, winback, loyalty, SMS, deliverability, measurement) on the scale below, each with an evidence reference.
4. Opportunity list: every failed or weak stage, open insight and approved learning becomes a candidate lever. Put broken promises, broken links, missing events and consent problems first.
5. Size each lever: eligible pool × expected rate change × value per conversion × months. Write each input with its source. Low, base and high are separate assumptions, not ±20%. Rate changes come from the account's own history first, then a stated Wavy default. Months are the run time left after the build. Subtract ordinary revenue the lever displaces.
6. Remove double counting: assign each customer and order pool to one lever. One order is never a winback win, a campaign win and a replenishment win.
7. Dependencies: for each lever, list its gates (DNS and authentication proof, consent census, event tests, offer approval, margin, inventory, client build). Each gate gets an owner and a due date. If a gate fails, the lever goes to zero.
8. Resource allocation: estimate build-days per lever, rank by base value ÷ build-days, and fill `wavy_capacity` after reserving ordinary production. Mark client-dependent work against `client_capacity`.
9. Strategic initiatives: group the top levers into three to five initiatives, each with goal, levers, success metric, measurement window and owner.
10. 30/60/90-day roadmap: days 1–30 foundations and gates plus quick fixes; 31–60 core builds; 61–90 optimization, test reads and the reforecast. Each row: initiative, owner, start, due, dependency, success measure.
11. Draft one decision proposal per initiative: insight refs, metric, `window_days`, baseline and plan scope (only approved_plan actions). The account lead files them with `decision.propose`.
12. QBR: goal review, maturity change, learnings with verdicts, sized plan, roadmap and decisions needed. `save_output` (kind `analysis` for the sizing, `report` for the QBR). Sending it is `request_approval` with `client.message.send`. `finish`.

## Decision rules
- If a lever's eligible pool can't be counted from current consent with suppressions and overlaps removed, then don't size it. Mark it "pool unverified".
- If a lever depends on a gate with no owner or date, then it can't enter the 30-day plan.
- If a gate fails at a checkpoint, then set that lever to zero and reforecast. Don't push extra volume elsewhere to cover the gap.
- If foundations (sending authentication, canonical events, consent, broken promises) are failing, then they come before any expansion, whatever their sized value.
- If total build-days exceed capacity, then cut from the bottom of the value ÷ effort ranking. Don't compress estimates.
- If a lever needs a new offer, then gate it on `discount.change` approval and margin.
- If the target exceeds the high case of all gated levers combined, then say so in the QBR and ask the client to confirm or revise the target.
- If no holdout is possible, then the plan's success measures are observed change. Don't promise incremental revenue.
- If a launch falls inside a freeze window or peak week, then move it or get an explicit client decision.

## Examples
### Good
"Lever: second-order journey for one-time DTC buyers aged 30–90 days. Pool: current email-consented, deduplicated count from the census dated the 3rd, excluding active subscribers. Rate change: +0.5 / +1.0 / +2.0 pp on the 60-day second-order rate (low a Wavy default, base from the account's own comparable journey, high from an approved learning). Value: gross AOV from the pinned order metric. Months: 2 after a 3-week build. Gates: product data backfill (client, the 16th), consent refresh (Wavy, the 9th). Overlap: excludes winback pool. Build: 6 days; ranked 2nd by value ÷ effort."
### Not good
"Q4 plan: add $500k by launching 8 new flows and daily SMS. Floor $120k." It sizes from list size, has no inputs, gates or capacity check, adds levers that share customers, and calls a scenario a guaranteed floor.

## Quality checklist
- [ ] Goal review compares target and actual on the same basis, timezone and definitions.
- [ ] Every stage has a maturity score with an evidence reference.
- [ ] Every sized lever shows pool, rate change, value and months with sources, as separate low, base and high.
- [ ] No customer or order pool is assigned to more than one lever.
- [ ] Every lever lists its gates with owner and due date, and states that a failed gate sets it to zero.
- [ ] Levers are ranked by value ÷ build-days and the plan fits stated capacity.
- [ ] Gross and contribution are labelled on every figure; attributed targets are not added to store revenue.
- [ ] Each roadmap row has owner, start, due, dependency and success measure across 30/60/90 days.
- [ ] No figure is described as guaranteed, a floor, or incremental without a holdout.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `analysis.run` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `task.manage` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent; stays proposed |
| `decision.propose` | autonomous | Staff or agent, logged |
| `decision.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `discount.change` | explicit_approval | Org admin or client owner/approver (locked) |
| `client.message.send` | explicit_approval | Account lead or org admin |

## Outputs and destination
- **Opportunity sizing table** (analysis): lever, pool, assumptions, low/base/high, overlap assignment, gates, build-days, rank. Task output.
- **Lifecycle maturity assessment** (analysis): stage, score, evidence, gap. Task output; feeds `journey`.
- **30/60/90-day roadmap and decision proposals** (brief): initiatives with owners, dates, dependencies and measurement. Decisions after approval.
- **QBR deck** (report): goal review, maturity, learnings, plan, decisions needed. Client Hub and client message after approval.

Thresholds are Wavy starting defaults: maturity scale 0 absent, 1 live but generic, 2 segmented by customer state, 3 tested with a holdout or controlled test; reserve at least 20% of Wavy capacity for ordinary production and fixes; reforecast at day 30 and day 60; re-audit at 30, 60 and 90 days on the same definitions; at most five initiatives per quarter.

## Maintenance
The lead strategist reviews this skill every 90 days, after each QBR cycle. Review early when a quarter's result misses the low case, when a gated lever failed late, or when the sizing method changes.
