# The audit, end to end: build plan

Owner of the plan: Andrew (Wavy Studios). Builder: whoever picks up the phases below, working on branches with pull requests. Read `CLAUDE.md` first.

The audit is the product's front door and the most important thing to get right. A brand connects Klaviyo, and Wavy OS must turn that into a measured, honest, ranked list of what to fix, shown in a workspace that matches the Wavy Client Hub Andrew built for Wavy's first clients. Everything else (plans, tasks, campaigns, the paid transactional-email redo) builds on its output.

## 1. Goals

1. **Fast to first value.** The brand sees real findings from its own account within 3 minutes of connecting Klaviyo, and the full audit within 45 minutes, most of it while they are still answering the questionnaire.
2. **Measured, not invented.** Every number comes from the brand's own data and says where it came from. Missing data is shown as "unavailable" with the reason, never as zero. No constants stand in for a brand's numbers (today `onboarding.py` hard-codes subscriber counts; that goes).
3. **One engine.** The 36 implemented checks in `src/wavyos/audit/` are the single source of truth for verdicts. The brand path, the staff console and re-audits all run the same engine on the same snapshot shape.
4. **Matches the hub.** The workspace follows the Client Hub's sections, data shapes and design system (section 6), so a brand's workspace looks and reads like the hubs Wavy already delivers.
5. **Safe on a public repo and a shared API budget.** Keys stay in the vault, raw data stays in the database, and the audit respects Klaviyo's per-account limits that the brand's other apps share.

## 2. What exists today

| Piece | State | Where |
|---|---|---|
| Check engine, 45-check library (36 implemented), scorecards, before/after compare | Built and tested, but only runs on a saved collector folder; the brand path never calls it | `audit/engine.py`, `audit/checks_*.py`, `audit/check-library.json`, `audit/snapshot.py` (`Snapshot.from_dicts` already accepts in-memory files) |
| Brand-path collection | A thin intake: one UTC monthly aggregate, 12 months of orders, flow names, two ungrouped 365-day reports. Writes none of the snapshot files the engine reads | `klaviyo_intake.py`, `storage.py`, `jobmods/client_onboard.py` |
| Sizing | Pool percentages and hard-coded subscriber counts | `audit/sizing.py`, `onboarding.py` around line 265 |
| Workspace | 9 sections (overview, health, performance, journeys, plan, campaign, brand, answers, sources); dollar-ranked opportunities | `console/static/workspace.html` |
| Klaviyo client | Per-family pacing, Retry-After retries, POST allowlist | `connectors/klaviyo.py` |
| The Node collector Wavy actually ran, the audit SOP, the analyst brief | Reference only | `reference/` (see its README) |

## 3. The brand's journey (target)

| Time | Brand sees | System does |
|---|---|---|
| 0:00 | `/onboard` step 1: paste read-only Klaviyo key | Validate key (`GET /accounts`), store in vault, **queue the audit run immediately** |
| 0:00–0:20 | Step 2: questionnaire (autosaved). A small status chip in the header: "Reading your Klaviyo account · 3 of 8 steps" | Stage 1 ("first look") finishes in about 2 minutes; the full collection keeps going in the background |
| ~0:02 | Chip turns lime: "First findings ready · View". Opening it shows the first-look panel without losing questionnaire progress | First-look scorecard: revenue baseline, email and SMS share, live flow coverage, top 3 preliminary findings |
| questionnaire done | Step 3: the loading screen shows real stage progress and what each finished stage found ("62 flows read · 4 core flows missing") | Remaining stages; answers feed sizing inputs (margin, AOV source, goals) |
| ~0:30–0:45 | Redirect to `/audit/<slug>`. Sections fill in as their data lands; anything still collecting says so | Checks, sizing, opportunities, scorecard saved |
| later | Email "Your audit is ready" (once an email provider exists, backlog item 4) | |

If the brand leaves, nothing is lost: every stage is checkpointed and the workspace shows the run's state when they return.

## 4. Pipeline

### 4.1 Data model (new tables only; see the no-migrations rule)

- **`audit_runs`**: org_id, client_id, connection_id, track (`snapshot` or `deep`), status (`queued`, `collecting`, `scoring`, `ready`, `partial`, `failed`), `stages` JSON (per stage: status, started/finished, calls used, counts, error), library_version, scorecard_id (nullable), started_by, timestamps. One row per audit attempt; the workspace reads the latest `ready` or `partial` run.
- **`audit_artifacts`**: run_id, name (a `snapshot.FILES` key: `run`, `api`, `perf`, `templates`, `totals`, `forms`, `q4`), payload JSON, captured_at, truncated, error. Same shapes the Node collector wrote (`reference/klaviyo-audit/`), so the engine and checks don't change. Keep raw artifacts for the last 3 runs per client; scorecards and check results are kept forever.
- **`audit_orders`**: client_id, order_id (unique per client), profile_id (Klaviyo's pseudonymous id only; no email, name or address), placed_at, value, items (up to 8 titles, via the ported `order-items` logic), discount, source. Upserted by order id, so a re-audit fetches only new orders. The engine's `orders` file is built from this table. This replaces `commerce_orders` for Klaviyo data; leave `commerce_orders` for Shopify later.
- **`klaviyo_report_calls`**: account id, UTC day, count. Every reporting-endpoint call (`/flow-values-reports`, `/campaign-values-reports`, `/form-values-reports`, `/flow-series-reports`) increments it inside the call path. Refuse a run that would take an account past **40 a day** (Klaviyo allows 225 per account per day, shared with every other app the brand uses).

Storage check: a large account (about 190k orders in 24 months) is roughly 30 MB of `audit_orders`. Neon free gives 0.5 GB; log total database size in `/healthz` details for staff and plan the paid move before 10 large clients.

### 4.2 Stages

Each stage is its own job (`audit_collect`, payload `{run_id, stage}`), claimed by the existing worker. A stage writes its artifact (or rows), records its counts in `audit_runs.stages`, then enqueues the stages that depended on it. A stage that dies is picked up again by `requeue_stuck` and resumes from its saved cursor (page link or last order time), never from scratch.

| # | Stage | Port from | Calls | Writes | Needs |
|---|---|---|---|---|---|
| 0 | Verify | every script's `GET /accounts` check, `source-contract.js` | 1 | account, timezone, currency in `run` | key |
| 1 | **First look** | parts of 24, 34, 38 | `GET /metrics`; `GET /flows` (attributes incl. trigger type); 2 reporting calls (flow and campaign values, `last_365_days`, grouped exactly like step 34); 1 monthly `placed_order` aggregate by `$attributed_channel` in the account timezone | the 365-day half of `perf`; a `first_look` summary in `audit_runs.stages` | 0 |
| 2 | Account structure | step 24 | about 2 per flow + 2 per action + 1 per message (cap 5,000 messages), plus campaigns, forms, lists, segments, templates list | `api` | 0 |
| 3 | Performance, 90 days | step 34 | 2 reporting calls, 31 s apart | completes `perf` | 1 |
| 4 | Template weight | step 37 | 1 per unique live template | `templates` (measurements only: bytes, counts, a short text sample) | 2 |
| 5 | Aggregates and campaigns | step 38 sections 1–3 and 5 (form values report only) | about 45 aggregates, campaigns with messages, 1 reporting call | `totals` | 0 |
| 6 | Orders, 24 months | step 38 section 4 | `GET /events` for the pinned Placed Order metric, page size 1,000, resumable | `audit_orders` | 0 |
| 7 | Sizing pools | step 38 section 7, narrowed | `profile_count` only for the segments sizing needs (engaged and unengaged, matched by definition or name; if ambiguous, "not measured"), at most 10 | sizing inputs in `totals.groupCounts` | 2 |
| 8 | Score | — | 0 | scorecard, check results, opportunities, insights | all above |
| D1 | Forms detail (deep track) | step 31 | per form | `forms` | 2 |
| D2 | Q4 baseline (deep track) | step 35, minus `sending-domains` (404s) | about 48 aggregates, 4 reporting calls | `q4` | 0 |

Drop what no check reads: coupons, catalog items, the weekly flow-series report (fails with 400 on every saved run), and profile counts for every list and segment (the slowest part of step 38). This should bring the snapshot track from 27–43 minutes down to roughly 10–25 minutes on the accounts Wavy has run.

Klaviyo limits are per endpoint family, so stages 2, 5 and 6 can safely run at the same time. Start sequential; run families in parallel threads inside one worker only after the sequential version is correct.

### 4.3 Logic to port exactly (with their tests)

From `reference/klaviyo-audit/`, into `src/wavyos/collector/`:

- `metric-selection.js` → `collector/metrics.py`: `pick_metric` (alias groups; `ecommerce` vs `klaviyo` preference; name priority only within one integration; ties are `ambiguous`, never silently picked) and `describe_metric_choice` → `{used, candidates, otherCandidates, status, reason}`. This replaces `KlaviyoClient.metric_id("Placed Order")`. DAT-01 and DAT-02 read its output. An ambiguous Placed Order metric stops revenue sizing and becomes the brand's first finding, with a staff action to pin the right one.
- `source-contract.js` → per-client pinned metric ids that fail closed (wrong name, wrong integration, or a key for a different account is an error, not a guess). Store pins in a new `metric_pins` table.
- `aggregate-boundaries.js` → `collector/boundaries.py`: account-local midnights to UTC instants, DST-safe, rejecting missing or ambiguous midnights. Every aggregate passes the account timezone (today's intake doesn't, so its months are UTC).
- `order-items.js` → `collector/order_items.py`: titles only, from `ItemNames`, `Items` (including numeric-key objects), `$extra.line_items`, `line_items`. Never keep whole line items.
- Order rows: id from `$event_id` or `OrderId`, value `$value`, source `Source Name`; dedupe on order id.
- Retry and pacing rules from step 24 (408, 429 and 5xx up to 6 times, honour Retry-After; follow `links.next` to a per-collection page cap and set `truncated` when the cap cuts a list).

Port `aggregate-boundaries.test.js` and `order-items.test.js` as pytest cases. `metric-selection` tests must be rewritten with synthetic metric inventories (the originals read client data and are not in the repo).

### 4.4 Scoring, sizing, opportunities

- Refactor `engine.run_audit` so its core takes a `Snapshot`, and add `run_audit_for_run(session, scope, run_id)` that builds one with `Snapshot.from_dicts` from `audit_artifacts` plus `audit_orders`. Keep the folder entry point for staff and the parity script.
- **Sizing** (`reference/audit-os/sop/05-sizing.md`): eligible pool × expected rate change × value per conversion × months, with separate low/base/high assumptions. Pools and AOV come from the snapshot (stage 5 and 7 aggregates, orders), margin from the questionnaire. Remove the hard-coded subscriber counts, the 0.28 baseline and the pool percentages. A lever whose inputs aren't measured is "Not sized" with the missing input named. Apply the double-counting rules (one pool per lever; overlapping levers take the documented haircut) and never add attributed revenue to store revenue.
- **Opportunities** use the hub's model (section 5.2), generated from failed and warning checks through a new static `audit/playbooks.json` keyed by check id: title, outcome, steps, effort in hands-on hours (low/high), ease 1–5 with reason, owner role, dependencies, readout (how we'll know it worked). Wavy writes playbooks once; they are not AI-generated per brand. The brand-specific parts (observation, evidence, impact) come from the check result and sizing.
- **Impact 1–5:** from the sized base value as a share of the brand's own 12-month revenue (thresholds in the playbook file, starting at <0.5%, <1.5%, <3%, <6%, ≥6%), or from check severity when not sized. **Rank:** gates first (a failed data-integrity check gates revenue levers; failed deliverability gates "send more" levers), then base value × confidence ÷ effort when sized, else impact × ease.
- **Evidence label** (hub vocabulary): Observed problem (a measured fail), Descriptive signal (a warning or trend), Untested hypothesis (a lever whose rate change is assumed).
- Owner roles stay "UNCONFIRMED" until the brand or a strategist confirms them.

### 4.5 First look

Computed from stage 1 only, shown within about 2 minutes, labelled "First look · preliminary" everywhere:

- 12-month Placed Order revenue by month and the share attributed to email and SMS (with the metric choice shown under "Metric basis").
- Live flow coverage by trigger type (welcome, cart, checkout, browse, post-purchase, winback, sunset), marked preliminary until stage 2 confirms each flow's definition.
- Flow share of attributed revenue (FLW-08's measure on 365 days).
- Up to 3 preliminary findings from those, each with "Confirming with your full account data…".

When stage 8 finishes, first-look findings are replaced by scored ones. If a final verdict disagrees with a preliminary one, the final one wins silently in the brand view and is logged for staff.

## 5. Workspace: map to the Client Hub

The newest hub is the ChatGPT Sites build (published Oct 3–4, 2026). Its source and client data stay on Andrew's machine; the section list, data shapes and design system below are taken from it.

### 5.1 Section map

| Hub section | Wavy OS section | Data source | Phase |
|---|---|---|---|
| Opportunities (overview: baseline cards, business question, ranked list + detail panel, three sorts, "Download plan") | **Opportunities** (replaces today's overview and the dollar list) | checks + playbooks + sizing (4.4) | 3 |
| Foundational report (priorities, baseline, findings, email evidence, 90-day phases, 13-week plan, owner decisions, coverage, sources) | **Foundational report**: baseline, scorecard by area, findings (observation, implication, recommendation, verification, sources), "What we couldn't see" (every `na` check with its reason plus the manual checks). Phases, weeks and owner decisions arrive in phase 5 | scorecard + check results; phase 5 adds the narrative | 3 (findings), 5 (plan) |
| Performance (metric cards, monthly bars, flows vs campaigns split, quarterly table) | **Performance** (rebuild of today's) with dated snapshot, time-basis caveats | `totals` aggregates, `perf` | 3 |
| Current Journeys (flow map, performance history, next investigation) | **Current journeys**: real flow graphs from definitions (trigger, filters, nodes, delays, re-entry), live/draft counts, per-flow history; "Next investigation" comes in phase 5 | `api` definitions, `perf`, `$attributed_flow` aggregates | 3 |
| Customer cohorts (horizon filter, maturity heatmap, reference tables, exports; CAC/margin calculator) | **Customer cohorts**: mature-only horizons 30/60/90/180/365, repeat rate with Wilson intervals, sample-size labels (<100 insufficient, 100–499 small, ≥500 adequate). CAC/margin waits for finance inputs | `audit_orders` | 4 |
| Calendar → Send history tab | **Send history** | `totals.campaigns`, `perf` campaign reports | 4 |
| Sources & definitions | **Sources & definitions**: every artifact with captured time, truncation and errors; metric choices; definitions of RPR, attributed, mature cohort | `audit_runs`, artifacts | 3 |
| Retention lab: Data gaps tab | Merged into Foundational report "What we couldn't see" | `na` results | 3 |
| Retention lab: What changed tab | **What changed** (re-audit diff) | `engine.compare` | 7 |
| Ultimate Journey Map (lifecycle stages, nodes, message sequences, build priorities) | **Journey map** | AI draft from findings + strategist review | 5 |
| Calendar (Q4 calendar, BF/CM insights, research) | **Calendar** | AI draft + strategist review; deep track `q4` artifact | 5 |
| Retention lab: Action queue | **Plan** (today's 30-day plan, rebuilt on opportunities; approval creates tasks, backlog item 2) | opportunities + tasks | 6 |
| Design briefs | **Design briefs** (revision-locked, copy approval, ready check) | new tables | 6 |
| Feedback | **Feedback** (form, status feed, CSV) | new table | 6 |
| Competitor intelligence | Later; needs an inbox capture source | — | later |
| Knowledge galaxy / decision intelligence / library | Later; staff-side first | — | later |
| (Wavy OS only) Onboarding answers, Brand & guardrails | Keep as they are | questionnaire | — |
| (Wavy OS only) Retention health score | Replaced by the scorecard (8 area grades) | scorecard | 3 |
| (Wavy OS only) Campaign draft | Moves behind the paid tier with the transactional-email work | — | later |

### 5.2 The Opportunities model (hub shape, plus measured value)

Each opportunity, as JSON the page reads:

```json
{
  "id": "flw-02-checkout",
  "rank": 1,
  "title": "Rebuild checkout abandonment",
  "observation": "Checkout flow reached 9% of checkout starters in the last 90 days.",
  "outcome": "Recover more started checkouts without extra discounting.",
  "impact": {"score": 4, "label": "High", "reason": "Sized at 2.1–4.4% of 12-month revenue."},
  "ease": {"score": 4, "label": "Straightforward", "reason": "Flow exists; trigger and filters need fixing."},
  "whyRank": "Largest measured gap with no data-integrity gate.",
  "effort": {"low": 4, "high": 8, "unit": "hands-on hours"},
  "steps": ["..."],
  "dependencies": [],
  "readout": "Checkout flow recipients ÷ checkout starters, weekly for 4 weeks.",
  "ownerRole": "Retention lead (UNCONFIRMED)",
  "evidence": "Observed problem",
  "readinessLabel": "Ready to build",
  "findingIds": ["FLW-02"],
  "value": {"status": "sized", "low": 0, "base": 0, "high": 0, "period": "month", "basis": "gross, not incremental", "inputs": []},
  "klaviyoState": "Live in Klaviyo"
}
```

`value` is the one addition to the hub model. Whether brands see it is decision 1 in section 9; it is always computed and visible to staff. The example values are shapes, not targets.

### 5.3 UX rules for every section

- **First screen answers "what do I fix first".** Opportunities opens on the top 3 with their observation, effort and the one number that proves it. Everything else is one click away.
- **Every section has five states:** queued, collecting (stage name and a time estimate from the account's size), ready, partial (what's missing and why), unavailable (reason). Never a spinner without words; never a zero standing in for missing data.
- **Every number can explain itself:** a "Metric basis" expander with source, window, timezone, metric choice and caveats (attributed is not incremental; event time vs send date).
- **Caveat notice bar** at the top of any section whose data is partial, truncated or preliminary.
- **Deep links by hash** (`#opportunities/<id>`, `#journeys/<flowId>`) so staff can send a brand straight to a finding.
- **Export on every dataset:** CSV for tables, Markdown for the plan, and a print stylesheet for `/audit/<slug>` so "Download plan" works without a PDF service.
- **Design system** (from the hub; already partly in `workspace.html`): Geist and Geist Mono, self-hosted under `/assets/`; background `#0b0c0d`, card `#121415`, muted `#1b1e20`, border `#2b2e2f`, sidebar `#101213`, text `#f6f6f3`, muted text `#a0a5a8`, lime `#c8ff00`, accent text `#d6ff67`, eyebrow `#b5e833`, destructive `#ff8069`, campaign blue `#4a9eff`; square corners; sidebar 245 px; top bar 75 px (64 on mobile) with snapshot date; main padding 38 × 40 px, max width 1600 px; panels 27 px padding with 1 px borders; opportunity board grid `minmax(265px,.85fr) minmax(0,1.65fr)`, selected item with a 3 px lime left border, ranks as mono "01". Breakpoints 1150, 850, 800, 500 px. Check every page at 390 px.
- **Wording:** "proposed", "not incremental", "UNCONFIRMED" owners, "unavailable" for missing values. Plain and specific; never blame a previous agency.

## 6. AI analysis (phase 5)

The real audits used a language model to write the narrative from a sanitized packet, with a human scoring each draft. Port that, with the guardrails that made it trustworthy:

- **Packet** (`reference/analyst/build-packet.js`): only aggregates, check results and sizing go to the model. No emails, names or raw events; profile ids become salted HMAC pseudonyms if needed at all.
- **Brief** (`reference/analyst/BRIEF.template.md`): role, data caveats, sections and the 10-criterion rubric. Make the dates and season parameters, not text (the original is fixed to a Q4 plan starting Oct 2).
- **Facts check:** every number in model output must match a value in the run's facts table (built by code, never by the model) within rounding. A mismatch blocks the draft; it doesn't get auto-fixed.
- **Outputs:** foundational-report narrative (priorities, 90-day phases, 13-week plan, owner decisions), the journey map (hub shape: stages, nodes with trigger, timing, eligibility, exit, message jobs, success metric, guardrails, finding ids), calendar.
- **Review:** a strategist approves, edits or rejects each draft in the staff console before the brand sees it (decision 2). Approvals are policy actions and events.
- Runs on the Anthropic provider (backlog item 8), allowed per client.

## 7. Phases and acceptance

Each phase is one or more pull requests, merged only with `pytest -q` green on SQLite and Postgres.

| Phase | Scope | Done when |
|---|---|---|
| 0 | Merge the open fix PRs (staff roles, fonts, open redirect, job failure events, restarted runs) | `main` green, live site redeployed and smoke-tested |
| 1 | Collector port: tables in 4.1, stages 0–7, helpers in 4.3, budget counter, resumable cursors, fake-Klaviyo fixtures | On a synthetic account the run produces every snapshot-track artifact in the reference shapes; killing the worker mid-stage and restarting finishes without refetching completed pages; the 41st reporting call of a day is refused |
| 2 | Engine on the brand path: `run_audit_for_run`, measured sizing, playbooks, opportunities, first look | `client_onboard` produces a scorecard for brand-started audits; no hard-coded subscriber, pool or baseline values remain (a test greps for them); every opportunity has finding ids that resolve |
| 3 | Workspace v2, first wave: Opportunities, Foundational report (findings, scorecard, couldn't see), Performance, Current journeys, Sources; section states; questionnaire status chip; first-look panel | A brand on the live site sees first findings in under 3 minutes and a full workspace when the run finishes; partial runs show what's missing; checked at desktop and 390 px |
| 4 | Customer cohorts, Send history, exports, print view | Cohort numbers match a hand calculation on fixture orders; every table exports |
| 5 | AI narrative with facts check and strategist review: report plan, journey map, calendar | A draft with an altered number is blocked; brands only ever see approved drafts |
| 6 | Collaboration: plan approval creates tasks, design briefs, feedback | A lead's approval creates tasks through `operations.py`; brand feedback appears in the staff console |
| 7 | Re-audit at 30/60/90 days on the same library version, "What changed" | A scheduled re-run reuses stored orders and shows check-by-check changes |

Order matters: 1 → 2 → 3 is the critical path. 4 can run beside 3; 5 needs 2; 6 and 7 need 3.

## 8. Testing and parity

- **Fixtures are synthetic.** Extend the fake Klaviyo in `tests/` to serve a generated account: flows with definitions, messages, templates, campaigns, aggregates, paged events, ambiguous metrics, truncated collections, DST month boundaries, 429s with Retry-After.
- **Parity, run locally by Andrew only.** Add `scripts/parity.py`: given a saved Node collector folder and the same account's key, it scores the folder with the engine, runs the Python collector into a local SQLite database, scores that, and prints check-by-check differences. Output goes to `var/` and is never committed. Target before phase 3 ships: identical verdicts on the snapshot-track checks for the four accounts Wavy has already audited, or a written reason for each difference.
- **No client data in tests, fixtures, screenshots or PR descriptions.** The repository is public.

## 9. Decisions for Andrew

1. **Dollar values for brands.** Recommended: show a low–high range only when every input is measured, labelled "modeled, gross, not incremental"; otherwise show impact and ease only (the hub's approach). Alternatives: never show dollars to brands, or always show them.
2. **AI-written sections.** Recommended: measured findings show immediately; AI-written narrative (plan, journey map, calendar) is staff-only until a strategist approves it.
3. **A paid worker before the first real brand.** The free web service sleeps after 15 idle minutes, which pauses an audit if the brand closes the tab. Checkpointing (phase 1) makes that recoverable, but a $7/month Render background worker makes it reliable. Recommended: add it when phase 1 merges.

**Decided (Oct 10, 2026): the recommended option for all three.** Brands see dollar ranges only when every input is measured; AI-written sections are staff-only until a strategist approves; the paid worker is added when phase 1 merges. Keep each choice behind a single setting.

## 10. After the audit: queued work

**Shopify transactional emails, redone (first paid feature).** Wavy rebuilds a brand's Shopify notification emails (order confirmation, shipping, delivery, refunds, account emails) in the brand's own design and voice. It is queued behind the audit; don't start it until phases 1–3 ship. Questions to answer first, in a separate plan: whether Shopify's Admin API can write notification templates or the work is delivered as Liquid plus install steps; how brand kit and guardrails from onboarding feed the templates; how a brand previews and approves them; and how the paid tier gates it.
