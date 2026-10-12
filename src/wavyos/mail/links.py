"""Signed links in emails. The unsubscribe link carries the person's id and an HMAC of it, keyed by a key
derived from the vault's master key, so it works without signing in and can't be forged for someone else."""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode

PURPOSE = "email-unsubscribe"


def _sig(vault, user_id: str) -> str:
    return hmac.new(vault.derive(PURPOSE), user_id.encode(), hashlib.sha256).hexdigest()[:40]


def unsubscribe_url(app_url: str, vault, user_id: str) -> str:
    return f"{app_url}/email/unsubscribe?" + urlencode({"u": user_id, "t": _sig(vault, user_id)})


def verify(vault, user_id: str, token: str) -> bool:
    return bool(user_id and token) and hmac.compare_digest(_sig(vault, user_id), token)
