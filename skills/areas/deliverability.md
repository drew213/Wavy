---
key: area.deliverability
kind: area
area: deliverability
title: Deliverability, consent and list health
owner_role: analyst
version: 2
reviewed: 2026-10-09
review_every_days: 90
---
## Purpose
Keep the client's email in the inbox and every email and text legally sent to people who asked for it. Authentication, reputation, consent and unsubscribe handling are the floor every other area stands on: one complaint spike or consent failure can cut revenue from every flow at once, and SMS consent errors carry legal exposure.

## Client facts and rules
Look up each with `get_context`.

- `sending_domains` – the branded sending subdomain(s) and From addresses. Missing: `flag_insight`; DLV-05 is unknown.
- `dns_owner` – who can publish DNS records. Missing: ask the account lead; any DNS fix is blocked.
- `consent_matrix` – one row per recipient market (country, and state where rules differ) and channel: permitted consent basis (for example CAN-SPAM opt-out, CASL or GDPR consent, TCPA written consent), provider restrictions, disclosure version, effective date, retention obligation and approver. Missing row: no marketing to that market or channel; `flag_insight`. "Strictest market shipped to" is not executable.
- `sms_provider` and `sms_number_type` – platform and number (toll-free, 10DLC, short code) with registration status. Missing: SMS findings are unknown; do not recommend sends.
- `sms_consent_language` – the exact disclosure shown at capture. Missing: `flag_insight`; SMS consent is unverified.
- `consent_sources` – where each list's consent came from (form, checkout, import, other platform). Missing: treat unsourced records as not marketable.
- `regulated_category_limits` – category limits on channels and content. Missing: stop SMS planning. Some ESPs bar some categories from their SMS (for example, CBD and THC under Klaviyo's acceptable use policy).
- `sunset_policy` – the client's approved unengaged window and re-permission steps. Missing: propose the Wavy default with `propose_fact`.
- `incident_contacts` – who at the client approves a pause. Missing: escalate to the account lead before any incident occurs.

## Inputs and sources
- **Received message headers** from seed inboxes at Gmail and at least one of Microsoft or Yahoo: the only proof of aligned live mail. DNS records alone don't prove alignment or inbox placement.
- **DNS lookups** (SPF, DKIM selector, DMARC, PTR): trusted for what is published, not for what the ESP signs with.
- **ESP deliverability metrics** by month and per message: delivered, bounces (hard and soft), spam complaints, unsubscribes. Trap: the ESP sees feedback-loop complaints only; Gmail complaints show only in Google Postmaster Tools.
- **Google Postmaster Tools (daily user-reported spam rate), Yahoo sender data and Microsoft SNDS**: the only sources for provider-side rates, each on its own basis. Supplied as evidence; no access means "unavailable", never a pass.
- **Engagement by inbox provider** (DLV-04): opens are machine-affected; a diagnostic, not proof.
- **Form and list growth events**: trusted for bot-like spikes.
- **SMS platform reports and keyword logs**: trusted for opt-outs, failures and HELP/STOP replies.

## SOP
1. `get_context`. Confirm the facts above.
2. Authentication: check the branded domain is verified in the ESP (DLV-05) and read the published SPF, DKIM and DMARC records. From supplied received headers for a campaign and a flow message in each seed inbox, record SPF, DKIM and DMARC results. Evaluate DMARC alignment through SPF or DKIM; separately verify the applicable bulk-sender authentication requirements. Label branded aligned DKIM as a Wavy requirement where required, not as DMARC's only passing path (DLV-06). Check `List-Unsubscribe` and `List-Unsubscribe-Post` on marketing mail.
3. Reputation: score DLV-01 (ESP complaints), DLV-02 (bounces), DLV-03 (unsubscribes) and DLV-04 (provider engagement) by month and per message. Report Gmail's daily Postmaster spam rate, Yahoo's rate and SNDS separately on their own bases; otherwise record "unavailable".
4. Engagement policy: score CMP-03 (engaged targeting) and CMP-04 (ignore-unsubscribes off, Smart Sending on).
5. Sunset: score FLW-07. Confirm an unengaged segment, a re-permission path and a suppression step exist.
6. Bot and spam prevention: review sign-up spikes by source and hour, role and disposable addresses, gibberish names, and forms without bot protection.
7. Consent: map every list and capture source to a `consent_matrix` row. For email, confirm opt-out link, physical address and honest headers. For SMS, confirm capture disclosure, confirmation text, HELP and STOP replies, quiet hours and registration. Score SMS-01, SMS-02, SMS-03 and SMS-05.
8. Unsubscribe handling: cite supplied end-to-end receipts for one email unsubscribe and one SMS STOP on seed profiles, showing suppression on every platform. No receipt: mark not verified and save a test request naming the owner; never claim the test ran.
9. `record_check` (QA) or `save_output` the scorecard with value, verdict and evidence per check. `flag_insight` every fail. `request_approval` for any fix touching live sending, DNS or account settings, then `finish`; follow-up checks need a recorded task.

## Decision rules
- If headers show `dmarc=fail`, or branded aligned DKIM is missing where Wavy requires it, then hold audience expansion and new flow activations until fixed and re-tested. Say which failed: DMARC, or the Wavy DKIM requirement.
- **ESP complaint bands** (one contract shared with `reporting` and `campaign_production`, using the Klaviyo published bands in `reporting`): healthy below 0.01%, needs attention above 0.05%, per send and per month, with counts, denominator and data age. A rate between the bands is not healthy and is monitored. Above 0.05% triggers a high-severity investigation and an approval request to hold further affected sends. ESP rates never stand in for provider rates, and provider data never clears an ESP band.
- Monitor Gmail's user-reported spam rate daily in Postmaster Tools: Google recommends below 0.1% and avoiding 0.3% or higher. Yahoo's requirement is below 0.3% using its provider basis. Do not substitute an account-wide monthly ESP complaint rate. Unavailable provider data is not a pass. A Gmail day at or above 0.3%, or Yahoo at or above its limit, opens an incident.
- If bounces exceed 2% in a month or hard bounces exceed 0.5% (Wavy starting default), then find the list source and stop sending to it pending approval.
- If unsubscribes exceed 0.6% in a month (Wavy starting default), then audit the high-unsubscribe sends and propose a frequency cap for low tiers.
- If any campaign ignores unsubscribes, then it is a critical fail. Propose turning it off before any other work.
- If a sign-up source shows a sudden spike of role, disposable or gibberish addresses, then treat it as list bombing: propose bot protection or double opt-in on that form, and a suppression review.
- If a list has no consent source, then don't mail it. Purchased or rented lists are never mailed.
- If SMS consent language lacks brand, message type, frequency, "Msg & data rates may apply", HELP and STOP, or ties consent to purchase, then SMS sends stay off until fixed.
- If an opt-out appears on any platform, then it wins over any older consent. Native application and synchronization of a verified opt-out are automatic and never wait for `profile.suppress_delete` approval; without a consent tool, record the evidence and alert the responsible owner, never claiming consent was changed. A STOP to any of the client's numbers suppresses texts from all of them.
- **DMARC enforcement:** do not move to quarantine or reject solely because 2–4 weeks elapsed. Require an inventory of legitimate sending sources, aggregate-report coverage, aligned-mail evidence, a documented exception review, rollback instructions and the authorized DNS approval.
- **Incident response:** triggers are a provider spam-rate, bounce or unsubscribe threshold breach, a Postmaster reputation drop to Low or Bad, a blocklist listing, DMARC failure in headers, or any send to suppressed people. Then: (1) `flag_insight` as critical within the hour; (2) `request_approval` to pause discretionary campaigns and the affected flows (you never pause them yourself); (3) find the cause by source, segment and message; (4) propose the fix; (5) after approval, resume to the 30-day engaged tier first and widen as rates hold for two weeks.
- Escalate to the account lead for any DNS, consent or legal question. Wavy gives no legal advice; recommend the client's counsel review consent language.

### Thresholds
- **Gmail sender requirements** (Google's bulk-sender definition: close to 5,000+ messages a day to personal Gmail accounts): SPF and DKIM; DMARC published (p=none minimum) with the From domain aligned through SPF or DKIM; one-click unsubscribe on marketing mail, honored within 2 days; Postmaster spam rate below 0.1% recommended, never 0.3% or higher; valid forward and reverse DNS; TLS.
- **Yahoo sender requirements**: Yahoo's own authentication, one-click unsubscribe and spam-rate (below 0.3%) rules on its own basis. Google's bulk-sender definition does not apply to Yahoo. Microsoft applies similar SPF, DKIM and DMARC requirements to high-volume senders to its consumer inboxes.
- **CAN-SPAM Act**: no false or misleading headers or subject lines; identify the message as an ad where required; include a valid physical postal address; give a clear opt-out that works for at least 30 days after sending; honor opt-outs within 10 business days.
- **TCPA**: prior express written consent for marketing texts; consent cannot be a condition of purchase; no telephone solicitations before 8 a.m. or after 9 p.m. recipient local time, and stricter state rules apply. Honor revocation made by any reasonable means; FCC rules allow no more than 10 business days.
- **CTIA guidelines and carrier rules**: confirmation message on opt-in; HELP and STOP keywords must always work; registered numbers (10DLC or verified toll-free).
- **Wavy starting defaults** (ESP health bands, not provider rules): ESP complaints use the `reporting` bands above (published, not a Wavy default); bounces < 1% and hard < 0.3% pass; unsubscribes < 0.3% pass; every provider ≥ 70% of average engagement; branded aligned DKIM on every sending domain; sunset at no click, site visit or order for 120–180 days, then 2 re-permission emails, then suppress.

## Examples
### Good
"DLV-06 fail. A received checkout email at Gmail shows SPF passing for the ESP's bounce domain and `dkim=pass` for the ESP's shared domain; neither aligns with the From domain, so `dmarc=fail`. DNS has SPF and DMARC p=none published, which does not prove alignment. Hold: no new flow activations or audience widening. Fix: complete branded-domain DKIM, then re-test with a seed send. Owner: client DNS owner. Approval requested for the account settings change."
### Not good
"SPF, DKIM and DMARC records exist, so deliverability is fine." It fails because DNS alone doesn't prove the mail is signed and aligned, and it ignores complaint, bounce and provider data.

## Quality checklist
- [ ] Authentication verdicts cite received headers, name the path (SPF or DKIM) that aligned for DMARC, and label branded aligned DKIM as a Wavy requirement.
- [ ] Complaint, bounce and unsubscribe rates are given by month and per message with denominators; ESP complaint rates are scored on the shared `reporting` bands only.
- [ ] Gmail's daily Postmaster spam rate, Yahoo's rate and SNDS are reported on their own bases, or marked unavailable; no monthly ESP rate stands in for a provider rate.
- [ ] Each regulator or platform rule cited is named; every other threshold is labelled a Wavy starting default.
- [ ] Every list and capture source maps to a `consent_matrix` row, and the SMS disclosure's required elements are quoted as present or missing.
- [ ] Email unsubscribe and SMS STOP results cite supplied end-to-end receipts, or are marked not verified with a test request and owner.
- [ ] Any DMARC enforcement proposal has a source inventory, aggregate-report coverage and rollback plan, not elapsed time alone.
- [ ] Every proposed pause, discretionary suppression or cleanup, DNS or setting change has an approval request, not a direct action; verified opt-outs are shown as applied and synchronized automatically, or handed off with evidence, never held for approval.
- [ ] No personal data appears in the output.

## Owner and approvals
| Action | Level | Who decides |
|---|---|---|
| data.read | autonomous | Agent (logged) |
| analysis.run | autonomous | Agent (logged) |
| insight.create | autonomous | Agent (logged) |
| issue.flag | autonomous | Agent (logged) |
| approval.request | autonomous | Agent (logged) |
| audience.prepare | approved_plan | Covered by a decision approved by org admin, account lead, or client owner/approver |
| flow.change_live | explicit_approval | Org admin, account lead, or client owner/approver |
| campaign.publish | explicit_approval | Org admin, account lead, or client owner/approver |
| profile.suppress_delete | explicit_approval (locked) | Org admin or client owner |
| account.settings_change | explicit_approval (locked) | Org admin or client owner |

## Outputs and destination
- **analysis**: deliverability scorecard. One row per check ID: value, verdict, evidence, fix. Goes to task output.
- **report**: incident note with trigger, impact, cause, proposed fix and approvals needed. Goes to the account lead at once, and to the client only through approved `client.message.send`.
- **draft_spec**: sunset flow spec for `build_draft` kind `flow` under an approved plan; suppression steps and DNS changes as a handoff to the authorized owner.
- **consent matrix** (kind `analysis`): one row per market and channel, for `propose_fact`.

## Maintenance
The analyst lead reviews this skill every 90 days. Review early whenever Gmail, Yahoo, Microsoft, the FCC, CTIA or a carrier changes sender rules, or after any incident.
