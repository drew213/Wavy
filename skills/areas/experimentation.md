---
key: area.experimentation
kind: area
area: experimentation
title: Experimentation and learning
owner_role: analyst
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Experimentation is how Wavy learns what actually changes customer behavior for this client, instead of guessing from attributed revenue. A good test answers one decision with a fixed plan, protects the audience with guardrails and ends in a learning someone can reuse. A founder should care because a false "winner" rolled out to the whole list costs more than no test, and only a controlled comparison can show incremental impact.

## Client facts and rules
Call `get_context` for `experimentation`, `reporting` and `economics`. If a fact is missing, `flag_insight` naming the test it blocks and `propose_fact` only with a written source. Don't design around a guess.

- `holdout_policy` – whether the client agreed to customer-level holdouts, the share and for which programs. Missing: no holdout test; propose the policy to the account lead.
- `test_approver` – who on the client side signs off tests and rollouts. Missing: the test can't launch.
- `order_metric` and `attribution_settings` – the pinned order metric and attribution windows. Missing: stop; outcomes can't be measured.
- `account_timezone` – for start and stop boundaries. Missing: stop.
- `contribution_margin` – for value-based primary metrics and offer tests. Missing: label value as gross; no offer test ranks on profit.
- `approved_offers` and `discount_rules` – what offers a test arm may use. Missing: no offer arm.
- `frequency_cap` – maximum messages per customer per period. Missing: an extra-send arm can't be designed.
- `learning_<slug>` – approved learnings from earlier tests. Missing: none exist; don't cite remembered results.

Common exceptions: subscription customers and service-recovery cases are usually excluded from promotional arms; marketplace-only buyers may lack consent.

## Inputs and sources
- **Order-level export** – trusted for conversions per assigned customer. Traps: duplicate order IDs; one order credited to several messages.
- **ESP campaign and flow reports** – trusted for deliveries, clicks, unsubscribes, complaints by arm. Traps: attribution windows overlap across arms; the ESP's auto-winner often picks on opens, which machine opens inflate.
- **Assignment records** – the list of customers in each arm with assignment date. Trap: without them, a holdout can't be audited and the test is observational.
- **Earlier learning log entries** – trusted only as approved facts, with their population and date.

## SOP
1. `get_context` for the three areas. List facts as approved, proposed or missing.
2. Write the hypothesis: "Because [evidence ref], we believe [one change] for [eligible audience] will move [primary metric] by at least [minimum detectable effect] within [window], without [guardrails] crossing [limits]."
3. Prioritize with value ÷ effort. Value = eligible pool × plausible effect × value per conversion over the plan window, low and base. Drop any test that can't reach its sample size inside the window; ship it as a judgment call instead and label it untested.
4. Design: randomize at customer level with stable assignment that persists across sends. One change per arm. Keep the test mutually exclusive with other live tests on the same audience, or declare the overlap. For flows and programs, use a persistent customer holdout.
5. Size it: compute sample per arm with the formula below from the baseline rate and the minimum detectable effect. Size the pool before holdout: pool = treated ÷ (1 − holdout share), rounded up, then allow for later exclusions.
6. Pick one primary metric before launch: placed-order rate or revenue per assigned customer (assigned, not just delivered, so holdout comparisons are fair); contribution per eligible customer for retention tests. Clicks are a labelled proxy only when orders can't reach the minimum.
7. Pick guardrails: unsubscribe, spam complaint, bounce, SMS opt-out, discount cost per order, refund or cancellation rate.
8. Set the evaluation window: run period + longest attribution window + late-event buffer, or the full maturity horizon for repeat outcomes. The result window opens when the build task completes, so `window_days` must also cover the gap to launch.
9. Save the plan (`save_output`, kind `brief`) with arms, assignment, n per arm, stop date, decision rule and the decision proposal: insight refs, metric, `window_days`, baseline and plan scope (`audience.prepare`, `flow.build_draft` or `campaign.build_draft`).
10. After approval: prepare the arms with `audience.prepare` and `build_draft`. Launch needs `request_approval` with `campaign.publish` or `flow.change_live`.
11. During the run, check guardrails only, at fixed checkpoints. Don't read the primary metric.
12. At the stop date and after the window closes: check that the arm split matches the plan, count each order once, compute the absolute and relative effect with a 95% interval, and review guardrails.
13. Save the readout (`save_output`, kind `analysis`) with the observed value or an inconclusive verdict and notes, ready to record under `result.record` once the window has closed. `propose_fact` the learning log entry as `learning_<slug>`. Draft the rollout recommendation. `finish`.

## Decision rules
- If the planned sample can't be reached inside the window, then don't run it as a test.
- If the arm split differs from the plan by more than 1 percentage point, or assignment records are missing, then the test is invalid: record it as inconclusive with the reason.
- If the 95% interval excludes zero in the good direction, the effect is at least the minimum detectable effect, and no guardrail is breached, then save a rollout recommendation for the strategist to propose as a decision (a person approves it under `decision.approve`), keeping a persistent holdout for flows.
- If the interval includes zero, then call it inconclusive. Don't call it "no effect" unless the interval also excludes the minimum detectable effect. Keep the cheaper or lower-risk arm, or re-test with a larger sample.
- If a guardrail crosses its limit at a checkpoint, then stop the test (request `flow.change_live` or cancel the scheduled send through the account lead) and `flag_insight` severity high. Stopping for harm is allowed; stopping for an early win is not.
- If one order could count in two tests or levers, then assign it to one before analysis. One order is never a campaign win, a flow win and a save at once.
- If a test needs a new offer, then `request_approval` with `discount.change` before building.
- If a stakeholder asks for interim results, then share guardrails only and the scheduled read date.
- If a learning came from one client, then it stays a client fact. Agency standards change only through reviewed promotion with client data removed.

## Examples
### Good
"Hypothesis: because the checkout flow's second message gets 70% of its clicks from mobile and lands on the cart, linking to a rebuilt cart will raise placed orders per assigned customer by at least 0.5 pp in 21 days, without unsubscribes above 0.3%. Customer-level 50/50 split, stable assignment, baseline 4.0%: 16 × 0.04 × 0.96 ÷ 0.005² ≈ 24,600 per arm; at current entry volume that takes 19 days, so the stop date is day 19 and the read is day 19 + attribution window + 5. Result: +0.3 pp, 95% interval −0.1 to +0.7. Inconclusive; keep the control link (lower effort) and re-test only with a bigger change."
### Not good
"Subject B won (open rate 48% vs 41%) after the first two hours, so we rolled it out. Revenue up 20% this week." It picks on machine-inflated opens, peeks, has no sample plan or guardrails, and reads an unrelated revenue change as the test effect.

## Quality checklist
- [ ] The hypothesis names evidence, one change, audience, primary metric, minimum detectable effect, window and guardrails.
- [ ] Randomization unit, stable assignment and overlap with other tests are stated.
- [ ] Sample per arm is computed from a stated baseline, and the pool before holdout is shown.
- [ ] Exactly one primary metric, fixed before launch, measured per assigned customer.
- [ ] The stop date and evaluation window are fixed in advance and cover attribution plus buffer.
- [ ] The result shows n per arm, absolute and relative effect, a 95% interval and guardrails.
- [ ] Inconclusive results are labelled inconclusive, not as wins or as "no effect".
- [ ] Orders are deduplicated, and none is counted for more than one test or lever.
- [ ] The learning log entry is proposed as a fact, not written as an approved rule.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `analysis.run` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent; stays proposed |
| `decision.propose` | autonomous | Staff or agent, logged |
| `result.record` | autonomous | Agent, once the measurement window has closed |
| `audience.prepare` | approved_plan | Covered by an approved decision that lists it |
| `campaign.build_draft` | approved_plan | Covered by an approved decision that lists it |
| `flow.build_draft` | approved_plan | Covered by an approved decision that lists it |
| `decision.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `campaign.publish` | explicit_approval | Account lead, org admin or client owner/approver |
| `flow.change_live` | explicit_approval | Account lead, org admin or client owner/approver |
| `discount.change` | explicit_approval | Org admin or client owner/approver (locked) |

## Outputs and destination
- **Test plan** (brief): hypothesis, arms, assignment, sample, metrics, window, decision rule, decision proposal. Task output; becomes a decision once approved.
- **Test readout** (analysis): setup, n per arm, effect with interval, guardrails, validity checks, verdict, rollout recommendation. Task output; summarized in the monthly report's learning section.
- **Learning log entry** (other): test, dates, population, verdict, effect, what to do next. Proposed client fact.

Thresholds. Published statistical rule (Lehr's approximation, 80% power, two-sided 5%): n per arm ≈ 16 × p × (1 − p) ÷ δ², where p is the baseline rate and δ the absolute minimum detectable effect. Wavy starting defaults: 10% persistent customer-level holdout for flows and programs; at least 20 primary conversions per arm before any read; 5-day late-event buffer; arm-split tolerance of 1 percentage point; guardrail limits at the Klaviyo "healthy" band edges used in `reporting`.

## Maintenance
The lead analyst reviews this skill every 90 days. Review early after any rollout that later reversed, any test invalidated by assignment errors, or a change to ESP testing or attribution features.
