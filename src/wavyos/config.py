"""Settings, read from the environment when asked (never at import time).

No secret ever has a default here: the master key lives in its own 0600 file and API keys
only ever arrive through the API, into the vault.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

DEFAULT_DATABASE_URL = "sqlite:///var/wavyos.db"
DEFAULT_MASTER_KEY_FILE = "var/master.key"
DEFAULT_AUDIT_OUTPUT_ROOT = "var/audit-output"
DEFAULT_AUDIT_OS_ROOT = "var/audit-os"


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    master_key_file: Path = Path(DEFAULT_MASTER_KEY_FILE)
    audit_output_root: Path = Path(DEFAULT_AUDIT_OUTPUT_ROOT)
    audit_os_root: Path = Path(DEFAULT_AUDIT_OS_ROOT)

    def __repr__(self) -> str:  # the URL may carry a database password: show the scheme only
        scheme = self.database_url.split(":", 1)[0]
        return (f"Settings(database_url={scheme}:..., master_key_file={str(self.master_key_file)!r}, "
                f"audit_output_root={str(self.audit_output_root)!r}, audit_os_root={str(self.audit_os_root)!r})")


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    return Settings(
        database_url=env.get("WAVYOS_DATABASE_URL") or DEFAULT_DATABASE_URL,
        master_key_file=Path(env.get("WAVYOS_MASTER_KEY_FILE") or DEFAULT_MASTER_KEY_FILE),
        audit_output_root=Path(env.get("WAVYOS_AUDIT_OUTPUT_ROOT") or DEFAULT_AUDIT_OUTPUT_ROOT),
        audit_os_root=Path(env.get("WAVYOS_AUDIT_OS_ROOT") or DEFAULT_AUDIT_OS_ROOT),
    )


def ensure_sqlite_dir(database_url: str) -> None:
    """For a file-backed SQLite URL, create the parent directory so the first connect works."""
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        path = database_url[len(prefix):].split("?", 1)[0]
        if path and path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
