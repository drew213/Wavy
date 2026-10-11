---
id: strategy
order: 6
title: Write the strategy
owner: Strategist
tracks: snapshot, deep
time_snapshot: 45 min
time_deep: 5 h
summary: Turn the scorecard and sized levers into a short, specific plan a designer and an email marketer could build tomorrow.
inputs: Scorecard, opportunity table, intake answers
outputs: Draft report in the track's template, claim ledger rows for every number
exit: The draft follows the template, every number has a ledger row, and the top recommendations are specific enough to build.
---
## What a good audit says

1. **What's broken**, with the number, in plain language. Example shape: "Your welcome series has been off since March. Every subscriber since then got nothing." (Use the account's real count.)
2. **What it's worth**, as a range with its inputs.
3. **What to do first**, ordered by value and effort, each with an owner and a done-when.
4. **What we couldn't see**, and what the client needs to send for us to see it.

Generic best practice scores zero. Every recommendation must cite this account's data.

## Steps

1. Build the analyst packet: `node analyst/build-packet.js <slug>`. It copies a sanitized version of the outputs plus the brief.
2. If an AI drafts the narrative, it gets the packet only, never raw exports or the key. Check rule 10 before sending client files to an outside AI service.
3. Write or edit to the template:
   - **Snapshot**: one-page scorecard, top 5 fixes with ranges, "what a Deep audit adds", next step.
   - **Deep**: summary, scorecard, sized top-10 roadmap, full findings by area, what we couldn't see, appendix.
4. Add a claim-ledger row for every number the client will read (`ledger_add.py` pattern from the Brand C/Brand D package).
5. Read it as the founder would. Cut anything that doesn't change a decision.

## Checklist

- [ ] Analyst packet built from sanitized outputs <!-- id:strategy-packet tracks:snapshot,deep -->
- [ ] Draft follows the track template <!-- id:strategy-template tracks:snapshot,deep -->
- [ ] Top recommendations each have owner, effort and done-when <!-- id:strategy-specific tracks:snapshot,deep -->
- [ ] Every client-facing number has a claim-ledger row <!-- id:strategy-ledger tracks:deep -->
- [ ] "What we couldn't see" section lists missing data and how to get it <!-- id:strategy-gaps tracks:snapshot,deep -->
- [ ] Regulated-category limits from intake respected (claims, SMS) <!-- id:strategy-regulated tracks:snapshot,deep -->

## Guardrails

- No claim the data doesn't support. Instrumentation footprints don't prove another agency is active; prefixes in names don't prove ownership.
- Keep the client's subscription and service flows intact in every recommendation.
- For CBD/THC brands, Klaviyo SMS isn't an option under its acceptable use policy. Recommend a compliant provider or email only.

## Tooling

| Tool | Status | Where |
|---|---|---|
| Analyst packet builder | Exists | `analyst/build-packet.js` |
| Brief template | Exists, hardcoded to Oct 2 / Q4 | `analyst/BRIEF.template.md`; parameterize in R-403 |
| Claim ledger | Exists (Brand C/Brand D) | `client-dashboards/brand-c-brand-d/ledger/` |
| Snapshot and Deep report templates | v0.4 (R-401, R-402) | |
