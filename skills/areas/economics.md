---
key: area.economics
kind: area
area: economics
title: Business model and economics
owner_role: strategist
version: 2
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Economics tells every other area what a message, offer or flow is worth to this business after costs. Without it, the agency optimizes attributed revenue, gives away margin with discounts and recommends volume the warehouse can't ship. A founder should care because retention work is only valuable if it improves contribution, not just platform-credited sales.

## Client facts and rules
Look each up with `get_context` (area `economics`). Margins, costs and offer rules come from the client in writing. If a fact is missing, record it as unavailable, `propose_fact` only when you have a written client source, and `flag_insight` naming what it blocks. Never infer a margin from prices or industry averages.

- `revenue_model` – channel mix (DTC, subscription, marketplace, wholesale) and which channels retention can influence. Missing: report DTC only and label it.
- `gross_margin_by_product` – gross margin per product or category. Missing: label all value as gross revenue; do not rank offers by profit.
- `contribution_margin_per_order` – net sales after discounts and refunds, less product cost, variable fulfillment, payment costs and messaging cost. Missing: size in gross revenue, labelled.
- `aov_source` – which order metric and window AOV comes from. AOV = eligible net sales ÷ eligible order count; median order value is reported separately. Missing: take it from the pinned order metric in `data_infrastructure`; if not pinned, stop.
- `acquisition_cost` – blended or paid CAC, and its source. Missing: do not state payback or LTV:CAC.
- `purchase_frequency` – typical days between orders, and how long one unit lasts per hero product. Missing: no replenishment timing; flag to `product_customer`.
- `subscription_terms` – price, discount, cadence, skip/cancel rules and the platform. Missing: subscription status is unknown; exclude unknowns from subscription pitches and refill messages.
- `seasonality` – peak periods, freeze windows, and last year's peak dates. Missing: use the account's own order history by month, labelled observed.
- `inventory_constraints` – stock limits, low-stock SKUs, lead times, fulfillment capacity. Missing: no volume recommendations for constrained SKUs; flag.
- `free_shipping_threshold` – the threshold and market eligibility from current shipping configuration and client approval, plus average shipping cost per order. Missing threshold: no shipping promise or offer. Missing order data: no order-value estimate.
- `discount_rules` – offers allowed, offers never allowed, max depth, stacking behavior at checkout, and who approves. Missing: no offer of any kind.
- `business_goals` – the period's goal with metric, number and date. Missing: record "no numeric goal".

## Inputs and sources
- **Client finance answers (written)** – trusted for margins, costs, CAC, thresholds. Trap: verbal numbers drift; ask for them in writing.
- **Pinned store order metric** – trusted for order counts, AOV and frequency. Trap: dormant or duplicate integrations inflate counts; use only the metric pinned by `data_infrastructure`.
- **ESP attributed revenue** – trusted as platform credit only. Trap: attributed revenue is not incremental or profit, and campaign/flow and email/SMS splits are two partitions of the same total.
- **Checkout tests** – trusted for whether discounts stack, eligibility and true expiry. Trap: a discount config screen is not a checkout result.
- **Inventory and fulfillment data** – trusted for what can ship. Trap: stock counts change daily; recheck before each send.
- **Subscription platform** – trusted for active contracts and terms. Trap: unknown status is not "not subscribed".

## SOP
1. Call `get_context` for `economics` and `data_infrastructure`. Confirm the order metric is pinned and note the account timezone and currency.
2. List each fact above as approved, proposed or missing. Record missing ones as unavailable, never zero.
3. From the pinned order metric, compute observed AOV (eligible net sales ÷ eligible orders), median order value separately, orders per customer and median days between first and second order over a stated window, in the account timezone. Use first-observed purchase if lifetime history is unproven, and say so.
4. Build the unit-economics sheet: per hero product, price, gross margin (if given), contribution per order (if given), shipping cost against threshold, and typical days to repurchase.
5. Model offers using the verified mechanics in `offers`, or a clearly labelled proposal scenario before implementation. Define C0 and C1 as baseline and promoted contribution per order on the same cost/refund basis. For C0 > 0 and C1 > 0, break-even order ratio = C0/C1 and required relative lift = C0/C1 - 1, assuming a constant basket/cost mix and that the promotion applies to all affected orders. For C0 > 0 and C1 <= 0, added volume cannot recover the positive baseline contribution. For C0 <= 0, flag unprofitable baseline economics and do not present the ratio as a growth target. State cannibalization, returns and messaging costs once, with any separately budgeted campaign cost; scenarios are not measured lift. Mark every offer that has not passed a checkout test.
6. Map seasonality and inventory: monthly order index from history, known peaks, freeze windows, and constrained SKUs with dates.
7. Write the economic guardrails other areas must follow: max discount depth, offers never allowed, SKUs not to push, the free-shipping lever if approved, and the valuation basis (contribution or labelled gross).
8. `flag_insight` for each risk with evidence, such as an offer below break-even, a stacking leak, or a hero SKU out of stock in a planned window.
9. Save the sheet and guardrails with `save_output` (kind `analysis`). `propose_fact` for any new client-sourced value. For an offer change, draft the exact terms and call `request_approval` with `discount.change`; never create or edit a discount yourself.
10. `finish` with what is approved, what is missing and what each gap blocks.

## Decision rules
- If contribution margin is missing, then value every lever in gross revenue, label it gross on every figure, and do not rank levers by profit.
- If an offer's discount exceeds the approved maximum depth, or its margin after discount is negative, then do not recommend it; flag it to the account lead.
- If discounts combine at checkout and the combined depth (from the verified discount classes, bases and application order in `offers`) exceeds the approved maximum, then flag as high severity and hold any copy that promotes the offer. Unknown mechanics block any combined-saving figure.
- If C0 > 0 and C1 ≤ 0, then added volume cannot recover the baseline contribution: don't recommend the offer. A strategic subsidy (for example acquisition or clearance) may be proposed only with an explicit, approved budget for the expected contribution loss and the authorized approvals (`decision.approve` for the budget, `discount.change` for the offer); account-lead agreement alone is not enough.
- If C0 ≤ 0, then flag the unprofitable baseline economics to the account lead with `flag_insight`, show no break-even ratio as a growth target, and recommend no volume-seeking offer until the baseline is resolved.
- **Value retention by business model** (`revenue_model`): consumables by reorder from usage evidence; durables by accessory, care, gifting or upgrade, never a reorder rate; subscriptions by renewal and save, with automatic renewals kept separate from discretionary repeat orders.
- If a SKU is constrained in the send window, then exclude it from promotions and recovery pushes for that window and say why.
- If subscription status is unknown for a customer group, then treat them as possibly subscribed: no subscription pitch and no refill reminder.
- If you only have attributed revenue, then never call it incremental, profit or ROI.
- If a goal number has no source and date, then treat it as an aspiration, not a target.
- If the client asks for an offer outside `discount_rules`, then draft it as a proposal with its economics and route it through `discount.change`.

## Examples
### Good
Synthetic example, not client data. "Baseline net price $100; combined variable costs $60 (client finance email); C0 = $40. A checkout-tested 20% discount leaves net price $80 and C1 = $20. C0 > 0 and C1 > 0, so the ratio applies: break-even requires 40 ÷ 20 = 2 times as many orders, a 100% lift, before fixed campaign costs. Assumes the same basket and cost mix. Recommendation: don't run it as a broad offer; test a smaller incentive. Missing cost inputs would mean: profitability unverified."
### Not good
"Margins are probably around 70% for this category, so a 30% offer is safe and should add $40k." It infers a margin, ignores stacking, uses attributed-style certainty and states a number with no formula or source.

## Quality checklist
- [ ] Every figure states its source, window, timezone and currency.
- [ ] Gross revenue and contribution are labelled on every value; none are mixed.
- [ ] No margin, cost or CAC appears without a written client source.
- [ ] Missing facts are shown as unavailable with what they block, never as zero.
- [ ] Each offer shows depth, C0, C1 and its case: break-even ratio and required lift only when C0 > 0 and C1 > 0; "volume cannot recover baseline contribution" when C0 > 0 and C1 ≤ 0; "unprofitable baseline, no ratio" when C0 ≤ 0; plus assumptions, checkout-test or proposal-scenario status, and any strategic subsidy's approved budget.
- [ ] Combined discounts follow the checkout-tested calculation in `offers` (discount classes, bases, application order); no universal multiplication or addition.
- [ ] AOV is eligible net sales ÷ eligible orders with median order value separate, and no shipping promise appears without a configured, approved threshold.
- [ ] Constrained SKUs and freeze windows are listed with dates.
- [ ] Attributed revenue is never described as incremental or profit.
- [ ] Any offer change is a `discount.change` approval request, not a direct edit.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| `data.read` | autonomous | Agent, logged |
| `analysis.run` | autonomous | Agent, logged |
| `insight.create` | autonomous | Agent, logged |
| `draft.create` | autonomous | Agent, logged |
| `fact.propose` | autonomous | Agent or client decider; stays proposed |
| `approval.request` | autonomous | Agent |
| `fact.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `decision.approve` | explicit_approval | Account lead, org admin or client owner/approver |
| `discount.change` | explicit_approval | Org admin or client owner/approver (locked) |

## Outputs and destination
- **Unit-economics sheet** (analysis): per hero product price, margin, contribution, shipping against threshold, repurchase interval; observed AOV and frequency with window. Task output, then Client Hub after account-lead review.
- **Discount economics table** (analysis): each offer with depth, C0, C1, break-even ratio and required lift, assumptions, combination basis and test status. Task output.
- **Seasonality and inventory calendar** (analysis): monthly index, peaks, freeze windows, constrained SKUs. Feeds `campaign_calendar` and `offers`.
- **Economic guardrails** (other): the rules other areas apply. Proposed facts until approved.
Thresholds in this area are Wavy starting defaults: re-request margin and cost facts every 90 days or when prices change; treat any offer whose margin after discount falls below the client's stated floor as blocked.

## Maintenance
The strategist reviews this skill every 90 days. Review early when a client changes pricing, shipping threshold, subscription terms or platform; after any peak season; or if an offer shipped that later proved unprofitable or stacked unexpectedly.
