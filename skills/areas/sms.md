---
key: area.sms
kind: area
area: sms
title: SMS program
owner_role: strategist
version: 1
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
SMS is the most personal and most expensive channel a brand has. Used for high-intent, time-sensitive moments it converts; used as a second inbox it burns consent, money and carrier trust. This area decides who is texted, when, how often and with what words, inside legal, carrier and platform rules, and proves each text earns more than it costs.

## Client facts and rules
Look up each fact with `get_context` for `sms`, then `deliverability`, `offers` and `voice_copy`.

- `sms_platform` – the sending platform and whether consent and events sync both ways. Missing: stop; `flag_insight`.
- `sending_number_type`, `registration_status` – toll-free, 10DLC or short code, and whether carrier registration or verification is approved. Missing: stop. Unregistered traffic is filtered or blocked.
- `regulated_category` – especially CBD/THC, alcohol, tobacco, firearms. Missing: stop. Klaviyo's SMS policy bars CBD/THC messaging and carriers restrict these categories; use only a provider that permits the category, after legal review.
- `consent_matrix` – one row per recipient market/state and channel: permitted consent basis, provider restrictions, disclosure version, effective date, retention obligation and approver (shared with `deliverability`). Missing row: no texts to that market. "Strictest market shipped to" is not an executable rule.
- `consent_sources` – which forms and keywords collected SMS marketing consent, with the disclosure text. Missing: no campaign sends; `flag_insight`.
- `abandonment_consent` – whether cart/checkout recovery texts have abandonment-specific consent and disclosures, collected with double opt-in, per source. Missing: no cart or checkout recovery texts.
- `transactional_sms_consent` – whether order and shipping texts run on separate consent. Missing: no transactional texts.
- `sms_frequency_cap` – client-approved allowance. Missing: the Wavy default below; `propose_fact`.
- `quiet_hours`, `recipient_location_source` – approved market/state/day rules and where recipient location and timezone come from, with fallback. Missing: the Wavy window below; unknown location blocks scheduling.
- `cost_per_segment`, `mms_multiplier`, `carrier_fees` – current billing. Missing: `flag_insight`; label revenue "cost unverified".
- `help_response`, `opt_out_confirmation` – current platform auto-replies. Missing: `flag_insight` (critical) before any send.
- `cutover_approver` – who approves platform changes or migrations. Missing: client owner.

Common exceptions: marketplace buyers and historical purchasers are not SMS-consented; a newer opt-out always beats an older consent import. With no `reply_owner`, write no copy that invites questions.

## Inputs and sources
- **SMS platform reports**: trusted for sends, deliveries, failures, clicks and opt-outs. Trap: platform revenue is usually last-touch and overlaps email attribution; read it as an upper bound.
- **Consent records**: trusted only with source, timestamp, disclosure text and opt-in method. Counts across two platforms rarely match; reconcile before scaling.
- **Billing and the provider's segment calculator**: the only sources for cost and billed segments. Encoding, link rewriting, MMS and sender type change both.

## SOP
1. Trigger: a campaign row, flow text request or program review. Run `get_context` for `sms`, `deliverability`, `offers` and `voice_copy`. Note the task phase; check only what that phase can evidence.
2. Confirm platform, registration, category, consent matrix row, consent sources, help and opt-out replies. If any is missing, `flag_insight` and `finish`.
3. Decide campaign versus triggered use (Decision rules). Triggered journeys come first.
4. Define eligibility: verified SMS marketing consent for this brand and market, not suppressed on any platform, no open service case, plus journey filters. Cart and checkout recovery also need `abandonment_consent`.
5. Check pressure per person over rolling 24 and 168 hours, counting every promotional text (welcome included), flows, campaigns and email cooldown.
6. Write copy: brand prefix, one link or one reply prompt, offer only from the offer spec. Count the rendered payload (Decision rules).
7. Set timing inside the quiet-hours intersection, with each recovery text's deadline and expiry.
8. Build in draft: `build_draft` kind `flow` or `campaign` with the full spec. `build_draft` does not create segments: save audience rules as a `draft_spec` and hand creation to the authorized executor under `audience.prepare`.
9. QA from supplied receipts (internal phones across carriers, provider segment count, HELP and STOP replies). Missing receipts: mark pending with owner; never claim a test ran.
10. `save_output` kind `copy` (messages with encoding and segment counts) and kind `draft_spec` (journey logic).
11. `request_approval` with `campaign.publish` or `flow.change_live` and the exact payload; `account.settings_change` for quiet-hour, sender or compliance settings; `profile.suppress_delete` for removing numbers. `finish`.
12. Post-launch review is a new execution: check first live sends, then review at 48 hours and 14 days. `save_output` kind `report` with opt-out, failure, click, cost and revenue net of cost.

## Decision rules
- **Federal and carrier rules (TCPA, FCC, CTIA):** marketing texts need prior express written consent (TCPA, 47 CFR 64.1200). Honour opt-outs by any reasonable means (STOP, END, CANCEL, UNSUBSCRIBE, QUIT, plain language) within 10 business days (FCC 2024 revocation rule); Wavy treats any opt-out as immediate. The opt-in confirmation names the program, frequency, "Msg & data rates may apply", HELP and STOP (CTIA). HELP replies give brand name and support contact.
- **Quiet hours:** send only in the intersection of approved market/state/day-of-week rules, provider restrictions and the Wavy 12:00–20:00 recipient-local starting window, end exclusive. TCPA bars solicitations before 8 AM and after 9 PM local time; some states are stricter (for example Florida, 8 AM–8 PM). Record the location/timezone source and fallback. Unknown location blocks scheduling unless an approved safe overlap is shown. When a queued text becomes eligible, recheck consent, purchase, stock, offer expiry and episode deadline; delayed texts are not guaranteed to send.
- **Frequency, Wavy starting default:** count every promotional SMS, including promotional welcome messages, toward one per rolling 24 hours, two per rolling 168 hours and any approved cross-channel `contact_cap_policy`. Only genuine non-promotional confirmation and service texts are separate, and they never carry promotion. An accepted welcome benefit may have priority but still counts; never exempt a five-day welcome sequence.
- **Priority when the allowance is full:** checkout, then cart, then browse, then other journeys and campaigns. A purchase stops pending recovery texts.
- **U.S. cart/checkout recovery gate (Wavy gate, not settled law):** require documented abandonment-specific consent and disclosures and double opt-in; allow one recovery SMS per episode. Provider guidance conflicts: Klaviyo's detailed help says 48 hours after abandonment, its 2026 compliance guide says 24 hours. Until the provider confirms, the stricter 24 hours is the Wavy deadline. A quiet-hour delay must not push delivery past it: expire and log the text, never send late. Browse follows its own verified rules.
- **Coordination with email:** a follow-up text sends only if its preceding email actually sent and the person neither purchased nor clicked it. Opens do not count. Campaigns keep 12 hours between a same-topic email and text (Wavy starting default). One channel owner per journey; never run the same journey on two platforms.
- **Campaign versus triggered:** campaigns go to segments that asked (drop lists, waitlists, engaged clickers). At most one full-list text per week (Wavy starting default).
- **Segments:** count the rendered payload after link rewriting, required compliance additions and bounded worst-case merges, using the actual provider/sender calculation. Record encoding, encoding units, segments and cost basis. Placeholder-only counts are provisional. Multipart limits vary by route (Twilio documents 152/66 for U.S./Canadian toll-free), so never assume 153/67. Prefer GSM-7 when it keeps the approved meaning; Unicode needs an approved message budget, not a ban.
- **Copy:** open first-touch texts with the brand prefix. Wavy starting default: under 140 rendered characters including the link. One action per text. The offer appears once per flow and never escalates. No unapproved claims (see `voice_copy`).
- **Links:** use a branded short domain. Public shared shorteners increase carrier filtering (CTIA guidance).
- **Performance, Wavy starting defaults:** opt-out per send under 1.5% passes, 1.5–3% warns, over 3% fails; failure rate under 3% passes, over 6% fails. Every new or changed journey ships with a holdout or split.
- **Migration or overlap:** reconcile opt-outs across platforms before every send and immediately before cutover. A STOP to either number suppresses both. Contradictory membership is held from both until reconciled. Every cutover needs client approval.
- If cost per order exceeds contribution per order, the text fails, whatever its attributed revenue.

## Examples
### Good
"Cart text, journey spec v2. Eligible: double opt-in with the abandonment disclosure, no checkout, no purchase, no click on the cart email. Sends 2 hours after abandonment inside the quiet-hours intersection; expires unsent 24 hours after abandonment. One per episode. 'BRAND: Your cart is saved and ready when you are: [cart link]' Provisional: GSM-7, 1 segment with placeholder link; final count pending the provider calculation on the rendered build. Ships with a holdout."

### Not good
"BRAND: Don't miss out!!! 🔥 Our clinically-backed formula is 25% off — today only… Shop now: bit.ly/x Reply STOP" sent to every phone number on file at 9:30 PM. The emoji and dash force Unicode, which raises segments and cost; the claim is unapproved; "today only" may be false; the public shortener invites filtering; it ignores quiet hours; and it includes buyers who never gave SMS consent.

## Quality checklist
- [ ] The output declares its phase (copy, journey spec, draft build, preflight or post-launch review) and version; checks for later phases are marked pending with owner and required evidence, never passed.
- [ ] The audience is limited to verified SMS marketing consent for this brand under a `consent_matrix` row for each market, with suppressions from every platform applied.
- [ ] Cart and checkout recovery texts require abandonment-specific consent and double opt-in, allow one text per episode, and expire at the 24-hour Wavy deadline instead of sending late.
- [ ] Every first-touch text opens with the brand prefix, and the opt-in confirmation has program name, frequency, rates, HELP and STOP.
- [ ] Each message records encoding, encoding units and segments from the rendered payload using the provider calculation, or is labelled provisional.
- [ ] Each text has one action and at most one branded link; no public shortener.
- [ ] Send windows use the quiet-hours intersection with the location source and fallback stated; unknown location blocks scheduling.
- [ ] The rolling 24- and 168-hour check counts every promotional text, including welcome texts, flows and campaigns.
- [ ] Follow-up texts depend on the preceding email having sent and on no purchase or click, not opens; service texts carry no promotion.
- [ ] A post-launch review reports cost per text and per order, or labels them "cost unverified".

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| draft.create | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| fact.propose | autonomous | Agent (logged); stays proposed until approved |
| approval.request | autonomous | Agent (logged) |
| audience.prepare | approved_plan | Only inside an approved decision |
| flow.build_draft | approved_plan | Only inside an approved decision |
| campaign.build_draft | approved_plan | Only inside an approved decision |
| campaign.publish | explicit_approval | Org admin, account lead, or client owner/approver |
| flow.change_live | explicit_approval | Org admin, account lead, or client owner/approver |
| discount.change | explicit_approval | Org admin or client owner/approver; locked |
| profile.suppress_delete | explicit_approval | Org admin or client owner; locked |
| account.settings_change | explicit_approval | Org admin or client owner; locked |

## Outputs and destination
- **SMS copy** (kind `copy`): each message with ID, trigger, timing, deadline and expiry, body, encoding, segment count and its basis (provisional or provider-calculated), link placeholder and purpose.
- **Journey spec** (kind `draft_spec`): triggers, consent gates, filters, exits, priority, caps, quiet-hours rule and holdout.
- **Program report** (kind `report`): opt-out, failure, click, cost and revenue net of cost, per message and journey, attribution labelled.
- Client-facing summaries go to the Client Hub.

## Maintenance
The strategist lead reviews this skill every 90 days. Review early when the FCC, a state legislature or CTIA changes texting rules, a carrier changes registration or filtering, the SMS provider confirms or changes its recovery deadline, policy or pricing, opt-out rate fails on any send, or a client migrates SMS platforms.
