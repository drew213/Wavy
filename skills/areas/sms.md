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
SMS is the most personal and most expensive channel a brand has. Used for high-intent, time-sensitive moments it converts; used as a second inbox it burns consent, money and carrier trust. This area decides who is texted, when, how often and with what words, inside federal, carrier and platform rules, and proves each text earns more than it costs.

## Client facts and rules
Look up each fact with `get_context` for `sms`, then `deliverability`, `offers` and `voice_copy`.

- `sms_platform` – the sending platform (native ESP SMS or a third-party tool) and whether consent and events sync both ways. Missing: stop; `flag_insight`.
- `sending_number_type`, `registration_status` – toll-free, 10DLC or short code, and whether carrier registration or verification is approved. Missing: stop. Unregistered traffic is filtered or blocked by carriers.
- `regulated_category` – especially CBD/THC, alcohol, tobacco, firearms. Missing: stop. Klaviyo's SMS policy does not allow CBD/THC messaging, and carriers restrict these categories.
- `consent_sources` – which forms and keywords collected SMS marketing consent, with the disclosure text used. Missing: no campaign sends; `flag_insight`.
- `transactional_sms_consent` – whether order and shipping texts run on separate consent. Missing: no transactional texts.
- `sms_frequency_cap` – client-approved allowance. Missing: use the Wavy starting default below and `propose_fact`.
- `quiet_hours` – allowed sending window in recipient local time. Missing: the Wavy default below.
- `cost_per_segment`, `mms_multiplier`, `carrier_fees` – current billing. Missing: `flag_insight`; report revenue without cost and label it "cost unverified".
- `reply_owner` – who answers inbound replies and within what time. Missing: no copy that invites questions.
- `help_response`, `opt_out_confirmation` – current platform auto-replies. Missing: `flag_insight` (critical) before any send.
- `cutover_approver` – who approves platform changes or migrations. Missing: client owner.

Common exceptions: marketplace buyers and historical purchasers are not SMS-consented; one phone number has one sending owner at a time during a platform overlap; a newer opt-out always beats an older consent import.

## Inputs and sources
- **SMS platform reports**: trusted for sends, deliveries, failures, clicks and opt-outs. Traps: platform revenue is usually last-touch, counts any order after a text and overlaps email attribution; read it as an upper bound.
- **Consent records**: trusted only with source, timestamp and disclosure text. Subscriber counts across two platforms rarely match; reconcile before scaling.
- **Billing**: the only source for cost. Encoding and MMS change cost per message; carrier fees change.
- **Live flow definitions**: triggers, filters, exits and timing on both platforms, to find duplicate journeys.

## SOP
1. Trigger: an SMS campaign row, a flow text request, or a program review. Run `get_context` for `sms`, `deliverability`, `offers` and `voice_copy`.
2. Confirm platform, registration, category, consent sources, help and opt-out replies. If any is missing, `flag_insight` and `finish`.
3. Decide campaign versus triggered use (Decision rules). Triggered journeys come first.
4. Define eligibility: verified SMS marketing consent for this brand, not suppressed on any platform, no open service case, plus journey filters (no purchase since trigger, no checkout started for cart and browse).
5. Check pressure for every recipient tier over a rolling 168 hours and 24 hours, including flows, campaigns and email cooldown.
6. Write copy: brand prefix, plain GSM-7 characters, one link or one reply prompt, offer only from the offer spec. Count characters and segments with the worst-case merge values filled.
7. Set timing inside quiet hours in recipient local time.
8. Build in draft: `build_draft` kind `flow` (or kind `campaign`) with the full spec. For audiences, use `audience.prepare`.
9. QA: preview on internal phones across carriers; check prefix, encoding, segments, link, opt-out language, HELP and STOP replies.
10. `save_output` kind `copy` (messages with character and segment counts) and kind `draft_spec` (journey logic).
11. `request_approval` with `campaign.publish` for a campaign or `flow.change_live` for a flow, with the exact payload. For quiet-hour, sender or compliance settings use `account.settings_change`; for removing numbers use `profile.suppress_delete`. `finish`.
12. After launch: check first live sends; review at 48 hours and 14 days. `save_output` kind `report` with opt-out, failure, click, cost and revenue net of cost per text.

## Decision rules
- **Federal and carrier rules (TCPA, FCC, CTIA):** marketing texts need prior express written consent (TCPA, 47 CFR 64.1200). Honour opt-outs sent by any reasonable means, including STOP, END, CANCEL, UNSUBSCRIBE, QUIT and plain-language requests, within 10 business days (FCC 2024 consent-revocation rule); platforms usually do it instantly, and Wavy treats any opt-out as immediate. The opt-in confirmation names the program, message frequency, "Msg & data rates may apply", and HELP and STOP instructions (CTIA Messaging Principles and Best Practices). HELP replies give the brand name and a support contact.
- **Quiet hours:** TCPA prohibits telephone solicitations before 8 AM and after 9 PM recipient local time; some state laws are stricter (for example Florida's 8 AM–8 PM). Wavy starting default: marketing texts send only 12:00–20:00 recipient local time; queued, never dropped silently.
- **Category:** if the client sells CBD or THC products, then do not use Klaviyo SMS (platform policy). Use only a provider that permits the category, after legal review.
- **Frequency, Wavy starting default:** at most 2 counted marketing texts per rolling 168 hours and 1 per 24 hours, across campaigns, recovery, review and refill texts. The welcome series (within 5 days of signup, once per profile) is outside the allowance but obeys quiet hours. Order-service texts are separate and never carry promotion. New subscribers get no campaign texts until the welcome series ends.
- **Priority when the allowance is full:** checkout, then cart, then browse, then other journeys and campaigns. A purchase stops pending recovery texts.
- **Abandonment, Wavy starting default:** one abandonment text per event, and at most one recovery text per person in any 48 hours across browse, cart and checkout.
- **Coordination with email:** a follow-up text sends only if its preceding email actually sent and the person neither purchased nor clicked it. Opens do not count: machine opens make them unreliable. For campaigns, keep 12 hours between a same-topic email and text (Wavy starting default); journey follow-ups use the journey's own delay. One channel owner per journey; never run the same journey on two platforms.
- **Campaign versus triggered:** campaigns go to segments that asked (drop lists, waitlists, engaged clickers). At most one full-list text per week (Wavy starting default).
- **Copy:** open first-touch texts with the brand prefix; use GSM-7 only, because one em dash, curly quote or emoji switches the whole message to UCS-2 (one segment holds 160 GSM-7 or 70 UCS-2 characters; split messages hold 153 or 67 per segment, per GSM 03.38 and 3GPP TS 23.040). Wavy starting default: under 140 characters including the link. One action per text. The offer appears once per flow and never escalates. No health claims (see `voice_copy`).
- **Links:** use a branded short domain. Public shared URL shorteners increase carrier filtering (CTIA guidance).
- **Performance, Wavy starting defaults:** opt-out rate per send under 1.5% passes, 1.5–3% warns, over 3% fails; failure rate under 3% passes, over 6% fails. Every new or changed journey ships with a holdout or split, because SMS attribution is last-touch.
- **Migration or overlap:** reconcile opt-outs across platforms before every send and immediately before cutover. A STOP to either number suppresses both. Contradictory membership is held from both until reconciled. Every cutover needs client approval.
- If cost per order exceeds the contribution per order, then the text fails, whatever its attributed revenue.

## Examples
### Good
"Cart text, sent 2 hours after add-to-cart if no checkout, no purchase, no click on the cart email, and no recovery text in 48 hours. 'BRAND: Your cart is saved and ready when you are: [cart link]' About 75 characters with a branded link filled in, GSM-7, 1 segment, sends 12:00–20:00 local. Ships with a holdout."

### Not good
"BRAND: Don't miss out!!! 🔥 Our clinically-backed formula is 25% off — today only… Shop now: bit.ly/x Reply STOP" sent to every phone number on file at 9:30 PM. The emoji and dash force UCS-2 (3 segments), the claim is unapproved, "today only" may be false, the public shortener invites filtering, it ignores quiet hours, and it includes buyers who never gave SMS consent.

## Quality checklist
- [ ] The audience is limited to verified SMS marketing consent for this brand, with suppressions from every platform applied.
- [ ] Every first-touch text opens with the brand prefix, and the opt-in confirmation has program name, frequency, rates, HELP and STOP.
- [ ] Each message shows its character count and segment count with worst-case merge values, and uses GSM-7 only.
- [ ] Each text has one action and at most one branded link; no public shortener.
- [ ] Send windows are within quiet hours in recipient local time.
- [ ] The frequency check over rolling 168 hours and 24 hours is shown, including flows and campaigns.
- [ ] Follow-up texts depend on the preceding email having sent and on no purchase or click, not opens.
- [ ] Transactional texts carry no promotion; no health or clinical claims appear.
- [ ] Cost per text and per order are reported, or labelled "cost unverified".

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
- **SMS copy** (kind `copy`): each message with ID, trigger, timing, body, character and segment counts, link placeholder and why it exists.
- **Journey spec** (kind `draft_spec`): triggers, filters, exits, priority, caps and holdout.
- **Program report** (kind `report`): opt-out, failure, click, cost and revenue net of cost, per message and per journey, with attribution labelled.
- Client-facing summaries go to the Client Hub.

## Maintenance
The strategist lead reviews this skill every 90 days. Review early when the FCC, a state legislature or CTIA changes texting rules, a carrier changes registration or filtering, Klaviyo or the SMS platform changes policy or pricing, opt-out rate fails on any send, or a client migrates SMS platforms.
