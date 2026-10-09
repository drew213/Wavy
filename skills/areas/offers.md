---
key: area.offers
kind: area
area: offers
title: Offers and merchandising
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Offers buy a conversion with margin, and every one teaches customers what to expect next time. This area decides which incentive (if any) a message carries, proves it is profitable and works at checkout exactly as written, and keeps the program from training the list to wait for discounts. A broken or over-stacked code costs trust at the moment someone is trying to pay.

## Client facts and rules
Look up each fact with `get_context` for `offers`, then `economics`.

- `approved_offer_types` – which mechanisms the client allows: percent off, amount off, free shipping, gift with purchase, bundle, tiered threshold, subscription incentive, early access, none. Missing: plan no-offer messages only; `flag_insight`.
- `approved_welcome_offer` – the one signup promise, with eligibility (first order only) and code type. Missing: `flag_insight` and hold welcome copy. Never infer it from old emails or forms.
- `max_discount_depth` – deepest allowed effective discount, by product or category. Missing: stop before proposing any depth.
- `contribution_margin_inputs` – unit cost, fulfilment, payment fees, shipping cost by product, given in writing. Missing: label every value gross and do not claim profitability.
- `discount_combinability` – how codes combine with sitewide, automatic, subscription and loyalty discounts in the store settings. Missing: assume unknown and require a checkout test before any send.
- `subscription_terms` – subscription discount, cadence, and whether one-time codes apply to subscription orders. Missing: no subscription incentive copy.
- `free_shipping_threshold`, `aov_baseline` – current threshold and median order value. Missing: `propose_fact` from order data, labelled proposed.
- `gwp_inventory` – gift items, units and cost. Missing: no gift-with-purchase.
- `excluded_products` – new launches, low-margin items, bundles, gift cards, subscriptions. Missing: `flag_insight`; checkout test must cover full-price and excluded lines.
- `offer_eligibility_rules` – first-time only, existing customers, VIP, winback age. Missing: stop audience setup.
- `promo_frequency_limit` – how often sitewide promotions may run. Missing: use the Wavy starting default and `propose_fact`.
- `discount_approver` – who approves new or changed offers. Missing: org admin or client owner (policy default).

Common exceptions: marketplace orders and retail partners have their own pricing; regulated products may prohibit certain incentives; a client may forbid public codes that leak to coupon sites.

## Inputs and sources
- **Store discount settings**: the only source for combinability and exclusions. Trap: the email platform's coupon catalog may be empty or stale; copy is not proof of what applies.
- **Order data with discount codes**: trusted for code usage, depth realized and the share of orders on discount. Trap: one order can carry several discounts; count the order once.
- **Checkout test**: the only proof an offer works. A preview or a code list is not.
- **Margin inputs from the client**: trusted only when given in writing.
- **Campaign history by offer type**: trusted for relative comparisons. Trap: deeper offers often went to warmer audiences; compare like with like.
- **Competitor offers**: show what ran, not what was profitable.

## SOP
1. Trigger: a calendar row or flow that needs an incentive. Run `get_context` for `offers` and `economics`.
2. Name the job: first order, threshold lift, clear stock, launch, winback, subscription start. If the job can be done without an offer (education, early access, social proof), propose that first.
3. Pick the mechanism from `approved_offer_types` that fits the job: threshold or gift to lift order value; bundle to move a slow item with a fast one; early access to reward without margin; subscription incentive only for eligible repeat buyers.
4. Confirm how discounts combine from the platform's combination settings and a test checkout, never from arithmetic alone: some setups apply only the best discount; others apply a later percentage to the already-discounted price, where the effective discount is 1 − ((1 − d1) × (1 − d2) × …) (25% then 15% is 36.25%, not 40%). Record the tested result as the `stacking_rules` fact.
5. Compute contribution per order: net price after all discounts − unit cost − fulfilment − payment fees − shipping subsidy − gift cost. Show the inputs. If any input is missing, write "gross only".
6. Check against `max_discount_depth`, the margin floor and `promo_frequency_limit`.
7. Define eligibility and exclusions: who qualifies, which products are excluded, expiry, code type (unique single-use for welcome and winback; shared only for public sales).
8. Write the offer spec: mechanism, value, effective depth, eligibility, exclusions, start and true expiry, code type, stacking behaviour, contribution, and the exact customer-facing terms line.
9. `save_output` (kind `brief`, label "Offer spec: <name>").
10. `request_approval` with action `discount.change` and the offer spec as payload. `finish`. Never create or edit a discount yourself.
11. After approval and creation, run the checkout test (SOP of the `campaign_production` QA step) and record the result with evidence: code applies, value matches copy, exclusions hold, stacking matches spec, expiry is real.
12. After the send window closes, `save_output` (kind `analysis`) with redemptions, effective depth realized, contribution and full-price conversion during the window.

## Decision rules
- If an offer is not in `approved_offer_types` or exceeds `max_discount_depth`, then do not propose it as ready. Present it as an option needing a client decision.
- If the effective discount on any possible order exceeds the approved depth, then fix combinability first. Copy never promises a stacked total the store does not enforce.
- If margin inputs are missing, then the offer may still be proposed, labelled "gross only, profitability unverified". Never call it profitable.
- **Thresholds, Wavy starting default:** set a free-shipping or gift threshold 15–25% above median order value, and confirm that at least one natural two-item basket clears it.
- **Escalation:** a flow carries one offer and never escalates its value across steps unless the client approved an escalation test with a control. No fake deadlines, countdown timers or "last chance" on an offer that will recur.
- **Promotional fatigue, Wavy starting default:** no more than one sitewide promotion every 6 weeks outside peak. Flag fatigue if the share of orders using a discount rises 3 months in a row, or full-price conversion falls during non-promo weeks.
- If a welcome or winback code would be shared publicly, then use unique single-use codes with an expiry.
- If a subscription is involved, then a one-time code must not stack onto the subscription discount unless the spec says so and the test proves it.
- If a "was/now" or "save $X" claim appears, then the reference price must be a real recent selling price (FTC Guides Against Deceptive Pricing, 16 CFR Part 233).
- If the checkout test fails, then the send stops. `flag_insight` (critical) and correct copy or settings before re-approval.
- If an offer must change after approval (value, dates, eligibility), then it is a new `discount.change` approval and the copy approval is invalidated.

## Examples
### Good
"Job: lift order value for repeat buyers. Mechanism: gift with purchase above a threshold set 20% over median order value; gift unit cost and inventory from the client's sheet. Effective discount: none on product price. Contribution: shown per order at threshold, gross of returns. Eligibility: purchased at least once, marketing consent. Exclusions: subscriptions, gift cards. Expiry: real, 23:59 recipient timezone on the stated date. Code: automatic, no code. Stacking: does not combine with the welcome code (store setting confirmed). Terms line drafted. Status: awaiting discount.change approval."

### Not good
"Add an extra 15% to the sale for VIPs, it'll stack, so they get 40% off. Ends tonight!" The stacked value is wrong (36.25%), the depth may break the margin floor, stacking is asserted not tested, "ends tonight" is false if the offer recurs, and nobody approved it.

## Quality checklist
- [ ] The offer spec names the job, mechanism, value and the effective discount computed multiplicatively for every possible stack.
- [ ] Contribution per order is shown with inputs, or the spec says "gross only, profitability unverified".
- [ ] Eligibility, exclusions, code type, start and a real expiry are all stated.
- [ ] Stacking behaviour is stated and matches the store's combinability settings.
- [ ] The mechanism is in the client's approved offer types and within the approved depth, or it is clearly marked as a client decision.
- [ ] A `discount.change` approval was requested for any new or changed offer; the agent created no discount.
- [ ] The checkout test result is recorded with evidence before any send that carries the offer.
- [ ] Customer-facing terms match the spec word for word (value, eligibility, expiry).
- [ ] No fake urgency, escalating flow offer or unsupported reference price.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed until approved |
| approval.request | autonomous | Agent (logged) |
| discount.change | explicit_approval | Org admin or client owner/approver; locked, never relaxed |
| decision.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| fact.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| client.message.send | explicit_approval | Org admin or account lead |

## Outputs and destination
- **Offer spec** (kind `brief`): the fields in SOP step 8. Goes to the task output and is the payload of the `discount.change` approval.
- **Checkout test record**: evidence per offer (code, basket, result) attached to the production task.
- **Offer review** (kind `analysis`) after the window closes: redemptions, realized depth, contribution, full-price conversion, fatigue signals.
- Approved offers appear in the Client Hub; clients see an offer only through approved copy or an approved client message.

## Maintenance
The strategist lead reviews this skill every 90 days. Review early when a checkout test fails on a live send, a client changes margin inputs or discount settings, a platform changes discount combinability, or a regulator updates pricing guidance.
