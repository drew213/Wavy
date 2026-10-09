from __future__ import annotations

import os

import re
from pathlib import Path

import pytest

from wavyos import cli
from wavyos import models as m
from wavyos import tenancy
from wavyos.db import make_engine, make_sessionmaker

AUDIT_OS = Path(os.environ.get("WAVYOS_TEST_AUDIT_OS_ROOT", "var/audit-os"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "var" / "cli.db"
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("WAVYOS_MASTER_KEY_FILE", str(tmp_path / "var" / "master.key"))
    monkeypatch.setenv("WAVYOS_AUDIT_OS_ROOT", str(AUDIT_OS))
    return make_sessionmaker(make_engine(f"sqlite:///{db}"))


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_init_bootstrap_token_client(env, capsys):
    assert run(capsys, "init-db")[0] == 0
    code, out, _ = run(capsys, "bootstrap", "--org-name", "Agency", "--org-slug", "agency",
                       "--admin-email", "Admin@Agency.test", "--admin-name", "Admin")
    assert code == 0
    token = out.strip().splitlines()[-1]
    assert token.startswith(tenancy.TOKEN_PREFIX)
    with env() as s:
        assert tenancy.user_for_token(s, token).email == "admin@agency.test"
        assert s.query(m.ApiToken).filter(m.ApiToken.token_hash == token).count() == 0
    # bootstrapping the same org twice fails cleanly
    code, _, err = run(capsys, "bootstrap", "--org-name", "Agency", "--org-slug", "agency",
                       "--admin-email", "x@agency.test", "--admin-name", "X")
    assert code == 2 and "already exists" in err

    code, out, _ = run(capsys, "token", "--email", "admin@agency.test", "--label", "laptop")
    second = out.strip().splitlines()[-1]
    assert code == 0 and second.startswith(tenancy.TOKEN_PREFIX) and second != token
    assert run(capsys, "token", "--email", "nobody@agency.test")[0] == 2

    code, out, _ = run(capsys, "client", "create", "--as", "admin@agency.test", "--name", "Brand A", "--slug", "brand-a",
                       "--timezone", "America/New_York")
    assert code == 0 and "brand-a" in out
    code, out, _ = run(capsys, "verify-events", "--client", "brand-a")
    assert code == 0 and "intact" in out
    code, out, _ = run(capsys, "schedule")
    assert code == 0 and "queued" in out
    code, out, _ = run(capsys, "work")
    assert "processed" in out


def test_cli_respects_roles(env, capsys):
    run(capsys, "init-db")
    run(capsys, "bootstrap", "--org-name", "Agency", "--org-slug", "agency", "--admin-email", "admin@agency.test",
        "--admin-name", "Admin")
    with env() as s:
        admin = s.query(m.User).filter_by(email="admin@agency.test").one()
        from wavyos.scope import scope_for_user
        tenancy.add_user(s, scope_for_user(s, admin, None), email="nobody@agency.test", name="No roles")
        s.commit()
    code, _, err = run(capsys, "client", "create", "--as", "nobody@agency.test", "--name", "X", "--slug", "xx")
    assert code == 2 and "client not found" in err  # no roles at all: same as not existing


@pytest.mark.skipif(not (AUDIT_OS / "sop").is_dir(), reason="audit-os isn't on this machine")
def test_seed_standards_cli_is_idempotent(env, capsys):
    run(capsys, "init-db")
    run(capsys, "bootstrap", "--org-name", "Agency", "--org-slug", "agency", "--admin-email", "admin@agency.test",
        "--admin-name", "Admin")
    code, out, _ = run(capsys, "seed", "standards", "--as", "admin@agency.test")
    assert code == 0 and "0 unchanged" in out
    code, out, _ = run(capsys, "seed", "standards", "--as", "admin@agency.test")
    assert code == 0 and re.search(r"^0 published, \d+ unchanged$", out.strip().splitlines()[-1])
    assert "published  " not in out
    with env() as s:
        assert all(st.version == 1 for st in s.query(m.Standard))
        assert s.query(m.Standard).filter_by(key="checks.audit.library").count() == 1
    # only an org admin can publish standards
    with env() as s:
        admin = s.query(m.User).filter_by(email="admin@agency.test").one()
        from wavyos.scope import scope_for_user
        lead = tenancy.add_user(s, scope_for_user(s, admin, None), email="lead@agency.test", name="Lead")
        tenancy.grant(s, scope_for_user(s, admin, None), user_id=lead.id, role="account_lead", client_id=None)
        s.commit()
    code, _, err = run(capsys, "seed", "standards", "--as", "lead@agency.test")
    assert code == 2 and "standard.publish" in err


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for cmd in ("init-db", "bootstrap", "token", "client", "seed", "schedule", "work", "serve", "verify-events"):
        assert cmd in out
