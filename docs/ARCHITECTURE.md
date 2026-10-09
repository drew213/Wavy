# Wavy OS: backend architecture

Wavy OS is the backend for the **Lifecycle Client Operating System**: the agency playbook, a knowledge base per client, and the operating loop an AI agent runs continuously, with clear ownership and approval rules.

The design follows one principle from the founder's spec: **every insight connects to a decision, every decision to a task, every completed task to a measured result.** The database enforces that chain.

## 1. Tenancy

```
Organization (an agency, e.g. Wavy Studios)
 └── Client workspace (a brand)
      ├── Client knowledge        (layer 2)
      ├── Live operating state    (layer 3)
      ├── Connections             (Klaviyo, Shopify; encrypted credentials)
      └── Members                 (agency staff and client stakeholders)
Organization
 └── Agency standards             (layer 1, shared by all its clients)
```

- Every row that belongs to a client carries `org_id` and `client_id`. Every query goes through a `Scope(org_id, client_id, actor)` object; services refuse a query without one. One client's data can't leak into another's results by a missing filter.
- Sister brands are separate client workspaces. Nothing joins across clients.
- We sell it two ways with the same backend: Wavy operates it for retainer clients (client stakeholders get read and approve access), and later other agencies get their own organization.

## 2. The three knowledge layers

| Layer | Table | Who can write | How it changes |
|---|---|---|---|
| Agency standards | `standards` | Org admins | Versioned. A new version supersedes the old; old versions stay readable. SOPs from `audit-os/sop/` load here. |
| Client knowledge | `client_facts` | Anyone proposes; an owner approves | Every fact starts **proposed** with its source (meeting note, intake form, data). It only becomes **approved** through an approval. A meeting note never silently becomes a brand rule. |
| Live operating state | `insights`, `decisions`, `tasks`, `results`, `approvals`, `issues`, `metric_snapshots`, `experiments` | Agents and people, within policy | Changes are events in the append-only log. |

Each fact and standard is tagged with one of the **23 operating areas** (onboarding through offboarding) and one of the **10 components** (purpose, facts and rules, inputs, SOP, decision rules, examples, quality checklist, owner and approvals, outputs, maintenance). Every fact has `verified_by`, `verified_at` and `review_by`, so stale knowledge shows up in the daily job.

Agency standards never inherit client facts. A successful offer at one client becomes a standard only through an explicit, human-approved promotion that strips client data.

## 3. The operating chain

```
Insight ──► Decision ──► Task ──► Result
(evidence)  (approved)   (owned)   (measured)
```

| Object | Required links | Created by |
|---|---|---|
| **Insight** | Evidence: a check result, metric snapshot or source document | Check engine, monitors, analyst agent, people |
| **Decision** | One or more insights; who decided; the approval if one was needed | Account lead or client, via approval |
| **Task** | Exactly one decision (or a standing job); owner; due date; done-when | Planner, account lead |
| **Result** | Exactly one task; metric, baseline, measurement window, observed value | Analyst agent after the window closes |

Rules the services enforce:
- A task can't move to **done** without its outputs recorded and its quality checklist passed.
- A completed task automatically gets a **pending result** with the window from its decision; the analyst job measures it when the window closes.
- A decision whose tasks are all done and measured is **closed** with its outcome. Closed outcomes feed the learning log (area 19).

## 4. Agents, roles and permissions

Five agent roles, matching the spec: **account lead, analyst, lifecycle strategist, creative producer, QA reviewer**. At first one model may play all of them, but every action is attributed to a role, and the QA reviewer can never approve its own role's output.

Every action an actor can take is registered in the **action catalog** with a permission level:

| Level | Meaning | Examples |
|---|---|---|
| `autonomous` | Do it, log it | Read data, analyze, draft briefs and copy, prepare reports, manage internal tasks, flag issues |
| `approved_plan` | Allowed only inside an approved decision whose scope covers it | Build draft campaigns and flows, prepare audience changes, implement agreed work in a non-live state |
| `explicit_approval` | Each instance needs its own approval from an authorized person | Send client communications, publish campaigns, change live flows, change discounts, suppress or delete profiles, change account settings |

`policy.authorize(actor, action, scope, context)` returns **allow**, **needs_plan** or **needs_approval**, with the reason. The decision is logged either way. Policies can be narrowed per client and per action (e.g. "post-send reports for this client are autonomous after 10 clean runs"), but nothing can be widened past what the organization allows, and three actions can never become autonomous: deleting profiles, changing account settings and moving money or discounts.

Connectors enforce the same thing a second time: a connector method declares its action, and the connector refuses to run it without an authorization token from the policy engine. In v0.1 every Klaviyo method is read-only.

## 5. Jobs: the always-on loop

A **job definition** is `trigger → checks → action → owner role → completion condition`. Triggers are a schedule (`daily`, `weekly`, `biweekly`, `monthly`, `quarterly`) or an event (`campaign.sent`, `flow.status_changed`, `metric.anomaly`, `client.feedback`, `inventory.changed`).

The scheduler writes a **job run** per (definition, client, due time) with a unique key, so a run happens once even if two workers race. A run records its checks, what it produced (insights, tasks, approval requests), cost and completion condition. A run that only observes and produces nothing is marked `no_action` with the reason, so "always-on" never becomes repeated observation without execution.

v0.1 ships the runner, the scheduler and the **daily account health** and **knowledge staleness** jobs. The rest are registered as definitions with their contracts, ready to implement.

## 6. Connectors and credentials

- `connectors/klaviyo.py`: read-only Klaviyo client (pagination, rate limits, revision pin, account identity check). It ports the endpoints the audit collector uses.
- Credentials are stored **encrypted** in `connections.secret_ciphertext` using envelope encryption: each secret is encrypted with a fresh data key; the data key is encrypted with the master key from the configured key provider. Dev provider: a local key file with 0600 permissions. Production: a cloud KMS. Plaintext keys are never logged, returned by the API or written to disk.
- The API never returns a secret. Rotating or revoking a connection is an event.

## 7. Audit engine

The Audit OS check library (`audit-os/checks/check-library.json`) is the first module. It is the baseline assessment step of the two-week cycle.

`audit/engine.py` loads the library, runs every implemented check against an account snapshot, writes a **scorecard** and turns each warn or fail into an **insight** with its evidence. Checks are plain Python functions registered by ID, each tested against fixtures. Unimplemented checks report `not_implemented`, never a verdict.

## 8. Event log

Every state change writes an `events` row: who (user or agent role), what, which object, before/after summary, and a hash chaining it to the previous event for that client. It's the audit trail clients and the agency can rely on, and the input for the learning log.

## 9. Stack and deployment

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | The existing collectors and analysis are Python and Node; analysis is Python-native; the Claude Agent SDK supports Python |
| API | FastAPI | Typed, fast, OpenAPI docs for free |
| Database | Postgres in production, SQLite for dev and tests | Same SQLAlchemy models; JSON columns for flexible payloads |
| Jobs | DB-backed queue with unique run keys | No extra infrastructure at this size; can swap to a managed queue later |
| Secrets | Envelope encryption, pluggable key provider | Local file now, cloud KMS in production |
| Auth | Hashed API tokens per user now; SSO/OIDC before external clients log in | Simple and safe for internal use first |

Nothing is deployed yet. Production hosting is a separate decision (see `docs/ROADMAP.md`).
