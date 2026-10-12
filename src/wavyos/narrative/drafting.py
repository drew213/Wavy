"""The analyst loop, automated: draft, facts check, adversarial critique, revise.

For each written section of a scored run:
  1. the drafter (Claude by default) writes it from the section packet;
  2. the facts check blocks any number that isn't in the ledger and cuts insights without an action;
  3. a critic from a different provider scores it on the 10-criterion rubric (the drafter's own opinion of
     its work is never asked for);
  4. the drafter revises against both, up to MAX_ROUNDS, until it scores PASS_SCORE or better with zero
     facts-check problems.
Only a passing draft enters the strategist queue (an approval). A draft still failing after the last round
is kept as needs_rewrite: a strategist can fix it, but it never publishes as is.

Spend is capped per audit (client settings: audit_llm_cap_usd, default DEFAULT_CAP_USD) and per org per day
(spend.daily_cap). A section that would cross either cap is skipped with the reason, never half-drafted silently.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import approvals, events, spend
from .. import models as m
from ..analysis.messages import _terms
from ..execution.engine import allowed_providers
from ..scope import Scope
from . import facts_check, packet, prompts
from .writers import Completion, Writer

DEFAULT_CAP_USD = 5.0
PREFERRED_DRAFTER = ("anthropic", "codex", "fake")
REVIEWED = ("approved", "edited", "rejected")
APPROVAL_ACTION = "narrative.approve"


def parse_json(text: str) -> Optional[dict]:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        out = json.loads(text[start:end])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def score_of(reply: Optional[dict]) -> tuple[dict[str, float], list[str], float]:
    """Scores clamped to 0..1 for the rubric's criteria only; the total is computed here, never taken from the critic."""
    raw = (reply or {}).get("scores") or {}
    scores = {}
    for k in prompts.CRITERIA:
        try:
            scores[k] = min(1.0, max(0.0, float(raw.get(k, 0))))
        except (TypeError, ValueError):
            scores[k] = 0.0
    notes = [str(n)[:600] for n in (reply or {}).get("notes") or [] if str(n).strip()][:20]
    if reply is None:
        notes = ["The critic's reply wasn't readable JSON; scored 0."]
    return scores, notes, round(sum(scores.values()), 2)


def spent_on(session: Session, run_id: str) -> float:
    return float(session.scalar(select(func.coalesce(func.sum(m.NarrativeDraft.cost_usd), 0.0))
                                .where(m.NarrativeDraft.run_id == run_id)) or 0.0)


def _route(client: m.Client, writers: dict[str, Writer]) -> tuple[Optional[str], Optional[str]]:
    allowed = [p for p in allowed_providers(client) if p in writers]
    if not allowed:
        return None, None
    drafter = next((p for p in PREFERRED_DRAFTER if p in allowed), allowed[0])
    critic = next((p for p in allowed if p != drafter), drafter)
    return drafter, critic


def _charge(session: Session, row: m.NarrativeDraft, c: Completion) -> float:
    spend.charge(session, row.org_id, c.cost_usd)
    row.input_tokens += c.input_tokens
    row.output_tokens += c.output_tokens
    row.cost_usd = round(row.cost_usd + c.cost_usd, 6)
    return c.cost_usd


def _retire_approval(session: Session, scope: Scope, row: m.NarrativeDraft) -> None:
    ap = session.get(m.Approval, row.approval_id) if row.approval_id else None
    if ap is not None and ap.status == "pending":
        ap.status = "cancelled"
        events.record(session, scope, "approval.cancelled", ap, "superseded by a new draft")
    row.approval_id = None


def draft_run(session: Session, scope: Scope, run: m.AuditRun, writers: dict[str, Writer], *,
              sections: Optional[list[str]] = None, today: Optional[date] = None) -> list[m.NarrativeDraft]:
    client = session.get(m.Client, run.client_id)
    cap = float((client.settings or {}).get("audit_llm_cap_usd", DEFAULT_CAP_USD))
    drafter, critic = _route(client, writers)
    ctx = packet.context(session, run)
    terms = _terms(ctx["answers"])
    card_ids = {c["id"] for c in ctx["summary"].get("opportunities") or []}
    spent = spent_on(session, run.id)
    per_call = 0.0
    out = []
    for section in sections or list(prompts.SECTIONS):
        row = session.scalar(select(m.NarrativeDraft).where(m.NarrativeDraft.run_id == run.id,
                                                            m.NarrativeDraft.section == section))
        if row is None:
            row = m.NarrativeDraft(org_id=run.org_id, client_id=run.client_id, run_id=run.id, section=section,
                                   input_tokens=0, output_tokens=0, cost_usd=0.0)
            session.add(row)
        elif row.status in REVIEWED:
            out.append(row)
            continue
        _retire_approval(session, scope, row)
        row.updated_at = datetime.now(timezone.utc)
        out.append(row)
        if drafter is None:
            row.status, row.reason = "skipped", ("No model provider is allowed for this brand yet, so nothing was "
                                                 "drafted (client settings: allowed_providers).")
            continue
        pk, row.packet_hash = packet.build(ctx, section, today)
        row.drafter, row.critic_by = drafter, critic
        draft: Optional[dict] = None
        check: dict = {"ok": False, "problems": [], "cut": [], "warnings": [], "numbers_checked": 0}
        rounds: list[dict] = []
        notes: list[str] = []
        total, passed, capped, org_capped = 0.0, False, False, False
        for rnd in range(1, prompts.MAX_ROUNDS + 1):
            if spent + 2 * per_call > cap:
                capped = True
                break
            org_left = spend.left(session, run.org_id)
            if org_left <= 0 or 2 * per_call > org_left:
                org_capped = True
                break
            c = writers[drafter].complete(prompts.SYSTEM_DRAFTER,
                                          prompts.draft_prompt(section, pk, (draft or {}) if rnd > 1 else None,
                                                               check["problems"], notes))
            spent += _charge(session, row, c)
            reply = parse_json(c.text)
            if reply is None:
                check = {"ok": False, "problems": [{"path": "", "reason": "the reply wasn't a JSON object"}],
                         "cut": [], "warnings": [], "numbers_checked": 0}
            else:
                check = facts_check.check(section, reply, ctx["ledger"], terms=terms,
                                          card_ids=card_ids if section == "opportunities" else None)
                draft = check.pop("draft")
            c2 = writers[critic].complete(prompts.SYSTEM_CRITIC,
                                          prompts.critic_prompt(section, pk, draft or {}, check["problems"]))
            spent += _charge(session, row, c2)
            per_call = max(per_call, c.cost_usd, c2.cost_usd)
            scores, notes, total = score_of(parse_json(c2.text))
            rounds.append({"round": rnd, "total": total, "scores": scores, "notes": notes,
                           "problems": len(check["problems"]), "cut": len(check["cut"])})
            if check["ok"] and total >= prompts.PASS_SCORE:
                passed = True
                break
        row.payload = draft or {}
        row.facts_check = {k: check.get(k) for k in ("ok", "problems", "cut", "warnings", "numbers_checked")}
        row.critic = {"independent": critic != drafter, "pass_score": prompts.PASS_SCORE, "rounds": rounds}
        row.score, row.rounds = (total if rounds else None), len(rounds)
        if passed:
            row.status = "draft"
            row.reason = "" if critic != drafter else ("Only one provider is allowed for this brand, so the critic "
                                                       "wasn't independent; review it with that in mind.")
            session.flush()
            ap = approvals.request(session, scope, APPROVAL_ACTION, "narrative_draft", row.id,
                                   f"{section.replace('_', ' ').title()} narrative for {client.name}: {total:g}/10",
                                   {"section": section, "packet_hash": row.packet_hash, "payload": row.payload})
            session.flush()
            row.approval_id = ap.id
        elif org_capped and not rounds:
            row.status = "skipped"
            row.reason = spend.cap_reason() + " Nothing was drafted for this section; draft it again tomorrow."
        elif capped and not rounds:
            row.status = "skipped"
            row.reason = f"The ${cap:.2f} model spend cap for this audit was reached before this section was drafted."
        else:
            why = []
            if capped:
                why.append(f"the ${cap:.2f} spend cap stopped revisions after round {len(rounds)}")
            if org_capped:
                why.append(f"the agency's ${spend.daily_cap():.2f} daily spend cap stopped revisions after round {len(rounds)}")
            if not check["ok"]:
                why.append(f"{len(check['problems'])} facts-check problem(s) remain")
            if rounds and total < prompts.PASS_SCORE:
                why.append(f"the critic scored it {total:g}/10 (needs {prompts.PASS_SCORE:g})")
            row.status = "needs_rewrite"
            row.reason = "Still failing after the last round: " + "; ".join(why) + ". A strategist must edit it."
        events.record(session, scope, f"narrative.{row.status}", row,
                      f"{section}: {row.status}" + (f" ({total:g}/10, {len(rounds)} round(s))" if rounds else ""),
                      {"section": section, "cost_usd": row.cost_usd, "rounds": len(rounds)})
    session.flush()
    return out
