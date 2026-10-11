# Working on Wavy OS

Read this first. `README.md` is the full product and API reference; `docs/ARCHITECTURE.md` explains the design. This file is the short version for anyone (person or agent) picking up the build.

## What this is

Wavy OS is Wavy Studios' retention-agency backend: FastAPI + SQLAlchemy, Python 3.12. Brands sign up, connect Klaviyo, answer the lifecycle onboarding questionnaire, and get a measured retention audit saved to their own workspace page. Staff run every client from the ops console.

The brand-facing path, end to end:

| Step | URL | Code |
|---|---|---|
| Sign up / sign in | `/start`, `/login` | `console/static/account.html`, `accounts.py`, `POST /v1/ops/signup` and `/login` in `console/ops.py` |
| Connect Klaviyo (read-only key) | `/onboard` step 1 | `onboard.html`, `POST /v1/ops/onboard/klaviyo-check` |
| Lifecycle questionnaire (8 sections, 49 questions, autosaved) | `/onboard` step 2 | `intake.py` (questions and the answer → audit mapping), `GET/PUT /v1/ops/intake`, table `onboarding_intakes` |
| Live audit (loading screen) | `/onboard` step 3 | `POST /v1/ops/onboard` queues job `client_onboard` (`jobmods/client_onboard.py` → `klaviyo_intake.py`, `storage.py`, `onboarding.py`) |
| Saved audit workspace (Wavy Client Hub layout) | `/audit/<slug>` | `workspace.html`, `GET /v1/ops/clients/<slug>/audit` |
| Staff console | `/console` | `index.html`, the rest of `console/ops.py` |

Plans from audits a brand starts stay `proposed` (no tasks) until a lead approves; audits a lead starts auto-approve.

## Run it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q                      # must stay green (about 240 tests, 15 s)
WAVYOS_DATABASE_URL=sqlite:///$PWD/var/dev.db .venv/bin/wavyos demo   # fictional demo workspace
WAVYOS_DATABASE_URL=sqlite:///$PWD/var/dev.db .venv/bin/wavyos serve --port 8792 --with-worker
```

The suite also runs against Postgres: `WAVYOS_TEST_POSTGRES_URL=postgresql+psycopg://user@host/db pytest -q` gives every test its own schema. Run it before merging anything that touches queries or models.

## Rules of the codebase

- **Tenant isolation is the product.** Every query goes through a scope (`scope.py`: `scope_for_user`, `scope_for_agent`). Never load a client row by id or slug without checking the caller's scope. Brands (`client_owner`, `client_approver`) see only their own client.
- **Every action is a policy action.** New things people can do get an entry in `policy.ACTIONS` with who may perform them, and are checked with `authorize()`.
- **Every schema change is a migration.** Edit `models.py`, run `wavyos db-revision -m "what changed"`, read the file it writes in `src/wavyos/migrations/versions/` (autogenerate misses renames and data moves; fix those by hand), and commit both. `wavyos init-db` applies migrations on every deploy. `tests/test_migrations.py` fails if the models and migrations disagree. New columns on existing tables must be nullable or have a `server_default`, since rows already exist. Never edit a migration that has reached `main`.
- **Jobs for one brand take turns; jobs for different brands run in parallel.** Each job writes to its brand's event chain, which has one writer at a time, so a job holding a long open transaction delays that brand's other jobs. Commit as work completes (the collector checkpoints) rather than holding one transaction for minutes.
- **Secrets.** Klaviyo and model keys only go into the vault (`vault.py`, `connections.py`). Never log, return or store a key in plain text; tests assert this. `WAVYOS_MASTER_KEY` must never change in a running environment, or every stored key becomes unreadable.
- **Never commit** `var/`, `.env` files, keys, database files or client data. The repository is public: anything pushed can be read and copied by anyone. Test fixtures are synthetic only.
- **Every change is an event.** Use `events.record(...)` for state changes, as the existing services do.
- **Jobs produce something or say why not.** A job handler that does nothing must return a `no_action_reason`.
- **Console pages** are static HTML served with a strict Content Security Policy: inline script and style only, no CDNs, fonts and images from `/assets/` (allowlisted in `console/__init__.py`). The visual language is the Wavy Client Hub: Geist and Geist Mono, near-black `#0b0c0d`, lime `#c8ff00`, square corners.
- **Copy** is plain, specific and short. Say what happened and what to do next; no filler.
- Match the surrounding code: small functions, comments only for constraints the code can't show.

## Deployment

**Now (free):** one Render free web service (`--with-worker` runs the job worker and scheduler inside it) plus a free Neon Postgres database (project `wavy-os`, Oregon). Free services sleep after 15 idle minutes, so an audit only runs while someone has the page open.

- Live at **https://wavyos-web.onrender.com** (Render service `wavyos-web`, `srv-db5uj7p42hec73a8tfdg`, workspace Wavy Studios). Brands start at `/start`; staff sign in at `/login`.
- Neon project `wavy-os`: schema and the staff admin (`drew@wavystudios.com`) are set up.
- The Neon URL, master key and admin password are set on the Render service and kept on Andrew's Mac in `wavy-os/var/` (git-ignored). Ask him if you need them; never put them in the repo, an issue or a PR.
- Render builds with `pip install .` and starts with `wavyos init-db && wavyos serve --host 0.0.0.0 --port $PORT --forwarded-allow-ips '*' --with-worker`.

**Later (paid, about $20/month):** `render.yaml` is a Blueprint with a separate worker, Render Postgres and a generated master key. Moving there means migrating the Neon data and carrying the same master key over.

Every push to `main` redeploys the live site. Work on branches and open pull requests; merge only with `pytest -q` green.

## Backlog, in priority order

**The audit comes first.** `docs/AUDIT-PLAN.md` is the plan for the whole audit: collector port, scoring on the brand path, and the workspace mapped to the Client Hub, in phases with acceptance criteria. Work through its phases in order before anything below except fixes. The Node collector, the audit SOP and the analyst brief it ports from are in `reference/` (read-only).

1. **First real brand on the live site:** sign up at `/start` with a real read-only Klaviyo key, complete the questionnaire, and confirm the audit finishes on the free plan (the service must stay awake while it runs) and `/audit/<slug>` shows it. Staff sign-in, pages and headers are already verified live.
2. **Approving a proposed plan should create its tasks.** Today a brand-started audit stores proposed task titles (`onboarding.py`, `first_30_day_plan.status == "proposed"`); a lead's approval needs to turn them into tasks through `operations.py`.
3. **Don't make a returning brand re-enter its Klaviyo key.** If the client already has a live Klaviyo connection, skip step 1 of `/onboard` and run the audit with the stored key (`connections.py`, vault).
4. **Account basics:** password reset and email verification (needs an email provider), and session tokens that survive a new browser tab (today they're in `sessionStorage`).
5. **Workspace sections from the Client Hub:** covered by `docs/AUDIT-PLAN.md` section 5 (section map, data shapes, design system).
6. **Model work in the cloud:** the `codex` executor needs a ChatGPT-signed-in machine and won't run on Render. Store an Anthropic key with `wavyos provider add anthropic` (or `ANTHROPIC_API_KEY`) and allow it per client with `wavyos client allow`.
7. **Shopify** as an order source alongside Klaviyo (`connectors/shopify.py` exists; not wired into onboarding).
8. **Shopify transactional emails, redone: the first paid feature.** Queued behind audit phases 1–3; see `docs/AUDIT-PLAN.md` section 10. Don't start it yet.

Capacity and the move to paid hosting are in `docs/SCALING.md`. Stay on the free plan until the first paying brand.

## Before you open a pull request

- `pytest -q` passes; for model or query changes, also against Postgres.
- New behaviour has a test next to the existing ones (`tests/test_brand_signup.py` and `tests/test_klaviyo_onboarding.py` show the HTTP-level style with fake Klaviyo).
- No secrets, client data or `var/` files in the diff.
- UI changes checked at desktop and 390 px width.
