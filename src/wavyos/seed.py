"""Load the Audit OS playbook into agency standards (layer 1).

Each `audit-os/sop/*.md` becomes standard `sop.audit.<id>` (component `sop`), and the check library
becomes `checks.audit.library` (component `quality_checklist`, the JSON in `data`). Rerunning is
safe: knowledge.publish_standard returns the current version when nothing changed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import knowledge
from . import models as m
from .config import DEFAULT_AUDIT_OS_ROOT
from .scope import Scope
from .taxonomy import require_area

# Audit OS stage id -> operating area
STAGE_AREAS = {
    "rules": "knowledge_maintenance",
    "intake": "onboarding",
    "access": "data_infrastructure",
    "collect": "data_infrastructure",
    "checks": "flow_audit",
    "sizing": "quarterly_planning",
    "strategy": "journey",
    "verify": "reporting",
    "deliver": "client_meetings",
    "close": "knowledge_maintenance",
    "reaudit": "reporting",
}
FALLBACK_AREA = "knowledge_maintenance"
LIBRARY_KEY = "checks.audit.library"
LIBRARY_AREA = "flow_audit"

_CHECK_ITEM = re.compile(r"^- \[[ xX]\] (?P<text>.+?)\s*<!--\s*id:(?P<id>\S+)(?:\s+tracks:(?P<tracks>\S+))?\s*-->\s*$")


@dataclass
class Seeded:
    key: str
    version: int
    area: str
    changed: bool


def parse_sop(text: str) -> tuple[dict, str]:
    """Split `---` front matter (flat `key: value` lines) from the markdown body."""
    meta: dict = {}
    body = text
    if text.startswith("---"):
        _, _, rest = text.partition("\n")
        head, sep, body = rest.partition("\n---")
        if not sep:
            raise ValueError("front matter isn't closed with ---")
        body = body.split("\n", 1)[1] if "\n" in body else ""
        for line in head.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            k, colon, v = line.partition(":")
            if not colon:
                continue
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            meta[k.strip()] = v
    if "tracks" in meta:
        meta["tracks"] = [t.strip() for t in meta["tracks"].split(",") if t.strip()]
    if "order" in meta and str(meta["order"]).isdigit():
        meta["order"] = int(meta["order"])
    checklist = []
    for line in body.splitlines():
        hit = _CHECK_ITEM.match(line.strip())
        if hit:
            checklist.append({"id": hit["id"], "text": hit["text"],
                              "tracks": (hit["tracks"] or "").split(",") if hit["tracks"] else []})
    meta["checklist"] = checklist
    return meta, body.strip() + "\n"


def _active_id(session: Session, org_id: str, key: str) -> Optional[str]:
    return session.scalar(select(m.Standard.id).where(m.Standard.org_id == org_id, m.Standard.key == key,
                                                      m.Standard.status == "active"))


def _publish(session, scope, key, area, component, title, body, data, source) -> Seeded:
    before = _active_id(session, scope.org_id, key)
    st = knowledge.publish_standard(session, scope, key, area, component, title, body=body, data=data, source=source)
    session.flush()
    return Seeded(key, st.version, area, changed=st.id != before)


def import_audit_os_standards(session: Session, scope: Scope, root: str | Path = DEFAULT_AUDIT_OS_ROOT) -> list[Seeded]:
    """Publish every Audit OS SOP and the check library as agency standards. Needs an org_admin scope
    with no client (publish_standard enforces both)."""
    root = Path(root)
    sop_dir = root / "sop"
    if not sop_dir.is_dir():
        raise ValueError(f"no sop/ folder under {root}")
    out: list[Seeded] = []
    for path in sorted(sop_dir.glob("*.md")):
        meta, body = parse_sop(path.read_text(encoding="utf-8"))
        stage = meta.get("id") or path.stem
        area = require_area(STAGE_AREAS.get(stage, FALLBACK_AREA))
        title = f"Audit OS: {meta.get('title') or stage}"
        out.append(_publish(session, scope, f"sop.audit.{stage}", area, "sop", title, body, meta,
                            f"audit-os/sop/{path.name}"))
    lib_path = root / "checks" / "check-library.json"
    if lib_path.is_file():
        library = json.loads(lib_path.read_text(encoding="utf-8"))
        checks = library.get("checks", [])
        body = (f"Audit OS check library v{library.get('version', '?')}: {len(checks)} checks across "
                f"{len(library.get('areas', []))} areas. Every audit scores the account against these checks; "
                "the full definitions are in data.")
        out.append(_publish(session, scope, LIBRARY_KEY, LIBRARY_AREA, "quality_checklist",
                            f"Audit OS check library v{library.get('version', '?')}", body, library,
                            "audit-os/checks/check-library.json"))
    return out
