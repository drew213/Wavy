---
id: reaudit
order: 10
title: Re-audit
owner: Audit lead
tracks: deep
time_deep: 2 h at each checkpoint
summary: Re-run the same scorecard at 30, 60 and 90 days and show the client what moved.
inputs: Original scorecard and as-of, new read-only key
outputs: New scorecard, delta report
exit: The delta report is delivered and the next checkpoint (or the end of the cycle) is in the tracker.
---
## Steps

1. Get a fresh read-only key (Access SOP). The original was revoked.
2. Collect with the **Snapshot** profile plus any Deep checks that changed in the roadmap. Use a new as-of, and keep the same order metric unless the client changed platforms.
3. Score with the **same library version** as the original. If the library changed, score with both and show the old version for comparison.
4. Write the delta: each check's before and after, what Wavy changed, what else changed (seasonality, offers, list growth), and what's next.
5. Present it as observed change, not proven lift, unless there was a holdout.

## Checklist

- [ ] Fresh key stored and checked <!-- id:reaudit-key tracks:deep -->
- [ ] Scorecard re-run on the original library version <!-- id:reaudit-scored tracks:deep -->
- [ ] Delta report written (before, after, what changed, what's next) <!-- id:reaudit-delta tracks:deep -->
- [ ] Delta delivered and next checkpoint booked <!-- id:reaudit-delivered tracks:deep -->
- [ ] Key revoked again <!-- id:reaudit-key-revoked tracks:deep -->

## Guardrails

- Same definitions as the original, or the comparison is meaningless.
- Separate what we changed from what the season changed.

## Tooling

| Tool | Status | Where |
|---|---|---|
| Re-audit diff | v1.0 (R-601) | |
