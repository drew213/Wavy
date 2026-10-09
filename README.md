# Wavy OS

**The Lifecycle Client Operating System from Wavy Studios.** Wavy OS runs an email and SMS retention program for a brand the way a great agency team would. It holds the agency's playbook and each client's approved knowledge, finds what needs attention, turns it into an approved plan, hands the work to AI models and people, has a different model check it, and measures the result. Every step is visible in a live console, and every change is recorded in a tamper-evident log.

> Every insight connects to a decision, every decision to a task, and every completed task to a measured result. Wavy OS enforces that chain in software.

Version 0.2.0 · Python 3.12 · FastAPI · SQLAlchemy · Anthropic SDK · Proprietary (see Licensing).

---

## Contents

1. [What it does](#what-it-does)
2. [Quick start: the demo](#quick-start-the-demo)
3. [Set it up for a real client](#set-it-up-for-a-real-client)
4. [The console](#the-console)
5. [How work moves](#how-work-moves)
6. [Agents, skills and models](#agents-skills-and-models)
7. [Permissions and approvals](#permissions-and-approvals)
8. [The always-on loop](#the-always-on-loop)
9. [Audits](#audits)
10. [Security and data handling](#security-and-data-handling)
11. [Configuration](#configuration)
12. [Command line reference](#command-line-reference)
13. [HTTP API reference](#http-api-reference)
14. [Project layout](#project-layout)
15. [Testing](#testing)
16. [Deploying to production](#deploying-to-production)
17. [Known limits and roadmap](#known-limits-and-roadmap)

---

## What it does

| Capability | What it means for the brand |
|---|---|
| **Three knowledge layers** | Agency standards (how Wavy does things), client knowledge (what's true for this brand) and live operating state (what's happening now) are kept apart. A meeting note never quietly becomes a brand rule. |
| **The operating chain** | Insight → Decision → Task → Result. An insight needs evidence. A decision needs a measurement plan and a person's approval. A task needs a done-when and a QA checklist. A finished task opens a result that gets measured. |
| **Task execution by models** | Work packets go to Claude (default), ChatGPT through Codex, or a person. The agent works only through permission-checked tools. |
| **Cross-model QA** | A different model reviews the work item by item against the checklist. Failures go back with notes, up to three times, then the task waits for a person. |
| **Approvals that bind** | Sending, publishing, live changes and discounts each need a person's approval, bound to the exact content they saw. If the content changes, the approval no longer counts. |
| **Agent skills** | 5 role skills and 23 area skills cover the full lifecycle program, from onboarding to offboarding. Agents work from them; QA checks against them. |
| **Always-on jobs** | Daily account health, result measurement and knowledge staleness run on schedule. Each job either produces something or says why not. |
| **Audits** | A 45-check library scores a Klaviyo account and turns every warning and failure into an insight. A re-audit compares scorecards. |
| **Live console** | One screen shows every client's chain, every execution trace, the approval inbox, job runs and the event log. |
| **Tenant isolation** | Every client workspace is isolated in every query. Client stakeholders see only their own brand. |

---

## Quick start: the demo

The demo builds a fictional brand, Northwind Botanicals, and runs its work through scripted executors. Nothing is sent to any model and no client data is involved.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
export WAVYOS_DATABASE_URL="sqlite:///$PWD/var/demo.db" WAVYOS_MASTER_KEY_FILE="$PWD/var/master.key"
.venv/bin/wavyos demo
.venv/bin/wavyos serve --port 8792
```

Open `http://127.0.0.1:8792/console` and paste the token `demo` printed. You'll see:

- **Welcome email 1** drafted and passed by QA on the first try.
- **Welcome email 2** sent back by QA for a "detox" health claim, fixed, then passed.
- **Send approval for welcome email 1** waiting in Approvals. Approve it, run `.venv/bin/wavyos work`, and watch it finish.

---

## Set it up for a real client

```bash
.venv/bin/wavyos init-db
.venv/bin/wavyos bootstrap --org-name "Wavy Studios" --org-slug wavy \
    --admin-email you@wavystudios.com --admin-name "Your Name"          # prints your API token once
.venv/bin/wavyos seed skills --as you@wavystudios.com                     # load the agent skills
.venv/bin/wavyos client create --as you@wavystudios.com --name "Acme Skincare" --slug acme \
    --timezone America/Los_Angeles
.venv/bin/wavyos client allow acme --providers anthropic,codex --budget 10 --as you@wavystudios.com
.venv/bin/wavyos provider add anthropic --as you@wavystudios.com          # hidden prompt; sealed in the vault
.venv/bin/wavyos serve                                                     # API + console
.venv/bin/wavyos work --loop                                               # worker, in a second terminal
```

Then connect the client's Klaviyo account with a **read-only** private key through the API (`POST /v1/clients/acme/connections`). Wavy OS checks which account the key opens before saving it.

Run `wavyos schedule` on a timer (every 15 minutes is plenty) to queue the always-on jobs. `wavyos work --loop` runs them and the model executions.

**Nothing is sent to a model provider for a client until `client allow` names that provider.**

---

## The console

`/console`, signed in with an API token. It refreshes every few seconds and redraws only when something changed.

| View | Shows |
|---|---|
| **Overview** | Each client: open insights, decisions, tasks by status, results being measured, what's running, pending approvals, model spend against today's budget. |
| **Flow** | One client's chain in four columns, insight → decision → tasks with their executions → result. "Run with model" dispatches a task. |
| **Executions** | Every model and QA run. Open one to see the exact work packet the model received, each model call with tokens and cost, each permission decision with its reason, each tool call, and the outputs. |
| **Jobs** | Recent job runs, and every job's contract: trigger → checks → action → owner → done when. |
| **Approvals** | What's waiting on a person, with exactly what they'd be approving. Approve or reject with a note. |
| **Events** | The hash-chained log of every change. |

---

## How work moves

```
 Audit check / job / agent          Person approves             Model or person           Different model           Analyst job
        │                                 │                           │                         │                       │
   INSIGHT (evidence) ──► DECISION (plan + measurement) ──► TASK (done-when + checklist) ──► EXECUTION ──► QA ──► RESULT (measured)
                                                                                                │
                                                                         needs to send/publish? │──► APPROVAL ──► resumes
```

1. **Insight.** Recorded with evidence by an audit check, a job or an agent. Duplicates of an open insight are merged.
2. **Decision.** A strategist agent proposes a plan answering one or more insights, with a metric and a measurement window. Its scope can only include draft, non-live work. A person approves it.
3. **Task.** Created from the approved decision. The area skill's quality checklist is the default QA checklist.
4. **Execution.** The task's work packet goes to the routed executor. The agent saves outputs, flags insights, proposes facts, builds drafts or asks for approval, all through tools.
5. **QA.** Routed to a different executor where the client allows one. Every checklist item gets a pass or fail with a note; the task's author can never pass its own checks.
6. **Result.** A done task opens a pending result. When the window closes, the analyst job creates a measurement task. The decision closes when every result is in, and its outcome joins the learning log.

---

## Agents, skills and models

### Roles

| Role | Owns |
|---|---|
| **Account lead** | Priorities, deadlines, client context, meeting prep, client updates (always approved before sending). |
| **Analyst** | Monitoring, diagnosis, audits, opportunity sizing, result measurement, experiment evaluation. |
| **Lifecycle strategist** | Decisions, journeys, segmentation, flows, task breakdown and prioritization. |
| **Creative producer** | Briefs, email and SMS copy, design direction, draft specs. |
| **QA reviewer** | Pass or fail on every checklist item, against the decision, approved facts and skills. Never approves. |

### Skills

`skills/roles/*.md` (5) and `skills/areas/*.md` (23), validated by `wavyos skills-check` and loaded as versioned standards by `wavyos seed skills`.

Each area skill follows the founder's ten-part template: **Purpose · Client facts and rules · Inputs and sources · SOP · Decision rules · Examples · Quality checklist · Owner and approvals · Outputs and destination · Maintenance**. The validator rejects a file with missing or out-of-order sections, a thin section, a checklist outside 5–12 items, or an approvals row whose action or level doesn't match the permission engine.

Areas: onboarding, economics, product and customer intelligence, account and data infrastructure, journey, segmentation, deliverability, list growth, flow audit, flow architecture, campaign calendar, offers, voice and copy, design system, campaign production, SMS, CRO and onsite, reporting, experimentation, client meetings, account management, quarterly planning, knowledge maintenance.

Skills never contain client specifics. They say what to look up in the client's approved knowledge.

### Executors and routing

| Executor | How it runs | Default for |
|---|---|---|
| `anthropic` | Claude through the Anthropic SDK with native tool use, model `claude-opus-5-5`, server-side refusal fallback enabled | Producing work in every role |
| `codex` | ChatGPT through the Codex CLI, signed in to ChatGPT on the worker machine; returns a plan of tool calls that the gateway runs | QA (so the reviewer is a different model) |
| `fake` | Scripted tool calls; sends nothing anywhere | Demo and tests |

Routing lives in `src/wavyos/execution/engine.py` (`ROUTES`). QA always prefers an executor other than the one that produced the work. Cost is computed from published per-token prices for Claude; Codex runs on the ChatGPT subscription and records no per-token cost.

### The tool gateway

Agents can only act through these tools, and each is checked against the permission engine before it runs:

| Tool | Permission action | Used for |
|---|---|---|
| `get_context` | `data.read` | Agency standards and the client's approved facts for an area |
| `save_output` | `draft.create` | Saving a deliverable as an artifact |
| `flag_insight` | `insight.create` | Recording something that needs attention, with evidence |
| `propose_fact` | `fact.propose` | Proposing client knowledge (stays proposed until approved) |
| `build_draft` | `campaign.build_draft` | Draft builds; allowed only inside an approved plan |
| `request_approval` | `approval.request` | Asking a person to approve an action the agent can't take |
| `record_check` | `task.manage` | QA verdicts, QA executions only |
| `finish` | `task.manage` | Ending the execution with a summary |

---

## Permissions and approvals

Every action has a level:

| Level | Meaning | Examples |
|---|---|---|
| **Autonomous** | Do it and log it | Read data, analyze, draft, manage internal tasks, flag issues, propose facts and decisions |
| **Approved plan** | Only inside an approved decision whose scope lists it | Build draft campaigns and flows, prepare audience changes, non-live implementation |
| **Explicit approval** | Each instance needs its own approval, bound to the exact payload, single use, with an expiry | Send client messages, publish campaigns, change live flows, change discounts, suppress or delete profiles, change account settings, approve facts and decisions |

- Only a person can approve, never an agent, and never the person or agent who asked.
- Approvers are role-specific. For example, a client approver can approve a campaign send, but only the agency's leads can approve a client message.
- Org admins can narrow or relax an action's level per client (`POST /v1/policy/overrides`). **Discount changes, profile suppression or deletion, and account settings are locked** at explicit approval and can never be relaxed.
- `POST /v1/clients/{c}/authorize` is a dry run: it says allow, needs a plan or needs approval, and why, without using an approval.

---

## The always-on loop

| Job | Cadence | Owner | Status |
|---|---|---|---|
| `account.daily_health` | Daily | Analyst | Compares every flow's status with the last run and raises an insight when a live flow goes draft, manual, paused or missing. Read-only. |
| `results.due` | Daily | Analyst | Opens a measurement task for each result whose window has closed. |
| `knowledge.staleness` | Weekly | Account lead | Opens a review task for approved facts past their review date. |
| `post_send.review`, `weekly.trends`, `weekly.calendar_readiness`, `biweekly.meeting_prep`, `monthly.report`, `monthly.calendar`, `quarterly.roadmap`, `event.anomaly`, `event.tracking_failure`, `event.client_feedback` | As named | Various | Contracts registered and visible in the console; handlers are next on the roadmap. |

The scheduler queues one run per job, client and period, in the client's timezone. Racing schedulers can't double-queue. A run that produces nothing must give a reason, so "always-on" never turns into observation without action.

---

## Audits

`POST /v1/clients/{c}/audits` runs the check library against a collected Klaviyo snapshot:

- **45 checks** across data integrity, capture, flows, campaigns, deliverability, retention, creative and SMS. 32 snapshot-track checks are implemented; the rest are deep-track or need a collector, and report `na` with the reason.
- A **scorecard** from 0 to 100: severity-weighted (critical 4, high 3, medium 2, low 1) over pass (1), warn (0.5) and fail (0).
- Every warning and failure becomes an **insight** with evidence pointing at the exact file and field. Re-running doesn't duplicate open insights.
- `GET /v1/clients/{c}/scorecards/{a}/compare/{b}` shows what moved between audits.

Thresholds are Wavy starting defaults unless a check cites a published platform rule. Recalibrate after five or more audits.

---

## Security and data handling

- **Tenant isolation.** Every service call takes a scope (organization, client, actor). Rows from another client look like they don't exist.
- **Secrets.** Client platform keys and model provider keys use envelope encryption: a fresh AES-256-GCM data key per secret, wrapped by a master key, and bound to the row it belongs to, so a copied ciphertext won't open elsewhere. The API never returns a secret. Revoking a connection destroys the local copy.
- **Read-only connectors.** The Klaviyo client allows GET plus an allowlist of read-only report endpoints, and refuses anything else before a request is made. Keys never appear in logs, errors or reprs.
- **Model data policy.** A client's data reaches a model provider only if that provider is in the client's `allowed_providers`. Work packets contain only approved knowledge and the task's own chain. Each client has a daily model budget.
- **Tokens.** API tokens are random, shown once, and stored as SHA-256 hashes.
- **Event log.** Every change is written with a hash chained to the previous event for that client. `wavyos verify-events` detects edits or deletions. It's tamper-evident, not tamper-proof; ship hashes off the box to anchor it.
- **Console headers.** Content Security Policy, `X-Frame-Options: DENY`, no-referrer, no caching.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WAVYOS_DATABASE_URL` | `sqlite:///var/wavyos.db` | SQLAlchemy URL. Use Postgres in production. |
| `WAVYOS_MASTER_KEY_FILE` | `var/master.key` | 32-byte master key, created on first use with 0600 permissions. Back it up; without it, stored secrets can't be opened. |
| `WAVYOS_AUDIT_OUTPUT_ROOT` | `var/audit-output` | Folder of collected audit snapshots (`<client>-<date>/`). |
| `WAVYOS_AUDIT_OS_ROOT` | `var/audit-os` | Optional Audit OS process folder for `seed standards`. |
| `ANTHROPIC_API_KEY` | — | Used only if no Anthropic key is stored with `provider add`. |

Per client, set with `wavyos client allow`: `allowed_providers` and `daily_budget_usd`.

---

## Command line reference

| Command | Does |
|---|---|
| `wavyos init-db` | Create the tables |
| `wavyos bootstrap --org-name --org-slug --admin-email --admin-name` | Create an organization, its admin and its five agents; prints a token once |
| `wavyos token --email` | Issue an API token; prints it once |
| `wavyos client create --as --name --slug [--timezone] [--website]` | Create a client workspace |
| `wavyos client allow <slug> --providers a,b [--budget USD] --as` | Choose which model providers may receive this client's data |
| `wavyos provider add anthropic --as` | Store a provider key in the vault (hidden prompt) |
| `wavyos seed skills --as [--root]` | Validate and load the agent skills |
| `wavyos seed standards --as [--root]` | Load an Audit OS process folder as standards |
| `wavyos skills-check [--root]` | Validate skill files without loading them |
| `wavyos schedule [--org]` | Queue the jobs that are due |
| `wavyos work [--loop] [--interval S] [--jobs-only \| --executions-only]` | Run queued jobs and executions |
| `wavyos serve [--host] [--port]` | Run the API and console |
| `wavyos verify-events --client <slug>` | Check a client's event chain |
| `wavyos demo` | Build and run the fictional demo workspace |

Commands that change data take `--as EMAIL`, so the services apply that person's roles exactly as they would over the API.

---

## HTTP API reference

Bearer-token auth on everything except `/healthz` and the console page. Errors are `{"error": "..."}` with 401, 403, 404, 409, 422 or 503. Interactive docs are at `/docs` while the server runs.

**Organization**: `GET /v1/me` · `GET|POST /v1/clients` · `GET|POST /v1/standards` · `GET /v1/policy/actions` · `POST /v1/policy/overrides` · `GET /v1/jobs/definitions`

**Client workspace** (`/v1/clients/{slug}/…`):

| Area | Endpoints |
|---|---|
| Knowledge | `GET\|POST facts` · `GET context/{area}` |
| Chain | `GET\|POST insights` · `POST insights/{id}/dismiss` · `POST decisions` · `GET decisions/{id}` · `GET\|POST tasks` · `POST tasks/{id}/status` · `POST tasks/{id}/checks/{item}` · `POST tasks/{id}/complete` · `GET results` · `POST results/{id}` |
| Approvals | `GET approvals` · `POST approvals/{id}/decide` · `POST authorize` (dry run) |
| Connections | `GET\|POST connections` · `DELETE connections/{id}` |
| Audits | `POST audits` · `GET scorecards/{id}` · `GET scorecards/{a}/compare/{b}` |
| Jobs and log | `GET job-runs` · `GET events` · `GET events/verify` |

**Ops (console)** (`/v1/ops/…`): `GET overview` · `GET clients/{slug}/flow` · `POST clients/{slug}/tasks/{id}/dispatch` · `GET executions` · `GET executions/{id}` · `POST executions/{id}/cancel` · `GET jobs` · `GET approvals` · `POST approvals/{id}/decide` · `GET events?after=` · `GET me`

---

## Project layout

```
src/wavyos/
  models.py          every table (tenancy, knowledge, chain, governance, jobs, execution, audit, events)
  scope.py           actors, scopes, tenant-safe lookups
  policy.py          the action catalog and authorize()
  approvals.py       requests and decisions, bound to payload hashes
  knowledge.py       standards (layer 1) and client facts (layer 2)
  operations.py      insight → decision → task → result
  skills.py          skill loader, validator, publisher
  jobs.py            registry, scheduler, runner
  jobmods/           job handlers and contracts
  execution/         work packets, tool gateway, adapters (Claude, Codex, fake), engine, provider keys
  connectors/        read-only Klaviyo client
  audit/             check library, snapshot loader, checks, scorecards
  vault.py           envelope encryption
  connections.py     connected accounts
  events.py          hash-chained event log
  api/               HTTP API
  console/           ops API and the console page
  cli.py, demo.py, config.py, seed.py
skills/              5 role skills, 23 area skills, FORMAT.md
docs/                ARCHITECTURE.md, CLIENT-HUB-INTEGRATION.md
tests/               unit, service, API, console and audit tests
```

---

## Testing

```bash
.venv/bin/python -m pytest -q
```

The suite covers tenant isolation, every permission level, approval binding and single use, the full chain, cross-model QA with rework and give-up, approval pause and resume, tool denials in the trace, the vault (including ciphertext swap between clients), the event chain, the Klaviyo read-only allowlist, job scheduling, the API and console over HTTP, the skills validator, and every audit check. Synthetic fixtures only; no client data ships in the repository.

Optional integration runs against real data you hold locally:

```bash
WAVYOS_TEST_SNAPSHOT_ROOT=/path/to/audit/output WAVYOS_TEST_AUDIT_OS_ROOT=/path/to/audit-os .venv/bin/python -m pytest -q
```

---

## Deploying to production

This release runs on one machine with SQLite. Before paying clients use it:

1. **Database.** Point `WAVYOS_DATABASE_URL` at Postgres and add migrations (Alembic). Tables are created with `init-db` today.
2. **Secrets.** Replace the local master key file with a cloud KMS key provider (implement `wrap` and `unwrap` in `vault.py`).
3. **Hosting.** Run `wavyos serve` behind HTTPS, and `wavyos work --loop` plus a scheduler for `wavyos schedule` as separate processes.
4. **Sign-in.** Add SSO (OIDC) for staff and client stakeholders; API tokens stay for automation.
5. **Backups.** Database, master key and event-hash anchors, together.

---

## Known limits and roadmap

- **Klaviyo writes.** `build_draft` records a draft spec today; the platform write connector comes next, behind the same approved-plan check.
- **Job handlers.** Ten job contracts are registered without handlers yet (post-send review, weekly trends, meeting prep, monthly report and others).
- **Client Hub feed.** The contract with the Wavy Client Hub is in `docs/CLIENT-HUB-INTEGRATION.md`; the feed generator and per-row client visibility are next.
- **Deep-track audit checks.** 8 checks need the deep collector; 3 more need new collectors (sending domains, consent census, link checking).
- **Thresholds.** Skill and check thresholds are Wavy starting defaults; regulator citations in skills should be confirmed by counsel before they're relied on.

## Licensing

Proprietary to Wavy Studios. Not for redistribution without written permission.
