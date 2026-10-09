---
key: area.experimentation
kind: area
area: experimentation
title: Experimentation and learning
owner_role: analyst
version: 2
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Experimentation is how Wavy learns what actually changes customer behavior for this client, instead of guessing from attributed revenue. A good test answers one decision with a fixed plan, protects the audience with guardrails and ends in a learning someone can reuse. A founder should care because a false "winner" rolled out to the whole list costs more than no test, and only a controlled comparison can show incremental impact.

## Client facts and rules
Call `get_context` for `experimentation`, `reporting` and `economics`. If a fact is missing, `flag_insight` naming the test it blocks and `propose_fact` only with a written source. Don't design around a guess.

- `holdout_policy` – whether the client agreed to customer-level holdouts, the share and for which programs. Missing: no holdout test; propose the policy to the account lead.
- `test_approver` – who on the client side signs off tests and rollouts. Missing: the test can't launch.
- `order_metric` – the pinned eligible-order source, identity join and event-time definition. Missing: block store-order outcomes.
- `attribution_settings` – required only for an attributed metric or contextual ESP reporting. Missing attribution settings do not block a randomized store-order outcome measured on the registered assignment horizon.
- `account_timezone` – for enrollment and outcome boundaries. Missing: stop.
- `contribution_margin` – for value-based primary metrics, offer tests and the minimum worthwhile effect. Missing: label value as gross; no offer test ranks on profit; the minimum worthwhile effect is set and labelled on a gross basis.
- `approved_offers` and `discount_rules` – what offers a test arm may use. Missing: no offer arm.
- `frequency_cap` – maximum messages per customer per period. Missing: an extra-send arm can't be designed.
- `learning_<slug>` – approved learnings from earlier tests. Missing: none exist; don't cite remembered results.

Common exceptions: subscription customers and service-recovery cases are usually excluded from promotional arms; marketplace-only buyers may lack consent.

## Inputs and sources
- **Order-level export** – trusted for conversions and revenue per assigned customer, and for the per-customer revenue variance that revenue sizing needs. Traps: duplicate order IDs; one order credited to several messages.
- **ESP campaign and flow reports** – trusted for deliveries, clicks, unsubscribes, complaints by arm. Traps: attribution windows overlap across arms; the ESP's auto-winner often picks on opens, which machine opens inflate.
- **Assignment records** – every customer randomized, their arm and assignment timestamp. Trap: without them, a holdout can't be audited, the sample-ratio check can't run and the test is observational.
- **Earlier learning log entries** – trusted only as approved facts, with their population and date.

## SOP
1. `get_context` for the three areas. List facts as approved, proposed or missing. Read `task.phase`: a `plan` task stops at step 9; a `monitor` task runs step 11 only; a `readout` task starts at step 12 from the approved plan. Arm builds and launch are separate `campaign_production` or `flow_architecture` tasks, not phases of this area. First classify the work: a deterministic repair (Decision rules) is not planned as an optimization test.
2. Write the hypothesis: "Because [evidence ref], we believe [one change] for [eligible audience] will move [primary metric] by at least [minimum worthwhile effect] within [outcome horizon H from each customer's assignment], without [guardrails] crossing [limits]."
3. Prioritize with value ÷ effort. Value = eligible cohort × plausible effect × value per conversion, counting each customer once, low and base. Drop any test whose arms can't be filled at the planned allocation inside the enrollment period; ship it as a judgment call instead and label it untested.
4. Design: randomize at customer level with stable assignment that persists across sends. Fix eligibility before assignment. One change per arm. Keep the test mutually exclusive with other live tests on the same audience, or prespecify the interaction analysis. For flows and programs, use a persistent customer holdout.
5. Size the actual design. Keep two effects distinct: the minimum worthwhile effect (MWE) is the smallest effect that justifies rollout cost and risk, set from `economics`; the power MDE δ is the effect this design has the stated power to detect. Require δ ≤ MWE, or the test cannot answer the rollout question. For a binary primary outcome, state baseline p, absolute δ, two-sided alpha, power and control share h; compute BOTH arm sizes for that allocation. Equal-arm screening uses n per arm ≈ 16 × p × (1 − p) ÷ δ² at 80% power and alpha .05. For unequal allocation, a small-effect total-N screening approximation is N ≈ 7.85 × p × (1 − p) ÷ (δ² × h × (1 − h)); confirm with the approved power implementation. Revenue or contribution outcomes need their own variance or a simulation, not the binary formula.
6. Pick one primary metric before launch: placed-order rate or revenue per assigned customer (assigned, not just delivered, so holdout comparisons are fair); contribution per eligible customer for retention tests. Clicks are a labelled proxy only when orders can't reach the planned sample.
7. Pick guardrails: unsubscribe, spam complaint, bounce, SMS opt-out, discount cost per order, refund or cancellation rate.
8. Define enrollment start and end separately from each assigned customer's fixed outcome horizon H. Earliest final read = last assignment + H + the declared data-latency buffer. A build-completion timestamp is not exposure; if the result scheduler only starts there, mark scheduling unsupported and require registered launch and assignment dates.
9. Save the plan (`save_output`, kind `brief`) with arms, allocation, both arm sizes, enrollment dates, H, earliest final read, analysis method, decision rule and the decision proposal: insight refs, metric, `window_days` (covering enrollment + H + buffer from the registered launch date), baseline and plan scope (`audience.prepare`, `flow.build_draft` or `campaign.build_draft`). `finish`.
10. After approval, arm builds and launch are delegated to separate `campaign_production` or `flow_architecture` tasks that cite the approved plan version; they use `build_draft`, hand segments and holdout audiences to an authorized executor under `audience.prepare`, and request `campaign.publish` or `flow.change_live`. This area does not build. Launch and assignment are confirmed only by the receipts those tasks record.
11. `monitor` phase, one execution per fixed checkpoint: check guardrails and assignment health only; don't read the primary metric. Save the checkpoint note (`save_output`, kind `analysis`) and `finish`.
12. At or after the earliest final read: run the sample-ratio-mismatch test, deduplicate order IDs within each customer's outcome, analyze everyone randomized in their assigned arm, compute the absolute and relative effect with a 95% interval by the planned method, and review guardrails.
13. Save the readout (`save_output`, kind `analysis`) with the observed value or an inconclusive verdict and notes, ready to record under `result.record` once the window has closed. `propose_fact` the learning log entry as `learning_<slug>`. Draft the rollout recommendation. `finish`.

## Decision rules
- If both arms can't reach their planned sizes at the planned allocation inside the enrollment period, then don't run it as a test.
- Test observed assignment counts against the planned allocation with a sample-ratio-mismatch test. Report observed/expected counts, method and p-value. Wavy investigation default: p < .001. Missing assignment records or an unexplained mismatch blocks a causal verdict.
- Conclusive only when the planned sample was reached, the final read date has passed, the ratio check passed and the 95% interval excludes zero. If the estimate also meets the prespecified minimum worthwhile effect in the good direction (the rollout bar, not the power MDE) and no guardrail is breached, then save a rollout recommendation for the strategist to propose as a decision (a person approves it under `decision.approve`), keeping a persistent holdout for flows. No fixed conversion count substitutes for this rule.
- If the interval includes zero, report inconclusive evidence of a difference. If its upper bound is below the prespecified minimum worthwhile benefit, say that benefit was ruled out at the stated interval level, not "no effect". Claim practical equivalence only under a prespecified equivalence test and bounds that rule out both meaningful benefit and meaningful harm. Then keep the cheaper or lower-risk arm, or re-test with a larger sample.
- **Repair versus optimization:** a deterministic repair (broken link, failing offer or code, consent or disclosure defect, dormant trigger, missing purchase exit) is fixed for everyone and verified with receipts; it is not an optimization experiment, and the broken path is never left live as a control or holdout. A holdout or split is for comparing working, compliant alternatives, including a compliant no-message arm.
- If a guardrail crosses its limit at a checkpoint, then stop the test (request `flow.change_live` or cancel the scheduled send through the account lead) and `flag_insight` severity high. Stopping for harm is allowed; stopping for an early win is not.
- Deduplicate order IDs within each customer's outcome. Preserve outcomes for everyone randomized. Don't remove or assign away outcomes to make overlapping tests additive. Prespecify interactions or use a program-level comparison; never sum separate flow tests as program lift.
- If a test needs a new offer, then `request_approval` with `discount.change` before building.
- If a stakeholder asks for interim results, then share guardrails only and the scheduled read date.
- If a learning came from one client, then it stays a client fact. Agency standards change only through reviewed promotion with client data removed.

## Examples
### Good
"Hypothesis: because the checkout flow's second message gets 70% of its clicks from mobile and lands on the cart, linking to a rebuilt cart will raise placed orders per assigned customer by at least 0.5 pp within H = 21 days of assignment, without unsubscribes above 0.3%. Minimum worthwhile effect +0.5 pp (from the economics sheet); power MDE set equal to it. Customer-level 50/50 split, stable assignment, baseline 4.0%: 16 × 0.04 × 0.96 ÷ 0.005² ≈ 24,600 per arm; at current entry volume enrollment takes 19 days. Earliest final read: last assignment + 21 days + 5-day buffer. (A 90/10 holdout at the same p and δ would need N ≈ 7.85 × 0.0384 ÷ (0.005² × 0.09) ≈ 134,000, about 13,400 in control.) Readout: assigned 24,710 / 24,655, ratio check p = .80. Result: +0.3 pp, 95% interval −0.1 to +0.7. Inconclusive: the interval includes zero, and the +0.5 pp benefit is not ruled out (upper bound +0.7). Not "no effect" and not equivalence. Both links work, so this was an optimization test; keep the control link (lower effort) and re-test only with a bigger change."
### Not good
"Subject B won (open rate 48% vs 41%) after the first two hours, so we rolled it out. Revenue up 20% this week." It picks on machine-inflated opens, peeks, has no sample plan, ratio check or guardrails, and reads an unrelated revenue change as the test effect.

## Quality checklist
- [ ] The output states its `task.phase` (plan, monitor or readout); a plan contains no result, interval or verdict, a monitor note contains guardrails only, a readout cites the approved plan version, and items for other phases are marked not applicable with that reason. Builds and launch are cited from production-task receipts, never done here.
- [ ] Plan: the work is classified as an optimization test, not a deterministic repair; the hypothesis names evidence, one change, eligible audience, primary metric, minimum worthwhile effect, outcome horizon H and guardrails.
- [ ] Plan: randomization unit, stable assignment, eligibility fixed before assignment and overlap handling (mutually exclusive or prespecified interaction) are stated.
- [ ] Plan: both arm sizes are computed for the actual allocation from a stated baseline, alpha and power, with power MDE ≤ minimum worthwhile effect; a revenue or contribution outcome uses its own variance or simulation.
- [ ] Plan: exactly one primary metric per assigned customer is fixed, and enrollment dates, H, data-latency buffer and earliest final read are anchored to assignment, not build completion.
- [ ] Readout: the read happens on or after last assignment + H + buffer, with assigned n per arm, a sample-ratio-mismatch test (observed/expected, method, p-value) and guardrails.
- [ ] Readout: everyone randomized is analyzed in the assigned arm, order IDs are deduplicated within each customer's outcome, and no outcome is removed to make tests additive.
- [ ] Readout: absolute and relative effect with a 95% interval are shown; inconclusive results are labelled inconclusive, a ruled-out benefit is stated as such, and neither is called "no effect" or equivalence without a prespecified equivalence test.
- [ ] Readout: the learning log entry is proposed as a fact and the rollout is a recommendation, not an approved rule.

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
| `audience.prepare` | approved_plan | Covered by an approved decision that lists it; new or detached audiences only |
| `campaign.build_draft` | approved_plan | Covered by an approved decision that lists it |
| `flow.build_draft` | approved_plan | Covered by an approved decision that lists it |
| `decision.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `campaign.publish` | explicit_approval | Account lead, org admin or client owner/approver |
| `flow.change_live` | explicit_approval | Account lead, org admin or client owner/approver |
| `discount.change` | explicit_approval | Org admin or client owner/approver (locked) |

## Outputs and destination
- **Test plan** (brief, phase `plan`): everything listed in SOP step 9. Task output; becomes a decision once approved.
- **Checkpoint note** (analysis, phase `monitor`): guardrails and assignment health at a fixed checkpoint; no primary metric. Task output.
- **Test readout** (analysis, phase `readout`): setup, assigned n per arm, ratio check, effect with interval, guardrails, verdict, rollout recommendation. Task output; summarized in the monthly report's learning section.
- **Learning log entry** (other): test, dates, population, verdict, effect, what to do next. Proposed client fact.

Thresholds. Published statistical approximations (80% power, two-sided 5%, binary outcomes): the equal-arm (Lehr) and unequal-allocation formulas in SOP step 5. They are screening values; confirm with the approved power implementation. Wavy starting defaults: 10% persistent customer-level holdout for flows and programs, sized for the 10% arm; 5-day data-latency buffer; sample-ratio-mismatch investigation at p < .001 (a Wavy alert, not a universal rule); guardrail limits at the Klaviyo "healthy" band edges used in `reporting`.

## Maintenance
The lead analyst reviews this skill every 90 days. Review early after any rollout that later reversed, any test invalidated by assignment errors, or a change to ESP testing or attribution features.
