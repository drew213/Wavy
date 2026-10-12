"""Live email HTML: stored once per brand per version at audit time, rendered as a preview in the workspace.

The workspace shows each email in a sandboxed frame (no scripts, no same-origin access). Images load from
the brand's image hosts the way an inbox loads them. Klaviyo template tags can't be evaluated outside a
send, so preview() shows each tag's default value ({{ first_name|default:'there' }} -> "there") and drops
the rest; the caption says so.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models as m

MAX_BYTES = 2_000_000
_DEFAULT = re.compile(r"\{\{[^{}]*?\|\s*default\s*:\s*(['\"])(.*?)\1[^{}]*\}\}", re.S)
_VAR = re.compile(r"\{\{[\s\S]{0,400}?\}\}")
_TAG = re.compile(r"\{%[\s\S]{0,600}?%\}")
_SCRIPT = re.compile(r"<script\b[\s\S]*?</script\s*>", re.I)
_REFRESH = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[^>]*>", re.I)
_ON = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)


def sha(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def store(session: Session, org_id: str, client_id: str, template_id: str, html: str) -> Optional[str]:
    """Save this version of the template's HTML if it's new for the brand. Returns its hash (None for empty)."""
    if not html or len(html.encode("utf-8")) > MAX_BYTES:
        return None
    h = sha(html)
    if session.scalar(select(m.EmailHtml.id).where(m.EmailHtml.client_id == client_id, m.EmailHtml.sha256 == h)):
        return h
    try:
        with session.begin_nested():
            session.add(m.EmailHtml(org_id=org_id, client_id=client_id, template_id=str(template_id)[:64], sha256=h,
                                    html=html, bytes=len(html.encode("utf-8")), captured_at=datetime.now(timezone.utc)))
    except IntegrityError:  # another worker stored the same version first
        pass
    return h


def get(session: Session, client_id: str, h: str) -> Optional[m.EmailHtml]:
    return session.scalar(select(m.EmailHtml).where(m.EmailHtml.client_id == client_id, m.EmailHtml.sha256 == h))


def preview(html: str) -> str:
    """The HTML to put in the sandboxed frame: template tags resolved to their defaults, scripts and refreshes
    removed, links opening in a new tab."""
    out = _DEFAULT.sub(lambda mt: mt.group(2), html)
    out = _VAR.sub("", out)
    out = _TAG.sub("", out)
    out = _SCRIPT.sub("", out)
    out = _REFRESH.sub("", out)
    out = _ON.sub("", out)
    base = '<base target="_blank">'
    if re.search(r"<head\b[^>]*>", out, re.I):
        return re.sub(r"(<head\b[^>]*>)", r"\1" + base, out, count=1, flags=re.I)
    return base + out
