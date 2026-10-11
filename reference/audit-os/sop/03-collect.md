---
id: collect
order: 3
title: Collect
owner: Audit lead
tracks: snapshot, deep
time_snapshot: 30 min hands-on, ~1 h unattended
time_deep: 1 h hands-on, 3–8 h unattended
summary: Pull the account's data read-only, pin the right order metric, and prove the dataset is complete and clean.
inputs: Active key, confirmed account ID
outputs: "`klaviyo-audit/output/<slug>/` snapshot files, `source-contract.json`, run log (Deep: finalized v2 datasets and a passing leak scan)"
exit: Every step in the track's profile is COMPLETE or explicitly PARTIAL with a reason, the order metric is pinned, and (Deep) the leak scan passes.
---
## Step 1: Pin the order source first

Before anything that touches revenue, decide which metric is the store's real Placed Order. Stores that switched platforms keep dormant metrics (Brand C and Brand D are WooCommerce but still show Shopify order metrics; Brand D really sold on Shopify from 2021-06 to 2022-02).

1. List every order-type metric with monthly counts over the full history (`klaviyo-audit/v2/commerce-source-history.py`).
2. Pick the metric with current, continuous events that matches the platform from intake. Record any earlier eras.
3. Write `klaviyo-audit/output/<slug>/source-contract.json` with the account ID, pinned metric IDs and the verified window. `source-contract.js` fails closed if a later step disagrees.

## Step 2: Run the track's profile

All commands run from `klaviyo-audit/` and get the key only through `wavy-key run`.

**Snapshot profile** (steps 24, 34, 37, 38):

```bash
../audit-os/bin/wavy-key run <slug> -- node run-audit.js <slug> --steps 24,34,37,38 --no-render
```

**Deep profile** (every Gen 1 step, then the v2 events):

```bash
../audit-os/bin/wavy-key run <slug> -- node run-audit.js <slug> --fresh
../audit-os/bin/wavy-key run <slug> --key-file -- ./v2/run-commerce-events-v3.sh <slug> <start-month> <asof-date> <metric-id>:<label> ...
python3 v2/finalize-inventory.py <slug> <label> <metric-id> <start-month> <asof-date>   # once per dataset
python3 v2/verify-sanitized.py <slug>
```

The reporting endpoints (steps 34, 35, 38) allow 2 calls a minute, so these steps are slow. Leave them running; don't restart a step that is still progressing.

## Step 3: Record the run

1. Set the **as-of** instant: the exclusive end of the analysis window in the account's timezone (e.g. `2026-10-07T00:00:00-07:00`). Everything is measured up to it.
2. Write the run log: each step, COMPLETE or PARTIAL, and why. Copy `run.json` status, don't retype it.
3. A dataset is COMPLETE only when `finalize-inventory.py` says so. A worker saying "finished" is not completion.

## Checklist

- [ ] Order metric pinned in `source-contract.json`, with past platforms noted <!-- id:collect-source-pinned tracks:snapshot,deep -->
- [ ] As-of instant and account timezone recorded <!-- id:collect-asof tracks:snapshot,deep -->
- [ ] Snapshot profile steps (24, 34, 37, 38) complete or marked PARTIAL <!-- id:collect-snapshot-profile tracks:snapshot,deep -->
- [ ] Remaining Gen 1 steps (31, 35, 40, 29) complete or marked PARTIAL <!-- id:collect-deep-profile tracks:deep -->
- [ ] v2 event datasets finalized COMPLETE by `finalize-inventory.py` <!-- id:collect-v2-finalized tracks:deep -->
- [ ] Leak scan (`verify-sanitized.py`) passes <!-- id:collect-leak-scan tracks:deep -->
- [ ] No list or flow in step 24 flagged `truncated` (or the truncation is noted) <!-- id:collect-no-truncation tracks:snapshot,deep -->
- [ ] Run log written <!-- id:collect-run-log tracks:snapshot,deep -->

## Guardrails

- Never mix "now-relative" report windows (`last_365_days`) with the pinned as-of window without labelling which is which.
- Sanitizer version is stamped in every month manifest. Older versions are refused for reuse; re-sanitize, don't override.
- If a run is interrupted, resume it. The v2 extractor is resumable by month. Don't start a second run on the same slug.

## Tooling

| Tool | Status | Where |
|---|---|---|
| Gen 1 runner, steps 24–40 | Exists | `klaviyo-audit/run-audit.js` |
| Source contract, fail-closed | Exists | `klaviyo-audit/source-contract.js` |
| v2 events, sanitizer, finalize, leak scan | Exists, hardcoded to Brand C/Brand D in places | `klaviyo-audit/v2/` |
| One config-driven runner with track profiles | v0.2 (R-201 to R-206) | |
