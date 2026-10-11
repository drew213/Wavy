"""The facts check: every number a draft writes must be a value in the run's fact ledger (audit_facts).

It blocks, it never fixes: a number that isn't in the ledger goes back to the writer (or the strategist) as a
problem. Claims (headline, finding, evidence, observation) are held to the ledger strictly. Proposals (actions,
steps, plan and calendar items) may also use the counts and offers a plan is made of ("a 3-email series",
"15% off", "within 48 hours"), never a measured-looking number that isn't in the ledger.

An insight without finding, evidence and action is cut, not repaired.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Optional

from . import numbers
from .prompts import SECTIONS

CLAIM_KEYS = frozenset({"headline", "finding", "evidence", "observation", "summary"})
STRUCTURAL_KEYS = frozenset({"id", "facts", "baseline_fact", "week", "weeks", "when", "owner", "stage", "status",
                             "channel"})
REQUIRED = ("finding", "evidence", "action")
PLAN_COUNTS = re.compile(
    r"\b\d{1,2}\s?%\s?off\b|\b\d{1,3}[- ](?:emails?|messages?|sms|texts?|touch(?:es)?|steps?|variants?|segments?|"
    r"tiers?|sends?|versions?|subject lines?|days?|weeks?|months?|hours?|minutes?|mins?|products?|skus?)\b", re.I)
NOT_INCREMENTAL = re.compile(r"(?<!not )(?<!isn't )(?<!never )\bincremental\b", re.I)
PROMISES = re.compile(r"\bguarantee[sd]?\b|\bwill (?:generate|deliver|add|drive) \$", re.I)


def _strings(node: Any, path: str = "") -> Iterable[tuple[str, str, str]]:
    """(path, key, text) for every string leaf."""
    if isinstance(node, dict):
        for k, v in node.items():
            sub = f"{path}.{k}" if path else str(k)
            if isinstance(v, str):
                yield sub, str(k), v
            else:
                yield from _strings(v, sub)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, str):
                yield f"{path}[{i}]", path.rsplit(".", 1)[-1], v
            else:
                yield from _strings(v, f"{path}[{i}]")


def _shape_problems(section: str, draft: Any) -> list[dict]:
    if not isinstance(draft, dict):
        return [{"path": "", "reason": "the draft isn't a JSON object"}]
    out = []
    if not isinstance(draft.get("headline"), str) or not draft["headline"].strip():
        out.append({"path": "headline", "reason": "missing the one-sentence verdict"})
    if not isinstance(draft.get("insights"), list):
        out.append({"path": "insights", "reason": "insights must be a list"})
    for key, example in SECTIONS[section][1].items():
        if not isinstance(draft.get(key), type(example)):
            out.append({"path": key, "reason": f"{key} must be a {type(example).__name__}"})
    return out


def check(section: str, draft: Any, ledger: dict[str, dict], *, terms: Iterable[str] = (),
          card_ids: Optional[set[str]] = None) -> dict:
    """ledger: {fact_id: {"value": ..., ...}}. Returns {ok, problems, cut, warnings, numbers_checked, draft} where
    draft is the input with cut insights removed."""
    problems = _shape_problems(section, draft)
    if problems and not isinstance(draft, dict):
        return {"ok": False, "problems": problems, "cut": [], "warnings": [], "numbers_checked": 0, "draft": draft}
    draft = copy.deepcopy(draft)
    cut, warnings = [], []
    kept = []
    for i, ins in enumerate(draft.get("insights") or []):
        if not isinstance(ins, dict):
            cut.append({"path": f"insights[{i}]", "reason": "not an object"})
            continue
        missing = [k for k in REQUIRED if not str(ins.get(k) or "").strip()]
        if missing:
            cut.append({"path": f"insights[{i}]", "reason": f"cut: no {', '.join(missing)}",
                        "finding": str(ins.get("finding") or "")[:200]})
            continue
        kept.append(ins)
    if isinstance(draft.get("insights"), list):
        draft["insights"] = kept
        if not kept:
            problems.append({"path": "insights", "reason": "no insight has a finding, evidence and action"})

    values = {fid: v for fid, f in ledger.items() if (v := numbers.as_number((f or {}).get("value"))) is not None}
    for i, ins in enumerate(draft.get("insights") or []):
        for fid in ins.get("facts") or []:
            if fid not in ledger:
                problems.append({"path": f"insights[{i}].facts", "token": str(fid), "reason": "cites a fact id that isn't in the ledger"})
            elif ledger[fid].get("value") is None:
                warnings.append({"path": f"insights[{i}].facts", "token": str(fid),
                                 "reason": "cites an unavailable fact; the text must call it unavailable, not zero"})
    if card_ids is not None:
        for i, c in enumerate(draft.get("cards") or []):
            if not isinstance(c, dict) or c.get("id") not in card_ids:
                problems.append({"path": f"cards[{i}].id", "token": str((c or {}).get("id") if isinstance(c, dict) else c),
                                 "reason": "isn't an opportunity card in the packet"})

    checked = 0
    for path, key, text in _strings(draft):
        if key in STRUCTURAL_KEYS:
            continue
        claim = key in CLAIM_KEYS
        scan = text if claim else PLAN_COUNTS.sub(" ", text)
        for tok in numbers.tokens(scan):
            checked += 1
            if not any(numbers.matches(tok, v) for v in values.values()):
                problems.append({"path": path, "token": tok.raw,
                                 "reason": "this number isn't in the fact ledger" + ("" if claim else " (and isn't a plan count)")})
        if NOT_INCREMENTAL.search(text):
            problems.append({"path": path, "reason": "calls revenue incremental; attributed revenue is not incremental"})
        if PROMISES.search(text):
            problems.append({"path": path, "reason": "promises an outcome; sized values are modeled ranges, not guarantees"})
        low = text.lower()
        for t in terms:
            if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", low):
                problems.append({"path": path, "token": t, "reason": "uses a claims-risk term the brand can't use"})
    seen, unique = set(), []
    for p in problems:
        k = (p["path"], p.get("token"), p["reason"])
        if k not in seen:
            seen.add(k)
            unique.append(p)
    return {"ok": not unique, "problems": unique, "cut": cut, "warnings": warnings, "numbers_checked": checked,
            "draft": draft}
