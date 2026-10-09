---
key: area.segmentation
kind: area
area: segmentation
title: Segmentation and audience architecture
owner_role: analyst
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Define one shared set of audiences that every flow, campaign and report uses, so "engaged", "customer" and "VIP" mean the same thing everywhere. Good architecture protects deliverability, keeps people out of messages that don't fit them, and makes reach numbers honest. The founder cares because sending to the wrong people costs inbox placement and customers, and inflated reach leads to inflated forecasts.

## Client facts and rules
Look up each with `get_context` before building or changing any audience.

- `canonical_order_metric` – the pinned Placed Order metric. Missing: stop. Customer and recency segments cannot be built without it.
- `account_timezone` – for every lookback boundary. Missing: `propose_fact` from account settings and label it.
- `product_families` – how products, variants and flavors group for affinity, replenishment and repeat rules. Missing: `flag_insight`; build no product-affinity segment.
- `vip_definition` – the client-approved VIP rule. Missing: propose the Wavy default below with `propose_fact` and mark segments draft.
- `subscription_platform` and `subscription_state_source` – where active, paused and cancelled status comes from. Missing: subscription state is unknown, never inactive.
- `retailer_account_rule` – how wholesale or retailer profiles are identified. Missing: `flag_insight`; exclude known business accounts manually and note the gap.
- `internal_and_test_profiles` – staff, seed and test addresses. Missing: ask the account lead; exclude obvious seeds.
- `markets_served` – countries that change consent rules. Missing: assume the strictest market the store ships to and flag it.
- `sms_provider` – where SMS consent lives. Missing: SMS-consented counts are unknown.

Common exceptions: transactional-only profiles are reachable for order service, never marketing. Imported lists without consent provenance are not marketable.

## Inputs and sources
- **Profiles with subscription fields**: trusted for current consent per channel. Trap: list membership is not consent; a profile can sit on a list after unsubscribing.
- **Pinned order events with line items**: trusted for recency, frequency and product affinity. Trap: empty item arrays block product segments; check the extractor and integration first (DAT-03).
- **Engagement events**: clicks, site activity, orders are trusted. Opens are machine-affected (Apple Mail Privacy Protection); use them only where the platform separates real opens, and never alone.
- **Segment counts**: membership counts, not sendable reach (DAT-07). Overlapping counts never add.
- **Subscription platform events**: trusted only when start, cancel and renewal events arrive every month (RET-03).
- **Third-party SMS platform**: its subscriber count usually differs from the ESP's SMS list. Reconcile before using either.

## SOP
1. `get_context`. Confirm the facts above. Stop if the order metric is not pinned.
2. Inventory existing lists and segments: name, definition, size, last used, flows and campaigns using it. Mark duplicates and conflicting definitions.
3. Build the master audiences (Wavy starting definitions):
   - **Consent layer**: email marketing subscribed; SMS marketing subscribed; transactional only; suppressed.
   - **Prospects**: consented, zero orders on the canonical metric.
   - **Customers**: one-time buyers (exactly one order), repeat buyers (two or more), lapsed (no order past 1.5× the account's median repeat gap), churn risk (between 1.0× and 1.5× of that gap with no order).
   - **Engagement tiers**: 30, 60, 90 and 180 days by click, site activity or order, made disjoint (each person in exactly one tier) plus an unengaged tier.
   - **Subscribers**: provider-verified active, paused, cancelled, never subscribed, unknown.
   - **VIP**: per `vip_definition`. If none is approved, propose the Wavy starting default: two or more orders and the top 10% of customers by order value in the last 365 days, net of refunds where known.
   - **Product affinity**: purchased or currently carted family, from line items.
4. Build the standing suppressions and exclusions: unsubscribed, hard bounced, spam complainers, manually suppressed, internal and test, retailer accounts, open service cases (promotions only), recent purchasers (acquisition and recovery only), active subscribers (replenishment and winback only).
5. Name every audience with the convention below.
6. Validate (see Decision rules). Record counts with the as-of time.
7. Compute sendable reach per channel: consented, not suppressed, deduplicated.
8. `save_output` the audience spec as a draft_spec and the validation as an analysis. Use `build_draft` to prepare segment changes, under an approved decision covering `audience.prepare`.
9. `request_approval` for anything that moves profiles in or out of sends. `finish`.

## Decision rules
- **Naming (Wavy starting default):** `<CHANNEL> | <STAGE> | <RULE> | <WINDOW>`, for example `EM | Engaged | click or site | 0-90d` or `ALL | Suppress | service case open | live`. No client initials, agency prefixes or dates in names. A prefix in a name never proves who owns it.
- If a segment uses "viewed ever" or another lifetime behavior, then don't call it current intent; use the last 30 days or the current cart.
- If subscription state is unknown, then exclude the person from replenishment, subscribe nudges and winback.
- If two audiences used in the same send overlap, then make them disjoint before quoting reach.
- If a VIP rule would include a profile with an open refund or service case, then exclude them from VIP promotions until resolved.
- If validation fails any test below, then the segment stays draft and you `flag_insight`.
- **Validation tests:** disjoint tiers sum exactly to their parent; counts reconcile to the order metric within the as-of window; 20 random members per segment (pseudonymous IDs only) match the rule by hand; suppressed profiles appear in zero send audiences; no segment depends on a dormant metric.
- If a change would suppress, delete or bulk-unsubscribe profiles, then it needs `profile.suppress_delete` explicit approval. Never do it under a plan.
- If an import lacks consent source and date, then don't add it to any marketing audience; escalate to the account lead.
- An older consent import must never overwrite a newer opt-out.
- Never copy emails, phones, names or raw profile IDs into outputs. Use aggregates.

## Examples
### Good
"Email reach today: 41,200 consented, not suppressed, deduplicated (as of 00:00 account time). Engagement tiers are disjoint and sum to that total: 0-30d 6,100; 31-90d 9,800; 91-180d 8,300; unengaged 17,000. Subscription state: 1,950 active, 4,400 unknown (excluded from replenishment). Validation: 5 of 5 tests passed; sample checks used pseudonymous IDs."
### Not good
"The engaged audience is 120,000: newsletter list 80,000 plus SMS list 25,000 plus customers 15,000." It fails because it adds overlapping lists, ignores consent and suppressions, and calls membership reach.

## Quality checklist
- [ ] Every audience has a written rule, the source metric, lookback window and as-of time.
- [ ] Names follow the convention and carry no client or agency prefixes.
- [ ] Engagement tiers are disjoint and sum to the parent count.
- [ ] Engagement uses clicks, site activity or orders; opens never qualify on their own.
- [ ] Reach figures are consented, suppressed and deduplicated; list sizes are labelled as membership.
- [ ] Unknown subscription state is a separate group excluded from replenishment and winback.
- [ ] Standing suppressions and exclusions are listed with which sends each applies to.
- [ ] Validation results are recorded per segment with pass or fail.
- [ ] No personal data appears in the output.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed |
| decision.propose | autonomous | Agent (logged) |
| audience.prepare | approved_plan | Covered by a decision approved by org admin, account lead, or client owner/approver |
| fact.approve | explicit_approval | Org admin, account lead, or client owner/approver |
| profile.suppress_delete | explicit_approval (locked) | Org admin or client owner |
| account.settings_change | explicit_approval (locked) | Org admin or client owner |

## Outputs and destination
- **draft_spec**: the audience architecture. One row per audience: name, channel, rule, source metric, window, inclusions, exclusions, used by, count, as-of. Goes to task output; approved specs feed `build_draft`.
- **analysis**: validation and reach census, with each test's result. Goes to task output and the Client Hub after review.
- **report**: a client summary of reach and tiers, sent only through approved `client.message.send`.

## Maintenance
The analyst lead reviews this skill every 90 days. Review early when the ESP changes engagement or consent fields, when the check library changes DAT-07 or CMP-03, or when a validation failure repeats across two clients.
