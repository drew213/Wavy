---
id: verify
order: 7
title: Verify
owner: QA reviewer
tracks: snapshot, deep
time_snapshot: 30 min
time_deep: 3 h
summary: Someone who didn't write the report recomputes its numbers from the raw outputs and challenges every claim.
inputs: Draft report, scorecard, claim ledger, collector outputs
outputs: Verification file with each check's expected vs. actual, list of fixes, sign-off
exit: Zero failed recomputes, every challenged claim resolved or removed, and the reviewer has signed off in the tracker.
---
## Steps

1. The reviewer gets the draft, the scorecard, the ledger and the outputs folder. Not the author's working notes.
2. **Recompute.** For every ledger row, recompute the value from the raw file. Snapshot: the top 5 levers and every scorecard fail. Deep: every row.
3. **Challenge.** For each recommendation, ask: is the source the pinned metric? Is the window the as-of window? Is the pool permissioned? Is anything counted twice? Would this embarrass us if the client checked it?
4. **Check the experience claims.** Any statement about what a customer receives must come from a received message or a click test, not a saved template.
5. Write `verification.json` (expected, actual, pass/fail per item) and a short list of fixes.
6. The author fixes; the reviewer re-checks only the changed items; then signs off.

## Checklist

- [ ] Reviewer is not the report's author <!-- id:verify-independent tracks:snapshot,deep -->
- [ ] Top levers and scorecard fails recomputed from raw outputs <!-- id:verify-recompute-top tracks:snapshot,deep -->
- [ ] Every claim-ledger row recomputed <!-- id:verify-recompute-all tracks:deep -->
- [ ] Source, window, reach and double-counting challenged per lever <!-- id:verify-challenge tracks:snapshot,deep -->
- [ ] Customer-experience claims backed by received messages or click tests <!-- id:verify-experience tracks:deep -->
- [ ] `verification.json` saved with zero open failures <!-- id:verify-file tracks:snapshot,deep -->
- [ ] Reviewer sign-off recorded <!-- id:verify-signoff tracks:snapshot,deep -->

## Guardrails

- A self-score is not verification.
- If the reviewer can't recompute a number, the number comes out of the report.
- Don't re-verify unchanged items after fixes; it wastes the reviewer's time.

## Tooling

| Tool | Status | Where |
|---|---|---|
| Per-brand recompute | Exists | `client-dashboards/brand-c-brand-d/build/verify_facts.py`, `analyst/verify-final-audits.py` |
| AI reviewer subagent | In use | Gets the evidence only |
| Generic `verify.py <audit>` | v0.5 (R-501, R-502) | |
