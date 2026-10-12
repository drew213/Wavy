"""The learning report: what strategists change, reject and approve, per section prompt and per playbook,
and how approved playbooks read out. It is the evidence for editing prompts and playbooks.json.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..audit.opportunities import confidence, playbooks
from .outcomes import DECIDED

INDEX = re.compile(r"\[\d+\]")
TOP = 8


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def report(session: Session, client_ids: list[str]) -> dict[str, Any]:
    clients = {c.id: c for c in session.scalars(select(m.Client).where(m.Client.id.in_(client_ids)))}
    drafts = list(session.scalars(select(m.NarrativeDraft).where(m.NarrativeDraft.client_id.in_(client_ids))))
    by_section: dict[str, list[m.NarrativeDraft]] = defaultdict(list)
    for d in drafts:
        by_section[d.section].append(d)

    sections = []
    card_edits: Counter = Counter()
    for section, rows in sorted(by_section.items()):
        status = Counter(d.status for d in rows)
        reviewed = status["approved"] + status["edited"] + status["rejected"]
        paths, reasons, criteria = Counter(), Counter(), defaultdict(list)
        for d in rows:
            for ch in d.diff or []:
                paths[INDEX.sub("[]", ch.get("path") or "")] += 1
                if section == "opportunities" and (m_ := re.match(r"cards\[(\d+)\]", ch.get("path") or "")):
                    cards = (d.original or {}).get("cards") or []
                    i = int(m_.group(1))
                    if i < len(cards) and cards[i].get("id"):
                        card_edits[cards[i]["id"]] += 1
            for p in (d.facts_check or {}).get("problems") or []:
                reasons[p.get("reason") or "unknown"] += 1
            last = ((d.critic or {}).get("rounds") or [{}])[-1]
            for k, v in (last.get("scores") or {}).items():
                criteria[k].append(float(v))
        weakest = sorted(((k, _avg(v)) for k, v in criteria.items()), key=lambda kv: kv[1] if kv[1] is not None else 9)
        sections.append({
            "section": section, "drafts": len(rows), "status": dict(status), "reviewed": reviewed,
            "approved_as_drafted": status["approved"], "edited": status["edited"], "rejected": status["rejected"],
            "edit_rate": round(status["edited"] / reviewed, 3) if reviewed else None,
            "reject_rate": round(status["rejected"] / reviewed, 3) if reviewed else None,
            "avg_score": _avg([d.score for d in rows if d.score is not None]),
            "avg_rounds": _avg([d.rounds for d in rows if d.rounds]),
            "cost_usd": round(sum(d.cost_usd or 0 for d in rows), 4),
            "edited_paths": [{"path": p, "count": n} for p, n in paths.most_common(TOP)],
            "facts_check_reasons": [{"reason": r, "count": n} for r, n in reasons.most_common(TOP)],
            "weakest_criteria": [{"criterion": k, "avg": v} for k, v in weakest[:3]],
            "notes": [{"client": clients[d.client_id].slug if d.client_id in clients else None, "status": d.status,
                       "note": d.review_note, "at": (d.reviewed_at or d.updated_at or d.created_at).isoformat()}
                      for d in sorted(rows, key=lambda d: d.reviewed_at or d.created_at, reverse=True)
                      if d.review_note][:TOP],
        })

    writers = defaultdict(list)
    for d in drafts:
        if d.drafter:
            writers[(d.drafter, d.critic_by)].append(d)
    models = [{"drafter": k[0], "critic": k[1], "drafts": len(v),
               "passed": sum(1 for d in v if d.status in ("draft", "approved", "edited")),
               "avg_score": _avg([d.score for d in v if d.score is not None]),
               "avg_rounds": _avg([d.rounds for d in v if d.rounds]),
               "cost_usd": round(sum(d.cost_usd or 0 for d in v), 4)} for k, v in sorted(writers.items())]

    outcomes = list(session.scalars(select(m.OpportunityOutcome).where(m.OpportunityOutcome.client_id.in_(client_ids))))
    plays: dict[str, dict[str, Any]] = {}
    book = playbooks()["playbooks"]
    for o in outcomes:
        p = plays.setdefault(o.source_key, {"source_key": o.source_key, "title": o.title, "approved": 0,
                                            "tracking": 0, "withdrawn": 0, **{k: 0 for k in DECIDED}, "card_edits": 0,
                                            "brands": set()})
        p["approved"] += 1
        p[o.status] = p.get(o.status, 0) + 1
        p["brands"].add(o.client_id)
    for card_id, n in card_edits.items():
        key = next((k for k in book if f"opp-{k.lower()}" == card_id), card_id)
        p = plays.setdefault(key, {"source_key": key, "title": (book.get(key) or {}).get("title") or card_id, "approved": 0,
                                   "tracking": 0, "withdrawn": 0, **{k: 0 for k in DECIDED}, "card_edits": 0,
                                   "brands": set()})
        p["card_edits"] += n
    playbook_rows = []
    for p in plays.values():
        p["brands"] = len(p["brands"])
        p["rank_weight"] = confidence(p)
        playbook_rows.append(p)
    playbook_rows.sort(key=lambda p: (-(p["improved"] + p["worse"] + p["flat"]), -p["card_edits"], p["source_key"]))
    return {"sections": sections, "models": models, "playbooks": playbook_rows,
            "totals": {"drafts": len(drafts), "outcomes": len(outcomes),
                       "decided": sum(1 for o in outcomes if o.status in DECIDED)}}
