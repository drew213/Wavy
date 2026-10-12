#!/usr/bin/env python3
"""Score the audit engine against audits a strategist already did by hand. Local only.

    python scripts/eval_audit.py var/eval/<name>.json [...]     score one or more expectation files
    python scripts/eval_audit.py --all                          every var/eval/*.json
    python scripts/eval_audit.py --init <folder> --name <name>  write a starter expectation file to fill in

An expectation file (kept in var/eval/, never committed) points at a saved collector output folder and says
what a good audit of it finds:

    {"folder": "/path/to/output/<brand>", "context": {"store_platform": "Shopify"}, "top_n": 10,
     "opportunities": [{"title": "Rebuild the welcome flow", "match": ["welcome"]}],   # the hand-made top cards
     "findings": [{"title": "Subscriptions on hold get no message", "match": ["on.hold"]}],
     "numbers": [{"fact": "performance.store_revenue_full_months", "expected": 1234.5, "tolerance": 0.01,
                  "source": "facts.js: store revenue"}]}

It reports three scores: top-opportunity overlap (expected opportunities found in the engine's top N),
headline recall (expected findings found anywhere in the cards or findings) and number parity (ledger
facts within tolerance of the independently recomputed value). It also lists every card missing a
finding, evidence or action, and everything the engine couldn't measure, with the reason. Reports go
to var/eval/<name>-report.{md,json}; nothing leaves the machine.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wavyos import analysis  # noqa: E402
from wavyos.audit import opportunities  # noqa: E402
from wavyos.audit.engine import evaluate, score  # noqa: E402
from wavyos.audit.library import specs as library_specs  # noqa: E402
from wavyos.audit.pipeline import scorecard_facts  # noqa: E402
from wavyos.audit.snapshot import Snapshot  # noqa: E402

OUT = ROOT / "var" / "eval"
STOP = set("the a an and or of to in for on with by from at is are be this that your their our its into than more "
           "most less flow flows email emails".split())


def run_engine(folder: str, context: Optional[dict] = None, track: str = "snapshot") -> dict[str, Any]:
    """The same steps as audit.pipeline.run_audit_for_run, in memory, on a saved folder."""
    snap = Snapshot(folder, context=context or {})
    specs = library_specs()
    outcomes = evaluate(snap, track)
    rows = {cid: {"verdict": o.verdict, "message": o.message, "evidence": o.evidence, "value": o.value}
            for cid, o in outcomes.items()}
    result = analysis.run_all(snap)
    built = opportunities.build(snap, rows, specs, findings=result["findings"], unavailable=result["unavailable"])
    summary = {"score": score(outcomes, specs)}
    facts = result["facts"] + scorecard_facts(summary, rows, specs, built)
    return {"score": summary["score"], "rows": rows, "analysis": result, "built": built,
            "facts": {f["id"]: f for f in facts}}


def _text(card: dict) -> str:
    return " ".join(str(x or "") for x in (card.get("title"), card.get("observation"), card.get("evidence_text"),
                                            " ".join(card.get("steps") or []), card.get("finding"), card.get("action")))


def _keywords(title: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z\-]{3,}", title.lower()) if w not in STOP]


def matches(item: dict, card: dict) -> bool:
    text = _text(card)
    if item.get("id") and item["id"] in (card.get("id"), card.get("check_id"), card.get("finding_id")):
        return True
    if item.get("match"):
        return any(re.search(p, text, re.I) for p in item["match"])
    words = _keywords(item.get("title") or "")
    return bool(words) and sum(1 for w in words if w in text.lower()) >= max(1, (len(words) + 1) // 2)


def complete(card: dict) -> list[str]:
    missing = []
    if not (card.get("observation") or "").strip():
        missing.append("finding")
    if not ((card.get("evidence_text") or "").strip() or card.get("evidence")):
        missing.append("evidence")
    if not [s for s in card.get("steps") or [] if str(s).strip()]:
        missing.append("action")
    return missing


def score_against(expect: dict, out: dict) -> dict[str, Any]:
    cards = out["built"]["opportunities"]
    top_n = int(expect.get("top_n") or 10)
    top = cards[:top_n]
    everything = cards + out["analysis"]["findings"] + [  # surfaced as unmeasured, with its reason, still counts
        {"id": g.get("check_id"), "title": g.get("title"), "observation": g.get("reason")} for g in out["built"]["gaps"]]

    def found(items: list[dict], pool: list[dict]) -> list[dict]:
        rows = []
        for item in items:
            hit = next((c for c in pool if matches(item, c)), None)
            rows.append({"expected": item.get("title") or item.get("id"), "found": bool(hit),
                         "card": hit and {"rank": hit.get("rank"), "id": hit.get("id"), "title": hit.get("title")}})
        return rows

    opp = found(expect.get("opportunities") or [], top)
    heads = found(expect.get("findings") or [], everything)
    nums = []
    for n in expect.get("numbers") or []:
        f = out["facts"].get(n["fact"])
        actual = f["value"] if f else None
        exp, tol = n.get("expected"), float(n.get("tolerance", 0.01))
        ok = None
        if isinstance(exp, (int, float)) and isinstance(actual, (int, float)):
            ok = abs(actual - exp) <= tol * max(abs(exp), 1e-9) or abs(actual - exp) < 1e-9
        nums.append({"fact": n["fact"], "source": n.get("source", ""), "expected": exp, "actual": actual,
                     "tolerance": tol, "ok": ok, "missing": f is None})
    rate = lambda rows, key="found": (sum(1 for r in rows if r[key]) / len(rows)) if rows else None  # noqa: E731
    checked = [r for r in nums if r["ok"] is not None]
    gaps = [{"what": g.get("title") or g.get("check_id"), "reason": g.get("reason")} for g in out["built"]["gaps"]]
    return {
        "scores": {"top_overlap": rate(opp), "headline_recall": rate(heads),
                   "number_parity": (sum(1 for r in checked if r["ok"]) / len(checked)) if checked else None,
                   "cards_complete": (sum(1 for c in cards if not complete(c)) / len(cards)) if cards else None},
        "top_n": top_n, "opportunities": opp, "findings": heads, "numbers": nums,
        "incomplete_cards": [{"id": c["id"], "missing": complete(c)} for c in cards if complete(c)],
        "unmeasured": gaps, "counts": {"cards": len(cards), "findings": len(out["analysis"]["findings"]),
                                       "facts": len(out["facts"]), "gaps": len(gaps)},
        "engine_score": out["score"],
    }


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def markdown(name: str, expect: dict, rep: dict, out: dict) -> str:
    s = rep["scores"]
    lines = [f"# Audit eval: {name}", "", f"Run {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
             "| Score | Value |", "|---|---|",
             f"| Top-{rep['top_n']} opportunity overlap | {_pct(s['top_overlap'])} |",
             f"| Headline finding recall | {_pct(s['headline_recall'])} |",
             f"| Number parity | {_pct(s['number_parity'])} |",
             f"| Cards with finding, evidence and action | {_pct(s['cards_complete'])} |", "",
             f"{rep['counts']['cards']} cards, {rep['counts']['findings']} findings, {rep['counts']['facts']} facts, "
             f"{rep['counts']['gaps']} things not measured.", "", "## Expected opportunities", ""]
    lines += [f"- {'FOUND' if r['found'] else 'MISSED'}: {r['expected']}"
              + (f" (rank {r['card']['rank']}: {r['card']['title']})" if r["card"] else "") for r in rep["opportunities"]]
    lines += ["", "## Expected headline findings", ""]
    lines += [f"- {'FOUND' if r['found'] else 'MISSED'}: {r['expected']}"
              + (f" ({r['card']['title']})" if r["card"] else "") for r in rep["findings"]]
    lines += ["", "## Numbers", "", "| Fact | Source | Expected | Engine | OK |", "|---|---|---|---|---|"]
    lines += [f"| {r['fact']} | {r['source']} | {r['expected']} | {'missing' if r['missing'] else r['actual']} | "
              f"{'—' if r['ok'] is None else 'yes' if r['ok'] else 'NO'} |" for r in rep["numbers"]]
    lines += ["", f"## Engine top {rep['top_n']}", ""]
    lines += [f"{c['rank']}. {c['title']} ({c['category']}, {c['severity']})" for c in out["built"]["opportunities"][:rep["top_n"]]]
    if rep["incomplete_cards"]:
        lines += ["", "## Cards missing a part", ""] + [f"- {c['id']}: no {', '.join(c['missing'])}" for c in rep["incomplete_cards"]]
    lines += ["", "## Not measured", ""] + [f"- {g['what']}: {g['reason']}" for g in rep["unmeasured"]]
    return "\n".join(lines) + "\n"


def run_file(path: Path) -> dict[str, Any]:
    expect = json.loads(path.read_text())
    name = expect.get("name") or path.stem
    out = run_engine(expect["folder"], expect.get("context"), expect.get("track", "snapshot"))
    rep = score_against(expect, out)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}-report.json").write_text(json.dumps(rep, indent=1, default=str))
    (OUT / f"{name}-report.md").write_text(markdown(name, expect, rep, out))
    return {"name": name, **rep["scores"]}


def init(folder: str, name: str, research: Optional[str], key: Optional[str]) -> Path:
    out = run_engine(folder)
    lead = [fid for fid, f in out["facts"].items() if fid.startswith(("kpi_scorecard.", "performance.", "cohorts.repeat_rate"))
            and isinstance(f["value"], (int, float))][:25]
    opps = []
    if research and key:
        for o in json.loads(Path(research).read_text()).get(key) or []:
            opps.append({"title": o.get("title"), "match": _keywords(o.get("title") or "")[:3]})
    expect = {"name": name, "folder": folder, "context": {}, "top_n": 10, "opportunities": opps,
              "findings": [], "numbers": [{"fact": fid, "expected": None, "tolerance": 0.01,
                                           "source": "fill in from facts.js / verify_facts.py"} for fid in lead]}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    if path.exists():
        raise SystemExit(f"{path} exists; edit it instead")
    path.write_text(json.dumps(expect, indent=1))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--init", metavar="FOLDER")
    ap.add_argument("--name")
    ap.add_argument("--research", help="a JSON file of hand-made top opportunities keyed by brand")
    ap.add_argument("--key", help="the brand's key in --research")
    a = ap.parse_args()
    if a.init:
        print(f"wrote {init(a.init, a.name or Path(a.init).name, a.research, a.key)}")
        return
    files = [Path(f) for f in a.files] + (sorted(p for p in OUT.glob("*.json") if not p.stem.endswith("-report"))
                                          if a.all else [])
    if not files:
        ap.error("give expectation files, --all, or --init")
    print(f"{'eval':<28} {'top':>6} {'recall':>7} {'numbers':>8} {'complete':>9}")
    for f in files:
        r = run_file(f)
        print(f"{r['name']:<28} {_pct(r['top_overlap']):>6} {_pct(r['headline_recall']):>7} "
              f"{_pct(r['number_parity']):>8} {_pct(r['cards_complete']):>9}")
    print(f"reports in {OUT}")


if __name__ == "__main__":
    main()
