"""Agent skills: the agency playbook (layer 1) the agents work from.

skills/roles/<role>.md   how each agent role thinks and works
skills/areas/<area>.md   what good looks like in each of the 23 operating areas, in the ten
                         components of the Lifecycle Client OS template

`validate()` checks the files; `publish()` loads them as versioned standards (unchanged files
mint no new version). Work packets then carry the role skill for the executing agent and the
area skill for the task, and an area's quality checklist is the default task checklist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .policy import ACTIONS
from .scope import Scope
from .taxonomy import AREAS

AREA_SECTIONS = [  # heading -> component, in order
    ("Purpose", "purpose"), ("Client facts and rules", "facts_rules"), ("Inputs and sources", "inputs"),
    ("SOP", "sop"), ("Decision rules", "decision_rules"), ("Examples", "examples"),
    ("Quality checklist", "quality_checklist"), ("Owner and approvals", "owner_approvals"),
    ("Outputs and destination", "outputs"), ("Maintenance", "maintenance"),
]
ROLE_SECTIONS = ["Mission", "You own", "You never", "How you work", "Decision rules", "Quality bar",
                 "Output formats", "When to stop and ask"]
ROLES = tuple(m.AGENT_ROLES)
ROLE_HOME = {"account_lead": "account_management", "analyst": "reporting", "strategist": "journey",
             "creative": "campaign_production", "qa": "campaign_production"}
LEVELS = ("autonomous", "approved_plan", "explicit_approval")
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "skills"


@dataclass
class Skill:
    key: str
    kind: str
    meta: dict
    sections: dict[str, str]          # heading -> body
    source: str
    checklist: list[str] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)

    @property
    def version(self) -> int:
        return int(self.meta.get("version", 1))


def _front(text: str) -> tuple[dict, str]:
    mm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not mm:
        return {}, text
    meta = {}
    for line in mm.group(1).splitlines():
        k, _, v = line.partition(":")
        if k.strip():
            meta[k.strip()] = v.strip()
    return meta, text[mm.end():]


def _sections(body: str) -> list[tuple[str, str]]:
    parts = re.split(r"^## (.+)$", body, flags=re.M)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)]


def parse(path: Path) -> tuple[Optional[Skill], list[str]]:
    errs: list[str] = []
    where = f"{path.parent.name}/{path.name}"
    meta, body = _front(path.read_text())
    kind = meta.get("kind")
    secs = _sections(body)
    names = [h for h, _ in secs]
    if kind == "area":
        area = meta.get("area", "")
        if area not in AREAS:
            errs.append(f"{where}: area {area!r} isn't one of the 23 operating areas")
        if path.stem != area:
            errs.append(f"{where}: file name must be {area}.md")
        if meta.get("key") != f"area.{area}":
            errs.append(f"{where}: key must be area.{area}")
        if meta.get("owner_role") not in ROLES:
            errs.append(f"{where}: owner_role must be one of {ROLES}")
        expected = [h for h, _ in AREA_SECTIONS]
        if names != expected:
            errs.append(f"{where}: sections must be exactly, in order: {', '.join(expected)} (found: {', '.join(names)})")
    elif kind == "role":
        role = meta.get("role", "")
        if role not in ROLES:
            errs.append(f"{where}: role {role!r} must be one of {ROLES}")
        if meta.get("key") != f"role.{role}":
            errs.append(f"{where}: key must be role.{role}")
        if names != ROLE_SECTIONS:
            errs.append(f"{where}: sections must be exactly, in order: {', '.join(ROLE_SECTIONS)} (found: {', '.join(names)})")
    else:
        return None, [f"{where}: front matter kind must be area or role"]
    for k in ("version", "reviewed", "title"):
        if not meta.get(k):
            errs.append(f"{where}: missing front matter {k}")
    sections = dict(secs)
    for h, txt in secs:
        if len(txt.split()) < 15:
            errs.append(f"{where}: section '{h}' is too thin to act on ({len(txt.split())} words)")
    skill = Skill(meta.get("key", ""), kind, meta, sections, where)
    if kind == "area":
        skill.checklist = [re.sub(r"\s+", " ", x).strip() for x in re.findall(r"^- \[ \] (.+)$", sections.get("Quality checklist", ""), re.M)]
        if not 5 <= len(skill.checklist) <= 10:
            errs.append(f"{where}: quality checklist needs 5–10 '- [ ]' items (found {len(skill.checklist)})")
        for row in re.findall(r"^\|(.+)\|\s*$", sections.get("Owner and approvals", ""), re.M):
            cells = [c.strip().strip("`") for c in row.split("|")]
            if len(cells) < 3 or cells[0].lower() in ("action", "") or set(cells[0]) <= set("-: "):
                continue
            # A level may carry a note, e.g. "explicit_approval (locked)": the first word is the level.
            action, level, who = cells[0], re.split(r"[\s(]", cells[1].lower().strip("`* "))[0], cells[2]
            if action not in ACTIONS:
                errs.append(f"{where}: approvals table uses unregistered action {action!r}")
                continue
            if level not in LEVELS:
                errs.append(f"{where}: {action} level {level!r} must be one of {LEVELS}")
            elif level != ACTIONS[action].level:
                errs.append(f"{where}: {action} is {ACTIONS[action].level} in the policy engine, not {level}")
            skill.approvals.append({"action": action, "level": level, "who": who})
        if not skill.approvals:
            errs.append(f"{where}: owner and approvals table has no rows with registered actions")
    return skill, errs


def load(root: Path | str = DEFAULT_ROOT) -> tuple[dict[str, Skill], list[str]]:
    root = Path(root)
    skills, errs = {}, []
    for path in sorted(list((root / "roles").glob("*.md")) + list((root / "areas").glob("*.md"))):
        s, e = parse(path)
        errs += e
        if s:
            if s.key in skills:
                errs.append(f"{s.source}: duplicate key {s.key}")
            skills[s.key] = s
    return skills, errs


def validate(root: Path | str = DEFAULT_ROOT, require_complete: bool = True) -> list[str]:
    skills, errs = load(root)
    if require_complete:
        errs += [f"missing role skill: roles/{r}.md" for r in ROLES if f"role.{r}" not in skills]
        errs += [f"missing area skill: areas/{a}.md" for a in AREAS if f"area.{a}" not in skills]
    return errs


def publish(session: Session, scope: Scope, root: Path | str = DEFAULT_ROOT) -> list[tuple[str, int, bool]]:
    """Publish every skill as standards. Returns (standard key, version, changed)."""
    from .knowledge import publish_standard
    errs = validate(root, require_complete=False)
    if errs:
        raise ValueError("skills don't validate:\n  " + "\n  ".join(errs))
    skills, _ = load(root)
    out = []

    def pub(key, area, component, title, body, data):
        before = session.scalar(select(m.Standard).where(m.Standard.org_id == scope.org_id, m.Standard.key == key,
                                                         m.Standard.status == "active"))
        s = publish_standard(session, scope, key=key, area=area, component=component, title=title, body=body,
                             data=data, source=f"skills/{data['source']}")
        out.append((key, s.version, before is None or before.id != s.id))

    for sk in skills.values():
        base = {"skill": sk.key, "skill_version": sk.version, "reviewed": sk.meta.get("reviewed"), "source": sk.source}
        if sk.kind == "role":
            role = sk.meta["role"]
            body = "\n\n".join(f"## {h}\n{t}" for h, t in sk.sections.items())
            pub(f"skill.role.{role}", ROLE_HOME[role], "sop", sk.meta["title"], body, {**base, "kind": "role", "role": role})
        else:
            area = sk.meta["area"]
            for heading, component in AREA_SECTIONS:
                data = {**base, "kind": "area", "owner_role": sk.meta["owner_role"]}
                if component == "quality_checklist":
                    data["checklist"] = sk.checklist
                if component == "owner_approvals":
                    data["approvals"] = sk.approvals
                pub(f"skill.area.{area}.{component}", area, component, f"{sk.meta['title']}: {heading}",
                    sk.sections[heading], data)
    return out


def role_skill(session: Session, org_id: str, role: str) -> Optional[str]:
    s = session.scalar(select(m.Standard).where(m.Standard.org_id == org_id, m.Standard.key == f"skill.role.{role}",
                                                m.Standard.status == "active"))
    return s.body if s else None


def checklist_for(session: Session, org_id: str, area: str) -> list[str]:
    """The area skill's quality checklist: the default checklist for a task in that area."""
    s = session.scalar(select(m.Standard).where(m.Standard.org_id == org_id,
                                                m.Standard.key == f"skill.area.{area}.quality_checklist",
                                                m.Standard.status == "active"))
    return list((s.data or {}).get("checklist", [])) if s else []
