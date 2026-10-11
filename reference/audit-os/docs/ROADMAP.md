---
version: 0.1.0
updated: 2026-10-09
---
# Roadmap

Each milestone ships something usable on the next audit. Ticket IDs are stable.

## v0.1 · Process foundation | status: in-progress | target: 2026-10-10
Goal: every audit from today follows one written process, tracked in one place.
- [x] (R-101) SOPs for all 10 stages plus operating rules
- [x] (R-102) Audit OS page: pipeline tracker, process map, SOPs, checks, roadmap
- [x] (R-103) Check library v0.1 spec (thresholds are starting defaults)
- [x] (R-104) `bin/wavy-key`: Keychain storage, `run` wrapper, revoke log
- [x] (R-105) Client intake questionnaire
- [ ] (R-106) Load the four existing audits into the tracker and confirm their stages
- [ ] (R-107) Confirm the old Brand A, Brand B, Brand C and Brand D keys are revoked
- [ ] (R-108) Revoke the Klaviyo key left in a Sept 22 session log

## v0.2 · One collector | status: planned | target: 2026-10-17
Goal: one command collects everything for either track, from one config file.
- [ ] (R-201) `audit.json` per audit: slug, track, timezone, as-of, pinned order metric, window
- [ ] (R-202) Auto-detect the order metric (most recent events), fail closed when ambiguous; write `source-contract.json`
- [ ] (R-203) `run-audit.js --config` reads keys only via `wavy-key run`
- [ ] (R-204) Snapshot profile: steps 24, 34, 37, 38 only
- [ ] (R-205) Deep profile: adds 31, 35, 40, 29 and the v2 event pulls with sanitizer, finalize and leak scan
- [ ] (R-206) Remove per-brand hardcoding (`<brand-c|brand-d>`, fixed timezone) from v2 scripts
- [ ] (R-207) Retire `analyst/reuse-running-key.sh`
- [ ] (R-208) Fix sending-domain fetch (404 in all four saved snapshots); unblocks DLV-05
- [ ] (R-209) Consent census in the runner (aggregate counts only); unblocks DAT-07 sendable reach
- [ ] (R-210) Link checker over saved flow and campaign HTML; unblocks CRV-03
- [ ] (R-211) SMS per-message performance and SMS revenue by channel (step 34/38 return email only)
- [ ] (R-212) Fix step 38 weekly flow series (needs flow_message_id grouping)
- [ ] (R-213) Investigate empty line items on Shopify accounts and zero catalog/coupon items before checks rely on them

## v0.3 · Check engine | status: planned | target: 2026-10-24
Goal: the scorecard writes itself; people spend their time on judgment.
- [ ] (R-301) `checks/engine/` runs every `data-available` check and writes `scorecard.json`
- [ ] (R-302) Each result carries value, threshold, verdict, evidence path and dollar estimate
- [ ] (R-303) Manual checks appear as open items with a form to record the verdict and evidence
- [ ] (R-304) Unit tests per check using the four saved audits as fixtures
- [ ] (R-305) Opportunity sizing formulas (low/base/high) with dependency gates

## v0.4 · Report templates | status: planned | target: 2026-10-31
Goal: same deliverable shape every time.
- [ ] (R-401) Snapshot report: one-page scorecard, top 5 fixes, the offer to go Deep
- [ ] (R-402) Deep report: summary, scorecard, sized top-10 roadmap, appendix, claim ledger
- [ ] (R-403) Brief template parameterized (no hardcoded dates or seasons)
- [ ] (R-404) Readout deck generated from the same scorecard

## v0.5 · Generic verify | status: planned | target: 2026-11-07
- [ ] (R-501) One `verify.py <audit>` that recomputes every number in the report from raw outputs
- [ ] (R-502) Claim ledger required for every client-facing number; build fails on an untraced number

## v1.0 · Re-audit and benchmarks | status: planned | target: 2026-11-21
Goal: prove the work moved the numbers, and know what good looks like.
- [ ] (R-601) Re-audit diff: scorecard vs. scorecard, with what changed and why
- [ ] (R-602) Anonymized benchmark table across Wavy clients; recalibrate thresholds after 5 audits
- [ ] (R-603) Tracker reminders for 30/60/90-day re-audits
