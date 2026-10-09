---
key: area.list_growth
kind: area
area: list_growth
title: List growth and capture
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Turn store visitors into consented email and SMS subscribers who buy, and hand each one to the welcome journey with the promise they signed up for intact. Capture is the top of every lifecycle number: a weak or broken form starves every flow. The founder cares because a subscriber who never receives the promised offer, or was never really consented, costs money and trust.

## Client facts and rules
Look up each with `get_context` before proposing any form or offer.

- `approved_welcome_offer` – the one approved signup benefit, its value basis, eligibility and expiry. Missing: `flag_insight` and stop; never write an offer into copy.
- `offer_stacking_rules` – how the welcome offer combines with sitewide sales and other codes (discount classes, bases, order; see `offers`). Missing: block offer copy until checkout-tested.
- `first_order_eligibility` – whether existing customers can claim the welcome offer. Missing: assume first-time buyers only and flag it.
- `sms_provider`, `sms_consent_language` – where phone capture goes and the exact approved disclosure. Missing: email-only capture.
- `regulated_category_limits` – claim, channel and incentive limits. Missing: stop and flag.
- `markets_served` – consent regime by country or state. Missing: `flag_insight`; block capture copy for any market without a confirmed consent basis.
- `brand_voice_rules` – approved tone and banned words. Missing: write neutral draft copy and mark it for voice review.
- `form_pages_excluded` – pages where forms must never show (checkout, account, wholesale portal). Missing: exclude cart and checkout by default.
- `site_change_owner` – who implements and publishes forms (shared with `cro_onsite`). Missing: every form change is blocked at handoff.
- `back_in_stock_rules` – whether waitlist or restock alerts exist, channel, consent scope and alert expiry. Missing: no alert capture.
- `zero_party_questions` – any approved preference question at sign-up. Missing: propose one with `propose_fact`.

## Inputs and sources
`get_context` supplies standards and approved facts; it does not query the ESP, submit forms or run checkouts. Use the supplied exports and test receipts below; if one is absent, request it from a named owner and mark the item not verified. Check IDs (CAP-*, CRV-*, SMS-*) count only when the packet supplies each check's version, precondition, calculation, severity and threshold; otherwise the check is unknown.

- **Form inventory** with type, status, targeting and submit action: trusted for configuration. Form type may only appear in deep collection.
- **Form views and submits** by form, device and variant: trusted for submit rate. Trap: a submit is not confirmed consent.
- **Subscribed-to-email and SMS consent events** by month: trusted for gross growth. Trap: imports and suppressions move list size without these events (CAP-02).
- **Welcome flow entries and first-message receipts**: trusted for handoff. Only a received message proves the offer arrived.
- **Checkout test**: the only proof the offer applies at the stated value (CRV-05).
- **Orders, bounces, complaints and unsubscribes by `signup_source`**: trusted for subscriber quality.
- **Mobile render** of every live form on a real phone width: trusted for usability.

## SOP
1. `get_context`. Confirm the facts above. Stop if the offer or regulated limits are missing.
2. Inventory live forms. Score CAP-01 (live popup and submit rate), CAP-02 (net growth), CAP-03 (every form feeds a list with a live welcome) and SMS-01 (phone capture with consent).
3. From supplied test receipts for each live form, desktop and mobile, confirm the test sign-up, welcome entry, first received message and checkout redemption. If a receipt is missing, save a test request naming the tester and the release gate; the item stays not verified.
4. Review targeting and timing: pages, devices, triggers, frequency, and suppression of existing subscribers and customers.
5. Review copy and design: headline states the benefit, one clear field per step, visible close, consent text near the button, terms and privacy links.
6. Check source tracking: every form writes `signup_source` (form ID, page type, variant) to the profile.
7. Compute subscriber quality by source over 30 and 60 days (Decision rules).
8. Draft the capture plan as `draft_spec`: exact form steps, fields, disclosures, source tracking, targeting, close/skip behavior, destination and welcome handoff. Do not call `build_draft` for a form; it supports only campaigns and flows. Route unpublished form implementation and live publication to `site_change_owner` with separate acceptance evidence.
9. `save_output` the audit as an analysis and the plan as a draft_spec. `flag_insight` every broken handoff or failed offer. `request_approval` with `discount.change` for any offer change; form publication has no catalog action and stays with the form owner. `finish`.

## Decision rules
- **Capture sequence (Wavy starting default):** step 1 email; step 2 phone with the approved SMS disclosure, skippable; optional step 3 one preference question. The email is saved even if the phone step is skipped.
- **Timing and targeting (Wavy starting default):** desktop triggers on exit intent or after 8–15 seconds or 40–50% scroll, whichever comes first; mobile triggers on time or scroll, because exit intent is unreliable on touch. Show once per session; after a close, wait 7 days. Never show to known subscribers, on cart or checkout, or on `form_pages_excluded`.
- **Mobile experience:** use a bottom sheet or a form that leaves page content visible on landing, with an obvious close and tap targets at least 44 points (Apple Human Interface Guidelines). Google Search's intrusive-interstitial guidance penalizes popups that cover content right after a mobile visitor lands from search.
- If any live form feeds a list with no live welcome, then it is a critical fail (CAP-03). Fix it before any design work.
- If the offer fails or combines unexpectedly at checkout, then the copy doesn't ship (CRV-05). Combined depth depends on discount classes and bases: sequential discounts compound, while two combinable percentage order discounts on the same subtotal can add. Unknown mechanics block a combined-saving claim.
- If SMS capture lacks the brand, message type, frequency, "Msg & data rates may apply", HELP and STOP, a terms link, or implies consent is required to buy, then remove the phone step until fixed (TCPA and CTIA). If SMS cart or checkout recovery is planned, capture must also record the abandonment-specific consent and disclosure and support double opt-in (`sms`). A promotional welcome SMS counts toward the SMS caps.
- If an existing customer submits a first-order offer form, then route them to a welcome without the code, per `first_order_eligibility`.
- If a source shows high submits but poor quality, then judge it by quality, not volume. Quality signals (Wavy starting default): 30-day click rate, 60-day first-order rate, hard-bounce rate, complaints, and unsubscribes within 7 days. A source in the bottom quartile on two signals gets bot protection or double opt-in proposed.
- If sign-ups spike from one source with role, disposable or gibberish addresses, then treat it as bot traffic and escalate to deliverability.
- **Testing plan (Wavy starting default):** one variable per test (offer framing, headline, trigger, or step order). Run full weeks for at least 14 days, with at least 1,000 views and 100 submits per variant. The primary metric is consented submits per view; the guardrail is 60-day first-order rate. Never test a different offer value without `discount.change` approval.
- **Back-in-stock and waitlist capture:** record the exact variant, channel, request date and source. An alert request is consent only for that alert unless the form disclosed and collected marketing consent. Set an alert expiry; never promise reserved stock the store doesn't hold.
- New offers, offer values, eligibility or expiry changes are `discount.change`. Never propose them as copy edits.

### Thresholds
- **Wavy starting defaults** (check library): popup submit rate ≥ 3% pass, 1.5–3% warn, < 1.5% fail (CAP-01). Net email growth positive in each of the last 3 months and gross subscribes ≥ 80% of the prior year (CAP-02).
- **TCPA**: prior express written consent for marketing texts, not a condition of purchase. **CTIA**: confirmation message with HELP and STOP. **CAN-SPAM** applies to every email sent to new subscribers.

## Examples
### Good
"Mobile popup converts at 1.2% (fail, CAP-01) against 3.4% on desktop. The mobile version covers the page at load and its close button is 24 points. The SMS step's disclosure lacks frequency and HELP. Test sign-up entered welcome in 2 minutes and the code applied at checkout. Plan: bottom-sheet mobile form after 10 seconds or 50% scroll, 44-point close, corrected SMS disclosure. Test: headline A vs B, 14 days, primary metric consented submits per view, guardrail 60-day first-order rate."
### Not good
"Raise the popup offer to a bigger discount and show it immediately on every page to double sign-ups." It fails because it changes the offer without approval, ignores mobile and checkout exclusions, and measures volume instead of subscriber quality.

## Quality checklist
- [ ] Every live form is listed with its list, welcome flow and a supplied test sign-up receipt, or "not verified" with the tester named.
- [ ] A supplied checkout-redemption receipt for the welcome offer is attached, or offer copy is blocked with the release gate named.
- [ ] Offer copy references the approved welcome offer and nothing else.
- [ ] Mobile and desktop are assessed separately, with submit rates for each.
- [ ] Any SMS step lists the required disclosure elements as present or missing.
- [ ] Every form writes a source property, and quality is reported by source.
- [ ] The test plan names one variable, duration, sample minimum, primary metric and guardrail.
- [ ] Thresholds are labelled Wavy starting default or by their named rule.
- [ ] No form is built through `build_draft` or reported published; form work is a draft spec handed to the named form owner.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed |
| decision.propose | autonomous | Agent (logged) |
| work.implement_nonlive | approved_plan | Covered by an approved decision; not a form tool, the named form owner implements forms |
| flow.build_draft | approved_plan | Covered by a decision approved by org admin, account lead, or client owner/approver (welcome flow drafts only) |
| flow.change_live | explicit_approval | Org admin, account lead, or client owner/approver |
| discount.change | explicit_approval (locked) | Org admin, or client owner/approver |
| account.settings_change | explicit_approval (locked) | Org admin or client owner |

## Outputs and destination
- **analysis**: capture audit. Per form: status, targeting, submit rate by device, list and welcome mapping, test result, quality by source. Goes to task output.
- **draft_spec**: form and capture-sequence spec with targeting, steps, fields, disclosures, source property and test plan. Goes to `site_change_owner` as a handoff request requiring implementation and acceptance receipts.
- **copy**: headline, body, button, consent text and success message per step, with offer as a reference to the approved fact. Goes to voice review, then the Client Hub.

## Maintenance
The strategist lead reviews this skill every 90 days. Review early when the ESP changes form features, when Google, Apple, the FCC or CTIA changes guidance, or when two clients' tests contradict a default here.
