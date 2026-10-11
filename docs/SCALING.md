# Scaling Wavy OS

How far the current setup goes, what was built so it can grow, and the move to paid hosting. Decided Oct 11, 2026: **stay on the free plan until the first paying brand**, then make the move in "Step 1" below.

## What every brand's data costs

| Data | Per large brand (about 190k orders in 24 months) | Per typical brand (about 20k orders) | Kept |
|---|---|---|---|
| Orders (`audit_orders`: ids, time, value, item titles; no personal data) | about 30 MB | about 3 MB | Always. Re-audits add only new orders. |
| Raw audit artifacts (`audit_artifacts`: the API responses one run collected) | about 5–10 MB per run | about 1–2 MB per run | The last 3 finished runs per brand (`housekeeping.py`). Older runs keep their row, stage history and scorecard. |
| Scorecards, check results, events, tasks, intake answers | well under 1 MB | well under 1 MB | Always. |

So a brand's whole history costs roughly 5–60 MB. That is the number to watch, not the brand count.

## What the free setup can carry

Render free web service (512 MB, sleeps after 15 idle minutes) with the worker inside it, and Neon free (0.5 GB storage).

| Limit | Where it breaks | What you'll see |
|---|---|---|
| Neon free storage, 0.5 GB | about 10–15 large brands or 80–100 typical ones | Writes start failing. Check the size in the Neon console weekly once real brands sign up. |
| The service sleeps | the first audit that runs while nobody has a page open | The audit pauses until someone visits; nothing is lost (stages resume), it's just late. Scheduled jobs don't run overnight. |
| Klaviyo reporting API: 40 calls per account per day (our cap; Klaviyo allows 225) | never for snapshot audits (5 calls each) | A run that would cross it is refused with a clear reason. |
| Worker throughput | with `--worker-threads 4`, roughly 200+ snapshot audits a day while awake | Audits wait in the queue. Each audit spends most of its time waiting on Klaviyo's pacing, so threads help almost linearly. |

## Built so it can grow (done)

- **Migrations (Alembic).** Every schema change is a revision in `src/wavyos/migrations/versions/`, applied by `wavyos init-db` on deploy. The live database, made before migrations, is adopted automatically on its first upgrade. Two processes running `init-db` at once take turns (a Postgres advisory lock).
- **Brand data kept per brand and reused.** Orders are stored once per brand and re-audits read only what's new since the last one, with a 2-day overlap.
- **Shared rate limits.** Sign-up and sign-in limits are counted in the database (hashed keys, no IPs or emails stored), so they hold across any number of web instances.
- **Several jobs at once.** `wavyos work --threads N` (and `serve --with-worker --worker-threads N`) runs jobs in parallel; claims are atomic, so any number of workers can share the queue. Jobs for one brand take turns on its event chain; different brands run in parallel.
- **Crash recovery that tells a dead worker from a slow one.** Running jobs touch a heartbeat every minute. Only a job whose heartbeat has stopped for 10 minutes is put back in the queue (up to 3 tries, then marked failed with a reason). A two-hour audit that's still working is left alone.
- **Event log safe under concurrency.** Two writers that take the same sequence number no longer fail; the loser links onto the winner. Event ids are 64-bit on Postgres (32-bit runs out at 2.1 billion rows). Allowed reads aren't logged, so viewing a page never writes; denials and every change still are.
- **Queries that don't grow with brand count.** The staff console's lists (overview, approvals, jobs, executions, events) use one grouped query per table across all visible brands, and the scheduler checks which runs exist in batches of 100 brands. Tests assert the query count is the same at 1 brand and 25.
- **Indexes for the hot paths.** The job queue is indexed on (status, due time); orders on (brand, order time).
- **Short transactions.** The job runner commits its authorization before the job starts, so two jobs for the same brand run side by side instead of waiting on the brand's event chain.
- **Connection pool size** is set per process with `WAVYOS_DB_POOL_SIZE` (default 5) and `WAVYOS_DB_MAX_OVERFLOW` (default 10). Keep (processes × (size + overflow)) under the database's connection limit, or use Neon's pooled connection string.
- **Pruning.** The scheduler tick clears expired rate-limit rows and raw artifacts beyond each brand's last 3 runs.
- **Error reporting.** Set `SENTRY_DSN` and errors, including failed jobs, go to Sentry (free plan). Nothing personal is sent.

## The move to paid hosting

### Step 1: at the first paying brand (about $45–60 a month)

Prices as of late 2026; check them at the time.

| Piece | Choice | About |
|---|---|---|
| Web | Render Starter web service (always on) | $7 |
| Worker | Render Starter background worker: `wavyos init-db && wavyos work --loop --interval 2 --schedule-every 300 --threads 4` | $7 |
| Database | Neon Launch (10 GB included, 7-day point-in-time restore) **or** Render Postgres basic-1gb with daily backups | $19–25 |
| Errors | Sentry Developer (free) | $0 |
| Uptime | Better Stack or UptimeRobot free, pinging `/healthz` | $0 |

Staying on Neon means no data move: point the paid Render services at the same `WAVYOS_DATABASE_URL` and carry the same `WAVYOS_MASTER_KEY` over (without it, stored Klaviyo keys can't be opened). Then remove `--with-worker` from the web service's start command so only the worker runs jobs.

10 GB carries about 300 large brands or 2,000+ typical ones.

Checklist for the move:

1. Upgrade Neon (or create Render Postgres and restore a `pg_dump` into it).
2. Create the worker service with the same environment group as the web service.
3. Change the web start command to drop `--with-worker`; keep `wavyos init-db && wavyos ensure-admin` as the pre-deploy step.
4. Set `SENTRY_DSN` on both services.
5. Add the uptime check.
6. Run one audit end to end and check `/audit/<slug>`.

### Step 2: past about 200 brands or 10 GB

- **Raw artifacts move to object storage** (Cloudflare R2: 10 GB free, then about $0.015 per GB a month, no download fees). `audit_artifacts.payload` becomes a pointer; the collector writes the JSON to R2 and the engine reads it back. The database keeps only what's queried.
- **More worker threads, then more workers.** Raise `--threads` until the database connection count or Klaviyo's per-account limits bind, then add a second worker service. Nothing else changes: claims are atomic and heartbeats are per job.
- **Connection pooling.** Use Neon's pooled connection string (PgBouncer) once there are more than a couple of services.
- **Second web instance** behind Render's load balancer; rate limits are already shared.

### Step 3: thousands of brands

- Partition `audit_orders` by month (Postgres declarative partitioning) so old months stay cheap and queries stay fast.
- Summaries per brand per month (revenue, orders, attributed revenue by channel) computed once and stored, so dashboards never scan raw orders.
- A dedicated read replica for staff dashboards and exports.

## Backups and restore

- **Free plan now:** Neon free keeps about a day of history for restores. Before the first paying brand, also take a weekly `pg_dump` to Andrew's machine (`pg_dump "$WAVYOS_DATABASE_URL" > var/backups/wavyos-$(date +%F).sql`; `var/` is git-ignored).
- **Paid:** Neon Launch restores to any point in the last 7 days; Render Postgres keeps daily backups.
- **Always keep the master key with the backups.** A restored database without `WAVYOS_MASTER_KEY` has unreadable Klaviyo keys; brands would have to reconnect.
- **Restore drill** before relying on it: restore into a new Neon branch, point a local `wavyos serve` at it, and open a brand's `/audit/<slug>`.
