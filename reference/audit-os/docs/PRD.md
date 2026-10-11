# PRD: Wavy Audit OS

Owner: Andrew Lauchner · Status: v0.1 in build · Updated 2026-10-09

## Problem

Wavy has run four Klaviyo audits (Brand A and Brand B on Oct 2; Brand C and Brand D on Oct 7–8). Each one was built from scratch:

- **Tooling drifted.** The Gen 1 runner (`klaviyo-audit/run-audit.js`) is documented and reusable. The Gen 2 event pipeline (`klaviyo-audit/v2/`) is rigorous but hardcoded to two brands and documented only inside the scripts and one client's status file. Report builders are per-brand (about 1,100 lines for Brand C/Brand D alone).
- **No standard checks.** Findings depend on who ran the audit and what they noticed. Nothing is comparable across clients or across time.
- **Effort skews to defensibility.** Most of a night went to sanitizer versions, timestamps and QA reconciliation. The client sees none of that.
- **Key handling is inconsistent.** Keys arrive three ways (hidden prompt, scratch file, scraped from a running process), sometimes pasted into chat, and revocation is not tracked.
- **No re-audit.** We can't show a client the same scorecard 60 days later, which is the strongest retainer argument we have.

## Goals

1. Every audit runs the same stages, SOPs and checks, on a Snapshot or Deep track.
2. Snapshot: ≤ 4 human hours, delivered within 48 h of key receipt.
3. Deep: ≤ 2 human days, delivered within 5 working days.
4. Every client-facing number traces to a check result or a claim-ledger row.
5. Every key is stored in the Keychain, never in chat or files, and revoked within 24 h of delivery.
6. Every Deep client gets a 30/60/90-day re-audit on the same scorecard.

## Non-goals (v1)

- Making live account changes. The audit is read-only, always.
- Non-Klaviyo ESPs. Attentive and OneText SMS are handled as manual checks until a collector exists.
- A multi-user SaaS. This is an internal tool for Wavy staff.

## Users

| Role | Uses the system to |
|---|---|
| Audit lead | Run intake, collection and checks; own the timeline |
| Strategist | Turn findings into the roadmap and narrative |
| QA reviewer | Independently recompute and challenge claims (a second person or a separate AI reviewer, never the author) |
| Account lead / sales | Run the readout and convert Snapshot to Deep |

One person may hold several roles, except that the QA reviewer must not be the author of the report they review.

## How it works

```
Intake → Access → Collect → Checks → Sizing → Strategy → Verify → Deliver → Close → Re-audit
```

Each stage has an SOP (`sop/`), a checklist with stable IDs, an exit gate and a time budget per track. The Audit OS page tracks each client through the stages and records who ticked what.

### System components

| Component | v0.1 state | Target |
|---|---|---|
| SOPs and tracker page | Built | Maintained in this repo, compiled to the page |
| Key handling (`bin/wavy-key`) | Built, not yet used on a live audit | Only way keys reach collectors |
| Collector | Gen 1 runner + v2 scripts, separate | One `audit.json` config drives both (v0.2) |
| Check engine | Library spec only | Scores every check automatically into `scorecard.json` (v0.3) |
| Report templates | Per-brand builders | One Snapshot template and one Deep template fed by the scorecard (v0.4) |
| Verify | `verify_facts.py` per brand, `verify-final-audits.py` | One generic recompute plus the claim ledger (v0.5) |
| Re-audit diff | None | Scorecard-vs-scorecard delta report (v1.0) |

## Success measures

| Measure | Baseline | Target by end of Q4 |
|---|---|---|
| Human hours per Snapshot | Not tracked | ≤ 4 |
| Human hours per Deep audit | ~2 overnight multi-agent sessions + QA | ≤ 16 |
| Snapshot → signed client | Not tracked | Track from first Snapshot |
| Keys revoked ≤ 24 h after delivery | Unknown | 100% |
| Deep clients with a 60-day re-audit | 0 | 100% |
| QA defects found after delivery | Not tracked | 0 critical |
