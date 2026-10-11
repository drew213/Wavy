"""The packet a drafter sees for one section (ported from reference/analyst/build-packet.js).

Only measured material goes in: the section's facts (with their basis), its findings, the scored checks, the
sized opportunity cards and short evidence tables, plus the brand's own rules from the questionnaire. No
profile-level data: personal keys are dropped and email addresses and phone numbers are masked, so a
packet can go to any allowed provider.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..policy import payload_hash

TABLE_ROWS = 15
MAX_FACTS = 180
BRAND_KEYS = ("success", "kpis", "regulated_category", "avoid_claims", "never_sound_like", "voice_words",
              "discount_stance", "deepest_discount", "sms_platform", "store_platform", "hero_products", "peak_season")
PERSONAL_KEYS = frozenset({"email", "phone", "phone_number", "first_name", "last_name", "external_id", "anonymous_id",
                           "profile_id", "address", "address1", "address2", "location", "image", "organization", "ip"})
ROLE_INBOX = re.compile(r"^(info|support|hello|help|team|orders?|customer-?service|care|contact|news|noreply|no-reply|"
                        r"marketing|shop|sales)$", re.I)
EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{8,}\d(?!\w)")


def _mask(text: str) -> str:
    text = EMAIL.sub(lambda mo: mo[0] if ROLE_INBOX.match(mo[1]) else f"[person]@{mo[2]}", text)
    return PHONE.sub("[phone]", text)


def scrub(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: scrub(v) for k, v in node.items() if str(k).lower() not in PERSONAL_KEYS}
    if isinstance(node, list):
        return [scrub(v) for v in node]
    if isinstance(node, str):
        return _mask(node)
    return node


def _plan_start(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def _short(row: dict) -> dict:
    return {k: (v[:160] if isinstance(v, str) else v) for k, v in row.items()
            if not isinstance(v, (dict, list)) or k in ("issues", "terms")}


def context(session: Session, run: m.AuditRun) -> dict:
    """Everything shared by a run's section packets, loaded once."""
    from .. import workspace
    client = session.get(m.Client, run.client_id)
    sc = session.get(m.Scorecard, run.scorecard_id) if run.scorecard_id else None
    summary = (sc.summary or {}) if sc else {}
    analysis = workspace._artifact(session, run.id, "analysis") or {}
    ledger = {f.fact_id: {"label": f.label, "value": (f.value or {}).get("v"), "unit": f.unit, "window": f.window,
                          "time_basis": f.time_basis, "evidence_class": f.evidence_class, "module": f.module}
              for f in session.scalars(select(m.AuditFact).where(m.AuditFact.run_id == run.id))}
    row = session.scalar(select(m.OnboardingIntake).where(m.OnboardingIntake.client_id == client.id))
    answers = (row.answers if row else {}) or {}
    from ..loop.outcomes import for_client
    tracked = [{"title": o.title, "status": o.status, "owner": o.owner, "readout_metric": o.readout_metric,
                "readout_fact": o.readout_fact} for o in for_client(session, client.id)[:12]]
    return {"client": client, "summary": summary, "analysis": analysis, "ledger": ledger, "answers": answers,
            "checks": workspace._checks(session, sc) if sc is not None else [], "outcomes": tracked}


def build(ctx: dict, section: str, today: Optional[date] = None) -> tuple[dict, str]:
    from ..workspace import SECTIONS
    modules = next((mods for sid, _, mods, _ in SECTIONS if sid == section), ())
    client, summary, analysis, ledger = ctx["client"], ctx["summary"], ctx["analysis"], ctx["ledger"]
    today = today or date.today()
    findings = analysis.get("findings") or []
    if section not in ("opportunities", "plan"):
        findings = [f for f in findings if f.get("module") in modules]
    wanted_modules = set(modules) | {"kpi_scorecard"}
    if section in ("opportunities", "plan"):
        wanted_modules |= {"sizing"}
    if section == "foundational":
        wanted_modules |= {"checks"}
    cited = {fid for f in findings for fid in f.get("facts") or []}
    facts = {fid: {k: v for k, v in f.items() if k != "module"} for fid, f in ledger.items()
             if fid in cited or f["module"] in wanted_modules}
    if len(facts) > MAX_FACTS:
        facts = dict(sorted(facts.items(), key=lambda kv: (kv[0] not in cited, kv[0]))[:MAX_FACTS])
    mods = analysis.get("modules") or {}
    packet: dict[str, Any] = {
        "section": section,
        "brand": {"name": client.name, "timezone": client.timezone,
                  **{k: ctx["answers"].get(k) for k in BRAND_KEYS if ctx["answers"].get(k) not in (None, "", [])}},
        "today": today.isoformat(), "plan_starts": _plan_start(today).isoformat(),
        "facts": facts,
        "findings": [{k: f.get(k) for k in ("id", "title", "severity", "finding", "evidence", "action", "facts",
                                            "lever", "effort_days")} for f in findings],
        "unavailable": {k: v.get("reason") for k, v in mods.items() if v.get("status") != "ok"},
        "tables": {f"{mod}.{name}": [_short(r) for r in rows[:TABLE_ROWS]]
                   for mod in modules for name, rows in ((mods.get(mod) or {}).get("tables") or {}).items() if rows},
    }
    if section in ("opportunities", "plan"):
        packet["opportunities"] = [{
            "id": c["id"], "rank": c.get("rank"), "title": c.get("title"), "severity": c.get("severity"),
            "observation": c.get("observation"), "evidence": c.get("evidence_text"), "steps": c.get("steps"),
            "effort_days": c.get("effort_days"), "readout": c.get("readout"),
            "sizing": ({"lever": (c.get("sizing") or {}).get("lever"), "status": (c.get("sizing") or {}).get("status"),
                        "reason": (c.get("sizing") or {}).get("reason")} if c.get("sizing") else None),
        } for c in summary.get("opportunities") or []]
        packet["gaps"] = summary.get("gaps") or []
    if section == "report":
        packet["top_opportunities"] = [{"id": c["id"], "rank": c.get("rank"), "title": c.get("title"),
                                        "observation": c.get("observation"), "status": c.get("status")}
                                       for c in (summary.get("opportunities") or [])[:5]]
        packet["outcomes"] = ctx.get("outcomes") or []
    if section == "foundational":
        packet["checks"] = [{k: c.get(k) for k in ("id", "title", "area", "severity", "verdict", "message")}
                            for c in ctx["checks"] if c.get("verdict") not in ("pass",)]
        packet["score"] = {k: summary.get(k) for k in ("score", "by_area", "coverage")}
    packet = scrub(packet)
    return packet, payload_hash(packet)
