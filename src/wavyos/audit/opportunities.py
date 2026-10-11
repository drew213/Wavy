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


def _rank_key(card: dict) -> tuple:
    sized = card["sizing"] if card.get("sizing") and card["sizing"].get("status") == "sized" else None
    per_day = sized["annual"]["base"] / max(card["effort_days"], 1) if sized else 0.0
    return (card["category"] != "fix_first", sized is None, -per_day,
            -SEVERITY_WEIGHT.get(card["severity"], 2), card["verdict"] != "fail", card["check_id"])


def build(snap: Snapshot, results: dict[str, dict], specs: dict[str, dict]) -> dict[str, Any]:
    """results: {check_id: {verdict, message, evidence, value}}. Returns {opportunities, sizing, total}."""
    book = playbooks()["playbooks"]
    hits = [cid for cid, r in results.items() if r["verdict"] in ("warn", "fail") and cid in book]
    sizing = size_levers(snap, sorted({book[c]["lever"] for c in hits if book[c].get("lever")}))
    cards = []
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
    cards.sort(key=_rank_key)
    for i, card in enumerate(cards, 1):
        card["rank"] = i
    ranked_levers = [c["sizing"] for c in cards if c.get("sizing")]
    gaps = [{"check_id": cid, "title": (specs.get(cid) or {}).get("title"), "verdict": r["verdict"],
             "reason": r["message"], "unlock": (book.get(cid) or {}).get("title")}
            for cid, r in sorted(results.items()) if r["verdict"] in ("na", "error", "not_implemented")
            and "not part of the" not in (r["message"] or "")]
    return {"opportunities": cards, "sizing": sizing, "total": total_of(ranked_levers), "gaps": gaps,
            "playbook_version": playbooks().get("version")}
