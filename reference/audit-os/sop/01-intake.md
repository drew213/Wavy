---
id: intake
order: 1
title: Intake
owner: Account lead
tracks: snapshot, deep
time_snapshot: 20 min
time_deep: 60 min
summary: Agree what we're auditing, why, and by when, and learn the facts the data can't tell us.
inputs: Signed audit agreement (Deep) or a yes on the call (Snapshot)
outputs: Tracker entry, completed questionnaire, audit slug
exit: Questionnaire answered for the track's required fields, and the client knows exactly how to create the key.
---
## Steps

1. Create the audit in the Audit OS tracker. Pick the track. The slug is `<brand>-<yyyy-mm-dd>` in the start date, e.g. `brand-d-2026-10-07`.
2. Send the intake questionnaire (`templates/intake-questionnaire.md`). Snapshot needs section A only; Deep needs all of it.
3. On the kickoff call, confirm:
   - the store platform (Shopify, WooCommerce, other) and any **past** platforms, because old metrics linger in Klaviyo
   - every messaging tool in use: Klaviyo, Attentive, OneText, Postscript, Alpine IQ
   - subscription app (Recharge, Loop, Skio, none) and whether its events reach Klaviyo
   - the goal in their words, with a number if they have one
   - regulated-category limits (CBD/THC, supplements, claims), since these change what we can recommend
4. Send the key instructions from the Access SOP. Don't ask for login credentials.
5. Book the readout before the work starts: within 48 h for Snapshot, within 5 working days for Deep.

## Checklist

- [ ] Audit created in the tracker with track, slug and readout date <!-- id:intake-created tracks:snapshot,deep -->
- [ ] Questionnaire section A answered (platform, tools, goal) <!-- id:intake-form-a tracks:snapshot,deep -->
- [ ] Questionnaire sections B–D answered (margins, offers, subscription, ops) <!-- id:intake-form-full tracks:deep -->
- [ ] Past store platforms and SMS tools recorded <!-- id:intake-platform-history tracks:snapshot,deep -->
- [ ] Regulated-category limits recorded, or "none" <!-- id:intake-regulated tracks:snapshot,deep -->
- [ ] Key instructions sent to the client <!-- id:intake-key-instructions tracks:snapshot,deep -->
- [ ] Readout booked <!-- id:intake-readout-booked tracks:snapshot,deep -->

## Guardrails

- Don't promise outcomes or numbers on the intake call. The audit sizes them.
- Margins and offer rules come from the client, in writing. Never infer a margin.
- If they use an ESP other than Klaviyo, stop and tell the audit lead: the collectors are Klaviyo-only.

## Tooling

| Tool | Status | Where |
|---|---|---|
| Intake questionnaire | Built | `templates/intake-questionnaire.md` |
| Tracker | Built | Audit OS page, Pipeline tab |
