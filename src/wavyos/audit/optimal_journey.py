"""The optimal journey: the lifecycle playbook (journeys.json) laid over the brand's live flows.

Each moment and each of its steps is marked keep (live and healthy), fix (live with problems), build (missing),
or validate (needs tracking, consent or a category check first). Live messages are matched to playbook steps by
channel and timing. Priority comes from the opportunity cards behind each moment (their modeled value) scaled
by how those plays have gone for this agency (the outcomes track record), so the order shifts as results come in.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..analysis.journey_graph import REGULATED_SMS, STAGES
from ..analysis.journey_map import MODULE, SEVERITY, STAGE_INFO, walk_messages
from .opportunities import confidence

BOOK = Path(__file__).with_name("journeys.json")
STATUS_ORDER = {"build": 0, "fix": 1, "validate": 2, "keep": 3}


@lru_cache(maxsize=1)
def library() -> dict:
    return json.loads(BOOK.read_text())


def _step_message(x: dict, fl: dict) -> dict:
    return {"message_id": x["message_id"], "flow_id": fl["id"], "flow": fl["name"], "name": x["name"],
            "subject": x["subject"], "preview": x["preview"], "day": x["day"], "html_hash": x.get("html_hash"),
            "rpr_365d": (x.get("p365") or {}).get("rpr"), "recipients_365d": (x.get("p365") or {}).get("recipients"),
            "suggestions": [{"rule": s["rule"], "severity": s["severity"], "title": s["title"]} for s in x["suggestions"]],
            "since_last": x.get("since_last")}


def _match_steps(mo: dict, flows: list[dict], sms: str) -> list[dict]:
    """Each playbook step with the live message that fills it (closest timing within the window), if any."""
    used: set[str] = set()
    out = []
    for st in mo["steps"]:
        row = {"channel": st["channel"], "day": st["day"], "job": st["job"], "status": "build", "reason": "", "message": None}
        if st.get("needs") == "sms" and sms != "ok":
            row["status"], row["reason"] = ("validate", "Klaviyo doesn't allow SMS for this category; use a permitted "
                                                        "provider after legal review") if sms == "regulated" \
                else ("validate", "Only once the brand collects SMS consent")
        best: Optional[tuple[float, dict, dict]] = None
        for fl in flows:
            sends = [x for x in walk_messages(fl["tree"]) if x["live"] and x["day"] is not None]
            start = min((x["day"] for x in sends), default=0.0) if mo.get("anchor") == "first_send" else 0.0
            for x in sends:
                if x["kind"] != st["channel"] or x["message_id"] in used:
                    continue
                gap = abs(x["day"] - start - st["day"])
                if gap <= st["window"] and (best is None or gap < best[0]):
                    best = (gap, x, fl)
        if best is not None:
            _, x, fl = best
            used.add(x["message_id"])
            row["message"] = _step_message(x, fl)
            bad = [s for s in x["suggestions"] if SEVERITY[s["severity"]] >= SEVERITY["medium"]]
            row["status"] = "fix" if bad else "keep"
            row["reason"] = "; ".join(s["title"] for s in bad[:3])
        out.append(row)
    return out


def _sms_state(context: dict, flows: list[dict]) -> str:
    if context.get("regulated_category") in REGULATED_SMS:
        return "regulated"
    return "ok" if any(fl["live"] and fl["sms"] for fl in flows) or context.get("sms_consent") else "unknown"


def build(result: dict, cards: list[dict], record: Optional[dict], context: dict, metrics: set[str]) -> list[dict]:
    mod = (result.get("modules") or {}).get(MODULE) or {}
    if mod.get("status") != "ok":
        return []
    flows = mod["tables"].get("flows") or []
    live = [fl for fl in flows if fl["live"]]
    findings = {f["id"]: f for f in result.get("findings") or []}
    sms = _sms_state(context, flows)
    book = library()
    names = {mo["id"]: re.compile(mo["name"], re.I) for mo in book["moments"] if mo.get("name")}
    moments = []
    for mo in book["moments"]:
        rx = names.get(mo["id"])
        by_kind = [fl for fl in live if fl.get("kind") in mo["kinds"]]
        if not by_kind and mo["kinds"]:
            by_kind = [fl for fl in live if fl["stage"] == mo["stage"] and not fl.get("kind")
                       and not any(r.search(fl["name"]) for k, r in names.items() if k != mo["id"])]
        by_name = [fl for fl in live if rx and (rx.search(fl["name"]) or any(
            rx.search(f"{x['name']} {x['subject']}") for x in walk_messages(fl["tree"]) if x["live"]))]
        candidates = list({fl["id"]: fl for fl in by_name + by_kind}.values())
        steps = _match_steps(mo, by_name or by_kind, sms)
        drafts = [fl for fl in flows if not fl["live"] and (fl.get("kind") in mo["kinds"] or (rx and rx.search(fl["name"])))]
        related = [findings[i] for i in mo.get("finding_ids") or [] if i in findings
                   and (not candidates or any(fl["name"] in (findings[i].get("evidence") or "") for fl in candidates)
                        or not i.startswith(MODULE))]
        core = [s for s in steps if s["status"] != "validate" or s["message"]]
        filled = [s for s in core if s["message"]]
        silent = candidates and all(fl.get("recipients_90d") == 0 for fl in candidates)
        if mo.get("requires_metric") and not any(mo["requires_metric"] in n for n in metrics) and not candidates:
            status, why = "validate", f"{mo['requires_metric'].title()} isn't tracked in this account, so this moment can't trigger yet."
        elif not filled and not (rx and by_name):
            status = "build"
            why = ("Nothing live covers this moment." + (f" Drafts exist: {', '.join(fl['name'] for fl in drafts[:3])}."
                                                         if drafts else ""))
        elif silent:
            status, why = "fix", "Live, but nothing was sent in the last 90 days."
        elif any(s["status"] == "fix" for s in core) or len(filled) < len(core) or related:
            status = "fix"
            parts = []
            if n := sum(1 for s in core if s["status"] == "fix"):
                parts.append(f"{n} live step(s) need work")
            if n := len(core) - len(filled):
                parts.append(f"{n} step(s) of the playbook are missing")
            if related:
                parts.append("; ".join(f["title"] for f in related[:2]))
            why = ". ".join(parts) + ". Live in: " + ", ".join(fl["name"] for fl in (by_name or by_kind)[:3]) + "."
        else:
            status, why = "keep", "Live, timed to the playbook, and no step has a medium or worse issue."
        keys = set(mo.get("finding_ids") or [])
        linked = [c for c in cards if (c.get("sizing") or {}).get("lever") == mo.get("lever") and mo.get("lever")
                  or c.get("finding_id") in keys
                  or (c.get("module") == MODULE and any(fl["name"] in (c.get("evidence_text") or "") for fl in candidates))]
        sized = [c["sizing"]["annual"]["base"] for c in linked if (c.get("sizing") or {}).get("status") == "sized"]
        confs = [confidence((record or {}).get(c.get("check_id") or c.get("finding_id"))) for c in linked]
        conf = round(sum(confs) / len(confs), 2) if confs else 1.0
        value = max(sized) if sized else None
        if status == "build":
            todo = f"Build the {mo['title'].lower()} ({len(core)} steps)" + (f"; start from the draft '{drafts[0]['name']}'" if drafts else "")
        elif status == "fix":
            todo = "; ".join([f"Fix step {i + 1}: {s['reason']}" for i, s in enumerate(core) if s["status"] == "fix"][:2]
                             + [f"add step {i + 1} ({s['job'][:1].lower() + s['job'][1:]})" for i, s in enumerate(core)
                                if s["status"] == "build"][:2])
            todo = (todo[:1].upper() + todo[1:]) if todo else (related[0]["action"] if related else "Repair the trigger")
        elif status == "validate":
            todo = "Set up the tracking it needs, then build it"
        else:
            todo = "Keep it; test one change at a time against its current revenue per recipient"
        moments.append({
            "id": mo["id"], "stage": mo["stage"], "stage_label": STAGE_INFO[mo["stage"]][0], "title": mo["title"],
            "status": status, "why": why, "trigger": mo["trigger"], "eligibility": mo["eligibility"], "exits": mo["exits"],
            "success_metric": mo["success_metric"], "guardrails": mo["guardrails"], "steps": steps,
            "flows": [{"id": fl["id"], "name": fl["name"]} for fl in candidates],
            "drafts": [{"id": fl["id"], "name": fl["name"], "status": fl["status"]} for fl in drafts],
            "findings": [f["id"] for f in related], "opportunities": [c["id"] for c in linked],
            "value_base": value, "confidence": conf, "next_action": todo,
        })
    moments.sort(key=lambda x: (x["status"] == "keep", -((x["value_base"] or 0) * x["confidence"]),
                                STATUS_ORDER[x["status"]], STAGES.index(x["stage"])))
    for i, x in enumerate(moments, 1):
        x["rank"] = i
    moments.sort(key=lambda x: (STAGES.index(x["stage"]), x["rank"]))
    return moments


def attach(result: dict, cards: list[dict], record: Optional[dict], context: dict, metrics: set[str]) -> None:
    """Add the optimal journey to the journey_map module's tables and its counts to the fact ledger."""
    moments = build(result, cards, record, context, metrics)
    if not moments:
        return
    mod = result["modules"][MODULE]
    mod["tables"]["optimal"] = moments
    mod["tables"]["principles"] = [{"text": p} for p in library()["principles"]]
    mod["tables"]["stage_goals"] = library()["stages"]
    for status in STATUS_ORDER:
        result["facts"].append({"id": f"{MODULE}.moments_{status}", "module": MODULE,
                                "value": sum(1 for x in moments if x["status"] == status), "unit": "moments",
                                "label": f"Optimal journey moments to {status}", "window": "", "time_basis": "event",
                                "source": f"journeys.json@{library()['version']}", "evidence_class": "proposed"})
