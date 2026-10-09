---
key: area.journey
kind: area
area: journey
title: Lifecycle strategy and customer journey
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Map how a person moves from first visit to lead, first purchase and repeat purchase, find the transitions where the client loses people, and decide what to fix first. A good journey strategy makes every flow, campaign and text part of one coordinated conversation instead of parallel inventories. The founder cares because the second order, not attributed flow revenue, is where retention pays back acquisition.

## Client facts and rules
Look up each fact with `get_context` before writing strategy.

- `primary_goal` – the client's goal in their words, with a number if given. Missing: `flag_insight` and stop; do not invent a target.
- `quarter_targets` – agreed revenue, repeat or list targets for the quarter. Missing: write the plan without targets and label it "targets not agreed".
- `store_platform`, `past_store_platforms` – current and previous commerce platforms. Missing: stop; old platforms leave dormant order metrics.
- `canonical_order_metric` – the pinned Placed Order metric. Missing: stop. No revenue or repeat number is written until it is pinned.
- `account_timezone` – used for every period boundary. Missing: `propose_fact` from the account settings, then continue with the proposed value labelled.
- `messaging_tools` and `sms_provider` – every ESP and SMS tool in use. Missing: `flag_insight`; do not treat no native SMS as no SMS contact.
- `subscription_platform` – the subscription app, and whether its events reach the ESP. Missing: treat subscription state as unknown.
- `regulated_category_limits` – claim, channel and offer limits for the category. Missing: `flag_insight` and hold any copy direction.
- `approved_welcome_offer`, `offer_stacking_rules` – the one approved signup promise and how it combines, using the checkout-tested calculation in `offers`. Missing: plan the offer as "client decision needed".
- `contribution_margin` – margin inputs, given in writing. Missing: label every value as gross. Never infer a margin.
- `contact_cap_policy` – the client's approved cross-channel frequency rule. Missing: use the Wavy starting default under Decision rules and mark it proposed.
- `peak_freeze_window` – dates when structural changes stop. Missing: propose one in the quarterly plan.

Common exceptions: retailer or wholesale accounts follow their own operations, not consumer lifecycle. Native subscription and order-service messages stay intact in every recommendation.

## Inputs and sources
- **Pinned order metric events**: trusted for first and repeat purchase timing. Trap: a 24-month window makes "first observed" differ from "truly new".
- **Flow and campaign reporting**: trusted for exposure and attributed revenue. Trap: attribution is platform credit, not lift. Campaign, flow, email and SMS splits partition the same attributed total, so never add them.
- **Flow definitions**: trusted for configured triggers, filters and timing. Trap: today's definition says nothing about what was live last quarter.
- **Check library results** (RET-01 to RET-04, FLW-08, DAT-01, DAT-07): trusted as scored evidence with paths only when the packet supplies each check's version, precondition, calculation, severity, threshold and accepted evidence. Otherwise the result is unknown; never reconstruct a check from its ID.
- **Received messages and click tests**: the only proof of what a customer experiences. A saved template is not a received message.
- **Third-party SMS exports**: trusted for that platform's sends. Trap: its revenue usually overlaps email attribution.
- **Client intake answers**: trusted for margins, offers and limits only when given in writing.

## SOP
1. Run `get_context` for the client. Confirm the facts above. Stop on any missing blocking fact (goal, platform, order metric, regulated limits).
2. Build the stage map: capture, welcome, recover (browse, cart, checkout), first order and delivery, product use, second order (replenish, subscribe, cross-sell), retain and reconnect (winback, sunset, preferences), advocacy (reviews, referral). For each stage, record the live flows, channel, trigger, exits and owner. If the client sells subscriptions, also map subscription retention: onboarding, skip, pause and swap, renewal reminders, failed payment, cancellation reasons and save eligibility. Record which of these the native subscription tool already sends; never duplicate or contradict them.
3. Mark each stage as covered, partial, missing, not applicable or unknown. Present is not healthy.
4. Quantify transitions with the correct starting population: lead-to-first-order needs deduplicated consent or signup records joined to eligible store orders; first-to-second-order needs a mature first-order cohort joined to subsequent eligible orders. Show n/N, index event, horizon, channel scope, history coverage and exclusions. Label p25 and median gaps among repeat buyers as conditional on repeating; never present them as depletion times or as quantiles for all buyers.
5. List friction points with evidence: promise mismatch from form to welcome to checkout, offers that fail at checkout, broken links, missing purchase exits, service problems answered with promotions, reorder messages that miss the reorder window, duplicate asks across email and SMS.
6. Assign channel roles (see Decision rules). Record one channel owner per journey.
7. Size each opportunity with the `quarterly_planning` method: a cohort counted once (or disjoint dated cohorts) × absolute H-day rate change × value per incremental conversion; never multiply an H-day effect by months. Use permissioned, deduplicated reach, never list size. Assign each customer pool to one lever.
8. Rank with the prioritization framework below. Keep at most three priorities per quarter, each with owner, done-when, dependency and success measure.
9. `save_output` the journey map as an analysis and the quarterly plan as a brief. `flag_insight` for each critical friction point.
10. Use `request_approval` for the plan through a proposed decision. `finish` with the open client decisions listed.

## Decision rules
- **Prioritization tiers**, in order: (1) broken promises, consent and compliance risks; (2) wrong or dormant data and triggers; (3) service routing that sends promotions to people with open problems; (4) missing core coverage (welcome, checkout, post-purchase); (5) optimization and tests. Within a tier, rank by reach × severity × evidence strength ÷ effort, and show each input. No unexplained composite score.
- If a lever depends on a client action (DNS fix, offer decision, subscription data) and that gate is unmet, then its value is zero in the plan until the gate clears.
- If the repeat cohort is immature, then report it as provisional and size no second-order value on it.
- **The business model sets the second-order objective** (`revenue_model` and product data). Consumables: reorder timed from usage evidence, not repeat gaps alone. Durables: care, accessory, gifting, upgrade or referral; no reorder reminders. Subscriptions: renewal, retention and save, coordinated with the native tool. Assign each product to one pathway; an unknown pathway gets no timed reorder message.
- **Email is the default marketing channel.** Use SMS only for high-intent, time-sensitive moments (checkout, the accepted welcome benefit, requested alerts) and only after verified SMS consent, provider coverage and cross-channel deduplication.
- If an SMS follow-up depends on an email, then send it only when that email actually sent and the person neither purchased nor clicked it. Opens do not qualify: machine opens inflate them.
- **Contact cap (Wavy starting default, used only when `contact_cap_policy` is missing):** at most 2 discretionary promotional contacts per rolling 24 hours and 5 per rolling 7 days across email and SMS; SMS at most 1 per 24 hours and 2 per 7 days. Promotional welcome messages count; only genuine non-promotional service messages don't.
- If two journeys compete for one person, then use this priority: service and customer requests, checkout, cart, accepted welcome benefit, browse, product-use help, requested refill, winback, discovery, advocacy, general campaigns. A purchase ends open recovery and acquisition episodes.
- If a recommendation changes a live flow, offer, audience or account setting, then it becomes a proposed decision, never a direct action.
- If the category is regulated, then no efficacy, clinical or result-timeline claim enters the strategy without the client's approved substantiation.
- If the client asks for a change during an audit, then log it as a roadmap item for after delivery.
- Escalate to the account lead when goals conflict with consent or compliance limits, or when a priority needs a new offer.

## Examples
### Good
"Second order is the leak. Of buyers whose first order is at least 120 days old, 14% (n/N shown) ordered again within 120 days. Among those who repeated, the median gap was 52 days and p25 38 days (conditional on repeating; usage guidance for the hero consumable says one unit lasts about 30 days). The first post-purchase email after the thank-you sends at day 75. Priority 1: test one reorder reminder at day 30–38, email first, for one-time buyers with no active subscription. Gate: subscription status must be known. Measure: mature 60-day second-order rate against a held-out group. Owner: strategist. Done when: draft flow approved and QA'd."
### Not good
"Add more SMS, launch a VIP program and refresh all creative to grow retention 30%." It fails because it cites no account data, sums attributed revenue across channels, ignores consent and gates, and gives no owner, measure or order of work.

## Quality checklist
- [ ] Every lifecycle stage is marked covered, partial, missing, not applicable or unknown, with the live flow named.
- [ ] Each transition states its starting population, index event, horizon and n/N from mature cohorts; repeat gaps are labelled conditional on repeating.
- [ ] Each friction point cites evidence (received message, click test, check ID or event data), not a saved template alone.
- [ ] Each journey has one named channel owner and SMS steps state their consent and engagement condition.
- [ ] No more than three quarterly priorities, each with owner, done-when, dependency and success measure.
- [ ] Sized levers show cohort definition, absolute H-day rate change and value basis (gross or margin), with no H-day effect multiplied by months and no customer pool counted twice.
- [ ] Each second-order priority matches the product's business-model pathway; subscription moments are mapped beside the native tool's existing messages.
- [ ] Gated levers show their gate and are zero when the gate is unmet.
- [ ] No client data from another brand, no invented margin, no guaranteed lift.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed |
| decision.propose | autonomous | Agent (logged) |
| approval.request | autonomous | Agent (logged) |
| decision.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| fact.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| client.message.send | explicit_approval | Org admin or account lead |

## Outputs and destination
- **analysis**: the journey map. One row per stage: purpose, live flows, coverage state, top finding, evidence, freshness date. Goes to task output and, once reviewed, the Client Hub.
- **brief**: the quarterly plan. Up to three priorities, each with problem, evidence, action, owner, done-when, dependency, measure and sized range. Goes to task output, then into a proposed decision.
- **report**: client-facing summary only after the account lead approves it through `client.message.send`.

## Maintenance
The strategist lead reviews this skill every 90 days. Review early when a mailbox provider or regulator changes rules, when the check library changes version, or when two audits show the same uncovered friction point.
