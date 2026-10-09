# Wavy OS ↔ Wavy Client Hub

Status: draft v0.1, 2026-10-09. For review by ChatGPT (which built the Hub) before either side builds against it.

## Who does what

| System | Job | Built and run by |
|---|---|---|
| **Wavy OS** (`wavy-os/`) | System of record: knowledge layers, insight → decision → task → result chain, approvals, policy, jobs, connectors, audit engine, event log | Claude Code |
| **Wavy Client Hub** (`client-dashboards/atrakt`, `client-dashboards/max-muscle`; ChatGPT Sites, Next.js on Workers + D1) | What the client sees and does: performance, journeys, calendar, knowledge galaxy, cohorts, feedback, design briefs, saved decisions | ChatGPT / Codex, published through the Sites workflow |

The Hub stays the client's front door. Wavy OS never renders client UI and never hosts client logins in v1. The Hub never becomes a second source of truth: anything that matters to the operating chain flows into Wavy OS and comes back as data.

## What exists today

- Each Hub is a separate Site per client, owner-private, with its own D1 database and source repo.
- `app/data.json` is a dated Oct 2 snapshot. It doesn't refresh.
- Hub D1 tables: `feedback` (title, body, topic, status, author), `galaxy_entries` (kind, title, payload: saved scenarios and decisions), `design_briefs` (revision, status, data, approval).
- Feedback exports to CSV; nothing connects it to project management.

## Data flow

```
                 hub-feed/<client>.json  (versioned, client-safe)
   Wavy OS  ───────────────────────────────────────────────►  Client Hub
     ▲                                                             │
     │   inbound: feedback, saved decisions, brief approvals,      │
     └──────────────── client approval decisions ◄─────────────────┘
```

### Outbound: Wavy OS → Hub (replaces the static data.json)

Wavy OS generates one **hub feed** per client, validated against `contracts/hub-feed.schema.json`:

| Section | From | Notes |
|---|---|---|
| `scorecard` | Latest audit scorecard + previous one for deltas | Verdict per check, score, what changed |
| `insights` | Open, client-visible insights | Title, severity, area, plain summary; evidence refs are aggregated, never raw IDs |
| `plan` | Approved decisions with their tasks and results | The chain the client can follow: why, what, status, measured outcome |
| `approvals` | Pending approvals whose approver roles include a client role | What the client is being asked to approve, the exact payload summary, expiry |
| `knowledge` | Approved client facts marked client-visible | Offers, voice rules, stakeholders |
| `meta` | Generated time, as-of, Wavy OS version, feed hash | The Hub shows "as of" everywhere it shows numbers |

**Client-safe by construction.** Only rows marked `visibility = "client"` enter the feed. Everything defaults to internal. The generator also runs the Hub's existing exclusion rules: no other-client or agency records, no unsent drafts, no raw profile or order IDs, no workspace paths, no internal notes, and no AI or tool attribution unless approved.

**Delivery, phase 1 (no hosting needed).** Wavy OS writes `hub-feed/<client>.json`. The Hub build reads it in place of `app/data.json` sections and publishes the same Site. Same publish flow as today, but the data is generated and auditable instead of hand-assembled.

**Delivery, phase 2 (once Wavy OS is hosted).** The Hub Worker fetches the feed from `GET /v1/clients/{c}/hub-feed` with a per-client service token that can only read that client's feed.

### Inbound: Hub → Wavy OS

| Hub record | Becomes in Wavy OS | Rule |
|---|---|---|
| `feedback` row | **Insight** (`source_kind=person`, evidence = the feedback ref), or a **proposed client fact** when it states something true about the business | Never auto-approved. The account lead triages it. |
| `galaxy_entries` with `kind=decision` | **Proposed decision** linked to an insight created from the entry | The client saying "do this" still goes through approval, so it gets a measurement plan |
| `galaxy_entries` scenario (contribution lab) | **Proposed fact** in `economics` | Inputs the client typed become knowledge only when approved |
| `design_briefs.approval` | Evidence on the matching **task**'s copy-approval checklist item | Bound to the brief revision. A new revision clears it, matching the Hub's own rule |
| Client approves or rejects in the Hub | `POST /v1/clients/{c}/approvals/{id}/decide` as that client user | Identity comes from Sites auth, mapped to a Wavy OS user by verified email. The approval is bound to the payload hash the Hub displayed. |

Phase 1: `wavyos hub import <client> <export.json>` takes the Hub's export. Each record carries its Hub ID, so importing it twice changes nothing. Phase 2: the Hub posts to `POST /v1/clients/{c}/hub-events` with an idempotency key.

## Changes this needs in Wavy OS

1. A `visibility` column (`internal` | `client`) on insights, client facts, decisions and tasks, defaulting to `internal`. Setting `client` is a logged action.
2. `hub_feed.build(session, scope) -> dict` plus the JSON Schema, with tests that seed internal-only rows and assert none reach the feed.
3. An inbound importer with idempotency keyed on the Hub record ID.
4. Identity mapping: `users.external_identities` for Sites auth subjects.

## Changes this needs in the Hub (ChatGPT's side)

1. Read the hub feed in place of the static `data.json` sections it covers, and show its `as_of`.
2. Add an approvals inbox (client approvers only) that shows the payload summary and posts decisions.
3. Export feedback, galaxy decisions and brief approvals as JSON with stable IDs (it already has CSV).
4. Keep every existing rule: owner-private per-client Sites, same-origin writes, no cross-client data.

## Open questions for review

1. Should feedback create insights directly, or land in a triage queue first?
2. Phase 2 auth: per-client service token versus the Hub forwarding the signed-in user's identity on every call.
3. Is the Hub's current D1 data (feedback, briefs, decisions) worth backfilling into Wavy OS, or should we start clean from the cut-over?
