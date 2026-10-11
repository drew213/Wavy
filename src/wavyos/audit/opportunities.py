"""Failed and warned checks -> ranked opportunity cards (audit/playbooks.json), sized from measured inputs.

Cards are Proposed with an UNCONFIRMED owner until a strategist approves them. Ranking: fix_first cards
lead (every number after them depends on the fix), then sized levers by base value per effort day,
then the rest by check severity. Totals count each customer pool once.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .engine import SEVERITY_WEIGHT
from .sizing import size_levers, total_of
from .snapshot import Snapshot

PLAYBOOKS_PATH = Path(__file__).with_name("playbooks.json")


@lru_cache(maxsize=1)
def playbooks() -> dict[str, Any]:
    with open(PLAYBOOKS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def confidence(record: Optional[dict]) -> float:
    """How far a playbook's track record moves its rank: 1.0 with no decided outcomes, up to 1.5 when it has
    kept working and down to 0.5 when it hasn't (wavyos.loop.outcomes.track_record)."""
    r = record or {}
    n = r.get("improved", 0) + r.get("flat", 0) + r.get("worse", 0)
    if not n:
        return 1.0
    return max(0.5, min(1.5, 1 + (r.get("improved", 0) - r.get("worse", 0)) / (n + 2)))


def _rank_key(card: dict) -> tuple:
    sized = card["sizing"] if card.get("sizing") and card["sizing"].get("status") == "sized" else None
    conf = confidence(card.get("track_record"))
    per_day = sized["annual"]["base"] / max(card["effort_days"], 1) * conf if sized else 0.0
    return (card["category"] != "fix_first", sized is None, -per_day, -conf,
            -SEVERITY_WEIGHT.get(card["severity"], 2), card["verdict"] != "fail", card["id"])


def _finding_card(f: dict, sizing: dict) -> dict:
    readout = f.get("readout") or {"metric": f["title"].lower(), "days": 45}
    return {
        "id": f"opp-{f['id'].replace('.', '-').replace('_', '-')}", "check_id": None, "finding_id": f["id"],
        "module": f["module"], "verdict": "fail" if f["severity"] in ("critical", "high") else "warn",
        "severity": f["severity"], "area": f["module"], "title": f["title"], "category": f["category"],
        "observation": f["finding"], "evidence": [{"ref": fid, "note": "fact"} for fid in f.get("facts") or []],
        "evidence_text": f["evidence"], "evidence_class": f.get("evidence_class", "documented"),
        "outcome": f"Moves {readout['metric']}, read out after {readout['days']} days.", "steps": [f["action"]],
        "effort_days": f.get("effort_days", 2), "readout": readout,
        "sizing": f.get("value") or (sizing.get(f["lever"]) if f.get("lever") else None),
        "status": "Proposed", "owner": "UNCONFIRMED",
    }


def build(snap: Snapshot, results: dict[str, dict], specs: dict[str, dict], findings: Optional[list[dict]] = None,
          unavailable: Optional[dict[str, str]] = None, record: Optional[dict[str, dict]] = None) -> dict[str, Any]:
    """results: {check_id: {verdict, message, evidence, value}}; findings: analysis findings (Finding.as_dict);
    unavailable: {analysis module: reason}; record: decided outcomes per playbook (check id or finding id),
    which nudge ranking. Returns {opportunities, sizing, total, gaps}."""
    book = playbooks()["playbooks"]
    findings = findings or []
    hits = [cid for cid, r in results.items() if r["verdict"] in ("warn", "fail") and cid in book]
    levers = {book[c]["lever"] for c in hits if book[c].get("lever")} | {f["lever"] for f in findings if f.get("lever")}
    sizing = size_levers(snap, sorted(levers))
    cards = [_finding_card(f, sizing) for f in findings]
    for cid in hits:
        pb, r, spec = book[cid], results[cid], specs.get(cid) or {}
        lever: Optional[dict] = sizing.get(pb["lever"]) if pb.get("lever") else None
        cards.append({
            "id": f"opp-{cid.lower()}", "check_id": cid, "verdict": r["verdict"], "severity": spec.get("severity", "medium"),
            "area": spec.get("area"), "title": pb["title"], "category": pb["category"],
            "observation": r["message"], "evidence": r.get("evidence") or [], "evidence_class": "documented",
            "outcome": pb["outcome"], "steps": pb["steps"], "effort_days": pb["effort_days"], "readout": pb["readout"],
            "sizing": lever, "status": "Proposed", "owner": "UNCONFIRMED",
        })
    for card in cards:
        key = card.get("check_id") or card.get("finding_id")
        if record and key in record:
            card["track_record"] = record[key]
    cards.sort(key=_rank_key)
    for i, card in enumerate(cards, 1):
        card["rank"] = i
    ranked_levers = [c["sizing"] for c in cards if c.get("sizing")]
    gaps = [{"check_id": cid, "title": (specs.get(cid) or {}).get("title"), "verdict": r["verdict"],
             "reason": r["message"], "unlock": (book.get(cid) or {}).get("title")}
            for cid, r in sorted(results.items()) if r["verdict"] in ("na", "error", "not_implemented")
            and "not part of the" not in (r["message"] or "")]
    gaps += [{"check_id": None, "module": mod, "title": f"Analysis: {mod.replace('_', ' ')}", "verdict": "na",
              "reason": reason, "unlock": None} for mod, reason in sorted((unavailable or {}).items())]
    return {"opportunities": cards, "sizing": sizing, "total": total_of(ranked_levers), "gaps": gaps,
            "playbook_version": playbooks().get("version")}
