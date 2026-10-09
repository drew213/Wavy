---
key: area.reporting
kind: area
area: reporting
title: Reporting and diagnosis
owner_role: analyst
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Reporting tells the client what happened, why it probably moved, whether buyers came back, what is at risk and what decision is needed next. Diagnosis turns a surprising number into a verified cause or an honest "not yet known". A founder should care because a wrong number repeated monthly becomes a wrong strategy, and attribution dressed up as lift wastes budget.

## Client facts and rules
Call `get_context` for `reporting`, `data_infrastructure` and `economics`. If a fact is missing, print "unavailable" where it would be used, `flag_insight` naming what it blocks, and `propose_fact` only with a written or system source. Never assume a platform default.

- `account_timezone` – the ESP account's reporting timezone. Missing: stop; month boundaries can't be set.
- `reporting_currency` – currency of every money figure. Missing: stop if the account has orders in more than one currency.
- `order_metric` – the pinned canonical placed-order metric and integration. Missing: stop; do not choose between candidate metrics yourself.
- `attribution_settings` – model, email and SMS windows, bot and machine-open filters. Missing: read them from the account and `propose_fact`; until approved, print "settings not verified".
- `revenue_target` and `target_basis` – the period target and whether it is attributed or store revenue. Missing: print "Target not agreed" and use a factual change headline.
- `commerce_sources` – DTC, marketplace and subscription sources in scope; a dormant duplicate integration stays excluded. Missing: report DTC only, labelled.
- `subscription_platform` – where renewals come from. Missing: renewals can't be split from discretionary repeat; say so.
- `sms_cost_basis` – per-segment or per-message price, fees and contract. Missing: SMS cost and margin are unavailable, not zero.
- `wavy_start_date` – when Wavy took over. Missing: don't label periods "before Wavy".
- `report_cadence` – delivery day, recipients and format. Missing: draft only; no delivery.

## Inputs and sources
- **ESP campaign and flow reports** – trusted for attributed revenue, deliveries, clicks, unsubscribes, bounces, complaints. Traps: grouped by message send date, not order date; attribution keeps updating after send; campaign/flow and email/SMS are two partitions of one total, never added together.
- **ESP metric aggregates** – trusted for store orders recorded in the ESP, by event date. Trap: query in account-local calendar days converted to UTC instants, or orders at the month edges drop out.
- **Order-level export** – needed for cohorts. Traps: duplicate order IDs, zero-value and test orders, empty line items, history that starts mid-life ("first observed", not "first ever").
- **Inbox provider dashboards and received headers** – the only evidence for Gmail/Yahoo health and authentication. Trap: an ESP delivery rate or a DNS record is not provider evidence.
- **List and form reports** – trusted for additions and submissions. Traps: a submit is not a unique new consented subscriber; list size is not sendable reach; email and SMS can't be summed into people.
- **SMS invoices or platform billing** – trusted for cost. Trap: long messages and emoji bill as multiple segments.
- **Opens** – diagnostic only. Machine opens inflate them; never headline an open rate or pick a winner on opens.

## SOP
1. `get_context` for the three areas above. List each fact as approved, proposed or missing.
2. Fix the period: local calendar month M, end boundary E = local midnight starting M+1. Record extraction timestamp, timezone, currency, order metric and attribution settings in a metric dictionary block.
3. Check closure. If E plus the longest attribution window plus the late-event buffer hasn't passed, mark message results "provisional".
4. Pull campaign and flow results (send-date basis) and store orders (event-date basis). Recompute every rate from summed counts. Show attributed share of store revenue only from a reconciled order-date extract with identical scope; otherwise print N/A beside both totals.
5. Campaigns and flows: revenue, deliveries, revenue per delivered recipient, orders, unsubscribes, current versus prior. Split reach or traffic change from yield change. Name at most four assets and compare only sends with comparable audiences.
6. Deliverability: bounce, unsubscribe, spam complaint and click rates per channel against the published bands below, with counts and denominators; provider rows marked Verified, Issue or Not verified.
7. List growth: opening balance + additions − unsubscribes − suppressions ± other = closing balance, per channel. Flag any bridge that doesn't reconcile.
8. Repeat purchase and cohorts: the mature 45-day second-order rate for DTC-first one-time buyers (cohort month M−2), second order counted anywhere observed, with n/N and the prior mature cohort in percentage points. Report marketplace-first and subscription-first cohorts separately. Gross placed orders; print "refunds not deducted". Don't read horizons built from different customer populations as one curve.
9. SMS costs: deliveries, segments, cost, revenue per delivery and cost per attributed order. Without `sms_cost_basis`, print cost unavailable.
10. Business outcomes: store revenue, orders, AOV, new versus returning buyers from order history, against target and prior period. In Nov–Dec add calendar YoY plus a separately labelled event-aligned view.
11. Anomalies: run the investigation order in Decision rules for every move past the thresholds. `flag_insight` each confirmed issue with the export, filter and date as evidence.
12. Write findings as claims with a number, a comparison and an implication. End with at most three priorities, each with success measure, owner, deadline, dependency, and one client decision.
13. `save_output` (kind `report`). To deliver, `request_approval` with `client.message.send` and the exact report. `finish`.

## Decision rules
- If a value wasn't measured, then show a dash and "unavailable". Never enter zero, and never call a missing trend "stable".
- If a series drops to zero or vanishes, then treat it as a data question first: check pagination and truncation, metric or integration changes, time boundaries, attribution setting changes, then sends and audience, then site, stock and offers, then deliverability. Only then call it a business problem.
- If two revenue numbers have different date bases or scopes, then never subtract one from the other.
- If a metric moved but no controlled test exists, then write "observed change", never "lift" or "caused". Asked "what did email add", report attributed revenue as platform credit and say incremental impact needs a holdout.
- If a cohort isn't fully mature, then it can't be the headline.
- If a cohort has N < 100 or fewer than 20 repeaters, or a ranked asset has fewer than 20 orders, then flag it as small and show the interval or counts.
- If a provider threshold is breached, or a Klaviyo rate is in "needs attention", then `flag_insight` severity high and add a deliverability priority.
- If attribution settings changed during the comparison window, then restate both periods or don't compare.
- If a "stop" fact is missing, then `flag_insight`, save a draft and `finish`.

## Examples
### Good
"Flow revenue fell 12% while deliveries fell 18% and revenue per recipient rose 7%: fewer people entered the checkout flow; it didn't get worse. Store orders (event date) were flat. Attributed share is N/A because no reconciled order-date extract exists. The July cohort's 45-day second-order rate was 9.1% (n/N shown), −0.8 pp versus June, above the small-sample flag. Next: verify the checkout event fired every day (the series has two zero days); owner analyst, due Friday."
### Not good
"Email drove 41% of revenue this month and the new welcome flow lifted repeat rate by 2 points." It mixes date bases to get a share, calls attribution "drove" and claims lift with no control.

## Quality checklist
- [ ] A metric dictionary block states timezone, currency, period boundaries, extraction time, order metric and attribution settings.
- [ ] Attributed and store revenue are labelled separately, and no figure adds campaign/flow to email/SMS.
- [ ] Attributed share appears only with a reconciled order-date extract; otherwise N/A.
- [ ] Missing values show as unavailable, never zero; provisional periods are marked.
- [ ] Rate changes are in percentage points, money and count changes in percent, each with its denominator.
- [ ] The repeat-purchase headline uses a fully mature cohort with n/N and the refund caveat.
- [ ] Deliverability uses the published bands with counts, and provider checks are separate.
- [ ] No sentence claims lift, causation or incrementality without a controlled test.
- [ ] Every anomaly lists the checks run and the evidence.
- [ ] At most three priorities, each with measure, owner, deadline and dependency.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `analysis.run` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent; stays proposed |
| `result.record` | autonomous | Agent, once the measurement window has closed |
| `approval.request` | autonomous | Agent |
| `fact.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `client.message.send` | explicit_approval | Account lead or org admin |
| `account.settings_change` | explicit_approval | Org admin or client owner (locked); includes attribution settings |

## Outputs and destination
- **Monthly report** (report): executive summary (result versus target, driver, mature retention, risk, decision), channel sections, deliverability, list growth, forms, learning, next month; appendix with full inventories, metric dictionary and reconciliation. Task output; Client Hub after approval.
- **Anomaly note** (analysis): what moved, checks run in order, cause or "unresolved", evidence, next check. Task output plus `flag_insight`.

Thresholds. Published (Klaviyo campaign and flow health benchmarks): bounce healthy below 1.00%, needs attention above 2.00%; unsubscribe healthy below 0.30%, needs attention above 1.00%; spam complaints healthy below 0.01%, needs attention above 0.05%; unique clicks healthy above 1.2%, needs attention below 0.8%. Published (Google and Yahoo bulk sender requirements): user-reported spam below 0.10% and never 0.30% or above; SPF, DKIM and DMARC with alignment; one-click unsubscribe honored within two days. Published encoding rule (GSM 03.38): one SMS segment holds 160 GSM-7 or 70 UCS-2 characters. Wavy starting defaults: 5-day late-event buffer; 45-day second-order window; small-sample flags as above; investigate any headline metric moving more than 25% period over period, any rate crossing a band, and any zero or missing day.

## Maintenance
The lead analyst reviews this skill every 90 days. Review early when the ESP changes attribution or reporting endpoints, when Gmail or Yahoo change sender rules, after a report is reissued for a material error, and before peak-season reporting.
