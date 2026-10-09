# Changelog

## 0.2.0 · 2026-10-09

First complete release of the backend.

- **Agent skills:** 5 role skills and 23 area skills on the ten-part Lifecycle Client OS template, with a validator, versioned publishing (`wavyos seed skills`) and work-packet integration. Area quality checklists become the default QA checklists. Two rounds of independent review by ChatGPT Pro (rework → fix-then-ready); all remaining fixes applied; skills at version 2. See docs/REVIEW-LOG.md.
- **Release safety:** fixed task phases, QA-flagged high/critical findings block release, executor rules for capabilities, untrusted content and phase, consent exception for verified opt-outs.
- **Execution layer:** work packets, a policy-checked tool gateway, Claude (Anthropic SDK) and ChatGPT (Codex) adapters, cross-model QA with rework and give-up, approval pause and resume, per-client provider allowlists and daily budgets, live step traces.
- **Ops console:** overview, flow board, execution traces, jobs, approvals inbox and event log at `/console`.
- **Audit engine:** 32 snapshot-track checks of the 45-check library, severity-weighted scorecards, insights with evidence, scorecard comparison.
- **Read-only Klaviyo connector** and the daily account health, results due and knowledge staleness jobs; ten more job contracts registered.
- **HTTP API and CLI**, including the fictional demo workspace (`wavyos demo`).
- Client-neutral configuration, docs and tests.

## 0.1.0 · 2026-10-09

- Core: tenancy, knowledge layers, the insight → decision → task → result chain, permission engine, approvals bound to payload hashes, envelope-encrypted vault, job scheduler and runner, hash-chained event log.
