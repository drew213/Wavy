from __future__ import annotations

import base64
import os
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from wavyos import models as m
from wavyos.cli import main
from wavyos.config import load_settings, normalize_database_url
from wavyos.db import utcnow
from wavyos.ratelimit import Limiter, prune
from wavyos.vault import EnvKeyProvider, LocalKeyProvider, Vault, VaultError, key_provider


def test_host_database_urls_get_the_psycopg_driver():
    assert normalize_database_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"
    assert normalize_database_url("sqlite:///var/x.db") == "sqlite:///var/x.db"
    s = load_settings({"WAVYOS_DATABASE_URL": "postgres://u:secret@h/db", "WAVYOS_MASTER_KEY": "k" * 40})
    assert s.database_url.startswith("postgresql+psycopg://")
    assert "secret" not in repr(s) and "k" * 40 not in repr(s)


def test_env_master_key_seals_and_opens(tmp_path):
    raw = base64.b64encode(os.urandom(32)).decode()
    vault = Vault(EnvKeyProvider(raw))
    blob, ref = vault.seal("pk_live_secret", b"bind")
    assert ref.startswith("env:")
    with Vault(EnvKeyProvider(raw)).open(blob, ref, b"bind") as secret:
        assert secret == "pk_live_secret"
    with pytest.raises(VaultError):
        Vault(EnvKeyProvider(base64.b64encode(os.urandom(32)).decode())).open(blob, ref, b"bind").__enter__()


def test_env_master_key_accepts_long_passphrases_and_rejects_short_ones():
    assert EnvKeyProvider("x" * 48).key_ref == EnvKeyProvider("x" * 48).key_ref
    with pytest.raises(VaultError):
        EnvKeyProvider("too-short")


def test_key_provider_prefers_the_env_key(tmp_path):
    env = load_settings({"WAVYOS_MASTER_KEY": "y" * 40, "WAVYOS_MASTER_KEY_FILE": str(tmp_path / "m.key")})
    assert isinstance(key_provider(env), EnvKeyProvider)
    assert not (tmp_path / "m.key").exists()
    local = load_settings({"WAVYOS_MASTER_KEY_FILE": str(tmp_path / "m.key")})
    assert isinstance(key_provider(local), LocalKeyProvider)


def test_limiter_blocks_after_the_limit_per_key(session):
    ip = "203.0.113.9"
    lim = Limiter("t", 3, 60)
    assert [lim.allow(session, ip, now=6000) for _ in range(4)] == [True, True, True, False]
    assert lim.allow(session, "198.51.100.1", now=6000)
    # Halfway through the next window the last one's 3 attempts count as 1.5.
    assert [lim.allow(session, ip, now=6090) for _ in range(3)] == [True, True, False]
    assert lim.allow(session, ip, now=6200)
    assert all(ip not in k for k in session.scalars(select(m.RateLimit.key)))


def test_limiter_counts_are_shared_and_pruned(factory):
    lim = Limiter("t", 2, 60)
    with factory() as a, factory() as b:  # two web processes
        assert lim.allow(a, "k") and lim.allow(b, "k")
        assert not lim.allow(a, "k")
    with factory() as s:
        s.execute(update(m.RateLimit).values(expires_at=utcnow() - timedelta(seconds=1)))
        assert prune(s) == 1
        s.commit()


def test_login_is_rate_limited(tmp_path):
    from fastapi.testclient import TestClient

    from wavyos.api import create_app
    from wavyos.config import Settings
    from wavyos.db import create_all, make_engine, make_sessionmaker
    eng = make_engine(f"sqlite:///{tmp_path / 'rl.db'}")
    create_all(eng)
    app = create_app(factory=make_sessionmaker(eng),
                     services={"settings": Settings(database_url="sqlite://", master_key_file=tmp_path / "m.key")})
    c = TestClient(app)
    codes = [c.post("/v1/ops/login", json={"email": "nobody@x.test", "password": "wrong-password"}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_ensure_admin_sets_up_once_and_never_resets_a_password(tmp_path, monkeypatch, capsys):
    from wavyos import accounts
    from wavyos.db import make_engine, make_sessionmaker
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{tmp_path / 'a.db'}")
    monkeypatch.setenv("WAVYOS_MASTER_KEY", "z" * 40)
    assert main(["init-db"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_EMAIL", "Andrew@Wavy.test")
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "first-password-123")
    assert main(["ensure-admin"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "second-password-456")
    assert main(["ensure-admin"]) == 0
    out = capsys.readouterr().out
    assert out.count("organization wavy created") == 1 and "wos_" not in out
    with make_sessionmaker(make_engine(f"sqlite:///{tmp_path / 'a.db'}"))() as s:
        accounts.sign_in(s, "andrew@wavy.test", "first-password-123")
        with pytest.raises(accounts.SignInFailed):
            accounts.sign_in(s, "andrew@wavy.test", "second-password-456")


def test_ensure_admin_sets_up_once_and_never_resets_a_password(tmp_path, monkeypatch, capsys):
    from wavyos import accounts
    from wavyos.db import make_engine, make_sessionmaker
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{tmp_path / 'a.db'}")
    monkeypatch.setenv("WAVYOS_MASTER_KEY", "z" * 40)
    assert main(["init-db"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_EMAIL", "Andrew@Wavy.test")
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "first-password-123")
    assert main(["ensure-admin"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "second-password-456")
    assert main(["ensure-admin"]) == 0
    out = capsys.readouterr().out
    assert out.count("organization wavy created") == 1 and "wos_" not in out
    with make_sessionmaker(make_engine(f"sqlite:///{tmp_path / 'a.db'}"))() as s:
        accounts.sign_in(s, "andrew@wavy.test", "first-password-123")
        with pytest.raises(accounts.SignInFailed):
            accounts.sign_in(s, "andrew@wavy.test", "second-password-456")


def test_ensure_admin_sets_up_once_and_never_resets_a_password(tmp_path, monkeypatch, capsys):
    from wavyos import accounts
    from wavyos.db import make_engine, make_sessionmaker
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{tmp_path / 'a.db'}")
    monkeypatch.setenv("WAVYOS_MASTER_KEY", "z" * 40)
    assert main(["init-db"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_EMAIL", "Andrew@Wavy.test")
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "first-password-123")
    assert main(["ensure-admin"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "second-password-456")
    assert main(["ensure-admin"]) == 0
    out = capsys.readouterr().out
    assert out.count("organization wavy created") == 1 and "wos_" not in out
    with make_sessionmaker(make_engine(f"sqlite:///{tmp_path / 'a.db'}"))() as s:
        accounts.sign_in(s, "andrew@wavy.test", "first-password-123")
        with pytest.raises(accounts.SignInFailed):
            accounts.sign_in(s, "andrew@wavy.test", "second-password-456")


def test_ensure_admin_sets_up_once_and_never_resets_a_password(tmp_path, monkeypatch, capsys):
    from wavyos import accounts
    from wavyos.db import make_engine, make_sessionmaker
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{tmp_path / 'a.db'}")
    monkeypatch.setenv("WAVYOS_MASTER_KEY", "z" * 40)
    assert main(["init-db"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_EMAIL", "Andrew@Wavy.test")
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "first-password-123")
    assert main(["ensure-admin"]) == 0
    monkeypatch.setenv("WAVYOS_ADMIN_PASSWORD", "second-password-456")
    assert main(["ensure-admin"]) == 0
    out = capsys.readouterr().out
    assert out.count("organization wavy created") == 1 and "wos_" not in out
    with make_sessionmaker(make_engine(f"sqlite:///{tmp_path / 'a.db'}"))() as s:
        accounts.sign_in(s, "andrew@wavy.test", "first-password-123")
        with pytest.raises(accounts.SignInFailed):
            accounts.sign_in(s, "andrew@wavy.test", "second-password-456")


def test_serve_with_worker_starts_the_worker_beside_the_api(tmp_path, monkeypatch):
    import threading

    import uvicorn

    from wavyos import cli
    started = threading.Event()
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.wait(2))
    monkeypatch.setattr(cli, "cmd_work", lambda args, settings: (started.set(), threading.Event().wait())
                        if args.loop and args.schedule_every else None)
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{tmp_path / 's.db'}")
    monkeypatch.setenv("WAVYOS_MASTER_KEY", "z" * 40)
    assert main(["serve", "--with-worker"]) == 0
    assert started.is_set()


def test_worker_schedules_due_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WAVYOS_DATABASE_URL", f"sqlite:///{tmp_path / 'w.db'}")
    monkeypatch.setenv("WAVYOS_MASTER_KEY", "z" * 40)
    assert main(["init-db"]) == 0
    assert main(["work", "--schedule-every", "60"]) == 0
    assert "processed" in capsys.readouterr().out
