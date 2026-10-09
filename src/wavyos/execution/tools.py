"""The tool gateway: the only way an executor can change anything.

Every tool maps to a registered policy action and runs as the execution's agent. Each call is
authorized, run and written to the execution's trace, so the console shows what the model tried,
what policy said and what happened. A denied or failed call returns an error result to the model
instead of raising, so it can adjust.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import approvals, knowledge, operations
from .. import models as m
from ..policy import ACTIONS, authorize
from ..scope import InvalidState, NotFound, NotPermitted, Scope
from ..taxonomy import AREAS, COMPONENTS

ARTIFACT_KINDS = ("brief", "copy", "design_direction", "report", "analysis", "draft_spec", "other")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict
    action: str                 # policy action checked before the handler runs
    purposes: tuple[str, ...]   # which execution purposes may use it

    def to_anthropic(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema, "strict": True}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S = {"type": "string"}
TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_context", "Read the agency standard and this client's approved facts for one operating area.",
         _obj({"area": {"type": "string", "enum": list(AREAS)}}, ["area"]), "data.read", ("produce", "qa")),
    Tool("save_output", "Save a finished deliverable for this task (brief, copy, design direction, report, analysis, draft spec). Call once per deliverable.",
         _obj({"kind": {"type": "string", "enum": list(ARTIFACT_KINDS)}, "label": S, "content": S}, ["kind", "label", "content"]),
         "draft.create", ("produce",)),
    Tool("flag_insight", "Record something that needs attention, with the evidence you saw.",
         _obj({"area": {"type": "string", "enum": list(AREAS)}, "title": S, "summary": S,
               "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]}, "evidence": S},
              ["area", "title", "summary", "severity", "evidence"]), "insight.create", ("produce", "qa")),
    Tool("propose_fact", "Propose a fact about this client. It stays proposed until a person approves it.",
         _obj({"area": {"type": "string", "enum": list(AREAS)}, "component": {"type": "string", "enum": list(COMPONENTS)},
               "key": S, "title": S, "body": S}, ["area", "component", "key", "title", "body"]), "fact.propose", ("produce",)),
    Tool("build_draft", "Build a draft campaign or flow in the platform (never live). Only allowed inside an approved decision that covers it.",
         _obj({"kind": {"type": "string", "enum": ["campaign", "flow"]}, "spec": S}, ["kind", "spec"]),
         "campaign.build_draft", ("produce",)),
    Tool("request_approval", "Ask a person to approve an action you are not allowed to take yourself (sending, publishing, live changes, discounts). Then finish; work resumes once they decide.",
         _obj({"action": {"type": "string", "enum": sorted(k for k, a in ACTIONS.items() if a.level == "explicit_approval" and k not in ("fact.approve", "decision.approve"))},
               "summary": S, "payload": S}, ["action", "summary", "payload"]), "approval.request", ("produce",)),
    Tool("record_check", "QA only: record pass or fail for one checklist item, with a specific note.",
         _obj({"item_id": S, "passed": {"type": "boolean"}, "note": S}, ["item_id", "passed", "note"]), "task.manage", ("qa",)),
    Tool("finish", "End this execution with a short summary of what you did.", _obj({"summary": S}, ["summary"]), "task.manage", ("produce", "qa")),
]}


def tools_for(purpose: str) -> list[Tool]:
    return [t for t in TOOLS.values() if purpose in t.purposes]


class Gateway:
    """Runs tool calls for one execution and records each as a trace step."""

    def __init__(self, session: Session, scope: Scope, execution: m.Execution, task: m.Task):
        self.session, self.scope, self.ex, self.task = session, scope, execution, task
        self.finished: Optional[str] = None
        self.outputs: list[dict] = []
        self.checks: list[dict] = []
        self.awaiting: Optional[str] = None

    # ---- trace
    def step(self, kind: str, name: str, *, status: str = "ok", input: Any = None, output: Any = None,
             input_tokens: int = 0, output_tokens: int = 0, cost_usd: float = 0.0, duration_ms: int = 0) -> m.ExecutionStep:
        seq = (self.session.scalar(select(func.max(m.ExecutionStep.seq)).where(m.ExecutionStep.execution_id == self.ex.id)) or 0) + 1
        st = m.ExecutionStep(execution_id=self.ex.id, seq=seq, kind=kind, name=name, status=status,
                             input=_jsonable(input), output=_jsonable(output), input_tokens=input_tokens,
                             output_tokens=output_tokens, cost_usd=cost_usd, duration_ms=duration_ms)
        self.session.add(st)
        # Commit each step as it happens: the console sees the trace live, and a later failure
        # can't roll back the record of what already ran.
        self.session.commit()
        return st

    # ---- dispatch
    def call(self, name: str, args: dict) -> tuple[bool, str]:
        """Returns (ok, text for the model)."""
        t0 = time.monotonic()
        tool = TOOLS.get(name)
        if tool is None or self.ex.purpose not in tool.purposes:
            self.step("tool_call", name, status="denied", input=args, output={"error": "tool not available"})
            return False, f"{name} isn't available for a {self.ex.purpose} execution"
        kw = {"decision_id": self.task.decision_id} if ACTIONS[tool.action].level == "approved_plan" else {}
        auth = authorize(self.session, self.scope, tool.action, **kw)
        self.step("policy", f"{tool.action}", status="ok" if auth.allowed else "denied",
                  input={"tool": name}, output={"outcome": auth.outcome, "reason": auth.reason})
        if not auth.allowed:
            self.step("tool_call", name, status="denied", input=args, output={"error": auth.reason},
                      duration_ms=int((time.monotonic() - t0) * 1000))
            return False, f"Not allowed: {auth.reason}"
        try:
            with self.session.begin_nested():
                text = getattr(self, "_" + name)(**args)
            self.step("tool_call", name, input=args, output={"result": text}, duration_ms=int((time.monotonic() - t0) * 1000))
            return True, text
        except (ValueError, NotPermitted, NotFound, InvalidState, TypeError) as e:
            self.step("tool_call", name, status="error", input=args, output={"error": str(e)},
                      duration_ms=int((time.monotonic() - t0) * 1000))
            return False, f"Error: {e}"

    # ---- handlers
    def _get_context(self, area: str) -> str:
        import json
        return json.dumps(knowledge.context_pack(self.session, self.scope, area))

    def _save_output(self, kind: str, label: str, content: str) -> str:
        if not content.strip():
            raise ValueError("content is empty")
        a = m.Artifact(org_id=self.scope.org_id, client_id=self.scope.client_id, task_id=self.task.id,
                       execution_id=self.ex.id, kind=kind, label=label, content=content,
                       content_hash=hashlib.sha256(content.encode()).hexdigest(), created_by=self.scope.actor.ref)
        self.session.add(a)
        self.session.flush()
        self.outputs.append({"kind": kind, "ref": f"artifact:{a.id}", "label": label})
        return f"saved artifact {a.id}"

    def _flag_insight(self, area, title, summary, severity, evidence) -> str:
        ins, created = operations.create_insight(
            self.session, self.scope, area=area, title=title, summary=summary, severity=severity,
            evidence=[{"kind": "execution", "ref": f"execution:{self.ex.id}", "detail": evidence}],
            source_kind="agent", source_ref=f"execution:{self.ex.id}")
        return f"insight {ins.id} {'recorded' if created else 'already open'}"

    def _propose_fact(self, area, component, key, title, body) -> str:
        fact, ap = knowledge.propose_fact(self.session, self.scope, area=area, component=component, key=key, title=title,
                                          body=body, source_kind="agent", source_ref=f"execution:{self.ex.id}")
        return f"proposed fact {fact.id}; awaiting approval {ap.id}"

    def _build_draft(self, kind, spec) -> str:
        # v0.1: records the draft spec as an artifact. The platform write lands with the Klaviyo write connector.
        return self._save_output("draft_spec", f"Draft {kind} spec", spec)

    def _request_approval(self, action, summary, payload) -> str:
        ap = approvals.request(self.session, self.scope, action, "execution", self.ex.id, summary, {"detail": payload})
        self.awaiting = ap.id
        return f"approval {ap.id} requested; finish now, work resumes after the decision"

    def _record_check(self, item_id, passed, note) -> str:
        operations.pass_check(self.session, self.scope, self.task.id, item_id, passed)
        self.checks.append({"item_id": item_id, "passed": passed, "note": note})
        return f"{item_id}: {'pass' if passed else 'fail'}"

    def _finish(self, summary) -> str:
        self.finished = summary
        return "ok"


def _jsonable(v: Any) -> dict:
    if v is None:
        return {}
    if isinstance(v, dict):
        return {k: (x if isinstance(x, (str, int, float, bool, type(None), list, dict)) else str(x)) for k, x in v.items()}
    return {"value": str(v)}
