"""The work packet: everything an executor gets for one task, and nothing else.

Built only from approved knowledge and the task's own chain, so a model never sees another
client's data, proposed (unapproved) facts, or internal notes it doesn't need. The packet is
stored on the execution with its hash, so the console shows exactly what the model was given.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import knowledge, skills
from .. import models as m
from ..policy import payload_hash
from ..scope import Scope

ROLE_BRIEFS = {
    "creative": "You are Wavy's creative producer. Turn the approved decision into the deliverable: briefs, copy and design direction that follow the client's approved voice, offers and rules exactly.",
    "strategist": "You are Wavy's lifecycle strategist. Design journeys, segments and flows that answer the approved decision, within the client's approved facts.",
    "analyst": "You are Wavy's analyst. Work from the evidence given. State what is measured versus assumed. Missing data is unavailable, never zero.",
    "account_lead": "You are Wavy's account lead. Keep priorities, commitments and client communication accurate and specific.",
    "qa": "You are Wavy's QA reviewer. You did not write this work. Check it against every checklist item, the approved decision and the client's approved facts. Pass an item only when the work clearly meets it; otherwise fail it and say exactly what to fix.",
}


def build(session: Session, scope: Scope, task: m.Task, purpose: str, *, agent_role: str,
          feedback: Optional[str] = None, approval: Optional[m.Approval] = None) -> tuple[dict, str]:
    decision = session.get(m.Decision, task.decision_id) if task.decision_id else None
    artifacts = list(session.scalars(select(m.Artifact).where(m.Artifact.task_id == task.id).order_by(m.Artifact.created_at)))
    packet = {
        "purpose": purpose,
        "role": agent_role,
        "role_brief": ROLE_BRIEFS.get(agent_role, ""),
        # The full role skill (skills/roles/<role>.md) once `wavyos seed skills` has run.
        "role_skill": skills.role_skill(session, scope.org_id, agent_role),
        "client": {"id": task.client_id},
        "task": {"id": task.id, "title": task.title, "area": task.area, "phase": task.phase or None, "done_when": task.done_when,
                 "due_date": task.due_date.isoformat() if task.due_date else None,
                 "checklist": [{"id": i["id"], "text": i["text"]} for i in task.checklist]},
        "decision": ({"id": decision.id, "title": decision.title, "rationale": decision.rationale,
                      "measurement": decision.measurement, "plan_scope": decision.scope,
                      "insights": [{"title": i.title, "summary": i.summary, "severity": i.severity, "evidence": i.evidence}
                                   for i in decision.insights]} if decision else None),
        "knowledge": knowledge.context_pack(session, scope, task.area),
        "work_to_review": ([{"artifact_id": a.id, "kind": a.kind, "label": a.label, "content": a.content}
                            for a in artifacts] if purpose == "qa" else []),
        "previous_qa_feedback": feedback,
        "approval_granted": ({"id": approval.id, "action": approval.action, "payload": approval.payload}
                             if approval is not None and approval.status == "approved" else None),
    }
    return packet, payload_hash(packet)
