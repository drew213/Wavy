---
key: area.flow_architecture
kind: area
area: flow_architecture
title: Flow architecture and optimization
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Design the client's automated flows as one coordinated system: the right message for each moment from sign-up to repeat purchase, with clear triggers, exits and priority so no customer gets competing or contradictory messages. Then keep that system tested and maintained. The founder cares because flows run every day without anyone watching, so their logic decides most of the customer experience.

## Client facts and rules
Look up each with `get_context` before specifying any flow.

- `canonical_order_metric` and the canonical browse, cart and checkout metrics. Missing: stop.
- `approved_welcome_offer`, `offer_stacking_rules`, `first_order_eligibility`. Missing: design the welcome without an offer slot filled and flag it.
- `product_families` – grouping for replenishment, cross-sell and education. Missing: use account-level timing and generic content.
- `usage_guidance` – approved serving or usage information per product. Missing: no depletion-based timing; use the observed repeat gap and label it.
- `subscription_platform` and its native notices (renewal, skip, pause, swap, failed payment, cancellation). Missing: no subscription flows; state unknown holds refill and winback.
- `back_in_stock_rules` – alert source, exact-variant restock event, consent scope, reservation policy and alert expiry. Missing: no back-in-stock flow.
- `delivery_event_source` – whether a verified delivered event exists. Missing: anchor to order or fulfillment and label it.
- `review_platform_terms` – the review app's rules on incentives and timing. Missing: no incentive, no review gating.
- `referral_program` – whether one exists and its terms. Missing: no referral flow.
- `contact_cap_policy`, `send_windows` – approved frequency and hours. Missing: use the Wavy defaults below and mark them proposed.
- `sms_provider`, `regulated_category_limits`, `approved_claims`, and documented abandonment-specific SMS consent with double opt-in. Missing: email only for recovery, and no claims.
- `peak_freeze_window` – dates when structural flow changes stop. Missing: propose one with `propose_fact` before peak season.

## Inputs and sources
- **Flow audit scorecard**: start from it. Repair before you add.
- **Repeat-gap distribution** (p25, median) from the pinned order metric, overall and per family when line items exist.
- **Live flow definitions**: current triggers, filters and timing. Trusted for configuration, not performance.
- **Message performance**: descriptive, not causal.
- **ESP behavior docs**: Smart Sending skips a message, it doesn't reschedule it. Profile filters are rechecked before each action; trigger filters only qualify entry. Native global holdouts need a minimum account size (Klaviyo documents 400,000 profiles); confirm current rules before relying on one.

## SOP
1. `get_context`. Read the latest flow audit. List the repairs that block new building.
2. For each journey, write a buildable spec: trigger type (metric, list, segment, date or other verified type), ID and source; exact platform filters/operators; state fields and update owner; delays and anchors; re-entry/episode identity; exits and expiries; channel consent; service/subscription holds; cap enforcement; message purpose, destination and fallbacks. For every mechanism state native-supported, externally implemented or unsupported, with current evidence. Unsupported mechanisms are design gates, not claims of a completed build.
3. Specify the flows (Wavy starting defaults; adjust only with account evidence):
   - **Welcome:** deliver the promised benefit at once; 3–5 emails over 5–7 days covering why the brand, how to choose, proof, and an offer reminder before expiry. Buyers branch to a non-promotional path. Existing customers get no first-order code.
   - **Browse:** 1–2 emails from 1 hour after the last view, exact viewed product, exit on cart, checkout or order, once per 14 days.
   - **Cart:** 2 emails at 2–4 hours and 24 hours, excluding checkout starters.
   - **Checkout:** 3 emails at 1, 24 and 72 hours with the dynamic cart. The episode's one recovery SMS needs abandonment-specific consent, double opt-in and the 24-hour Wavy gate (`sms`); expire it rather than send late. Exit on purchase since flow start.
   - **Post-purchase:** order-state messages that match the actual event, then thank-you without a pitch, then product-use help after delivery.
   - **Education:** one route chosen from delivered items, sent once per product, never repeated on reorders.
   - **Cross-sell:** after onboarding and any service case closes; a product that complements the purchase, never one already owned.
   - **Replenishment:** one reminder at about the p25 repeat gap (per family when possible), one follow-up; active and unknown subscribers excluded.
   - **Subscription onboarding:** confirm plan, how to skip, swap or cancel, coordinated with native notices.
   - **Subscription retention (conditional):** pre-renewal skip, pause and swap reminders only where native tooling sends none; never duplicate or contradict platform renewal notices; on cancellation, confirm in service tone and capture the reason; save offer only if approved with defined eligibility.
   - **Payment recovery:** on failed-payment events, plain service tone, update-payment link; never duplicate the subscription platform's own dunning.
   - **Winback:** at 1.5–2× the median gap, question or reminder first, any incentive later and only with `discount.change` approval.
   - **VIP:** early access and recognition for the approved VIP segment; no extra discount pressure.
   - **Review request:** after a fair use window from delivery, one ask and one reminder, the same for every buyer regardless of satisfaction. Any incentive is neutral, disclosed, allowed by the platform and never conditioned on rating or wording; no gating.
   - **Back-in-stock (conditional):** one alert per requester, in the requested channel, on the exact variant's restock, then expire; exclude anyone who has since bought it; no reserved-stock promise unless the store reserves it.
   - **Referral:** one low-pressure invitation after a supported experience, only if a program exists.
4. Write the overlap rules (Decision rules) into every spec, plus a recipient-state table: episode identity (what an episode is keyed on), the person-level contact ledger or verified native rule that enforces caps, each state field's update owner and expiry, and synthetic tests for simultaneous cart, checkout, purchase and service events.
5. Write the test plan for any change to a working flow.
6. Use `build_draft` to build draft flows under an approved decision covering `flow.build_draft`. Nothing goes live. `audience.prepare` covers new or detached audiences only (`segmentation`).
7. `save_output` specs as draft_spec. `request_approval` for activation (`flow.change_live`) with the exact draft. `finish`.

## Decision rules
- **Repair before add.** If the audit shows a dormant trigger, broken link, failing offer or missing purchase exit, fix that first.
- **Priority across journeys:** service and customer requests, checkout, cart, accepted welcome benefit, browse, product-use help, requested refill, winback, discovery, advocacy, general campaigns. A purchase ends open browse, cart, checkout and acquisition episodes. Starting checkout ends cart and browse. One shopping episode can't own browse, cart and checkout at once.
- **Contact cap (Wavy starting default):** at most 2 discretionary promotional contacts per rolling 24 hours and 5 per rolling 7 days across email and SMS, at least 4 hours apart. Every promotional SMS, including welcome and recovery texts, counts toward 1 per rolling 24 hours and 2 per rolling 168 hours (`sms`). Only genuine non-promotional confirmation and service messages are separate. The accepted welcome benefit may have priority but still counts. A cap holds only where a person-level ledger or verified native rule enforces it; otherwise label it unenforced.
- **Freshness (Wavy starting default):** browse steps expire 24 hours after the last activity, cart 48 hours, checkout 96 hours, welcome follow-ups at day 7; the recovery SMS expires at the 24-hour gate. Quiet-hour delays (`sms` send window) must not push a message past its expiry. Recheck consent, purchase, stock and offer expiry when a queued message becomes eligible. Never replay missed steps in a burst.
- **SMS steps** follow an email only if that email sent and the person neither purchased nor clicked it. Opens don't qualify. One recovery SMS per shopping episode across browse, cart and checkout.
- If a person has an open delivery, refund or product case, then hold discretionary messages until the case is explicitly resolved, then wait 72 hours (Wavy starting default). Service messages continue.
- If a cart is mixed, then send one cart message, not one per product. Current cart items override older interest.
- Build one flow per journey with product modules in shared slots. A separate flow per product needs different timing, eligibility or service needs.
- If subscription state is unknown, then exclude from replenishment, subscribe nudges and winback.
- Never put required consent sync or service holds behind a promotional filter that could skip them.
- **Testing:** use persistent, randomized, person-level assignment and a fixed follow-up window. Analyze everyone assigned, including non-buyers. Message A/B tests and no-message holdouts answer different questions; don't add separate flow tests together as program lift. Guardrails: unsubscribes, complaints, refunds.
- **Maintenance cadence (Wavy starting default):** link and offer check monthly and before each seasonal swap; full flow review quarterly; no structural changes during the approved `peak_freeze_window`.
- Escalate when a design needs a new offer, a new data source, or an SMS provider change.

## Examples
### Good
"Replenishment spec: trigger on the canonical order metric for one-time buyers of a family; wait to the family's p25 repeat gap (label: observed, not depletion); profile filters recheck no same-family order, subscription state known and inactive, no open case, email consent. Email 1 asks if they're running low, with a reorder link to the exact variant. SMS follow-up at +1 day only if email 1 sent and wasn't clicked, inside the send window and counted toward SMS caps. Exit on same-family order. Test: 50/50 persistent split against the current winback timing; measure 90-day second-order rate."
### Not good
"Add a 6-email winback at day 30 with 10%, 15%, then 20% off, and a cart flow with no exclusions." It fails because it escalates discounts without approval, ignores the repeat gap, and lets cart compete with checkout.

## Quality checklist
- [ ] Every flow spec names trigger type and ID, exact filters, exits, episode identity, re-entry rule and owner, and marks each mechanism native-supported, externally implemented or unsupported.
- [ ] Every delay states its anchor (sign-up, last activity, order, delivery) and expiry.
- [ ] Purchase exits are present on welcome, browse, cart and checkout.
- [ ] The cross-journey priority and contact cap appear in the spec, labelled default or client-approved, with the ledger or native rule that enforces them and each state field's owner and expiry.
- [ ] SMS steps state their consent and email-dependency condition; recovery uses at most one SMS per episode with abandonment consent and the 24-hour gate, and every promotional SMS counts toward the caps.
- [ ] Subscription-unknown and open-service-case holds are specified; subscription and back-in-stock flows, where present, defer to native notices and name variant, consent scope and expiry.
- [ ] Offers appear only as references to approved facts; any new incentive is routed to approval.
- [ ] Any change to a working flow carries a test plan with assignment, window, metric and guardrails.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| decision.propose | autonomous | Agent (logged) |
| approval.request | autonomous | Agent (logged) |
| flow.build_draft | approved_plan | Covered by a decision approved by org admin, account lead, or client owner/approver |
| audience.prepare | approved_plan | Covered by a decision approved by org admin, account lead, or client owner/approver; new or detached audiences only |
| flow.change_live | explicit_approval | Org admin, account lead, or client owner/approver |
| discount.change | explicit_approval (locked) | Org admin, or client owner/approver |
| account.settings_change | explicit_approval (locked) | Org admin or client owner |

## Outputs and destination
- **draft_spec**: one flow spec per journey in the shape above, plus the cross-journey rules and recipient-state table. Goes to task output, then `build_draft` (flows only). Externally implemented mechanisms go as handoff requests to a named owner with the receipt required.
- **brief**: creative brief per message (purpose, audience, timing, product slot, offer reference, destination, fallback). Goes to creative.
- **analysis**: test readout after the follow-up window closes. Goes to task output and the Client Hub.

## Maintenance
The strategist lead reviews this skill every 90 days. Review early when the ESP changes Smart Sending, filter or holdout behavior, when a carrier or regulator changes SMS rules, or when a test result contradicts a default here.
