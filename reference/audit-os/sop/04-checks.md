---
id: checks
order: 4
title: Run the checks
owner: Audit lead
tracks: snapshot, deep
time_snapshot: 90 min
time_deep: 4 h
summary: Score the account against the standard check library, so every audit asks the same questions and produces a comparable scorecard.
inputs: Completed collection, pinned source contract
outputs: "`scorecard.json` (every check with verdict, value, evidence path), rendered-email review notes"
exit: Every check for the track has a verdict (pass, warn, fail or n/a with reason), and every fail has an evidence path.
---
## Steps

1. Open the Checks tab (or `checks/check-library.json`). Filter to the audit's track.
2. For each check, record:
   - **value**: the measured number, or the presence/absence fact
   - **verdict**: pass, warn, fail, or n/a (with the reason, such as "no SMS program")
   - **evidence**: the file and field it came from, e.g. `account-totals.json → flowRevenue.share`
3. Run `data-available` checks from the collector files. Until the v0.3 engine exists, use `jq` or a short script, and save the script next to the scorecard so QA can rerun it.
4. Do the `manual` checks by hand: send yourself the welcome flow, click every link in the top 3 flows, try the signup offer at checkout, and read the received headers for DMARC alignment.
5. Look at the rendered emails (`flow-email-renders/`) before scoring any creative check.
6. Write down anything important that no check covers. It's a candidate for the next library version.

## How to read the thresholds

Every threshold is a **Wavy starting default**, not an industry benchmark. They get recalibrated once we have 5+ audits (R-602). When a value sits close to a threshold, say so rather than leaning on the verdict.

## Checklist

- [ ] Every Snapshot-track check scored <!-- id:checks-snapshot-scored tracks:snapshot,deep -->
- [ ] Every Deep-only check scored <!-- id:checks-deep-scored tracks:deep -->
- [ ] Manual checks done (received email, link clicks, checkout offer, headers) <!-- id:checks-manual-done tracks:deep -->
- [ ] Rendered emails reviewed before scoring creative <!-- id:checks-renders-reviewed tracks:snapshot,deep -->
- [ ] Every fail has an evidence path <!-- id:checks-evidence-paths tracks:snapshot,deep -->
- [ ] Uncovered findings logged as library candidates <!-- id:checks-library-candidates tracks:snapshot,deep -->

## Guardrails

- A zero or empty series is a question, not a finding. Check the metric source and the collector before calling it a business problem.
- List and segment sizes are not sendable reach. Reach needs current consent and suppressions applied.
- Don't score a check you couldn't measure. Use n/a with the reason.

## Tooling

| Tool | Status | Where |
|---|---|---|
| Check library spec | Built (v0.1) | `checks/check-library.json` |
| Check engine and `scorecard.json` | v0.3 (R-301 to R-304) | |
| Gmail clipping audit | Exists | `klaviyo-audit/37-audit-live-template-weight.js` |
