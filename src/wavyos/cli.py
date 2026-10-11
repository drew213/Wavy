"""`wavyos` command line: database setup, bootstrap, tokens, seeding, the job loop and the server.

Commands that change data act as a named person (`--as EMAIL`), so the services apply that
person's roles exactly as they would over the API. Nothing here prints a secret, except the
API token from `bootstrap` and `token`, once, at creation.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from sqlalchemy import select

from . import events, housekeeping, jobs, monitoring, tenancy
from . import models as m
from .config import Settings, ensure_sqlite_dir, load_settings
from .db import make_engine, make_sessionmaker, transaction
from .scope import InvalidState, NotFound, NotPermitted, scope_for_user


class CliError(Exception):
    pass


def _factory(settings: Settings):
    ensure_sqlite_dir(settings.database_url)
    return make_sessionmaker(make_engine(settings.database_url))


def _org(session, slug: Optional[str]) -> Optional[m.Organization]:
    if slug:
        org = session.scalar(select(m.Organization).where(m.Organization.slug == slug))
        if org is None:
            raise CliError(f"no organization {slug}")
        return org
    return None


def _user(session, email: str, org_slug: Optional[str]) -> m.User:
    q = select(m.User).where(m.User.email == email.lower())
    org = _org(session, org_slug)
    if org:
        q = q.where(m.User.org_id == org.id)
    users = session.scalars(q).all()
    if not users:
        raise CliError(f"no user {email}" + (f" in {org_slug}" if org_slug else ""))
    if len(users) > 1:
        raise CliError(f"{email} exists in several organizations; pass --org")
    return users[0]


def _client(session, user: m.User, slug: str) -> m.Client:
    c = session.scalar(select(m.Client).where(m.Client.org_id == user.org_id, m.Client.slug == slug))
    if c is None:
        raise CliError(f"no client {slug}")
    return c


def _load_jobmods() -> None:
    try:
        import wavyos.jobmods  # noqa: F401
    except ModuleNotFoundError as e:
        if e.name != "wavyos.jobmods":
            raise


def _klaviyo_factory(api_key: str):
    from .connectors.klaviyo import KlaviyoClient  # read-only client; imported only when a job needs it
    return KlaviyoClient(api_key)


def _job_services(settings: Settings) -> dict:
    """What job handlers find in ctx.services (see wavyos.jobmods)."""
    from .vault import Vault, key_provider
    return {"vault": Vault(key_provider(settings)), "klaviyo_factory": _klaviyo_factory,
            "settings": settings}


# ---------------------------------------------------------------- commands

def cmd_init_db(args, settings: Settings) -> int:
    from . import migrate
    ensure_sqlite_dir(settings.database_url)
    engine = make_engine(settings.database_url)
    print(migrate.upgrade(engine))
    drift = migrate.differences(engine)
    engine.dispose()
    if drift:
        print(f"warning: the database differs from models.py in {len(drift)} place(s), first: {drift[0]}")
    print("database ready")
    return 0


def cmd_db_revision(args, settings: Settings) -> int:
    from . import migrate
    path = migrate.new_revision(args.message)
    print(f"wrote {path}; read it before committing")
    return 0


def cmd_bootstrap(args, settings: Settings) -> int:
    with transaction(_factory(settings)) as s:
        org, admin = tenancy.bootstrap_org(s, name=args.org_name, slug=args.org_slug, admin_email=args.admin_email,
                                           admin_name=args.admin_name)
        token = tenancy.issue_token(s, admin, "bootstrap")
    print(f"organization {org.slug} created; admin {admin.email}")
    print("API token (shown once, store it now):")
    print(token)
    return 0


def cmd_ensure_admin(args, settings: Settings) -> int:
    """First-deploy setup from the host's environment, safe to run on every deploy: create the
    organization and its admin if missing, and give the admin a password if they have none.
    Never prints a token and never replaces an existing password."""
    import os
    from . import accounts
    email = (os.environ.get("WAVYOS_ADMIN_EMAIL") or "").strip().lower()
    if not email:
        print("WAVYOS_ADMIN_EMAIL not set; nothing to do")
        return 0
    slug = settings.signup_org or "wavy"
    with transaction(_factory(settings)) as s:
        org = s.scalar(select(m.Organization).where(m.Organization.slug == slug))
        if org is None:
            org, _ = tenancy.bootstrap_org(s, name=os.environ.get("WAVYOS_ORG_NAME") or "Wavy Studios", slug=slug,
                                           admin_email=email, admin_name=os.environ.get("WAVYOS_ADMIN_NAME") or "Admin")
            print(f"organization {slug} created with admin {email}")
        user = s.scalar(select(m.User).where(m.User.org_id == org.id, m.User.email == email))
        if user is None:
            raise CliError(f"{email} is not a user in {slug}; add them in the console")
        has_password = s.scalar(select(m.PasswordCredential.id).where(m.PasswordCredential.user_id == user.id))
        password = os.environ.get("WAVYOS_ADMIN_PASSWORD") or ""
        if not has_password and password:
            try:
                accounts.set_password(s, user, password)
            except ValueError as e:
                raise CliError(f"WAVYOS_ADMIN_PASSWORD: {e}")
            print(f"{email} can now sign in at /login")
    return 0


def cmd_token(args, settings: Settings) -> int:
    with transaction(_factory(settings)) as s:
        user = _user(s, args.email, args.org)
        if not user.active:
            raise CliError(f"{user.email} is deactivated")
        token = tenancy.issue_token(s, user, args.label)
    print(f"API token for {user.email} (shown once, store it now):")
    print(token)
    return 0


def cmd_password(args, settings: Settings) -> int:
    import getpass
    from . import accounts
    password = getpass.getpass(f"New password for {args.email} (hidden): ")
    if getpass.getpass("Again: ") != password:
        raise CliError("the passwords don't match")
    with transaction(_factory(settings)) as s:
        user = _user(s, args.email, args.org)
        try:
            accounts.set_password(s, user, password)
        except ValueError as e:
            raise CliError(str(e))
    print(f"{user.email} can now sign in at /login")
    return 0


def cmd_client_create(args, settings: Settings) -> int:
    with transaction(_factory(settings)) as s:
        actor = _user(s, args.actor, args.org)
        c = tenancy.create_client(s, scope_for_user(s, actor, None), name=args.name, slug=args.slug,
                                  timezone=args.timezone, website=args.website)
    print(f"client {c.slug} created ({c.id})")
    return 0


def cmd_seed_standards(args, settings: Settings) -> int:
    from .seed import import_audit_os_standards
    with transaction(_factory(settings)) as s:
        actor = _user(s, args.actor, args.org)
        seeded = import_audit_os_standards(s, scope_for_user(s, actor, None), args.root or settings.audit_os_root)
    for x in seeded:
        print(f"{'published' if x.changed else 'unchanged'}  {x.key} v{x.version} ({x.area})")
    print(f"{sum(x.changed for x in seeded)} published, {sum(not x.changed for x in seeded)} unchanged")
    return 0


def cmd_seed_skills(args, settings: Settings) -> int:
    from . import skills
    errs = skills.validate(args.root or skills.DEFAULT_ROOT)
    if errs:
        print("skills don't validate:\n  " + "\n  ".join(errs), file=sys.stderr)
        return 2
    with transaction(_factory(settings)) as s:
        actor = _user(s, args.actor, args.org)
        out = skills.publish(s, scope_for_user(s, actor, None), args.root or skills.DEFAULT_ROOT)
    changed = sum(c for _, _, c in out)
    print(f"{len(out)} skill standards: {changed} published, {len(out) - changed} unchanged")
    return 0


def cmd_skills_check(args, settings: Settings) -> int:
    from . import skills
    errs = skills.validate(args.root or skills.DEFAULT_ROOT)
    print("all skills valid" if not errs else "\n".join(errs))
    return 0 if not errs else 2


def cmd_schedule(args, settings: Settings) -> int:
    _load_jobmods()
    with transaction(_factory(settings)) as s:
        org = _org(s, args.org)
        runs = jobs.enqueue_due(s, org_id=org.id if org else None)
        keys = [r.job_key for r in runs]
    print(f"queued {len(keys)} job run(s)" + (": " + ", ".join(sorted(set(keys))) if keys else ""))
    return 0


def cmd_work(args, settings: Settings) -> int:
    """Run queued jobs, then queued executions (model and QA work). --loop keeps the worker running."""
    import threading
    import time
    import traceback
    from .execution import engine
    from .execution.providers import adapter_factory
    _load_jobmods()
    factory = _factory(settings)
    services = _job_services(settings)
    adapters = adapter_factory(factory, services["vault"])
    threads = getattr(args, "threads", 1)
    if threads > 1 and settings.database_url.startswith("sqlite"):
        print("worker     SQLite allows one writer at a time; running jobs on 1 thread")
        threads = 1

    def show(r: m.JobRun) -> None:
        print(f"job        {r.status:16} {r.job_key} {r.summary.splitlines()[0] if r.summary else ''}".rstrip())

    def job_loop() -> None:
        while True:
            try:
                for r in jobs.run_pending(factory, services, limit=args.limit):
                    show(r)
            except Exception as e:
                monitoring.report(e)
                traceback.print_exc()
            time.sleep(args.interval)

    # Looping with several threads, each claims its own jobs so a long audit never holds up the rest.
    background = args.loop and threads > 1 and not args.executions_only
    if background:
        for i in range(threads):
            threading.Thread(target=job_loop, name=f"wavyos-jobs-{i}", daemon=True).start()
    failed, n_jobs, n_ex = False, 0, 0
    next_schedule = 0.0
    while True:
        try:
            if args.schedule_every and time.monotonic() >= next_schedule:
                with transaction(factory) as s:
                    requeued = jobs.requeue_stuck(s)
                    queued = jobs.enqueue_due(s)
                    cleared = housekeeping.run(s)
                if queued or requeued:
                    print(f"scheduler  queued {len(queued)} job run(s), requeued {requeued} stuck")
                if cleared:
                    print(f"cleanup    {cleared}")
                next_schedule = time.monotonic() + args.schedule_every
            if not args.executions_only and not background:
                for r in jobs.run_pending(factory, services, limit=args.limit, threads=threads):
                    n_jobs += 1
                    failed |= r.status == "failed"
                    show(r)
            if not args.jobs_only:
                for ex in engine.work(factory, adapters, limit=args.limit):
                    n_ex += 1
                    failed |= ex.status == "failed"
                    why = ex.error.splitlines()[0] if ex.error else (ex.result or {}).get("summary", "")
                    print(f"execution  {ex.status:16} {ex.purpose:7} {ex.executor} {ex.model} ${ex.cost_usd:.4f} {why[:120]}".rstrip())
        except Exception as e:
            if not args.loop:
                raise
            monitoring.report(e)
            traceback.print_exc()  # a bad pass (say the database blipped) shouldn't stop the worker
        if not args.loop:
            break
        time.sleep(args.interval)
    print(f"{n_jobs} job run(s) and {n_ex} execution(s) processed")
    return 1 if failed else 0


def cmd_provider_add(args, settings: Settings) -> int:
    import getpass
    from .execution.providers import store_key
    from .vault import Vault, key_provider
    secret = getpass.getpass(f"Paste the {args.provider} API key (hidden): ").strip()
    if not secret:
        raise CliError("no key entered")
    with transaction(_factory(settings)) as s:
        actor = _user(s, args.actor, args.org)
        store_key(s, scope_for_user(s, actor, None), Vault(key_provider(settings)), args.provider, secret)
    del secret
    print(f"{args.provider} key stored in the vault")
    return 0


def cmd_client_allow(args, settings: Settings) -> int:
    from .execution.adapters import PRICES  # noqa: F401  (validates the module imports)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    bad = [p for p in providers if p not in ("anthropic", "codex", "fake", "human")]
    if bad:
        raise CliError(f"unknown provider(s): {', '.join(bad)}")
    with transaction(_factory(settings)) as s:
        actor = _user(s, args.actor, args.org)
        sc = scope_for_user(s, actor, None)
        if not ({"org_admin", "account_lead"} & sc.actor.roles):
            raise CliError("only an org admin or account lead can choose which providers see a client's data")
        c = _client(s, actor, args.slug)
        c.settings = {**(c.settings or {}), "allowed_providers": providers,
                      **({"daily_budget_usd": args.budget} if args.budget is not None else {})}
        events.record(s, scope_for_user(s, actor, c.id), "client.providers", c, ", ".join(providers) or "none",
                      {"providers": providers, "budget": args.budget})
    print(f"{args.slug}: allowed providers {providers or 'none'}")
    return 0


def cmd_demo(args, settings: Settings) -> int:
    from . import migrate
    from .demo import build
    ensure_sqlite_dir(settings.database_url)
    migrate.upgrade(make_engine(settings.database_url))
    token = build(_factory(settings))
    print("demo workspace ready: organization wavy-demo, client northwind-demo (fictional)")
    print("console token for admin@demo.wavy.test (shown once):")
    print(token)
    return 0


def cmd_serve(args, settings: Settings) -> int:
    import uvicorn
    from .api import create_app
    if args.with_worker:  # single-process hosting (free tiers): the worker runs as a thread beside the API
        import threading
        import time
        import traceback
        work_args = argparse.Namespace(loop=True, interval=2.0, schedule_every=300.0, limit=50,
                                       jobs_only=False, executions_only=False, threads=args.worker_threads)

        def worker() -> None:
            while True:
                try:
                    cmd_work(work_args, settings)
                except Exception:
                    traceback.print_exc()
                    time.sleep(5)

        threading.Thread(target=worker, name="wavyos-worker", daemon=True).start()
    # Behind a host's TLS proxy, trust X-Forwarded-* so request.client is the visitor, not the proxy.
    uvicorn.run(create_app(services={"settings": settings}), host=args.host, port=args.port,
                proxy_headers=True, forwarded_allow_ips=args.forwarded_allow_ips)
    return 0


def cmd_verify_events(args, settings: Settings) -> int:
    with _factory(settings)() as s:
        if args.client:
            q = select(m.Client).where(m.Client.slug == args.client)
            org = _org(s, args.org)
            if org:
                q = q.where(m.Client.org_id == org.id)
            clients = s.scalars(q).all()
            if len(clients) != 1:
                raise CliError(f"no client {args.client}" if not clients else f"{args.client} is ambiguous; pass --org")
            org_id, chain = clients[0].org_id, clients[0].id
        else:
            org = _org(s, args.org)
            if org is None:
                raise CliError("pass --client, or --org to check the organization's own chain")
            org_id, chain = org.id, "org"
        ok, broken = events.verify_chain(s, org_id, chain)
        n = events.count(s, org_id, chain)
    print(f"event chain intact ({n} events)" if ok else f"event chain BROKEN at seq {broken} ({n} events)")
    return 0 if ok else 1


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wavyos", description="Wavy OS backend")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create or upgrade the database tables (runs migrations)").set_defaults(fn=cmd_init_db)
    r = sub.add_parser("db-revision", help="write a migration for model changes (autogenerated; read it)")
    r.add_argument("-m", "--message", required=True)
    r.set_defaults(fn=cmd_db_revision)
    sub.add_parser("ensure-admin", help="create the org and admin from WAVYOS_ADMIN_* if missing (for deploys)"
                   ).set_defaults(fn=cmd_ensure_admin)

    b = sub.add_parser("bootstrap", help="create an organization and its first admin; prints a token once")
    b.add_argument("--org-name", required=True)
    b.add_argument("--org-slug", required=True)
    b.add_argument("--admin-email", required=True)
    b.add_argument("--admin-name", required=True)
    b.set_defaults(fn=cmd_bootstrap)

    t = sub.add_parser("token", help="issue an API token for a user; prints it once")
    t.add_argument("--email", required=True)
    t.add_argument("--org", help="organization slug (when the email exists in several)")
    t.add_argument("--label", default="cli")
    t.set_defaults(fn=cmd_token)

    pw = sub.add_parser("password", help="set a user's browser sign-in password; prompts with hidden input")
    pw.add_argument("--email", required=True)
    pw.add_argument("--org", help="organization slug (when the email exists in several)")
    pw.set_defaults(fn=cmd_password)

    c = sub.add_parser("client", help="client workspaces")
    csub = c.add_subparsers(dest="client_command", required=True)
    cc = csub.add_parser("create", help="create a client workspace")
    cc.add_argument("--as", dest="actor", required=True, help="email of the org admin or account lead acting")
    cc.add_argument("--org")
    cc.add_argument("--name", required=True)
    cc.add_argument("--slug", required=True)
    cc.add_argument("--timezone", default="UTC")
    cc.add_argument("--website")
    cc.set_defaults(fn=cmd_client_create)

    sd = sub.add_parser("seed", help="load reference content")
    ssub = sd.add_subparsers(dest="seed_command", required=True)
    st = ssub.add_parser("standards", help="publish the Audit OS SOPs and check library as agency standards")
    st.add_argument("--as", dest="actor", required=True, help="email of an org admin")
    st.add_argument("--org")
    st.add_argument("--root", help="audit-os folder (default: WAVYOS_AUDIT_OS_ROOT)")
    st.set_defaults(fn=cmd_seed_standards)

    sk = ssub.add_parser("skills", help="validate and publish skills/ (roles and areas) as agency standards")
    sk.add_argument("--as", dest="actor", required=True, help="email of an org admin")
    sk.add_argument("--org")
    sk.add_argument("--root", help="skills folder (default: the repo's skills/)")
    sk.set_defaults(fn=cmd_seed_skills)

    kc = sub.add_parser("skills-check", help="validate the skill files without loading them")
    kc.add_argument("--root")
    kc.set_defaults(fn=cmd_skills_check)

    sc = sub.add_parser("schedule", help="queue every job whose period is due")
    sc.add_argument("--org")
    sc.set_defaults(fn=cmd_schedule)

    w = sub.add_parser("work", help="run queued jobs and executions")
    w.add_argument("--limit", type=int, default=50)
    w.add_argument("--loop", action="store_true", help="keep running")
    w.add_argument("--interval", type=float, default=5.0, help="seconds between passes with --loop")
    w.add_argument("--schedule-every", type=float, default=0, metavar="S",
                   help="also queue due jobs, requeue runs a dead worker left and clear expired data every S seconds; 0 is off")
    w.add_argument("--threads", type=int, default=1, help="jobs to run at once (Postgres only)")
    g = w.add_mutually_exclusive_group()
    g.add_argument("--jobs-only", action="store_true")
    g.add_argument("--executions-only", action="store_true")
    w.set_defaults(fn=cmd_work)

    pv = sub.add_parser("provider", help="model provider keys (stored in the vault)")
    pvs = pv.add_subparsers(dest="provider_command", required=True)
    pa = pvs.add_parser("add", help="store a provider API key; prompts with hidden input")
    pa.add_argument("provider", choices=["anthropic"])
    pa.add_argument("--as", dest="actor", required=True, help="email of an org admin")
    pa.add_argument("--org")
    pa.set_defaults(fn=cmd_provider_add)

    ca = csub.add_parser("allow", help="choose which model providers may receive this client's data")
    ca.add_argument("slug")
    ca.add_argument("--providers", required=True, help="comma list: anthropic,codex (empty string for none)")
    ca.add_argument("--budget", type=float, help="daily model budget in USD")
    ca.add_argument("--as", dest="actor", required=True)
    ca.add_argument("--org")
    ca.set_defaults(fn=cmd_client_allow)

    sub.add_parser("demo", help="create the fictional demo workspace and run its work").set_defaults(fn=cmd_demo)

    sv = sub.add_parser("serve", help="run the HTTP API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--forwarded-allow-ips", default="127.0.0.1",
                    help="proxies whose X-Forwarded-For is trusted ('*' behind a host's load balancer)")
    sv.add_argument("--with-worker", action="store_true",
                    help="also run the worker and scheduler in this process (when the host has no separate worker)")
    sv.add_argument("--worker-threads", type=int, default=1, help="with --with-worker: jobs to run at once (Postgres only)")
    sv.set_defaults(fn=cmd_serve)

    v = sub.add_parser("verify-events", help="check a client's event hash chain")
    v.add_argument("--client", help="client slug")
    v.add_argument("--org")
    v.set_defaults(fn=cmd_verify_events)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    monitoring.init()
    try:
        return args.fn(args, load_settings())
    except (CliError, NotPermitted, NotFound, InvalidState, ValueError) as e:
        print(f"wavyos: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
